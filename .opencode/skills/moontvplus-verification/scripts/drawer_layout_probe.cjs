// Live check of the PC 选集/换源 drawer on a real /play page.
//   node scripts/drawer_layout_probe.cjs <auth-cookie-value> <play-path> [--shots=<dir>]
//
// What it asserts (all measured in a real browser, not with TestClient):
//   1. desktop (>=768px): the player spans the whole grid row (md:col-span-4) and the
//      EpisodeSelector is portalled INSIDE `.art-video-player` as a right-docked drawer;
//      the drawer stops above the control bar, the video/poster/danmuku/subtitle layers
//      are narrowed by --mntv-drawer-width, and the episode grid renders buttons.
//   2. the 换源 tab switches inside the drawer and the source rows carry
//      data-source-key / data-active (the ordering fix's assertion surface).
//   3. the control-bar toggle (`.mntv-drawer-toggle`) closes and reopens the drawer,
//      and the video returns to the full player width when closed.
//   4. the loading mask (VideoLoadingMask, z-[120]) is narrowed to the video area while
//      the drawer is open, so 选集/换源 are clickable during "视频加载中...".
//   5. web fullscreen: the drawer is still inside the player and still reachable.
//   6. mobile (<768px): no drawer, no toggle, panel below the player (unchanged).
//
// Needs a multi-episode TV play URL (stype=tv). chrome_start.sh must already be running.
const fs = require('fs');
const path = require('path');

const CDP_PORT = process.env.CDP_PORT || '9334';
const CDP = 'http://127.0.0.1:' + CDP_PORT;
const COOKIE = process.argv[2];
const PATH = process.argv[3];
const SHOTS = (process.argv.find((a) => a.startsWith('--shots=')) || '--shots=/tmp/opencode/drawer-shots').slice(8);
const VIEWPORTS = [[1440, 900], [1280, 800], [1024, 720], [900, 600], [768, 600], [767, 600], [375, 667]];

function sleep(ms) { return new Promise((p) => setTimeout(p, ms)); }

function makeClient(url) {
  const ws = new WebSocket(url);
  const pending = new Map();
  const opened = new Promise((r) => ws.addEventListener('open', r));
  ws.addEventListener('message', (ev) => {
    let m; try { m = JSON.parse(ev.data); } catch (_) { return; }
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
  });
  let id = 0;
  return {
    call: (method, params) => {
      const myId = ++id;
      return opened.then(() => new Promise((resolve) => {
        pending.set(myId, resolve);
        ws.send(JSON.stringify({ id: myId, method, params: params || {} }));
      })).then((m) => { if (m.error) throw new Error(method + ': ' + JSON.stringify(m.error)); return m.result; });
    },
    close: () => ws.close(),
  };
}

