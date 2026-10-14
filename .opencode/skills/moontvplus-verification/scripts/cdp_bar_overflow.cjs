// Control-bar fit census: how far the right-hand controls hang outside the
// player at each emulated phone width.
//   node scripts/cdp_bar_overflow.cjs <auth-cookie-value> <play-path>
//       [--widths=320,360,414,430,440,480]
//
// Why this exists: `.art-controls` is a single flex row of three groups, and the
// middle one is the danmuku plugin's toggle + config icons. With `emitter: false`
// (play-utils.ts) that group is 28 + 24 gap + 28 = 80px plus the group's own
// `padding: 0 10px`, i.e. a min-content of 100px that `flex: 1` cannot shrink.
// Left group (play/next/time) is 160px, right group (setting/pip/fullscreenWeb/
// fullscreen) is 152px, so the row needs 412px while the container is
// player width - 2 * --art-padding. `.art-bottom` has `overflow: hidden`, so the
// excess is clipped rather than wrapped: the 全屏 button is simply unreachable.
//
// Measured with the shipped CSS before the fix (globals.css `@media (max-width:
// 412px) { .art-controls-center { display: none } }`):
//   320 -> overflow 92   360 -> overflow 52   414 -> 0   430 -> 0
// and after it, overflow is 0 at every width tested (the right group lands exactly
// on the player's right edge: left 160 + right 152 = 312 against a 300px container
// at 320px, which flexbox right-aligns).
//
// Exit code is non-zero if any tested width overflows.
const fs = require('fs');
const path = require('path');

const ROOT = process.env.MOONTVPLUS_ROOT || path.resolve(__dirname, '..', '..', '..', '..');
const CLIP = path.join(__dirname, '..', 'fixtures', 'probe.mp4');
const LOCAL_VIDEO = 'data:video/mp4;base64,' + fs.readFileSync(CLIP).toString('base64');

const CDP = 'http://127.0.0.1:' + (process.env.CDP_PORT || '9334');
const COOKIE = process.argv[2];
const PLAY = process.argv[3];

function flag(name, dflt) {
  for (const a of process.argv) {
    if (a.startsWith('--' + name + '=')) return a.slice(name.length + 3);
  }
  return dflt;
}
const WIDTHS = (flag('widths', '320,360,414,430,440,480')).split(',').map(Number);

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

async function main() {
  if (!COOKIE || !PLAY) {
    console.error('usage: node scripts/cdp_bar_overflow.cjs <auth-cookie-value> <play-path>');
    process.exit(2);
  }
  const list = await (await fetch(CDP + '/json/list')).json();
  const target = list.find((t) => t.type === 'page');
  if (!target) throw new Error('no page target; run scripts/chrome_start.sh 9334 first');
  const page = makeClient(target.webSocketDebuggerUrl);
  await sleep(300);

  await page.call('Page.enable', {});
  await page.call('Runtime.enable', {});
  await page.call('Network.enable', {});
  // tailwind.css keeps its URL across builds; without this the probe measures the
  // previous build's CSS.
  await page.call('Network.setCacheDisabled', { cacheDisabled: true });
  await page.call('Network.setCookie', { name: 'auth', value: COOKIE, path: '/', url: 'http://127.0.0.1:3000' });
  await page.call('Emulation.setUserAgentOverride', {
    userAgent:
      'Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36',
  });
  await page.call('Emulation.setTouchEmulationEnabled', { enabled: true });

  const evalIn = async (expr) => {
    const r = await page.call('Runtime.evaluate', { expression: expr, returnValues: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails));
    return r.result.value;
  };

  let bad = 0;
  for (const W of WIDTHS) {
    const H = Math.round(W * 1.4);
    await page.call('Emulation.setDeviceMetricsOverride', { width: W, height: H, deviceScaleFactor: 1, mobile: true });
    await page.call('Page.navigate', { url: 'http://127.0.0.1:3000' + PLAY });
    await sleep(9000);
    // The source CDN answers the manifest XHR with 404 + no ACAO here, so play the
    // local clip (same swap as cdp_control_hide.cjs) — a bar that never shows up
    // cannot be measured.
    await evalIn(`(() => {
      const inst = window.Artplayer.instances[0];
      const v = inst.video;
      if (v.hls) v.hls.destroy();
      v.removeAttribute('crossOrigin');
      v.removeAttribute('src');
      v.src = ${JSON.stringify(LOCAL_VIDEO)};
      v.muted = true;
      v.play();
      return true;
    })()`);
    await sleep(2500);
    // A tap on the video is what raises the bar on a phone (Artplayer:
    // `click -> isMobile ? this.toggle() : this.show = true`).
    await evalIn(`(() => {
      const inst = window.Artplayer.instances[0];
      inst.toggle();
      inst.video.currentTime = 0;
      inst.play();
      return true;
    })()`);
    await sleep(800);

    const out = JSON.parse(
      await evalIn(`(async () => {
        const inst = window.Artplayer.instances[0];
        const box = (el) => {
          const r = el.getBoundingClientRect();
          return { w: Math.round(r.width), left: Math.round(r.left), right: Math.round(r.right) };
        };
        const player = box(inst.template.$player);
        const right = box(inst.template.$controlsRight);
        const last = inst.template.$controlsRight.lastElementChild;
        return JSON.stringify({
          viewport: { w: window.innerWidth, h: window.innerHeight },
          player,
          controls: box(inst.template.$controls),
          left: box(inst.template.$controlsLeft),
          center: box(inst.template.$controlsCenter),
          centerDisplay: getComputedStyle(inst.template.$controlsCenter).display,
          right,
          overflowRight: Math.max(0, right.right - player.right),
          lastControl: { cls: last.className, box: box(last) },
        });
      })()`));
    // Counterfactual: put the middle group back (the rule the fix adds is
    // `display: none` at <=412px) so the "before" numbers are reproducible on the
    // shipped CSS rather than only in a git history.
    const before = JSON.parse(
      await evalIn(`(async () => {
        const inst = window.Artplayer.instances[0];
        const st = document.createElement('style');
        st.textContent = '.art-controls-center{display:flex !important}';
        document.head.appendChild(st);
        const box = (el) => {
          const r = el.getBoundingClientRect();
          return { w: Math.round(r.width), left: Math.round(r.left), right: Math.round(r.right) };
        };
        const r = box(inst.template.$controlsRight);
        const c = box(inst.template.$controlsCenter);
        document.head.removeChild(st);
        return JSON.stringify({ overflowRight: Math.max(0, r.right - box(inst.template.$player).right), center: c });
      })()`));
    const ok = out.overflowRight === 0;
    if (!ok) bad++;
    console.log(
      '%dx%d %s overflow=%d (without the fix: %d, center %dpx) right=%s center=%s (%s) last=%s',
      W,
      H,
      ok ? 'ok  ' : 'FAIL',
      out.overflowRight,
      before.overflowRight,
      before.center.w,
      JSON.stringify(out.right),
      JSON.stringify(out.center),
      out.centerDisplay,
      JSON.stringify(out.lastControl)
    );
  }

  console.log(bad ? '\n%d widths clip the right-hand controls' : '\nall widths fit');
  page.close();
  process.exit(bad ? 1 : 0);
}

main().catch((e) => {
  console.error('FATAL', e);
  process.exit(1);
});
