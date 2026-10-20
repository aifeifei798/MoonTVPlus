// Fixture table for filterAdsFromM3U8 (seam rule: a marker survives only
// where the codec actually changes, i.e. never at a splice seam whose two
// sides share a host+directory signature).
//
// Two ad rules are covered here:
//   1. the signature rule — the ad run comes from another host+directory
//   2. the shape rule — a SHORT run wedged between LONG content runs, which
//      is the only thing left when the ad is spliced in from the SAME
//      directory (the ffzy / quantum / modu sources do exactly that).
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

// ---------------------------------------------------------------------------
// The shape rule: a short run wedged between long content runs is an ad even
// when it comes from the same directory (measured on the ffzy source: the ad
// is 1920x1080 while the content is 1280x720, both under the same .../hls/).
// ---------------------------------------------------------------------------
function runOf(segList, count, extinf) {
  // `count` segments of `extinf` seconds each, as one run
  return Array.from({ length: count }, (_, i) => `#EXTINF:${extinf},\n${segList[i]}`).join('\n');
}

// 358.7s of content, a 19.6s ad from the SAME directory, then 949.9s more.
cases.push({
  name: 'same-directory ad wedged between long runs -> dropped, 0 markers',
  in: '#EXTM3U\n' +
    runOf(Array.from({ length: 20 }, (_, i) => '/hls/c' + i + '.ts'), 20, 17.935) +
    '\n#EXT-X-DISCONTINUITY\n' +
    runOf(Array.from({ length: 5 }, (_, i) => '/hls/a' + i + '.ts'), 5, 3.92) +
    '\n#EXT-X-DISCONTINUITY\n' +
    runOf(Array.from({ length: 20 }, (_, i) => '/hls/d' + i + '.ts'), 20, 47.495) +
    '\n#EXT-X-ENDLIST\n',
  expect: 'drop',
  wantSegs: Array.from({ length: 20 }, (_, i) => '/hls/c' + i + '.ts')
    .concat(Array.from({ length: 20 }, (_, i) => '/hls/d' + i + '.ts')),
  wantMarkers: 0,
});

// a source that chops the episode into many short runs (the source C/source F shape):
// every run is short, so no run is wedged between two long ones -> untouched.
cases.push({
  name: 'episode chopped into many short runs -> identical',
  in: '#EXTM3U\n' +
    Array.from({ length: 20 }, (_, g) =>
      Array.from({ length: 10 }, (_, i) => `#EXTINF:39.4,\n/hls/x${g}_${i}.ts`).join('\n') +
      '\n#EXT-X-DISCONTINUITY\n'
    ).join('\n') +
    '#EXT-X-ENDLIST\n',
  expect: 'same',
});

// a short run between two short runs is content, not an ad
cases.push({
  name: 'short run between two short runs -> identical',
  in: '#EXTM3U\n' +
    runOf(['/hls/a.ts'], 1, 40) +
    '\n#EXT-X-DISCONTINUITY\n' +
    runOf(['/hls/b.ts'], 1, 25) +
    '\n#EXT-X-DISCONTINUITY\n' +
    runOf(['/hls/c.ts'], 1, 40) +
    '\n#EXT-X-ENDLIST\n',
  expect: 'same',
});

// a pre-roll: the ad is the first run, the content behind it is long
cases.push({
  name: 'same-directory pre-roll ad -> dropped, 0 markers',
  in: '#EXTM3U\n' +
    runOf(['/hls/ad.ts'], 1, 19.6) +
    '\n#EXT-X-DISCONTINUITY\n' +
    runOf(['/hls/c0.ts', '/hls/c1.ts'], 2, 200) +
    '\n#EXT-X-ENDLIST\n',
  expect: 'drop',
  wantSegs: ['/hls/c0.ts', '/hls/c1.ts'],
  wantMarkers: 0,
});

