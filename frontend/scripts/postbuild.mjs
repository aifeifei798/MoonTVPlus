#!/usr/bin/env node
/**
 * Everything the Vite build cannot produce on its own, in the shape the
 * backend's static file server expects (`frontend/dist` is its document root):
 *
 *   public/**            -> dist/**            favicon, icons, logo, robots.txt,
 *                                             manifest.json, and StreamSaver's
 *                                             same-origin `mitm.html` + `sw.js`
 *   sweetalert2 css      -> dist/assets/sweetalert2.min.css
 *
 * `layout.tsx` used to `import 'sweetalert2/dist/sweetalert2.min.css'`; a JS/app
 * entry has no CSS imports, so the stylesheet is placed where `index.html`
 * links it.
 *
 * `scripts/restore-sw.js` from MoonTV is not needed: it undid `next-pwa`'s
 * clobbering of `public/sw.js`, and there is no PWA plugin in this build.
 */

import fs from 'node:fs';
import path from 'node:path';

const root = path.resolve(import.meta.dirname, '..');
const dist = path.join(root, 'dist');

function copyTree(from, to) {
  fs.mkdirSync(to, { recursive: true });
  for (const entry of fs.readdirSync(from)) {
    const src = path.join(from, entry);
    const dest = path.join(to, entry);
    if (fs.statSync(src).isDirectory()) {
      copyTree(src, dest);
    } else {
      fs.copyFileSync(src, dest);
    }
  }
}

// public/ -> dist/
copyTree(path.join(root, 'public'), dist);

// sweetalert2's stylesheet, resolved out of the installed package
const sweetalert2Css = path.resolve(
  'node_modules/sweetalert2/dist/sweetalert2.min.css',
);
fs.copyFileSync(sweetalert2Css, path.join(dist, 'assets/sweetalert2.min.css'));

console.log('✅ postbuild: public assets and stylesheets placed in dist/');
