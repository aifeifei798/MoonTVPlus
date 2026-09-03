/* eslint-disable no-console */

import { NextRequest, NextResponse } from 'next/server';

import { getConfig } from '@/lib/config';
import { db } from '@/lib/db';
import { fetchVideoDetail } from '@/lib/fetchVideoDetail';
import { SearchResult } from '@/lib/types';

export const runtime = 'edge';

// 定时任务并发度：CRON_CONCURRENCY，默认 5，钳制 1~20
function getCronConcurrency(): number {
  const n = Number(process.env.CRON_CONCURRENCY || 5);
  if (!Number.isFinite(n)) return 5;
  return Math.min(20, Math.max(1, Math.floor(n)));
}

// 非活跃用户阈值（天）：CRON_ACTIVE_DAYS，默认 7，0 表示不过滤
function getCronActiveDays(): number {
  const raw = process.env.CRON_ACTIVE_DAYS;
  const n = raw == null || raw === '' ? 7 : Number(raw);
  if (!Number.isFinite(n) || n < 0) return 7;
  return n;
}

// 简单并发池（无外部依赖）
async function runWithConcurrency<T>(
  items: T[],
  limit: number,
  fn: (item: T) => Promise<void>
): Promise<void> {
  let cursor = 0;
  const workers = Array.from(
    { length: Math.max(1, Math.min(limit, items.length)) },
    async () => {
      while (cursor < items.length) {
        const item = items[cursor++];
        await fn(item);
      }
    }
  );
  await Promise.all(workers);
}

interface RefreshTask {
  user: string;
  kind: 'record' | 'favorite';
  key: string;
  source: string;
  id: string;
  title: string;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  data: any;
}

export async function GET(request: NextRequest) {
  // 纵深防御：中间件已校验 CRON_SECRET，此处再校验一次，避免中间件未覆盖时被匿名触发
  const secret = process.env.CRON_SECRET || '';
  if (secret) {
    const provided =
      request.headers.get('x-cron-secret') ||
      new URL(request.url).searchParams.get('secret') ||
      '';
    if (provided !== secret) {
      return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
    }
  }
  console.log(request.url);
  try {
    console.log('Cron job triggered:', new Date().toISOString());

    refreshRecordAndFavorites();

    return NextResponse.json({
      success: true,
      message: 'Cron job executed successfully',
      timestamp: new Date().toISOString(),
    });
  } catch (error) {
    console.error('Cron job failed:', error);

    return NextResponse.json(
      {
        success: false,
        message: 'Cron job failed',
        error: error instanceof Error ? error.message : 'Unknown error',
        timestamp: new Date().toISOString(),
      },
      { status: 500 }
    );
  }
}

