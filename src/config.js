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

// --- STT: ElevenLabs Scribe v2 ---
export const ELEVENLABS_API_URL = "https://api.elevenlabs.io/v1/speech-to-text";
export const ELEVENLABS_MODEL = "scribe_v2";
export const ELEVENLABS_API_KEY = process.env.ELEVENLABS_API_KEY || "";

// --- Filename safety (macOS/APFS: 255 bytes per component) ---
export const FILENAME_TITLE_MAX_BYTES = 200;

export const LOG_PATH = process.env.DIGEST_LOG_PATH || "/tmp/digest.log";
