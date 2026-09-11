/* eslint-disable no-console */

import { getCacheTime } from '@/lib/config';
import { DoubanResult } from '@/lib/types';

/**
 * 豆瓣列表数据本地缓存：
 * 优先读写磁盘 JSON 文件（Node runtime，Docker/自托管场景），
 * Edge runtime（Vercel 等）无 fs 时自动退化为进程内 LRU Map，尽力而为。
 * 豆瓣上游不可用时通过 stale 数据兜底，保证列表页可用。
 */

interface DoubanCacheEntry {
  savedAt: number;
  data: DoubanResult;
}

const MEMORY_CACHE_MAX = 200;

// 进程内缓存：Node 下作为磁盘之上的读穿热点，Edge 下作为唯一存储
const memoryCache = new Map<string, DoubanCacheEntry>();
const memoryCacheOrder = new Map<string, number>();
let memoryHitSeq = 0;

function bumpMemoryOrder(key: string): void {
  memoryCacheOrder.set(key, ++memoryHitSeq);
}

function setMemoryWithCap(key: string, entry: DoubanCacheEntry): void {
  memoryCache.set(key, entry);
  bumpMemoryOrder(key);
  if (memoryCache.size > MEMORY_CACHE_MAX) {
    let oldestKey: string | null = null;
    let oldestSeq = Infinity;
    memoryCacheOrder.forEach((seq, k) => {
      if (seq < oldestSeq) {
        oldestSeq = seq;
        oldestKey = k;
      }
    });
    if (oldestKey) {
      memoryCache.delete(oldestKey);
      memoryCacheOrder.delete(oldestKey);
    }
  }
}

// ---------- 运行时可用的 Node 模块路径（fs 等），Edge 下保持 null ----------
interface NodeModules {
  fs: typeof import('fs');
  path: typeof import('path');
  crypto: typeof import('crypto');
}

let nodeModules: NodeModules | null | undefined;
let nodeModulesPromise: Promise<NodeModules | null> | null = null;

async function loadNodeModules(): Promise<NodeModules | null> {
  try {
    if (typeof process === 'undefined' || !process.versions?.node) {
      return null;
    }
    const fs = await import('node:fs');
    const path = await import('node:path');
    const crypto = await import('node:crypto');
    return { fs, path, crypto };
  } catch {
    return null;
  }
}

async function getNodeModules(): Promise<NodeModules | null> {
  if (nodeModules === undefined) {
    if (!nodeModulesPromise) {
      nodeModulesPromise = loadNodeModules();
    }
    nodeModules = await nodeModulesPromise;
  }
  return nodeModules;
}

// ---------- 配置 ----------
export function getDoubanCacheTtlMs(): number {
  const sec = Number(process.env.DOUBAN_CACHE_TTL ?? 7200);
  if (!Number.isFinite(sec) || sec < 0) return 7200000;
  return sec * 1000;
}

function getDoubanCacheDir(): string {
  return process.env.DOUBAN_CACHE_DIR || '/tmp/douban-cache';
}

const MAX_FILES = 500;

export async function buildDoubanCacheKey(
  scope: string,
  params: Record<string, string>,
): Promise<string> {
  const modules = await getNodeModules();
  const canonical = Object.keys(params)
    .sort()
    .map((k) => `${k}=${params[k]}`)
    .join('&');
  const input = `${scope}:${canonical}`;
  let hash: string;
  if (modules) {
    hash = modules.crypto
      .createHash('sha256')
      .update(input)
      .digest('hex')
      .slice(0, 16);
  } else {
    // Edge/无 crypto 时用 cyrb53（53bit，碰撞率远低于 32bit imul）
    let h1 = 0xdeadbeef;
    let h2 = 0x41c6ce57;
    for (let i = 0; i < input.length; i++) {
      const ch = input.charCodeAt(i);
      h1 = Math.imul(h1 ^ ch, 2654435761);
      h2 = Math.imul(h2 ^ ch, 1597334677);
    }
    h1 =
      Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^
      Math.imul(h2 ^ (h2 >>> 13), 3266489909);
    h2 =
      Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^
      Math.imul(h1 ^ (h1 >>> 13), 3266489909);
    hash = (4294967296 * (2097151 & h2) + (h1 >>> 0))
      .toString(16)
      .padStart(14, '0');
  }
  return `${scope}-${hash}`;
}

// ---------- 各列表接口的缓存 key 构造函数（GET 路由与暖缓存接口共用，保证 key 一致） ----------
export async function listCacheKey(params: {
  type?: string;
  tag?: string;
  pageSize?: string;
  pageStart?: string;
}): Promise<string> {
  return buildDoubanCacheKey('list', {
    type: String(params.type ?? ''),
    tag: String(params.tag ?? ''),
    pageSize: String(params.pageSize ?? ''),
    pageStart: String(params.pageStart ?? ''),
  });
}

export async function categoriesCacheKey(params: {
  kind?: string;
  category?: string;
  type?: string;
  limit?: string;
  start?: string;
}): Promise<string> {
  return buildDoubanCacheKey('categories', {
    kind: String(params.kind ?? ''),
    category: String(params.category ?? ''),
    type: String(params.type ?? ''),
    limit: String(params.limit ?? ''),
    start: String(params.start ?? ''),
  });
}

