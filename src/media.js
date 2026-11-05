// Media download - the durability layer in front of the chat platforms' file APIs.
//
// The incident (log, 2026-09-24 .. 2026-10-29): seven voice notes died as
// `queue task failed: fetch failed`. The bot downloaded the file from Telegram BEFORE
// it ACKed, with one bare fetch(), no timeout and no retry - so a single network blip
// meant no ACK, no saved audio, no transcript, no reply. Silence.
//
// Now the chat platform hands us a REFERENCE (Telegram file_id, Lark message_id +
// file_key). The reference is persisted and ACKed first; the bytes are fetched
// afterwards, off the per-chat queue, through `downloadMedia()` below: the same
// attempt ladder as STT (`src/retry.js`), a hard per-attempt timeout, a hard total
// budget, and a progress hook so the chat can say "retrying 2/5".
//
// A platform supplies a FETCHER: `({ timeoutMs }) => Promise<Buffer>` that throws
// MediaHttpError (the platform answered with an error) or MediaTransportError (it
// could not be reached / did not answer in time).
import {
  DOWNLOAD_MAX_ATTEMPTS,
  DOWNLOAD_ATTEMPT_TIMEOUT_MS,
  DOWNLOAD_BACKOFF_BASE_MS,
  DOWNLOAD_BACKOFF_MAX_MS,
  DOWNLOAD_TOTAL_BUDGET_MS,
} from "./config.js";
import { runWithRetry, errorDetail, truncate } from "./retry.js";

/** Download failure reasons. Persisted into the block, so treat them as a contract. */
export const DL_FAIL = {
  TOO_BIG: "too-big", // the platform refuses to serve a file this large
  GONE: "gone", // the file no longer exists on the platform
  REJECTED: "rejected", // we are not allowed to fetch it (auth / permission)
  BAD_REQUEST: "bad-request", // any other definitive "no" from the platform
  EXHAUSTED: "exhausted", // transient failures all the way to the attempt cap
};

/** The platform answered, but with an error. `status` decides retryability. */
export class MediaHttpError extends Error {
  constructor(platform, status, body) {
    super(`${platform} HTTP ${status}`);
    this.name = "MediaHttpError";
    this.platform = platform;
    this.status = status;
    this.body = String(body ?? "");
  }
}

/** The platform could not be reached or did not answer in time. Always retryable. */
export class MediaTransportError extends Error {
  constructor(platform, cause) {
    super(`${platform} transport: ${errorDetail(cause)}`);
    this.name = "MediaTransportError";
    this.platform = platform;
    this.cause = cause;
  }
}

export function classifyDownload(err) {
  if (err instanceof MediaHttpError) {
    const s = err.status;
    if (s === 429 || s >= 500) return { action: "retry" };
    if (s === 413 || /too (big|large)/i.test(err.body)) return { action: "stop", reason: DL_FAIL.TOO_BIG };
    if (s === 404 || s === 410) return { action: "stop", reason: DL_FAIL.GONE };
    if (s === 401 || s === 403) return { action: "stop", reason: DL_FAIL.REJECTED };
    return { action: "stop", reason: DL_FAIL.BAD_REQUEST };
  }
  // Network error, DNS, socket reset, abort/timeout: transient by definition.
  return { action: "retry" };
}

/**
 * Backstop for a fetcher that ignores its `timeoutMs`: the attempt is abandoned at
 * the deadline no matter what, so the ladder's "hard ceiling" stays true.
 */
function withDeadline(promise, timeoutMs, platform) {
  let timer;
  const deadline = new Promise((_, reject) => {
    timer = setTimeout(() => {
      const e = Object.assign(new Error(`no answer within ${timeoutMs}ms`), { name: "TimeoutError" });
      reject(new MediaTransportError(platform, e));
    }, timeoutMs);
    // Not unref'd: while an attempt is outstanding this deadline is what guarantees
    // it ends, and it is cleared the moment the race settles.
  });
  return Promise.race([promise, deadline]).finally(() => clearTimeout(timer));
}

/**
 * Fetch media bytes, surviving transient platform failure.
 *
 * @returns {Promise<{ok:true, buffer:Buffer, attempts:number}
 *                  | {ok:false, reason:string, attempts:number, lastError:?string}>}
 */
