/* eslint-disable @typescript-eslint/ban-ts-comment, @typescript-eslint/no-explicit-any, react-hooks/exhaustive-deps, no-console, @next/next/no-img-element */

/**
 * 播放页纯函数工具集合。
 *
 * 将不依赖 React 状态/生命周期、可独立测试的纯逻辑从巨型播放页中剥离，
 * 便于后续单元测试与复用。
 */

import {
  buildSegmentCacheKey,
  DEFAULT_CACHE_SETTINGS,
  loadCacheSettings,
  readCachedSegment,
  recordSegmentProbe,
  touchMeta,
} from '@/lib/video-cache';

/** 切集后延迟恢复弹幕可见性的毫秒数 */
export const DANMAKU_VISIBLE_RESTORE_DELAY_MS = 1500;

/** 跳过片头片尾配置结构 */
export interface SkipConfig {
  enable: boolean;
  intro_time: number;
  outro_time: number;
}

/**
 * 将秒数格式化为 00:00 或 00:00:00。
 */
export function formatTime(seconds: number): string {
  if (seconds === 0) return '00:00';

  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remainingSeconds = Math.round(seconds % 60);

  if (hours === 0) {
    // 不到一小时，格式为 00:00
    return `${minutes.toString().padStart(2, '0')}:${remainingSeconds
      .toString()
      .padStart(2, '0')}`;
  } else {
    // 超过一小时，格式为 00:00:00
    return `${hours.toString().padStart(2, '0')}:${minutes
      .toString()
      .padStart(2, '0')}:${remainingSeconds.toString().padStart(2, '0')}`;
  }
}

/**
 * 去广告：把 M3U8 里的广告分片（及其 #EXTINF 行）出。
 *
 * Upstream only deleted the `#EXT-X-DISCONTINUITY` lines and kept every
 * segment. That is the bug behind "picture freezes, audio keeps playing":
 * the ad segments come from a different encoder (measured on a live source:
 * content 1280x536 h264, ad 1920x1080 h264), so their SPS/PPS and moof
 * differ. hls.js emits a fresh init segment only at a discontinuity, so
 * deleting the marker makes the ad's mdat get appended under the content's
 * init segment — the video decoder produces no frames (frozen picture)
 * while the AAC track, whose params match, keeps decoding (sound).
 *
 * Correct shape: drop the ad segments themselves. A marker is then only
 * honest where the codec really changes, and on the measured sources it
 * does not: the content on both sides of a cut-out ad shares one encoder
 * AND one continuous timeline (0000074.ts ends at PTS 301.863 while
 * 0000075.ts starts at exactly 301.863), so the splice is a plain gap in
 * an otherwise continuous playlist. Declaring a discontinuity there makes
 * hls.js reset its muxers and re-anchor the two tracks with different
 * offsets, which is the "audio and picture drift 1-3s after the ad" bug.
 * So a marker is emitted at a splice seam only when the two sides'
 * host+directory differ; a marker the packager declared between two
 * untouched groups is never dropped, since that one may be a real codec
 * change. Ad groups are recognised as the minority runs of segment URLs
 * whose host+directory differs from the playlist's dominant one and
 * whose duration share is small; when nothing matches the manifest is
 * returned untouched (markers intact).
 *
 * A second rule catches the ads the signature cannot see: the sources that
 * splice the ad in from the *same* directory (measured with ffprobe on
 * source E: the ad is 1920x1080 while the content is 1280x720, both under
 * `.../hls/`, so the host+directory signature is identical on both sides of
 * the ad and the run is invisible to the signature rule). What those ads
 * always have is a shape: a SHORT run wedged between LONG content runs —
 * source E 19.6s and 17.4s between 359.9s and 1319.3s, source C 25.8s between
 * 300.6s and 1123.5s, source D 17.6s between 153.9s and 1392.9s, source B 26.0s
 * between 300.2s and 1120.0s.
 *
 * That rule has to be conservative, because some sources chop the episode
 * into many short runs (source F 2-40s, source C 36-43s, source E 16-99s) and those runs
 * are content, not ads. It therefore only fires when a neighbour run is at
 * least {@link CONTENT_MIN_DURATION_S} long — a source that chops the
 * episode never has such a run, so it is never touched. Over the 37 live
 * episode manifests of the sources this box has configured it flags the 31
 * real ads and none of the content runs.
 *
 * Dropping the ad is what fixes the sound, not just the picture: the ad
 * carries its own timeline (its PTS restarts at 1.48s while the content
 * around it continues at 361.4s), so at the packager's discontinuity
 * hls.js re-anchors the two tracks differently — `remuxAudio` anchors the
 * audio to the fragment's own PTS, `remuxVideo` to the playlist position —
 * and the sound ends up behind the picture. With the ad gone there is no
 * discontinuity at that seam, so both tracks stay on one timeline.
 */
