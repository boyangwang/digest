// Config — all paths, model ids, and constants in one place.
import { homedir } from "node:os";
import { join } from "node:path";

// --- Timezone (SGT, UTC+8) — used for all timestamps ---
export const SGT_OFFSET_MIN = 8 * 60;

// --- Telegram ---
export const BOT_TOKEN = process.env.DIGEST_BOT_TOKEN || "";
export const BOYANG_USER_ID = 411364623; // only this user is served
export const ALLOWED_USER_IDS = new Set([BOYANG_USER_ID]);

// --- Obsidian vault ---
export const VAULT_ROOT = "/Users/claw/Documents/NotesVault";
export const DIGEST_DIR =
  process.env.DIGEST_OUT_DIR || join(VAULT_ROOT, "Heresy-Anthology", "digest");
// Attachments live under the digest dir; embeds use the vault-relative prefix.
export const ATTACHMENTS_DIRNAME = "ATTACHMENTS";
export const ATTACHMENTS_DIR = join(DIGEST_DIR, ATTACHMENTS_DIRNAME);
export const ATTACHMENTS_VAULT_PREFIX = "Heresy-Anthology/digest/ATTACHMENTS";

// --- Pending-digest working store (must survive restarts → not /tmp) ---
export const DATA_DIR =
  process.env.DIGEST_DATA_DIR || join(homedir(), ".local", "share", "digest");
export const PENDING_DIR = join(DATA_DIR, "pending");

// --- LLM: title + tags, image captions (Chat Completions API) -------------
// Providers in order, rotated by the same ladder as STT (src/retry.js):
//   openai   - the default since 2026-11-06. Key: OPENAI_API_KEY, which the sops
//              store already ships for the STT fallback below. gpt-5.4-mini is
//              current-gen, vision-capable and cheap ($0.75 in / $4.50 out per 1M
//              tokens, developers.openai.com/api/docs/pricing): a title is ~1-2 cents.
//   tokenhub - Tencent TokenHub, glm-5.2 + glm-5v-turbo. Both vault keys have
//              returned 401 since 2026-09-13; kept so it can come back by env alone:
//              DIGEST_LLM_PROVIDERS=openai,tokenhub
export const LLM_PROVIDER_ORDER = (process.env.DIGEST_LLM_PROVIDERS || "openai")
  .split(",")
  .map((s) => s.trim().toLowerCase())
  .filter(Boolean);
export const OPENAI_LLM_BASE_URL = process.env.DIGEST_OPENAI_BASE_URL || "https://api.openai.com/v1";
export const TITLE_MODEL = process.env.DIGEST_TITLE_MODEL || "gpt-5.4-mini";
export const VISION_MODEL = process.env.DIGEST_VISION_MODEL || "gpt-5.4-mini";
// GPT-5 models reason before answering. The title is where that is worth paying for.
export const TITLE_REASONING_EFFORT = process.env.DIGEST_TITLE_REASONING_EFFORT || "medium";
export const VISION_REASONING_EFFORT = process.env.DIGEST_VISION_REASONING_EFFORT || "low";
// CAPS, not targets. For GPT-5 models they include the hidden reasoning tokens, so
// they must leave room for it: an answer starved by reasoning comes back empty.
export const TITLE_MAX_TOKENS = Number(process.env.DIGEST_TITLE_MAX_TOKENS || 16000);
export const VISION_MAX_TOKENS = Number(process.env.DIGEST_VISION_MAX_TOKENS || 4000);

export const TOKENHUB_BASE_URL =
  process.env.TOKENHUB_BASE_URL ||
  "https://tokenhub-intl.tencentcloudmaas.com/v1";
export const TOKENHUB_API_KEY = process.env.TENCENT_TOKENHUB_API_KEY || "";
export const TOKENHUB_TITLE_MODEL = process.env.DIGEST_TOKENHUB_TITLE_MODEL || "glm-5.2";
export const TOKENHUB_VISION_MODEL = process.env.DIGEST_TOKENHUB_VISION_MODEL || "glm-5v-turbo";
// Generous CAP (not a target); glm-5.2 supports up to 128k output.
export const TOKENHUB_TITLE_MAX_TOKENS = Number(process.env.DIGEST_TOKENHUB_TITLE_MAX_TOKENS || 100000);

