/**
 * The two page-metadata types `src/app/warning/page.tsx` imports from `next`.
 *
 * Next rendered `export const metadata` into the document `<head>`. MoonTVPlus
 * has one document, so its `<head>` lives in `frontend/index.html` and these
 * annotations are inert — kept so the page files stay byte-identical to the
 * ones they were ported from.
 */

export interface Metadata {
  title?: string;
  description?: string;
  manifest?: string;
}

export interface Viewport {
  width?: string | number;
  initialScale?: number;
  minimumScale?: number;
  maximumScale?: number;
  userScalable?: boolean;
  viewportFit?: 'contain' | 'cover';
}