// One pass per viewport. Everything is measured from the DOM; the tab click is a
// dispatched MouseEvent (`.click()` on a plain div works too, but the tab is a div and
// the wrapper div also contains the tab text, so the match is on exact textContent).
const VIEW_EXPR = `(async () => {
  const box = (el) => { if (!el) return null; const r = el.getBoundingClientRect(); return { w: Math.round(r.width), h: Math.round(r.height), left: Math.round(r.left), top: Math.round(r.top), right: Math.round(r.right), bottom: Math.round(r.bottom) }; };
  const player = document.querySelector('.art-video-player');
  if (!player) return JSON.stringify({ error: 'no player' });
  const drawer = player.querySelector('.mntv-episode-drawer');
  const video = player.querySelector('video');
  const toggle = player.querySelector('.mntv-drawer-toggle');
  const controls = player.querySelector('.art-controls');
  const grid = (drawer || document).querySelectorAll('div[class*="grid-cols-3"] button');
  const out = {
    viewport: [window.innerWidth, window.innerHeight],
    isDesktop: window.matchMedia('(min-width: 768px)').matches,
    player: box(player),
    video: box(video),
    drawer: box(drawer),
    drawerInPlayer: !!drawer && !!drawer.closest('.art-video-player'),
    drawerOpenClass: player.classList.contains('mntv-drawer-open'),
    toggleVisible: toggle ? getComputedStyle(toggle).display !== 'none' : false,
    controls: box(controls),
    episodeButtons: grid.length,
    firstButton: box(grid[0]),
    drawerClearance: drawer && controls ? Math.round(controls.getBoundingClientRect().top - drawer.getBoundingClientRect().bottom) : null,
    videoNarrowed: drawer ? Math.abs(video.getBoundingClientRect().right - drawer.getBoundingClientRect().left) <= 2 : null,
    tabClip: (() => {
      const row = (drawer || document).querySelector('div.flex.mb-1');
      if (!row) return null;
      const box2 = (el) => { const r = el.getBoundingClientRect(); return { left: Math.round(r.left), right: Math.round(r.right) }; };
      const rb = box2(row), db = box2(drawer || row.parentElement);
      return { rowLeft: rb.left, rowRight: rb.right, drawerLeft: db.left, drawerRight: db.right, overflowLeft: db.left - rb.left, overflowRight: rb.right - db.right };
    })(),
    mobilePanel: drawer ? null : (() => {
      const el = document.querySelector("div[class*='bg-black/10']");
      return el ? Object.assign(box(el), { inPlayer: !!el.closest('.art-video-player') }) : null;
    })(),
  };
  if (toggle) {
    toggle.click();
    await new Promise((r) => setTimeout(r, 600));
    out.afterClose = {
      drawer: box(document.querySelector('.mntv-episode-drawer')),
      video: box(video),
      openClass: player.classList.contains('mntv-drawer-open'),
    };
    toggle.click();
    await new Promise((r) => setTimeout(r, 600));
    out.afterReopen = {
      drawer: box(document.querySelector('.mntv-episode-drawer')),
      video: box(video),
      openClass: player.classList.contains('mntv-drawer-open'),
    };
  }
  // 换源 tab: re-query the drawer, the portal div is recreated on every toggle.
  // On mobile there is no drawer, so fall back to the document (the panel below the
  // player) and the same grid assertions apply there too.
  const live = document.querySelector('.mntv-episode-drawer') || document;
  const srcTab = live ? Array.from(live.querySelectorAll('div.flex-1')).find((el) => el.textContent.trim() === '\u6362\u6e90') : null;
  if (srcTab) {
    srcTab.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    await new Promise((r) => setTimeout(r, 1500));
    const rows = document.querySelectorAll('[data-source-key]');
    out.sourceRows = rows.length;
    out.sourceOrder = Array.from(rows).slice(0, 6).map((r) => r.getAttribute('data-source-key') + ' active=' + r.getAttribute('data-active'));
    out.firstSourceRow = box(rows[0]);
    // compact grid: how many tiles share the first row, and how wide/tall one is.
    const tops = Array.from(rows).slice(0, 8).map((r) => Math.round(r.getBoundingClientRect().top));
    out.sourcePerRow = tops.filter((t) => t === tops[0]).length;
    // VIEW_EXPR is a template literal, so the regex must be double-escaped here:
    // a single \s inside backticks evaluates to the letter s and silently eats the
    // 's' of every "MB/s" in the printed tile text.
    out.sourceTileText = Array.from(rows).slice(0, 4).map((r) => r.textContent.trim().replace(/\\s+/g, ' '));
    out.sourceSpeedLines = Array.from(rows).map((r) => (r.children[1] ? r.children[1].textContent.trim() : ''));
    srcTab.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    await new Promise((r) => setTimeout(r, 400));
  }
  out.errs = (window.__errs || []).slice(0, 5);
  return JSON.stringify(out, null, 1);
})()`;

