// Media jobs: everything that happens to a voice note, photo or file AFTER it was
// ACKed. Platform-agnostic: the platform supplies `fetchMedia(source) -> fetcher` and
// `reply(text, opts)`; this module owns the pipeline and its guarantees.
//
//   1. download   (skipped when the bytes are already on disk) - src/media.js ladder
//   2. process    voice -> transcribe (src/stt.js ladder); image -> vision caption
//   3. report     a result message, or an explicit apology with a Retry button
//
// Guarantees, each pinned by test/media-job.test.js:
//   - the user ALWAYS hears the outcome: success, or "sorry, it failed" with the
//     reason and attempt count. Silence is the bug this module exists to remove.
//   - every retry is announced ("retrying 2/5"), and a job that is still running
//     after JOB_PROGRESS_NOTICE_MS says so once, so a long voice note is visibly alive.
//   - job state lives in the manifest (`block.job`), so a restart resumes it
//     (`resumeJobs`) instead of leaving it half-done forever.
//   - jobs run OFF the per-chat serial queue and are tracked in
//     src/transcriptions.js, which is what `/done` waits on.
import { loadPending, updateBlock, saveAttachment, pendingAttachmentPath } from "./store.js";
import { trackTranscription } from "./transcriptions.js";
import { downloadMedia, sniffMedia, extFromMime } from "./media.js";
import { transcribe } from "./stt.js";
import { captionImage } from "./llm.js";
import { moveAttachment } from "./finalize.js";
import { stampSecond } from "./util.js";
import { JOB_PROGRESS_NOTICE_MS, ATTACHMENTS_DIRNAME } from "./config.js";
import { log } from "./log.js";

export const RECOVER_CMD = "npm run retranscribe -- --all";

export const KIND = {
  voice: { emoji: "🎙️", zh: "语音", en: "voice message", fallbackExt: ".ogg" },
  image: { emoji: "🖼", zh: "图片", en: "image", fallbackExt: ".jpg" },
  file: { emoji: "📎", zh: "文件", en: "file", fallbackExt: ".bin" },
};

