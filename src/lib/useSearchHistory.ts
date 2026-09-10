'use client';

import { useEffect, useState } from 'react';

import { getSearchHistory, subscribeToDataUpdates } from '@/lib/db.client';

/**
 * 搜索历史：首屏空数组，挂载后加载并订阅 `searchHistoryUpdated` 更新。
 * 替代各组件内重复的 getSearchHistory + subscribe 样板。
 */
export function useSearchHistory(): string[] {
  const [history, setHistory] = useState<string[]>([]);

  useEffect(() => {
    let cancelled = false;
    getSearchHistory().then((h) => {
      if (!cancelled) setHistory(h);
    });
    const unsubscribe = subscribeToDataUpdates(
      'searchHistoryUpdated',
      setHistory,
    );
    return () => {
      cancelled = true;
      unsubscribe();
    };
  }, []);

  return history;
}
