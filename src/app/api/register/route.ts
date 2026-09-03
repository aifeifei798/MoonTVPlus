/* eslint-disable no-console,@typescript-eslint/no-explicit-any */
import { NextRequest, NextResponse } from 'next/server';

import {
  generateSignature,
  getAuthCookieOptions,
  getAuthInfoCookieOptions,
} from '@/lib/auth';
import { getConfig } from '@/lib/config';
import { db } from '@/lib/db';

export const runtime = 'edge';

// 读取存储类型环境变量，默认 localstorage
const STORAGE_TYPE =
  (process.env.NEXT_PUBLIC_STORAGE_TYPE as
    | 'localstorage'
    | 'redis'
    | 'upstash'
    | undefined) || 'localstorage';

// 生成认证Cookie（带签名，绑定 role+timestamp，7 天过期由中间件校验）
async function generateAuthCookie(username: string): Promise<string> {
  const timestamp = Date.now();
  const signingKey = process.env.PASSWORD || '';
  const signature = await generateSignature(
    `${username}:user:${timestamp}`,
    signingKey
  );
  return encodeURIComponent(
    JSON.stringify({ username, role: 'user', timestamp, signature })
  );
}

export async function POST(req: NextRequest) {
  try {
    // localstorage 模式下不支持注册
    if (STORAGE_TYPE === 'localstorage') {
      return NextResponse.json(
        { error: '当前模式不支持注册' },
        { status: 400 }
      );
    }

    const config = await getConfig();
    // 校验是否开放注册
    if (!config.UserConfig.AllowRegister) {
      return NextResponse.json({ error: '当前未开放注册' }, { status: 400 });
    }

    if (!process.env.PASSWORD) {
      return NextResponse.json(
        { error: '服务端未配置 PASSWORD，拒绝注册' },
        { status: 500 }
      );
    }

    const { username, password } = await req.json();

    if (!username || typeof username !== 'string') {
      return NextResponse.json({ error: '用户名不能为空' }, { status: 400 });
    }
    if (!password || typeof password !== 'string') {
      return NextResponse.json({ error: '密码不能为空' }, { status: 400 });
    }
    const cleanUsername = username.trim();
    // 基础强度与格式校验：3-32 位，字母数字下划线/中划线
    if (!/^[a-zA-Z0-9_-]{3,32}$/.test(cleanUsername)) {
      return NextResponse.json(
        { error: '用户名仅允许 3-32 位字母/数字/下划线/中划线' },
        { status: 400 }
      );
    }
    if (password.length < 6 || password.length > 64) {
      return NextResponse.json(
        { error: '密码长度需为 6-64 位' },
        { status: 400 }
      );
    }

    // 检查是否和管理员重复（统一返回，避免枚举差异过大；此处保持 400 但不泄露角色）
    if (process.env.USERNAME && cleanUsername === process.env.USERNAME) {
      return NextResponse.json({ error: '用户已存在' }, { status: 400 });
    }

    try {
      // 检查用户是否已存在
      const exist = await db.checkUserExist(cleanUsername);
      if (exist) {
        return NextResponse.json({ error: '用户已存在' }, { status: 400 });
      }

      await db.registerUser(cleanUsername, password);

      // 添加到配置中并保存
      config.UserConfig.Users.push({
        username: cleanUsername,
        role: 'user',
      });
      await db.saveAdminConfig(config);

      // 注册成功，设置认证cookie
      const response = NextResponse.json({ ok: true });
      const cookieValue = await generateAuthCookie(cleanUsername);
      const expires = new Date();
      expires.setDate(expires.getDate() + 7); // 7天过期

      response.cookies.set('auth', cookieValue, getAuthCookieOptions(expires));
      response.cookies.set(
        'auth_info',
        encodeURIComponent(
          JSON.stringify({ username: cleanUsername, role: 'user' })
        ),
        getAuthInfoCookieOptions(expires)
      );

      return response;
    } catch (err) {
      console.error('数据库注册失败', err);
      return NextResponse.json({ error: '数据库错误' }, { status: 500 });
    }
  } catch (error) {
    console.error('注册接口异常', error);
    return NextResponse.json({ error: '服务器错误' }, { status: 500 });
  }
}
