/**
 * `next/navigation` — the browser's own location/history is the router.
 *
 * MoonTV's App Router is genuinely multi-page: `/search`, `/douban`, `/login`
 * … are separate HTML documents, so `router.back()` was always "go to the
 * previous document" and `usePathname()` was always "the document I am in".
 * MoonTVPlus serves one document at every non-API path, which keeps both
 * meanings: the document is the app, and navigating is a real browser
 * navigation.
 *
 *   router.push(url)    -> location.assign(url)
 *   router.replace(url) -> location.replace(url)
 *   router.back()       -> history.back()
 *   usePathname()       -> location.pathname
 *   useSearchParams()   -> new URLSearchParams(location.search)
 *
 * The one visible difference is called out in PLAN.md: a navigation is a hard
 * reload rather than Next's soft swap, so the bundle is re-parsed and the
 * `NavigationLoadingProvider` spinner belongs to the document being replaced.
 * The spinner still covers the in-page loaders, which call `startLoading()`
 * without navigating.
 *
 * `useSearchParams()` is typed `URLSearchParams | null` in Next, but every
 * call site here dereferences it directly, so the shim never returns null.
 */

export function usePathname(): string {
  return location.pathname;
}

export function useSearchParams(): URLSearchParams {
  return new URLSearchParams(location.search);
}

export function useRouter(): {
  push: (url: string) => void;
  replace: (url: string) => void;
  back: () => void;
  reload: () => void;
} {
  return {
    push: (url: string) => {
      location.assign(url);
    },
    replace: (url: string) => {
      location.replace(url);
    },
    back: () => {
      history.back();
    },
    reload: () => {
      location.reload();
    },
  };
}
