/* eslint-disable @typescript-eslint/no-explicit-any */


import * as React from 'react';
import { Cat, Clapperboard, Clover, Compass, Film, Home, Search, Trophy, Tv } from 'lucide-react';
import Link from '@/shims/link';
import { usePathname } from '@/shims/navigation';
import { memo, useEffect, useState } from 'react';

import { useNavigationLoading } from './NavigationLoadingProvider';

interface MobileBottomNavProps {
  /**
   * 主动指定当前激活的路径。当未提供时，自动使用 usePathname() 获取的路径。
   */
  activePath?: string;
}

const MobileBottomNav = ({ activePath }: MobileBottomNavProps) => {
  const pathname = usePathname();
  const { startLoading } = useNavigationLoading();

  // 当前激活路径：优先使用传入的 activePath，否则回退到浏览器地址
  const currentActive = activePath ?? pathname;

  const [navItems] = useState([
    { icon: Home, label: '首页', href: '/' },
    { icon: Search, label: '搜索', href: '/search' },
    {
      icon: Film,
      label: '电影',
      href: '/douban?type=movie',
    },
    {
      icon: Tv,
      label: '剧集',
      href: '/douban?type=tv',
    },
    {
      icon: Clapperboard,
      label: '短剧',
      href: '/douban?type=short',
    },
    {
      icon: Compass,
      label: '纪录片',
      href: '/douban?type=doc',
    },
    {
      icon: Cat,
      label: '动漫',
      href: '/douban?type=anime',
    },
    {
      icon: Clover,
      label: '综艺',
      href: '/douban?type=show',
    },
    {
      icon: Trophy,
      label: '榜单',
      href: '/ranking',
    },
  ]);

  // 检查是否启用简洁模式 - 使用状态管理
  const [simpleMode, setSimpleMode] = useState(false);
  const [isClient, setIsClient] = useState(false);

  useEffect(() => {
    setIsClient(true);
    if (typeof window !== 'undefined') {
      const savedSimpleMode = localStorage.getItem('simpleMode');
      if (savedSimpleMode !== null) {
        setSimpleMode(JSON.parse(savedSimpleMode));
      }
    }
  }, []);

  const isActive = (href: string) => {
    const typeMatch = href.match(/type=([^&]+)/)?.[1];

    // 解码URL以进行正确的比较
    const decodedActive = decodeURIComponent(currentActive);
    const decodedItemHref = decodeURIComponent(href);

    return (
      decodedActive === decodedItemHref ||
      (decodedActive.startsWith('/douban') &&
        decodedActive.includes(`type=${typeMatch}`))
    );
  };

  return (
    <nav
      className='md:hidden fixed left-0 right-0 z-[600] bg-white/90 backdrop-blur-xl border-t border-[#d2d2d7]/50 overflow-hidden dark:bg-black/80 dark:border-[#424245]/50'
      style={{
        /* 紧贴视口底部，同时在内部留出安全区高度 */
        bottom: 0,
        paddingBottom: 'env(safe-area-inset-bottom)',
        minHeight: 'calc(3.5rem + env(safe-area-inset-bottom))',
      }}
    >
      {/* 8 个入口一屏排下：上游是每项 20vw + 横滑（滚动条还藏了），
          手机上只能看到 5 个，剩下 3 个像丢了一样。这里改 flex 均分，
          再小的屏也不用滑 */}
      <ul className='flex items-stretch'>
        {navItems.map((item) => {
          const active = isActive(item.href);
          
          // 简洁模式下只显示首页和搜索，但在服务器端渲染时先不渲染
          if (!isClient) {
            return null; // 服务器端渲染时不显示任何内容，避免闪烁
          }
          
          if (simpleMode && !['/', '/search'].includes(item.href)) {
            return null;
          }

          return (
            <li
              key={item.href}
              className='flex-1 min-w-0'
            >
              <Link
                href={item.href}
                className='flex flex-col items-center justify-center w-full h-14 gap-1 px-0.5 text-[11px]'
                onClick={() => {
                  // 如果不是当前激活的链接，则触发加载动画
                  if (!active) {
                    startLoading();
                  }
                }}
              >
                <item.icon
                  className={`h-6 w-6 shrink-0 ${active
                      ? 'text-apple-600 dark:text-apple-400'
                      : 'text-gray-500 dark:text-gray-400'
                    }`}
                />
                <span
                  className={`whitespace-nowrap leading-none ${active
                      ? 'text-apple-600 dark:text-apple-400'
                      : 'text-gray-600 dark:text-gray-300'
                    }`}
                >
                  {item.label}
                </span>
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
};

// 使用 React.memo 优化，避免父组件更新时导致不必要的重新渲染
export default memo(MobileBottomNav);
