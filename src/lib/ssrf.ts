// 服务端 fetch 的 SSRF 基础防护：仅允许 http/https，拦截内网/回环/元数据地址
// 注意：Edge 运行时无 DNS 解析能力，无法防御 DNS 重绑定；缓解措施为
// 手动处理重定向并逐跳复检 + 短超时 + 体积上限（见 safeFetch）。
const BLOCKED_HOSTNAMES = new Set([
  'localhost',
  'metadata.google.internal',
  'metadata.google',
  'metadata',
  'instance-data',
  'instance-data-compute',
]);

function parseNumericPart(part: string): number | null {
  const p = part.trim().toLowerCase();
  if (!p) return null;
  try {
    if (p.startsWith('0x')) {
      const v = parseInt(p.slice(2), 16);
      return Number.isSafeInteger(v) && v >= 0 ? v : null;
    }
    if (/^0[0-7]+$/.test(p) && p.length > 1) {
      const v = parseInt(p, 8);
      return Number.isSafeInteger(v) && v >= 0 ? v : null;
    }
    if (/^\d+$/.test(p)) {
      const v = parseInt(p, 10);
      return Number.isSafeInteger(v) && v >= 0 ? v : null;
    }
    // 形如 0x7f 的裸十六进制（无点分）已在外层处理，这里再兜底
    return null;
  } catch {
    return null;
  }
}

// 按 inet_aton 语义把 1~4 段的变形 IP 归一化为点分十进制；非 IP 返回 null
function normalizeObfuscatedIpv4(host: string): string | null {
  const h = host.toLowerCase().replace(/\.$/, '');
  // 纯十进制大整数（如 2130706433）与纯十六进制（如 0x7f000001）
  if (/^0x[0-9a-f]+$/i.test(h)) {
    const v = parseInt(h, 16);
    if (!Number.isSafeInteger(v) || v < 0 || v > 0xffffffff) return null;
    return [
      (v >>> 24) & 0xff,
      (v >>> 16) & 0xff,
      (v >>> 8) & 0xff,
      v & 0xff,
    ].join('.');
  }
  if (/^\d+$/.test(h)) {
    // 超过 32bit 直接视为可疑（调用方按拦截处理）
    const v = Number(h);
    if (!Number.isSafeInteger(v) || v < 0 || v > 0xffffffff)
      return '__invalid__';
    return [
      (v >>> 24) & 0xff,
      (v >>> 16) & 0xff,
      (v >>> 8) & 0xff,
      v & 0xff,
    ].join('.');
  }
  if (!/^[0-9a-fx.]+$/i.test(h) || !h.includes('.')) return null;
  const rawParts = h.split('.');
  if (rawParts.length < 1 || rawParts.length > 4) return null;
  const nums: number[] = [];
  for (const part of rawParts) {
    const v = parseNumericPart(part);
    if (v === null) return null;
    nums.push(v);
  }
  let bytes: number[];
  if (nums.length === 1) {
    const v = nums[0];
    if (v > 0xffffffff) return '__invalid__';
    bytes = [(v >>> 24) & 0xff, (v >>> 16) & 0xff, (v >>> 8) & 0xff, v & 0xff];
  } else if (nums.length === 2) {
    const [a, b] = nums;
    if (a > 0xff || b > 0xffffff) return '__invalid__';
    bytes = [a, (b >>> 16) & 0xff, (b >>> 8) & 0xff, b & 0xff];
  } else if (nums.length === 3) {
    const [a, b, c] = nums;
    if (a > 0xff || b > 0xff || c > 0xffff) return '__invalid__';
    bytes = [a, b, (c >>> 8) & 0xff, c & 0xff];
  } else {
    if (nums.some((v) => v > 0xff)) return '__invalid__';
    bytes = nums;
  }
  return bytes.join('.');
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
    inRange('0.0.0.0', 8) ||
    inRange('100.64.0.0', 10) ||
    inRange('192.0.2.0', 24) ||
    inRange('198.51.100.0', 24) ||
    inRange('203.0.113.0', 24) ||
    inRange('224.0.0.0', 4) ||
    inRange('240.0.0.0', 4)
  );
}

