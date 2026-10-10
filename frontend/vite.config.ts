import { defineConfig, type Plugin } from 'vite';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.dirname(fileURLToPath(import.meta.url));
const srcDir = path.join(root, 'src');
const vendorMuxjs = path.join(srcDir, 'vendor', 'muxjs');

/**
 * The frontend is built as an app whose entry is `index.html`, so Vite rewrites
 * the bundle and stylesheet URLs into `dist/index.html` — which is the file the
 * FastAPI backend serves at every non-API path (with the `__SITE_NAME__` and
 * `__RUNTIME_*__` markers substituted). The hashed asset names survive into it,
 * which is what lets the backend cache them forever.
 *
 * `@/...` import paths are kept exactly as they were in the MoonTV sources, so
 * the copied files needed no rewriting. MoonTV resolved them through Next.js
 * (`next.config.js` + the `@/*` entry in `tsconfig.json`); here a small resolver
 * does the same job. It is a resolver rather than Vite's `alias` because an
 * alias only rewrites the path, and Vite does not run its extension search on
 * the rewritten value — `@/components/ThemeProvider` would never find
 * `src/components/ThemeProvider.tsx`.
 *
 * `mux.js` is vendored: MoonTV installed it as `github:Momo707577045/mux.js`, a
 * git clone rather than an npm package, and the code depends on that fork's
 * `mp4.Transmuxer` behaviour. `src/vendor/muxjs` is that clone, pruned to its
 * `lib/` tree (its `files` field), and aliased to its browser entry.
 */
export default defineConfig({
  // `index.html` is the entry without naming it: Vite's build entry defaults to
  // the project's `index.html`, and that is the document we want it to produce.
  plugins: [
    {
      name: 'moontv-resolve',
      resolveId: (source, importer) => {
        if (source === 'mux.js') {
          // `.cjs` rather than the clone's `.js`: rollup only gives a CJS
          // module a default export when it knows it is CommonJS, and
          // `import muxjs from 'mux.js'` needs that default.
          return path.join(vendorMuxjs, 'lib', 'index.cjs');
        }
        if (source.startsWith('@/')) {
          const base = path.join(srcDir, source.slice(2));
          for (const ext of ['.ts', '.tsx', '.js', '.mjs', '.css']) {
            const candidate = base + ext;
            if (fs.existsSync(candidate)) return candidate;
          }
          if (fs.existsSync(base)) return base;
          return null;
        }
        // Nothing else is intercepted. In particular rollup resolves a CommonJS
        // `require()` internally and never calls this hook for it, so Node's
        // extension search cannot be restored here: the vendored mux.js clone
        // carries explicit `.js` filenames in its `require()` calls instead
        // (see the note in `src/vendor/muxjs/lib/index.cjs`).
        return null;
      },
    } satisfies Plugin,
  ],
  // Empty plugin list rather than the project's `postcss.config.js`: that file
  // belongs to the Tailwind CLI that compiles `src/app/globals.css`, and Vite
  // searches the project root for it, so it would otherwise run tailwindcss +
  // autoprefixer over every stylesheet in the graph (`@fontsource/inter`'s
  // included). Vite's own url rewriting and minification still apply.
  css: {
    postcss: { plugins: [] },
  },
  build: {
    outDir: 'dist',
    // `target` is the esbuild syntax level. Output is ESM by default for an
    // app build (there is no `build.format`; that key belongs to library
    // mode's `build.lib.formats`, and an unknown key is silently ignored).
    // Module-preload polyfill likewise lives at
    // `build.modulePreload.polyfill` (default true), not `build.polyfill`.
    target: 'es2020',
    minify: true,
    // Vite runs @rollup/plugin-commonjs over the files matching this list, and
    // the default is `/node_modules/` alone. The vendored mux.js clone lives in
    // `src/`, so without adding it here its `require()` calls survive into the
    // ESM output as literal calls — `require` is undefined there, the module
    // throws at import time, and the document the backend serves stays blank.
    commonjsOptions: {
      include: [/node_modules/, /src\/vendor\/muxjs\//],
      extensions: ['.js', '.cjs'],
    },
  },
});
