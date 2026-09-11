import { NextResponse } from 'next/server';

import { safeFetch } from '@/lib/ssrf';

export const runtime = 'edge';

// OrionTV 兼容接口
export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const imageUrl = searchParams.get('url');

  if (!imageUrl) {
    return NextResponse.json({ error: 'Missing image URL' }, { status: 400 });
  }

  try {
    // safeFetch 内部做初始校验 + 逐跳转复检，避免 302 到内网/元数据
    const imageResponse = await safeFetch(
      imageUrl,
      {
        headers: {
          Referer: 'https://movie.douban.com/',
          'User-Agent':
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
        },
      },
      { timeoutMs: 15000, maxRedirects: 3 },
    );

    if (!imageResponse.ok) {
      return NextResponse.json(
        { error: imageResponse.statusText },
        { status: imageResponse.status },
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
        { status: 400 },
      );
    }
    const contentLength = Number(
      imageResponse.headers.get('content-length') || '0',
    );
    if (contentLength > 10 * 1024 * 1024) {
      return NextResponse.json({ error: '图片过大' }, { status: 413 });
    }

    if (!imageResponse.body) {
      return NextResponse.json(
        { error: 'Image response has no body' },
        { status: 500 },
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
    const msg = error instanceof Error ? error.message : 'Error fetching image';
    // SSRF 拦截 / 非法 URL / 重定向异常 → 400，其余上游失败 → 502
    if (
      msg.includes('内网') ||
      msg.includes('非法') ||
      msg.includes('仅允许') ||
      msg.includes('重定向') ||
      msg.includes('认证信息')
    ) {
      return NextResponse.json({ error: msg }, { status: 400 });
    }
    if (msg === '请求超时') {
      return NextResponse.json({ error: '上游超时' }, { status: 504 });
    }
    return NextResponse.json(
      { error: 'Error fetching image' },
      { status: 502 },
    );
  }
}
