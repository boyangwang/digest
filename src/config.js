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

// --- LLM: Tencent TokenHub (OpenAI Chat Completions) ---
export const TOKENHUB_BASE_URL =
  process.env.TOKENHUB_BASE_URL ||
  "https://tokenhub-intl.tencentcloudmaas.com/v1";
export const TOKENHUB_API_KEY = process.env.TENCENT_TOKENHUB_API_KEY || "";
export const TITLE_MODEL = process.env.DIGEST_TITLE_MODEL || "glm-5.2";
export const VISION_MODEL = process.env.DIGEST_VISION_MODEL || "glm-5v-turbo";
// Generous CAP (not a target); glm-5.2 supports up to 128k output.
export const TITLE_MAX_TOKENS = Number(process.env.DIGEST_TITLE_MAX_TOKENS || 100000);
export const VISION_MAX_TOKENS = Number(process.env.DIGEST_VISION_MAX_TOKENS || 2000);

// --- LLM retry ladder (same mechanism as STT, see src/retry.js) -----------
// The title/tag call is not a nicety any more: `npm run retranscribe` cannot commit
// a recovered transcript without it, so a single-shot call to a flaky vendor would
// reintroduce exactly the transient-blip failure this work exists to remove.
export const LLM_MAX_ATTEMPTS = Number(process.env.DIGEST_LLM_MAX_ATTEMPTS || 4);
// glm-5.2 runs with thinking enabled, so a legitimate title call is slow.
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
// 60s per attempt. A long voice note can legitimately take most of a minute; a
// tighter cap would time out every one of the six attempts on the same slow file
// and `npm run retranscribe` — which shares this cap — would fail identically.
export const STT_ATTEMPT_TIMEOUT_MS = Number(process.env.DIGEST_STT_ATTEMPT_TIMEOUT_MS || 60000);
// Backoff between attempts: base * 2^(n-1), capped, then jittered ±25%.
export const STT_BACKOFF_BASE_MS = Number(process.env.DIGEST_STT_BACKOFF_BASE_MS || 1000);
export const STT_BACKOFF_MAX_MS = Number(process.env.DIGEST_STT_BACKOFF_MAX_MS || 8000);
/**
 * Hard ceiling on the whole transcribe() call. Worst case with the defaults:
 * 6 × 60s of request time (360000ms) + the 5 backoff sleeps (1+2+4+8+8 = 23000ms,
 * at most 28750ms once jittered by +25%) = 388750ms, so 390000ms is the binding
 * bound. Checked before each attempt AND used to clamp each attempt's own timeout.
 *
 * Nobody waits on this in the chat: transcription runs OUTSIDE the per-chat serial
 * queue (see `src/transcriptions.js`), so a long retry storm never delays the next
 * message or its ACK. `/done` is the one thing that waits, and it waits bounded by
 * this same number before compiling with the retryable failure marker instead.
 */
export const STT_TOTAL_BUDGET_MS = Number(process.env.DIGEST_STT_TOTAL_BUDGET_MS || 390000);

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
