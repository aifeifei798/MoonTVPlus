import Link from 'next/link';

export const runtime = 'edge';

export default function NotFound() {
  return (
    <div className='flex min-h-[60vh] flex-col items-center justify-center gap-4 p-6 text-center'>
      <h1 className='text-3xl font-bold'>页面未找到</h1>
      <p className='text-gray-500 dark:text-gray-400'>
        您访问的页面不存在或已被移动。
      </p>
      <Link
        href='/'
        className='rounded-sm bg-green-600 px-4 py-2 text-white hover:bg-green-700 dark:bg-green-600 dark:hover:bg-green-700'
      >
        返回首页
      </Link>
    </div>
  );
}
