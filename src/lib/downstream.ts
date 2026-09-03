import { API_CONFIG, ApiSite, getCacheTime, getConfig } from '@/lib/config';
import { SearchResult } from '@/lib/types';
import { cleanHtmlTags } from '@/lib/utils';

// ---------- 进程内热点缓存（serverless 多实例下尽力而为） ----------
// 搜索：key=`${site}:${maxPages}:${query}`，默认 10 分钟；同一词重复搜不再打上游
const searchCache = new Map<
  string,
  { at: number; batches: SearchResult[][] }
>();
const SEARCH_CACHE_MAX = 200;

function getSearchCacheTtlMs(): number {
  const sec = Number(process.env.SEARCH_CACHE_TTL || 600);
  if (!Number.isFinite(sec) || sec < 0) return 600000;
  return sec * 1000;
}

// 详情：key=`${site}:${id}`，TTL 取站点接口缓存时间，默认上限 500 条
const detailCache = new Map<string, { at: number; data: SearchResult }>();
const DETAIL_CACHE_MAX = 500;

function setWithCap<K, V>(map: Map<K, V>, key: K, value: V, max: number): void {
  if (map.has(key)) map.delete(key);
  else if (map.size >= max) {
    const oldest = map.keys().next();
    if (!oldest.done) map.delete(oldest.value);
  }
  map.set(key, value);
}

// ---------- 源健康熔断（进程内，serverless 下尽力而为） ----------
// 连续失败达阈值的源会被跳过一个冷却期，避免每次搜索都被慢源拖到超时；
// 跳过信息通过各搜索接口既有的 failedSources/source_error 透出，前端失败源面板直接可见。
const sourceHealth = new Map<string, { fails: number; openedAt: number }>();

function getCircuitThreshold(): number {
  const n = Number(process.env.SOURCE_CIRCUIT_THRESHOLD ?? 5);
  if (!Number.isFinite(n) || n < 0) return 5;
  return Math.floor(n);
}

function getCircuitCooldownMs(): number {
  const n = Number(process.env.SOURCE_CIRCUIT_COOLDOWN_S ?? 300);
  if (!Number.isFinite(n) || n < 0) return 300000;
  return n * 1000;
}

/** 熔断器是否打开（打开=本轮跳过该源）；冷却期过后半开，允许试探一次 */
export function isSourceCircuitOpen(key: string): boolean {
  if (getCircuitThreshold() === 0) return false;
  const entry = sourceHealth.get(key);
  if (!entry || entry.fails < getCircuitThreshold()) return false;
  if (Date.now() - entry.openedAt > getCircuitCooldownMs()) {
    sourceHealth.delete(key);
    return false;
  }
  return true;
}

export function recordSourceSuccess(key: string): void {
  if (sourceHealth.has(key)) sourceHealth.delete(key);
}

export function recordSourceFailure(key: string): void {
  const threshold = getCircuitThreshold();
  if (threshold === 0) return;
  const entry = sourceHealth.get(key) || { fails: 0, openedAt: 0 };
  entry.fails++;
  if (entry.fails >= threshold && !entry.openedAt) {
    entry.openedAt = Date.now();
    // eslint-disable-next-line no-console
    console.warn(
      `源熔断: ${key} 连续失败 ${entry.fails} 次，冷却 ${
        getCircuitCooldownMs() / 1000
      }s`
    );
  }
  sourceHealth.set(key, entry);
}

interface ApiSearchItem {
  vod_id: string;
  vod_name: string;
  vod_pic: string;
  vod_remarks?: string;
  vod_play_url?: string;
  vod_class?: string;
  vod_year?: string;
  vod_content?: string;
  vod_douban_id?: number;
  type_name?: string;
}

// 匹配 m3u8 链接的正则
const M3U8_PATTERN = /(https?:\/\/[^"'\s]+?\.m3u8)/g;

/** 封装带超时的 fetch，区分超时和网络错误 */
async function fetchWithTimeout(
  url: string,
  options: RequestInit,
  timeout = 30000
): Promise<Response> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeout);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } catch (error: unknown) {
    // 区分超时错误和网络错误
    const err = error as Error;
    if (err.name === 'AbortError') {
      throw new Error('请求超时');
    } else if (
      err.message?.includes('Failed to fetch') ||
      err.message?.includes('fetch failed') ||
      err.message?.includes('NetworkError')
    ) {
      throw new Error('请求失败');
    } else {
      throw new Error(`网络错误: ${err.message || '未知错误'}`);
    }
  } finally {
    clearTimeout(timeoutId);
  }
}

/**
 * 通用的播放源解析
 * 支持：
 *  1. vod_play_url (通过 $$$、#、$ 分割)
 *  2. 内容中的 m3u8 链接（正则提取）
 */
