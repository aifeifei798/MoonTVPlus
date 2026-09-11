// Edge/Node 通用的密码哈希（WebCrypto SHA-256 + 每用户随机盐）
// 存储格式：`sha256$<saltHex>$<hashHex>`；兼容旧明文以便平滑迁移
function bytesToHex(bytes: Uint8Array): string {
  return Array.from(bytes)
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('');
}

function hexToBytes(hex: string): Uint8Array {
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < out.length; i++) {
    out[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16);
  }
  return out;
}

async function sha256Hex(data: string): Promise<string> {
  const digest = await crypto.subtle.digest(
    'SHA-256',
    new TextEncoder().encode(data),
  );
  return bytesToHex(new Uint8Array(digest));
}

export function isHashedPassword(stored: string): boolean {
  return stored.startsWith('sha256$');
}

export async function hashPassword(password: string): Promise<string> {
  const salt = bytesToHex(crypto.getRandomValues(new Uint8Array(16)));
  const hash = await sha256Hex(`${salt}:${password}`);
  return `sha256$${salt}$${hash}`;
}

function timingSafeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) {
    diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  }
  return diff === 0;
}

/** 验密：支持新哈希 + 旧明文（命中旧明文时调用方可在后台升级） */
export async function verifyPassword(
  password: string,
  stored: string,
): Promise<{ ok: boolean; needsUpgrade: boolean }> {
  if (!stored) return { ok: false, needsUpgrade: false };
  if (!isHashedPassword(stored)) {
    return { ok: timingSafeEqual(stored, password), needsUpgrade: true };
  }
  const parts = stored.split('$');
  if (parts.length !== 3) return { ok: false, needsUpgrade: false };
  const [, salt, expected] = parts;
  if (!salt || !expected) return { ok: false, needsUpgrade: false };
  // 校验盐格式，避免 hexToBytes 抛错
  if (!/^[0-9a-f]+$/i.test(salt) || salt.length % 2 !== 0) {
    return { ok: false, needsUpgrade: false };
  }
  void hexToBytes;
  const actual = await sha256Hex(`${salt}:${password}`);
  return { ok: timingSafeEqual(actual, expected), needsUpgrade: false };
}
