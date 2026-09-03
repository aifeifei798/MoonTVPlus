import { assertSafeFetchUrl } from './ssrf';

describe('assertSafeFetchUrl', () => {
  it('允许公网 https 地址', () => {
    expect(() =>
      assertSafeFetchUrl('https://example.com/image.png')
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
});
