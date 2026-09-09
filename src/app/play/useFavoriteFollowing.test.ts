import { act, renderHook, waitFor } from '@testing-library/react';
import { MutableRefObject } from 'react';

import { generateStorageKey, getAllFavorites } from '@/lib/db.client';
import { SearchResult } from '@/lib/types';

import { useFavoriteFollowing } from './useFavoriteFollowing';

function createRefs() {
  const videoTitleRef = { current: '测试影片' } as MutableRefObject<string>;
  const detailRef = {
    current: {
      id: 'id1',
      title: '测试影片',
      poster: '',
      episodes: ['e1', 'e2'],
      episodes_titles: ['第1集', '第2集'],
      source: 'source1',
      source_name: '测试源',
      year: '2024',
    } as SearchResult | null,
  } as MutableRefObject<SearchResult | null>;
  const currentEpisodeIndexRef = { current: 0 } as MutableRefObject<number>;
  return { videoTitleRef, detailRef, currentEpisodeIndexRef };
}

function setupHook() {
  const refs = createRefs();

  const { result, rerender } = renderHook(
    ({ currentSource, currentId }) =>
      useFavoriteFollowing({
        currentSource,
        currentId,
        ...refs,
        searchTitle: '搜索标题',
      }),
    { initialProps: { currentSource: 'source1', currentId: 'id1' } }
  );

  return { result, rerender, refs };
}

describe('useFavoriteFollowing', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('初始状态为未收藏、未追更', () => {
    const { result } = setupHook();
    expect(result.current.favorited).toBe(false);
    expect(result.current.following).toBe(false);
  });

  it('切换收藏后状态与本地存储同步，再次切换则取消', async () => {
    const { result } = setupHook();

    await act(async () => {
      await result.current.handleToggleFavorite();
    });
    expect(result.current.favorited).toBe(true);
    const favorites = await getAllFavorites();
    expect(favorites[generateStorageKey('source1', 'id1')]).toBeTruthy();

    await act(async () => {
      await result.current.handleToggleFavorite();
    });
    expect(result.current.favorited).toBe(false);
  });

  it('source/id 变化后重新校验收藏状态', async () => {
    const { result, rerender } = setupHook();

    await act(async () => {
      await result.current.handleToggleFavorite();
    });
    expect(result.current.favorited).toBe(true);

    rerender({ currentSource: 'source1', currentId: 'id2' });
    await waitFor(() => expect(result.current.favorited).toBe(false));
  });

  it('切换追更后状态与本地存储同步', async () => {
    const { result } = setupHook();

    await act(async () => {
      await result.current.handleToggleFollowing();
    });
    expect(result.current.following).toBe(true);

    await act(async () => {
      await result.current.handleToggleFollowing();
    });
    expect(result.current.following).toBe(false);
  });

  it('缺少标题或详情时切换操作不生效', async () => {
    const { result } = renderHook(() =>
      useFavoriteFollowing({
        currentSource: 'source1',
        currentId: 'id1',
        videoTitleRef: { current: '' },
        detailRef: { current: null },
        currentEpisodeIndexRef: { current: 0 },
        searchTitle: '搜索标题',
      })
    );

    await act(async () => {
      await result.current.handleToggleFavorite();
      await result.current.handleToggleFollowing();
    });
    expect(result.current.favorited).toBe(false);
    expect(result.current.following).toBe(false);
  });
});
