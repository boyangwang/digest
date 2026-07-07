// Minimal logger → stdout + a log file (append). Never throws.
import { createWriteStream } from "node:fs";
import { LOG_PATH } from "./config.js";

let stream = null;
try {
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