// Desktop-only behaviour: loading mask narrowing, episode click, source switch,
// z-order vs Artplayer's settings panel, web fullscreen reachability.
const BEHAVIOR_EXPR = `(async () => {
  const box = (el) => { if (!el) return null; const r = el.getBoundingClientRect(); return { w: Math.round(r.width), h: Math.round(r.height), left: Math.round(r.left), top: Math.round(r.top), right: Math.round(r.right), bottom: Math.round(r.bottom) }; };
  const player = document.querySelector('.art-video-player');
  const out = {};
  const mask = document.querySelector('.mntv-loading-mask');
  out.mask = mask ? Object.assign(box(mask), { rightCss: getComputedStyle(mask).right }) : null;
  const buttons = document.querySelectorAll('.mntv-episode-drawer div[class*="grid-cols-3"] button');
  if (buttons.length > 1) {
    buttons[1].click();
    await new Promise((r) => setTimeout(r, 2500));
    out.header = document.querySelector('.moontv-play-header') ? document.querySelector('.moontv-play-header').textContent.trim() : '';
  }
  const tab = Array.from(document.querySelectorAll('.mntv-episode-drawer div.flex-1')).find((t) => t.textContent.trim() === '\u6362\u6e90');
  if (tab) {
    tab.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    await new Promise((r) => setTimeout(r, 2000));
    const rows = Array.from(document.querySelectorAll('[data-source-key]'));
    out.beforeSwitch = rows.map((r) => r.getAttribute('data-source-key') + '=' + r.getAttribute('data-active'));
    if (rows[1]) {
      rows[1].click();
      await new Promise((r) => setTimeout(r, 3000));
      out.afterSwitch = Array.from(document.querySelectorAll('[data-source-key]')).map((r) => r.getAttribute('data-source-key') + '=' + r.getAttribute('data-active'));
    }
    tab.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    await new Promise((r) => setTimeout(r, 500));
    // back to the 选集 tab so the fullscreen check below sees the episode grid
    const epTab = Array.from(document.querySelectorAll('.mntv-episode-drawer div.flex-1')).find((t) => t.textContent.trim() === '\u9009\u96c6');
    if (epTab) { epTab.dispatchEvent(new MouseEvent('click', { bubbles: true })); await new Promise((r) => setTimeout(r, 500)); }
  }
  const settings = document.querySelector('.art-settings');
  out.zOrder = { settings: settings ? getComputedStyle(settings).zIndex : null, drawer: getComputedStyle(document.querySelector('.mntv-episode-drawer')).zIndex };
  const fsBtn = document.querySelector('.art-control-fullscreenWeb');
  if (fsBtn) {
    fsBtn.click();
    await new Promise((r) => setTimeout(r, 1500));
    const drawer = document.querySelector('.mntv-episode-drawer');
    const hit = document.elementFromPoint(Math.round(window.innerWidth - 120), Math.round(window.innerHeight / 2));
    out.webFullscreen = {
      fullscreenWeb: player.classList.contains('art-fullscreen-web'),
      player: box(player),
      video: box(player.querySelector('.art-video')),
      drawer: box(drawer),
      drawerInPlayer: !!drawer && !!drawer.closest('.art-video-player'),
      hitAtDrawerArea: hit ? (hit.closest('.mntv-episode-drawer') ? 'drawer' : hit.className) : null,
      episodeButtons: drawer ? drawer.querySelectorAll('div[class*="grid-cols-3"] button').length : 0,
    };
    fsBtn.click();
    await new Promise((r) => setTimeout(r, 1000));
  }
  return JSON.stringify(out, null, 1);
})()`;

let failures = 0;
function check(name, ok, detail) {
  console.log((ok ? 'PASS' : 'FAIL') + ' ' + name + (detail ? ' — ' + detail : ''));
  if (!ok) failures++;
}

