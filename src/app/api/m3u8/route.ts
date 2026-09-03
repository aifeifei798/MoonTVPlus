import { NextRequest, NextResponse } from 'next/server';

import { downloadTsSegment, parseM3U8 } from '@/lib/m3u8-downloader';
import { assertSafeFetchUrl } from '@/lib/ssrf';

export const runtime = 'edge';

/**
 * 解析M3U8文件接口
 * POST /api/m3u8/parse
 */
export async function POST(request: NextRequest) {
  try {
    const { url } = await request.json();

    if (!url || typeof url !== 'string') {
      return NextResponse.json({ error: '缺少 m3u8 URL' }, { status: 400 });
    }
    if (url.length > 2048) {
      return NextResponse.json({ error: 'URL 过长' }, { status: 400 });
    }
    try {
      assertSafeFetchUrl(url);
    } catch (e) {
      return NextResponse.json(
        { error: (e as Error).message || '非法 URL' },
        { status: 400 }
      );
    }

    const task = await parseM3U8(url);

    return NextResponse.json({
      success: true,
      data: {
        title: task.title,
        type: task.type,
        totalSegments: task.tsUrlList.length,
        duration: task.durationSecond,
        hasAes: !!task.aesConf.key,
        segments: task.tsUrlList.map((url, index) => ({
          index: index + 1,
          url,
        })),
      },
    });
  } catch (error) {
    // eslint-disable-next-line no-console
    console.error('解析M3U8失败:', error);
    return NextResponse.json(
      {
        error: '解析M3U8文件失败',
        message: error instanceof Error ? error.message : '未知错误',
      },
      { status: 500 }
    );
  }
}

/**
 * 代理下载TS片段（避免CORS问题）
 * GET /api/m3u8/proxy?url=xxx
 */
export async function GET(request: NextRequest) {
  try {
    const { searchParams } = new URL(request.url);
    const url = searchParams.get('url');

    if (!url) {
      return NextResponse.json({ error: '缺少 URL 参数' }, { status: 400 });
    }
    try {
      assertSafeFetchUrl(url);
    } catch (e) {
      return NextResponse.json(
        { error: (e as Error).message || '非法 URL' },
        { status: 400 }
      );
    }

    const data = await downloadTsSegment(url);
    if (data.byteLength > 50 * 1024 * 1024) {
      return NextResponse.json({ error: '片段过大' }, { status: 413 });
    }
    
    return new NextResponse(data, {
      headers: {
        'Content-Type': 'application/octet-stream',
        'Cache-Control': 'public, max-age=31536000',
      },
    });
  } catch (error) {
    // eslint-disable-next-line no-console
    console.error('代理下载失败:', error);
    return NextResponse.json(
      {
        error: '下载失败',
        message: error instanceof Error ? error.message : '未知错误',
      },
      { status: 500 }
    );
  }
}
