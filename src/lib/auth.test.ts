import { isAuthExpired, isSecureRequest, verifySignature } from './auth';

describe('auth helpers', () => {
  it('签名可生成并验签', async () => {
    const { generateSignature } = await import('./auth');
    const sig = await generateSignature('alice:user:123', 'secret');
    expect(sig).toMatch(/^[0-9a-f]{64}$/);
    await expect(
      verifySignature('alice:user:123', sig, 'secret')
    ).resolves.toBe(true);
    await expect(verifySignature('alice:user:123', sig, 'wrong')).resolves.toBe(
      false
    );
  });

  it('Secure 只在 HTTPS（含反代头）下开启，http 明文绝不开', () => {
    const req = (url: string, proto?: string) =>
      ({
        url,
        headers: new Headers(proto ? { 'x-forwarded-proto': proto } : {}),
      } as unknown as Request);
    // http 明文：必须 false，否则浏览器丢弃 Cookie 导致登录死循环
    expect(isSecureRequest(req('http://192.168.1.10:3000/api/login'))).toBe(
      false
    );
    expect(isSecureRequest(req('http://x:3000/api/login', 'http'))).toBe(false);
    // https 直连与反代：true
    expect(isSecureRequest(req('https://fei.lat/api/login'))).toBe(true);
    expect(isSecureRequest(req('http://x:3000/api/login', 'https'))).toBe(true);
  });

  it('过期判断覆盖未来与超期时间戳', () => {
    expect(isAuthExpired(undefined)).toBe(true);
    expect(isAuthExpired(Date.now())).toBe(false);
    expect(isAuthExpired(Date.now() - 8 * 24 * 60 * 60 * 1000)).toBe(true);
  });
});
