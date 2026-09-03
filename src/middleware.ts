/* eslint-disable no-console */

import { NextRequest, NextResponse } from 'next/server';

import { getVerifiedAuthInfo } from '@/lib/auth';

export async function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  // 跳过不需要认证的路径
  if (shouldSkipAuth(pathname)) {
    return NextResponse.next();
  }

  // 定时任务：如配置 CRON_SECRET 则必须携带 x-cron-secret 头，避免匿名触发全量刷新
  if (pathname.startsWith('/api/cron')) {
    const secret = process.env.CRON_SECRET || '';
    if (secret) {
      const provided =
        request.headers.get('x-cron-secret') ||
        request.nextUrl.searchParams.get('secret') ||
        '';
      if (provided !== secret) {
        return new NextResponse('Unauthorized', { status: 401 });
      }
    }
    return NextResponse.next();
  }

  if (!process.env.PASSWORD) {
    // 如果没有设置密码，重定向到警告页面
    const warningUrl = new URL('/warning', request.url);
    return NextResponse.redirect(warningUrl);
  }

  // 统一验签（含过期校验，兼容旧 localstorage 明文 cookie 过渡期）
  // 注意：banned/role 细粒度校验在各路由内二次执行，此处只做网关级签名门禁
  const authInfo = await getVerifiedAuthInfo(request);

  if (!authInfo) {
    return handleAuthFailure(request, pathname);
  }

  return NextResponse.next();
}

// 处理认证失败的情况
function handleAuthFailure(
  request: NextRequest,
  pathname: string
): NextResponse {
  // 如果是 API 路由，返回 401 状态码
  if (pathname.startsWith('/api')) {
    return new NextResponse('Unauthorized', { status: 401 });
  }

  // 否则重定向到登录页面
  const loginUrl = new URL('/login', request.url);
  // 保留完整的URL，包括查询参数
  const fullUrl = `${pathname}${request.nextUrl.search}`;
  loginUrl.searchParams.set('redirect', fullUrl);
  return NextResponse.redirect(loginUrl);
}

// 判断是否需要跳过认证的路径
function shouldSkipAuth(pathname: string): boolean {
  const skipPaths = [
    '/_next',
    '/favicon.ico',
    '/robots.txt',
    '/manifest.json',
    '/icons/',
    '/logo.png',
    '/screenshot.png',
  ];

  return skipPaths.some((path) => pathname.startsWith(path));
}

// 配置middleware匹配规则
// 注意：api/admin/tvbox 不再放行，必须走鉴权；api/cron 纳入中间件做 CRON_SECRET 校验
export const config = {
  matcher: [
    '/((?!_next/static|_next/image|favicon.ico|login|warning|api/login|api/register|api/logout|api/server-config|api/tvbox/config|api/tvbox/categories|api/douban/recommends).*)',
  ],
};
