jest.mock('@/lib/db', () => ({
  getStorage: jest.fn(() => null),
}));

import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'fs';
import { tmpdir } from 'os';
import { join } from 'path';

import {
  buildDoubanCacheKey,
  categoriesCacheKey,
  getDoubanCache,
  listCacheKey,
  recommendsCacheKey,
  setDoubanCache,
} from './douban-cache';

const REAL_ENV = { ...process.env };
let tmpDir = '';

function makeResult(listSize: number) {
  return {
    code: 200,
    message: '获取成功',
    list: Array.from({ length: listSize }, (_, i) => ({
      id: `10000${i}`,
      title: `测试影片${i}`,
      poster: `https://img.example.com/${i}.jpg`,
      rate: '8.5',
      year: '2024',
    })),
  };
}

describe('豆瓣缓存', () => {
  beforeEach(() => {
    tmpDir = mkdtempSync(join(tmpdir(), 'douban-cache-test-'));
    process.env.DOUBAN_CACHE_DIR = tmpDir;
    process.env.DOUBAN_CACHE_TTL = '7200';
  });

  afterEach(() => {
    process.env = { ...REAL_ENV };
    try {
      rmSync(tmpDir, { recursive: true, force: true });
    } catch {
      // ignore
    }
  });

  it('缓存 key 与参数顺序无关', () => {
    const a = buildDoubanCacheKey('list', {
      type: 'movie',
      tag: '热门',
      ps: '16',
    });
    const b = buildDoubanCacheKey('list', {
      ps: '16',
      tag: '热门',
      type: 'movie',
    });
    expect(a).toBe(b);
    expect(buildDoubanCacheKey('categories', { kind: 'tv' })).not.toBe(a);
  });

  it('各接口 key 构造函数与 buildDoubanCacheKey 一致（路由与暖缓存不漂移）', () => {
    const listParams = {
      type: 'movie',
      tag: '热门',
      pageSize: '20',
      pageStart: '0',
    };
    expect(listCacheKey(listParams)).toBe(
      buildDoubanCacheKey('list', listParams),
    );
    const catParams = {
      kind: 'tv',
      category: '剧情',
      type: 'tv',
      limit: '20',
      start: '0',
    };
    expect(categoriesCacheKey(catParams)).toBe(
      buildDoubanCacheKey('categories', catParams),
    );
    const recParams = {
      kind: 'movie',
      limit: '20',
      start: '0',
      category: 'all',
      format: 'all',
      region: 'all',
      year: 'all',
      platform: 'all',
      sort: 'T',
      label: 'all',
    };
    const norm = {
      kind: 'movie',
      limit: '20',
      start: '0',
      category: '',
      format: '',
      region: '',
      year: '',
      platform: '',
      sort: '',
      label: '',
    };
    expect(recommendsCacheKey(recParams)).toBe(
      buildDoubanCacheKey('recommends', norm),
    );
    expect(recommendsCacheKey(recParams)).toBe(recommendsCacheKey(norm));
  });

  it('set 后可从缓存读到同一数据', async () => {
    const key = buildDoubanCacheKey('list', { type: 'movie', tag: '热门' });
    const data = makeResult(3);
    await setDoubanCache(key, data);
    const got = await getDoubanCache(key);
    expect(got).toEqual(data);
  });

  it('磁盘文件被真实写入且可被读取', async () => {
    const key = buildDoubanCacheKey('list', { type: 'movie', tag: '热门' });
    const data = makeResult(2);
    await setDoubanCache(key, data);

    // 直接校验磁盘文件内容
    const raw = readFileSync(join(tmpDir, `${key}.json`), 'utf-8');
    const entry = JSON.parse(raw);
    expect(entry.data).toEqual(data);
    expect(entry.savedAt).toBeGreaterThan(0);

    // 仅磁盘命中（进程内缓存没写过的 key）也能读回
    const diskOnlyKey = buildDoubanCacheKey('list', { type: 'tv', tag: '冷' });
    writeFileSync(
      join(tmpDir, `${diskOnlyKey}.json`),
      JSON.stringify({ savedAt: Date.now(), data: data }),
    );
    const fromDisk = await getDoubanCache(diskOnlyKey);
    expect(fromDisk).toEqual(data);
  });

  it('TTL 过期后 getDoubanCache 返回 null，allowStale 时返回过期数据', async () => {
    const key = buildDoubanCacheKey('recommends', { kind: 'movie' });
    const data = makeResult(1);
    await setDoubanCache(key, data);
    process.env.DOUBAN_CACHE_TTL = '0';

    expect(await getDoubanCache(key)).toBeNull();
    expect(await getDoubanCache(key, true)).toEqual(data);
  });

  it('空列表不写入缓存，避免固化上游抖动', async () => {
    const key = buildDoubanCacheKey('categories', { kind: 'movie' });
    await setDoubanCache(key, { code: 200, message: '获取成功', list: [] });
    expect(await getDoubanCache(key)).toBeNull();
  });
});
