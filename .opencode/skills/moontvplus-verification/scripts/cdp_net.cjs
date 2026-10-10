// CDP harness that logs network events while loading a page: node cdp_net.cjs <auth> <path> <hostFilter> [wait-ms]
const CDP = 'http://127.0.0.1:' + (process.env.CDP_PORT || '9334');
const COOKIE = process.argv[2];
const PATH = process.argv[3];
const FILTER = process.argv[4] || '';
const WAIT = parseInt(process.argv[5] || '12000', 10);

function sleep(ms) {
  return new Promise((p) => setTimeout(p, ms));
}

const events = [];

function makeClient(url) {
  const ws = new WebSocket(url);
  const pending = new Map();
  const opened = new Promise((resolve) => {
    ws.addEventListener('open', () => resolve());
  });
  ws.addEventListener('message', (ev) => {
    let m;
    try {
      m = JSON.parse(ev.data);
    } catch (_) {
      return;
    }
    if (m.method && m.id === undefined) {
      events.push(m);
      return;
    }
    if (m.id && pending.has(m.id)) {
      const done = pending.get(m.id);
      pending.delete(m.id);
      done(m);
    }
  });
  let id = 0;
  return {
    call: (method, params) => {
      const myId = ++id;
      return opened
        .then(() => {
          return new Promise((resolve) => {
            pending.set(myId, resolve);
            ws.send(JSON.stringify({ id: myId, method, params: params || {} }));
          });
        })
        .then((m) => {
          if (m.error) throw new Error(method + ': ' + JSON.stringify(m.error));
          return m.result;
        });
    },
    close: () => ws.close(),
  };
}

async function main() {
  const list = await (await fetch(CDP + '/json/list')).json();
  const pageTarget = list.find((t) => t.type === 'page');
  if (!pageTarget) throw new Error('no page target');
  const page = makeClient(pageTarget.webSocketDebuggerUrl);
  await sleep(200);

  await page.call('Page.enable', {});
  await page.call('Network.enable', {});
  await page.call('Runtime.enable', {});
  await page.call('Page.addScriptToEvaluateOnNewDocument', {
    source:
      'window.__errs = [];' +
      'window.addEventListener("error", function(e){ window.__errs.push(String(e.message || "unknown") + " @ " + String(e.filename || "")); });',
  });
  await page.call('Network.setCookie', {
    name: 'auth',
    value: COOKIE,
    path: '/',
    domain: '127.0.0.1',
  });
  await page.call('Page.navigate', { url: 'http://127.0.0.1:3000' + PATH });
  await sleep(WAIT);

  const reqs = {};
  for (const e of events) {
    const p = e.params || {};
    const req = p.request || p.response || p.info;
    if (!req || !req.url) continue;
    if (FILTER && req.url.indexOf(FILTER) < 0) continue;
    const rec = reqs[req.url] || (reqs[req.url] = { sent: 0, recv: 0, err: [], info: null });
    if (e.method === 'Network.requestWillBeSent') rec.sent++;
    if (e.method === 'Network.responseReceived') {
      rec.recv++;
      rec.status = req.status;
      rec.type = p.type;
      const h = req.headers || {};
      rec.headers = {};
      for (const k of Object.keys(h)) {
        if (/access-control|content-type|cache|vary|x-|date|via|server/i.test(k)) rec.headers[k] = h[k];
      }
    }
    if (e.method === 'NetworkResourceLoadFailed' || e.method === 'Network.signedExchangeReceived') rec.err.push(e.method);
    if (e.method === 'Network.loadNetworkResource') rec.info = p.info;
  }
  console.log('--- network events matching', FILTER || '(all)');
  for (const u of Object.keys(reqs)) {
    const r = reqs[u];
    console.log(u.slice(0, 120), '| sent', r.sent, '| recv', r.recv, '| status', r.status, '| type', r.type);
    if (r.headers) console.log('   headers', JSON.stringify(r.headers).slice(0, 400));
    if (r.info) console.log('   info', JSON.stringify(r.info));
  }
  const fails = events.filter((e) => /fail|error/i.test(e.method));
  console.log('--- failure events', fails.length);
  fails.slice(0, 8).forEach((f) => console.log('  ', f.method, JSON.stringify(f.params).slice(0, 200)));

  const res = await page.call('Runtime.evaluate', {
    expression: '(window.__errs||[]).slice(0,8)',
    returnByValue: true,
  });
  console.log('errs', JSON.stringify(res && res.result && res.result.value));
  page.close();
}

main().catch((e) => {
  console.log('FATAL', e);
  process.exit(1);
});
