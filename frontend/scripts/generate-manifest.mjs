#!/usr/bin/env node
/* Port of `scripts/generate-manifest.js`: the PWA manifest is generated from
 * `NEXT_PUBLIC_SITE_NAME` rather than committed, so the installed app is named
 * after the site. Unchanged template and env fallback.

 * MoonTVPlus writes the `__SITE_NAME__` marker instead of the build-time value:
 * the backend substitutes it when the file is served (`static.py`), so an admin
 * who renames the site in the panel does not have to rebuild the frontend to
 * have the installed PWA follow. The runtime name is the same
 * `NEXT_PUBLIC_SITE_NAME` this script used to bake in, plus whatever the admin
 * set, so nothing changes for a deployment that never touches the panel. */

import fs from 'node:fs';
import path from 'node:path';

const projectRoot = path.resolve(import.meta.dirname, '..');
const publicDir = path.join(projectRoot, 'public');
const manifestPath = path.join(publicDir, 'manifest.json');

const manifestTemplate = {
  name: '__SITE_NAME__',
  short_name: '__SITE_NAME__',
  description: '影视聚合',
  start_url: '/',
  scope: '/',
  display: 'standalone',
  background_color: '#000000',
  'apple-mobile-web-app-capable': 'yes',
  'apple-mobile-web-app-status-bar-style': 'black',
  icons: [
    {
      src: '/icons/icon-192x192.png',
      sizes: '192x192',
      type: 'image/png',
    },
    {
      src: '/icons/icon-256x256.png',
      sizes: '256x256',
      type: 'image/png',
    },
    {
      src: '/icons/icon-384x384.png',
      sizes: '384x384',
      type: 'image/png',
    },
    {
      src: '/icons/icon-512x512.png',
      sizes: '512x512',
      type: 'image/png',
    },
  ],
};

try {
  if (!fs.existsSync(publicDir)) {
    fs.mkdirSync(publicDir, { recursive: true });
  }

  fs.writeFileSync(manifestPath, JSON.stringify(manifestTemplate, null, 2));
  console.log('✅ Generated manifest.json (site name substituted at serve time)');
} catch (error) {
  console.error('❌ Error generating manifest.json:', error);
  process.exit(1);
}