function parseEpisodes(
  vod_play_url?: string,
  fallbackContent?: string
): { episodes: string[]; titles: string[] } {
  let episodes: string[] = [];
  let titles: string[] = [];

  // 1. 优先解析 vod_play_url
  if (vod_play_url) {
    const sources = vod_play_url.split('$$$');
    sources.forEach((source) => {
      const currentEpisodes: string[] = [];
      const currentTitles: string[] = [];

      source.split('#').forEach((entry) => {
        const [title, url] = entry.split('$');
        if (url?.endsWith('.m3u8')) {
          currentTitles.push(title);
          currentEpisodes.push(url);
        }
      });

      // 选用分集最多的播放源
      if (currentEpisodes.length > episodes.length) {
        episodes = currentEpisodes;
        titles = currentTitles;
      }
    });
  }

  // 2. 如果没有解析到，尝试 fallback 内容
  if (episodes.length === 0 && fallbackContent) {
    episodes = (fallbackContent.match(M3U8_PATTERN) ?? []).map((link: string) =>
      link.replace(/^\$/, '')
    );
    titles = episodes.map((_, i) => (i + 1).toString()); // 默认用序号作为标题
  }

  return { episodes, titles };
}

/** 映射 API 数据到 SearchResult */
function mapItemToResult(
  item: ApiSearchItem,
  apiSite: ApiSite,
  apiName: string
): SearchResult {
  const { episodes, titles } = parseEpisodes(
    item.vod_play_url,
    item.vod_content
  );

  return {
    id: item.vod_id.toString(),
    title: item.vod_name.trim().replace(/\s+/g, ' '),
    poster: item.vod_pic,
    episodes,
    episodes_titles: titles,
    source: apiSite.key,
    source_name: apiName,
    class: item.vod_class,
    year: item.vod_year?.match(/\d{4}/)?.[0] || 'unknown',
    desc: cleanHtmlTags(item.vod_content || ''),
    type_name: item.type_name,
    douban_id: item.vod_douban_id,
  };
}

/** API 搜索流（maxPages 不传时回退读配置；调用方已有配置请直接传入，避免每源一次读库） */
export async function* searchFromApiStream(
  apiSite: ApiSite,
  query: string,
  parallel = true,
  timeout?: number,
  maxPages?: number
): AsyncGenerator<SearchResult[], void, unknown> {
  // 分页上限先确定（调用方透传则零读库），缓存 key 与之绑定
  const resolvedMaxPages =
    maxPages ?? (await getConfig()).SiteConfig.SearchDownstreamMaxPage;
  const maxPagesNum = resolvedMaxPages;
  const cacheKey = `s:${apiSite.key}:${maxPagesNum}:${query}`;
  const cached = searchCache.get(cacheKey);
  if (cached && Date.now() - cached.at < getSearchCacheTtlMs()) {
    // 命中则刷新 LRU 顺序后直接回放
    searchCache.delete(cacheKey);
    searchCache.set(cacheKey, cached);
    for (const batch of cached.batches) {
      yield batch;
    }
    return;
  }

  const collected: SearchResult[][] = [];
  let fullyConsumed = false;
  try {
    const apiUrl =
      apiSite.api + API_CONFIG.search.path + encodeURIComponent(query);

    const response = await fetchWithTimeout(
      apiUrl,
      { headers: API_CONFIG.search.headers },
      timeout
    );
    if (!response.ok) return;

    const data = await response.json();
    if (!Array.isArray(data?.list)) return;

    // 第一页
    const firstBatch = data.list.map((item: ApiSearchItem) =>
      mapItemToResult(item, apiSite, apiSite.name)
    );
    collected.push(firstBatch);
    yield firstBatch;

    // 分页
    const pageCount = data.pagecount || 1;
    const pagesToFetch = Math.min(pageCount, maxPagesNum);

    if (pagesToFetch > 1) {
      if (parallel) {
        // ------------------ 并行模式 ------------------
        const pagePromises: Promise<{
          page: number;
          results: SearchResult[];
        } | null>[] = [];

        for (let page = 2; page <= pagesToFetch; page++) {
          const pageUrl =
            apiSite.api +
            API_CONFIG.search.pagePath
              .replace('{query}', encodeURIComponent(query))
              .replace('{page}', page.toString());

          const promise = (async () => {
            const pageRes = await fetchWithTimeout(
              pageUrl,
              { headers: API_CONFIG.search.headers },
              timeout
            );
            if (!pageRes.ok) return null;

            const pageData = await pageRes.json();
            if (!Array.isArray(pageData?.list)) return null;

            const results = pageData.list.map((item: ApiSearchItem) =>
              mapItemToResult(item, apiSite, apiSite.name)
            );
            return { page, results };
          })();

          pagePromises.push(promise);
        }

        const settled = await Promise.all(pagePromises);
        for (const res of settled
          .filter(
            (r): r is { page: number; results: SearchResult[] } =>
              !!r && r.results.length > 0
          )
          .sort((a, b) => a.page - b.page)) {
          collected.push(res.results);
          yield res.results;
        }
      } else {
        // ------------------ 顺序模式 ------------------
        for (let page = 2; page <= pagesToFetch; page++) {
          const pageUrl =
            apiSite.api +
            API_CONFIG.search.pagePath
              .replace('{query}', encodeURIComponent(query))
              .replace('{page}', page.toString());

          const pageRes = await fetchWithTimeout(
            pageUrl,
            { headers: API_CONFIG.search.headers },
            timeout
          );
          if (!pageRes.ok) continue;

          const pageData = await pageRes.json();
          if (Array.isArray(pageData?.list)) {
            const results = pageData.list.map((item: ApiSearchItem) =>
              mapItemToResult(item, apiSite, apiSite.name)
            );
            if (results.length > 0) {
              collected.push(results);
              yield results;
            }
          }
        }
      }
    }

    fullyConsumed = true;
  } finally {
    // 仅完整消费才缓存：提前 break（如精确命中）时半截结果不进缓存；
    // 空结果多为上游抖动，同样不缓存避免把空洞固化
    if (fullyConsumed && collected.length > 0) {
      setWithCap(
        searchCache,
        cacheKey,
        { at: Date.now(), batches: collected },
        SEARCH_CACHE_MAX
      );
    }
  }
}

