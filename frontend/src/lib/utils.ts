/* eslint-disable @typescript-eslint/no-explicit-any,no-console */
import he from 'he';

function getDoubanImageProxyConfig(): {
  proxyType:
    | 'direct'
    | 'server'
    | 'img3'
    | 'cmliussss-cdn-tencent'
    | 'cmliussss-cdn-ali'
    | 'custom';
  proxyUrl: string;
} {
  const doubanImageProxyType =
    localStorage.getItem('doubanImageProxyType') ||
    (window as any).RUNTIME_CONFIG?.DOUBAN_IMAGE_PROXY_TYPE ||
    'direct';
  const doubanImageProxy =
    localStorage.getItem('doubanImageProxyUrl') ||
    (window as any).RUNTIME_CONFIG?.DOUBAN_IMAGE_PROXY ||
    '';
  return {
    proxyType: doubanImageProxyType,
    proxyUrl: doubanImageProxy,
  };
}

/**
 * 图片加载失败时的兜底：改走服务端 `/api/image-proxy`。
 *
 * 浏览器直连海报失败的两类原因：豆瓣图片站对**没有 Referer** 的请求直接回 418
 * （卡片都是 `referrerPolicy='no-referrer'`，所以 `direct` 模式的海报全部显示
 * 不出来），以及源站 CDN 在观看者所在的网络里不可达但服务器可达。代理带上游的
 * `Referer`，并对豆瓣/番组计划两族海报走镜像链，所以第二次尝试通常成功。
 *
 * 只重试一次，且只有绝对 URL 可以代理；返回 `false` 表示兜底已经用尽，调用方
 * 自己处理（占位符或隐藏）。
 */
