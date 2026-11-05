// Telegram file download: getFile -> file URL -> bytes, as a media FETCHER for
// src/media.js. One AbortController covers both requests AND the body, so a stall
// anywhere is cut off at `timeoutMs` (fetch() resolves on headers; a timer cleared
// there would let a stalled body hang on undici's 300s default instead).
//
// Never put the bot token into an error message: errors land in the log and in chat.
import { MediaHttpError, MediaTransportError } from "./media.js";

export const TELEGRAM_API = "https://api.telegram.org";

/**
 * @param {{token:string, fileId:string, fetchImpl?:Function, apiBase?:string}} p
 * @returns {({timeoutMs}) => Promise<Buffer>}
 */
export function telegramFetcher({ token, fileId, fetchImpl = globalThis.fetch, apiBase = TELEGRAM_API }) {
  return async ({ timeoutMs }) => {
    const ac = new AbortController();
    const timer = setTimeout(
      () => ac.abort(Object.assign(new Error(`no answer within ${timeoutMs}ms`), { name: "TimeoutError" })),
      timeoutMs
    );
    try {
      let res;
      try {
        res = await fetchImpl(`${apiBase}/bot${token}/getFile?file_id=${encodeURIComponent(fileId)}`, {
          signal: ac.signal,
        });
      } catch (e) {
        throw new MediaTransportError("telegram", e);
      }
      let body;
      try {
        body = await res.json();
      } catch (e) {
        if (!res.ok) throw new MediaHttpError("telegram", res.status, "");
        throw new MediaTransportError("telegram", e);
      }
      if (!res.ok || !body?.ok) {
        throw new MediaHttpError("telegram", body?.error_code || res.status, `getFile: ${body?.description || ""}`);
      }
      const filePath = body.result?.file_path;
      if (!filePath) throw new MediaHttpError("telegram", 404, "getFile returned no file_path");

      let fres;
      try {
        fres = await fetchImpl(`${apiBase}/file/bot${token}/${filePath}`, { signal: ac.signal });
      } catch (e) {
        throw new MediaTransportError("telegram", e);
      }
      if (!fres.ok) {
        const text = await fres.text().catch(() => "");
        throw new MediaHttpError("telegram", fres.status, `file: ${text.slice(0, 200)}`);
      }
      try {
        return Buffer.from(await fres.arrayBuffer());
      } catch (e) {
        throw new MediaTransportError("telegram", e);
      }
    } finally {
      clearTimeout(timer);
    }
  };
}
