/**
 * `next/image` — a plain `<img>`.
 *
 * Next's `Image` rewrites `src` through its own optimizer and owns the
 * `onLoad`/`onError` props. MoonTV already points `src` at its own
 * `/api/image-proxy`, so there is nothing to optimize: the shim passes the
 * attributes through and attaches the two event handlers to the element
 * itself, which is what the callers expect (`VideoCard` uses `onLoad` to
 * clear its placeholder state and `onError` to fall back to a placeholder).
 *
 * `fill` is CSS, not attributes. Next renders a filled image as
 * `position: absolute; inset: 0; width: 100%; height: 100%`, and the callers'
 * `object-cover` className does the rest. Writing it as `width="100%"`
 * `height="100%"` attributes does not work: a percentage is not a valid HTML
 * `width`/`height` value, so the element falls back to Tailwind's preflight
 * (`img, video { max-width: 100%; height: auto }`) and is sized **by its width**
 * with the height taken from the natural ratio. Every card whose poster is not
 * exactly 2/3 then either leaves a strip of the box empty at the bottom or gets
 * clipped by the box's `overflow: hidden` — the "图片下面缺了一块" symptom.
 *
 * A poster the browser cannot fetch is retried through the app's own
 * `/api/image-proxy` before the caller's `onError` is allowed to fire. That is
 * what makes a broken poster render instead of leaving the card on its skeleton
 * forever: `img*.doubanio.com` answers a referer-less request with 418 (every
 * card is `referrerPolicy='no-referrer'`, so `direct` douban posters never
 * load), and a source CDN that is unreachable from the viewer's network but
 * reachable from the server fails the same way. The server-side proxy sends the
 * upstream `Referer` and, for the douban and bangumi poster families, walks a
 * mirror chain, so the second attempt succeeds where the first could not.
 */
import * as React from 'react';
import { retryImageThroughProxy } from '@/lib/utils';

export default function Image(props: Record<string, unknown>) {
  const { fill, onLoad, onError, style, ...rest } = props;
  const attrs: Record<string, unknown> = { ...rest };
  if (fill) {
    attrs.style = {
      ...(style as Record<string, unknown> | undefined),
      position: 'absolute',
      top: 0,
      right: 0,
      bottom: 0,
      left: 0,
      width: '100%',
      height: '100%',
    };
  } else if (style) {
    attrs.style = style;
  }

  return (
    <img
      {...attrs}
      ref={(element) => {
        // React calls an element-level `ref` twice: with the DOM node when the
        // element is attached, and with **`null`** when it is detached
        // (`safelyDetachRef` in `react-dom`). The second call is what makes a
        // re-render that drops a card — a douban refresh, a search that
        // returns a different set — throw inside the commit phase, which
        // blanks the whole page. There is nothing to undo on `null`: the
        // listeners die with the node.
        if (element === null) return;
        const load = (event: Event) => (onLoad as ((e: Event) => void) | undefined)?.(event);
        const error = (event: Event) => {
          // One retry through the server before the caller sees the failure: the
          // proxy sends the Referer the host wants and walks the mirror chain for
          // the douban/bangumi poster families.
          if (retryImageThroughProxy(element)) return;
          (onError as ((e: Event) => void) | undefined)?.(event);
        };
        element.addEventListener('load', load);
        element.addEventListener('error', error);
        // React 19 treats the ref callback's return value as cleanup, so a
        // re-render that reuses the node removes the previous pair instead of
        // stacking a duplicate on every commit.
        return () => {
          element.removeEventListener('load', load);
          element.removeEventListener('error', error);
        };
      }}
    />
  );
}
