/* eslint-disable @typescript-eslint/no-explicit-any,no-console */

import { NextRequest, NextResponse } from 'next/server';

import { getVerifiedAuthInfo } from '@/lib/auth';
import { getAvailableApiSites, getConfig } from '@/lib/config';
import {
  isSourceCircuitOpen,
  recordSourceFailure,
  recordSourceSuccess,
  searchFromApiStream,
} from '@/lib/downstream';
import { yellowWords } from '@/lib/yellow';

export const runtime = 'edge';

export async function GET(request: NextRequest) {
  const storageType = process.env.NEXT_PUBLIC_STORAGE_TYPE || 'localstorage';
  const authInfo = await getVerifiedAuthInfo(request);
  if (!authInfo) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  }
  // localstorage 模式无 username 概念，仅验签；数据库模式必须有 username
  if (storageType !== 'localstorage' && !authInfo.username) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  }

  const { searchParams } = new URL(request.url);
  const query = searchParams.get('q');

  if (!query) {
    return new Response(JSON.stringify({ error: '搜索关键词不能为空' }), {
      status: 400,
      headers: {
        'Content-Type': 'application/json',
      },
    });
  }

  const config = await getConfig();
  const allSites = await getAvailableApiSites(authInfo.username);
  // 熔断中的源本轮跳过，调用方以 source_error 事件透出
  const trippedSites = allSites.filter((site) => isSourceCircuitOpen(site.key));
  const apiSites = allSites.filter((site) => !isSourceCircuitOpen(site.key));

  // 共享状态
  let streamClosed = false;

  // 创建可读流
  const stream = new ReadableStream({
    async start(controller) {
      const encoder = new TextEncoder();

      // 辅助函数：安全地向控制器写入数据
      const safeEnqueue = (data: Uint8Array) => {
        try {
          if (
            streamClosed ||
            (!controller.desiredSize && controller.desiredSize !== 0)
          ) {
            // 流已标记为关闭或控制器已关闭
            return false;
          }
          controller.enqueue(data);
          return true;
        } catch (error) {
          // 控制器已关闭或出现其他错误
          console.warn('Failed to enqueue data:', error);
          streamClosed = true;
          return false;
        }
      };

      // 发送开始事件（总数含熔断源，避免前端一直等待）
      const startEvent = `data: ${JSON.stringify({
        type: 'start',
        query,
        totalSources: apiSites.length + trippedSites.length,
        timestamp: Date.now(),
      })}\n\n`;

      if (!safeEnqueue(encoder.encode(startEvent))) {
        return; // 连接已关闭，提前退出
      }

      // 记录已完成的源数量
      let completedSources = 0;
      const allResults: any[] = [];

      // 熔断源直接以 source_error 透出并计入完成
      for (const site of trippedSites) {
        completedSources++;
        if (!streamClosed) {
          const trippedEvent = `data: ${JSON.stringify({
            type: 'source_error',
            source: site.key,
            sourceName: site.name,
            error: '源暂时不可用，已熔断跳过',
            timestamp: Date.now(),
          })}\n\n`;
          if (!safeEnqueue(encoder.encode(trippedEvent))) {
            streamClosed = true;
            return;
          }
        }
      }

      // 为每个源创建搜索 Promise
      const searchPromises = apiSites.map(async (site) => {
        try {
          // 添加超时控制
          const searchPromise = Promise.race([
            searchFromApiStream(
              site,
              query,
              true,
              undefined,
              config.SiteConfig.SearchDownstreamMaxPage
            ),
            new Promise((_, reject) =>
              setTimeout(() => reject(new Error(`${site.name} timeout`)), 20000)
            ),
          ]);

          const resultsGenerator = (await searchPromise) as AsyncGenerator<
            any[],
            void,
            unknown
          >;

          // 收集所有结果
          const allResults: any[] = [];
          for await (const batch of resultsGenerator) {
            allResults.push(...batch);
          }

          // 过滤黄色内容
          let filteredResults = allResults;
          if (!config.SiteConfig.DisableYellowFilter) {
            filteredResults = allResults.filter((result) => {
              const typeName = result.type_name || '';
              return !yellowWords.some((word: string) =>
                typeName.includes(word)
              );
            });
          }

          // 有产出视为健康，复位该源熔断计数
          if (allResults.length > 0) recordSourceSuccess(site.key);

          // 发送该源的搜索结果
          completedSources++;

          if (!streamClosed) {
            const sourceEvent = `data: ${JSON.stringify({
              type: 'source_result',
              source: site.key,
              sourceName: site.name,
              results: filteredResults,
              timestamp: Date.now(),
            })}\n\n`;

            if (!safeEnqueue(encoder.encode(sourceEvent))) {
              streamClosed = true;
              return; // 连接已关闭，停止处理
            }
          }

          if (filteredResults.length > 0) {
            allResults.push(...filteredResults);
          }
        } catch (error) {
          console.warn(`搜索失败 ${site.name}:`, error);
          // 仅传输层错误计入熔断（超时/请求失败/网络错误）
          const msg = error instanceof Error ? error.message : '';
          if (
            msg === '请求超时' ||
            msg === '请求失败' ||
            msg.includes('网络错误') ||
            msg.endsWith('timeout')
          ) {
            recordSourceFailure(site.key);
          }

          // 发送源错误事件
          completedSources++;

          if (!streamClosed) {
            const errorEvent = `data: ${JSON.stringify({
              type: 'source_error',
              source: site.key,
              sourceName: site.name,
              error: error instanceof Error ? error.message : '搜索失败',
              timestamp: Date.now(),
            })}\n\n`;

            if (!safeEnqueue(encoder.encode(errorEvent))) {
              streamClosed = true;
              return; // 连接已关闭，停止处理
            }
          }
        }

        // 检查是否所有源都已完成（含熔断源）
        if (completedSources === apiSites.length + trippedSites.length) {
          if (!streamClosed) {
            // 发送最终完成事件
            const completeEvent = `data: ${JSON.stringify({
              type: 'complete',
              totalResults: allResults.length,
              completedSources,
              timestamp: Date.now(),
            })}\n\n`;

            if (safeEnqueue(encoder.encode(completeEvent))) {
              // 只有在成功发送完成事件后才关闭流
              try {
                controller.close();
              } catch (error) {
                console.warn('Failed to close controller:', error);
              }
            }
          }
        }
      });

      // 等待所有搜索完成
      await Promise.allSettled(searchPromises);
    },

    cancel() {
      // 客户端断开连接时，标记流已关闭
      streamClosed = true;
      console.log('Client disconnected, cancelling search stream');
    },
  });

  // 返回流式响应（同源 SSE，不再通配 CORS，避免登录态数据被任意站点读取）
  return new Response(stream, {
    headers: {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache, no-store',
      Connection: 'keep-alive',
      'X-Content-Type-Options': 'nosniff',
    },
  });
}
