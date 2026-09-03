import '@testing-library/jest-dom';
import { webcrypto } from 'crypto';
import { TextDecoder, TextEncoder } from 'util';

// jsdom 未自带 Web 编码 API，补齐以支持 auth 签名等单测
// eslint-disable-next-line no-undef
if (typeof global.TextEncoder === 'undefined') {
  // eslint-disable-next-line no-undef
  global.TextEncoder = TextEncoder;
}
// eslint-disable-next-line no-undef
if (typeof global.TextDecoder === 'undefined') {
  // eslint-disable-next-line no-undef
  global.TextDecoder = TextDecoder;
}
// jsdom 自带的 crypto 无 subtle，必须强制覆盖为 Node webcrypto
try {
  // eslint-disable-next-line no-undef
  Object.defineProperty(global, 'crypto', {
    value: webcrypto,
    writable: true,
    configurable: true,
  });
} catch {
  // eslint-disable-next-line no-undef
  global.crypto = webcrypto;
}

// Allow router mocks (next/router 兼容层；App Router 请改用 next/navigation mock).
// eslint-disable-next-line no-undef
try {
  jest.mock('next/router', () => require('next-router-mock'));
} catch {
  // next-router-mock 未安装时跳过，避免首个真实测试即失败
}
