import { NextRequest, NextResponse } from 'next/server';

import { getVerifiedAuthInfo } from '@/lib/auth';
import { getAvailableApiSites, getCacheTime } from '@/lib/config';

export const runtime = 'edge';

// OrionTV 兼容接口
export async function GET(request: NextRequest) {
  try {
    const auth = await getVerifiedAuthInfo(request);
    const username = auth?.username;
    const apiSites = await getAvailableApiSites(username);
    const cacheTime = await getCacheTime();

    return NextResponse.json(apiSites, {
      headers: {
        // 按用户过滤的资源列表，禁止 CDN 跨用户共享
        'Cache-Control': `private, max-age=${cacheTime}`,
        'CDN-Cache-Control': 'no-store',
        'Vercel-CDN-Cache-Control': 'no-store',
        'Netlify-Vary': 'query',
      },
    });
  } catch {
    return NextResponse.json({ error: '获取资源失败' }, { status: 500 });
  }
}