async function refreshRecordAndFavorites() {
  if (
    (process.env.NEXT_PUBLIC_STORAGE_TYPE || 'localstorage') === 'localstorage'
  ) {
    console.log('跳过刷新：当前使用 localstorage 存储模式');
    return;
  }

  try {
    const users = await db.getAllUsers();
    if (process.env.USERNAME && !users.includes(process.env.USERNAME)) {
      users.push(process.env.USERNAME);
    }
    // 函数级缓存：key 为 `${source}+${id}`，值为 Promise<VideoDetail | null>
    const detailCache = new Map<string, Promise<SearchResult | null>>();

    // 获取详情 Promise（带缓存和错误处理）
    const getDetail = async (
      source: string,
      id: string,
      fallbackTitle: string
    ): Promise<SearchResult | null> => {
      const key = `${source}+${id}`;
      let promise = detailCache.get(key);
      if (!promise) {
        promise = fetchVideoDetail({
          source,
          id,
          fallbackTitle: fallbackTitle.trim(),
          timeout: 30000, // 定时任务使用30秒超时
        })
          .then((detail) => {
            // 成功时才缓存结果
            const successPromise = Promise.resolve(detail);
            detailCache.set(key, successPromise);
            return detail;
          })
          .catch((err) => {
            console.error(`获取视频详情失败 (${source}+${id}):`, err);
            return null;
          });
      }
      return promise;
    };

    // 跳过被封禁用户与长期不活跃用户（lastOnline 缺失视为活跃，兼容旧客户端）
    const userMeta = new Map<
      string,
      { banned?: boolean; lastOnline?: number }
    >();
    try {
      const adminConfig = await getConfig();
      for (const u of adminConfig.UserConfig.Users || []) {
        userMeta.set(u.username, {
          banned: u.banned,
          lastOnline: u.lastOnline,
        });
      }
    } catch (err) {
      console.error('读取用户元信息失败，不过滤用户:', err);
    }
    const activeDays = getCronActiveDays();
    const activeUsers = users.filter((user) => {
      const meta = userMeta.get(user);
      if (meta?.banned) {
        console.log(`跳过被封禁用户: ${user}`);
        return false;
      }
      if (
        activeDays > 0 &&
        meta?.lastOnline &&
        Date.now() - meta.lastOnline > activeDays * 24 * 60 * 60 * 1000
      ) {
        console.log(`跳过长期不活跃用户: ${user}`);
        return false;
      }
      return true;
    });

    // 收集任务：DB 读取仍按用户串行（便宜），上游详情请求走并发池（贵）
    const tasks: RefreshTask[] = [];
    for (const user of activeUsers) {
      try {
        const playRecords = await db.getAllPlayRecords(user);
        for (const [key, record] of Object.entries(playRecords)) {
          const [source, id] = key.split('+');
          if (!source || !id) {
            console.warn(`跳过无效的播放记录键: ${key}`);
            continue;
          }
          tasks.push({
            user,
            kind: 'record',
            key,
            source,
            id,
            title: record.title || '',
            data: record,
          });
        }
      } catch (err) {
        console.error(`获取用户播放记录失败 (${user}):`, err);
      }

      try {
        const favorites = await db.getAllFavorites(user);
        for (const [key, fav] of Object.entries(favorites)) {
          const [source, id] = key.split('+');
          if (!source || !id) {
            console.warn(`跳过无效的收藏键: ${key}`);
            continue;
          }
          tasks.push({
            user,
            kind: 'favorite',
            key,
            source,
            id,
            title: fav.title || '',
            data: fav,
          });
        }
      } catch (err) {
        console.error(`获取用户收藏失败 (${user}):`, err);
      }
    }

    const concurrency = getCronConcurrency();
    console.log(
      `开始刷新: ${activeUsers.length} 用户, ${tasks.length} 条目, 并发 ${concurrency}`
    );
    const stats = new Map<string, { processed: number; total: number }>();
    const statKey = (t: RefreshTask) => `${t.user}:${t.kind}`;
    for (const t of tasks) {
      const s = stats.get(statKey(t)) || { processed: 0, total: 0 };
      s.total++;
      stats.set(statKey(t), s);
    }

    await runWithConcurrency(tasks, concurrency, async (task) => {
      const { user, kind, key, source, id, title, data } = task;
      try {
        const detail = await getDetail(source, id, title);
        if (!detail) {
          console.warn(
            `跳过无法获取详情的${
              kind === 'record' ? '播放记录' : '收藏'
            }: ${key}`
          );
          return;
        }

        const episodeCount = detail.episodes?.length || 0;
        if (episodeCount > 0 && episodeCount !== data.total_episodes) {
          if (kind === 'record') {
            await db.savePlayRecord(user, source, id, {
              title: detail.title || data.title,
              source_name: data.source_name,
              cover: detail.poster || data.cover,
              index: data.index,
              total_episodes: episodeCount,
              play_time: data.play_time,
              year: detail.year || data.year,
              total_time: data.total_time,
              save_time: data.save_time,
              search_title: data.search_title,
            });
            console.log(
              `更新播放记录: ${data.title} (${data.total_episodes} -> ${episodeCount})`
            );
          } else {
            await db.saveFavorite(user, source, id, {
              title: detail.title || data.title,
              source_name: data.source_name,
              cover: detail.poster || data.cover,
              year: detail.year || data.year,
              total_episodes: episodeCount,
              save_time: data.save_time,
              search_title: data.search_title,
            });
            console.log(
              `更新收藏: ${data.title} (${data.total_episodes} -> ${episodeCount})`
            );
          }
        }

        stats.get(statKey(task))!.processed++;
      } catch (err) {
        console.error(
          `处理${kind === 'record' ? '播放记录' : '收藏'}失败 (${key}):`,
          err
        );
        // 继续处理下一个
      }
    });

    for (const [k, s] of stats) {
      console.log(`处理完成 ${k}: ${s.processed}/${s.total}`);
    }
    console.log('刷新播放记录/收藏任务完成');
  } catch (err) {
    console.error('刷新播放记录/收藏任务启动失败', err);
  }
}
