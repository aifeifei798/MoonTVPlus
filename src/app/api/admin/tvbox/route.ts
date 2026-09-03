/* eslint-disable @typescript-eslint/no-explicit-any */

import { NextRequest, NextResponse } from 'next/server';

import { getVerifiedAuthInfo } from '@/lib/auth';
import { getConfig, invalidateConfigCache } from '@/lib/config';
import { getStorage } from '@/lib/db';
import { getRequestOrigin } from '@/lib/request-origin';

export const runtime = 'edge';

function buildTvboxConfigUrl(request: NextRequest) {
  const origin = getRequestOrigin(request);
  return `${origin}/api/tvbox/config`;
}

export async function GET(request: NextRequest) {
  // 所有模式均需登录（网关已拦截，此处再验签做纵深防御，避免中间件失效即越权）
  const authInfo = await getVerifiedAuthInfo(request);
  if (!authInfo) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  }

  const storageType = process.env.NEXT_PUBLIC_STORAGE_TYPE || 'localstorage';
  const adminConfig = await getConfig();

  // 本地模式：已登录才返回只读信息（不再匿名泄露 PASSWORD）
  if (storageType === 'localstorage') {
    const baseUrl = buildTvboxConfigUrl(request);
    return NextResponse.json({
      enabled:
        (process.env.TVBOX_ENABLED == null || String(process.env.TVBOX_ENABLED).trim() === '')
          ? true
          : String(process.env.TVBOX_ENABLED).toLowerCase() === 'true',
      password: process.env.PASSWORD || '',
      url: baseUrl,
      localMode: true,
    });
  }

  if (!authInfo.username) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  }
  // 生成接口 URL（基于请求 URL 推导）
  const baseUrl = buildTvboxConfigUrl(request);
  // 为生成的订阅 URL 添加加密后的 un 查询参数
  const un = Buffer.from(authInfo.username, 'utf8').toString('base64');
  const url = `${baseUrl}?un=${encodeURIComponent(un)}`;

  const payload = {
    enabled:
      adminConfig.SiteConfig.TVBoxEnabled === true,
    password: adminConfig.SiteConfig.TVBoxPassword || '',
    url,
    localMode: false,
  } as any;

  return NextResponse.json(payload);
}

export async function POST(request: NextRequest) {
  const authInfo = await getVerifiedAuthInfo(request);
  if (!authInfo || !authInfo.username) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  }

  const adminConfig = await getConfig();
  const username = authInfo.username;
  if (username !== process.env.USERNAME) {
    const user = adminConfig.UserConfig.Users.find((u) => u.username === username);
    if (!user || user.role !== 'admin' || user.banned) {
      return NextResponse.json({ error: '权限不足' }, { status: 403 });
    }
  }

  const body = await request.json();
  const { enabled, password, mode } = body as {
    enabled?: boolean;
    password?: string;
    mode?: 'custom' | 'random' | 'keep';
  };

  const storageType = process.env.NEXT_PUBLIC_STORAGE_TYPE || 'localstorage';

  // localstorage 模式：开关由环境变量控制，这里只允许返回提示，不修改
  if (storageType === 'localstorage') {
    return NextResponse.json(
      { error: '本地模式下由环境变量 TVBOX_ENABLED 控制开关，口令=PASSWORD' },
      { status: 400 }
    );
  }

  // 非本地模式：允许修改配置并持久化
  if (typeof enabled === 'boolean') {
    (adminConfig.SiteConfig as any).TVBoxEnabled = enabled;
  }

  let finalPassword = (adminConfig.SiteConfig as any).TVBoxPassword || '';
  if (mode === 'random') {
    // 简单随机口令
    const alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789';
    finalPassword = Array.from({ length: 16 })
      .map(() => alphabet[Math.floor(Math.random() * alphabet.length)])
      .join('');
  } else if (mode === 'custom' && typeof password === 'string') {
    finalPassword = password;
  }

  (adminConfig.SiteConfig as any).TVBoxPassword = finalPassword;

  const storage = getStorage();
  if (storage && typeof (storage as any).setAdminConfig === 'function') {
    await (storage as any).setAdminConfig(adminConfig);
      invalidateConfigCache();
  }

  const baseUrl = buildTvboxConfigUrl(request);

  return NextResponse.json({
    enabled: (adminConfig.SiteConfig as any).TVBoxEnabled === true,
    password: (adminConfig.SiteConfig as any).TVBoxPassword || '',
    url: (() => {
      const un = Buffer.from(username, 'utf8').toString('base64');
      return `${baseUrl}?un=${encodeURIComponent(un)}`;
    })(),
  });
}


