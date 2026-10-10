/**
 * The app document's entry — a port of `src/app/layout.tsx`.
 *
 * Next.js ran `RootLayout` once per URL, with the page component handed in as
 * `children`. There is one document here, so the entry picks the page from
 * `location.pathname` and renders the same tree around it. Everything else is
 * the layout file's own tree, in the same order.
 *
 * What changed, and why:
 *
 *   * `process.env.*` reads and `await getConfig()` are gone. Those values are
 *     the server's, and the server inlines them into this document as
 *     `window.RUNTIME_CONFIG` (the nine fields `layout.tsx` already published)
 *     plus `window.RUNTIME_LAYOUT` (`siteName`, `announcement`,
 *     `autoUpdateEnabled`, which `layout.tsx` passed to `SiteProvider` and used
 *     to gate `SubscriptionAutoUpdate`). Same values, same timing, no boot fetch.
 *   * `inter.className` on the body becomes the `font-primary` utility from
 *     `tailwind.config.ts`, whose `fontFamily.primary` is `['Inter', …sans]`.
 *   * `import './globals.css'` and the sweetalert2 stylesheet are `<link>`s in
 *     `index.html`; a JS entry cannot import CSS.
 *
 * `autoUpdateEnabled` keeps the old gate rather than always rendering the
 * component: `SubscriptionAutoUpdate` POSTs `/api/admin/subscription` on every
 * document load, and MoonTV only mounted it when the subscription config asked
 * for auto-update.
 */

import * as React from 'react';
import { createRoot } from 'react-dom/client';

import { ThemeProvider } from '@/components/ThemeProvider';
import ServiceWorkerRegistration from '@/components/ServiceWorkerRegistration';
import { NavigationLoadingIndicator } from '@/components/NavigationLoadingIndicator';
import { NavigationLoadingProvider } from '@/components/NavigationLoadingProvider';
import { SiteProvider } from '@/components/SiteProvider';
import SubscriptionAutoUpdate from '@/components/SubscriptionAutoUpdate';
import UserOnlineUpdate from '@/components/UserOnlineUpdate';
import ConditionalNav from '@/components/ConditionalNav';
import GlobalDownloadManager from '@/components/GlobalDownloadManager';
import { GlobalErrorIndicator } from '@/components/GlobalErrorIndicator';

import Home from '@/app/page';
import SearchPage from '@/app/search/page';
import DoubanPage from '@/app/douban/page';
import LoginPage from '@/app/login/page';
import PlayPage from '@/app/play/page';
import RankingPage from '@/app/ranking/page';
import AdminPage from '@/app/admin/page';
import WarningPage from '@/app/warning/page';
import NotFound from '@/app/not-found';

/**
 * The page table Next.js implied with its directory layout. Paths match
 * exactly, as they did in the App Router; anything else gets `not-found.tsx`.
 */
const PAGES = {
  '/': Home,
  '/search': SearchPage,
  '/douban': DoubanPage,
  '/play': PlayPage,
  '/ranking': RankingPage,
  '/admin': AdminPage,
  '/login': LoginPage,
  '/warning': WarningPage,
};

export default function RootLayout() {
  const layout = window.RUNTIME_LAYOUT ?? {};
  const Page = (PAGES as Record<string, () => React.JSX.Element>)[location.pathname] ?? NotFound;

  return (
    <ThemeProvider
      attribute='class'
      defaultTheme='system'
      enableSystem
      disableTransitionOnChange
    >
      <ServiceWorkerRegistration />
      <NavigationLoadingProvider>
        <SiteProvider siteName={layout.siteName as string} announcement={layout.announcement as string | undefined}>
          <NavigationLoadingIndicator />
          <UserOnlineUpdate />

          {/* 条件导航栏 - 根据路径自动判断是否显示 */}
          <ConditionalNav />

          {/* 全局下载管理器 - 只渲染一次，被所有导航栏共享 */}
          <GlobalDownloadManager />

          {/* 页面内容 */}
          <div className='relative w-full'>
            <main
              className='flex-1 mb-14 md:mb-0'
              style={{
                paddingBottom: 'calc(3.5rem + env(safe-area-inset-bottom))',
              }}
            >
              <Page />
            </main>
          </div>

          <GlobalErrorIndicator />
          {layout.autoUpdateEnabled ? <SubscriptionAutoUpdate /> : null}
        </SiteProvider>
      </NavigationLoadingProvider>
    </ThemeProvider>
  );
}

// The body element itself is in `index.html` (it carries the layout classes and
// the `<html lang>` attribute), so the tree is rendered into it rather than
// producing a document of its own. React 19 has no top-level `ReactDOM.render`
// — only `createRoot(container).render(value)` — and in 19.0.0 `createRoot` is
// exported by the `react-dom/client` subpath, not by `react-dom` itself (whose
// public exports are `createPortal`, `flushSync`, the `__DOM_INTERNALS…`
// namespace, …). Importing it from `react-dom` binds `undefined`, which throws
// at mount time and leaves the served document an empty white page.
//
// `RootLayout` is called, not passed. Upstream's `RootLayout` is a page
// component Next invoked with `{ children }`; passing the function itself makes
// React treat the render value as a leaf and produce no DOM at all, which is
// the second white-screen failure mode: the module evaluates with no error and
// the only body mutation is the root clearing its whitespace text node.
createRoot(document.body).render(RootLayout());