/** `0:42` / `12:05` for a duration in seconds. */
export function fmtDuration(sec) {
  const s = Math.max(0, Math.round(Number(sec) || 0));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** The instant ACK for a media input: says what arrived, before anything can fail. */
export function mediaAck(n, type, { duration, origName } = {}) {
  const k = KIND[type];
  if (type === "voice") {
    const len = duration ? ` (${fmtDuration(duration)})` : "";
    return `✓ ACK #${n} · ${k.emoji} 收到语音${len}，转写中 · voice received${len}, transcribing…`;
  }
  if (type === "image") return `✓ ACK #${n} · ${k.emoji} 收到图片 · image received`;
  return `✓ ACK #${n} · ${k.emoji} 收到文件 · file received${origName ? `: ${origName}` : ""}`;
}

const safeName = (s) => String(s || "").replace(/[/\\]/g, "_").replace(/^\.+/, "");

/** Attachment filename for a block, stamped with its ARRIVAL time. */
export function attachmentName(block, ext) {
  const stamp = stampSecond(block.ts ? new Date(block.ts) : new Date());
  if (block.type === "voice") return `${stamp}-${block.seq}-voice${ext}`;
  if (block.type === "image") return `${stamp}-${block.seq}-img${ext}`;
  const orig = safeName(block.origName) || `file${ext}`;
  return `${stamp}-${block.seq}-${orig}`;
}

/** Short human reason for a retry notice: "elevenlabs 503" / "telegram: fetch failed". */
function retryReason(i) {
  const status = i.status ? ` ${i.status}` : "";
  const detail = i.status ? "" : `: ${String(i.detail || "").replace(/^\w+ transport: /, "").slice(0, 80)}`;
  return `${i.provider}${status}${detail}`;
}

/** chatId:seq of every job currently running in this process (so Retry cannot double-start). */
const running = new Set();
const jobKey = (chatId, seq) => `${chatId}:${seq}`;
export const isJobRunning = (chatId, seq) => running.has(jobKey(chatId, seq));

/**
 * Start (or restart) the job for one block. Returns the tracked task, which never
 * rejects. `deps`:
 *   reply(text, opts)        - send to the chat (opts.retry = seq attaches Retry)
 *   fetchMedia(source)       - platform fetcher factory for a stored reference
 *   stt, download            - optional test seams passed through to the ladders
 *   progressNoticeMs         - when to say "still working" (default from config)
 */
export function startMediaJob(chatId, seq, deps) {
  const key = jobKey(chatId, seq);
  if (running.has(key)) return Promise.resolve();
  running.add(key);
  const task = runMediaJob(chatId, seq, deps)
    .catch(async (e) => {
      // A bug or an I/O error inside the pipeline must still end in a message.
      log.error(`media job ${key} crashed: ${e?.stack || e}`);
      await updateBlock(chatId, seq, { job: { state: "failed", stage: "internal", at: new Date().toISOString() } }).catch(
        () => {}
      );
      await deps
        .reply(`❌ #${seq + 1} 抱歉，处理出错 · Sorry, processing failed: ${e?.message || e}`, { retry: seq })
        .catch(() => {});
    })
    .finally(() => running.delete(key));
  return trackTranscription(chatId, task);
}

async function runMediaJob(chatId, seq, deps) {
  const { reply, progressNoticeMs = JOB_PROGRESS_NOTICE_MS } = deps;
  const manifest = await loadPending(chatId);
  const block = manifest?.blocks.find((b) => b.seq === seq);
  if (!block) return;
  const n = seq + 1;
  const kind = KIND[block.type];
  if (!kind) return;

  const timer = setTimeout(() => {
    reply(`⏳ #${n} 仍在处理 · still working on your ${kind.en}…`).catch(() => {});
  }, progressNoticeMs);
  timer.unref?.();
  try {
    let current = block;
    if (!current.attachment) {
      current = await downloadStage(chatId, current, deps);
      if (!current) return;
    }
    if (current.type === "voice") await transcribeStage(chatId, current, deps);
    else if (current.type === "image") await captionStage(chatId, current, deps);
    else {
      await updateBlock(chatId, seq, { job: { state: "done", at: new Date().toISOString() } });
      await reply(`📎 已存 · file saved: ${current.origName || current.attachment} (#${n})`);
    }
  } finally {
    clearTimeout(timer);
  }
}

/** Fetch the bytes for a block stored by reference. Returns the patched block, or null. */
async function downloadStage(chatId, block, deps) {
  const { reply, fetchMedia, download = {} } = deps;
  const n = block.seq + 1;
  const kind = KIND[block.type];
  const platform = block.source?.platform || "media";

  if (!block.source || typeof fetchMedia !== "function") {
    await failDownload(chatId, block, reply, { reason: "no-source", attempts: 0, lastError: "no stored reference to fetch" });
    return null;
  }

  const dl = await downloadMedia(fetchMedia(block.source), {
    platform,
    onRetry: (i) =>
      reply(`⏳ #${n} 下载重试 · download retry ${i.attempt + 1}/${i.maxAttempts} (${retryReason(i)})`),
    ...download,
  });
  if (!dl.ok) {
    await failDownload(chatId, block, reply, dl);
    return null;
  }

  const sniffed = sniffMedia(dl.buffer);
  const mime = block.mime || sniffed?.mime || null;
  const ext = sniffed?.ext || extFromMime(mime, kind.fallbackExt);
  const name = attachmentName(block, ext);
  await saveAttachment(chatId, name, dl.buffer);
  const patched = await updateBlock(chatId, block.seq, {
    attachment: name,
    mime,
    bytes: dl.buffer.length,
    downloadFailure: null,
  });
  if (!patched) {
    // `/done` compiled this digest while the download was still running (only when
    // the bounded wait bit). The bytes are not lost: park them in the vault.
    const kept = await moveAttachment(chatId, name).catch(() => null);
    await reply(
      `⚠️ #${n} ${kind.zh}下载完成时笔记已编译 · your ${kind.en} arrived after the note was compiled.\n` +
        (kept ? `已存到 · saved to ${ATTACHMENTS_DIRNAME}/${kept} (not linked in the note)` : "请重发 · please send it again")
    );
    return null;
  }
  return patched;
}

async function failDownload(chatId, block, reply, dl) {
  const n = block.seq + 1;
  const kind = KIND[block.type];
  const at = new Date().toISOString();
  await updateBlock(chatId, block.seq, {
    job: { state: "failed", stage: "download", at },
    downloadFailure: { reason: dl.reason, attempts: dl.attempts, error: dl.lastError || null, at },
  });
  await reply(
    `❌ #${n} 抱歉，${kind.zh}下载失败 · Sorry, I could not download your ${kind.en} ` +
      `(${dl.reason}, ${dl.attempts} attempt${dl.attempts === 1 ? "" : "s"}).\n` +
      (dl.lastError ? `原因 reason: ${dl.lastError}\n` : "") +
      `点 🔁 重试，或重新发送 · Tap 🔁 Retry, or send it again.`,
    { retry: block.seq }
  );
}

async function transcribeStage(chatId, block, deps) {
  const { reply, stt = {} } = deps;
  const n = block.seq + 1;
  const r = await transcribe(pendingAttachmentPath(chatId, block.attachment), {
    mime: block.mime,
    onRetry: (i) => reply(`⏳ #${n} 转写重试 · transcription retry ${i.attempt + 1}/${i.maxAttempts} (${retryReason(i)})`),
    ...stt,
  });
  const at = new Date().toISOString();

  if (r.ok) {
    const patched = await updateBlock(chatId, block.seq, {
      transcript: r.text,
      sttProvider: r.provider,
      sttAttempts: r.attempts,
      sttFailure: null,
      job: { state: "done", at },
    });
    // `patched === null` means /done already compiled this digest while the retry
    // loop was still running. The words are not lost - say where they went.
    await reply(
      patched
        ? `🎙️ 已转写 · transcribed (#${n}):\n\n> ${r.text}`
        : `🎙️ 转写迟到，笔记已编译 · transcript arrived after the note was compiled (#${n}):\n\n` +
            `> ${r.text}\n\n↻ 写进笔记 · patch it into the note: ${RECOVER_CMD}`
    );
    return;
  }

  await updateBlock(chatId, block.seq, {
    transcript: null,
    sttFailure: { reason: r.reason, attempts: r.attempts, providers: r.providersTried, error: r.lastError, at },
    job: { state: "failed", stage: "transcribe", at },
  });
  const tried = r.providersTried.length ? r.providersTried.join(", ") : "none";
  await reply(
    `❌ #${n} 抱歉，转写失败 · Sorry, transcription failed. ` +
      `已存音频，可恢复 · audio saved, transcription failed but recoverable (#${n})\n` +
      `原因 reason: ${r.reason} (${r.attempts} attempt(s) via ${tried})\n` +
      `点 🔁 重试；或笔记保存后重跑 · tap 🔁 Retry, or after the note is saved, re-run:\n` +
      // --all, not the pending name: moveAttachment renames on collision at /done,
      // so the name quoted here is not guaranteed to be the one in the vault.
      RECOVER_CMD,
    { retry: block.seq }
  );
}

async function captionStage(chatId, block, deps) {
  const { reply } = deps;
  const caption = await captionImage(pendingAttachmentPath(chatId, block.attachment), block.mime || "image/jpeg");
  await updateBlock(chatId, block.seq, { visionCaption: caption, job: { state: "done", at: new Date().toISOString() } });
  await reply(`🖼 已存 · image saved (#${block.seq + 1})`);
}

/** Does this block have unfinished work a restart must pick back up? */
export function needsResume(b) {
  if (!KIND[b.type]) return false;
  if (b.job) return b.job.state === "pending";
  // Blocks written before `job` existed: a voice note whose transcription was in
  // flight when the process died has an attachment and neither outcome recorded.
  return b.type === "voice" && Boolean(b.attachment) && b.transcript === undefined && !b.sttFailure;
}

/**
 * Resume every unfinished job in the given chats (call once at startup).
 * `depsFor(chatId)` returns the deps for that chat (its reply + fetchMedia).
 * @returns {Promise<number>} how many jobs were resumed
 */
export async function resumeJobs(chatIds, depsFor) {
  let count = 0;
  for (const chatId of chatIds) {
    const m = await loadPending(chatId);
    for (const b of m?.blocks || []) {
      if (!needsResume(b)) continue;
      const deps = depsFor(chatId);
      count++;
      log.info(`resume job ${chatId}:${b.seq} (${b.type}, ${b.attachment ? "on disk" : "not downloaded"})`);
      await deps.reply(`♻️ #${b.seq + 1} 重启后继续处理 · resuming your ${KIND[b.type].en} after a restart`).catch(() => {});
      startMediaJob(chatId, b.seq, deps);
    }
  }
  return count;
}

/**
 * The Retry button. Re-runs a failed job from wherever it stopped: a failed
 * download downloads again; a failed transcription re-transcribes the saved audio.
 */
export async function retryJob(chatId, seq, deps) {
  const { reply } = deps;
  const n = seq + 1;
  const m = await loadPending(chatId);
  const b = m?.blocks.find((x) => x.seq === seq);
  if (!b) {
    await reply(
      `#${n} 所在的笔记已编译 · the note holding #${n} was already compiled.\n` +
        `转写可用 · for a transcript: ${RECOVER_CMD}; 下载失败请重发 · for a failed download, send it again.`
    );
    return null;
  }
  if (isJobRunning(chatId, seq)) {
    await reply(`⏳ #${n} 正在处理中 · #${n} is still being processed`);
    return null;
  }
  if (b.job?.state === "done" || (b.type === "voice" && b.transcript)) {
    await reply(`✓ #${n} 已完成，无需重试 · #${n} already succeeded`);
    return null;
  }
  await updateBlock(chatId, seq, { job: { state: "pending", retriedAt: new Date().toISOString() }, sttFailure: null, downloadFailure: null });
  await reply(`🔁 #${n} 重试中 · retrying…`);
  return startMediaJob(chatId, seq, deps);
}

