/**
 * M3U8 去广告过滤。
 *
 * 启发式：两个 `#EXT-X-DISCONTINUITY` 之间的“干净块”
 * （只有 EXTINF + 分片地址行）且总时长不超过上限时，视为中插广告并删除。
 *
 * 熔断：部分转码源用 DISCONTINUITY 做切片分界，此时启发式会隔块误删正片。
 * 当删除量占比过高时宁可放过广告，直接返回原始内容。
 */

const MAX_AD_DURATION_SECONDS = 300;

// 删除占比超过该阈值（且删除绝对量超过下限）时触发熔断。
// 正常中插广告通常只有几十秒，远低于此线。
const REMOVE_RATIO_LIMIT = 0.3;
const REMOVE_SECONDS_FLOOR = 120;

const isDiscontinuity = (line: string): boolean =>
  line.trim() === '#EXT-X-DISCONTINUITY';

const isSegmentLine = (line: string): boolean => {
  const t = line.trim();
  if (t === '') return true;
  if (t.startsWith('#EXTINF')) return true;
  return !t.startsWith('#');
};

function parseExtinfDuration(line: string): number {
  const match = line.match(/#EXTINF:\s*([\d.]+)/);
  if (!match) return 0;
  const duration = parseFloat(match[1]);
  return Number.isFinite(duration) ? duration : 0;
}

export function filterAdsFromM3U8(m3u8Content: string): string {
  if (!m3u8Content) return '';

  const lines = m3u8Content.split('\n');
  const filteredLines: string[] = [];

  let inAdBlock = false;
  let pendingLines: string[] = [];
  let pendingDuration = 0;
  let pendingClean = true;
  let totalDuration = 0;
  let removedDuration = 0;

  for (const line of lines) {
    if (line.includes('#EXTINF:')) {
      totalDuration += parseExtinfDuration(line);
    }

    if (isDiscontinuity(line)) {
      if (inAdBlock) {
        if (!pendingClean || pendingDuration > MAX_AD_DURATION_SECONDS) {
          filteredLines.push(...pendingLines);
        } else {
          removedDuration += pendingDuration;
        }
        pendingLines = [];
        pendingDuration = 0;
        pendingClean = true;
        inAdBlock = false;
      } else {
        inAdBlock = true;
      }
      filteredLines.push(line);
      continue;
    }

    if (inAdBlock) {
      if (line.includes('#EXTINF:')) {
        pendingDuration += parseExtinfDuration(line);
      }
      if (!isSegmentLine(line)) pendingClean = false;
      pendingLines.push(line);
      continue;
    }

    filteredLines.push(line);
  }

  if (inAdBlock) {
    filteredLines.push(...pendingLines);
  }

  if (
    totalDuration > 0 &&
    removedDuration / totalDuration > REMOVE_RATIO_LIMIT &&
    removedDuration > REMOVE_SECONDS_FLOOR
  ) {
    // eslint-disable-next-line no-console
    console.warn(
      `[去广告熔断] 将删除 ${Math.round(removedDuration)}s / 共 ${Math.round(totalDuration)}s，跳过去广告还原原始播放列表`,
    );
    return m3u8Content;
  }

  return filteredLines.join('\n');
}
