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
 * `fill` becomes `width/height: 100%`, the same box Next produced; the
 * callers' `object-cover` className does the rest.
 */
import * as React from 'react';

export default function Image(props: Record<string, unknown>) {
  const { fill, onLoad, onError, ...rest } = props;
  const attrs: Record<string, unknown> = { ...rest };
  if (fill) {
    attrs.width = '100%';
    attrs.height = '100%';
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
        const error = (event: Event) => (onError as ((e: Event) => void) | undefined)?.(event);
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
