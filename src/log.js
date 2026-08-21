// Minimal logger → stdout + a log file (append). Never throws.
import { createWriteStream, mkdirSync } from "node:fs";
import { dirname } from "node:path";
import { LOG_PATH } from "./config.js";

let stream = null;
try {
  // LOG_PATH lives under DATA_DIR by default, which may not exist on a fresh box.
  mkdirSync(dirname(LOG_PATH), { recursive: true });
  stream = createWriteStream(LOG_PATH, { flags: "a" });
  stream.on("error", () => { stream = null; });
} catch {
  stream = null;
}

function emit(level, args) {
  const line = `${new Date().toISOString()} [digest] ${level}: ${args
    .map((a) => (typeof a === "string" ? a : JSON.stringify(a)))
    .join(" ")}`;
  (level === "ERROR" ? console.error : console.log)(line);
  try {
    stream?.write(line + "\n");
  } catch {
    /* logging must never crash the bot */
  }
}

export const log = {
  info: (...a) => emit("INFO", a),
  warn: (...a) => emit("WARN", a),
  error: (...a) => emit("ERROR", a),
};
