/* eslint-disable @typescript-eslint/ban-ts-comment, @typescript-eslint/no-explicit-any, react-hooks/exhaustive-deps, no-console, @next/next/no-img-element */


import * as React from 'react';
import { Download, Heart } from 'lucide-react';

import { SearchResult } from '@/lib/types';
import { processImageUrl, retryImageThroughProxy } from '@/lib/utils';
import { FALLBACK_POSTER, resolveDoubanPoster } from '@/lib/poster.client';

import { FollowingIconButton } from '@/components/FollowingIcon';

interface VideoDetailPanelProps {
  videoTitle: string;
  videoYear: string;
  totalEpisodes: number;
  currentEpisodeIndex: number;
  detail: SearchResult | null;
  favorited: boolean;
  following: boolean;
  onToggleFavorite: () => void;
  onToggleFollowing: () => void;
  videoUrl: string;
  videoDoubanId: number;
  currentSource: string;
  currentId: string;
  onDownload: () => void;
}

/**
 * 播放页下方的影片标题、操作按钮与简介区域。
 */
export function VideoDetailPanel(props: VideoDetailPanelProps) {
  const {
    videoTitle,
    videoYear,
    totalEpisodes,
    currentEpisodeIndex,
    detail,
    favorited,
    following,
    onToggleFavorite,
    onToggleFollowing,
    videoUrl,
    videoDoubanId,
    currentSource,
    currentId,
    onDownload,
  } = props;

  // 海报链：豆瓣 → 源 → 站点图标，与记录卡片（VideoCard）一致。播放页的封面来自
  // 源站，豆瓣的同名海报更标准也更稳定；`posterIndex` 是当前尝试到的位置，加载
  // 失败时前进一格，所以豆瓣海报取不到会退回源海报，两者都失败才显示兜底图标。
  const [doubanPoster, setDoubanPoster] = React.useState('');
  const [posterIndex, setPosterIndex] = React.useState(0);

  React.useEffect(() => {
    if (!videoTitle) return;
    let cancelled = false;
    resolveDoubanPoster(videoTitle, videoYear)
      .then((found) => {
        if (cancelled || !found) return;
        setDoubanPoster(found);
        // 豆瓣海报到手后重新从链首尝试，之前失败的源海报不再占着兜底位。
        setPosterIndex(0);
      })
      .catch(() => {
        // 搜索失败只是"没有豆瓣海报"，源海报照常显示。
      });
    return () => {
      cancelled = true;
    };
  }, [videoTitle, videoYear]);

  const posterChain = React.useMemo(() => {
    const chain: string[] = [];
    if (doubanPoster) chain.push(processImageUrl(doubanPoster));
    if (detail?.poster) chain.push(processImageUrl(detail.poster));
    chain.push(FALLBACK_POSTER);
    return chain;
  }, [doubanPoster, detail?.poster]);

  return (
    <div className='grid grid-cols-1 md:grid-cols-[auto_1fr] gap-6'>
      {/* 封面图片区 */}
      {(detail?.poster || doubanPoster) && (
        <div className='flex-shrink-0 mx-auto md:mx-0'>
          <div className='w-32 sm:w-40 md:w-48 lg:w-56 aspect-[2/3] rounded-lg overflow-hidden shadow-lg bg-[#e8e8ed] dark:bg-[#2d2d2f]'>
            <img
              src={posterChain[posterIndex] ?? FALLBACK_POSTER}
              alt={videoTitle || '影片封面'}
              className='w-full h-full object-cover'
              onError={(e) => {
                const target = e.target as HTMLImageElement;
                // 先走服务端代理兜底：豆瓣对浏览器直连一律失败（无 Referer 418，
                // 本站 Referer 403），代理带上游 Referer 就能取到。兜底也失败才
                // 前进一格，否则豆瓣海报会被源海报取代。
                if (retryImageThroughProxy(target)) return;
                if (posterIndex < posterChain.length - 1) {
                  setPosterIndex(posterIndex + 1);
                  return;
                }
                target.style.display = 'none';
              }}
            />
          </div>
        </div>
      )}
      {/* 文字区 */}
      <div className='w-full min-w-0'>
        <div className='p-0 md:p-2 flex flex-col min-h-0'>
          {/* 标题 */}
          <h1 className='text-2xl sm:text-3xl font-bold mb-2 tracking-wide flex items-center flex-wrap flex-shrink-0 text-center md:text-left w-full justify-center md:justify-start'>
            <span className='truncate'>{videoTitle || '影片标题'}</span>
            {totalEpisodes > 1 && (
              <span className='text-gray-500 dark:text-gray-400 text-xl sm:text-2xl ml-2 md:ml-3'>
                {detail?.episodes_titles?.[currentEpisodeIndex] ||
                  `第 ${currentEpisodeIndex + 1} 集`}
              </span>
            )}
            <div className='ml-0 md:ml-3 mt-2 md:mt-0 flex flex-shrink-0 items-center gap-3'>
              <button
                type='button'
                onClick={(e) => {
                  e.stopPropagation();
                  onToggleFavorite();
                }}
                className={`flex h-8 w-8 items-center justify-center rounded-full shadow-md transition-all duration-300 ease-out hover:scale-[1.1] ${
                  favorited
                    ? 'bg-red-500 text-white hover:bg-red-600'
                    : 'bg-[#e8e8ed] text-gray-600 hover:bg-[#d2d2d7] dark:bg-[#2d2d2f] dark:text-gray-300 dark:hover:bg-[#3a3a3c]'
                }`}
                title={favorited ? '取消收藏' : '加入收藏'}
                aria-label={favorited ? '取消收藏' : '加入收藏'}
              >
                <Heart
                  className={`h-4 w-4 ${
                    favorited
                      ? 'fill-white stroke-white'
                      : 'fill-transparent stroke-current stroke-[1.5]'
                  }`}
                />
              </button>
              {videoUrl && (
                <button
                  type='button'
                  onClick={() => onDownload()}
                  className='flex h-8 w-8 items-center justify-center rounded-full bg-blue-500 text-white shadow-md transition-all duration-300 ease-out hover:scale-[1.1] hover:bg-blue-600'
                  title='下载视频'
                  aria-label='下载视频'
                >
                  <Download className='h-4 w-4' />
                </button>
              )}
              {videoDoubanId !== 0 && (
                <a
                  href={`https://movie.douban.com/subject/${videoDoubanId.toString()}`}
                  target='_blank'
                  rel='noopener noreferrer'
                  className='flex h-8 w-8 items-center justify-center rounded-full bg-apple-500 text-white shadow-md transition-all duration-300 ease-out hover:scale-[1.1] hover:bg-apple-600'
                  title='打开豆瓣页面'
                  aria-label='打开豆瓣页面'
                >
                  <svg
                    width='16'
                    height='16'
                    viewBox='0 0 24 24'
                    fill='none'
                    stroke='currentColor'
                    strokeWidth='2'
                    strokeLinecap='round'
                    strokeLinejoin='round'
                    aria-hidden='true'
                  >
                    <path d='M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71'></path>
                    <path d='M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71'></path>
                  </svg>
                </a>
              )}
              {currentSource && currentId && (
                <FollowingIconButton
                  following={following}
                  size={16}
                  padding={8}
                  theme='detail'
                  onClick={(e) => {
                    e.stopPropagation();
                    onToggleFollowing();
                  }}
                />
              )}
            </div>
          </h1>

          {/* 关键信息行 */}
          <div className='flex flex-wrap items-center gap-3 text-base mb-4 opacity-80 flex-shrink-0 justify-center md:justify-start'>
            {detail?.class && (
              <span className='text-apple-600 font-semibold'>
                {detail.class}
              </span>
            )}
            {(detail?.year || videoYear) && (
              <span>{detail?.year || videoYear}</span>
            )}
            {detail?.source_name && (
              <span className='border border-gray-500/60 px-2 py-[1px] rounded'>
                {detail.source_name}
              </span>
            )}
            {detail?.type_name && <span>{detail.type_name}</span>}
          </div>
          {/* 剧情简介 */}
          {detail?.desc && (
            <div
              className='mt-0 text-base leading-relaxed opacity-90 overflow-y-auto pr-2 flex-1 min-h-0 scrollbar-hide max-h-48 md:max-h-none'
              style={{ whiteSpace: 'pre-line' }}
            >
              {detail.desc}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