// --- LLM retry ladder (same mechanism as STT, see src/retry.js) -----------
// The title/tag call is not a nicety any more: `npm run retranscribe` cannot commit
// a recovered transcript without it, so a single-shot call to a flaky vendor would
// reintroduce exactly the transient-blip failure this work exists to remove.
export const LLM_MAX_ATTEMPTS = Number(process.env.DIGEST_LLM_MAX_ATTEMPTS || 4);
// Title models reason before answering, so a legitimate title call is slow.
export const LLM_ATTEMPT_TIMEOUT_MS = Number(process.env.DIGEST_LLM_ATTEMPT_TIMEOUT_MS || 90000);
export const LLM_BACKOFF_BASE_MS = Number(process.env.DIGEST_LLM_BACKOFF_BASE_MS || 1000);
export const LLM_BACKOFF_MAX_MS = Number(process.env.DIGEST_LLM_BACKOFF_MAX_MS || 8000);
/**
 * Hard ceiling on one generateTitleAndTags() call. Worst case with the defaults:
 * 4 × 90s of request time (360000ms) + the 3 backoff sleeps (1+2+4 = 7000ms, at most
 * 8750ms once jittered by +25%) = 368750ms, so 370000ms is the binding bound.
 */
export const LLM_TOTAL_BUDGET_MS = Number(process.env.DIGEST_LLM_TOTAL_BUDGET_MS || 370000);

// --- STT vendors ---------------------------------------------------------
// Transcription is the one step with no local fallback: if it fails the words are
// gone, so every call goes through the retry/rotation loop in `stt.js`. Vendors,
// order, attempt cap and timing all live here so the loop stays data-driven.
//
// Vendor 1 — ElevenLabs Scribe v2 (proven; auto-detects zh/en).
export const ELEVENLABS_API_URL = "https://api.elevenlabs.io/v1/speech-to-text";
export const ELEVENLABS_MODEL = process.env.DIGEST_ELEVENLABS_MODEL || "scribe_v2";
export const ELEVENLABS_API_KEY = process.env.ELEVENLABS_API_KEY || "";

// Vendor 2 — OpenAI audio transcriptions. Chosen over the originally-suggested
// MiniMax because MiniMax's speech API is text-to-audio only (T2A / voice clone /
// voice design / voice management) — it publishes no ASR endpoint — and TokenHub
// exposes no ASR model either (`/v1/audio/transcriptions` → 404). OpenAI needs no
// new credential: OPENAI_API_KEY already ships in the same sops store the service
// loads, and it was verified against the real failed Chinese voice notes.
export const OPENAI_STT_API_URL =
  process.env.DIGEST_OPENAI_STT_URL || "https://api.openai.com/v1/audio/transcriptions";
export const OPENAI_STT_MODEL = process.env.DIGEST_OPENAI_STT_MODEL || "gpt-4o-transcribe";
export const OPENAI_API_KEY = process.env.OPENAI_API_KEY || "";

/**
 * Vendor rotation order. Attempt N uses order[(N-1) % order.length], so with two
 * vendors the attempts alternate 1,2,1,2,1,2; with one vendor all attempts use it.
 * Override with DIGEST_STT_PROVIDERS="elevenlabs,openai" (comma-separated).
 */
export const STT_PROVIDER_ORDER = (process.env.DIGEST_STT_PROVIDERS || "elevenlabs,openai")
  .split(",")
  .map((s) => s.trim().toLowerCase())
  .filter(Boolean);

