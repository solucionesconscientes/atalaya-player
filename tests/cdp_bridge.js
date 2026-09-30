// Tiny Chrome DevTools Protocol bridge for the browser tests (node >= 22: global WebSocket and fetch).
// Usage: node cdp_bridge.js <devtools port>. Reads one JSON command per stdin line and prints one JSON line back:
//   {"nav": "<url>"}            → Page.navigate
//   {"eval": "<js expression>"} → Runtime.evaluate (returnByValue, awaitPromise) → {"value": …} or {"error": …}
'use strict';
const readline = require('readline');

async function main() {
  const port = process.argv[2];
  const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  const page = targets.find((t) => t.type === 'page');
  if (!page) throw new Error('no page target');
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
  let id = 0;
  const pending = new Map();
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); }
  };
  const send = (method, params) => new Promise((resolve) => {
    id += 1;
    pending.set(id, resolve);
    ws.send(JSON.stringify({ id, method, params }));
  });
  await send('Page.enable', {});
  await send('Runtime.enable', {});
  console.log(JSON.stringify({ ready: true }));
  const rl = readline.createInterface({ input: process.stdin });
  for await (const line of rl) {
    if (!line.trim()) continue;
    const cmd = JSON.parse(line);
    let out;
    if (cmd.nav) {
      const r = await send('Page.navigate', { url: cmd.nav });
      out = r.error ? { error: r.error.message } : { value: true };
    } else if (cmd.eval) {
      const r = await send('Runtime.evaluate', { expression: cmd.eval, returnByValue: true, awaitPromise: true });
      if (r.error) out = { error: r.error.message };
      else if (r.result.exceptionDetails) out = { error: r.result.exceptionDetails.text + ' ' + JSON.stringify(r.result.exceptionDetails.exception || {}) };
      else out = { value: r.result.result.value };
    } else {
      out = { error: 'unknown command' };
    }
    console.log(JSON.stringify(out));
  }
  ws.close();
}

main().catch((e) => { console.log(JSON.stringify({ fatal: String(e) })); process.exit(1); });
