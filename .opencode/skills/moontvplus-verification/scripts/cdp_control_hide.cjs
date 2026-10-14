// Live check of the control-bar auto-hide fix on a real /play page.
//   node scripts/cdp_control_hide.cjs <auth-cookie-value> <play-path>
//
// Artplayer 5.3.0 hides the bottom bar inside `video:timeupdate`:
//   !setting.show && !controls.isHover && !isInput && playing && show
//     && Date.now() - controls.timer >= CONTROL_HIDE_TIME (3000)
// and `controls.isHover` is only ever refreshed by a `document` mousemove
// (`includeFromEvent(e, $bottom)`). A pointer that exits the window straight
// from the bar never produces a mousemove inside the page, so isHover stays
// true and the bar stays on screen forever. In web fullscreen the player fills
// the viewport, so "leaving the player" IS "leaving the page" — which is why the
// symptom only shows up there.
//
// The probe therefore measures both halves:
//   1. the gate: does the shipped bundle clear isHover when the pointer leaves?
//   2. the timing: does the bar actually collapse, and how long after?
//
// The source CDN answers the manifest XHR with 404 + no ACAO in this browser,
// so the app's own HLS source never plays and `playing` (hence timeupdate) is
// false. The probe destroys the hls.js instance and points the <video> at the
// 30 s clip in fixtures/probe.mp4 (inlined as a data URL, so no extra server is
// needed) so the real page really plays and Artplayer's own gate can run.
const fs = require('fs');
const path = require('path');

// This file lives at <repo>/.opencode/skills/moontvplus-verification/scripts/,
// so the repo root is four levels up. MOONTVPLUS_ROOT overrides it.
const ROOT = process.env.MOONTVPLUS_ROOT || path.resolve(__dirname, '..', '..', '..', '..');
const CLIP = path.join(__dirname, '..', 'fixtures', 'probe.mp4');

const CDP_PORT = process.env.CDP_PORT || '9334';
const CDP = 'http://127.0.0.1:' + CDP_PORT;
const COOKIE = process.argv[2];
const PATH = process.argv[3] || '/';
const LOCAL_VIDEO = 'data:video/mp4;base64,' + fs.readFileSync(CLIP).toString('base64');

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

const SNAPSHOT = `JSON.stringify((() => {
  const inst = (window.Artplayer && window.Artplayer.instances || [])[0];
  if (!inst) return { ready: false, errs: window.__errs || [] };
  const player = inst.template.$player;
  const bottom = inst.template.$bottom;
  const r = bottom.getBoundingClientRect();
  return {
    ready: true,
    show: inst.controls.show,
    isHover: inst.controls.isHover,
    playing: inst.playing,
    videoSrc: inst.video ? inst.video.src : null,
    paused: inst.video ? inst.video.paused : null,
    currentTime: inst.video ? Math.round(inst.video.currentTime * 100) / 100 : null,
    duration: inst.video ? inst.video.duration : null,
    timerAge: Date.now() - inst.controls.timer,
    fullscreenWeb: !!inst.fullscreenWeb,
    classes: player.className,
    bottomOpacity: getComputedStyle(bottom).opacity,
    rect: { left: Math.round(r.left), top: Math.round(r.top), right: Math.round(r.right), bottom: Math.round(r.bottom) },
    viewport: { w: window.innerWidth, h: window.innerHeight },
    lastMove: window.__lastMove || null,
    errs: window.__errs || [],
  };
})());`;

