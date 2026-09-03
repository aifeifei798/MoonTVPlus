import { NextRequest, NextResponse } from 'next/server';

import { isSecureRequest } from '@/lib/auth';

export const runtime = 'edge';

export async function POST(request: NextRequest) {
  const response = NextResponse.json({ ok: true });
  const expired = new Date(0);
  // 清除时 Secure 必须与写入时一致，否则浏览器删不掉旧 cookie
  const secureCookie = isSecureRequest(request);

  // 清除认证cookie（私密 + 展示各清一次）
  response.cookies.set('auth', '', {
    path: '/',
    expires: expired,
    sameSite: 'lax',
    httpOnly: true,
    secure: secureCookie,
  });
  response.cookies.set('auth_info', '', {
    path: '/',
    expires: expired,
    sameSite: 'lax',
    httpOnly: false,
    secure: secureCookie,
  });

  return response;
}
