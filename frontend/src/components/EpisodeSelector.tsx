/* eslint-disable @next/next/no-img-element */

import { useRouter } from '@/shims/navigation';
import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';

import { SearchResult } from '@/lib/types';
import { getVideoResolutionFromM3u8 } from '@/lib/utils';

// 定义视频信息类型
interface VideoInfo {
  quality: string;
  loadSpeed: string;
  pingTime: number;
  hasError?: boolean; // 添加错误状态标识
}

// 模块级探测缓存：按剧集 URL 去重并发 + 缓存解析结果，10 分钟 TTL。
// key: episode URL, value: { promise(在途) 或 info(已解析), timestamp }
const PROBE_CACHE_TTL_MS = 10 * 60 * 1000;
const probeCache = new Map<
  string,
  { promise: Promise<VideoInfo> | null; info?: VideoInfo; ts: number }
>();

function getCachedProbe(episodeUrl: string): Promise<VideoInfo> | VideoInfo | null {
  const entry = probeCache.get(episodeUrl);
  if (!entry) return null;
  if (Date.now() - entry.ts > PROBE_CACHE_TTL_MS) {
    probeCache.delete(episodeUrl);
    return null;
  }
  if (entry.info) return entry.info;
  if (entry.promise) return entry.promise;
  return null;
}

/** 换源列表里每个源的唯一标识（与 videoInfoMap 的 key 一致） */
function sourceKeyOf(source: SearchResult): string {
  return `${source.source}-${source.id}`;
}

interface EpisodeSelectorProps {
  /** 总集数 */
  totalEpisodes: number;
  /** 剧集标题 */
  episodes_titles: string[];
  /** 每页显示多少集，默认 50 */
  episodesPerPage?: number;
  /** 当前选中的集数（1 开始） */
  value?: number;
  /** 用户点击选集后的回调 */
  onChange?: (episodeNumber: number) => void;
  /** 换源相关 */
  onSourceChange?: (source: string, id: string, title: string) => void;
  currentSource?: string;
  currentId?: string;
  videoTitle?: string;
  videoYear?: string;
  availableSources?: SearchResult[];
  sourceSearchLoading?: boolean;
  sourceSearchError?: string | null;
  /** 预计算的测速结果，避免重复测速 */
  precomputedVideoInfo?: Map<string, VideoInfo>;
  /** 优选播放源相关 */
  preferBestSource?: (sources: SearchResult[], isCancelled?: () => boolean) => Promise<SearchResult>;
  setLoading: (loading: boolean) => void;
  /** 设置视频是否正在加载中的状态 */
  setIsVideoLoading: (loading: boolean) => void;
  /** 设置视频加载阶段的状态 */
  setVideoLoadingStage: (stage: 'initing' | 'sourceChanging' | 'optimizing') => void;
}

/**
 * 选集组件，支持分页、自动滚动聚焦当前分页标签，以及换源功能。
 */
