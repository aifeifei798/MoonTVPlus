'use client';

import { useCallback, useEffect, useState } from 'react';

function readStoredValue<T>(key: string, defaultValue: T): T {
  if (typeof window === 'undefined') return defaultValue;
  try {
    const raw = window.localStorage.getItem(key);
    return raw === null ? defaultValue : (JSON.parse(raw) as T);
  } catch {
    return defaultValue;
  }
}

/**
 * SSR 安全的 localStorage 状态 hook。
 * - 服务端/首屏渲染返回 defaultValue（与 SSR 输出一致，避免 hydration mismatch）
 * - 挂载后读取真实存储值；JSON 解析失败时回退默认值
 * - 监听 storage 事件，多标签页同步
 */
export function useLocalStorage<T>(
  key: string,
  defaultValue: T,
): [T, (value: T | ((prev: T) => T)) => void] {
  const [value, setValue] = useState<T>(defaultValue);

  useEffect(() => {
    setValue(readStoredValue(key, defaultValue));
    const onStorage = (event: StorageEvent) => {
      if (event.key === key) {
        setValue(readStoredValue(key, defaultValue));
      }
    };
    window.addEventListener('storage', onStorage);
    return () => window.removeEventListener('storage', onStorage);
    // defaultValue 要求传入稳定值（字面量/常量），避免重复订阅
  }, [key, defaultValue]);

  const setStoredValue = useCallback(
    (next: T | ((prev: T) => T)) => {
      setValue((prev) => {
        const resolved =
          typeof next === 'function' ? (next as (prev: T) => T)(prev) : next;
        try {
          window.localStorage.setItem(key, JSON.stringify(resolved));
        } catch {
          // 配额不足或隐私模式：仅保留内存值
        }
        return resolved;
      });
    },
    [key],
  );

  return [value, setStoredValue];
}
