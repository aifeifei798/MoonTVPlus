/* eslint-disable @typescript-eslint/no-explicit-any,no-console */

import { searchDouban } from './douban.client';

/**
 * 记录卡片的海报兜底图：站点图标（`frontend/scripts/make-logo.py` 生成的 2:3
 * 版本）。豆瓣拿不到、源也拿不到时，卡片显示它而不是空白灰框。
 */
export const FALLBACK_POSTER = '/poster-fallback.svg';

const CACHE_KEY = 'moontv_douban_poster';
const CACHE_TTL_MS = 7 * 24 * 60 * 60 * 1000; // 海报几乎不变，一周足够
const CACHE_MAX = 500;
const CONCURRENCY = 4; // 同一页几十条记录同时搜索会把豆瓣限流

type Entry = { poster: string; at: number };

const memory = new Map<string, Entry>();
const inFlight = new Map<string, Promise<string>>();
let running = 0;
const queue: Array<() => void> = [];

function normalize(title: string): string {
  return (title || '')
    .toLowerCase()
    .replace(/[\s_·．.,\-–—:：!?！'"“”()（）\[\]【]]/g, '')
    .trim();
}

function yearOf(title: string): string {
  const match = title.match(/(\d{4})/);
  return match ? match[1] : '';
}

/**
 * 豆瓣条目与记录标题的匹配分数：完全一致 3，前缀 2，包含 1，年份一致 +1，
 * 年份冲突 -2。低于 2 的候选不算命中——源站标题常带"HD/更新至X集"之类的噪声，
 * 只靠"包含"会挑到另一部片子。
 */
function score(candidate: any, title: string, year: string): number {
  const wanted = normalize(title);
  const found = normalize(candidate.title || '');
  if (!wanted || !found) return 0;

  let value = 0;
  if (wanted === found) value = 3;
  else if (found.startsWith(wanted) || wanted.startsWith(found)) value = 2;
  else if (found.includes(wanted) || wanted.includes(found)) value = 1;

  const foundYear = candidate.year || yearOf(candidate.title || '');
  if (year && foundYear) value += foundYear === year ? 1 : -2;
  return value;
}

// The cache is parsed once and kept in memory: a page of 30 record cards used to
// `JSON.parse` the same 500-entry blob 30 times, and every store re-parsed it too.
let diskCache: Record<string, Entry> | null = null;

function readDiskCache(): Record<string, Entry> {
  if (diskCache !== null) return diskCache;
  try {
    const raw = localStorage.getItem(CACHE_KEY);
    if (!raw) {
      diskCache = {};
      return diskCache;
    }
    const parsed = JSON.parse(raw) as Record<string, Entry>;
    diskCache = parsed && typeof parsed === 'object' ? parsed : {};
    return diskCache;
  } catch {
    diskCache = {};
    return diskCache;
  }
}

function writeDiskCache(cache: Record<string, Entry>) {
  const keys = Object.keys(cache);
  if (keys.length <= CACHE_MAX) {
    diskCache = cache;
    localStorage.setItem(CACHE_KEY, JSON.stringify(cache));
    return;
  }
  // 只保留最近写入的 CACHE_MAX 条，避免 localStorage 无限增长。
  const kept = keys
    .sort((a, b) => (cache[b]?.at || 0) - (cache[a]?.at || 0))
    .slice(0, CACHE_MAX);
  const trimmed: Record<string, Entry> = {};
  for (const key of kept) trimmed[key] = cache[key];
  diskCache = trimmed;
  localStorage.setItem(CACHE_KEY, JSON.stringify(trimmed));
}

function store(key: string, poster: string) {
  const entry = { poster, at: Date.now() };
  memory.set(key, entry);
  const cache = readDiskCache();
  cache[key] = entry;
  writeDiskCache(cache);
}

function acquire(): Promise<void> {
  if (running < CONCURRENCY) {
    running += 1;
    return Promise.resolve();
  }
  return new Promise<void>((resolve) => {
    queue.push(() => {
      running += 1;
      resolve();
    });
  });
}

function release() {
  running -= 1;
  const next = queue.shift();
  if (next) next();
}

/**
 * 豆瓣海报优先：记录卡片（继续观看 / 追更 / 收藏）的海报来自源站，豆瓣的同名
 * 海报通常更稳定也更标准。查不到返回空字符串，调用方退回源海报。
 *
 * 结果（含"没有命中"）都缓存，所以同一标题只搜索一次；缓存存 localStorage，
 * 换页面也不重复请求。
 */
export async function resolveDoubanPoster(
  title: string,
  year?: string
): Promise<string> {
  if (!title) return '';

  const key = `${normalize(title)}|${year || ''}`;
  const hit = memory.get(key);
  if (hit) return hit.poster;

  const disk = readDiskCache()[key];
  if (disk && Date.now() - (disk.at || 0) < CACHE_TTL_MS) {
    memory.set(key, disk);
    return disk.poster;
  }

  const pending = inFlight.get(key);
  if (pending) return pending;

  const promise = (async () => {
    await acquire();
    try {
      const result = await searchDouban(title);
      let best = '';
      let bestScore = 1; // 低于 2 的候选不算命中
      for (const item of result.list || []) {
        const value = score(item, title, year || '');
        if (value > bestScore && item.poster) {
          bestScore = value;
          best = item.poster;
        }
      }
      store(key, best);
      return best;
    } catch (error) {
      // 豆瓣不可达时不缓存失败：下次渲染还能再试。
      return '';
    } finally {
      release();
      inFlight.delete(key);
    }
  })();

  inFlight.set(key, promise);
  return promise;
}
