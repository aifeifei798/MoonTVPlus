import { NextResponse } from 'next/server';

import { fetchDoubanData } from '@/lib/douban';
import {
  buildDoubanOkResponse,
  getDoubanCache,
  listCacheKey,
  setDoubanCache,
} from '@/lib/douban-cache';
import { DoubanItem, DoubanResult } from '@/lib/types';

interface DoubanApiResponse {
  subjects: Array<{
    id: string;
    title: string;
    cover: string;
    rate: string;
  }>;
}

export const runtime = 'edge';

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);

  // 获取参数
  const type = searchParams.get('type');
  const tag = searchParams.get('tag');
  const pageSize = parseInt(searchParams.get('pageSize') || '16');
  const pageStart = parseInt(searchParams.get('pageStart') || '0');

  // 验证参数
  if (!type || !tag) {
    return NextResponse.json(
      { error: '缺少必要参数: type 或 tag' },
      { status: 400 },
    );
  }

  if (!['tv', 'movie'].includes(type)) {
    return NextResponse.json(
      { error: 'type 参数必须是 tv 或 movie' },
      { status: 400 },
    );
  }

  if (pageSize < 1 || pageSize > 100) {
    return NextResponse.json(
      { error: 'pageSize 必须在 1-100 之间' },
      { status: 400 },
    );
  }

  if (pageStart < 0) {
    return NextResponse.json(
      { error: 'pageStart 不能小于 0' },
      { status: 400 },
    );
  }

  const cacheKey = await listCacheKey({
    type,
    tag,
    pageSize: pageSize.toString(),
    pageStart: pageStart.toString(),
  });

  // 先读缓存，命中直接返回
  const cached = await getDoubanCache(cacheKey);
  if (cached) {
    return buildDoubanOkResponse(cached, 'hit');
  }

  if (tag === 'top250') {
    return handleTop250(pageStart, cacheKey);
  }

  const target = `https://movie.douban.com/j/search_subjects?type=${type}&tag=${tag}&sort=recommend&page_limit=${pageSize}&page_start=${pageStart}`;

  try {
    // 调用豆瓣 API
    const doubanData = await fetchDoubanData<DoubanApiResponse>(target);

    // 转换数据格式
    const list: DoubanItem[] = doubanData.subjects.map((item) => ({
      id: item.id,
      title: item.title,
      poster: item.cover,
      rate: item.rate,
      year: '',
    }));

    const response: DoubanResult = {
      code: 200,
      message: '获取成功',
      list: list,
    };

    await setDoubanCache(cacheKey, response);
    return buildDoubanOkResponse(response, 'fresh');
  } catch (error) {
    // 豆瓣不可用时退回缓存（允许过期数据兜底）
    const stale = await getDoubanCache(cacheKey, true);
    if (stale) {
      return buildDoubanOkResponse(stale, 'stale');
    }
    return NextResponse.json(
      { error: '获取豆瓣数据失败', details: (error as Error).message },
      { status: 500 },
    );
  }
}

async function handleTop250(pageStart: number, cacheKey: string) {
  const target = `https://movie.douban.com/top250?start=${pageStart}&filter=`;

  // 直接使用 fetch 获取 HTML 页面
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 10000);

  const fetchOptions = {
    signal: controller.signal,
    headers: {
      'User-Agent':
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
      Referer: 'https://movie.douban.com/',
      Accept:
        'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    },
  };

  try {
    const fetchResponse = await fetch(target, fetchOptions);
    clearTimeout(timeoutId);

    if (!fetchResponse.ok) {
      throw new Error(`HTTP error! Status: ${fetchResponse.status}`);
    }

    // 获取 HTML 内容
    const html = await fetchResponse.text();

    // 通过正则同时捕获影片 id、标题、封面以及评分
    const moviePattern =
      /<div class="item">[\s\S]*?<a[^>]+href="https?:\/\/movie\.douban\.com\/subject\/(\d+)\/"[\s\S]*?<img[^>]+alt="([^"]+)"[^>]*src="([^"]+)"[\s\S]*?<span class="rating_num"[^>]*>([^<]*)<\/span>[\s\S]*?<\/div>/g;
    const movies: DoubanItem[] = [];
    let match;

    while ((match = moviePattern.exec(html)) !== null) {
      const id = match[1];
      const title = match[2];
      const cover = match[3];
      const rate = match[4] || '';

      // 处理图片 URL，确保使用 HTTPS
      const processedCover = cover.replace(/^http:/, 'https:');

      movies.push({
        id: id,
        title: title,
        poster: processedCover,
        rate: rate,
        year: '',
      });
    }

    const apiResponse: DoubanResult = {
      code: 200,
      message: '获取成功',
      list: movies,
    };

    await setDoubanCache(cacheKey, apiResponse);
    return buildDoubanOkResponse(apiResponse, 'fresh');
  } catch (error) {
    clearTimeout(timeoutId);
    // 豆瓣不可用时退回缓存（允许过期数据兜底）
    const stale = await getDoubanCache(cacheKey, true);
    if (stale) {
      return buildDoubanOkResponse(stale, 'stale');
    }
    return NextResponse.json(
      {
        error: '获取豆瓣 Top250 数据失败',
        details: (error as Error).message,
      },
      { status: 500 },
    );
  }
}
