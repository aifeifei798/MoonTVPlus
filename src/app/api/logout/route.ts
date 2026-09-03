import { NextResponse } from 'next/server';

export const runtime = 'edge';

export async function POST() {
  const response = NextResponse.json({ ok: true });
  const expired = new Date(0);

  // 清除认证cookie（私密 + 展示各清一次）
  response.cookies.set('auth', '', {
    path: '/',
    expires: expired,
    sameSite: 'lax',
    httpOnly: true,
    secure: process.env.NODE_ENV === 'production',
  });
  response.cookies.set('auth_info', '', {
    path: '/',
    expires: expired,
    sameSite: 'lax',
    httpOnly: false,
    secure: process.env.NODE_ENV === 'production',
  });

  return response;
}
