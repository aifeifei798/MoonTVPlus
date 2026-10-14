
import * as React from 'react';
import { memo, useEffect, useRef, useState } from 'react';

/**
 * 全局下载管理器组件
 * 在根布局中渲染一次，被所有导航栏组件共享
 * 通过自定义事件进行通信
 *
 * `DownloadManager` is deliberately not imported at module level. It pulls in
 * `lib/m3u8-downloader`, which pulls in the vendored mux.js transmuxer and
 * stream-saver — all of that used to sit in the entry chunk every page loads,
 * for a panel that is opened rarely. The module is fetched on the first
 * `showDownloadManager` / `addDownloadTask` event instead. A task event that
 * arrives while the import is still in flight is buffered and replayed after
 * the manager mounts, so adding a download from the play page never loses the
 * task.
 */
const GlobalDownloadManager = () => {
  const [Manager, setManager] = useState<
    React.ComponentType<{ isOpen: boolean; onClose: () => void }> | null
  >(null);
  const [showDownloadManager, setShowDownloadManager] = useState(false);
  const mountedRef = useRef(false);
  const loadingRef = useRef(false);
  const queuedEvents = useRef<CustomEvent[]>([]);

  useEffect(() => {
    if (typeof window === 'undefined') return;

    const load = () => {
      setShowDownloadManager(true);
      if (loadingRef.current) return;
      loadingRef.current = true;
      import('./DownloadManager').then((module) => setManager(() => module.default));
    };

    const handleShowEvent = () => load();

    const handleAddTaskEvent = (event: Event) => {
      load();
      // The manager is not mounted yet, so its own listener cannot see this
      // event yet — hold it and replay it once it can.
      if (event instanceof CustomEvent && !mountedRef.current) {
        queuedEvents.current.push(event);
      }
    };

    window.addEventListener('showDownloadManager', handleShowEvent);
    window.addEventListener('addDownloadTask', handleAddTaskEvent);

    return () => {
      window.removeEventListener('showDownloadManager', handleShowEvent);
      window.removeEventListener('addDownloadTask', handleAddTaskEvent);
    };
  }, []);

  useEffect(() => {
    if (!Manager || typeof window === 'undefined') return;
    mountedRef.current = true;
    const pending = queuedEvents.current;
    queuedEvents.current = [];
    // Deferred one tick so DownloadManager's own `addDownloadTask` listener is
    // registered before the replayed events arrive, whatever the effect order.
    setTimeout(() => {
      for (const event of pending) window.dispatchEvent(event);
    }, 0);
  }, [Manager]);

  if (!Manager) return null;

  return (
    <Manager
      isOpen={showDownloadManager}
      onClose={() => setShowDownloadManager(false)}
    />
  );
};

// 使用 React.memo 优化
export default memo(GlobalDownloadManager);
