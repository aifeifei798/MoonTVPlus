// Synthetic oracle for the control-bar auto-hide fix.
//   node scripts/artplayer_hide_probe.cjs [--port 8899]
//
// Serves the pinned Artplayer 5.3.0 out of frontend/node_modules plus the 30 s
// clip in fixtures/probe.mp4, drives headless Chrome over CDP (start it with
// scripts/chrome_start.sh 9334 first), and runs the same gesture twice: with the
// `hover` listener the app installs and without it.
//
// Artplayer 5.3.0 hides the bottom bar inside `video:timeupdate`:
//   !setting.show && !controls.isHover && !isInput && playing && show
//     && Date.now() - controls.timer >= CONTROL_HIDE_TIME (3000)
// and controls.isHover is refreshed only by a `document` mousemove that lands on
// $bottom. A pointer that exits the window straight from the bar produces no
// such move, so isHover stays true and the bar never collapses. The fix clears
// isHover on Artplayer's own `hover` event (the player's mouseleave), which lets
// Artplayer's timer do the hiding.
//
// Expected: fix -> collapses ~3 s after the pointer leaves; nofix -> never.
const fs = require('fs');
const path = require('path');
const http = require('http');

// This file lives at <repo>/.opencode/skills/moontvplus-verification/scripts/,
// so the repo root is four levels up. MOONTVPLUS_ROOT overrides it.
const ROOT = process.env.MOONTVPLUS_ROOT || path.resolve(__dirname, '..', '..', '..', '..');
const CDP_PORT = process.env.CDP_PORT || '9334';
const CDP = 'http://127.0.0.1:' + CDP_PORT;
const PORT = Number(process.argv.find((a) => a.startsWith('--port'))?.split('=')[1] || process.argv[2] || 8899);

const ARTPLAYER = path.join(ROOT, 'frontend/node_modules/artplayer/dist/artplayer.js');
const CLIP = path.join(__dirname, '..', 'fixtures', 'probe.mp4');

const PAGE = `<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <style>html,body{margin:0;background:#111}#player{width:800px;height:450px;margin:60px auto}</style>
  </head>
  <body>
    <div id="player"></div>
    <script src="/artplayer.js"></script>
    <script>
      window.__probe = { events: [], art: null };
      const nofix = location.search.indexOf('nofix') >= 0;
      const art = new Artplayer({
        container: '#player',
        url: '/probe.mp4',
        autoplay: true,
        fullscreenWeb: true,
        setting: true,
        muted: true,
      });
      window.__probe.art = art;
      // the exact listener usePlayEngine.ts installs, toggled by ?nofix=1
      art.on('hover', (v) => {
        if (!v && !nofix) art.controls.isHover = false;
      });
      art.on('hover', (v) => window.__probe.events.push({ t: 'hover', v }));
      art.on('control', (v) => window.__probe.events.push({ t: 'control', v }));
      document.addEventListener('mousemove', (e) => {
        window.__probe.events.push({
          t: 'docmousemove',
          x: e.clientX,
          y: e.clientY,
          bottomInPath: e.composedPath().includes(art.template.$bottom),
          target: e.target.tagName + '.' + (e.target.className || ''),
        });
      }, true);
      window.__probe.state = () => {
        const bottom = art.template.$bottom;
        const r = bottom.getBoundingClientRect();
        return {
          show: art.controls.show,
          isHover: art.controls.isHover,
          timerAge: Date.now() - art.controls.timer,
          playing: art.playing,
          fullscreenWeb: !!art.fullscreenWeb,
          classes: art.template.$player.className,
          rect: { left: Math.round(r.left), top: Math.round(r.top), right: Math.round(r.right), bottom: Math.round(r.bottom) },
          viewport: { w: window.innerWidth, h: window.innerHeight },
        };
      };
    </script>
  </body>
</html>`;

function sleep(ms) {
  return new Promise((p) => setTimeout(p, ms));
}

function makeClient(url) {
  const ws = new WebSocket(url);
  const pending = new Map();
  const opened = new Promise((r) => ws.addEventListener('open', () => r()));
  ws.addEventListener('message', (ev) => {
    let m;
    try {
      m = JSON.parse(ev.data);
    } catch (_) {
      return;
    }
    if (m.id && pending.has(m.id)) {
      pending.get(m.id)(m);
      pending.delete(m.id);
    }
  });
  let id = 0;
  return {
    call: (method, params) =>
      opened
        .then(() =>
          new Promise((resolve) => {
            pending.set(++id, resolve);
            ws.send(JSON.stringify({ id, method, params: params || {} }));
          })
        )
        .then((m) => {
          if (m.error) throw new Error(method + ': ' + JSON.stringify(m.error));
          return m.result;
        }),
    close: () => ws.close(),
  };
}