export function recommendsCacheKey(params: {
  kind?: string;
  limit?: string;
  start?: string;
  category?: string;
  format?: string;
  region?: string;
  year?: string;
  platform?: string;
  sort?: string;
  label?: string;
}): Promise<string> {
  // 与 recommends 路由的归一化保持一致：category/format/label/region/year/platform 的 'all'
  // 和 sort 的 'T' 都会归一为空
  const field = (v?: string) =>
    String(v ?? '') === 'all' ? '' : String(v ?? '');
  const sortField = (v?: string) =>
    String(v ?? '') === 'T' ? '' : String(v ?? '');
  return buildDoubanCacheKey('recommends', {
    kind: String(params.kind ?? ''),
    limit: String(params.limit ?? ''),
    start: String(params.start ?? ''),
    category: field(params.category),
    format: field(params.format),
    region: field(params.region),
    year: field(params.year),
    platform: field(params.platform),
    sort: sortField(params.sort),
    label: field(params.label),
  });
}

async function readEntryFromDisk(
  key: string,
): Promise<DoubanCacheEntry | null> {
  const modules = await getNodeModules();
  if (!modules) return null;
  try {
    const file = modules.path.join(getDoubanCacheDir(), `${key}.json`);
    const raw = modules.fs.readFileSync(file, 'utf-8');
    return JSON.parse(raw) as DoubanCacheEntry;
  } catch {
    return null;
  }
}

/**
 * 读取缓存。
 * @param key 缓存键
 * @param allowStale 豆瓣上游不可用时允许返回过期数据（兜底）
 * 过期但未要求 stale 时不删除数据，留作后续兜底路径使用。
 */
export async function getDoubanCache(
  key: string,
  allowStale = false,
): Promise<DoubanResult | null> {
  const ttl = getDoubanCacheTtlMs();
  const now = Date.now();

  // 进程内热点优先
  const mem = memoryCache.get(key);
  if (mem) {
    const fresh = now - mem.savedAt < ttl;
    if (fresh || allowStale) {
      bumpMemoryOrder(key);
      return mem.data;
    }
    return null;
  }

  const disk = await readEntryFromDisk(key);
  if (!disk) return null;
  const fresh = now - disk.savedAt < ttl;
  if (fresh) setMemoryWithCap(key, disk);
  if (fresh || allowStale) return disk.data;
  return null;
}

async function writeEntryToDisk(
  key: string,
  entry: DoubanCacheEntry,
): Promise<boolean> {
  const modules = await getNodeModules();
  if (!modules) return false;
  try {
    const dir = getDoubanCacheDir();
    modules.fs.mkdirSync(dir, { recursive: true });
    const file = modules.path.join(dir, `${key}.json`);
    const tmp = `${file}.${process.pid}.tmp`;
    modules.fs.writeFileSync(tmp, JSON.stringify(entry), 'utf-8');
    modules.fs.renameSync(tmp, file);
    return true;
  } catch (error) {
    // 磁盘不可写（只读 serverless 等）时静默失败，上层仍有内存缓存兜底
    console.warn('豆瓣缓存写入失败:', (error as Error).message);
    return false;
  }
}

/** 磁盘文件超过上限时清理（先删除过期项，过期项不足则删最旧），防止目录长期膨胀 */
async function sweepExpiredFiles(): Promise<void> {
  const modules = await getNodeModules();
  if (!modules) return;
  try {
    const dir = getDoubanCacheDir();
    const ttl = getDoubanCacheTtlMs();
    const now = Date.now();
    const files = modules.fs.readdirSync(dir);
    if (files.length <= MAX_FILES) return;
    const stale = files.filter((f) => {
      try {
        const entry = JSON.parse(
          modules.fs.readFileSync(modules.path.join(dir, f), 'utf-8'),
        ) as DoubanCacheEntry;
        return now - entry.savedAt > ttl;
      } catch {
        return true;
      }
    });
    const missing = files.length - stale.length;
    if (missing < files.length - MAX_FILES) {
      // 过期项不够补足限额，用最旧的文件补齐
      const fresh = files
        .filter((f) => !stale.includes(f))
        .map((f) => {
          try {
            return {
              f,
              mtime: modules.fs.statSync(modules.path.join(dir, f)).mtimeMs,
            };
          } catch {
            return { f, mtime: 0 };
          }
        })
        .sort((a, b) => a.mtime - b.mtime);
      stale.push(
        ...fresh
          .slice(0, files.length - MAX_FILES - stale.length)
          .map((d) => d.f),
      );
    }
    stale.slice(0, files.length - MAX_FILES).forEach((f) => {
      try {
        modules.fs.unlinkSync(modules.path.join(dir, f));
      } catch {
        // ignore
      }
    });
  } catch {
    // 目录不存在或不可用，忽略
  }
}

/** 写入缓存（内存 + 磁盘）。list 为空时不缓存，避免把上游抖动固化 */
export async function setDoubanCache(
  key: string,
  data: DoubanResult,
): Promise<void> {
  if (!data?.list || data.list.length === 0) return;
  const entry: DoubanCacheEntry = {
    savedAt: Date.now(),
    data,
  };
  setMemoryWithCap(key, entry);
  await writeEntryToDisk(key, entry);
  await sweepExpiredFiles();
}

/** 统一构造豆瓣接口的成功响应，fresh/hit/stale 走不同的 Cache-Control */
export async function buildDoubanOkResponse(
  data: DoubanResult,
  source: 'hit' | 'stale' | 'fresh',
): Promise<Response> {
  let cacheTime = 60;
  if (source !== 'stale') {
    cacheTime = await getCacheTime();
  }
  const headers = new Headers({
    'Content-Type': 'application/json',
    'x-douban-cache': source,
    'Cache-Control': `public, max-age=${cacheTime}, s-maxage=${cacheTime}`,
    'CDN-Cache-Control': `public, s-maxage=${cacheTime}`,
    'Vercel-CDN-Cache-Control': `public, s-maxage=${cacheTime}`,
    'Netlify-Vary': 'query',
  });
  return new Response(JSON.stringify(data), { status: 200, headers });
}
