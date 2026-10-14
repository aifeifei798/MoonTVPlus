// Mobile-emulated census of the player control bar on a real /play page.
//   node scripts/cdp_mobile_player.cjs <auth-cookie-value> <play-path>
//
// Knobs: --width=414 --height=896 (default is a portrait phone), --landscape
// emulates a landscape phone (896x414).
//
// Why this exists: globals.css had a `@media (max-width: 768px)` block that
// `display: none !important`'d .art-control-pip and .art-control-fullscreenWeb,
// so a phone never saw the 画中画 / 网页全屏 buttons at all. Artplayer adds both
// controls regardless of isMobile (only .art-control-screenshot is gated on
// !isMobile), so the hiding was CSS-only and the buttons work when shown.
//
// The probe therefore measures three things a screenshot cannot:
//   1. which right-hand controls are actually rendered and where they land
//      (the bar must not overflow the player on a 414px-wide phone);
//   2. whether tapping 网页全屏 really engages web fullscreen (class + the
//      player covering the viewport + the sticky top nav being behind it);
//   3. whether tapping 画中画 reaches the browser PiP API, both while the video
//      is playing and while it is paused.
//
// Two CDP traps make a naive version of this probe report a false failure:
//   * `element.click()` from Runtime.evaluate is not a user gesture, and
//     requestPictureInPicture() requires transient activation, so every tap here
//     is dispatched through Input.dispatchTouchEvent;
//   * on a phone the control bar is only up after a tap on the video (Artplayer:
//     `click -> isMobile ? this.toggle() : this.show = true`), so the probe taps
//     the video first; otherwise the controls are translated by
//     --art-bottom-offset below the player and the tap misses them.
//
// The source CDN answers the manifest XHR with 404 + no ACAO in this browser,
// so the probe swaps the <video> for fixtures/probe.mp4 (data URL) exactly like
// cdp_control_hide.cjs does; without that the video never plays and PiP cannot
// be exercised at all.
const fs = require('fs');
const path = require('path');

const ROOT = process.env.MOONTVPLUS_ROOT || path.resolve(__dirname, '..', '..', '..', '..');
const CLIP = path.join(__dirname, '..', 'fixtures', 'probe.mp4');

const CDP_PORT = process.env.CDP_PORT || '9334';
const CDP = 'http://127.0.0.1:' + CDP_PORT;
const COOKIE = process.argv[2];
const PATH_ARG = process.argv[3] || '/play';

function flag(name, dflt) {
  for (const a of process.argv) {
    if (a.startsWith('--' + name + '=')) return a.slice(name.length + 3);
  }
  return dflt;
}

const LANDSCAPE = process.argv.includes('--landscape');
const WIDTH = parseInt(flag('width', LANDSCAPE ? '896' : '414'), 10);
const HEIGHT = parseInt(flag('height', LANDSCAPE ? '414' : '896'), 10);
const UA =
  'Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) ' +
  'Chrome/120.0.0.0 Mobile Safari/537.36';

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

// Runs inside the page. Reports, for every right-hand control, the computed
// display and its rendered box, plus the bar's own extents.
const BAR_CENSUS = `JSON.stringify((() => {
  const inst = (window.Artplayer && window.Artplayer.instances || [])[0];
  if (!inst) return { ready: false, errs: window.__errs || [] };
  const player = inst.template.$player;
  const controls = inst.template.$controls;
  const right = inst.template.$controlsRight;
  const pr = player.getBoundingClientRect();
  const items = Array.from(player.querySelectorAll('.art-control')).map((el) => {
    const r = el.getBoundingClientRect();
    return {
      name: (el.className || '').split(/\\s+/).find((c) => c.startsWith('art-control-')) || 'art-control',
      display: getComputedStyle(el).display,
      visible: r.width > 0 && r.height > 0,
      box: { w: Math.round(r.width), h: Math.round(r.height), left: Math.round(r.left), right: Math.round(r.right) },
    };
  });
  const rr = right ? right.getBoundingClientRect() : null;
  return {
    ready: true,
    viewport: { w: window.innerWidth, h: window.innerHeight },
    orientation: (window.screen && window.screen.orientation) ? window.screen.orientation.type : null,
    isMobile: !!(window.navigator.userAgent.match(/Mobile|Android|iPhone/i)),
    fullscreenWeb: !!inst.fullscreenWeb,
    fullscreen: !!inst.fullscreen,
    classes: inst.template.$player.className,
    playerBox: { w: Math.round(pr.width), h: Math.round(pr.height), left: Math.round(pr.left), right: Math.round(pr.right) },
    controlsRightBox: rr ? { w: Math.round(rr.width), left: Math.round(rr.left), right: Math.round(rr.right) } : null,
    overflowRight: rr ? Math.max(0, Math.round(rr.right - pr.right)) : null,
    controls: items,
    pipEnabled: document.pictureInPictureEnabled,
    videoHasPipApi: typeof (inst.video && inst.video.requestPictureInPicture) === 'function',
    videoHasWebkitPresentation: !!(inst.video && inst.video.webkitSupportsPresentationMode),
    playing: inst.playing,
    paused: inst.video ? inst.video.paused : null,
    errs: window.__errs || [],
  };
})());`;

