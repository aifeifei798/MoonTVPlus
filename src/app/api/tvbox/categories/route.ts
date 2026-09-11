/* eslint-disable @typescript-eslint/no-explicit-any */

import { NextResponse } from 'next/server';

import { getCacheTime, getConfig } from '@/lib/config';
import { fetchDoubanData } from '@/lib/douban';
import {
  getDoubanCache,
  recommendsCacheKey,
  setDoubanCache,
} from '@/lib/douban-cache';

export const runtime = 'edge';

// TVBox 内调豆瓣推荐：不再经 HTTP 回环（避免 Host 投毒 SSRF），直接复用推荐链路
async function fetchRecommendsDirect(params: {
  kind: 'movie' | 'tv';
  category: string;
  label: string;
  year: string;
  sort: string;
  start: number;
  limit: number;
}): Promise<any[]> {
  const { kind, category, label, year, sort, start, limit } = params as {
    kind: 'movie' | 'tv';
    category: string;
    label: string;
    year: string;
    sort: string;
    start: number;
    limit: number;
  };
  const cacheKey = await recommendsCacheKey({
    kind,
    limit: String(limit),
    start: String(start),
    category,
    format: '',
    region: '',
    year,
    platform: '',
    sort,
    label,
  });
  const cached = await getDoubanCache(cacheKey);
  if (cached && Array.isArray((cached as any).list))
    return (cached as any).list;

  const selectedCategories = { 类型: category } as Record<string, string>;
  const tags: string[] = [];
  if (category) tags.push(category);
  if (label) tags.push(label);
  if (year) tags.push(year);

  const baseUrl = `https://m.douban.com/rexxar/api/v2/${kind}/recommend`;
  const qs = new URLSearchParams();
  qs.append('refresh', '0');
  qs.append('start', String(start));
  qs.append('count', String(limit));
  qs.append('selected_categories', JSON.stringify(selectedCategories));
  qs.append('uncollect', 'false');
  qs.append('score_range', '0,10');
  qs.append('tags', tags.join(','));
  if (sort) qs.append('sort', sort);
  const target = `${baseUrl}?${qs.toString()}`;
  try {
    const data = await fetchDoubanData<{
      items: Array<{
        id: string;
        title: string;
        year: string;
        type: string;
        pic: { large: string; normal: string };
        rating: { value: number };
      }>;
    }>(target);
    const list = (data.items || [])
      .filter((item) => item.type === 'movie' || item.type === 'tv')
      .map((item) => ({
        id: item.id,
        title: item.title,
        poster: item.pic?.normal || item.pic?.large || '',
        rate: item.rating?.value ? item.rating.value.toFixed(1) : '',
        year: item.year,
      }));
    await setDoubanCache(cacheKey, {
      code: 200,
      message: '获取成功',
      list,
    });
    return list;
  } catch {
    const stale = await getDoubanCache(cacheKey, true);
    if (stale && Array.isArray((stale as any).list)) return (stale as any).list;
    return [];
  }
}

