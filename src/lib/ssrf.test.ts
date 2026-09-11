import { assertSafeFetchUrl } from './ssrf';

describe('assertSafeFetchUrl', () => {
  it('允许公网 https 地址', () => {
    expect(() =>
      assertSafeFetchUrl('https://example.com/image.png'),
    ).not.toThrow();
  });

  it('拦截内网与回环地址', () => {
    expect(() => assertSafeFetchUrl('http://127.0.0.1/admin')).toThrow();
    expect(() => assertSafeFetchUrl('http://localhost:3000/')).toThrow();
    expect(() => assertSafeFetchUrl('http://169.254.169.254/')).toThrow();
    expect(() => assertSafeFetchUrl('http://192.168.1.1/')).toThrow();
    expect(() => assertSafeFetchUrl('http://10.0.0.1/')).toThrow();
  });

  it('拦截非 http/https 协议', () => {
    expect(() => assertSafeFetchUrl('file:///etc/passwd')).toThrow();
    expect(() => assertSafeFetchUrl('ftp://example.com/a')).toThrow();
  });

  it('拦截变形 IP（十六/十/八进制与混合点分）', () => {
    expect(() => assertSafeFetchUrl('http://0x7f.0.0.1/')).toThrow();
    expect(() => assertSafeFetchUrl('http://2130706433/')).toThrow();
    expect(() => assertSafeFetchUrl('http://0x7f000001/')).toThrow();
    expect(() => assertSafeFetchUrl('http://0177.0.0.1/')).toThrow();
    expect(() => assertSafeFetchUrl('http://0xC0.0xA8.0x01.0x01/')).toThrow();
  });

  it('拦截 IPv6 内网与 IPv4 映射地址', () => {
    expect(() => assertSafeFetchUrl('http://[::1]/')).toThrow();
    expect(() => assertSafeFetchUrl('http://[::ffff:127.0.0.1]/')).toThrow();
    expect(() => assertSafeFetchUrl('http://[fe80::1]/')).toThrow();
    expect(() => assertSafeFetchUrl('http://[fc00::1]/')).toThrow();
  });

  it('拦截携带认证信息的 URL', () => {
    expect(() =>
      assertSafeFetchUrl('https://user:pass@example.com/'),
    ).toThrow();
  });
});