const EpisodeSelector: React.FC<EpisodeSelectorProps> = ({
  totalEpisodes,
  episodes_titles,
  episodesPerPage = 50,
  value = 1,
  onChange,
  onSourceChange,
  currentSource,
  currentId,
  videoTitle,
  availableSources = [],
  sourceSearchLoading = false,
  sourceSearchError = null,
  precomputedVideoInfo,
  preferBestSource,
  setLoading,
  setIsVideoLoading,
  setVideoLoadingStage
}) => {
  const router = useRouter();
  const pageCount = Math.ceil(totalEpisodes / episodesPerPage);

  // 存储每个源的视频信息
  const [videoInfoMap, setVideoInfoMap] = useState<Map<string, VideoInfo>>(
    new Map()
  );
  const [attemptedSources, setAttemptedSources] = useState<Set<string>>(
    new Set()
  );

  // 使用 ref 来避免闭包问题
  const attemptedSourcesRef = useRef<Set<string>>(new Set());
  const videoInfoMapRef = useRef<Map<string, VideoInfo>>(new Map());

  // 同步状态到 ref
  useEffect(() => {
    attemptedSourcesRef.current = attemptedSources;
  }, [attemptedSources]);

  useEffect(() => {
    videoInfoMapRef.current = videoInfoMap;
  }, [videoInfoMap]);

  // 主要的 tab 状态：'episodes' 或 'sources'
  // 当只有一集时默认展示 "换源"，并隐藏 "选集" 标签
  const [activeTab, setActiveTab] = useState<'episodes' | 'sources'>(
    totalEpisodes > 1 ? 'episodes' : 'sources'
  );

  // 当前分页索引（0 开始）
  const initialPage = Math.floor((value - 1) / episodesPerPage);
  const [currentPage, setCurrentPage] = useState<number>(initialPage);

  // 是否倒序显示
  const [descending, setDescending] = useState<boolean>(false);
  // 优选播放源加载状态
  const [isOptimizing, setIsOptimizing] = useState<boolean>(false);
  // 取消优选标志
  const cancelOptimizationRef = useRef<boolean>(false);

  // 根据 descending 状态计算实际显示的分页索引
  const displayPage = useMemo(() => {
    if (descending) {
      return pageCount - 1 - currentPage;
    }
    return currentPage;
  }, [currentPage, descending, pageCount]);

  // 获取视频信息的函数 - 移除 attemptedSources 依赖避免不必要的重新创建
  // 节流：命中模块级缓存(episode URL, 10min TTL)则直接复用；在途请求去重。
  const getVideoInfo = useCallback(async (source: SearchResult) => {
    const sourceKey = `${source.source}-${source.id}`;

    // 使用 ref 获取最新的状态，避免闭包问题
    if (attemptedSourcesRef.current.has(sourceKey)) {
      return;
    }

    // 获取第一集的URL
    if (!source.episodes || source.episodes.length === 0) {
      return;
    }
    const episodeUrl =
      source.episodes.length > 1 ? source.episodes[1] : source.episodes[0];

    // 标记为已尝试
    setAttemptedSources((prev) => new Set(prev).add(sourceKey));

    // 先查模块级缓存（按 episode URL）
    const cached = getCachedProbe(episodeUrl);
    if (cached) {
      try {
        const info = cached instanceof Promise ? await cached : cached;
        setVideoInfoMap((prev) => new Map(prev).set(sourceKey, info));
      } catch (error) {
        setVideoInfoMap((prev) =>
          new Map(prev).set(sourceKey, {
            quality: '错误',
            loadSpeed: '未知',
            pingTime: 0,
            hasError: true,
          })
        );
      }
      return;
    }

    try {
      const promise = getVideoResolutionFromM3u8(episodeUrl);
      probeCache.set(episodeUrl, { promise, ts: Date.now() });
      const info = await promise;
      probeCache.set(episodeUrl, { promise: null, info, ts: Date.now() });
      setVideoInfoMap((prev) => new Map(prev).set(sourceKey, info));
    } catch (error) {
      probeCache.delete(episodeUrl);
      // 失败时保存错误状态
      setVideoInfoMap((prev) =>
        new Map(prev).set(sourceKey, {
          quality: '错误',
          loadSpeed: '未知',
          pingTime: 0,
          hasError: true,
        })
      );
    }
  }, []);

  // 当有预计算结果时，先合并到videoInfoMap中
  useEffect(() => {
    if (precomputedVideoInfo && precomputedVideoInfo.size > 0) {
      // 原子性地更新两个状态，避免时序问题
      setVideoInfoMap((prev) => {
        const newMap = new Map(prev);
        precomputedVideoInfo.forEach((value, key) => {
          newMap.set(key, value);
        });
        return newMap;
      });

      setAttemptedSources((prev) => {
        const newSet = new Set(prev);
        precomputedVideoInfo.forEach((info, key) => {
          if (!info.hasError) {
            newSet.add(key);
          }
        });
        return newSet;
      });

      // 同步更新 ref，确保 getVideoInfo 能立即看到更新
      precomputedVideoInfo.forEach((info, key) => {
        if (!info.hasError) {
          attemptedSourcesRef.current.add(key);
        }
      });
    }
  }, [precomputedVideoInfo]);

  // 读取本地“优选和测速”开关，默认开启
  const [optimizationEnabled] = useState<boolean>(() => {
    if (typeof window !== 'undefined') {
      const saved = localStorage.getItem('enableOptimization');
      if (saved !== null) {
        try {
          return JSON.parse(saved);
        } catch {
          /* ignore */
        }
      }
    }
    return true;
  });

  // 换源 tab 打开时探测列表里的源：网格一格一个源，没有测速就只能显示 "..."，
  // 所以一屏的源都要有速度。并发 3（每个探测 4s 超时，模块级缓存按剧集 URL 去重，
  // 10min TTL），当前生效源先探测，其余按列表顺序。
  useEffect(() => {
    if (
      !optimizationEnabled ||
      activeTab !== 'sources' ||
      availableSources.length === 0
    )
      return;

    const current =
      availableSources.find(
        (s) =>
          s.source?.toString() === currentSource?.toString() &&
          s.id?.toString() === currentId?.toString()
      ) ?? availableSources[0];
    const queue = [current, ...availableSources.filter((s) => s !== current)];

    const PROBE_CONCURRENCY = 3;
    let next = 0;
    const worker = async () => {
      while (next < queue.length) {
        const source = queue[next++];
        if (source) await getVideoInfo(source);
      }
    };
    Promise.all(
      Array.from({ length: Math.min(PROBE_CONCURRENCY, queue.length) }, worker)
    );
    // 依赖项保持与之前一致
  }, [activeTab, availableSources, getVideoInfo, optimizationEnabled, currentSource, currentId]);

  // 升序分页标签
  const categoriesAsc = useMemo(() => {
    return Array.from({ length: pageCount }, (_, i) => {
      const start = i * episodesPerPage + 1;
      const end = Math.min(start + episodesPerPage - 1, totalEpisodes);
      return { start, end };
    });
  }, [pageCount, episodesPerPage, totalEpisodes]);

  // 根据 descending 状态决定分页标签的排序和内容
  const categories = useMemo(() => {
    if (descending) {
      // 倒序时，label 也倒序显示
      return [...categoriesAsc]
        .reverse()
        .map(({ start, end }) => `${end}-${start}`);
    }
    return categoriesAsc.map(({ start, end }) => `${start}-${end}`);
  }, [categoriesAsc, descending]);

  const categoryContainerRef = useRef<HTMLDivElement>(null);
  const buttonRefs = useRef<(HTMLButtonElement | null)[]>([]);

  // 当分页切换时，将激活的分页标签滚动到视口中间
  useEffect(() => {
    const btn = buttonRefs.current[displayPage];
    const container = categoryContainerRef.current;
    if (btn && container) {
      // 手动计算滚动位置，只滚动分页标签容器
      const containerRect = container.getBoundingClientRect();
      const btnRect = btn.getBoundingClientRect();
      const scrollLeft = container.scrollLeft;

      // 计算按钮相对于容器的位置
      const btnLeft = btnRect.left - containerRect.left + scrollLeft;
      const btnWidth = btnRect.width;
      const containerWidth = containerRect.width;

      // 计算目标滚动位置，使按钮居中
      const targetScrollLeft = btnLeft - (containerWidth - btnWidth) / 2;

      // 平滑滚动到目标位置
      container.scrollTo({
        left: targetScrollLeft,
        behavior: 'smooth',
      });
    }
  }, [displayPage, pageCount]);

  // 处理换源tab点击，只在点击时才搜索
  const handleSourceTabClick = () => {
    setActiveTab('sources');
  };

  const handleCategoryClick = useCallback(
    (index: number) => {
      if (descending) {
        // 在倒序时，需要将显示索引转换为实际索引
        setCurrentPage(pageCount - 1 - index);
      } else {
        setCurrentPage(index);
      }
    },
    [descending, pageCount]
  );

  const handleEpisodeClick = useCallback(
    (episodeNumber: number) => {
      onChange?.(episodeNumber);
    },
    [onChange]
  );

  const handleSourceClick = useCallback(
    (source: SearchResult) => {
      if (!source || !source.source || !source.id) return;
      // 确保传递完整的参数
      onSourceChange?.(
        source.source,
        source.id,
        source.title || source.source_name || ''
      );
    },
    [onSourceChange]
  );

  // 换源列表的显示顺序：当前生效源置顶，之前生效的源沉底。
  // sourceHistoryRef 记录"曾经生效"的源，越近生效的越靠列表底部。
  const sourceHistoryRef = useRef<string[]>([]);
  const lastCurrentKeyRef = useRef<string | null>(null);

  const orderedSources = useMemo(() => {
    const currentKey = `${currentSource ?? ''}-${currentId ?? ''}`;

    // 生效源变化时，把上一个生效源沉到列表底部（去重，避免来回切换堆积）
    const previousKey = lastCurrentKeyRef.current;
    if (previousKey !== null && previousKey !== currentKey) {
      sourceHistoryRef.current = sourceHistoryRef.current.filter(
        (key) => key !== previousKey
      );
      sourceHistoryRef.current.push(previousKey);
    }
    lastCurrentKeyRef.current = currentKey;

    const list = availableSources.slice();
    const current = list.filter((s) => sourceKeyOf(s) === currentKey);
    const history = sourceHistoryRef.current;
    const rest = list.filter((s) => sourceKeyOf(s) !== currentKey);
    const others = rest.filter((s) => !history.includes(sourceKeyOf(s)));
    const sunk = rest
      .filter((s) => history.includes(sourceKeyOf(s)))
      .sort(
        (a, b) =>
          history.indexOf(sourceKeyOf(a)) - history.indexOf(sourceKeyOf(b))
      );

    return [...current, ...others, ...sunk];
  }, [availableSources, currentSource, currentId]);

  const currentStart = currentPage * episodesPerPage + 1;
  const currentEnd = Math.min(
    currentStart + episodesPerPage - 1,
    totalEpisodes
  );

  return (
    <div className='px-4 py-0 h-full bg-black/10 dark:bg-white/5 flex flex-col overflow-hidden'>
      {/* 主要的 Tab 切换 - 无缝融入设计 */}
      {/* -mx-4 matches the container's px-4 so the tab row is flush with the panel
          edges; -mx-6 pushed it 8px past the box and the 换源 tab was clipped. */}
      <div className='flex mb-1 -mx-4 flex-shrink-0'>
        {totalEpisodes > 1 && (
          <div
            onClick={() => setActiveTab('episodes')}
            className={`flex-1 py-3 px-6 text-center cursor-pointer transition-all duration-200 font-medium
              ${
                activeTab === 'episodes'
                  ? 'text-apple-600 dark:text-apple-400'
                  : 'text-gray-700 hover:text-apple-600 bg-black/5 dark:bg-white/5 dark:text-gray-300 dark:hover:text-apple-400 hover:bg-black/3 dark:hover:bg-white/3'
              }
            `.trim()}
          >
            选集
          </div>
        )}
        <div
          onClick={handleSourceTabClick}
          className={`flex-1 py-3 px-6 text-center cursor-pointer transition-all duration-200 font-medium flex items-center justify-center
            ${
              activeTab === 'sources'
                ? 'text-apple-600 dark:text-apple-400'
                : 'text-gray-700 hover:text-apple-600 bg-black/5 dark:bg-white/5 dark:text-gray-300 dark:hover:text-apple-400 hover:bg-black/3 dark:hover:bg-white/3'
            }
          `.trim()}
        >
          <span>换源</span>
          {preferBestSource && availableSources && availableSources.length > 0 && (
            <div
              onClick={(e) => {
                e.stopPropagation();
                if (isOptimizing) return; // 防止重复点击
                if (!availableSources || availableSources.length === 0) return;
                // 重置取消标志
                cancelOptimizationRef.current = false;
                setIsOptimizing(true);
                preferBestSource(availableSources, () => cancelOptimizationRef.current)
                  .then((bestSource) => {
                    // 如果已取消，则忽略结果
                    if (cancelOptimizationRef.current) return;
                    // 确保bestSource有效
                    if (bestSource && (bestSource.source !== currentSource || bestSource.id !== currentId)) {
                      // 切换到最佳播放源
                      handleSourceClick(bestSource);
                    }
                  })
                  .catch((_err: Error) => {
                    // 静默处理错误，因为已经有UI提示
                  })
                  .finally(() => {
                    if (!cancelOptimizationRef.current) {
                      setIsOptimizing(false);
                      if (setLoading) setLoading(false);
                    }
                    // 重置取消标志
                    cancelOptimizationRef.current = false;
                  });
              }}
              className={`ml-2 bg-blue-500 text-white text-xs font-bold w-6 h-6 rounded-full flex items-center justify-center shadow-md transition-all duration-300 ease-out ${
                isOptimizing
                  ? 'opacity-50 cursor-not-allowed'
                  : 'hover:bg-blue-600 hover:scale-110 cursor-pointer'
              }`}
              title={isOptimizing ? '优选进行中...' : '优选播放源'}
            >
              <svg
                className="w-3.5 h-3.5"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z" />
              </svg>
            </div>
          )}
        </div>
      </div>

      {/* 选集 Tab 内容 */}
      {activeTab === 'episodes' && (
        <>
          {/* 分类标签 */}
          <div className='flex items-center gap-4 mb-4 border-b border-[#d2d2d7] dark:border-[#424245] -mx-6 px-6 flex-shrink-0'>
            <div className='flex-1 overflow-x-auto scrollbar-hide' ref={categoryContainerRef}>
              <div className='flex gap-2 min-w-max'>
                {categories.map((label, idx) => {
                  const isActive = idx === displayPage;
                  return (
                    <button
                      key={label}
                      ref={(el) => {
                        buttonRefs.current[idx] = el;
                      }}
                      onClick={() => handleCategoryClick(idx)}
                      className={`w-20 relative py-2 text-sm font-medium transition-colors whitespace-nowrap flex-shrink-0 text-center 
                        ${
                          isActive
                            ? 'text-apple-500 dark:text-apple-400'
                            : 'text-gray-700 hover:text-apple-600 dark:text-gray-300 dark:hover:text-apple-400'
                        }
                      `.trim()}
                    >
                      {label}
                      {isActive && (
                        <div className='absolute bottom-0 left-0 right-0 h-0.5 bg-apple-500 dark:bg-apple-400' />
                      )}
                    </button>
                  );
                })}
              </div>
            </div>
            {/* 向上/向下按钮 */}
            <button
              className='flex-shrink-0 w-8 h-8 rounded-md flex items-center justify-center text-gray-700 hover:text-apple-600 hover:bg-[#f5f5f7] dark:text-gray-300 dark:hover:text-apple-400 dark:hover:bg-white/20 transition-colors transform translate-y-[-4px]'
              onClick={() => {
                // 切换集数排序（正序/倒序）
                setDescending((prev) => !prev);
              }}
            >
              <svg
                className='w-4 h-4'
                fill='none'
                stroke='currentColor'
                viewBox='0 0 24 24'
              >
                <path
                  strokeLinecap='round'
                  strokeLinejoin='round'
                  strokeWidth='2'
                  d='M7 16V4m0 0L3 8m4-4l4 4m6 0v12m0 0l4-4m-4 4l-4-4'
                />
              </svg>
            </button>
          </div>

          {/* 集数网格 */}
          <div className='overflow-y-auto flex-1 pb-4 scrollbar-hide'>
            <div className='grid grid-cols-3 sm:grid-cols-4 gap-3'>
              {(() => {
                const len = currentEnd - currentStart + 1;
                const episodes = Array.from({ length: len }, (_, i) =>
                  descending ? currentEnd - i : currentStart + i
                );
                return episodes;
              })().map((episodeNumber) => {
                const isActive = episodeNumber === value;
                return (
                  <button
                    key={episodeNumber}
                    onClick={() => handleEpisodeClick(episodeNumber - 1)}
                    className={`h-9 px-1 py-1 flex items-center justify-center text-xs font-medium rounded transition-all duration-200 whitespace-nowrap font-mono
                      ${
                        isActive
                          ? 'bg-apple-500 text-white shadow-lg shadow-apple-500/25 dark:bg-apple-600'
                          : 'bg-[#e8e8ed] text-gray-700 hover:bg-[#d2d2d7] hover:scale-105 dark:bg-white/10 dark:text-gray-300 dark:hover:bg-white/20'
                      }`.trim()}
                  >
                    {(() => {
                      const title = episodes_titles?.[episodeNumber - 1];
                      if (!title) {
                        return episodeNumber;
                      }
                      // 如果匹配"第X集"格式，提取中间的数字
                      const match = title.match(/第(\d+)集/);
                      if (match) {
                        return match[1];
                      }
                      return title;
                    })()}
                  </button>
                );
              })}
            </div>
          </div>
        </>
      )}

      {/* 换源 Tab 内容 */}
      {activeTab === 'sources' && (
        <div className='flex flex-col h-full mt-4'>
          {sourceSearchLoading && (
            <div className='flex items-center justify-center py-8'>
              <div className='animate-spin rounded-full h-8 w-8 border-b-2 border-apple-500'></div>
              <span className='ml-2 text-sm text-gray-600 dark:text-gray-300'>
                搜索中...
              </span>
            </div>
          )}

          {isOptimizing && (
            <div className='flex items-center justify-center py-3'>
              <div className='flex items-center'>
                <div className='animate-spin rounded-full h-6 w-6 border-b-2 border-blue-500'></div>
                <span className='ml-2 text-sm text-gray-600 dark:text-gray-300'>
                  优选播放源中...
                </span>
              </div>
              <button
                onClick={() => {
                  cancelOptimizationRef.current = true;
                  setIsOptimizing(false);
                  if (setLoading) setLoading(false);
                }}
                className='ml-4 text-xs text-red-500 hover:text-red-700 dark:text-red-400 dark:hover:text-red-300 px-2 py-1 rounded border border-red-300 dark:border-red-700 hover:bg-red-50 dark:hover:bg-red-900/30 transition-colors'
              >
                取消
              </button>
            </div>
          )}

          {sourceSearchError && (
            <div className='flex items-center justify-center py-8'>
              <div className='text-center'>
                <div className='text-red-500 text-2xl mb-2'>⚠️</div>
                <p className='text-sm text-red-600 dark:text-red-400'>
                  {sourceSearchError}
                </p>
              </div>
            </div>
          )}

          {!sourceSearchLoading &&
            !sourceSearchError &&
            availableSources.length === 0 && (
              <div className='flex items-center justify-center py-8'>
                <div className='text-center'>
                  <div className='text-gray-400 text-2xl mb-2'>📺</div>
                  <p className='text-sm text-gray-600 dark:text-gray-300'>
                    暂无可用的换源
                  </p>
                </div>
              </div>
            )}

          {!sourceSearchLoading &&
            !sourceSearchError &&
            availableSources.length > 0 && (
              <div className='flex-1 overflow-y-auto pb-2 scrollbar-hide'>
                {/* 换源排成和选集一样的网格：一格一个源，只留源名和测速。上游的整行
                    卡片带封面，一屏只能放三四个，换源时要滚很长。 */}
                <div className='grid grid-cols-3 sm:grid-cols-4 gap-2'>
                  {orderedSources.map((source) => {
                    const key = sourceKeyOf(source);
                    const isCurrentSource =
                      source.source?.toString() === currentSource?.toString() &&
                      source.id?.toString() === currentId?.toString();
                    const info = videoInfoMap.get(key);
                    const speed = info
                      ? info.hasError
                        ? '测速失败'
                        : info.loadSpeed
                      : '...';
                    return (
                      <button
                        key={key}
                        data-source-key={key}
                        data-active={isCurrentSource ? '1' : '0'}
                        onClick={() =>
                          !isCurrentSource && handleSourceClick(source)
                        }
                        title={`${source.source_name} · ${info?.quality || '未知'} · ${source.episodes.length} 集${isCurrentSource ? ' · 当前生效源' : ''}`}
                        className={`h-12 px-2 py-1 rounded flex flex-col items-center justify-center gap-1 transition-all duration-200 select-none
                          ${
                            isCurrentSource
                              ? 'bg-apple-500 text-white shadow-lg shadow-apple-500/25 dark:bg-apple-600'
                              : 'bg-[#e8e8ed] text-gray-700 hover:bg-[#d2d2d7] hover:scale-105 cursor-pointer dark:bg-white/10 dark:text-gray-300 dark:hover:bg-white/20'
                          }`.trim()}
                      >
                        <span className='text-xs font-medium truncate max-w-full'>
                          {source.source_name}
                        </span>
                        <span
                          className={`text-[10px] font-mono leading-none truncate max-w-full ${
                            info
                              ? info.hasError
                                ? 'text-red-600 dark:text-red-400'
                                : 'text-gray-500 dark:text-gray-400'
                              : 'text-gray-400'
                          }`}
                        >
                          {speed}
                        </span>
                      </button>
                    );
                  })}
                </div>
                <div className='mt-3 pt-2 border-t border-gray-400 dark:border-[#424245]'>
                  <button
                    onClick={() => {
                      if (videoTitle) {
                        router.push(
                          `/search?q=${encodeURIComponent(videoTitle)}`
                        );
                      }
                    }}
                    className='w-full text-center text-xs text-gray-500 dark:text-gray-400 hover:text-apple-500 dark:hover:text-apple-400 transition-colors py-2'
                  >
                    影片匹配有误？点击去搜索
                  </button>
                </div>
              </div>
            )}
        </div>
      )}
    </div>
  );
};

export default EpisodeSelector;
