/* eslint-disable @typescript-eslint/no-explicit-any */

import { NextResponse } from 'next/server';

import {
  categoriesCacheKey,
  listCacheKey,
  recommendsCacheKey,
  setDoubanCache,
} from '@/lib/douban-cache';
import { DoubanResult } from '@/lib/types';

/**
 * 客户端暖缓存接口：代理/CDN/custom 源成功抓取豆瓣数据后，把结果回写本地缓存，
 * 确保即使服务端从未直接请求过豆瓣源站，客户端所选源故障时也能用缓存兜底。
 * 鉴权由中间件统一处理（/api/douban/cache 不在跳过认证的路径列表中）。
 */

export const runtime = 'edge';

const MAX_BODY_BYTES = 256 * 1024;

function sanitizeItem(item: any) {
  return {
    id: String(item?.id ?? '').slice(0, 64),
    title: String(item?.title ?? '').slice(0, 300),
    poster: String(item?.poster ?? '').slice(0, 1000),
    rate: String(item?.rate ?? '').slice(0, 16),
    year: String(item?.year ?? '').slice(0, 16),
  };
}

export async function POST(request: Request) {
  const contentLength = Number(request.headers.get('content-length') || 0);
  if (contentLength > MAX_BODY_BYTES) {
    return NextResponse.json({ error: '请求体过大' }, { status: 413 });
  }

  let body: any;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: 'JSON body 无效' }, { status: 400 });
  }

  const { scope, params = {}, data } = body ?? {};
  if (
    !params ||
    typeof params !== 'object' ||
    !data ||
    !Array.isArray(data.list)
  ) {
    return NextResponse.json({ error: '参数不完整' }, { status: 400 });
  }

  const p = (k: string) => String((params as Record<string, string>)[k] ?? '');

  let key: string;
  if (scope === 'list') {
    key = listCacheKey({
      type: p('type'),
      tag: p('tag'),
      pageSize: p('pageSize'),
      pageStart: p('pageStart'),
    });
  } else if (scope === 'categories') {
    key = categoriesCacheKey({
      kind: p('kind'),
      category: p('category'),
      type: p('type'),
      limit: p('limit'),
      start: p('start'),
    });
  } else if (scope === 'recommends') {
    key = recommendsCacheKey({
      kind: p('kind'),
      limit: p('limit'),
      start: p('start'),
      category: p('category'),
      format: p('format'),
      region: p('region'),
      year: p('year'),
      platform: p('platform'),
      sort: p('sort'),
      label: p('label'),
    });
  } else {
    return NextResponse.json({ error: '非法 scope' }, { status: 400 });
  }

  const list = Array.isArray(data.list)
    ? data.list.slice(0, 100).map(sanitizeItem)
    : [];
  if (list.length === 0) {
    return NextResponse.json({ ok: true });
  }

  const result: DoubanResult = {
    code: 200,
    message: '获取成功',
    list,
  };
  await setDoubanCache(key, result);
  return NextResponse.json({ ok: true });
}