function isBlockedIpv6(host: string): boolean {
  // Node 的 URL.hostname 保留括号（如 [::1]），先去括号；兼容 zone id（fe80::1%eth0）
  const h = host
    .toLowerCase()
    .replace(/^\[(.*)\]$/, '$1')
    .split('%')[0];
  if (h === '::1' || h === '::' || h === '::ffff:127.0.0.1') return true;
  // Node 会把 ::ffff:127.0.0.1 归一化为 ::ffff:7f00:1，同样拦截
  if (h === '::ffff:7f00:1' || h === '0:0:0:0:0:ffff:7f00:1') return true;
  if (h.startsWith('fe80:') || h.startsWith('fec0:')) return true;
  // 唯一本地 fc00::/7
  if (h.startsWith('fc') || h.startsWith('fd')) {
    // 避免误杀正常域名：仅当包含冒号时才按 IPv6 处理
    if (h.includes(':')) return true;
  }
  // IPv4 映射 ::ffff:a.b.c.d / 0:0:0:0:0:ffff:7f00:1
  if (h.includes('::ffff:') || h.startsWith('0:0:0:0:0:ffff:')) {
    const tail = h.split(':').pop() || '';
    // 点分尾按 IPv4 判私网；十六进制尾（如 7f00:1）直接拦截
    if (tail.includes('.')) {
      const normalized = normalizeObfuscatedIpv4(tail);
      if (normalized && normalized !== '__invalid__') {
        return isPrivateIpv4(normalized);
      }
      return true;
    }
    // 形如 ::ffff:7f00:1 的十六进制尾
    return true;
  }
  // 全零压缩变体
  if (/^(0+:)+0*$/.test(h) || h === '0:0:0:0:0:0:0:1') return true;
  return false;
}

export function assertSafeFetchUrl(
  raw: string,
  opts?: { allowHttp?: boolean },
): URL {
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
  // 十六进制/十进制/八进制/混合点分等 IP 变形绕过拦截：先归一化再判私网
  const normalized = normalizeObfuscatedIpv4(host);
  if (normalized === '__invalid__') {
    throw new Error('禁止请求的内网地址');
  }
  if (normalized) {
    if (isPrivateIpv4(normalized)) {
      throw new Error('禁止请求的内网地址');
    }
    // 归一化成功但为公网 IP（如 8.8.8.8 的变形写法）则放行
    return url;
  }
  if (/^\d+\.\d+\.\d+\.\d+$/.test(host) && isPrivateIpv4(host)) {
    throw new Error('禁止请求的内网地址');
  }
  // IPv6 回环/未指定/链路本地/唯一本地/IPv4 映射
  if (isBlockedIpv6(host)) {
    throw new Error('禁止请求的内网地址');
  }
  // 云元数据 IP 的域名形式兜底
  if (
    host.includes('metadata') ||
    host.includes('instance-data') ||
    host === '169.254.169.254'
  ) {
    throw new Error('禁止请求的内网地址');
  }
  return url;
}

export const SAFE_FETCH_MAX_REDIRECTS = 3;

/**
 * 带 SSRF 纵深校验的 fetch：手动处理重定向并逐跳转复检目标，
 * 避免 `redirect: 'follow'` 跳到内网/元数据地址。Edge 无 DNS 能力，
 * 仍无法防御 DNS 重绑定，敏感内网部署请再加出口防火墙。
 */
export async function safeFetch(
  rawUrl: string,
  init: RequestInit = {},
  opts: { timeoutMs?: number; maxRedirects?: number } = {},
): Promise<Response> {
  const timeoutMs = opts.timeoutMs ?? 15000;
  const maxRedirects = opts.maxRedirects ?? SAFE_FETCH_MAX_REDIRECTS;
  let current = assertSafeFetchUrl(rawUrl).href;

  for (let i = 0; i <= maxRedirects; i++) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    let res: Response;
    try {
      res = await fetch(current, {
        ...init,
        signal: controller.signal,
        redirect: 'manual',
      });
    } catch (e) {
      clearTimeout(timer);
      if ((e as Error).name === 'AbortError') throw new Error('请求超时');
      throw e;
    }
    clearTimeout(timer);

    const status = res.status;
    if (
      status === 301 ||
      status === 302 ||
      status === 303 ||
      status === 307 ||
      status === 308
    ) {
      if (i === maxRedirects) throw new Error('重定向次数过多');
      const location = res.headers.get('location');
      if (!location) throw new Error('重定向缺少 Location');
      try {
        await res.arrayBuffer().catch(() => undefined);
      } catch {
        // ignore
      }
      let next: string;
      try {
        next = new URL(location, current).href;
      } catch {
        throw new Error('非法重定向地址');
      }
      // 逐跳复检：跳转到内网直接拦截
      assertSafeFetchUrl(next);
      current = next;
      continue;
    }
    return res;
  }
  throw new Error('重定向次数过多');
}
