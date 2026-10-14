/**
 * Prove the card posters actually fill their box, and prove why they did not.
 *
 * Usage: node <this-file> <auth-cookie-value> [path ...]   (CDP_PORT, default 9334)
 *
 * Two halves, because the bug was a sizing mechanism, not a CSS typo:
 *
 * 1. CENSUS — every <img> whose parent has an aspect-ratio (the card boxes are
 *    `aspect-[2/3]`) must render at the parent's exact size. An image that
 *    renders shorter than its box is the "图片下面缺了一块" symptom.
 * 2. COUNTERFACTUAL — the same poster, in the same 180x270 box, inserted twice:
 *    once the way the shim used to do `fill` (`width="100%" height="100%"`
 *    ATTRIBUTES — a percentage is not a valid HTML width/height value, so the
 *    element falls back to Tailwind preflight `img{max-width:100%;height:auto}`
 *    and is sized by its width), and once the way Next's `fill` really works
 *    (`position:absolute; inset:0; width:100%; height:100%`). The attribute
 *    variant is the bug, the CSS variant is the fix, measured side by side.
 *
 * Needs a running server (it reads posters through /api/image-proxy) and a
 * Chrome the harness can attach to: scripts/chrome_start.sh 9334.
 */
const CDP = 'http://127.0.0.1:' + (process.env.CDP_PORT || '9334');
const COOKIE = process.argv[2] || '';
const PATHS = process.argv.slice(3).filter((p) => p.startsWith('/'));
if (PATHS.length === 0) PATHS.push('/');

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
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
    } catch (e) {
      return;
    }
    if (m.id && pending.has(m.id)) {
      pending.get(m.id)(m);
      pending.delete(m.id);
    }
  });
  return {
    call: async (method, params) => {
      await opened;
      const id = pending.size + 1;
      const p = new Promise((resolve) => pending.set(id, resolve));
      ws.send(JSON.stringify({ id, method, params }));
      const m = await p;
      if (m.error) throw new Error(method + ': ' + JSON.stringify(m.error));
      return m.result;
    },
    close: () => ws.close(),
  };
}

const CENSUS = `(async () => {
  await new Promise((r) => setTimeout(r, 3000));
  const boxes = [];
  let imgs = 0, filled = 0, short = 0, broken = 0;
  for (const img of document.querySelectorAll('img')) {
    const parent = img.parentElement;
    if (!parent) continue;
    const cs = getComputedStyle(parent);
    const declaredAspect = cs.aspectRatio !== 'auto' && cs.aspectRatio !== 'none';
    const isPosterBox = cs.overflow === 'hidden' && (declaredAspect || /aspect-/.test(parent.className || ''));
    if (!isPosterBox) continue;
    imgs++;
    const bw = parent.clientWidth, bh = parent.clientHeight;
    const iw = img.clientWidth, ih = img.clientHeight;
    const ok = Math.abs(iw - bw) <= 1 && Math.abs(ih - bh) <= 1;
    if (ok) filled++;
    else if (img.naturalWidth === 0) broken++;
    else short++;
    if (!ok && boxes.length < 8) {
      boxes.push({
        src: (img.src || '').slice(0, 70),
        natural: img.naturalWidth + 'x' + img.naturalHeight,
        ratio: img.naturalWidth ? (img.naturalHeight / img.naturalWidth).toFixed(3) : 'n/a',
        rendered: iw + 'x' + ih,
        box: bw + 'x' + bh,
        gap: bh - ih,
      });
    }
  }
  return JSON.stringify({ imgs, filled, short, broken, boxes });
})()`;

const COUNTERFACTUAL = `(async () => {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const poster = 'https://img3.doubanio.com/view/photo/s_ratio_poster/public/p2935880473.jpg';
  const src = '/api/image-proxy?url=' + encodeURIComponent(poster);
  const box = document.createElement('div');
  box.className = 'relative aspect-[2/3] overflow-hidden rounded-lg';
  box.style.width = '180px';
  box.style.position = 'fixed';
  box.style.left = '0';
  box.style.top = '0';
  document.body.appendChild(box);

  const attr = document.createElement('img');
  attr.setAttribute('width', '100%');
  attr.setAttribute('height', '100%');
  attr.className = 'object-cover';
  attr.src = src;
  box.appendChild(attr);

  const css = document.createElement('img');
  css.style.position = 'absolute';
  css.style.top = '0'; css.style.right = '0'; css.style.bottom = '0'; css.style.left = '0';
  css.style.width = '100%';
  css.style.height = '100%';
  css.className = 'object-cover';
  css.src = src;
  box.appendChild(css);

  await sleep(2500);
  const out = {
    natural: attr.naturalWidth + 'x' + attr.naturalHeight,
    ratio: attr.naturalWidth ? (attr.naturalHeight / attr.naturalWidth).toFixed(3) : 'n/a',
    box: box.clientWidth + 'x' + box.clientHeight,
    attributeFill: attr.clientWidth + 'x' + attr.clientHeight,
    cssFill: css.clientWidth + 'x' + css.clientHeight,
    preflight: getComputedStyle(attr).height,
  };
  box.remove();
  return JSON.stringify(out);
})()`;

async function main() {
  const list = await (await fetch(CDP + '/json/list')).json();
  const pageTarget = list.find((t) => t.type === 'page');
  if (!pageTarget) throw new Error('no page target — start Chrome with scripts/chrome_start.sh 9334');
  const page = makeClient(pageTarget.webSocketDebuggerUrl);
  await sleep(200);

  await page.call('Page.enable', {});
  await page.call('Runtime.enable', {});

  let failures = 0;
  for (const path of PATHS) {
    await page.call('Page.navigate', { url: 'http://127.0.0.1:3000' + path });
    await sleep(6000);
    if (COOKIE) {
      await page.call('Network.setCookie', {
        name: 'auth',
        value: COOKIE,
        path: '/',
        domain: '127.0.0.1',
      });
      await page.call('Page.navigate', { url: 'http://127.0.0.1:3000' + path });
      await sleep(6000);
    }

    const census = JSON.parse((await page.call('Runtime.evaluate', { expression: CENSUS, returnByValue: true, awaitPromise: true })).result.value);
    console.log('census ' + path + ': ' + census.imgs + ' poster imgs, ' + census.filled + ' fill their box, ' + census.short + ' do not, ' + census.broken + ' failed to load');
    for (const b of census.boxes) console.log('  NOT FILLED ' + JSON.stringify(b));
    if (census.short > 0) failures++;

    const cf = JSON.parse((await page.call('Runtime.evaluate', { expression: COUNTERFACTUAL, returnByValue: true, awaitPromise: true })).result.value);
    console.log('counterfactual: poster ' + cf.natural + ' (ratio ' + cf.ratio + ') in a ' + cf.box + ' box');
    console.log('  width/height ATTRIBUTES (old shim) -> ' + cf.attributeFill + '  [' + cf.preflight + ']');
    console.log('  CSS fill (Next semantics)         -> ' + cf.cssFill);
    if (cf.attributeFill === cf.box) console.log('  note: this poster is 2/3, so the old shim happened to look fine here');
    if (cf.cssFill !== cf.box) {
      console.log('  FAIL: the CSS fill does not cover the box');
      failures++;
    }
  }
  page.close();
  console.log(failures === 0 ? 'OK: every poster img fills its box' : 'FAIL: ' + failures + ' page(s) have unfilled poster boxes');
  process.exit(failures === 0 ? 0 : 1);
}

main().catch((e) => {
  console.log('FATAL', e);
  process.exit(1);
});