export const STT_MAX_ATTEMPTS = Number(process.env.DIGEST_STT_MAX_ATTEMPTS || 6);
// 180s (3 minutes) per attempt - the captain's ruling of 2026-11-05: a long voice
// note must never be cut off by an impatient timer. Vendors normally answer in
// seconds; this cap only bites on a genuine stall, and then the next attempt (on the
// other vendor) takes over. `npm run retranscribe` shares this cap.
export const STT_ATTEMPT_TIMEOUT_MS = Number(process.env.DIGEST_STT_ATTEMPT_TIMEOUT_MS || 180000);
// Backoff between attempts: base * 2^(n-1), capped, then jittered ±25%.
export const STT_BACKOFF_BASE_MS = Number(process.env.DIGEST_STT_BACKOFF_BASE_MS || 1000);
export const STT_BACKOFF_MAX_MS = Number(process.env.DIGEST_STT_BACKOFF_MAX_MS || 8000);
/**
 * Hard ceiling on the whole transcribe() call. Worst case with the defaults:
 * 6 × 180s of request time (1080000ms) + the 5 backoff sleeps (1+2+4+8+8 = 23000ms,
 * at most 28750ms once jittered by +25%) = 1108750ms, so 1110000ms is the binding
 * bound. Checked before each attempt AND used to clamp each attempt's own timeout.
 *
 * Nobody waits on this in the chat: transcription runs OUTSIDE the per-chat serial
 * queue (see `src/transcriptions.js`), so a long retry storm never delays the next
 * message or its ACK. `/done` waits for it OUTSIDE the queue too (see
 * `src/commands.js`), bounded by JOB_TOTAL_BUDGET_MS.
 */
export const STT_TOTAL_BUDGET_MS = Number(process.env.DIGEST_STT_TOTAL_BUDGET_MS || 1110000);

// --- Media download (Telegram getFile / Lark message resources) ------------
// The download used to be a single bare fetch() run BEFORE the ACK, with no timeout
// and no retry: one `fetch failed` and the voice note vanished without a reply
// (seven times in the log between 2026-09-24 and 2026-10-29). It now runs AFTER the
// ACK, off the per-chat queue, on the same attempt ladder as STT (src/retry.js).
export const DOWNLOAD_MAX_ATTEMPTS = Number(process.env.DIGEST_DOWNLOAD_MAX_ATTEMPTS || 5);
// Same 3-minute ruling as STT: a big file on a slow link gets the time it needs.
export const DOWNLOAD_ATTEMPT_TIMEOUT_MS = Number(process.env.DIGEST_DOWNLOAD_ATTEMPT_TIMEOUT_MS || 180000);
export const DOWNLOAD_BACKOFF_BASE_MS = Number(process.env.DIGEST_DOWNLOAD_BACKOFF_BASE_MS || 2000);
export const DOWNLOAD_BACKOFF_MAX_MS = Number(process.env.DIGEST_DOWNLOAD_BACKOFF_MAX_MS || 15000);
/**
 * Worst case with the defaults: 5 × 180s (900000ms) + 4 backoff sleeps
 * (2+4+8+15 = 29000ms, at most 36250ms jittered) = 936250ms → 940000ms.
 */
export const DOWNLOAD_TOTAL_BUDGET_MS = Number(process.env.DIGEST_DOWNLOAD_TOTAL_BUDGET_MS || 940000);

/**
 * Hard ceiling on one media job (download, then transcribe/caption). `/done` waits
 * at most this long for in-flight jobs, and it waits OUTSIDE the per-chat queue, so
 * new messages are still ACKed while it does.
 */
export const JOB_TOTAL_BUDGET_MS =
  Number(process.env.DIGEST_JOB_TOTAL_BUDGET_MS || 0) || DOWNLOAD_TOTAL_BUDGET_MS + STT_TOTAL_BUDGET_MS + 60000;

/** A job still running after this long tells the user, once, that it is alive. */
export const JOB_PROGRESS_NOTICE_MS = Number(process.env.DIGEST_JOB_PROGRESS_NOTICE_MS || 45000);

// --- Outbound chat messages (ACKs, results, apologies) ----------------------
// A reply that silently fails is as bad as no reply, so sends retry briefly too.
export const SEND_MAX_ATTEMPTS = Number(process.env.DIGEST_SEND_MAX_ATTEMPTS || 4);
export const SEND_ATTEMPT_TIMEOUT_MS = Number(process.env.DIGEST_SEND_ATTEMPT_TIMEOUT_MS || 30000);
export const SEND_TOTAL_BUDGET_MS = Number(process.env.DIGEST_SEND_TOTAL_BUDGET_MS || 150000);

