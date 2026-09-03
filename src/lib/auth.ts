import { NextRequest } from 'next/server';

// 统一的服务端鉴权信息结构
// localstorage 模式: { role, timestamp, signature }，signature = HMAC(PASSWORD, `localstorage:${timestamp}`)
// 数据库模式: { username, role, timestamp, signature }，signature = HMAC(PASSWORD, `${username}:${role}:${timestamp}`)
export interface ServerAuthInfo {
  password?: string; // 仅兼容旧 cookie，新签发不再写入明文密码
  username?: string;
  role?: 'owner' | 'admin' | 'user';
  signature?: string;
  timestamp?: number;
}

export const AUTH_COOKIE_MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000;

// 生成服务端签名的逐字节实现（Edge/Node 通用，依赖 WebCrypto）
export async function generateSignature(
  data: string,
  secret: string
): Promise<string> {
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey(
    'raw',
    encoder.encode(secret),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign']
  );
  const sig = await crypto.subtle.sign('HMAC', key, encoder.encode(data));
  return Array.from(new Uint8Array(sig))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('');
}

export async function verifySignature(
  data: string,
  signature: string,
  secret: string
): Promise<boolean> {
  if (!data || !signature || !secret) return false;
  try {
    const encoder = new TextEncoder();
    const key = await crypto.subtle.importKey(
      'raw',
      encoder.encode(secret),
      { name: 'HMAC', hash: 'SHA-256' },
      false,
      ['verify']
    );
    const sigBytes = new Uint8Array(
      signature.match(/.{1,2}/g)?.map((b) => parseInt(b, 16)) || []
    );
    if (sigBytes.length !== 32) return false;
    return await crypto.subtle.verify(
      'HMAC',
      key,
      sigBytes as unknown as ArrayBuffer,
      encoder.encode(data)
    );
  } catch {
    return false;
  }
}

export function isAuthExpired(timestamp?: number): boolean {
  if (!timestamp || typeof timestamp !== 'number') return true;
  const now = Date.now();
  if (timestamp > now + 5 * 60 * 1000) return true; // 时钟漂移容忍 5 分钟
  return now - timestamp > AUTH_COOKIE_MAX_AGE_MS;
}

// 服务端从 httpOnly 的 auth cookie 读取并验签
export async function getVerifiedAuthInfo(
  request: NextRequest
): Promise<ServerAuthInfo | null> {
  const raw = getAuthInfoFromCookie(request);
  if (!raw) return null;
  const secret = process.env.PASSWORD || '';
  if (!secret) return null;

  // 兼容旧 localstorage cookie（明文 password）：仅在过渡期承认，且要求密码正确、未过期（按 7 天计）
  if (raw.password && !raw.username) {
    if (raw.password !== secret) return null;
    if (raw.timestamp && isAuthExpired(raw.timestamp)) return null;
    return raw;
  }

  const storageType = process.env.NEXT_PUBLIC_STORAGE_TYPE || 'localstorage';
  if (storageType === 'localstorage') {
    if (!raw.signature || !raw.timestamp) return null;
    if (isAuthExpired(raw.timestamp)) return null;
    const ok = await verifySignature(
      `localstorage:${raw.timestamp}`,
      raw.signature,
      secret
    );
    return ok ? raw : null;
  }

  if (!raw.username || !raw.signature || !raw.timestamp || !raw.role) {
    return null;
  }
  if (isAuthExpired(raw.timestamp)) return null;
  const ok = await verifySignature(
    `${raw.username}:${raw.role}:${raw.timestamp}`,
    raw.signature,
    secret
  );
  return ok ? raw : null;
}

// 判断请求是否走 HTTPS（含反代场景的 X-Forwarded-Proto）。
// Secure Cookie 在 http 下会被浏览器直接丢弃（登录成功也存不住），故必须按实际协议设置。
export function isSecureRequest(request: NextRequest | Request): boolean {
  try {
    if (new URL(request.url).protocol === 'https:') return true;
  } catch {
    // 解析失败则继续看代理头
  }
  const forwarded =
    request.headers.get('x-forwarded-proto') ||
    request.headers.get('x-forwarded-protocol') ||
    '';
  return forwarded.split(',')[0].trim().toLowerCase() === 'https';
}

// 统一的 cookie 配置：auth 为 httpOnly 私密凭证，auth_info 为前端可读的展示信息
// secure 默认跟随 NODE_ENV，调用方传入请求的实际协议后以实际协议为准
export function getAuthCookieOptions(expires?: Date, secure?: boolean) {
  return {
    path: '/',
    expires: expires ?? new Date(Date.now() + AUTH_COOKIE_MAX_AGE_MS),
    sameSite: 'lax' as const,
    httpOnly: true,
    secure: secure ?? process.env.NODE_ENV === 'production',
  };
}

export function getAuthInfoCookieOptions(expires?: Date, secure?: boolean) {
  return {
    path: '/',
    expires: expires ?? new Date(Date.now() + AUTH_COOKIE_MAX_AGE_MS),
    sameSite: 'lax' as const,
    httpOnly: false,
    secure: secure ?? process.env.NODE_ENV === 'production',
  };
}

// 从cookie获取认证信息 (服务端使用，原始解析，不验签；验签请用 getVerifiedAuthInfo)
export function getAuthInfoFromCookie(
  request: NextRequest
): ServerAuthInfo | null {
  const authCookie = request.cookies.get('auth');

  if (!authCookie) {
    return null;
  }

  try {
    const decoded = decodeURIComponent(authCookie.value);
    const authData = JSON.parse(decoded);
    return authData;
  } catch (error) {
    return null;
  }
}

// 从cookie获取认证信息 (客户端使用)
// 优先读取不含密钥的 auth_info，兼容旧的 auth cookie（过渡期）
export function getAuthInfoFromBrowserCookie(): {
  password?: string;
  username?: string;
  signature?: string;
  timestamp?: number;
  role?: 'owner' | 'admin' | 'user';
} | null {
  if (typeof window === 'undefined') {
    return null;
  }

  try {
    // 解析 document.cookie
    const cookies = document.cookie.split(';').reduce((acc, cookie) => {
      const trimmed = cookie.trim();
      const firstEqualIndex = trimmed.indexOf('=');

      if (firstEqualIndex > 0) {
        const key = trimmed.substring(0, firstEqualIndex);
        const value = trimmed.substring(firstEqualIndex + 1);
        if (key && value) {
          acc[key] = value;
        }
      }

      return acc;
    }, {} as Record<string, string>);

    // 新版：auth_info 仅含 username/role，无密钥；旧版回退读 auth
    const raw = cookies['auth_info'] || cookies['auth'];
    if (!raw) {
      return null;
    }

    // 处理可能的双重编码
    let decoded = decodeURIComponent(raw);

    // 如果解码后仍然包含 %，说明是双重编码，需要再次解码
    if (decoded.includes('%')) {
      try {
        decoded = decodeURIComponent(decoded);
      } catch {
        // 保持单次解码结果
      }
    }

    const authData = JSON.parse(decoded);
    return authData;
  } catch (error) {
    return null;
  }
}
