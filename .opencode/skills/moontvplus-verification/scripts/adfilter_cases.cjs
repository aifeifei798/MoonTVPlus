// Fixture table for filterAdsFromM3U8 (seam rule: a marker survives only
// where the codec actually changes, i.e. never at a splice seam whose two
// sides share a host+directory signature).
const fs = require('fs');
const path = require('path');

// This file lives at <repo>/.opencode/skills/moontvplus-verification/scripts/.
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

function segs(s) {
  // hls.js's own URI capture is (\S[^\r\n]*), so a trailing \r from a CRLF
  // manifest is not part of the URI; mirror that here.
  return s.split('\n').filter((l) => l.trim() && !l.trim().startsWith('#')).map((l) => l.replace(/\r$/, ''));
}
function markers(s) {
  return s.split('\n').filter((l) => /#EXT-X-DISCONTINUITY(?!-)/.test(l)).length;
}

const cases = [];

cases.push({
  name: 'no markers → identical',
  in: '#EXTM3U\n#EXT-X-VERSION:3\n#EXTINF:4,\na.ts\n#EXTINF:4,\nb.ts\n#EXT-X-ENDLIST\n',
  expect: 'same',
});

cases.push({ name: 'empty → empty', in: '', expect: 'same' });

cases.push({
  name: 'no dominant (3 hosts, 1 seg each) → identical',
  in: '#EXTM3U\n#EXT-X-DISCONTINUITY\n#EXTINF:4,\nhttps://a/x/1.ts\n#EXT-X-DISCONTINUITY\n#EXTINF:4,\nhttps://b/y/2.ts\n#EXT-X-DISCONTINUITY\n#EXTINF:4,\nhttps://c/z/3.ts\n#EXT-X-ENDLIST\n',
  expect: 'same',
});

// 4. ad run in another directory → dropped; both sides share sig "|" → no marker
cases.push({
  name: 'mid-roll ad, same encoder both sides → 2 segs kept, 0 markers',
  in: '#EXTM3U\n#EXTINF:4,\na.ts\n#EXT-X-DISCONTINUITY\n#EXTINF:3,\n/ad/1.ts\n#EXT-X-DISCONTINUITY\n#EXTINF:4,\nb.ts\n#EXT-X-ENDLIST\n',
  expect: 'drop',
  wantSegs: ['a.ts', 'b.ts'],
  wantMarkers: 0,
});

cases.push({
  name: 'minority run owns 50% of duration → identical',
  in: '#EXTM3U\n#EXTINF:10,\na.ts\n#EXT-X-DISCONTINUITY\n#EXTINF:10,\n/ad/1.ts\n#EXT-X-ENDLIST\n',
  expect: 'same',
});

cases.push({
  name: 'dominant 6/9 segs, minority owns 36.8% of duration → identical',
  in: '#EXTM3U\n' +
    Array.of(1, 2, 3, 4, 5, 6).map((i) => '#EXTINF:10,\n/v/' + i + '.ts').join('\n') +
    '\n#EXT-X-DISCONTINUITY\n' +
    Array.of(1, 2, 3).map((i) => '#EXTINF:' + (35 / 3).toFixed(4) + ',\n/ad/' + i + '.ts').join('\n') +
    '\n#EXT-X-ENDLIST\n',
  expect: 'same',
});

cases.push({
  name: 'same dir as content → identical',
  in: '#EXTM3U\n#EXTINF:4,\n/v/1.ts\n#EXT-X-DISCONTINUITY\n#EXTINF:3,\n/v/2.ts\n#EXT-X-DISCONTINUITY\n#EXTINF:4,\n/v/3.ts\n#EXT-X-ENDLIST\n',
  expect: 'same',
});

cases.push({
  name: 'absolute ad URL on another host → dropped, 0 markers',
  in: '#EXTM3U\n#EXTINF:4,\n/v/1.ts\n#EXT-X-DISCONTINUITY\n#EXTINF:3,\nhttps://ads.example.com/spots/a.ts\n#EXT-X-DISCONTINUITY\n#EXTINF:4,\n/v/2.ts\n#EXT-X-ENDLIST\n',
  expect: 'drop',
  wantSegs: ['/v/1.ts', '/v/2.ts'],
  wantMarkers: 0,
});

// post-roll: the marker between the two untouched content groups stays
cases.push({
  name: 'post-roll ad at the end → dropped, 1 packager marker kept',
  in: '#EXTM3U\n#EXTINF:4,\n/v/1.ts\n#EXT-X-DISCONTINUITY\n#EXTINF:4,\n/v/2.ts\n#EXT-X-DISCONTINUITY\n#EXTINF:3,\n/ad/z.ts\n#EXT-X-ENDLIST\n',
  expect: 'drop',
  wantSegs: ['/v/1.ts', '/v/2.ts'],
  wantMarkers: 1,
});

cases.push({
  name: 'two ad runs, one encoder throughout → all content kept, 0 markers',
  in: '#EXTM3U\n#EXTINF:4,\n/v/1.ts\n#EXT-X-DISCONTINUITY\n#EXTINF:3,\n/ad/a.ts\n#EXT-X-DISCONTINUITY\n#EXTINF:4,\n/v/2.ts\n#EXT-X-DISCONTINUITY\n#EXTINF:3,\n/ad/b.ts\n#EXT-X-DISCONTINUITY\n#EXTINF:4,\n/v/3.ts\n#EXT-X-ENDLIST\n',
  expect: 'drop',
  wantSegs: ['/v/1.ts', '/v/2.ts', '/v/3.ts'],
  wantMarkers: 0,
});

cases.push({
  name: 'CRLF manifest → CRLF out, segments clean, 0 markers',
  in: '#EXTM3U\r\n#EXTINF:4,\r\na.ts\r\n#EXT-X-DISCONTINUITY\r\n#EXTINF:3,\r\n/ad/1.ts\r\n#EXT-X-DISCONTINUITY\r\n#EXTINF:4,\r\nb.ts\r\n#EXT-X-ENDLIST\r\n',
  expect: 'drop',
  wantSegs: ['a.ts', 'b.ts'],
  wantMarkers: 0,
  wantCRLF: true,
});

cases.push({
  name: 'empty group keeps its directives, no marker leak',
  in: '#EXTM3U\n#EXT-X-DISCONTINUITY\n#EXT-X-INDEPENDENT-SEGMENTS\n#EXT-X-DISCONTINUITY\n#EXTINF:4,\n/v/1.ts\n#EXT-X-DISCONTINUITY\n#EXTINF:3,\n/ad/1.ts\n#EXT-X-DISCONTINUITY\n#EXTINF:4,\n/v/2.ts\n#EXT-X-ENDLIST\n',
  expect: 'drop',
  wantSegs: ['/v/1.ts', '/v/2.ts'],
  wantMarkers: 0,
  wantDirective: '#EXT-X-INDEPENDENT-SEGMENTS',
});

cases.push({
  name: 'DISCONTINUITY-SEQUENCE survives as a directive, ad still dropped',
  in: '#EXTM3U\n#EXT-X-DISCONTINUITY-SEQUENCE:0\n#EXTINF:4,\n/v/1.ts\n#EXT-X-DISCONTINUITY\n#EXTINF:3,\n/ad/1.ts\n#EXT-X-DISCONTINUITY\n#EXTINF:4,\n/v/2.ts\n#EXT-X-ENDLIST\n',
  expect: 'drop',
  wantSegs: ['/v/1.ts', '/v/2.ts'],
  wantMarkers: 0,
  wantDirective: '#EXT-X-DISCONTINUITY-SEQUENCE:0',
});

// NEW: splice seam where the encoder really does change → marker kept
cases.push({
  name: 'splice seam with a real encoder change → marker kept',
  in: '#EXTM3U\n' +
    Array.of(1, 2, 3, 4, 5, 6, 7).map((i) => '#EXTINF:10,\n/v/' + i + '.ts').join('\n') +
    '\n#EXT-X-DISCONTINUITY\n#EXTINF:3,\n/ad/1.ts\n#EXT-X-DISCONTINUITY\n' +
    Array.of(1, 2, 3).map(() => '#EXTINF:20,\nhttps://mirrorB/x/n.ts').join('\n') +
    '\n#EXT-X-ENDLIST\n',
  expect: 'drop',
  wantSegs: Array.of(1, 2, 3, 4, 5, 6, 7).map((i) => '/v/' + i + '.ts')
    .concat(Array.of(1, 2, 3).map(() => 'https://mirrorB/x/n.ts')),
  wantMarkers: 1,
});

// NEW: a packager marker between two untouched groups is never dropped,
// even when both sides share a signature.
cases.push({
  name: 'packager marker between untouched groups stays → 1 marker',
  in: '#EXTM3U\n' +
    Array.of(1, 2, 3, 4).map((i) => '#EXTINF:10,\n/v/' + i + '.ts').join('\n') +
    '\n#EXT-X-DISCONTINUITY\n' +
    Array.of(5, 6).map((i) => '#EXTINF:10,\n/v/' + i + '.ts').join('\n') +
    '\n#EXT-X-DISCONTINUITY\n#EXTINF:3,\n/ad/1.ts\n#EXT-X-ENDLIST\n',
  expect: 'drop',
  wantSegs: Array.of(1, 2, 3, 4, 5, 6).map((i) => '/v/' + i + '.ts'),
  wantMarkers: 1,
});

// NEW: an ad group that declared #EXT-X-KEY must not steal the inheritance
cases.push({
  name: 'dropped ad group declared #EXT-X-KEY → key re-emitted at the seam',
  in: '#EXTM3U\n#EXTINF:4,\n/v/1.ts\n#EXT-X-DISCONTINUITY\n#EXT-X-KEY:METHOD=AES-128,KEY=0x1234\n#EXTINF:3,\n/ad/1.ts\n#EXT-X-DISCONTINUITY\n#EXTINF:4,\n/v/2.ts\n#EXT-X-ENDLIST\n',
  expect: 'drop',
  wantSegs: ['/v/1.ts', '/v/2.ts'],
  wantMarkers: 0,
  wantDirective: '#EXT-X-KEY:METHOD=AES-128,KEY=0x1234',
});

const realPath = path.join(ROOT, '.opencode/skills/moontvplus-verification/fixtures/live-source-a.m3u8');
const real = fs.readFileSync(realPath, 'utf8');
cases.push({
  name: 'real live manifest A -> 354 content segs, 0 markers, no content lost',
  in: real,
  expect: 'drop',
  wantSegs: segs(real).filter((s) => !/adjump/.test(s)),
  wantMarkers: 0,
});

let fails = 0;
for (const c of cases) {
  const out = filter(c.in);
  const got = segs(out);
  const gotM = markers(out);
  let ok = true;
  let why = [];
  if (c.expect === 'same' && out !== c.in) { ok = false; why.push('changed'); }
  if (c.expect === 'drop' && out === c.in) { ok = false; why.push('not changed'); }
  if (c.wantSegs && got.join('|') !== c.wantSegs.join('|')) {
    ok = false;
    why.push('segs ' + JSON.stringify(got) + ' want ' + JSON.stringify(c.wantSegs));
  }
  if (c.wantMarkers != null && gotM !== c.wantMarkers) { ok = false; why.push('markers ' + gotM + ' want ' + c.wantMarkers); }
  if (c.wantCRLF != null && (out.includes('\r\n')) !== c.wantCRLF) { ok = false; why.push('CRLF mismatch'); }
  if (c.wantDirective != null && !out.includes(c.wantDirective)) { ok = false; why.push('lost directive ' + c.wantDirective); }
  if (!ok) fails++;
  console.log((ok ? 'PASS ' : 'FAIL ') + c.name + (ok ? '' : '  ← ' + why.join('; ')));
}
console.log('cases', cases.length, '| fails', fails);