export function retryImageThroughProxy(target: HTMLImageElement): boolean {
  const src = target.getAttribute('src') || '';
  if (!/^https?:\/\//i.test(src)) return false;
  // 标记存的是"已经兜底过的原始 URL"，所以换海报后仍可再兜底一次。
  if (target.dataset.imageProxyRetried === src) return false;
  target.dataset.imageProxyRetried = src;
  target.src = `/api/image-proxy?url=${encodeURIComponent(src)}`;
  return true;
}

/**
 * 海报作为 CSS background 时的 URL（Artplayer 的 `.art-poster` 是
 * `background-image`，不是 `<img>`，所以没有 `onError` 可以挂钩，不能像卡片那样
 * "先直连、失败再代理"）。豆瓣图片站对浏览器直连一律失败（无 Referer 418，本站
 * Referer 403），因此 `direct` 模式下的豆瓣海报在这里预先走服务端代理；其余模式
 * 与 `processImageUrl` 一致。
 */
export function posterUrlForCss(originalUrl: string): string {
  const processed = processImageUrl(originalUrl);
  // `processImageUrl` 已经把 http 图片（混合内容）和 `server` 模式换成代理 URL，
  // 那种结果不能再包一层：`/api/image-proxy?url=%2Fapi%2Fimage-proxy%3F...` 的
  // 内层不是绝对地址，代理会拒绝。所以只处理仍是绝对地址的豆瓣海报。
  if (
    /^https?:\/\//i.test(processed) &&
    processed.includes('doubanio.com') &&
    getDoubanImageProxyConfig().proxyType === 'direct'
  ) {
    return `/api/image-proxy?url=${encodeURIComponent(processed)}`;
  }
  return processed;
}

/**
 * 处理图片 URL，如果设置了图片代理则使用代理
 */
export function processImageUrl(originalUrl: string): string {
  if (!originalUrl) return originalUrl;

  const normalizedUrl = originalUrl.trim();

  // 已经是本地代理、内联资源或本地资源时，直接返回
  if (
    normalizedUrl.startsWith('/api/image-proxy?url=') ||
    normalizedUrl.startsWith('data:') ||
    normalizedUrl.startsWith('blob:') ||
    normalizedUrl.startsWith('/')
  ) {
    return normalizedUrl;
  }

  // HTTPS 页面下直接加载 http 图片会触发 Mixed Content，统一走服务端代理兜底
  if (normalizedUrl.startsWith('http://')) {
    return `/api/image-proxy?url=${encodeURIComponent(normalizedUrl)}`;
  }

  // 仅处理豆瓣图片代理
  if (!normalizedUrl.includes('doubanio.com')) {
    return normalizedUrl;
  }

  const { proxyType, proxyUrl } = getDoubanImageProxyConfig();
  switch (proxyType) {
    case 'server':
      return `/api/image-proxy?url=${encodeURIComponent(normalizedUrl)}`;
    case 'img3':
      return normalizedUrl.replace(/img\d+\.doubanio\.com/g, 'img3.doubanio.com');
    case 'cmliussss-cdn-tencent':
      return normalizedUrl.replace(
        /img\d+\.doubanio\.com/g,
        'img.doubanio.cmliussss.net'
      );
    case 'cmliussss-cdn-ali':
      return normalizedUrl.replace(
        /img\d+\.doubanio\.com/g,
        'img.doubanio.cmliussss.com'
      );
    case 'custom':
      return `${proxyUrl}${encodeURIComponent(normalizedUrl)}`;
    case 'direct':
    default:
      return normalizedUrl;
  }
}

/**
 * 从m3u8地址获取视频质量等级和网络信息
 * @param m3u8Url m3u8播放列表的URL
 * @returns Promise<{quality: string, loadSpeed: string, pingTime: number}> 视频质量等级和网络信息
 */
export async function getVideoResolutionFromM3u8(m3u8Url: string): Promise<{
  quality: string; // 如720p、1080p等
  loadSpeed: string; // 自动转换为KB/s或MB/s
  pingTime: number; // 网络延迟（毫秒）
}> {
  try {
    const { default: Hls } = await import('hls.js');
    // 直接使用m3u8 URL作为视频源，避免CORS问题
    return new Promise((resolve, reject) => {
      const video = document.createElement('video');
      video.muted = true;
      video.preload = 'metadata';

      // 测量网络延迟（ping时间） - 使用m3u8 URL而不是ts文件
      const pingStart = performance.now();
      let pingTime = 0;

      // 测量ping时间（使用m3u8 URL）
      fetch(m3u8Url, { method: 'HEAD', mode: 'no-cors' })
        .then(() => {
          pingTime = performance.now() - pingStart;
        })
        .catch(() => {
          pingTime = performance.now() - pingStart; // 记录到失败为止的时间
        });

      // 固定使用hls.js加载
      const hls = new Hls();

      // 设置超时处理
      const timeout = setTimeout(() => {
        hls.destroy();
        video.remove();
        reject(new Error('Timeout loading video metadata'));
      }, 4000);

      video.onerror = () => {
        clearTimeout(timeout);
        hls.destroy();
        video.remove();
        reject(new Error('Failed to load video metadata'));
      };

      let actualLoadSpeed = '未知';
      let hasSpeedCalculated = false;
      let hasMetadataLoaded = false;

      let fragmentStartTime = 0;

      // 检查是否可以返回结果
      const checkAndResolve = () => {
        if (
          hasMetadataLoaded &&
          (hasSpeedCalculated || actualLoadSpeed !== '未知')
        ) {
          clearTimeout(timeout);
          const width = video.videoWidth;
          if (width && width > 0) {
            hls.destroy();
            video.remove();

            // 根据视频宽度判断视频质量等级，使用经典分辨率的宽度作为分割点
            const quality =
              width >= 3840
                ? '4K' // 4K: 3840x2160
                : width >= 2560
                ? '2K' // 2K: 2560x1440
                : width >= 1920
                ? '1080p' // 1080p: 1920x1080
                : width >= 1280
                ? '720p' // 720p: 1280x720
                : width >= 854
                ? '480p'
                : 'SD'; // 480p: 854x480

            resolve({
              quality,
              loadSpeed: actualLoadSpeed,
              pingTime: Math.round(pingTime),
            });
          } else {
            // webkit 无法获取尺寸，直接返回
            resolve({
              quality: '未知',
              loadSpeed: actualLoadSpeed,
              pingTime: Math.round(pingTime),
            });
          }
        }
      };

      // 监听片段加载开始
      hls.on(Hls.Events.FRAG_LOADING, () => {
        fragmentStartTime = performance.now();
      });

      // 监听片段加载完成，只需首个分片即可计算速度
      hls.on(Hls.Events.FRAG_LOADED, (event: any, data: any) => {
        if (
          fragmentStartTime > 0 &&
          data &&
          data.payload &&
          !hasSpeedCalculated
        ) {
          const loadTime = performance.now() - fragmentStartTime;
          const size = data.payload.byteLength || 0;

          if (loadTime > 0 && size > 0) {
            const speedKBps = size / 1024 / (loadTime / 1000);

            // 立即计算速度，无需等待更多分片
            const avgSpeedKBps = speedKBps;

            if (avgSpeedKBps >= 1024) {
              actualLoadSpeed = `${(avgSpeedKBps / 1024).toFixed(1)} MB/s`;
            } else {
              actualLoadSpeed = `${avgSpeedKBps.toFixed(1)} KB/s`;
            }
            hasSpeedCalculated = true;
            checkAndResolve(); // 尝试返回结果
          }
        }
      });

      hls.loadSource(m3u8Url);
      hls.attachMedia(video);

      // 监听hls.js错误
      hls.on(Hls.Events.ERROR, (event: any, data: any) => {
        console.error('HLS错误:', data);
        if (data.fatal) {
          clearTimeout(timeout);
          hls.destroy();
          video.remove();
          reject(new Error(`HLS播放失败: ${data.type}`));
        }
      });

      // 监听视频元数据加载完成
      video.onloadedmetadata = () => {
        hasMetadataLoaded = true;
        checkAndResolve(); // 尝试返回结果
      };
    });
  } catch (error) {
    throw new Error(
      `Error getting video resolution: ${
        error instanceof Error ? error.message : String(error)
      }`
    );
  }
}

export function cleanHtmlTags(text: string): string {
  if (!text) return '';

  const cleanedText = text
    .replace(/<[^>]+>/g, '\n') // 将 HTML 标签替换为换行
    .replace(/\n+/g, '\n') // 将多个连续换行合并为一个
    .replace(/[ \t]+/g, ' ') // 将多个连续空格和制表符合并为一个空格，但保留换行符
    .replace(/^\n+|\n+$/g, '') // 去掉首尾换行
    .trim(); // 去掉首尾空格

  // 使用 he 库解码 HTML 实体
  return he.decode(cleanedText);
}

/**
 * 获取配置的超时时间（秒）
 * 从 localStorage 读取，如果不存在或无效则返回默认值3秒
 */
export function getRequestTimeout(): number {
  if (typeof window === 'undefined') {
    return 30; // 服务器端返回默认值
  }
  
  try {
    const savedTimeout = localStorage.getItem('requestTimeout');
    if (savedTimeout) {
      const timeoutSeconds = parseInt(savedTimeout, 10);
      if (!isNaN(timeoutSeconds) && timeoutSeconds >= 1 && timeoutSeconds <= 60) {
        return timeoutSeconds;
      }
    }
  } catch (error) {
    console.warn('Failed to read timeout from localStorage:', error);
  }
  
  return 30; // 默认30秒
}
