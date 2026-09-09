import { NextResponse } from 'next/server';

/**
 * Bangumi 放送表服务端代理：
 * 客户端直接请求 api.bgm.tv 常因网络/跨域问题失败，此路由在服务端抓取并缓存，
 * 保证首页“新番放送”与动漫页“每日放送”只要有服务端网络即可展示。
 */

export const runtime = 'edge';

interface BangumiCacheEntry {
  savedAt: number;
  data: unknown;
}

const memoryCache = new Map<string, BangumiCacheEntry>();

const BANGUMI_CACHE_TTL_MS = 3600 * 1000;

function getCacheTtlMs(): number {
  const sec = Number(process.env.BANGUMI_CACHE_TTL ?? 3600);
  if (!Number.isFinite(sec) || sec < 0) return BANGUMI_CACHE_TTL_MS;
  return sec * 1000;
}

async function fetchCalendarFromBangumi(): Promise<unknown> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 10000);

  try {
    const response = await fetch('https://api.bgm.tv/calendar', {
      signal: controller.signal,
      headers: {
        'User-Agent':
          'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
      },
    });
    if (!response.ok) {
      throw new Error(`HTTP error! Status: ${response.status}`);
    }
    return await response.json();
  } finally {
    clearTimeout(timeoutId);
  }
}

export async function GET() {
  const now = Date.now();
  const ttl = getCacheTtlMs();
  const cached = memoryCache.get('calendar');

  // 命中且未过期直接返回；过期时不删除，留作上游故障兜底
  if (cached) {
    const fresh = now - cached.savedAt < ttl;
    const headers = new Headers({
      'Content-Type': 'application/json',
      'Cache-Control': `public, max-age=${fresh ? ttl / 1000 : 60}`,
      'CDN-Cache-Control': `public, s-maxage=${fresh ? ttl / 1000 : 60}`,
      'Vercel-CDN-Cache-Control': `public, s-maxage=${fresh ? ttl / 1000 : 60}`,
    });
    return new Response(JSON.stringify(cached.data), { status: 200, headers });
  }

  try {
    const data = await fetchCalendarFromBangumi();
    memoryCache.set('calendar', { savedAt: now, data });
    return NextResponse.json(data, {
      headers: {
        'Cache-Control': `public, max-age=${ttl / 1000}, s-maxage=${
          ttl / 1000
        }`,
        'CDN-Cache-Control': `public, s-maxage=${ttl / 1000}`,
        'Vercel-CDN-Cache-Control': `public, s-maxage=${ttl / 1000}`,
      },
    });
  } catch (error) {
    // 上游不可用时退回过期缓存（重新读取，避免 TS 控制流收窄）
    const stale = memoryCache.get('calendar');
    if (stale) {
      return new Response(JSON.stringify(stale.data), { status: 200 });
    }
    return NextResponse.json(
      { error: '获取 Bangumi 放送表失败', details: (error as Error).message },
      { status: 502 }
    );
  }
}