export function filterAdsFromM3U8(m3u8Content: string): string {
  /**
   * Duration thresholds for the "short run wedged between long runs" ad rule.
   *
   * Measured on the live sources this box has configured (10 sources, 37 real
   * episode manifests):
   *   - ad runs: 26.0s, 25.8s, 17.6s, 19.6s and 17.4s
   *   - the content runs next to them: 300s/1120s, 300s/1123s, 153s/1392s,
   *     359s/1319s
   *   - the sources that chop an episode into many short runs (2-40s, 36-43s,
   *     16-99s) never produce a run this long, so the rule cannot misfire
   *     there.
   */
  const AD_MAX_DURATION_S = 90; // an ad break is never longer than this
  const CONTENT_MIN_DURATION_S = 120; // a content chunk is at least this long
  const AD_MAX_NEIGHBOR_RATIO = 0.25; // the ad is at most a quarter of it
  const AD_MAX_TIMELINE_SHARE = 0.15; // and at most this share of the timeline

  if (!m3u8Content) return '';

  const lines = m3u8Content.split('\n');

  // No discontinuity at all → nothing to strip, return as-is.
  // `(?!-)` keeps `#EXT-X-DISCONTINUITY-SEQUENCE:n` a directive, not a seam:
  // hls.js's own parser tries the `-SEQUENCE` alternative first, and eating
  // that directive would desync its discontinuity numbering.
  const isMarkerLine = (line: string) => /#EXT-X-DISCONTINUITY(?!-)/.test(line);
  if (!lines.some(isMarkerLine)) return m3u8Content;

  /**
   * Split the playlist into groups at every `#EXT-X-DISCONTINUITY` line.
   * The marker lines themselves are dropped here and re-emitted later, so
   * their count never leaks into the output.
   *   - lines: every non-marker line of the group (directives kept verbatim)
   *   - segs : the group's segment URIs, each tagged with its #EXTINF duration
   */
  const groups: { lines: string[]; segs: { url: string; dur: number }[] }[] = [];
  let groupLines: string[] = [];
  let groupSegs: { url: string; dur: number }[] = [];
  let pendingDur: number | null = null;

  const closeGroup = () => {
    groups.push({ lines: groupLines, segs: groupSegs });
    groupLines = [];
    groupSegs = [];
    pendingDur = null;
  };

  for (const line of lines) {
    if (isMarkerLine(line)) {
      closeGroup();
      continue;
    }
    groupLines.push(line);
    const trimmed = line.trim();
    if (trimmed === '' || trimmed.startsWith('#')) {
      const extinf = /^#EXTINF:(-?\d*\.?\d+(?:e[+-]?\d+)?)/i.exec(trimmed);
      if (extinf) {
        const parsed = parseFloat(extinf[1]);
        pendingDur = Number.isFinite(parsed) ? parsed : 0;
      }
      continue;
    }
    groupSegs.push({ url: trimmed, dur: pendingDur ?? 0 });
    pendingDur = null;
  }
  closeGroup();

  /**
   * Signature of a segment URI: host + directory, ignoring the file name
   * and query string. Ads are spliced in from another directory (or host),
   * while the content around them shares one.
   *   https://host:65/a/b/x.ts → "host:65|/a/b"
   *   /video/adjump/time/x.ts  → "|/video/adjump/time"
   *   0000000.ts               → "|"
   */
  const segmentSignature = (url: string): string => {
    let host = '';
    let path = url;
    const scheme = /^[a-z][a-z0-9-]*:\/\//i.exec(url);
    if (scheme) {
      const afterScheme = url.slice(scheme[0].length);
      const slash = afterScheme.indexOf('/');
      if (slash < 0) {
        host = afterScheme;
        path = '';
      } else {
        host = afterScheme.slice(0, slash);
        path = afterScheme.slice(slash);
      }
    }
    const query = path.indexOf('?');
    if (query >= 0) path = path.slice(0, query);
    const lastSlash = path.lastIndexOf('/');
    const dir = lastSlash <= 0 ? '' : path.slice(0, lastSlash);
    return `${host}|${dir}`;
  };

  // Tally every segment by signature, so the "dominant" one (the encoder
  // the bulk of the timeline comes from) is known before any group is judged.
  const sigCount = new Map<string, number>();
  let totalSegs = 0;
  let totalDur = 0;
  for (const group of groups) {
    for (const seg of group.segs) {
      const key = segmentSignature(seg.url);
      sigCount.set(key, (sigCount.get(key) ?? 0) + 1);
      totalSegs += 1;
      totalDur += seg.dur;
    }
  }
  if (totalSegs === 0) return m3u8Content;

  let dominantSig = '';
  let dominantCount = 0;
  for (const [sig, count] of sigCount) {
    if (count > dominantCount) {
      dominantCount = count;
      dominantSig = sig;
    }
  }

  // No clear majority (load-balanced mirrors, stitched files…) → the
  // signature cannot tell the ad from the content, so that rule stays off.
  // The duration rule below does not need a majority: it only asks how a run
  // sits between its neighbours.
  const hasDominantSignature = dominantCount / totalSegs >= 0.6;

  /**
   * Majority signature of a group, so the seam logic can ask "same
   * encoder on both sides?" without re-tallying every time.
   */
  const modalSignature = (segs: { url: string; dur: number }[]): string => {
    const counts = new Map<string, number>();
    for (const seg of segs) {
      const key = segmentSignature(seg.url);
      counts.set(key, (counts.get(key) ?? 0) + 1);
    }
    let best = '';
    let bestCount = 0;
    for (const [sig, count] of counts) {
      if (count > bestCount) {
        bestCount = count;
        best = sig;
      }
    }
    return best;
  };

  const groupSig = groups.map((group) => modalSignature(group.segs));
  const groupDur = groups.map((group) =>
    group.segs.reduce((sum, seg) => sum + seg.dur, 0),
  );

  /**
   * "A short run wedged between long content runs" — the shape an ad
   * break has when the packager splices it in from the same directory
   * (so the signature is identical on both sides and cannot see it).
   *
   * The guards keep it away from the sources that chop an episode into
   * many short runs: those never have a neighbour this long, so the rule
   * cannot fire there, and the run's own share of the timeline has to stay
   * small as well.
   */
  const isAdShapedRun = (index: number): boolean => {
    if (groups[index].segs.length === 0) return false;
    const dur = groupDur[index];
    if (!(dur > 0) || dur > AD_MAX_DURATION_S) return false;
    if (totalDur > 0 && dur > AD_MAX_TIMELINE_SHARE * totalDur) return false;
    for (const neighbour of [index - 1, index + 1]) {
      if (neighbour < 0 || neighbour >= groups.length) continue;
      if (groups[neighbour].segs.length === 0) continue;
      const neighbourDur = groupDur[neighbour];
      if (
        neighbourDur >= CONTENT_MIN_DURATION_S &&
        dur <= AD_MAX_NEIGHBOR_RATIO * neighbourDur
      ) {
        return true;
      }
    }
    return false;
  };

  const dropGroup: boolean[] = groups.map((group, i) => {
    if (group.segs.length === 0) return false;
    // Rule 1: another encoder (a different host+directory) and only a small
    // slice of the timeline.
    if (hasDominantSignature && groupSig[i] !== dominantSig) {
      // A run that owns a big slice of the timeline is content, not an ad.
      const share =
        totalDur > 0 ? groupDur[i] / totalDur : group.segs.length / totalSegs;
      if (share <= 0.35) return true;
    }
    // Rule 2: a short run wedged between long content runs — the ad the
    // signature cannot see because it comes from the same directory.
    return isAdShapedRun(i);
  });

  // Nothing looked like an ad → return the original bytes, markers included.
  if (dropGroup.every((flag) => !flag)) return m3u8Content;

  /**
   * `#EXT-X-KEY` is inherited by later groups until one is redeclared, so
   * dropping a group that declared one would hand the following segments
   * the previous group's key. The dropped group's last key line is put
   * back at the seam to keep that inheritance.
   */
  const lastKeyLine = (groupLines: string[]): string | null => {
    for (let k = groupLines.length - 1; k >= 0; k--) {
      if (/^#EXT-X-KEY\b/i.test(groupLines[k].trim())) return groupLines[k];
    }
    return null;
  };

  const keptLines: string[] = [];
  let lastKept = -1;
  let inheritedKey: string | null = null;

  for (let i = 0; i < groups.length; i++) {
    if (dropGroup[i]) {
      const key = lastKeyLine(groups[i].lines);
      if (key !== null) inheritedKey = key;
      continue;
    }
    const group = groups[i];
    if (group.segs.length > 0) {
      if (lastKept >= 0) {
        // Was something spliced out between this group and the previous
        // kept one? Only those seams may lose the marker: a discontinuity
        // the packager declared between untouched groups stays, because it
        // can be a genuine codec change that needs a fresh init segment.
        let spliced = false;
        for (let k = lastKept + 1; k < i; k++) {
          if (dropGroup[k]) {
            spliced = true;
            break;
          }
        }
        if (!spliced || groupSig[i] !== groupSig[lastKept]) {
          keptLines.push('#EXT-X-DISCONTINUITY');
        }
      }
      lastKept = i;
    }
    if (inheritedKey !== null) {
      keptLines.push(inheritedKey);
      inheritedKey = null;
    }
    keptLines.push(...group.lines);
  }

  return keptLines.join('\n');
}

/**
 * 弹幕密度限制参数。
 *
 * 用时间补偿的方式平滑削峰，避免硬窗口把某个时间段直接掏空。
 */
const DANMAKU_REFILL_RATE = 14 / 5;
const DANMAKU_BUCKET_CAPACITY = 28;
const DANMAKU_MAX_TOTAL = 8000;

function createDanmakuFilter() {
  let credits = DANMAKU_BUCKET_CAPACITY;
  let lastTime = -Infinity;
  let acceptedCount = 0;

  return (danmu: any) => {
    if (!danmu || typeof danmu !== 'object') return false;

    if (typeof danmu.text === 'string' && danmu.text.length > 100) {
      return false;
    }

    const rawTime =
      typeof danmu.time === 'number'
        ? danmu.time
        : typeof danmu.time === 'string'
        ? Number(danmu.time)
        : NaN;

    if (!Number.isFinite(rawTime)) {
      return true;
    }

    if (rawTime + 0.5 < lastTime) {
      credits = DANMAKU_BUCKET_CAPACITY;
      acceptedCount = 0;
      lastTime = rawTime;
    } else {
      const elapsed = Math.max(0, rawTime - lastTime);
      credits = Math.min(
        DANMAKU_BUCKET_CAPACITY,
        credits + elapsed * DANMAKU_REFILL_RATE
      );
      lastTime = rawTime;
    }

    if (acceptedCount >= DANMAKU_MAX_TOTAL) {
      return false;
    }

    if (credits < 1) {
      return false;
    }

    credits -= 1;
    acceptedCount += 1;
    return true;
  };
}

/**
 * 计算播放源综合评分。
 *
 * 权重：分辨率 60% + 下载速度 30% + 网络延迟 10%。
 *
 * 原则：**画质优先，但慢源降权**——高分辨率源优先；速度作为同档位内的
 * 打破平局（同样 1080p 时选更快的），并把慢到会卡的源明显压低，避免
 * 选到"分辨率高但卡成 PPT"的源。
 */
export function calculateSourceScore(
  testResult: {
    quality: string;
    loadSpeed: string;
    pingTime: number;
  },
  maxSpeed: number,
  minPing: number,
  maxPing: number
): number {
  let score = 0;

  // 分辨率评分 (60% 权重) —— 画质优先
  const qualityScore = (() => {
    switch (testResult.quality) {
      case '4K':
        return 100;
      case '2K':
        return 85;
      case '1080p':
        return 75;
      case '720p':
        return 60;
      case '480p':
        return 40;
      case 'SD':
        return 20;
      default:
        return 0;
    }
  })();
  score += qualityScore * 0.6;

  // 下载速度评分 (30% 权重) —— 达标加分 + 同档打破平局
  const speedScore = (() => {
    const speedStr = testResult.loadSpeed;
    if (speedStr === '未知' || speedStr === '测量中...') return 30;

    // 解析速度值
    const match = speedStr.match(/^([\d.]+)\s*(KB\/s|MB\/s)$/);
    if (!match) return 30;

    const value = parseFloat(match[1]);
    const unit = match[2];
    const speedKBps = unit === 'MB/s' ? value * 1024 : value;

    // 基于最大速度线性映射，最高100分
    const speedRatio = speedKBps / maxSpeed;
    return Math.min(100, Math.max(0, speedRatio * 100));
  })();
  score += speedScore * 0.3;

  // 网络延迟评分 (10% 权重) - 基于延迟范围线性映射
  const pingScore = (() => {
    const ping = testResult.pingTime;
    if (ping <= 0) return 0; // 无效延迟给默认分

    // 如果所有延迟都相同，给满分
    if (maxPing === minPing) return 100;

    // 线性映射：最低延迟=100分，最高延迟=0分
    const pingRatio = (maxPing - ping) / (maxPing - minPing);
    return Math.min(100, Math.max(0, pingRatio * 100));
  })();
  score += pingScore * 0.1;

  return Math.round(score * 100) / 100; // 保留两位小数
}

/**
 * 创建弹幕插件的默认配置对象。
 *
 * 每次调用返回全新对象，避免多处共享同一引用导致状态污染。
 */
export function createDanmakuDefaultConfig(): any {
  return {
    danmuku: '',
    speed: 5,
    margin: [10, '25%'],
    opacity: 1,
    color: '#FFFFFF',
    mode: 0,
    modes: [0, 1, 2],
    fontSize: 25,
    antiOverlap: true,
    synchronousPlayback: false,
    mount: undefined,
    heatmap: false,
    width: 512,
    points: [],
    filter: createDanmakuFilter(),
    beforeVisible: () => true,
    visible: true,
    emitter: false,
    maxLength: 200,
    lockTime: 5,
    theme: 'dark',
    OPACITY: {},
    FONT_SIZE: {},
    MARGIN: {},
    SPEED: {},
    COLOR: [],
    beforeEmit(_danmu: any) {
      return new Promise((resolve) => {
        setTimeout(() => {
          resolve(true);
        }, 1000);
      });
    },
  };
}

/** 弹幕设置本地存储键 */
export const DANMAKU_SETTINGS_STORAGE_KEY = 'danmaku_settings';

/**
 * 可持久化到本地的弹幕设置字段。
 *
 * 仅包含用户可调的展示类设置，不包含 danmuku 地址、mount 等运行时字段。
 */
export interface DanmakuSettings {
  /** 弹幕是否可见 */
  visible: boolean;
  /** 不透明度，范围 [0, 1] */
  opacity: number;
  /** 字号（像素） */
  fontSize: number;
  /** 弹幕速度，范围 [1, 10] */
  speed: number;
  /** 显示区域 [上边距, 下边距] */
  margin: [number | string, number | string];
  /** 发送弹幕的模式：0-滚动，1-顶部，2-底部 */
  mode: number;
  /** 可见的弹幕模式列表 */
  modes: number[];
  /** 是否防止弹幕重叠 */
  antiOverlap: boolean;
  /** 是否同步视频速度 */
  synchronousPlayback: boolean;
  /** 默认弹幕颜色 */
  color: string;
}

/** 将数值限制在 [min, max] 区间内，非法值返回 undefined */
function clampNumber(
  value: unknown,
  min: number,
  max: number
): number | undefined {
  if (typeof value !== 'number' || !Number.isFinite(value)) return undefined;
  return Math.min(max, Math.max(min, value));
}

/** 校验弹幕上下边距（支持像素数字与百分比字符串） */
function normalizeMargin(
  value: unknown
): [number | string, number | string] | undefined {
  if (!Array.isArray(value) || value.length !== 2) return undefined;

  const normalizeEdge = (edge: unknown): number | string | undefined => {
    if (typeof edge === 'number' && Number.isFinite(edge)) return edge;
    if (typeof edge === 'string' && /^\d+(\.\d+)?%$/.test(edge)) return edge;
    return undefined;
  };

  const top = normalizeEdge(value[0]);
  const bottom = normalizeEdge(value[1]);
  if (top === undefined || bottom === undefined) return undefined;

  return [top, bottom];
}

/**
 * 从（可能不完整或不可信的）弹幕配置对象中提取可持久化的设置字段。
 *
 * 同时用于写入前的字段提取与读取后的数据校验，避免脏数据注入播放器配置。
 */
export function pickDanmakuSettings(config: any): Partial<DanmakuSettings> {
  const result: Partial<DanmakuSettings> = {};
  if (!config || typeof config !== 'object') return result;

  if (typeof config.visible === 'boolean') result.visible = config.visible;

  const opacity = clampNumber(config.opacity, 0, 1);
  if (opacity !== undefined) result.opacity = opacity;

  const fontSize = clampNumber(config.fontSize, 12, 120);
  if (fontSize !== undefined) result.fontSize = fontSize;

  const speed = clampNumber(config.speed, 1, 10);
  if (speed !== undefined) result.speed = speed;

  const margin = normalizeMargin(config.margin);
  if (margin) result.margin = margin;

  const mode = clampNumber(config.mode, 0, 2);
  if (mode !== undefined) result.mode = Math.round(mode);

  if (Array.isArray(config.modes)) {
    const modes = config.modes.filter(
      (m: unknown): m is number =>
        typeof m === 'number' && Number.isFinite(m) && m >= 0 && m <= 2
    );
    result.modes = Array.from(new Set(modes));
  }

  if (typeof config.antiOverlap === 'boolean') {
    result.antiOverlap = config.antiOverlap;
  }

  if (typeof config.synchronousPlayback === 'boolean') {
    result.synchronousPlayback = config.synchronousPlayback;
  }

  if (typeof config.color === 'string' && config.color) {
    result.color = config.color;
  }

  return result;
}

/**
 * 读取本地保存的弹幕设置（含校验），无有效数据时返回空对象。
 */
export function loadDanmakuSettings(): Partial<DanmakuSettings> {
  if (typeof window === 'undefined') return {};

  try {
    const raw = window.localStorage.getItem(DANMAKU_SETTINGS_STORAGE_KEY);
    if (!raw) return {};
    return pickDanmakuSettings(JSON.parse(raw));
  } catch {
    return {};
  }
}

/**
 * 将弹幕设置写入本地存储。
 *
 * 默认与已有设置合并，便于只更新部分字段；replace 为 true 时整体覆盖。
 */
export function saveDanmakuSettings(
  settings: Partial<DanmakuSettings>,
  options: { replace?: boolean } = {}
): void {
  if (typeof window === 'undefined') return;

  try {
    const merged = options.replace
      ? settings
      : { ...loadDanmakuSettings(), ...settings };
    window.localStorage.setItem(
      DANMAKU_SETTINGS_STORAGE_KEY,
      JSON.stringify(merged)
    );
  } catch {
    // localStorage 不可用（如隐私模式）时静默失败，不影响播放
  }
}

/**
 * 创建弹幕插件初始配置：默认配置叠加本地已保存的设置，刷新后可自动恢复。
 */
export function createDanmakuInitialConfig(): any {
  return {
    ...createDanmakuDefaultConfig(),
    ...loadDanmakuSettings(),
  };
}

export interface HlsLoaderOptions {
  /** 是否过滤 m3u8 中的 #EXT-X-DISCONTINUITY（去广告） */
  blockAd?: boolean;
  /** 片段缓存键是否走同源代理，必须与 VideoPrefetcher 保持一致 */
  useProxy?: boolean;
  /** 缓存诊断回调（命中/未命中），用于观测缓存键是否与预取器一致 */
  onProbe?: (hit: boolean) => void;
}

/**
 * 合成 hls.js 的 LoaderStats。
 *
 * 关键点：命中缓存时不能上报"耗时 0"。hls.js 的 ABR 用 EWMA 估算带宽，
 * 0 延迟会被当成无限带宽，导致下一批片段直接判到最高码率而卡顿。
 * 这里复用预取时记录的真实耗时，让带宽估算保持连续。
 */
function buildCachedStats(costMs: number, bytes: number): Record<string, unknown> {
  const end = performance.now();
  const span = Math.max(1, costMs);
  const start = end - span;
  const zero = { start: 0, first: 0, end: 0 };
  return {
    aborted: false,
    loaded: bytes,
    retry: 0,
    total: bytes,
    chunkCount: 1,
    bwEstimate: (bytes / span) * 1000,
    loading: { start, first: start + span * 0.6, end },
    parsing: { ...zero },
    buffering: { ...zero },
  };
}

/**
 * 创建「去广告 + 缓存优先」的 HLS Loader。
 *
 * - manifest / level：按需过滤 #EXT-X-DISCONTINUITY
 * - fragment：先查 Cache Storage（由 VideoPrefetcher 预取填充），命中直接返回，
 *   未命中再走原 loader 回源。因此缓存未命中时行为与默认 loader 完全一致。
 */
export function createCustomHlsLoader(
  Hls: any,
  options: HlsLoaderOptions = {}
): any {
  const BaseLoader = Hls.DefaultConfig.loader;
  const useAdFilter = options.blockAd !== false;
  const onProbe = options.onProbe ?? recordSegmentProbe;

  /**
   * 构建候选缓存键列表（按可能性先后排序）。
   *  - 不再信任创建时快照的 useProxy：设置面板里切开关后，loader 和预取器
   *    用的 format 可能不统一，因此两种 format 都生成，逐个试。
   *  - 再叠加 readCachedSegment 内部的「相对路径/绝对 URL」双重兜底，
   *    基本能覆盖所有 key 不一致的情况。
   */
  function candidateCacheKeys(contextUrl: string): string[] {
    const liveSettings =
      typeof window !== 'undefined' ? loadCacheSettings() : DEFAULT_CACHE_SETTINGS;
    const preferredUseProxy =
      options.useProxy !== undefined ? options.useProxy : liveSettings.useProxy;
    const urls = new Set<string>();
    // 优先按当前偏好的 useProxy 生成，其次反过来兜底
    urls.add(buildSegmentCacheKey(contextUrl, preferredUseProxy));
    urls.add(buildSegmentCacheKey(contextUrl, !preferredUseProxy));
    return Array.from(urls);
  }

  return class MoontvHlsLoader extends BaseLoader {
    constructor(config: any) {
      super(config);
      const baseLoad = this.load.bind(this);
      const baseAbort =
        typeof this.abort === 'function' ? this.abort.bind(this) : null;
      const baseDestroy =
        typeof this.destroy === 'function' ? this.destroy.bind(this) : null;

      // 基类是 any，成员无法直接推断，这里做一次显式断言。
      // `stats` 是 hls.js 内部读取带宽估算的来源，必须就地更新。
      const self = this as unknown as { stats: Record<string, unknown> };

      // 缓存查询是异步的：若期间 hls.js 取消了这次加载（seek / 切码率 /
      // 组件卸载），必须让后续的 onSuccess 与回源请求同时失效，
      // 否则会发出一个没人接收的网络请求。
      let cancelled = false;

      this.abort = function () {
        cancelled = true;
        baseAbort?.();
      };

      this.destroy = function () {
        cancelled = true;
        baseDestroy?.();
      };

      this.load = function (context: any, cfg: any, callbacks: any) {
        // hls.js 不同版本 loader 调用约定不一致，type/url 可能在 context 或 cfg 上：
        //   - type: manifest / level / fragment
        //   - url / uri: 实际请求地址
        // 这里做一次健壮的字段探测。
        const rawType =
          (context && typeof context.type === 'string' ? context.type : undefined) ??
          (cfg && typeof cfg.type === 'string' ? cfg.type : undefined);
        const rawUrl =
          (context && (typeof context.url === 'string' || typeof context.uri === 'string')
            ? (context.url || context.uri)
            : undefined) ??
          (cfg && (typeof cfg.url === 'string' || typeof cfg.uri === 'string')
            ? (cfg.url || cfg.uri)
            : undefined);
        const rangeStart = context?.rangeStart ?? cfg?.rangeStart;
        const type: string | undefined = rawType as any;
        const url = typeof rawUrl === 'string' ? rawUrl : '';

        // ① 文本播放列表：去广告
        if (type === 'manifest' || type === 'level') {
          if (useAdFilter) {
            const onSuccess = callbacks.onSuccess;
            callbacks.onSuccess = function (
              response: any,
              stats: any,
              ctx: any
            ) {
              if (response.data && typeof response.data === 'string') {
                response.data = filterAdsFromM3U8(response.data);
              }
              return onSuccess(response, stats, ctx, null);
            };
          }
          baseLoad(context, cfg, callbacks);
          return;
        }

        // ② 片段：缓存优先
        //    - 真正需要跳过缓存的字节范围分片特征是：URL 完全相同、每次请求的字节区段不同。
        //      但 hls.js 常把 rangeStart=0、rangeEnd=undefined 也传过来（语义就是整个文件），
        //      这对 plist0.ts / plist1.ts 这种"每个分片一个独立 URL"的源站完全可以缓存。
        //      所以只有 rangeStart > 0 才保守跳过；rangeStart == null / ==0 一律正常走缓存。
        //    - 没有明确 type 时：用 URL 后缀/关键词启发式判断是不是视频/音频分片。
        const looksLikeFragment =
          /\.(ts|m4s|m4v|mp4|webm|aac|mp3)(\?|$)/i.test(url) ||
          /segment|fragment|clip|part|chunk|frag|plist/i.test(url);
        const treatAsFragment = type === 'fragment' || (!type && looksLikeFragment);
        const isTrueByteRange =
          rangeStart != null &&
          (typeof rangeStart === 'number' ? rangeStart > 0 : String(rangeStart) !== '0');

        if (
          !treatAsFragment ||
          isTrueByteRange ||
          typeof caches === 'undefined'
        ) {
          baseLoad(context, cfg, callbacks);
          return;
        }

        // 候选缓存键按 (useProxy=true/false) × (相对路径/绝对URL) 生成，
        // 任何一个命中就算命中，解决设置切换 / 浏览器自动补全 origin 导致的不匹配。
        const keys = candidateCacheKeys(url);

        (async () => {
          for (const cacheKey of keys) {
            const res = await readCachedSegment(cacheKey);
            if (!res) continue;
            if (cancelled) return;

            const { fragment, matchedKey } = res;

            onProbe(true);
            void touchMeta(matchedKey);

            // 就地更新基础类的 stats，避免 hls.js 带宽估算失真
            const stats = buildCachedStats(
              fragment.costMs,
              fragment.data.byteLength
            );
            Object.assign(self.stats, stats);

            callbacks.onSuccess(
              { url: context.url, data: fragment.data },
              self.stats,
              context
            );
            return;
          }

          // 所有候选键都没命中 → 走网络
          if (cancelled) return;
          onProbe(false);
          baseLoad(context, cfg, callbacks);
        })().catch(() => {
          if (!cancelled) baseLoad(context, cfg, callbacks);
        });
      };
    }
  };
}