// a post-roll: the ad is the last run, the content in front of it is long
cases.push({
  name: 'same-directory post-roll ad -> dropped, 0 markers',
  in: '#EXTM3U\n' +
    runOf(['/hls/c0.ts', '/hls/c1.ts'], 2, 200) +
    '\n#EXT-X-DISCONTINUITY\n' +
    runOf(['/hls/ad.ts'], 1, 19.6) +
    '\n#EXT-X-ENDLIST\n',
  expect: 'drop',
  wantSegs: ['/hls/c0.ts', '/hls/c1.ts'],
  wantMarkers: 0,
});

// the ad is too long to be an ad break -> kept
cases.push({
  name: 'run of 200s between long runs -> identical',
  in: '#EXTM3U\n' +
    runOf(['/hls/a.ts'], 1, 300) +
    '\n#EXT-X-DISCONTINUITY\n' +
    runOf(['/hls/b.ts'], 1, 200) +
    '\n#EXT-X-DISCONTINUITY\n' +
    runOf(['/hls/c.ts'], 1, 300) +
    '\n#EXT-X-ENDLIST\n',
  expect: 'same',
});

// the real ffzy capture: the ad run is the only one in the same directory
// with a short duration, and it must be the one that disappears
{
  const cPath = path.join(ROOT, '.opencode/skills/moontvplus-verification/fixtures/live-source-c.m3u8');
  const cText = fs.readFileSync(cPath, 'utf8');
  // split into runs at the markers
  const runs = [];
  let cur = [];
  for (const line of cText.split('\n')) {
    if (/#EXT-X-DISCONTINUITY(?!-)/.test(line)) {
      if (cur.length) runs.push(cur);
      cur = [];
      continue;
    }
    if (line.trim() && !line.trim().startsWith('#')) cur.push(line.trim());
  }
  if (cur.length) runs.push(cur);
  // the ad run: 5 segments of 19.6s between 358.7s and 949.9s of content,
  // all under the same directory (so the signature rule cannot see it)
  const adRuns = runs.filter((r) => r.length <= 6);
  const kept = runs.filter((r) => r.length > 6).flat();
  if (adRuns.length !== 1 || kept.length !== 327) {
    throw new Error('fixture C changed shape: ' + runs.map((r) => r.length).join(','));
  }
  cases.push({
    name: 'real live manifest C (same-directory ad) -> ad run gone, 0 markers',
    in: cText,
    expect: 'drop',
    wantSegs: kept,
    wantMarkers: 0,
  });
}

// the real source A capture: the whole episode is cut into 5-segment chunks
// and the ad is a *part* of a chunk (1-3 segments), so only the segment
// count gives it away.
{
  const dPath = path.join(ROOT, '.opencode/skills/moontvplus-verification/fixtures/live-source-d.m3u8');
  const dText = fs.readFileSync(dPath, 'utf8');
  const dRuns = [];
  let dCur = [];
  for (const line of dText.split('\n')) {
    if (/#EXT-X-DISCONTINUITY(?!-)/.test(line)) {
      if (dCur.length) dRuns.push(dCur);
      dCur = [];
      continue;
    }
    if (line.trim() && !line.trim().startsWith('#')) dCur.push(line.trim());
  }
  if (dCur.length) dRuns.push(dCur);
  // the ad runs are the only ones with fewer than 4 segments
  const dAd = dRuns.filter((r) => r.length <= 3);
  const dKept = dRuns.filter((r) => r.length > 3).flat();
  if (dAd.length !== 6) {
    throw new Error('fixture D changed shape: ' + dRuns.map((r) => r.length).join(','));
  }
  cases.push({
    name: 'real live manifest D (chunked source) -> 6 ad runs gone, no content lost',
    in: dText,
    expect: 'drop',
    wantSegs: dKept,
    // 159 markers in, 149 out: the ad's own markers and the four markers
    // that framed the ad go away, so the content around it stays on one
    // continuous timeline (a marker there is what re-anchors the audio).
    wantMarkers: 149,
  });
}

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