export async function downloadMedia(fetcher, opts = {}) {
  const {
    platform = "media",
    onRetry,
    maxAttempts = DOWNLOAD_MAX_ATTEMPTS,
    attemptTimeoutMs = DOWNLOAD_ATTEMPT_TIMEOUT_MS,
    totalBudgetMs = DOWNLOAD_TOTAL_BUDGET_MS,
    backoffBaseMs = DOWNLOAD_BACKOFF_BASE_MS,
    backoffMaxMs = DOWNLOAD_BACKOFF_MAX_MS,
    sleepImpl,
    now,
    rand,
  } = opts;

  const r = await runWithRetry({
    label: `download(${platform})`,
    providers: [{ name: platform }],
    call: (_p, { timeoutMs }) => withDeadline(Promise.resolve().then(() => fetcher({ timeoutMs })), timeoutMs, platform),
    classify: classifyDownload,
    describe: (err) => ({
      status: err instanceof MediaHttpError ? err.status : 0,
      detail: err instanceof MediaHttpError ? `${err.status} ${err.body}` : err?.message,
    }),
    summarize: (buf) => `bytes=${buf?.length ?? 0}`,
    maxAttempts,
    attemptTimeoutMs,
    totalBudgetMs,
    backoffBaseMs,
    backoffMaxMs,
    exhaustedReason: DL_FAIL.EXHAUSTED,
    rejectedReason: DL_FAIL.REJECTED,
    onRetry,
    ...(sleepImpl ? { sleepImpl } : {}),
    ...(now ? { now } : {}),
    ...(rand ? { rand } : {}),
  });
  if (r.ok) return { ok: true, buffer: r.value, attempts: r.attempts };
  return { ok: false, reason: r.reason, attempts: r.attempts, lastError: truncate(r.lastError, 300) };
}

const EXT_BY_MIME = {
  "image/jpeg": ".jpg",
  "image/png": ".png",
  "image/webp": ".webp",
  "image/gif": ".gif",
  "audio/ogg": ".ogg",
  "audio/opus": ".opus",
  "audio/mpeg": ".mp3",
  "audio/mp4": ".m4a",
  "audio/x-m4a": ".m4a",
  "audio/wav": ".wav",
  "application/pdf": ".pdf",
};

/** File extension for a MIME type, or `fallback`. */
export function extFromMime(mime, fallback) {
  if (!mime) return fallback;
  return EXT_BY_MIME[String(mime).toLowerCase()] || fallback;
}

/**
 * Identify common media from magic bytes. Lark hands over bytes with no MIME at all,
 * and an audio container mislabelled to the STT vendor is a terminal 400.
 * @returns {{mime:string, ext:string}|null}
 */
export function sniffMedia(buf) {
  if (!buf || buf.length < 12) return null;
  const head = (n) => buf.subarray(0, n);
  const ascii = (a, b) => buf.subarray(a, b).toString("latin1");
  if (ascii(0, 4) === "OggS") return { mime: "audio/ogg", ext: ".ogg" };
  if (ascii(0, 3) === "ID3") return { mime: "audio/mpeg", ext: ".mp3" };
  // MPEG-1/2 layer III frame sync (FF FB / FF F3 / FF F2). AAC ADTS (FF F1 / FF F9) is not MP3.
  if (buf[0] === 0xff && [0xfb, 0xf3, 0xf2].includes(buf[1])) return { mime: "audio/mpeg", ext: ".mp3" };
  if (ascii(4, 8) === "ftyp") return { mime: "audio/mp4", ext: ".m4a" };
  if (ascii(0, 4) === "RIFF" && ascii(8, 12) === "WAVE") return { mime: "audio/wav", ext: ".wav" };
  if (ascii(0, 4) === "RIFF" && ascii(8, 12) === "WEBP") return { mime: "image/webp", ext: ".webp" };
  if (head(3).equals(Buffer.from([0xff, 0xd8, 0xff]))) return { mime: "image/jpeg", ext: ".jpg" };
  if (head(8).equals(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]))) return { mime: "image/png", ext: ".png" };
  if (ascii(0, 4) === "GIF8") return { mime: "image/gif", ext: ".gif" };
  if (ascii(0, 5) === "%PDF-") return { mime: "application/pdf", ext: ".pdf" };
  if (ascii(0, 9) === "#!SILK_V3" || ascii(1, 10) === "#!SILK_V3") return { mime: "audio/silk", ext: ".silk" };
  return null;
}
