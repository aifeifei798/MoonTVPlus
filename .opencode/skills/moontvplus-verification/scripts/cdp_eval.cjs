// Generic CDP eval harness: node /tmp/opencode/cdp_eval.cjs <auth> <path> <expr> [wait-ms]
const CDP = 'http://127.0.0.1:' + (process.env.CDP_PORT || '9334');
const COOKIE = process.argv[2];
const PATH = process.argv[3] || '/';
const EXPR_RAW = process.argv[4] || 'JSON.stringify({len: document.body.innerHTML.length})';
const EXPR = EXPR_RAW.startsWith('@') ? require('fs').readFileSync(EXPR_RAW.slice(1), 'utf8') : EXPR_RAW;
const WAIT = parseInt(process.argv[5] || '6000', 10);

function sleep(ms) {
  return new Promise((p) => setTimeout(p, ms));
}

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
      return opened.then(() => {
        return new Promise((resolve) => {
          pending.set(myId, resolve);
          ws.send(JSON.stringify({ id: myId, method, params: params || {} }));
        });
      }).then((m) => {
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
      'window.addEventListener("error", function(e){ window.__errs.push(String(e.message || "unknown") + " @ " + String(e.filename || "")); });' +
      'window.addEventListener("unhandledrejection", function(e){ window.__errs.push("unhandledrejection " + String(e.reason && e.reason.message ? e.reason.message : e.reason)); });',
  });

  await page.call('Page.navigate', { url: 'http://127.0.0.1:3000' + PATH });
  await sleep(1200);
  await page.call('Network.setCookie', {
    name: 'auth',
    value: COOKIE,
    path: '/',
    domain: '127.0.0.1',
  });
  await page.call('Page.navigate', { url: 'http://127.0.0.1:3000' + PATH });
  await sleep(WAIT);

  const res = await page.call('Runtime.evaluate', { expression: EXPR, returnByValue: true, awaitPromise: true });
  console.log('page', PATH);
  console.log(res.result && 'value' in res.result ? res.result.value : JSON.stringify(res));
  page.close();
}

main().catch((e) => {
  console.log('FATAL', e);
  process.exit(1);
});
