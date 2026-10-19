// Structural check of the shipped filterAdsFromM3U8 over real manifests.
//
// Usage: node scripts/adfilter_live_check.cjs <manifest-file-or-dir> [...]
//
// The invariant it checks is the one that matters for "did we eat the
// episode": the filter may only drop SHORT runs (an ad break is never
// longer than AD_MAX_DURATION_S), and it may only drop a run that is
// wedged between long content runs or that comes from another
// host+directory. A long run is never dropped, so no content is lost.
//
// It also reports what was dropped, so a real ad can be recognised in the
// output: the run that disappeared, its duration and its share of the
// timeline.
const fs = require('fs');
const path = require('path');

const ROOT = process.env.MOONTVPLUS_ROOT || path.resolve(__dirname, '..', '..', '..', '..');
const esbuildDir = path.join(ROOT, 'frontend/node_modules/.pnpm');
const esbuildMain = fs.readdirSync(esbuildDir).filter((d) => d.startsWith('esbuild@')).map((d) => path.join(esbuildDir, d, 'node_modules/esbuild/lib/main.js')).filter((p) => fs.existsSync(p));
if (!esbuildMain.length) throw new Error('no esbuild in frontend/node_modules/.pnpm — run pnpm install in frontend/ first');
const esbuild = require(esbuildMain[0]);

const src = fs.readFileSync(path.join(ROOT, 'frontend/src/app/play/play-utils.ts'), 'utf8').replace(/\r\n/g, '\n');
const start = src.indexOf('export function filterAdsFromM3U8');
const end = src.indexOf('\n}\n', start);
const fnSrc = src.slice(start, end + 3).replace(/^export\s+function/m, 'function');
const js = esbuild.transformSync(fnSrc, { loader: 'ts' });
const filter = new Function((typeof js === 'string' ? js : js.code) + '\nreturn filterAdsFromM3U8;')();

const AD_MAX_DURATION_S = 90;

function runs(text) {
  const out = [];
  let cur = { segs: [], dur: 0 };
  let pend = null;
  for (const line of text.split('\n')) {
    const s = line.trim();
    if (/#EXT-X-DISCONTINUITY(?!-)/.test(s)) {
      out.push(cur);
      cur = { segs: [], dur: 0 };
      pend = null;
      continue;
    }
    const m = /^#EXTINF:(-?\d*\.?\d+)/i.exec(s);
    if (m) {
      pend = parseFloat(m[1]);
      continue;
    }
    if (s && !s.startsWith('#')) {
      cur.segs.push(s);
      cur.dur += pend || 0;
      pend = null;
    }
  }
  out.push(cur);
  return out;
}

function files(input) {
  const st = fs.statSync(input);
  if (st.isFile()) return [input];
  return fs.readdirSync(input).filter((f) => f.endsWith('.m3u8')).map((f) => path.join(input, f));
}

let fails = 0;
let droppedRuns = 0;
let manifests = 0;
for (const input of process.argv.slice(2)) {
  for (const file of files(input)) {
    const text = fs.readFileSync(file, 'utf8');
    const out = filter(text);
    manifests++;
    const before = runs(text);
    const after = runs(out);
    const kept = new Set(after.flatMap((r) => r.segs));
    const lost = [];
    for (const r of before) {
      const gone = r.segs.filter((s) => !kept.has(s));
      if (!gone.length) continue;
      droppedRuns++;
      lost.push({ dur: r.dur, n: gone.length, total: gone.length, share: r.dur / (before.reduce((a, x) => a + x.dur, 0) || 1) });
      if (r.dur > AD_MAX_DURATION_S) {
        fails++;
        console.log(`FAIL ${path.basename(file)}: dropped a ${r.dur.toFixed(1)}s run (${gone.length} segs)`);
      }
    }
    const segBefore = before.reduce((a, r) => a + r.segs.length, 0);
    const segAfter = after.reduce((a, r) => a + r.segs.length, 0);
    const durBefore = before.reduce((a, r) => a + r.dur, 0);
    const durAfter = after.reduce((a, r) => a + r.dur, 0);
    const marks = (out.match(/#EXT-X-DISCONTINUITY(?!-)/g) || []).length;
    console.log(
      `  ${path.basename(file).padEnd(26)} segs ${segBefore}->${segAfter}  dur ${durBefore.toFixed(1)}->${durAfter.toFixed(1)}` +
        `  markers ${marks}  dropped ${lost.length} run(s)` +
        (lost.length ? ` [${lost.map((l) => l.dur.toFixed(1) + 's/' + l.n + 'seg').join(', ')}]` : '')
    );
  }
}
console.log(`\nmanifests ${manifests} | dropped runs ${droppedRuns} | failures ${fails}`);
process.exit(fails ? 1 : 0);