// --- Lark (international Feishu) ---------------------------------------------
// The "Sleep Digest" custom app in the Brain Computer Interface tenant, created
// 2026-11-05. Events arrive over the SDK's long connection (no public URL needed).
export const LARK_APP_ID = process.env.DIGEST_LARK_APP_ID || "";
export const LARK_APP_SECRET = process.env.DIGEST_LARK_APP_SECRET || "";
export const LARK_API_BASE = process.env.DIGEST_LARK_API_BASE || "https://open.larksuite.com";
/**
 * Lark open_ids are per-app, so these are Boyang's ids AS SEEN BY THE SLEEP DIGEST
 * APP (never reuse one from another app). The app's availability is also limited to
 * him in the admin console; this is the second gate, in code.
 */
export const BOYANG_LARK_OPEN_ID = "ou_246d39567c71fec91eb699bdfde95754"; // only user in the app's scope
export const LARK_ALLOWED_OPEN_IDS = new Set(
  (process.env.DIGEST_LARK_ALLOWED_OPEN_IDS || BOYANG_LARK_OPEN_ID)
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean)
);
/** Pending-store key prefix, so Lark chats never collide with Telegram chat ids. */
export const LARK_CHAT_PREFIX = "lark_";

// --- Filename safety (macOS/APFS: 255 bytes per component) ---
export const FILENAME_TITLE_MAX_BYTES = 200;

// Log file. NEVER default under /tmp: macOS purges /tmp periodically and the running
// launchd service keeps its handle on the now-unlinked inode, so the entire log
// history silently disappears (observed: pid holding /tmp/digest.log with NLINK=0).
// DATA_DIR is already the durable home for state that must survive a purge.
export const LOG_PATH = process.env.DIGEST_LOG_PATH || join(DATA_DIR, "digest.log");

/**
 * launchd's OWN capture of stdout/stderr, and it must NEVER equal LOG_PATH.
 *
 * They used to be the same file, so the service's fd 1/2 and this app both appended
 * to one inode. Any rename-based rotation then moved that inode out from under
 * launchd — which never reopens by path — so from the first rotation onward every
 * line the service wrote, including the crash output that bypasses the logger
 * entirely, went to an orphan nobody tails and that grew outside the size cap. That
 * is the /tmp NLINK=0 failure in a new costume. One writer per file, always.
 *
 * This file is the NET for whatever escapes the logger (an early FATAL, an uncaught
 * stack). If it is non-empty, something escaped and is worth reading.
 */
export const SERVICE_LOG_PATH = join(DATA_DIR, "digest-service.log");

/**
 * Mirror log lines to the console as well as the file. Off under launchd (no TTY),
 * because there the console IS the service log — mirroring would hand it an
 * unbounded, launchd-owned duplicate of a file we deliberately keep 0600 and capped.
 */
export const LOG_CONSOLE = ["1", "true", "yes"].includes(
  String(process.env.DIGEST_LOG_CONSOLE || "").toLowerCase()
);

/**
 * The log deliberately records raw model output when a response will not parse — the
 * actual bytes are the only thing that explains why — so it holds journal-derived
 * content and must be both private (mode 0600, see `src/log.js`) and BOUNDED.
 *
 * Rotation keeps history rather than discarding it: the whole point of moving off
 * /tmp was that a purge destroyed the history that would have answered the original
 * failure. Worst-case footprint with these defaults: one active file at the cap plus
 * LOG_RETAIN rotated files = 8 MiB × (1 + 12) = 104 MiB.
 */
export const LOG_MAX_BYTES = Number(process.env.DIGEST_LOG_MAX_BYTES || 8 * 1024 * 1024);
export const LOG_RETAIN = Number(process.env.DIGEST_LOG_RETAIN || 12);
