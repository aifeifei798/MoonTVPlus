import { filterAdsFromM3U8 } from './m3u8-filter';

const HEADER = [
  '#EXTM3U',
  '#EXT-X-VERSION:3',
  '#EXT-X-TARGETDURATION:12',
  '#EXT-X-MEDIA-SEQUENCE:0',
];

function segs(n: number, dur = 12, start = 0): string[] {
  const lines: string[] = [];
  for (let i = 0; i < n; i++) {
    lines.push(`#EXTINF:${dur.toFixed(1)},`, `seg${start + i}.ts`);
  }
  return lines;
}

function countSegs(m3u8: string): number {
  return (m3u8.match(/\.ts$/gm) || []).length;
}

describe('filterAdsFromM3U8', () => {
  it('空输入返回空字符串', () => {
    expect(filterAdsFromM3U8('')).toBe('');
  });

  it('无 DISCONTINUITY 时原样返回', () => {
    const input = [...HEADER, ...segs(5), '#EXT-X-ENDLIST'].join('\n');
    expect(filterAdsFromM3U8(input)).toBe(input);
  });

  it('短干净广告块被删除（绝对量小，不触发熔断）', () => {
    // 2 分片正片 + 5 分片×12s=60s 广告 + 2 分片正片
    const input = [
      ...HEADER,
      ...segs(2, 12, 0),
      '#EXT-X-DISCONTINUITY',
      ...segs(5, 12, 2),
      '#EXT-X-DISCONTINUITY',
      ...segs(2, 12, 7),
      '#EXT-X-ENDLIST',
    ].join('\n');
    const out = filterAdsFromM3U8(input);
    expect(countSegs(out)).toBe(4);
    expect(out).not.toContain('seg2.ts');
  });

  it('超过 300s 的块视为正片予以保留', () => {
    const input = [
      ...HEADER,
      ...segs(2, 12, 0),
      '#EXT-X-DISCONTINUITY',
      ...segs(30, 12, 2), // 360s
      '#EXT-X-DISCONTINUITY',
      ...segs(2, 12, 32),
      '#EXT-X-ENDLIST',
    ].join('\n');
    expect(filterAdsFromM3U8(input)).toBe(input);
  });

  it('含非分片标签的不干净块予以保留', () => {
    const input = [
      ...HEADER,
      ...segs(2, 12, 0),
      '#EXT-X-DISCONTINUITY',
      '#EXT-X-CUE-OUT:DURATION=60',
      ...segs(5, 12, 2),
      '#EXT-X-CUE-IN',
      '#EXT-X-DISCONTINUITY',
      ...segs(2, 12, 7),
      '#EXT-X-ENDLIST',
    ].join('\n');
    expect(filterAdsFromM3U8(input)).toBe(input);
  });

  it('熔断：周期性切片分界的源原样返回（误删 960s/2400s）', () => {
    const lines = [...HEADER];
    let seg = 0;
    for (let chunk = 0; chunk < 10; chunk++) {
      if (chunk > 0) lines.push('#EXT-X-DISCONTINUITY');
      lines.push(...segs(20, 12, seg));
      seg += 20;
    }
    lines.push('#EXT-X-ENDLIST');
    const input = lines.join('\n');
    // 不熔断会被删掉 80 个分片；熔断后必须原样返回
    expect(filterAdsFromM3U8(input)).toBe(input);
  });
});
