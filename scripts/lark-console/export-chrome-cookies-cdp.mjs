#!/usr/bin/env node
// export-chrome-cookies-cdp.mjs - decrypt macOS Chrome cookies for a domain and write them
// as a CDP `Network.setCookies` parameter array (keeps domain, path, secure, httpOnly,
// sameSite and expiry per cookie, which a flat Cookie header loses).
//
//   node export-chrome-cookies-cdp.mjs <domain-substring> <out.json> [profile]
//
// Same decryption as boyang-dev's scripts/chrome_cookies.mjs. The output file holds live
// session cookies: it is written 0600 and must be deleted after use.
import { execFileSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const [domain, outFile, profile = "Default"] = process.argv.slice(2);
if (!domain || !outFile) {
  console.error("usage: node export-chrome-cookies-cdp.mjs <domain> <out.json> [profile]");
  process.exit(64);
}

const pw = execFileSync("security", ["find-generic-password", "-w", "-s", "Chrome Safe Storage", "-a", "Chrome"])
  .toString()
  .trim();
const key = crypto.pbkdf2Sync(pw, "saltysalt", 1003, 16, "sha1");
const iv = Buffer.alloc(16, 0x20);

const base = path.join(os.homedir(), "Library/Application Support/Google/Chrome");
let dbPath = path.join(base, profile, "Cookies");
if (!fs.existsSync(dbPath)) dbPath = path.join(base, profile, "Network/Cookies");
const copy = path.join(os.tmpdir(), `cookies-copy-${process.pid}.db`);
fs.copyFileSync(dbPath, copy);

const SEP = "\x1f";
const sql =
  "SELECT host_key,name,path,is_secure,is_httponly,samesite,expires_utc,quote(encrypted_value) " +
  `FROM cookies WHERE host_key LIKE '%${domain.replace(/'/g, "")}%';`;
const rows = execFileSync("sqlite3", ["-separator", SEP, copy, sql]).toString().split("\n").filter(Boolean);
fs.rmSync(copy, { force: true });

function decrypt(hexLiteral) {
  const m = /^X'([0-9A-Fa-f]*)'$/.exec(hexLiteral);
  if (!m) return "";
  const buf = Buffer.from(m[1], "hex");
  const tag = buf.subarray(0, 3).toString();
  if (tag !== "v10" && tag !== "v11") return buf.toString("utf8");
  const d = crypto.createDecipheriv("aes-128-cbc", key, iv);
  d.setAutoPadding(false);
  let pt = Buffer.concat([d.update(buf.subarray(3)), d.final()]);
  const pad = pt[pt.length - 1];
  if (pad >= 1 && pad <= 16) pt = pt.subarray(0, pt.length - pad);
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(pt);
  } catch {
    return pt.subarray(32).toString("utf8");
  }
}

const SAMESITE = { "-1": undefined, 0: "None", 1: "Lax", 2: "Strict" };
const out = [];
for (const row of rows) {
  const [host, name, cpath, secure, httpOnly, samesite, expiresUtc, ev] = row.split(SEP);
  const value = decrypt(ev);
  if (!value) continue;
  const c = { name, value, domain: host, path: cpath || "/", secure: secure === "1", httpOnly: httpOnly === "1" };
  const ss = SAMESITE[samesite];
  if (ss) c.sameSite = ss;
  const exp = Number(expiresUtc);
  if (exp > 0) c.expires = Math.floor(exp / 1e6 - 11644473600);
  out.push(c);
}
fs.writeFileSync(outFile, JSON.stringify(out), { mode: 0o600 });
console.error(`wrote ${out.length} cookies for *${domain}* -> ${outFile}`);
