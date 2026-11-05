#!/usr/bin/env node
// cdp.mjs - tiny dependency-free CDP driver used to set up the "Sleep Digest" Lark app
// in the developer console (open.larksuite.com), which has no public API for app config.
// Node >= 22 (global WebSocket). Talks to a Chrome started with --remote-debugging-port.
//
//   node cdp.mjs cookies <port> <cookies.json>     Network.setCookies (CDP CookieParam[])
//   node cdp.mjs nav     <port> <url> [waitMs]     navigate the first page target
//   node cdp.mjs eval    <port> <js>               Runtime.evaluate (awaitPromise), prints JSON
//   node cdp.mjs shot    <port> <out.png>          Page.captureScreenshot
//   node cdp.mjs text    <port>                    document.body.innerText
//   node cdp.mjs click   <port> <x> <y>            trusted mouse click at CSS pixel x,y
//   node cdp.mjs type    <port> <text>             Input.insertText into the focused element
//   node cdp.mjs key     <port> <key>              Enter|Tab|Escape|Backspace
import fs from "node:fs";

const [cmd, portArg, a3, a4, a5] = process.argv.slice(2);
const PORT = Number(portArg);
if (!cmd || !PORT) {
  console.error("usage: cdp.mjs <cmd> <port> ...");
  process.exit(64);
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function pageWS() {
  const targets = await (await fetch(`http://127.0.0.1:${PORT}/json`)).json();
  const page = targets.find((t) => t.type === "page" && !t.url.startsWith("devtools://"));
  if (!page) throw new Error("no page target");
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  let id = 0;
  const pending = new Map();
  ws.addEventListener("message", (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) {
      const p = pending.get(m.id);
      pending.delete(m.id);
      m.error ? p.rej(new Error(JSON.stringify(m.error))) : p.res(m.result);
    }
  });
  await new Promise((res, rej) => {
    ws.addEventListener("open", res);
    ws.addEventListener("error", rej);
  });
  const send = (method, params = {}) =>
    new Promise((res, rej) => {
      const mid = ++id;
      pending.set(mid, { res, rej });
      ws.send(JSON.stringify({ id: mid, method, params }));
    });
  return { ws, send };
}

