'use client';

import { MutableRefObject, useEffect, useState } from 'react';

import {
  deleteFavorite,
  deleteFollowing,
  generateStorageKey,
  isFavorited,
  isFollowing,
  saveFavorite,
  saveFollowing,
  subscribeToDataUpdates,
} from '@/lib/db.client';
import { SearchResult } from '@/lib/types';

interface UseFavoriteFollowingParams {
  currentSource: string;
  currentId: string;
  videoTitleRef: MutableRefObject<string>;
  detailRef: MutableRefObject<SearchResult | null>;
  currentEpisodeIndexRef: MutableRefObject<number>;
  searchTitle: string;
}

// 收藏 + 追更状态 hook：监听数据变化事件，source/id 变化时同步最新状态
function useFavoriteFollowing({
  currentSource,
  currentId,
  videoTitleRef,
  detailRef,
  currentEpisodeIndexRef,
  searchTitle,
}: UseFavoriteFollowingParams) {
  const [favorited, setFavorited] = useState(false);
  const [following, setFollowing] = useState(false);

  // 每当 source 或 id 变化时检查收藏状态
  useEffect(() => {
    if (!currentSource || !currentId) return;
    (async () => {
      try {
        const fav = await isFavorited(currentSource, currentId);
        setFavorited(fav);
      } catch (err) {
        // eslint-disable-next-line no-console
        console.error('检查收藏状态失败:', err);
      }
    })();
  }, [currentSource, currentId]);

  // 监听收藏数据更新事件
  useEffect(() => {
    if (!currentSource || !currentId) return;

    const unsubscribe = subscribeToDataUpdates<Record<string, unknown>>(
      'favoritesUpdated',
      (favorites) => {
        const key = generateStorageKey(currentSource, currentId);
        const isFav = !!favorites[key];
        setFavorited(isFav);
      }
    );

    return unsubscribe;
  }, [currentSource, currentId]);

  // 初始化追更状态并监听更新事件
  useEffect(() => {
    if (!currentSource || !currentId) return;

    const refreshFollowingState = async () => {
      const isFollow = await isFollowing(currentSource, currentId);
      setFollowing(isFollow);
    };

    refreshFollowingState();

    const unsubscribe = subscribeToDataUpdates<Record<string, unknown>>(
      'followingsUpdated',
      (followings) => {
        const key = generateStorageKey(currentSource, currentId);
        setFollowing(!!followings[key]);
      }
    );

    return unsubscribe;
  }, [currentSource, currentId]);

  // 切换收藏
  const handleToggleFavorite = async () => {
    if (
      !videoTitleRef.current ||
      !detailRef.current ||
      !currentSource ||
      !currentId
    )
      return;

    try {
      if (favorited) {
        await deleteFavorite(currentSource, currentId);
        setFavorited(false);
      } else {
        await saveFavorite(currentSource, currentId, {
          title: videoTitleRef.current,
          source_name: detailRef.current?.source_name || '',
          year: detailRef.current?.year,
          cover: detailRef.current?.poster || '',
          total_episodes: detailRef.current?.episodes.length || 1,
          save_time: Date.now(),
          search_title: searchTitle,
        });
        setFavorited(true);
      }
    } catch (err) {
      // eslint-disable-next-line no-console
      console.error('切换收藏失败:', err);
    }
  };

  // 切换追更
  const handleToggleFollowing = async () => {
    if (
      !videoTitleRef.current ||
      !detailRef.current ||
      !currentSource ||
      !currentId
    )
      return;

    try {
      if (following) {
        await deleteFollowing(currentSource, currentId);
        setFollowing(false);
      } else {
        await saveFollowing(currentSource, currentId, {
          title: videoTitleRef.current,
          source_name: detailRef.current?.source_name || '',
          year: detailRef.current?.year,
          cover: detailRef.current?.poster || '',
          total_episodes: detailRef.current?.episodes.length || 1,
          watched_episodes: currentEpisodeIndexRef.current + 1,
          save_time: Date.now(),
          search_title: searchTitle,
          source: currentSource,
          id: currentId,
        });
        setFollowing(true);
      }
    } catch (err) {
      // eslint-disable-next-line no-console
      console.error('切换追更失败:', err);
    }
  };

  return { favorited, following, handleToggleFavorite, handleToggleFollowing };
}

export { useFavoriteFollowing };