async function main() {
  if (!COOKIE || !PATH) { console.log('usage: drawer_layout_probe.cjs <auth-cookie> <play-path> [--shots=dir]'); process.exit(2); }
  fs.mkdirSync(SHOTS, { recursive: true });
  const list = await (await fetch(CDP + '/json/list')).json();
  const page = makeClient(list.find((t) => t.type === 'page').webSocketDebuggerUrl);
  await sleep(200);
  await page.call('Page.enable', {});
  await page.call('Runtime.enable', {});
  // /assets/*.css is served with `cache-control: public, max-age=300`, so a Chrome
  // that probed the previous build answers from its own cache and the probe measures
  // the OLD layout. Clearing the cache makes a rebuilt dist visible immediately.
  await page.call('Network.enable', {});
  await page.call('Network.clearBrowserCache', {});
  await page.call('Page.addScriptToEvaluateOnNewDocument', {
    source: 'window.__errs = []; window.addEventListener("error", (e) => window.__errs.push(String(e.message)));',
  });
  await page.call('Network.setCookie', { name: 'auth', value: COOKIE, path: '/', domain: '127.0.0.1' });

  for (const [w, h] of VIEWPORTS) {
    await page.call('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: w < 500 });
    await page.call('Page.navigate', { url: 'http://127.0.0.1:3000' + PATH });
    // The cache was cleared, so every navigation re-downloads the ~1.5 MB bundle, and
    // the play page only mounts Artplayer after the search aggregation returns — these
    // CDNs are flaky, so retry before declaring the player missing.
    let res = null;
    let d = null;
    for (let attempt = 0; attempt < 3; attempt++) {
      await sleep(attempt === 0 ? 15000 : 8000);
      res = await page.call('Runtime.evaluate', { expression: VIEW_EXPR, returnByValue: true, awaitPromise: true });
      d = JSON.parse(res.result.value);
      if (!d.error) break;
    }
    console.log('=== ' + w + 'x' + h);
    console.log(JSON.stringify(d, null, 1));
    if (d.error) { check(w + 'x' + h + ' player mounted', false, d.error); continue; }
    if (d.isDesktop) {
      check(w + 'x' + h + ' drawer inside the player', d.drawerInPlayer, d.drawer && d.drawer.w + 'x' + d.drawer.h);
      check(w + 'x' + h + ' drawer open by default', d.drawerOpenClass && !!d.drawer);
      check(w + 'x' + h + ' drawer clears the control bar', d.drawerClearance !== null && d.drawerClearance > 0, 'gap ' + d.drawerClearance);
      check(w + 'x' + h + ' video narrowed to the left of the drawer', d.videoNarrowed === true, 'video right ' + (d.video && d.video.right) + ' drawer left ' + (d.drawer && d.drawer.left));
      check(w + 'x' + h + ' episode buttons render', d.episodeButtons > 0, d.firstButton && d.firstButton.w + 'x' + d.firstButton.h);
      check(w + 'x' + h + ' toggle visible', d.toggleVisible);
      check(w + 'x' + h + ' toggle closes the drawer', d.afterClose && d.afterClose.drawer === null && d.afterClose.openClass === false && d.afterClose.video.w === d.player.w);
      check(w + 'x' + h + ' toggle reopens the drawer', d.afterReopen && !!d.afterReopen.drawer && d.afterReopen.openClass);
      check(w + 'x' + h + ' 换源 tab renders source rows', d.sourceRows > 0, (d.sourceOrder || []).join(', '));
      check(w + 'x' + h + ' 换源 tiles sit several per row', d.sourcePerRow >= 2, 'per row ' + d.sourcePerRow + ' — ' + (d.sourceTileText || []).join(' | '));
      check(w + 'x' + h + ' 换源 tile is compact', d.firstSourceRow && d.firstSourceRow.h <= 60 && d.firstSourceRow.w < ((d.drawer || d.mobilePanel || {}).w || 0) / 2, d.firstSourceRow && d.firstSourceRow.w + 'x' + d.firstSourceRow.h + ' in a ' + (d.drawer || d.mobilePanel || {}).w + 'px panel');
      check(w + 'x' + h + ' every 换源 tile carries a speed line', (d.sourceSpeedLines || []).length === d.sourceRows && (d.sourceSpeedLines || []).every((s) => s.length > 0), (d.sourceSpeedLines || []).join(', '));
      check(w + 'x' + h + ' tab row fits the panel', d.tabClip && d.tabClip.overflowRight <= 0 && d.tabClip.overflowLeft <= 0, d.tabClip && 'overflow ' + d.tabClip.overflowLeft + '/' + d.tabClip.overflowRight);
      check(w + 'x' + h + ' no page errors', (d.errs || []).length === 0, (d.errs || []).join('; '));
      const shot = await page.call('Page.captureScreenshot', { format: 'png' });
      fs.writeFileSync(SHOTS + '/desktop-' + w + 'x' + h + '.png', Buffer.from(shot.data, 'base64'));
    } else {
      check(w + 'x' + h + ' no drawer on mobile', !d.drawer && !d.drawerInPlayer);
      check(w + 'x' + h + ' toggle hidden on mobile', d.toggleVisible === false);
      check(w + 'x' + h + ' panel below the player', !!d.mobilePanel && !d.mobilePanel.inPlayer, d.mobilePanel && d.mobilePanel.w + 'x' + d.mobilePanel.h);
      check(w + 'x' + h + ' episode buttons render', d.episodeButtons > 0, d.firstButton && d.firstButton.w + 'x' + d.firstButton.h);
      check(w + 'x' + h + ' 换源 tiles sit several per row', d.sourcePerRow >= 2, 'per row ' + d.sourcePerRow + ' — ' + (d.sourceTileText || []).join(' | '));
      check(w + 'x' + h + ' 换源 tile is compact', d.firstSourceRow && d.firstSourceRow.h <= 60 && d.firstSourceRow.w < ((d.mobilePanel || {}).w || 0) / 2, d.firstSourceRow && d.firstSourceRow.w + 'x' + d.firstSourceRow.h + ' in a ' + (d.mobilePanel || {}).w + 'px panel');
      check(w + 'x' + h + ' every 换源 tile carries a speed line', (d.sourceSpeedLines || []).length === d.sourceRows && (d.sourceSpeedLines || []).every((s) => s.length > 0), (d.sourceSpeedLines || []).join(', '));
      check(w + 'x' + h + ' tab row fits the panel', d.tabClip && d.tabClip.overflowRight <= 0 && d.tabClip.overflowLeft <= 0, d.tabClip && 'overflow ' + d.tabClip.overflowLeft + '/' + d.tabClip.overflowRight);
      const shot = await page.call('Page.captureScreenshot', { format: 'png' });
      fs.writeFileSync(SHOTS + '/mobile-' + w + 'x' + h + '.png', Buffer.from(shot.data, 'base64'));
    }
  }

  await page.call('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
  await page.call('Page.navigate', { url: 'http://127.0.0.1:3000' + PATH });
  await sleep(1200);
  const early = await page.call('Runtime.evaluate', {
    expression: `(() => {
      const m = document.querySelector('.mntv-loading-mask');
      if (m) {
        const r = m.getBoundingClientRect();
        const p = document.querySelector('.art-video-player').getBoundingClientRect();
        return JSON.stringify({ present: true, maskRight: Math.round(r.right), playerRight: Math.round(p.right), drawerWidth: Math.round(p.right - r.right) });
      }
      // mask already gone: the shipped rule is still what decides the geometry, so
      // read it out of the stylesheet instead of skipping the assertion.
      for (const sheet of document.styleSheets) {
        let rules; try { rules = sheet.cssRules; } catch (_) { continue; }
        for (const rule of rules) {
          if (rule.selectorText && rule.selectorText.includes('.mntv-loading-mask') && rule.style.right) {
            return JSON.stringify({ present: false, rule: rule.selectorText, right: rule.style.right });
          }
        }
      }
      return JSON.stringify({ present: false, rule: null });
    })()`,
    returnByValue: true,
  });
  const m = JSON.parse(early.result.value);
  if (m.present) check('loading mask covers only the video area', m.maskRight === m.playerRight - m.drawerWidth, 'mask right ' + m.maskRight + ', player right ' + m.playerRight);
  else check('loading mask narrowing rule shipped', !!m.rule && m.rule.includes('mntv-drawer-open') && m.rule.includes('mntv-loading-mask'), m.rule || 'no rule found');
  await sleep(6000);
  const b = JSON.parse((await page.call('Runtime.evaluate', { expression: BEHAVIOR_EXPR, returnByValue: true, awaitPromise: true })).result.value);
  console.log('=== behaviour 1440x900');
  console.log(JSON.stringify(b, null, 1));
  check('episode click changes the header', /第 2 集/.test(b.header || ''), b.header);
  const activeOf = (list) => (list || []).filter((r) => r.endsWith('=1')).map((r) => r.split('=')[0]);
  check('source switch moves the active marker', activeOf(b.beforeSwitch).length === 1 && activeOf(b.afterSwitch).length === 1 && activeOf(b.beforeSwitch)[0] !== activeOf(b.afterSwitch)[0], (b.beforeSwitch || []).join(', ') + ' -> ' + (b.afterSwitch || []).join(', '));
  check('drawer sits below Artplayer settings', Number(b.zOrder.drawer) < Number(b.zOrder.settings), 'drawer ' + b.zOrder.drawer + ' settings ' + b.zOrder.settings);
  check('drawer reachable in web fullscreen', b.webFullscreen && b.webFullscreen.drawerInPlayer && b.webFullscreen.episodeButtons > 0 && b.webFullscreen.hitAtDrawerArea === 'drawer', b.webFullscreen && b.webFullscreen.hitAtDrawerArea);
  const shot = await page.call('Page.captureScreenshot', { format: 'png' });
  fs.writeFileSync(SHOTS + '/behavior-1440x900.png', Buffer.from(shot.data, 'base64'));
  console.log('screenshots in ' + SHOTS);
  page.close();
  console.log(failures === 0 ? 'ALL PASS' : failures + ' FAILURES');
  process.exit(failures === 0 ? 0 : 1);
}
main().catch((e) => { console.log('FATAL', e); process.exit(1); });
