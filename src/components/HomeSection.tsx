'use client';

import { ChevronRight } from 'lucide-react';
import Link from 'next/link';

import ScrollableRow from '@/components/ScrollableRow';

interface HomeSectionProps {
  title: string;
  href: string;
  loading: boolean;
  emptyText: string;
  onMoreClick?: () => void;
  children: React.ReactNode;
}

// 首页板块通用外壳：标题 + “查看更多” + 横向滚动列表（含骨架屏/空态）
function HomeSection({
  title,
  href,
  loading,
  emptyText,
  onMoreClick,
  children,
}: HomeSectionProps) {
  return (
    <section className='mb-8'>
      <div className='mb-4 flex items-center justify-between'>
        <h2 className='text-xl font-bold text-gray-800 dark:text-gray-200'>
          {title}
        </h2>
        <Link
          href={href}
          onClick={onMoreClick}
          className='flex items-center text-sm text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200'
        >
          查看更多
          <ChevronRight className='w-4 h-4 ml-1' />
        </Link>
      </div>
      <ScrollableRow emptyText={emptyText}>
        {loading
          ? // 加载状态显示灰色占位数据
            Array.from({ length: 8 }).map((_, index) => (
              <div
                key={index}
                className='min-w-[96px] w-24 sm:min-w-[180px] sm:w-44'
              >
                <div className='relative aspect-[2/3] w-full overflow-hidden rounded-lg bg-gray-200 animate-pulse dark:bg-gray-800'>
                  <div className='absolute inset-0 bg-gray-300 dark:bg-gray-700'></div>
                </div>
                <div className='mt-2 h-4 bg-gray-200 rounded-sm animate-pulse dark:bg-gray-800'></div>
              </div>
            ))
          : children}
      </ScrollableRow>
    </section>
  );
}

export default HomeSection;
