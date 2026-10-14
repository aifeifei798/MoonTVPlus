// Mobile orientation harness: node scripts/cdp_orientation.cjs <auth> <play-path> [--landscape-at-load]
//
// Drives the "landscape -> fullscreen / portrait -> restore" behaviour added in
// frontend/src/app/play/usePlayEngine.ts, plus the 锁定 (lock) progress-bar fix in
// frontend/src/app/globals.css, against a live /play page.
//
// Headless Chrome cannot do native fullscreen (requestFullscreen() answers
// "Permissions check failed"), so only Artplayer's CSS fullscreenWeb fallback is
// observable here — which is exactly the path the code has to fall back to on
// iPhone, where HLS plays through MSE and webkitEnterFullscreen is a no-op.
// The emulated viewport never changes screen.orientation.type either, so the
// harness patches it and dispatches a synthetic `change` event.
//
// Start Chrome first: scripts/chrome_start.sh 9334
const CDP = 'http://127.0.0.1:' + (process.env.CDP_PORT || '9334');
const COOKIE = process.argv[2];
const PATH = process.argv[3] || '/play';
const LANDSCAPE_AT_LOAD = process.argv.includes('--landscape-at-load');

function sleep(ms) {
  return new Promise((p) => setTimeout(p, ms));
}

function makeClient(url) {
  const ws = new WebSocket(url);
  const pending = new Map();
  const opened = new Promise((resolve) => ws.addEventListener('open', resolve));
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
    call: (method, params) =>
      opened
        .then(() =>
          new Promise((resolve) => {
            const myId = ++id;
            pending.set(myId, resolve);
            ws.send(JSON.stringify({ id: myId, method, params: params || {} }));
          })
        )
        .then((m) => {
          if (m.error) throw new Error(method + ': ' + JSON.stringify(m.error));
          return m.result;
        }),
    close: () => ws.close(),
  };
}

async function evalx(page, expression) {
  const r = await page.call('Runtime.evaluate', {
    expression,
    returnByValue: true,
    awaitPromise: true,
  });
  return r.result && 'value' in r.result ? r.result.value : JSON.stringify(r);
}

// Player is fullscreen when either Artplayer class (or the real fullscreen
// element) is present. Mirrors isFullscreenEngaged() in usePlayEngine.ts.
const SNAP =
  '(function(){const p=document.querySelector(".art-video-player");const r=p.getBoundingClientRect();' +
  'return JSON.stringify({orient:screen.orientation.type,fs:p.classList.contains("art-fullscreen"),' +
  'fsWeb:p.classList.contains("art-fullscreen-web"),rect:[Math.round(r.width),Math.round(r.height)]});})()';

