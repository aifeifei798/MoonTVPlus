
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

/**
 * 官源 `api.bgm.tv` 与 `lain.bgm.tv` 一样被中间设备间歇性重置，所以日历本身也
 * 需要兜底：`fxbgm.tv` 是社区镜像，`/calendar` 返回与官源一致的 JSON（图片仍指向
 * lain.bgm.tv，服务端 `/api/image-proxy` 会换镜像）。每个候选 10s 超时，首个
 * 成功者胜出；全败则返回空列表，页面显示占位而不是卡死。
 */
const CALENDAR_ENDPOINTS = [
  'https://api.bgm.tv/calendar',
  'https://fxbgm.tv/calendar',
];

const CALENDAR_TIMEOUT_MS = 10000;

export async function GetBangumiCalendarData(): Promise<BangumiCalendarData[]> {
  for (const endpoint of CALENDAR_ENDPOINTS) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), CALENDAR_TIMEOUT_MS);
    try {
      const response = await fetch(endpoint, { signal: controller.signal });
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }

      const data = await response.json();
      if (!Array.isArray(data)) {
        continue;
      }

      return data.map((item: BangumiCalendarData) => ({
        ...item,
        items: item.items.filter((bangumiItem) => bangumiItem.images),
      }));
    } catch (error) {
      console.error(`获取 Bangumi 日历失败 (${endpoint}):`, error);
    } finally {
      clearTimeout(timer);
    }
  }

  return [];
}
