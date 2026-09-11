// 统一日志门面：生产默认静默 info/log，warn/error 保留（可经 LOG_LEVEL 调整）
// 替代散落的 console.* + 文件级 eslint-disable no-console
/* eslint-disable no-console */
type Level = 'debug' | 'info' | 'warn' | 'error' | 'silent';

function getLevel(): Level {
  const raw = (process.env.LOG_LEVEL || '').toLowerCase();
  if (
    raw === 'debug' ||
    raw === 'info' ||
    raw === 'warn' ||
    raw === 'error' ||
    raw === 'silent'
  ) {
    return raw;
  }
  return process.env.NODE_ENV === 'production' ? 'warn' : 'debug';
}

const order: Record<Level, number> = {
  debug: 0,
  info: 1,
  warn: 2,
  error: 3,
  silent: 4,
};

function enabled(level: Level): boolean {
  return order[level] >= order[getLevel()];
}

export const logger = {
  debug(...args: unknown[]): void {
    if (enabled('debug')) console.debug(...args);
  },
  info(...args: unknown[]): void {
    if (enabled('info')) console.info(...args);
  },
  log(...args: unknown[]): void {
    if (enabled('info')) console.log(...args);
  },
  warn(...args: unknown[]): void {
    if (enabled('warn')) console.warn(...args);
  },
  error(...args: unknown[]): void {
    if (enabled('error')) console.error(...args);
  },
};
