import { NextResponse } from 'next/server';

import { fetchDoubanData } from '@/lib/douban';
import {
  buildDoubanOkResponse,
  categoriesCacheKey,
  getDoubanCache,
  setDoubanCache,
} from '@/lib/douban-cache';
import { DoubanItem, DoubanResult } from '@/lib/types';

interface DoubanCategoryApiResponse {
  total: number;
  items: Array<{
    id: string;
    title: string;
    card_subtitle: string;
    pic: {
      large: string;
      normal: string;
    };
    rating: {
      value: number;
    };
  }>;
}

export const runtime = 'edge';

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);

  // 获取参数
  const kind = searchParams.get('kind') || 'movie';
  const category = searchParams.get('category');
  const type = searchParams.get('type');
  const pageLimit = parseInt(searchParams.get('limit') || '20');
  const pageStart = parseInt(searchParams.get('start') || '0');

  // 验证参数
  if (!kind || !category || !type) {
    return NextResponse.json(
      { error: '缺少必要参数: kind 或 category 或 type' },
      { status: 400 },
    );
  }

  if (!['tv', 'movie'].includes(kind)) {
    return NextResponse.json(
      { error: 'kind 参数必须是 tv 或 movie' },
      { status: 400 },
    );
  }

  if (pageLimit < 1 || pageLimit > 100) {
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

  const cacheKey = categoriesCacheKey({
    kind,
    category,
    type,
    limit: pageLimit.toString(),
    start: pageStart.toString(),
  });

  // 先读缓存，命中直接返回
  const cached = await getDoubanCache(cacheKey);
  if (cached) {
    return buildDoubanOkResponse(cached, 'hit');
  }

  const target = `https://m.douban.com/rexxar/api/v2/subject/recent_hot/${kind}?start=${pageStart}&limit=${pageLimit}&category=${category}&type=${type}`;

  try {
    // 调用豆瓣 API
    const doubanData = await fetchDoubanData<DoubanCategoryApiResponse>(target);

    // 转换数据格式
    const list: DoubanItem[] = doubanData.items.map((item) => ({
      id: item.id,
      title: item.title,
      poster: item.pic?.normal || item.pic?.large || '',
      rate: item.rating?.value ? item.rating.value.toFixed(1) : '',
      year: item.card_subtitle?.match(/(\d{4})/)?.[1] || '',
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