async function main() {
  const list = await (await fetch(CDP + '/json/list')).json();
  const target = list.find((t) => t.type === 'page');
  if (!target) throw new Error('no page target; run scripts/chrome_start.sh 9334');
  const page = makeClient(target.webSocketDebuggerUrl);
  await sleep(300);
  await page.call('Page.enable', {});
  await page.call('Runtime.enable', {});
  await page.call('Network.enable', {});
  // records the last mousemove's hit target so the probe can prove the pointer
  // really landed on the control bar (only .art-progress/.art-controls have
  // pointer-events:auto, so the exact row matters)
  await page.call('Page.addScriptToEvaluateOnNewDocument', {
    source: `
      document.addEventListener('mousemove', (e) => {
        const inst = (window.Artplayer && window.Artplayer.instances || [])[0];
        window.__lastMove = {
          x: e.clientX,
          y: e.clientY,
          target: e.target.tagName + '.' + (e.target.className || ''),
          bottomInPath: inst ? e.composedPath().includes(inst.template.$bottom) : null,
        };
      }, true);
    `,
  });
  await page.call('Network.setCookie', {
    name: 'auth',
    value: COOKIE,
    path: '/',
    url: 'http://127.0.0.1:3000',
  });
  await page.call('Page.navigate', { url: 'http://127.0.0.1:3000' + PATH });
  await sleep(9000);

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

  // Make the shipped page actually play: the app's HLS source is a blob this
  // browser cannot fetch, and Artplayer's reconnect logic puts that blob back
  // whenever the <video> errors, so destroy hls.js first. The app sets
  // crossOrigin="anonymous" (moreVideoAttr) and the local probe server sends no
  // ACAO, so that attribute has to go too or the swap silently fails.
  await evalIn(`
    (() => {
      const inst = window.Artplayer.instances[0];
      if (!inst) return false;
      const v = inst.video;
      if (v.hls) v.hls.destroy();
      v.removeAttribute('crossOrigin');
      v.removeAttribute('src');
      v.src = ${JSON.stringify(LOCAL_VIDEO)};
      v.muted = true; // headless autoplay is blocked with sound
      v.play();
      return true;
    })()
  `);
  await sleep(4000);

  // PlayStatusView's VideoLoadingMask (z-[120]) is React state keyed on the
  // original source; it covers the player and swallows every mouse event. It is
  // unrelated to the hide logic being measured, so the probe takes it out of the
  // way and reports whether it was there.
  const masked = await evalIn('document.querySelectorAll(".z-\\\\[120\\\\]").length');
  await evalIn('document.querySelectorAll(".z-\\\\[120\\\\]").forEach((e) => (e.style.display = "none")); true');

  const s0 = JSON.parse(await evalIn(SNAPSHOT));
  if (!s0.ready) {
    console.log('player never mounted:', s0);
    return;
  }
  const W = s0.viewport.w, H = s0.viewport.h;
  console.log('mounted: playing=%s show=%s isHover=%s src=%s rect=%s viewport=%s mask=%s errs=%s',
    s0.playing, s0.show, s0.isHover, s0.videoSrc, JSON.stringify(s0.rect), JSON.stringify(s0.viewport), masked, s0.errs.length);

  // 1. park the pointer on the bottom control bar
  let barPoint = null;
  for (let y = s0.rect.bottom - 5; y > s0.rect.bottom - 90 && y > 0; y -= 8) {
    await mouse(Math.round((s0.rect.left + s0.rect.right) / 2), y);
    await sleep(300);
    const s = JSON.parse(await evalIn(SNAPSHOT));
    if (s.isHover) {
      barPoint = [Math.round((s0.rect.left + s0.rect.right) / 2), y];
      console.log('pointer on bar at y=%d: show=%s isHover=%s target=%s', y, s.show, s.isHover, JSON.stringify(s.lastMove));
      break;
    }
  }
  if (!barPoint) {
    const s = JSON.parse(await evalIn(SNAPSHOT));
    console.log('never got isHover=true on the bar; last move:', JSON.stringify(s.lastMove));
    return;
  }

  // 2. web fullscreen, then the pointer leaves the viewport in one step
  await evalIn('window.Artplayer.instances[0].fullscreenWeb = true; true');
  await sleep(800);
  const s2 = JSON.parse(await evalIn(SNAPSHOT));
  await mouse(Math.round(W / 2), s2.rect.bottom - 20);
  await sleep(600);
  const s2b = JSON.parse(await evalIn(SNAPSHOT));
  console.log('web fullscreen, on bar: show=%s isHover=%s fullscreenWeb=%s lastMove=%s',
    s2b.show, s2b.isHover, s2b.fullscreenWeb, JSON.stringify(s2b.lastMove));

  await mouse(Math.round(W / 2), H + 40);
  await sleep(1200);
  const s3 = JSON.parse(await evalIn(SNAPSHOT));
  console.log('pointer offscreen:   show=%s isHover=%s  (isHover must be false)', s3.show, s3.isHover);

  // 3. timing: how long after the pointer leaves does the bar collapse?
  await evalIn('window.Artplayer.instances[0].controls.show = true; true');
  await evalIn('(() => { const i = window.Artplayer.instances[0]; if (i.video.paused) i.video.play(); return true; })()');
  await mouse(Math.round(W / 2), s2.rect.bottom - 20);
  await sleep(500);
  const t0 = Date.now();
  await mouse(Math.round(W / 2), H + 40);
  let hiddenAt = null;
  for (let i = 0; i < 24; i++) {
    await sleep(250);
    const s = JSON.parse(await evalIn(SNAPSHOT));
    if (!s.show) {
      hiddenAt = Date.now() - t0;
      break;
    }
    if (i % 4 === 3) {
      console.log('  still visible at %d ms: isHover=%s timerAge=%d playing=%s paused=%s t=%s/%s classes=%s',
        Date.now() - t0, s.isHover, s.timerAge, s.playing, s.paused, s.currentTime, s.duration, s.classes);
    }
  }
  console.log('shipped bundle: bar collapsed %s ms after the pointer left the page (CONTROL_HIDE_TIME = 3000)', hiddenAt);

  // 4. pointer comes back inside the player: the bar must come back
  await mouse(Math.round(W / 2), Math.round(H / 2));
  await sleep(600);
  const s4 = JSON.parse(await evalIn(SNAPSHOT));
  console.log('pointer back inside: show=%s isHover=%s', s4.show, s4.isHover);

  // 5. counterfactual: pin isHover to the value upstream leaves behind and
  //    dispatch no further mousemove (exactly what happens when the pointer
  //    exits the window and the page never sees another move) — the bar then
  //    stays up forever, which is the reported symptom.
  await evalIn('window.Artplayer.instances[0].controls.isHover = true; true');
  await evalIn('window.Artplayer.instances[0].controls.show = true; true');
  await sleep(6000);
  const s6 = JSON.parse(await evalIn(SNAPSHOT));
  console.log('isHover pinned, no mousemove (upstream state): show=%s bottomOpacity=%s classes=%s', s6.show, s6.bottomOpacity, s6.classes);

  page.close();
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
