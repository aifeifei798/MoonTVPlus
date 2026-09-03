import { isAuthExpired, verifySignature } from './auth';

describe('auth helpers', () => {
  it('签名可生成并验签', async () => {
    const { generateSignature } = await import('./auth');
    const sig = await generateSignature('alice:user:123', 'secret');
    expect(sig).toMatch(/^[0-9a-f]{64}$/);
    await expect(verifySignature('alice:user:123', sig, 'secret')).resolves.toBe(
      true
    );
    await expect(
      verifySignature('alice:user:123', sig, 'wrong')
    ).resolves.toBe(false);
  });

  it('过期判断覆盖未来与超期时间戳', () => {
    expect(isAuthExpired(undefined)).toBe(true);
    expect(isAuthExpired(Date.now())).toBe(false);
    expect(isAuthExpired(Date.now() - 8 * 24 * 60 * 60 * 1000)).toBe(true);
  });
});