async function main() {
  if (!COOKIE) {
    console.error('usage: node scripts/cdp_mobile_player.cjs <auth-cookie-value> <play-path>');
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
  // The profile has a disk cache, and tailwind.css keeps its URL across builds;
  // without this the probe measures the CSS from the previous build.
  await page.call('Network.setCacheDisabled', { cacheDisabled: true });

  // Emulate a phone before navigating: viewport, mobile flag, UA, touch.
  await page.call('Emulation.setDeviceMetricsOverride', {
    width: WIDTH,
    height: HEIGHT,
    deviceScaleFactor: 1,
    mobile: true,
  });
  await page.call('Emulation.setUserAgentOverride', { userAgent: UA });
  await page.call('Emulation.setTouchEmulationEnabled', { enabled: true });

  await page.call('Network.setCookie', {
    name: 'auth',
    value: COOKIE,
    path: '/',
    url: 'http://127.0.0.1:3000',
  });
  await page.call('Page.navigate', { url: 'http://127.0.0.1:3000' + PATH_ARG });
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

  // Make the page actually play (same swap as cdp_control_hide.cjs).
  await evalIn(`
    (() => {
      const inst = window.Artplayer.instances[0];
      if (!inst) return false;
      const v = inst.video;
      if (v.hls) v.hls.destroy();
      v.removeAttribute('crossOrigin');
      v.removeAttribute('src');
      v.src = ${JSON.stringify(LOCAL_VIDEO)};
      v.muted = true;
      v.play();
      return true;
    })()
  `);
  await sleep(4000);
  await evalIn('document.querySelectorAll(".z-\\\\[120\\\\]").forEach((e) => (e.style.display = "none")); true');

  // A real tap, not `element.click()`. requestPictureInPicture() is gated on
  // transient activation ("Must be handling a user gesture if there isn't
  // already an element in Picture-in-Picture"), which a Runtime.evaluate
  // `.click()` does not provide, so every interaction here goes through
  // Input.dispatchTouchEvent.
  const tap = async (selector) => {
    const box = JSON.parse(
      await evalIn(`(async () => {
        const inst = window.Artplayer.instances[0];
        const btn = inst.template.$player.querySelector('${selector}');
        if (!btn) return JSON.stringify({ ok: false });
        const r = btn.getBoundingClientRect();
        return JSON.stringify({
          ok: true,
          x: Math.round(r.left + r.width / 2),
          y: Math.round(r.top + r.height / 2),
        });
      })()`)
    );
    if (!box.ok) return false;
    console.log('tap %s at %d,%d', selector, box.x, box.y);
    await page.call('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: box.x, y: box.y }] });
    await page.call('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
    return true;
  };

  // The clip is short; rewind so every tap lands on a playing video.
  const rewindAndPlay = () =>
    evalIn(`(() => {
      const v = window.Artplayer.instances[0].video;
      v.currentTime = 0;
      v.play();
      return true;
    })()`);

  // On a phone the control bar is only up after a tap on the video: Artplayer's
  // control module registers `click -> isMobile ? this.toggle() : this.show = true`,
  // and while neither `art-control-show` nor `art-hover` is set the controls are
  // translated by --art-bottom-offset (20px) below the player, so a probe that
  // taps a button without first "touching the screen" measures a bar that is not
  // where the user sees it.
  const controlsUp = async () =>
    JSON.parse(
      await evalIn(`(async () => {
        const inst = window.Artplayer.instances[0];
        const el = inst.template.$player.querySelector('.art-control-pip');
        if (!el) return JSON.stringify({ ready: false });
        const r = el.getBoundingClientRect();
        return JSON.stringify({
          ready: true,
          shown: r.bottom <= window.innerHeight + 1 && r.top >= 0,
          top: Math.round(r.top),
          classes: inst.template.$player.className,
        });
      })()`)
    );

  const showControls = async () => {
    for (let i = 0; i < 4; i++) {
      const st = await controlsUp();
      if (st.ready && st.shown) return st;
      await tap('.art-video');
      await sleep(700);
      await rewindAndPlay();
    }
    return await controlsUp();
  };

  // --- what the browser says when PiP is called WITHOUT a user gesture ---
  // Runs before any tap so there is no transient activation yet: this is the
  // reason the probe taps with Input.dispatchTouchEvent instead of .click().
  const api = JSON.parse(await evalIn(`(async () => {
    const v = window.Artplayer.instances[0].video;
    const out = {};
    try { await v.requestPictureInPicture(); out.gestureless = 'entered'; }
    catch (e) { out.gestureless = e.name + ': ' + e.message; }
    return JSON.stringify(out);
  })()`));
  console.log('requestPictureInPicture with no user gesture -> %s (the button must be tapped, not called from script)', api.gestureless);
  await evalIn('document.exitPictureInPicture().catch(() => {}); true');
  await sleep(1000);

  const bar = await showControls();
  console.log('control bar up after tapping the video: %s (pip control top=%s)', bar.shown, bar.top);

  const census = JSON.parse(await evalIn(BAR_CENSUS));
  console.log('=== control bar census (%dx%d, UA mobile=%s) ===', WIDTH, HEIGHT, census.isMobile);
  if (!census.ready) {
    console.log('player never mounted:', census);
    return;
  }
  console.log('viewport=%s player=%s controlsRight=%s overflowRight=%s',
    JSON.stringify(census.viewport), JSON.stringify(census.playerBox),
    JSON.stringify(census.controlsRightBox), census.overflowRight);
  console.log('orientation=%s fullscreenWeb=%s fullscreen=%s classes=%s',
    census.orientation, census.fullscreenWeb, census.fullscreen, census.classes);
  console.log('pictureInPictureEnabled=%s requestPictureInPicture=%s webkitPresentationMode=%s playing=%s paused=%s',
    census.pipEnabled, census.videoHasPipApi, census.videoHasWebkitPresentation, census.playing, census.paused);

  const fmt = (c) =>
    '  ' + c.name.padEnd(24) + ' display=' + String(c.display).padEnd(6) +
    ' visible=' + c.visible + ' box=' + JSON.stringify(c.box);
  for (const c of census.controls) console.log(fmt(c));

  const byName = (n) => census.controls.find((c) => c.name === n);
  const pip = byName('art-control-pip');
  const webFs = byName('art-control-fullscreenWeb');
  const fullscreen = byName('art-control-fullscreen');

  const pass = [];
  const fail = [];
  const check = (ok, msg) => (ok ? pass : fail).push(msg);

  check(pip && pip.display !== 'none', '画中画 button is rendered on a phone (display=' + (pip ? pip.display : 'absent') + ')');
  check(webFs && webFs.display !== 'none', '网页全屏 button is rendered on a phone (display=' + (webFs ? webFs.display : 'absent') + ')');
  check(fullscreen && fullscreen.display !== 'none', '全屏 button is rendered on a phone');
  check(!census.overflowRight, 'right-hand controls do not overflow the player (overflow=' + census.overflowRight + ')');
  check(bar.ready && bar.shown, 'the control bar is up after tapping the video (Artplayer toggles it on mobile click)');
  check(
    census.controls.filter((c) => c.visible).every((c) => c.box.left >= 0 && c.box.right <= census.viewport.w),
    'every visible control sits inside the viewport width'
  );

  const playerState = async () =>
    JSON.parse(
      await evalIn(`JSON.stringify((() => {
        const inst = window.Artplayer.instances[0];
        const p = inst.template.$player.getBoundingClientRect();
        const hit = document.elementFromPoint(Math.round(window.innerWidth / 2), 10);
        return {
          fullscreenWeb: !!inst.fullscreenWeb,
          classes: inst.template.$player.className,
          player: { w: Math.round(p.width), h: Math.round(p.height), left: Math.round(p.left), top: Math.round(p.top) },
          viewport: { w: window.innerWidth, h: window.innerHeight },
          topHit: hit ? hit.tagName + '.' + (hit.className || '').split(' ')[0] : null,
          insidePlayer: hit ? inst.template.$player.contains(hit) : false,
          errs: window.__errs || [],
        };
      })());`)
    );

  // --- web fullscreen ---
  // The page can already be in it: the orientation listener fires when Chrome's
  // emulated viewport flips, so normalise to "not fullscreen" before tapping.
  if (webFs && webFs.visible) {
    if (census.fullscreenWeb) {
      await tap('.art-control-fullscreenWeb');
      await sleep(1200);
      const off = await playerState();
      console.log('after first 网页全屏 tap (was in it): fullscreenWeb=%s player=%s', off.fullscreenWeb, JSON.stringify(off.player));
      check(!off.fullscreenWeb, 'tapping 网页全屏 while in it exits web fullscreen');
    }

    await tap('.art-control-fullscreenWeb');
    await sleep(1200);
    const on = await playerState();
    console.log('after tapping 网页全屏: fullscreenWeb=%s player=%s viewport=%s topHit=%s insidePlayer=%s',
      on.fullscreenWeb, JSON.stringify(on.player), JSON.stringify(on.viewport), on.topHit, on.insidePlayer);
    check(on.fullscreenWeb, 'tapping 网页全屏 engages web fullscreen');
    check(Math.abs(on.player.w - on.viewport.w) <= 2 && Math.abs(on.player.h - on.viewport.h) <= 2,
      'web fullscreen covers the viewport (' + on.player.w + 'x' + on.player.h + ')');
    check(on.insidePlayer, 'the top of the screen is the player, not the sticky nav (topHit=' + on.topHit + ')');

    await tap('.art-control-fullscreenWeb');
    await sleep(1000);
    const off = await playerState();
    check(!off.fullscreenWeb, 'tapping 网页全屏 again exits web fullscreen');
    check(off.player.h < off.viewport.h, 'exiting web fullscreen restores the inline player box (' + off.player.w + 'x' + off.player.h + ')');
  }

  // --- PiP: tap the button with a real gesture and see what the browser does ---
  if (pip && pip.visible) {
    await rewindAndPlay();
    await sleep(1500);
    await tap('.art-control-pip');
    await sleep(3000);
    const s = JSON.parse(await evalIn(`JSON.stringify((() => {
      const inst = window.Artplayer.instances[0];
      return {
        pipElement: !!document.pictureInPictureElement,
        artPip: !!inst.pip,
        state: inst.state,
        notice: inst.notice.show ? inst.template.$noticeInner.textContent : null,
        paused: inst.video.paused,
        errs: window.__errs || [],
      };
    })());`));
    console.log('after tapping 画中画: pictureInPictureElement=%s art.pip=%s state=%s notice=%s paused=%s',
      s.pipElement, s.artPip, s.state, s.notice, s.paused);
    check(s.pipElement, 'tapping 画中画 while playing enters PiP (notice=' + s.notice + ')');
    if (s.pipElement) {
      await evalIn('document.exitPictureInPicture().catch(() => {}); true');
      await sleep(1200);
    }

    // the paused-video case: a phone user who taps 画中画 before the video is
    // playing must not just get an error toast
    await evalIn('window.Artplayer.instances[0].video.pause(); true');
    await sleep(600);
    await tap('.art-control-pip');
    await sleep(2500);
    const pausedPip = JSON.parse(await evalIn(`JSON.stringify((() => {
      const inst = window.Artplayer.instances[0];
      return {
        pipElement: !!document.pictureInPictureElement,
        paused: inst.video.paused,
        notice: inst.notice.show ? inst.template.$noticeInner.textContent : null,
        errs: window.__errs || [],
      };
    })());`));
    console.log('tap 画中画 while paused: pictureInPictureElement=%s paused=%s notice=%s',
      pausedPip.pipElement, pausedPip.paused, pausedPip.notice);
    check(pausedPip.pipElement, 'tapping 画中画 while paused still enters PiP (notice=' + pausedPip.notice + ')');
    if (pausedPip.pipElement) {
      await evalIn('document.exitPictureInPicture().catch(() => {}); true');
      await sleep(1200);
    }
  }

  console.log('\nPASS %d / FAIL %d', pass.length, fail.length);
  for (const p of pass) console.log('  ok   ' + p);
  for (const f of fail) console.log('  FAIL ' + f);
  if (census.errs.length) console.log('page errors:', census.errs);
  page.close();
  process.exit(fail.length ? 1 : 0);
}

main().catch((e) => {
  console.error('FATAL', e);
  process.exit(1);
});
