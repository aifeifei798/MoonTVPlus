/* eslint-disable @typescript-eslint/no-explicit-any,no-console */

import { NextRequest } from 'next/server';

import { getVerifiedAuthInfo } from '@/lib/auth';
import { getAvailableApiSites, getCacheTime, getConfig } from '@/lib/config';
import {
  isSourceCircuitOpen,
  recordSourceFailure,
  recordSourceSuccess,
  searchFromApiStream,
} from '@/lib/downstream';
import { yellowWords } from '@/lib/yellow';

export const runtime = 'edge';

export async function GET(request: NextRequest) {
  // 检查是否为本地存储模式
  const storageType = process.env.NEXT_PUBLIC_STORAGE_TYPE || 'localstorage';
  const isLocalStorage = storageType === 'localstorage';

  let authInfo = null;
  if (!isLocalStorage) {
    // 非本地存储模式才需要认证
    authInfo = await getVerifiedAuthInfo(request);
    if (!authInfo || !authInfo.username) {
      return new Response(JSON.stringify({ error: 'Unauthorized' }), {
        status: 401,
        headers: { 'Content-Type': 'application/json' },
      });
    }
  }

  const { searchParams } = new URL(request.url);
  const query = searchParams.get('q');
  const streamParam = searchParams.get('stream');
  const enableStream = streamParam ? streamParam !== '0' : false; // 无该参数关闭流式
  const timeoutParam = searchParams.get('timeout');
  const timeout = timeoutParam ? parseInt(timeoutParam, 10) * 1000 : undefined; // 转换为毫秒

  const config = await getConfig();

  // 获取用户可用的搜索源
  let apiSites = await getAvailableApiSites(authInfo?.username);

  // 如果指定了搜索源，只使用选中的搜索源
  const selectedSourcesParam = searchParams.get('sources');
  if (selectedSourcesParam) {
    const selectedSources = selectedSourcesParam.split(',');
    apiSites = apiSites.filter((site) => selectedSources.includes(site.key));
  }

  // 熔断中的源直接跳过，不占用 20s 超时；信息进 failedSources，前端失败源面板可见
  const tripped = apiSites.filter((site) => isSourceCircuitOpen(site.key));
  if (tripped.length > 0) {
    apiSites = apiSites.filter((site) => !isSourceCircuitOpen(site.key));
  }
  const trippedFailures = tripped.map((site) => ({
    name: site.name,
    key: site.key,
    error: '源暂时不可用，已熔断跳过',
  }));

  const encoder = new TextEncoder();
  const { readable, writable } = new TransformStream();
  const writer = writable.getWriter();

  if (!query) {
    // 空查询，明确不缓存
    return new Response(JSON.stringify({ results: [] }), {
      headers: {
        'Content-Type': 'application/json',
        'Cache-Control': 'no-store, no-cache, must-revalidate',
        Pragma: 'no-cache',
        Expires: '0',
      },
    });
  }

  // 安全写入与断连处理
  let shouldStop = false;
  const abortSignal = (request as any).signal as AbortSignal | undefined;
  abortSignal?.addEventListener('abort', () => {
    shouldStop = true;
    try {
      writer.close();
    } catch {
      // ignore
    }
  });

  const safeWrite = async (obj: unknown) => {
    if (shouldStop || abortSignal?.aborted) return false;
    try {
      await writer.write(encoder.encode(JSON.stringify(obj) + '\n'));
      return true;
    } catch {
      shouldStop = true;
      return false;
    }
  };

  // -------------------------
  // 非流式：并发
  // -------------------------
  if (!enableStream) {
    const tasks = apiSites.map(async (site) => {
      const siteResults: any[] = [];
      let hasResults = false;
      try {
        const generator = searchFromApiStream(
          site,
          query,
          true,
          timeout,
          config.SiteConfig.SearchDownstreamMaxPage
        );
        for await (const pageResults of generator) {
          let filteredResults = pageResults;
          if (filteredResults.length !== 0) {
            hasResults = true;
          }
          if (!config.SiteConfig.DisableYellowFilter) {
            filteredResults = pageResults.filter((result) => {
              const typeName = result.type_name || '';
              return !yellowWords.some((word) => typeName.includes(word));
            });
          }
          if (hasResults && filteredResults.length === 0) {
            throw new Error('结果被过滤');
          }
          siteResults.push(...filteredResults);
        }
        if (!hasResults) {
          throw new Error('无搜索结果');
        }
        recordSourceSuccess(site.key);
        return { siteResults, failed: null };
      } catch (err: any) {
        let errorMessage = err.message || '未知的错误';

        // 根据错误类型提供更具体的错误信息；仅传输层错误计入熔断
        if (err.message === '请求超时') {
          errorMessage = '请求超时';
          recordSourceFailure(site.key);
        } else if (err.message === '请求失败') {
          errorMessage = '请求失败';
          recordSourceFailure(site.key);
        } else if (err.message?.includes('网络错误')) {
          errorMessage = '网络错误';
          recordSourceFailure(site.key);
        }

        return {
          siteResults: [],
          failed: { name: site.name, key: site.key, error: errorMessage },
        };
      }
    });

    const results = await Promise.all(tasks);
    const aggregatedResults = results.flatMap((r) => r.siteResults);
    const failedSources = [
      ...trippedFailures,
      ...results.filter((r) => r.failed).map((r) => r.failed),
    ];

    if (aggregatedResults.length === 0) {
      const body = { results: [], failedSources };
      return new Response(JSON.stringify(body), {
        headers: {
          'Content-Type': 'application/json; charset=utf-8',
          'Cache-Control': 'no-store, no-cache, must-revalidate',
          Pragma: 'no-cache',
          Expires: '0',
        },
      });
    } else {
      const cacheTime = await getCacheTime();
      const body = { results: aggregatedResults, failedSources };
      return new Response(JSON.stringify(body), {
        headers: {
          'Content-Type': 'application/json; charset=utf-8',
          'Cache-Control': `private, max-age=${cacheTime}`,
        },
      });
    }
  }

  // -------------------------
  // 流式：并发
  // -------------------------
  (async () => {
    const aggregatedResults: any[] = [];
    const failedSources: { name: string; key: string; error: string }[] = [
      ...trippedFailures,
    ];
    if (trippedFailures.length > 0) {
      await safeWrite({ failedSources });
    }

    const tasks = apiSites.map(async (site) => {
      try {
        const generator = searchFromApiStream(
          site,
          query,
          true,
          timeout,
          config.SiteConfig.SearchDownstreamMaxPage
        );
        let hasResults = false;

        for await (const pageResults of generator) {
          let filteredResults = pageResults;
          if (filteredResults.length !== 0) {
            hasResults = true;
          }
          if (!config.SiteConfig.DisableYellowFilter) {
            filteredResults = pageResults.filter((result) => {
              const typeName = result.type_name || '';
              return !yellowWords.some((word) => typeName.includes(word));
            });
          }

          if (hasResults && filteredResults.length === 0) {
            failedSources.push({
              name: site.name,
              key: site.key,
              error: '结果被过滤',
            });
            await safeWrite({ failedSources });
            return;
          }

          aggregatedResults.push(...filteredResults);
          if (
            !(await safeWrite({ site: site.key, pageResults: filteredResults }))
          ) {
            return;
          }
        }

        if (!hasResults) {
          failedSources.push({
            name: site.name,
            key: site.key,
            error: '无搜索结果',
          });
          await safeWrite({ failedSources });
        } else {
          recordSourceSuccess(site.key);
        }
      } catch (err: any) {
        console.warn(`搜索失败 ${site.name}:`, err.message);
        let errorMessage = err.message || '未知的错误';

        // 根据错误类型提供更具体的错误信息；仅传输层错误计入熔断
        if (err.message === '请求超时') {
          errorMessage = '请求超时';
          recordSourceFailure(site.key);
        } else if (err.message === '请求失败') {
          errorMessage = '请求失败';
          recordSourceFailure(site.key);
        } else if (err.message.includes('网络错误')) {
          errorMessage = '网络错误';
          recordSourceFailure(site.key);
        }

        failedSources.push({
          name: site.name,
          key: site.key,
          error: errorMessage,
        });
        await safeWrite({ failedSources });
      }
    });

    // 等所有 site 跑完
    await Promise.allSettled(tasks);

    if (failedSources.length > 0) {
      await safeWrite({ failedSources });
    }
    await safeWrite({ aggregatedResults });

    try {
      await writer.close();
    } catch {
      // ignore
    }
  })();

  const cacheTime = await getCacheTime();
  return new Response(readable, {
    headers: {
      'Content-Type': 'application/json; charset=utf-8',
      'Cache-Control': `private, max-age=${cacheTime}`,
    },
  });
}