async function main() {
  if (!COOKIE) {
    console.log('usage: node cdp_orientation.cjs <auth-cookie-value> <play-path> [--landscape-at-load]');
    process.exit(2);
  }
  const list = await (await fetch(CDP + '/json/list')).json();
  const pageTarget = list.find((t) => t.type === 'page');
  if (!pageTarget) throw new Error('no page target — start Chrome with scripts/chrome_start.sh');
  const page = makeClient(pageTarget.webSocketDebuggerUrl);
  await sleep(200);

  await page.call('Page.enable', {});
  await page.call('Runtime.enable', {});
  await page.call('Page.addScriptToEvaluateOnNewDocument', {
    source:
      'window.__errs = [];' +
      'window.addEventListener("error", function(e){ window.__errs.push(String(e.message || "unknown")); });' +
      'window.addEventListener("unhandledrejection", function(e){ window.__errs.push("unhandledrejection " + String(e.reason && e.reason.message ? e.reason.message : e.reason)); });' +
      // Artplayer defines the fullscreenWeb setter on the instance; hooking
      // defineProperty is the only way to get hold of the instance from outside.
      '(function(){const dp=Object.defineProperty;' +
      'Object.defineProperty=function(o,k,d){if(k==="fullscreenWeb"&&d&&d.set)window.__art=o;return dp(o,k,d);};' +
      'const so=screen.orientation;let fake=' + (LANDSCAPE_AT_LOAD ? '"landscape-primary"' : '"portrait-primary"') + ';' +
      'Object.defineProperty(so,"type",{get:function(){return fake;},configurable:true});' +
      'window.__setOrientation=function(t){fake=t;so.dispatchEvent(new Event("change"));return so.type;};})();',
  });
  await page.call('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
  await page.call('Emulation.setUserAgentOverride', {
    userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1',
  });
  await page.call('Network.setCookie', { name: 'auth', value: COOKIE, path: '/', domain: '127.0.0.1' });
  await page.call('Page.navigate', { url: 'http://127.0.0.1:3000' + PATH });
  for (let i = 0; i < 40; i++) {
    await sleep(1000);
    if (await evalx(page, '!!document.querySelector(".art-video-player")')) break;
  }
  await sleep(5000);

  const checks = [];
  const check = (name, want, got) => {
    const ok = want === got;
    checks.push({ name, want, got, ok });
    console.log((ok ? 'PASS' : 'FAIL') + '  ' + name + '  want ' + want + ' got ' + got);
  };
  const snap = async () => JSON.parse(await evalx(page, SNAP));
  const fullscreen = async () => {
    const s = await snap();
    return s.fs || s.fsWeb;
  };

  if (LANDSCAPE_AT_LOAD) {
    check('loads landscape -> fullscreen', true, await fullscreen());
    await evalx(page, 'window.__setOrientation("portrait-primary")');
    await sleep(1500);
    check('portrait -> restored', false, await fullscreen());
  } else {
    check('loads portrait -> not fullscreen', false, await fullscreen());
    await evalx(page, 'window.__setOrientation("landscape-primary")');
    await sleep(1500);
    check('rotate landscape -> fullscreen', true, await fullscreen());
    await evalx(page, 'window.__setOrientation("portrait-primary")');
    await sleep(1500);
    check('rotate portrait -> restored', false, await fullscreen());
    await evalx(page, 'window.__setOrientation("landscape-primary")');
    await sleep(1500);
    check('rotate landscape again -> fullscreen', true, await fullscreen());
    // user exits fullscreen by hand while still landscape
    await evalx(page, '(function(){const a=window.__art;if(a)a.fullscreenWeb=false;})()');
    await sleep(1200);
    check('manual exit in landscape -> restored', false, await fullscreen());
    await evalx(page, 'window.__setOrientation("landscape-primary")');
    await sleep(1500);
    check('still landscape -> no re-entry after manual exit', false, await fullscreen());
    await evalx(page, 'window.__setOrientation("portrait-primary")');
    await sleep(1500);
    check('portrait after manual exit -> stays restored', false, await fullscreen());
    await evalx(page, 'window.__setOrientation("landscape-primary")');
    await sleep(1500);
    check('next landscape cycle -> fullscreen', true, await fullscreen());
  }

  // 锁定 hides the stray progress bar that leaks past .art-bottom's overflow.
  const lock = JSON.parse(
    await evalx(
      page,
      '(function(){const p=document.querySelector(".art-video-player");p.classList.add("art-lock");' +
      'const pr=p.querySelector(".art-progress");const r=pr.getBoundingClientRect();' +
      'return JSON.stringify({display:getComputedStyle(pr).display,w:Math.round(r.width),h:Math.round(r.height)});})()'
    )
  );
  check('lock hides progress display', 'none', lock.display);
  check('lock collapses progress rect', 0, lock.w);

  const errs = JSON.parse(await evalx(page, 'JSON.stringify(window.__errs)'));
  console.log('page errors:', errs.length ? errs : 'none');

  const failed = checks.filter((c) => !c.ok);
  console.log(checks.length - failed.length + '/' + checks.length + ' checks green');
  page.close();
  process.exit(failed.length ? 1 : 0);
}

main().catch((e) => {
  console.log('FATAL', e);
  process.exit(1);
});
