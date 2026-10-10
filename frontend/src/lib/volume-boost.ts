/**
 * 声音增强 — WebAudio GainNode，2 倍增益，默认开启。
 *
 * 原理：`video.volume` 最大只能到 1，有些片源录得轻，推满也不够。
 * 这里在 video 后面串一个 GainNode：`video → gain(2.0) → destination`，
 * 开关只改 `gain.value`（2.0 / 1.0），不断图，不断播。
 *
 * 为什么默认开也不会把正常视频搞成爆音或静音：
 * - 播放器建 video 时已经带了 `crossOrigin='anonymous'`，能播的源
 *   CORS 都是干净的——MediaElementSource 接上去不会输出静音。
 *   真接不上（比如 iOS 古董）`ensure` 会抛，进 catch 直接返回 false，
 *   开关弹回关闭，视频本身不受影响。
 * - 一个页面只建一个 AudioContext；一个 video 只建一次 source
 *   （重复 createMediaElementSource 会抛，靠 WeakMap 避开）。
 * - 切集/切源换了 video 元素时，把旧图拆掉接到新元素上，
 *   旧 context 不关（关了再建会有爆音间隙）。
 */

export const VOLUME_BOOST_GAIN = 2.0;

const STORE_KEY = 'moontv.volumeBoost';

interface BoostGraph {
  src: MediaElementAudioSourceNode;
  gain: GainNode;
}

let sharedCtx: AudioContext | null = null;
let currentVideo: HTMLVideoElement | null = null;
const graphs = new WeakMap<HTMLVideoElement, BoostGraph>();

/** 开关记忆，默认开（null 就是开）。 */
export function loadVolumeBoostEnabled(): boolean {
  try {
    const raw = localStorage.getItem(STORE_KEY);
    return raw === null ? true : raw === 'true';
  } catch {
    return true;
  }
}

export function saveVolumeBoostEnabled(on: boolean): void {
  try {
    localStorage.setItem(STORE_KEY, String(on));
  } catch {
    // 隐私模式写不进就算了，默认开照样生效
  }
}

function sharedContext(): AudioContext | null {
  try {
    if (sharedCtx) return sharedCtx;
    const AC =
      window.AudioContext ||
      (window as unknown as { webkitAudioContext?: typeof AudioContext })
        .webkitAudioContext;
    if (!AC) return null;
    sharedCtx = new AC();
    return sharedCtx;
  } catch {
    return null;
  }
}

/**
 * 把增强图接到 `video` 上（已接过就只刷新 gain + resume）。
 * 失败返回 false，调用方负责把开关弹回去——视频本身不受影响。
 */
export function ensureVolumeBoost(
  video: HTMLVideoElement | null | undefined
): boolean {
  if (!video) return false;
  const ctx = sharedContext();
  if (!ctx) return false;
  try {
    if (currentVideo !== video) {
      const old = currentVideo ? graphs.get(currentVideo) : undefined;
      try {
        old?.src.disconnect();
        old?.gain.disconnect();
      } catch {
        // 旧图可能已经野了，不管
      }
      currentVideo = video;
    }
    let g = graphs.get(video);
    if (!g) {
      const src = ctx.createMediaElementSource(video);
      const gain = ctx.createGain();
      src.connect(gain);
      gain.connect(ctx.destination);
      g = { src, gain };
      graphs.set(video, g);
    }
    g.gain.gain.value = loadVolumeBoostEnabled() ? VOLUME_BOOST_GAIN : 1.0;
    // 自动播放策略下 context 可能是 suspended，playing 里会再调一次
    if (ctx.state === 'suspended') {
      void ctx.resume().catch(() => undefined);
    }
    return true;
  } catch {
    return false;
  }
}

/** 开关：先落盘，再对当前 video 生效（换集后由 ensure 重新应用）。 */
export function setVolumeBoostEnabled(
  video: HTMLVideoElement | null | undefined,
  on: boolean
): void {
  saveVolumeBoostEnabled(on);
  if (video && currentVideo === video) {
    const g = graphs.get(video);
    if (g) {
      try {
        g.gain.gain.value = on ? VOLUME_BOOST_GAIN : 1.0;
      } catch {
        // 设不进去下次 ensure 会纠正
      }
    }
  }
}