const { ws, send } = await pageWS();
try {
  if (cmd === "cookies") {
    const cookies = JSON.parse(fs.readFileSync(a3, "utf8"));
    // Storage.* on the BROWSER target: Network.enable on a backgrounded about:blank tab
    // was observed to stall forever, and cookies are browser-wide anyway.
    const ver = await (await fetch(`http://127.0.0.1:${PORT}/json/version`)).json();
    const bws = new WebSocket(ver.webSocketDebuggerUrl);
    await new Promise((res, rej) => {
      bws.addEventListener("open", res);
      bws.addEventListener("error", rej);
    });
    const bsend = (method, params) =>
      new Promise((res, rej) => {
        const mid = Math.floor(Math.random() * 1e9);
        const on = (ev) => {
          const m = JSON.parse(ev.data);
          if (m.id !== mid) return;
          bws.removeEventListener("message", on);
          m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result);
        };
        bws.addEventListener("message", on);
        bws.send(JSON.stringify({ id: mid, method, params }));
      });
    await bsend("Storage.setCookies", { cookies });
    const { cookies: got } = await bsend("Storage.getCookies", {});
    bws.close();
    console.log(`set ${cookies.length} cookies; browser holds ${got.filter((c) => c.domain.includes("larksuite")).length} larksuite cookies`);
  } else if (cmd === "nav") {
    await send("Page.enable");
    await send("Page.navigate", { url: a3 });
    await sleep(Number(a4 || 6000));
    const { result } = await send("Runtime.evaluate", {
      expression: "JSON.stringify({title:document.title,url:location.href})",
      returnByValue: true,
    });
    console.log(result.value);
  } else if (cmd === "eval") {
    const { result, exceptionDetails } = await send("Runtime.evaluate", {
      expression: a3,
      returnByValue: true,
      awaitPromise: true,
    });
    if (exceptionDetails) console.log("EXCEPTION", JSON.stringify(exceptionDetails).slice(0, 2000));
    console.log(typeof result.value === "string" ? result.value : JSON.stringify(result.value));
  } else if (cmd === "shot") {
    const { data } = await send("Page.captureScreenshot", { format: "png" });
    fs.writeFileSync(a3, Buffer.from(data, "base64"));
    console.log(`wrote ${a3}`);
  } else if (cmd === "text") {
    const { result } = await send("Runtime.evaluate", { expression: "document.body.innerText", returnByValue: true });
    console.log(result.value);
  } else if (cmd === "click") {
    const x = Number(a3), y = Number(a4);
    for (const type of ["mouseMoved", "mousePressed", "mouseReleased"]) {
      await send("Input.dispatchMouseEvent", { type, x, y, button: "left", clickCount: 1 });
      await sleep(60);
    }
    console.log(`clicked ${x},${y}`);
  } else if (cmd === "clicktext" || cmd === "rect") {
    // Find the innermost visible element whose trimmed text equals (or, with a leading
    // "~", contains) the needle; a4 picks the Nth match (default 0). `rect` only reports.
    const needle = a3;
    const nth = Number(a4 || 0);
    const expr = `(() => {
      const raw = ${JSON.stringify(needle)};
      const contains = raw.startsWith("~");
      const n = contains ? raw.slice(1) : raw;
      const all = [...document.querySelectorAll("body *")].filter((el) => {
        const t = (el.innerText || el.value || "").trim();
        if (!(contains ? t.includes(n) : t === n)) return false;
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
      });
      const inner = all.filter((el) => !all.some((o) => o !== el && el.contains(o)));
      const el = inner[${nth}];
      if (!el) return JSON.stringify({ found: inner.length });
      el.scrollIntoView({ block: "center" });
      const r = el.getBoundingClientRect();
      return JSON.stringify({ found: inner.length, x: r.x + r.width / 2, y: r.y + r.height / 2, tag: el.tagName, cls: String(el.className).slice(0, 80) });
    })()`;
    const { result } = await send("Runtime.evaluate", { expression: expr, returnByValue: true });
    const info = JSON.parse(result.value);
    if (info.x == null) {
      console.log(`NOTFOUND ${needle} (matches=${info.found})`);
      process.exitCode = 3;
    } else if (cmd === "rect") {
      console.log(JSON.stringify(info));
    } else {
      for (const type of ["mouseMoved", "mousePressed", "mouseReleased"]) {
        await send("Input.dispatchMouseEvent", { type, x: info.x, y: info.y, button: "left", clickCount: 1 });
        await sleep(60);
      }
      console.log(`clicked "${needle}" at ${Math.round(info.x)},${Math.round(info.y)} (${info.tag}, matches=${info.found})`);
    }
  } else if (cmd === "settext") {
    // Set a React-controlled input/textarea (selector a3) to a4 via the native setter.
    const expr = `(() => {
      const el = document.querySelector(${JSON.stringify(a3)});
      if (!el) return "NOTFOUND";
      const proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      Object.getOwnPropertyDescriptor(proto, "value").set.call(el, ${JSON.stringify(a4 ?? "")});
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
      return "set:" + el.value;
    })()`;
    const { result } = await send("Runtime.evaluate", { expression: expr, returnByValue: true });
    console.log(result.value);
  } else if (cmd === "type") {
    await send("Input.insertText", { text: a3 });
    console.log("typed");
  } else if (cmd === "key") {
    const KEYS = {
      Enter: { key: "Enter", code: "Enter", windowsVirtualKeyCode: 13, text: "\r" },
      Tab: { key: "Tab", code: "Tab", windowsVirtualKeyCode: 9 },
      Escape: { key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 },
      Backspace: { key: "Backspace", code: "Backspace", windowsVirtualKeyCode: 8 },
    };
    const k = KEYS[a3];
    if (!k) throw new Error(`unknown key ${a3}`);
    await send("Input.dispatchKeyEvent", { type: "keyDown", ...k });
    await send("Input.dispatchKeyEvent", { type: "keyUp", ...k });
    console.log(`key ${a3}`);
  } else {
    throw new Error(`unknown cmd ${cmd}`);
  }
} finally {
  ws.close();
}
