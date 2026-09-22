#!/usr/bin/env node
// CDP 客户端：驱动本地带 --remote-debugging-port=9222 的 Chrome，操作 DSW 页面
// 用法:
//   node cdp.mjs targets
//   node cdp.mjs goto <url>
//   node cdp.mjs eval <file.js|js 字符串>
//   node cdp.mjs cookies <out.json> [hostFilter]
import { readFileSync, writeFileSync } from 'node:fs';

const HOST = '127.0.0.1:9222';
const PATTERN = 'dsw-gateway-cn-hangzhou.data.aliyun.com';
const LAB = 'https://dsw-gateway-cn-hangzhou.data.aliyun.com/dsw-2203190/lab';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function listTargets() {
  const r = await fetch(`http://${HOST}/json/list`);
  return await r.json();
}

async function ensureTarget() {
  const pages = (await listTargets()).filter((t) => t.type === 'page');
  let t = pages.find((p) => (p.url || '').includes(PATTERN));
  if (!t) {
    const r = await fetch(`http://${HOST}/json/new?${encodeURIComponent(LAB)}`, { method: 'PUT' });
    t = await r.json();
    await sleep(1500);
  }
  return t;
}

class CDP {
  constructor(ws) {
    this.ws = ws;
    this.seq = 0;
    this.pending = new Map();
    this.waiters = [];
  }
  static async connect(url) {
    const ws = new WebSocket(url);
    await new Promise((res, rej) => {
      ws.onopen = res;
      ws.onerror = () => rej(new Error('CDP websocket error'));
    });
    const c = new CDP(ws);
    ws.onmessage = (ev) => {
      const m = JSON.parse(ev.data);
      if (m.id && c.pending.has(m.id)) {
        const { resolve, reject } = c.pending.get(m.id);
        c.pending.delete(m.id);
        m.error ? reject(new Error(JSON.stringify(m.error))) : resolve(m.result);
      } else if (m.method) {
        const keep = [];
        for (const w of c.waiters) (w.method === m.method ? (w.resolve(m.params), 0) : keep.push(w));
        c.waiters = keep;
      }
    };
    return c;
  }
  send(method, params = {}) {
    const id = ++this.seq;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }
  waitEvent(method, timeoutMs = 30000) {
    return new Promise((resolve, reject) => {
      const w = { method, resolve };
      this.waiters.push(w);
      setTimeout(() => {
        this.waiters = this.waiters.filter((x) => x !== w);
        reject(new Error(`timeout waiting ${method}`));
      }, timeoutMs);
    });
  }
  close() { try { this.ws.close(); } catch {} }
}

async function connectPage() {
  const t = await ensureTarget();
  const c = await CDP.connect(t.webSocketDebuggerUrl);
  await c.send('Page.enable').catch(() => {});
  await c.send('Runtime.enable').catch(() => {});
  return { cdp: c, target: t };
}

async function doGoto(url) {
  const { cdp, target } = await connectPage();
  await cdp.send('Page.navigate', { url });
  await sleep(2500);
  const r = await cdp.send('Runtime.evaluate', { expression: 'location.href', returnByValue: true });
  console.log(JSON.stringify({ targetId: target.id, url: r.result?.value }, null, 1));
  cdp.close();
}

async function doEval(arg) {
  const expr = arg.startsWith('@') ? readFileSync(arg.slice(1), 'utf8') : arg;
  const { cdp } = await connectPage();
  const r = await cdp.send('Runtime.evaluate', {
    expression: expr, awaitPromise: true, returnByValue: true, allowUnsafeEvalBlockedByCSP: true,
  });
  const out = r.exceptionDetails
    ? { error: r.exceptionDetails.text + ' :: ' + (r.exceptionDetails.exception?.description || '') }
    : { value: r.result?.value };
  console.log(typeof out.value === 'string' ? out.value : JSON.stringify(out, null, 1));
  cdp.close();
}

async function doCookies(outFile, hostFilter) {
  const { cdp } = await connectPage();
  const r = await cdp.send('Network.getAllCookies');
  const keep = r.cookies.filter((c) => {
    const d = c.domain.replace(/^\./, '');
    return !hostFilter || d.includes(hostFilter);
  });
  const jar = keep.map((c) => ({
    name: c.name, value: c.value, domain: c.domain, path: c.path,
    expires: c.expires, httpOnly: c.httpOnly, secure: c.secure,
  }));
  writeFileSync(outFile, JSON.stringify(jar, null, 1), 'utf8');
  console.log(`cookies=${jar.length} -> ${outFile}`);
  console.log(jar.map((c) => `${c.domain}\t${c.name}`).join('\n'));
  cdp.close();
}

const [cmd, ...args] = process.argv.slice(2);
try {
  if (cmd === 'targets') {
    const ts = await listTargets();
    console.log(ts.filter((t) => t.type === 'page').map((t) => `${t.id}\t${t.title}\t${t.url}`).join('\n'));
  } else if (cmd === 'goto') {
    await doGoto(args[0]);
  } else if (cmd === 'eval') {
    await doEval(args.join(' '));
  } else if (cmd === 'cookies') {
    await doCookies(args[0], args[1]);
  } else {
    console.log('usage: targets | goto <url> | eval <expr|@file> | cookies <out.json> [hostFilter]');
  }
} catch (e) {
  console.error('ERROR: ' + (e?.message || e));
  process.exit(1);
}
