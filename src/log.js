// Minimal logger → a log file (append), plus the console when someone is watching.
// Never throws.
//
// THIS APP IS THE ONLY WRITER OF LOG_PATH. launchd captures stdout/stderr to its own
// SERVICE_LOG_PATH (see config.js and the plist) precisely so that is true: sharing
// one inode would mean the rename-based rotation below moves the file out from under
// launchd's fd, and what would then go missing is the crash output that never reaches
// this logger at all. Sole ownership is also what makes the tracked `size` exact,
// since nothing else appends behind our back.
//
// The file is treated as PRIVATE and BOUNDED, because it deliberately records raw
// model output on an unparseable response (see `src/llm.js`) and that content is
// derived from the captain's notes:
//   • created mode 0600 in a 0700 directory, so no other local user can read it.
//     This governs CREATION only — an existing file the running service already holds
//     open is never chmod-ed out from under it;
//   • size-capped with generous retention (LOG_MAX_BYTES / LOG_RETAIN in config.js),
//     so it cannot grow without bound while still keeping a long history.
//
// Writes are synchronous appends rather than a stream: rotation then needs no handle
// hand-off, so there is no window where a buffered line lands in the file that was
// just renamed aside.
import { appendFileSync, mkdirSync, renameSync, statSync, rmSync } from "node:fs";
import { dirname } from "node:path";
import { LOG_PATH, LOG_MAX_BYTES, LOG_RETAIN, LOG_CONSOLE } from "./config.js";

const FILE_MODE = 0o600;
const DIR_MODE = 0o700;

/**
 * Build a logger over `path`. Exported so tests can exercise rotation against a temp
 * dir instead of the real DATA_DIR; production uses the module-level `log` below.
 */
export function createLogger({
  path = LOG_PATH,
  maxBytes = LOG_MAX_BYTES,
  retain = LOG_RETAIN,
  sink = console,
  // Mirror to the console only when a human is watching. Under launchd there is no
  // TTY and the console IS launchd's log file, so mirroring would hand that
  // app-unrotatable, launchd-owned file a full duplicate of everything we keep 0600
  // and capped here. DIGEST_LOG_CONSOLE=1 forces it back on for piped-but-live runs.
  useConsole = LOG_CONSOLE || Boolean(process.stdout?.isTTY),
} = {}) {
  let size = null; // null = the file is unusable, keep logging to the console only

  function open() {
    try {
      mkdirSync(dirname(path), { recursive: true, mode: DIR_MODE });
      try {
        size = statSync(path).size;
      } catch {
        size = 0; // no file yet; the first append creates it 0600
      }
    } catch {
      size = null;
    }
  }

  /**
   * digest.log → digest.log.1, .1 → .2, … The rename of `.retain-1` onto `.retain`
   * overwrites the oldest kept file, which is what drops it. Never throws: a failed
   * rotation just means we keep appending to the current file.
   */
  function rotate() {
    try {
      if (retain >= 1) {
        for (let i = retain - 1; i >= 1; i--) {
          try {
            renameSync(`${path}.${i}`, `${path}.${i + 1}`);
          } catch {
            /* that generation does not exist yet */
          }
        }
        renameSync(path, `${path}.1`);
      } else {
        rmSync(path, { force: true });
      }
      size = 0;
    } catch {
      /* keep the current file; logging must never crash the bot */
    }
  }

  /** @returns {boolean} true when the line reached the file. */
  function write(line) {
    if (size === null) return false;
    const bytes = Buffer.byteLength(line, "utf8");
    try {
      if (maxBytes > 0 && size > 0 && size + bytes > maxBytes) rotate();
      appendFileSync(path, line, { encoding: "utf8", mode: FILE_MODE });
      size += bytes;
      return true;
    } catch {
      return false; // logging must never crash the bot
    }
  }

  function emit(level, args) {
    const line = `${new Date().toISOString()} [digest] ${level}: ${args
      .map((a) => (typeof a === "string" ? a : JSON.stringify(a)))
      .join(" ")}`;
    // File first, console second: if the file is unavailable for ANY reason the line
    // still goes to the console regardless of TTY. Losing the file must never mean
    // losing the line — that is the failure this whole change exists to prevent.
    const persisted = write(line + "\n");
    if (useConsole || !persisted) (level === "ERROR" ? sink.error : sink.log)(line);
  }

  open();
  return {
    info: (...a) => emit("INFO", a),
    warn: (...a) => emit("WARN", a),
    error: (...a) => emit("ERROR", a),
  };
}

export const log = createLogger();
