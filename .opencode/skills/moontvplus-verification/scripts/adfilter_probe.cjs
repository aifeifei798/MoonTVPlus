// Structural check of the shipped filterAdsFromM3U8 on ANY saved manifest.
// Usage: node scripts/adfilter_probe.cjs <manifest-path>
// The manifest may be one of fixtures/live-source-*.m3u8 or a fresh one from
// scripts/fetch_manifest.py.
const fs = require('fs');
const path = require('path');

// This file lives at <repo>/.opencode/skills/moontvplus-verification/scripts/,
// so the repo root is four levels up. MOONTVPLUS_ROOT overrides it.
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

const txt = fs.readFileSync(process.argv[2], 'utf8');
const out = filter(txt);

const seg = (s) => s.split('\n').filter((l) => l.trim() && !l.startsWith('#'));
const census = (s) => s.split('\n').filter((l) => l.startsWith('#') && !l.startsWith('#EXTINF')).reduce((a, l) => { a[l] = (a[l] || 0) + 1; return a; }, {});
const dur = (s) => s.split('\n').filter((l) => l.startsWith('#EXTINF')).reduce((a, l) => a + parseFloat(l.slice(8).split(',')[0]), 0);

const rawSegs = seg(txt);
const newSegs = seg(out);
const kept = new Set(newSegs);
const dropped = rawSegs.filter((s) => !kept.has(s));

console.log('raw   segs', rawSegs.length, '| markers', (census(txt)['#EXT-X-DISCONTINUITY'] || 0), '| dur', dur(txt).toFixed(2));
console.log('new   segs', newSegs.length, '| markers', (census(out)['#EXT-X-DISCONTINUITY'] || 0), '| dur', dur(out).toFixed(2));
console.log('dropped', dropped.length, 'segs', '=', dur(txt) - dur(out).toFixed(2), 's');
dropped.slice(0, 12).forEach((d) => console.log('  DROP', d));

// where the splice seams are in the NEW playlist
const lines = out.split('\n');
const idx = [];
lines.forEach((l, i) => { if (seg1(l)) idx.push(i); });
function seg1(l) { return l.trim() !== '' && !l.startsWith('#'); }
console.log('--- seams (adjacent raw-adjacency broken)');
let seams = 0;
for (let i = 1; i < idx.length; i++) {
  const a = lines[idx[i - 1]], b = lines[idx[i]];
  const ra = rawSegs.indexOf(a);
  if (rawSegs[ra + 1] !== b) {
    seams++;
    console.log('  SEAM', a, '->', b);
  }
}
console.log('seams', seams);
