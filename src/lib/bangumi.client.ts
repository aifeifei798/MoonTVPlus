'use client';

export interface BangumiCalendarData {
  weekday: {
    en: string;
  };
  items: {
    id: number;
    name: string;
    name_cn: string;
    rating: {
      score: number;
    };
    air_date: string;
    images: {
      large: string;
      common: string;
      medium: string;
      small: string;
      grid: string;
    };
  }[];
}

async function fetchWithTimeout(url: string): Promise<Response> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 10000);
  try {
    const response = await fetch(url, { signal: controller.signal });
    clearTimeout(timeoutId);
    return response;
  } catch (error) {
    clearTimeout(timeoutId);
    throw error;
  }
}

function normalizeCalendarData(data: unknown): BangumiCalendarData[] {
  if (!Array.isArray(data)) return [];
  return (data as BangumiCalendarData[])
    .map((item: BangumiCalendarData) => ({
      ...item,
      items: item.items.filter((bangumiItem) => bangumiItem.images),
    }))
    .filter((item) => item.items.length > 0);
}

export async function GetBangumiCalendarData(): Promise<BangumiCalendarData[]> {
  // 优先走本地服务端代理（自带缓存），失败时退回直连 bgm.tv
  try {
    const response = await fetchWithTimeout('/api/bangumi/calendar');
    if (response.ok) {
      const data = await response.json();
      return normalizeCalendarData(data);
    }
  } catch {
    // 本地代理不可用，走直连兜底
  }

  try {
    const response = await fetchWithTimeout('https://api.bgm.tv/calendar');
    if (!response.ok) return [];
    const data = await response.json();
    return normalizeCalendarData(data);
  } catch {
    return [];
  }
}
