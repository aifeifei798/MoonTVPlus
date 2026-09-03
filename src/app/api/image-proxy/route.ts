import { NextResponse } from 'next/server';

import { assertSafeFetchUrl } from '@/lib/ssrf';

export const runtime = 'edge';

// OrionTV 兼容接口
export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const imageUrl = searchParams.get('url');

  if (!imageUrl) {
    return NextResponse.json({ error: 'Missing image URL' }, { status: 400 });
  }

  try {
    assertSafeFetchUrl(imageUrl);
  } catch (e) {
    return NextResponse.json(
      { error: (e as Error).message || '非法 URL' },
      { status: 400 }
    );
  }

  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);
    const imageResponse = await fetch(imageUrl, {
      signal: controller.signal,
      redirect: 'follow',
      headers: {
        Referer: 'https://movie.douban.com/',
        'User-Agent':
          'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
      },
    }).finally(() => clearTimeout(timeout));

    if (!imageResponse.ok) {
      return NextResponse.json(
        { error: imageResponse.statusText },
        { status: imageResponse.status }
      );
    }

    const contentType = imageResponse.headers.get('content-type') || '';
    if (!contentType.startsWith('image/')) {
      // 避免被用作任意文件代理
      try {
        await imageResponse.arrayBuffer();
      } catch {
        // ignore
      }
      return NextResponse.json(
        { error: '仅允许代理图片内容' },
        { status: 400 }
      );
    }
    const contentLength = Number(
      imageResponse.headers.get('content-length') || '0'
    );
    if (contentLength > 10 * 1024 * 1024) {
      return NextResponse.json({ error: '图片过大' }, { status: 413 });
    }

    if (!imageResponse.body) {
      return NextResponse.json(
        { error: 'Image response has no body' },
        { status: 500 }
      );
    }

    // 创建响应头
    const headers = new Headers();
    if (contentType) {
      headers.set('Content-Type', contentType);
    }

    // 设置缓存头（可选）
    headers.set('Cache-Control', 'public, max-age=15720000, s-maxage=15720000'); // 缓存半年
    headers.set('CDN-Cache-Control', 'public, s-maxage=15720000');
    headers.set('Vercel-CDN-Cache-Control', 'public, s-maxage=15720000');
    headers.set('Netlify-Vary', 'query');

    // 直接返回图片流
    return new Response(imageResponse.body, {
      status: 200,
      headers,
    });
  } catch (error) {
    return NextResponse.json(
      { error: 'Error fetching image' },
      { status: 500 }
    );
  }
}