/** 获取详情（进程内 LRU，TTL 取站点接口缓存时间；追更/cron/播放页反复拉同一批剧集时省上游请求） */
export async function getDetailFromApi(
  apiSite: ApiSite,
  id: string
): Promise<SearchResult> {
  const cacheKey = `d:${apiSite.key}:${id}`;
  const cached = detailCache.get(cacheKey);
  const ttlMs = (await getCacheTime()) * 1000;
  if (cached && Date.now() - cached.at < ttlMs) {
    detailCache.delete(cacheKey);
    detailCache.set(cacheKey, cached);
    return cached.data;
  }

  const fresh = apiSite.detail
    ? await handleSpecialSourceDetail(id, apiSite)
    : await fetchDetailFromApi(apiSite, id);
  setWithCap(
    detailCache,
    cacheKey,
    { at: Date.now(), data: fresh },
    DETAIL_CACHE_MAX
  );
  return fresh;
}

async function fetchDetailFromApi(
  apiSite: ApiSite,
  id: string
): Promise<SearchResult> {
  const detailUrl = `${apiSite.api}${API_CONFIG.detail.path}${id}`;
  const response = await fetchWithTimeout(detailUrl, {
    headers: API_CONFIG.detail.headers,
  });

  if (!response.ok) throw new Error(`详情请求失败: ${response.status}`);

  const data = await response.json();
  if (!Array.isArray(data?.list) || data.list.length === 0) {
    throw new Error('获取到的详情内容无效');
  }

  const video = data.list[0];
  const { episodes, titles } = parseEpisodes(
    video.vod_play_url,
    video.vod_content
  );

  return {
    id: id.toString(),
    title: video.vod_name,
    poster: video.vod_pic,
    episodes,
    episodes_titles: titles,
    source: apiSite.key,
    source_name: apiSite.name,
    class: video.vod_class,
    year: video.vod_year?.match(/\d{4}/)?.[0] || 'unknown',
    desc: cleanHtmlTags(video.vod_content),
    type_name: video.type_name,
    douban_id: video.vod_douban_id,
  };
}

/** 特殊站点详情处理 */
async function handleSpecialSourceDetail(
  id: string,
  apiSite: ApiSite
): Promise<SearchResult> {
  const detailUrl = `${apiSite.detail}/index.php/vod/detail/id/${id}.html`;
  const response = await fetchWithTimeout(detailUrl, {
    headers: API_CONFIG.detail.headers,
  });

  if (!response.ok) throw new Error(`详情页请求失败: ${response.status}`);

  const html = await response.text();

  // 特定站点规则（优先）
  let matches: string[] = [];
  if (apiSite.key === 'ffzy') {
    matches =
      html.match(
        /\$(https?:\/\/[^"'\s]+?\/\d{8}\/\d+_[a-f0-9]+\/index\.m3u8)/g
      ) || [];
  }

  // 通用正则
  if (matches.length === 0) {
    matches = html.match(/\$(https?:\/\/[^"'\s]+?\.m3u8)/g) || [];
  }

  // 去重并清理
  matches = Array.from(new Set(matches)).map((link) => {
    const clean = link.substring(1); // 去掉 $
    const parenIndex = clean.indexOf('(');
    return parenIndex > 0 ? clean.substring(0, parenIndex) : clean;
  });

  // 如果依旧没解析到，用 parseEpisodes fallback
  if (matches.length === 0) {
    const { episodes } = parseEpisodes(undefined, html);
    matches = episodes;
  }

  const episodes_titles = matches.map((_, i) => (i + 1).toString());

  const title = html.match(/<h1[^>]*>([^<]+)<\/h1>/)?.[1]?.trim() || '';
  const desc = cleanHtmlTags(
    html.match(/<div[^>]*class=["']sketch["'][^>]*>([\s\S]*?)<\/div>/)?.[1] ||
      ''
  );
  const cover = html.match(/(https?:\/\/[^"'\s]+?\.jpg)/)?.[0]?.trim() || '';
  const year = html.match(/>(\d{4})</)?.[1] || 'unknown';

  return {
    id,
    title,
    poster: cover,
    episodes: matches,
    episodes_titles,
    source: apiSite.key,
    source_name: apiSite.name,
    class: '',
    year,
    desc,
    type_name: '',
    douban_id: 0,
  };
}
