// 服务端 fetch 的 SSRF 基础防护：仅允许 http/https，拦截内网/回环/元数据地址
const BLOCKED_HOSTNAMES = new Set([
  'localhost',
  'metadata.google.internal',
  'metadata.google',
]);

function isHexIp(host: string): boolean {
  return /^0x[0-9a-f]+$/i.test(host);
}

function ipv4ToInt(ip: string): number | null {
  const parts = ip.split('.');
  if (parts.length !== 4) return null;
  let n = 0;
  for (const p of parts) {
    if (!/^\d+$/.test(p)) return null;
    const v = parseInt(p, 10);
    if (v < 0 || v > 255) return null;
    n = (n << 8) + v;
  }
  return n >>> 0;
}

function isPrivateIpv4(ip: string): boolean {
  const n = ipv4ToInt(ip);
  if (n === null) return false;
  const inRange = (base: string, bits: number) => {
    const b = ipv4ToInt(base);
    if (b === null) return false;
    const mask = bits === 0 ? 0 : (~0 << (32 - bits)) >>> 0;
    return (n & mask) === (b & mask);
  };
  return (
    inRange('10.0.0.0', 8) ||
    inRange('172.16.0.0', 12) ||
    inRange('192.168.0.0', 16) ||
    inRange('127.0.0.0', 8) ||
    inRange('169.254.0.0', 16) ||
    inRange('0.0.0.0', 8)
  );
}

export function assertSafeFetchUrl(raw: string, opts?: { allowHttp?: boolean }): URL {
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    throw new Error('URL 非法');
  }
  const allowHttp = opts?.allowHttp ?? true;
  if (url.protocol !== 'https:' && !(allowHttp && url.protocol === 'http:')) {
    throw new Error('仅允许 http/https 协议');
  }
  if (url.username || url.password) {
    throw new Error('URL 不得携带认证信息');
  }
  const host = url.hostname.toLowerCase().replace(/\.$/, '');
  if (!host || BLOCKED_HOSTNAMES.has(host)) {
    throw new Error('禁止请求的内网地址');
  }
  // 十六进制/十进制 IP 变形绕过拦截
  if (isHexIp(host) || /^\d+$/.test(host)) {
    throw new Error('禁止请求的内网地址');
  }
  if (/^\d+\.\d+\.\d+\.\d+$/.test(host) && isPrivateIpv4(host)) {
    throw new Error('禁止请求的内网地址');
  }
  // IPv6 回环/未指定/链路本地/唯一本地
  if (
    host === '::1' ||
    host === '::' ||
    host.startsWith('fe80:') ||
    host.startsWith('fc') ||
    host.startsWith('fd')
  ) {
    throw new Error('禁止请求的内网地址');
  }
  return url;
}