export async function GET(request: Request) {
  const url = new URL(request.url);
  const inputPassword =
    url.searchParams.get('pwd') || url.searchParams.get('password') || '';

  const adminConfig = await getConfig();
  const storageType = process.env.NEXT_PUBLIC_STORAGE_TYPE || 'localstorage';
  const enabled =
    storageType === 'localstorage'
      ? process.env.TVBOX_ENABLED == null
        ? true
        : String(process.env.TVBOX_ENABLED).toLowerCase() === 'true'
      : adminConfig.SiteConfig.TVBoxEnabled === true;

  if (!enabled) {
    return NextResponse.json({ error: 'TVBox 接口未开启' }, { status: 403 });
  }

  const expectedPassword =
    storageType === 'localstorage'
      ? process.env.PASSWORD || ''
      : adminConfig.SiteConfig.TVBoxPassword || '';
  // 未配置口令时拒绝匿名访问；已配置则必须显式携带正确口令（不再自动填充）
  if (!expectedPassword || inputPassword !== expectedPassword) {
    return NextResponse.json({ error: '密码错误或未提供' }, { status: 401 });
  }

  try {
    const [cfg, cacheTime] = await Promise.all([getConfig(), getCacheTime()]);

    // 豆瓣默认分类（来源于 README 可用分类）
    const doubanDefaults = {
      movie: ['热门', '最新', '经典', '豆瓣高分'],
      tv: ['热门', '美剧', '英剧', '韩剧', '日剧', '国产剧', '日本动画'],
    };

    // 用户自定义分类（从配置获取）
    const custom = (cfg.CustomCategories || []).map((c) => ({
      name: c.name || c.query,
      type: c.type,
      query: c.query,
    }));

    // Apple CMS 类似分类返回（参考 provide/vod 的分类结构）
    const classes: { type_id: number; type_name: string }[] = [];
    let nextId = 1;

    doubanDefaults.movie.forEach((name) => {
      classes.push({ type_id: nextId++, type_name: `电影·${name}` });
    });
    doubanDefaults.tv.forEach((name) => {
      classes.push({ type_id: nextId++, type_name: `剧集·${name}` });
    });
    custom.forEach((c) => {
      classes.push({ type_id: nextId++, type_name: `${c.name}` });
    });

    // 分页参数：t（分类 id），pg（页码，默认1），wd（关键字）
    const tParam = Number(url.searchParams.get('t') || '');
    const wdParam = url.searchParams.get('wd') || '';
    const pgParam = Math.max(1, parseInt(url.searchParams.get('pg') || '1'));
    const pageSize = Math.max(
      1,
      Math.min(50, parseInt(url.searchParams.get('pagesize') || '20')),
    );

    if (tParam || wdParam) {
      // 重建与 classes 相同顺序的选择器映射
      const selectors: Array<{
        kind: 'movie' | 'tv';
        category?: string;
        label?: string;
      }> = [];
      doubanDefaults.movie.forEach((name) =>
        selectors.push({ kind: 'movie', category: name }),
      );
      doubanDefaults.tv.forEach((name) =>
        selectors.push({ kind: 'tv', category: name }),
      );
      custom.forEach((c) => selectors.push({ kind: c.type, label: c.query }));

      let kind: 'movie' | 'tv' = 'movie';
      let category = '';
      let label = '';
      let sort = '';
      if (tParam && tParam >= 1 && tParam <= selectors.length) {
        const sel = selectors[tParam - 1];
        kind = sel.kind;
        category = sel.category || '';
        label = sel.label || '';
      }
      if (wdParam) {
        label = wdParam;
      }

      const origin = url.origin;
      void origin;
      const qs = new URLSearchParams();
      qs.set('kind', kind);
      // 处理“热门/最新”无数据的问题：
      // - 热门：不传 category/label，由后端按默认推荐返回
      // - 最新：不传 category/label，传 sort=time
      if (category === '最新') {
        sort = 'time';
        category = '';
        label = '';
        // 按首页策略靠近“最新上映”：限定年份为当年
        const year = new Date().getFullYear();
        qs.set('year', String(year));
      } else if (category === '热门') {
        category = '';
        label = '';
      }

      if (category) qs.set('category', category);
      if (label) qs.set('label', label);
      qs.set('start', String((pgParam - 1) * pageSize));
      qs.set('limit', String(pageSize));
      if (sort) qs.set('sort', sort);

      // 直接内调（不走 HTTP 回环，避免 Host 投毒）
      const list = await fetchRecommendsDirect({
        kind,
        category,
        label,
        year: qs.get('year') || '',
        sort,
        start: (pgParam - 1) * pageSize,
        limit: pageSize,
      });

      const payload = {
        code: 1,
        msg: 'success',
        page: pgParam,
        pagecount: 999,
        limit: pageSize,
        total: 0,
        list: list.map((item: any) => ({
          vod_id: item.id,
          vod_name: item.title,
          vod_pic: item.poster,
          vod_year: item.year || '',
          vod_remarks: item.rate || '',
        })),
      };

      return NextResponse.json(payload, {
        headers: {
          'Cache-Control': `public, max-age=${cacheTime}, s-maxage=0`,
        },
      });
    }

    // 返回分类
    return NextResponse.json(
      { code: 1, msg: 'success', class: classes, list: [] },
      {
        headers: {
          'Cache-Control': `public, max-age=${cacheTime}, s-maxage=0`,
        },
      },
    );
  } catch {
    return NextResponse.json(
      { code: 0, msg: 'error', class: [], list: [] },
      { status: 500 },
    );
  }
}