async function run(page, label, nofix) {
  const evalIn = async (expr) => {
    const r = await page.call('Runtime.evaluate', {
      expression: expr,
      returnValues: true,
      awaitPromise: true,
    });
    if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails));
    return r.result.value;
  };
  const mouse = async (x, y) =>
    await page.call('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y });
  const state = async () => JSON.parse(await evalIn('JSON.stringify(window.__probe.state())'));

  await page.call('Page.navigate', { url: 'http://127.0.0.1:' + PORT + '/index.html' + (nofix ? '?nofix=1' : '') });
  for (let i = 0; i < 40; i++) {
    await sleep(250);
    if ((await state()).playing) break;
  }

  // web fullscreen: the player fills the viewport, so "leaving the player" is
  // "leaving the page"
  await page.call('Runtime.evaluate', { expression: 'window.__probe.art.fullscreenWeb = true; true' });
  await sleep(800);
  const s = await state();
  const W = s.viewport.w, H = s.viewport.h;

  // park the pointer on the bottom bar (only .art-progress/.art-controls have
  // pointer-events:auto, so sweep y until the hit test reports a hover)
  let parked = false;
  for (let y = s.rect.bottom - 5; y > s.rect.bottom - 90 && y > 0; y -= 8) {
    await mouse(Math.round((s.rect.left + s.rect.right) / 2), y);
    await sleep(300);
    const st = await state();
    if (st.isHover) {
      parked = true;
      console.log('[%s] pointer parked on the bar at y=%d: show=%s isHover=%s', label, y, st.show, st.isHover);
      break;
    }
  }
  if (!parked) {
    console.log('[%s] could not park the pointer on the bar; aborting', label);
    return null;
  }

  // one step out of the viewport: the page never sees another mousemove
  await mouse(Math.round(W / 2), H + 40);
  let hiddenAt = null;
  for (let i = 0; i < 40; i++) {
    await sleep(250);
    if (!(await state()).show) {
      hiddenAt = (i + 1) * 250;
      break;
    }
  }
  console.log(
    '[%s] pointer offscreen: bar collapsed %s ms (CONTROL_HIDE_TIME = 3000)',
    label,
    hiddenAt === null ? 'never (within 10 s)' : hiddenAt
  );
  return hiddenAt;
}

async function main() {
  if (!fs.existsSync(ARTPLAYER)) throw new Error('pinned artplayer missing: ' + ARTPLAYER + ' — run pnpm install in frontend/ first');
  if (!fs.existsSync(CLIP)) throw new Error('fixture clip missing: ' + CLIP);

  const server = http.createServer((req, res) => {
    if (req.url.startsWith('/artplayer.js')) {
      res.writeHead(200, { 'Content-Type': 'application/javascript' });
      res.end(fs.readFileSync(ARTPLAYER));
    } else if (req.url.startsWith('/probe.mp4')) {
      res.writeHead(200, { 'Content-Type': 'video/mp4' });
      res.end(fs.readFileSync(CLIP));
    } else {
      res.writeHead(200, { 'Content-Type': 'text/html' });
      res.end(PAGE);
    }
  });
  server.listen(PORT, '127.0.0.1');

  const list = await (await fetch(CDP + '/json/list')).json();
  const target = list.find((t) => t.type === 'page');
  if (!target) throw new Error('no page target; run scripts/chrome_start.sh 9334 first');
  const page = makeClient(target.webSocketDebuggerUrl);
  await sleep(300);
  await page.call('Page.enable', {});
  await page.call('Runtime.enable', {});

  const withFix = await run(page, 'fix   ', false);
  const withoutFix = await run(page, 'nofix ', true);

  console.log('');
  console.log(withFix !== null && withFix >= 2500 && withFix <= 4000 ? 'PASS: fix hides the bar on Artplayer\'s own 3 s timer' : 'FAIL: fix did not produce a ~3 s collapse');
  console.log(withoutFix === null ? 'PASS: without the listener the bar never collapses (the reported symptom)' : 'FAIL: upstream behaviour reproduced a collapse at ' + withoutFix + ' ms');

  page.close();
  server.close();
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
