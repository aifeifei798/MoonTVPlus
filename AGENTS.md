# AGENTS.md — MoonTVPlus

This repo is a rewrite of MoonTV (Next.js 14, upstream `VERSION.txt` = `4.2.5`) as
a FastAPI backend (`backend/`) plus a Vite + React frontend (`frontend/`). The
original tree lives at `/home/feifei/develop/MoonTV` and is a git repo; **this
repo is not a git repo** and nothing here is committed.

## Read before porting anything

- `PLAN.md` — the port plan, the rules, the deliberate behavior changes, and
  milestones (B0–B6 plus `/api/m3u8` are in; B7 still owes the browser side of
  the `.dat` backup migration, and the Redis/Kvrocks backend is not). Module
  docstrings point at it.
- `/home/feifei/develop/MoonTV/src/lib/**` and `src/app/api/**/route.ts` — the
  source of truth for behavior and the wire format. When docs here disagree with
  the TypeScript, the TypeScript wins.

## Commands

```bash
./install.sh --backend            # backend/.venv + deps read from backend/pyproject.toml
./install.sh --frontend --build   # pnpm install + pnpm build
./server.sh start|stop|restart|status|run|log|cron|test
```

- Always run backend code through `backend/.venv/bin/python`. System `python3`
  has no fastapi/httpx/uvicorn and fails with an `ImportError` that looks like a
  code bug.
- Raw uvicorn form (from the repo root):
  `backend/.venv/bin/python -m uvicorn moontv.main:app --app-dir backend --host 0.0.0.0 --port 3000`
  The scripts honor upstream's Docker names `HOSTNAME`/`PORT`, but bash sets
  `HOSTNAME` unexported to the machine name, so only an **exported** one counts
  (`server.sh` tests it with `declare -p HOSTNAME`).
- Script-local knobs use the `MOONTVPLUS_` prefix so they cannot collide with
  upstream's names (`MOONTVPLUS_ENV_FILE` = `./.env`, `MOONTVPLUS_RUN_DIR` = `.run`,
  `MOONTVPLUS_DATA_DIR`, `MOONTVPLUS_LOG_FILE`, `MOONTVPLUS_RELOAD`, …); the app-facing
  ones keep upstream's names (`MOONTV_DB_PATH`, `MOONTV_FRONTEND_DIR`).
- Frontend, from `frontend/`: `pnpm build` runs
  `gen:manifest → vite build → tailwindcss -i src/app/globals.css → postbuild.mjs`.
  The order matters: a bare `vite build` leaves `dist/` without `public/**`
  (favicon, `mitm.html`, `sw.js`, icons) and without `sweetalert2.min.css`.
  `pnpm dev` is `vite build --watch`; `pnpm typecheck` is `tsc --noEmit` and is
  green (typescript + `@types/react/react-dom/node/he` are installed;
  `tsconfig.json` uses `jsx: react-jsx` + `moduleResolution: bundler`). There
  are no `lint`/`format` scripts — eslint/prettier were never configured for
  the copied tree, so the scripts were removed rather than left failing.
- The backend serves `frontend/dist`, so a frontend edit is invisible until it is
  rebuilt (`pnpm build` in `frontend/`, or `./server.sh build`). Compare
  `stat -c '%y' frontend/src/**` against `frontend/dist/index.html` before
  blaming the browser: a stale `dist` is the usual cause of "the fix is in the
  source but the page did not change".
- `server.sh` options (`--env`, `--host`, `--port`, `--log`) may go before or
  after the command. `--` (verbatim uvicorn args) must go **after** it, because
  everything past `--` is forwarded and the command would be swallowed.
- pytest: `backend/.venv/bin/python -m pytest` from `backend/`
  (`testpaths = ["tests"]`, `asyncio_mode = auto`). There is no `tests/` dir yet,
  so pytest only prints a testpaths warning and collects nothing.

## Wiring that filenames do not show

- **One origin.** FastAPI serves `/api/*` and the built UI at every other path.
  The catch-all `/{full_path:path}` is registered **last** in `main.py`, so API
  routes always win; an unknown non-API path returns the app document with
  status 200, not 404.
  The same fall-through is closed on the API side: `static.py: serve_path`
  answers an unmatched `/api/...` path with the app document at **status 404**
  (`_is_api_path`), matching Next's not-found page. Without it a typo or an
  unported milestone returned HTML at 200 and the admin panel read it as a
  successful JSON body (`Unexpected token '<', "<!-- The a"…`).
- Route modules export `ROUTES = [(path, handler, methods)]`. Adding a milestone
  means importing the module in `main.py` and appending it to `ROUTE_MODULES`
  (order matters, catch-all stays last).
- The auth gate is a single HTTP middleware (`auth.py: auth_gate_response`), not
  per-route checks: 307 → `/login?redirect=…`, 401 for unauthenticated `/api/*`,
  307 → `/warning` when `PASSWORD` is unset. `/assets` is in `SKIP_PATHS` (the
  Vite stand-in for Next's `/_next`) so the bundle loads before login.
- Storage: `storage/db.py` `getStorage()` is a per-process singleton;
  `storage/sqlite.py` ports `d1.db.ts` with the `d1-init.sql` schema verbatim,
  `SEARCH_HISTORY_LIMIT = 20`, `BATCH_LIMIT = 100`.
- Data model is plain Python dicts plus `strip_none()` at serialization
  (`None` == absent field, matching `JSON.stringify` dropping `undefined`).
  Do not introduce pydantic models: the JS mutates objects in place and relies
  on JSON key order.
- **Admin surface (B5)** is three modules, each exporting `ROUTES`: `routes/admin.py`
  (the ten config-mutating paths), `routes/data_migration.py` (SSE export/import),
  `routes/config.py` (`/api/config/{sources,custom_category}`). `/api/admin/subscription`
  and `/api/admin/tvbox` export a GET **and** a POST upstream, so they are two
  handlers registered on the same path with method-specific `ROUTES` entries.
- Every config-mutating route reads `await get_config()`, mutates that dict in
  place, and writes the whole blob back with `await Db.save_admin_config(...)`
  (upstream's `setAdminConfig`): the stored blob is whatever the panel sent, not
  a reconstruction. The data-migration **export** reads `Db.get_admin_config()`
  (the raw blob), not `getConfig()` — that is what makes a re-import round-trip.
- `Db` in `storage/db.py` is an **instance** (`Db = DbManager()`), mirroring
  `export const db = new DbManager()`. As a class it silently ate the first
  argument as `self`, so every non-owner login/register path answered
  `数据库错误` 500 — the owner path hid it because it short-circuits on env vars.
- The `storageType === 'localstorage'` guard is kept as a plain 400
  (`不支持本地存储进行管理员配置` / `不支持本地存储进行数据迁移`): unreachable with
  the ported backends, but it is on the wire and the panel branches on it.

## Gotchas

- Env names are upstream's, including the inconsistency: `PASSWORD` and
  `USERNAME` are **not** `NEXT_PUBLIC_`-prefixed; `NEXT_PUBLIC_SITE_NAME`,
  `NEXT_PUBLIC_STORAGE_TYPE`, … are. A wrong name silently turns the gate into
  a 307 → `/warning`.
- A browser that lands on `/warning` after `./server.sh start` means `PASSWORD`
  never reached the process — that is the gate working as designed, not a bug to
  chase in the frontend. `server.sh start` prints the same note; the value comes
  from the shell or from `./.env`, which `server.sh` parses dotenv-style (so
  `NEXT_PUBLIC_SITE_NAME=MoonTVPlus Demo` keeps its spaces, unlike `source`).
- The `./.env` example at the repo root has four rules that fail silently, all
  verified by replaying the parser: no inline comments (`PASSWORD=abc  # x` sets
  `abc  # x`), no indented assignments (`  KEY=value` is skipped and the file
  looks fine), values are **not trimmed** (a trailing space in `PASSWORD=`
  breaks login and is invisible in both the file and the browser), and `KEY=`
  with nothing after it sets an empty value, which is falsy to the app and so
  clears a value you exported in the shell. The file beats the shell for every
  name; only `--host`/`--port` beat it. `DOCKER_ENV`, non-prefixed `SITE_NAME`,
  `UPSTASH_URL` and `UPSTASH_TOKEN` are read by upstream but ignored here, so
  leaving them in an old `docker-compose` block is harmless.
- Import depth: modules directly in `moontv/` use `from .config`; modules in
  `moontv/routes/` or `moontv/storage/` use `from ..config`.
- The catch-all handler must annotate its param `request: Request`. Inside a base
  HTTP middleware the request is a `_CachedRequest` — use `request.url`
  (`.query` excludes the `?`), never `request.query_string`.
- Starlette's `TestClient` follows redirects by default, so a 307 auth failure
  looks like a 200 `text/html` response. Assert with `allow_redirects=False`.
- Starting the server creates `backend/moontv.db`; `server.sh` sets
  `MOONTV_DB_PATH` to an absolute path so the file does not follow the caller's
  cwd.
- `sqlite.py` runs in autocommit (`isolation_level=None`): every statement is
  durable on return, the way each D1 query was. The default isolation level
  held every write uncommitted — invisible to other connections, blocking their
  writes with `database is locked`, and rolled back on restart — so a missing
  commit here is silent total data loss, not a perf detail. Only `_run_batch`
  manages its own transaction (explicit `BEGIN`/`COMMIT` per 100-row chunk,
  `ROLLBACK` on failure, like D1's `batch()`).
- `cryptography` 50: `Cipher.decryptor()` / `encryptor()` are bound methods, not
  properties. Raw DEFLATE for `.dat` backups is
  `zlib.compressobj(6, zlib.DEFLATED, -15)` / `zlib.decompress(data, -15)`.
- `fetch.py` uses `trust_env=False` (browser `fetch` ignores env proxies) and
  `follow_redirects=True`; `fetch_with_timeout` takes ms, `None` → 30000, and
  NaN/0 abort immediately like `setTimeout(abort, NaN)`.
- Optional result fields (`poster`, `class`, `type_name`, `douban_id`) are copied
  only when the upstream key is present (`"k" in item`), so JS `null` stays
  `null` while absent stays absent. `strip_none` is **not** used in
  `map_item_to_result` / `get_detail_from_api`.
- JS number/string semantics live in `utils.py` (`js_parse_int` returns `None`
  for NaN, and its leading-whitespace regex class must be `*` not `+`).
- Ported regexes use non-capturing groups because JS `match(/…/g)` returns whole
  matches.
- `python-multipart` is a real dependency, not a nice-to-have: only the data-migration
  import route parses a multipart form, and without it `request.form()` raises
  inside the route. Starlette's `UploadedFile.read()` is **async** — `await` it,
  otherwise the body is a coroutine and the backup decrypts to garbage.
- SSE framing is `data: ${JSON.stringify(x)}\n\n` (`json_dump`, so the same
  separators as JS). Both data-migration routes send
  `text/event-stream; charset=utf-8` + `no-cache, no-transform` + `X-Accel-Buffering: no`
  and the export streams the encrypted backup in 64 KB **character** chunks of the
  base64 string, not bytes. Progress percentages are `Math.round` (`_js_round`),
  not Python's banker's `round`.
  `/api/followings/refresh` is the one SSE route whose upstream header had **no**
  charset, and Starlette's `StreamingResponse` appends `; charset=utf-8` to any
  `text/*` `media_type` — put the content type in `headers` to keep it exact.
  That route keeps upstream's `CONCURRENCY = 5` worker pool (an `asyncio.Queue`
  the response generator drains, plus a shared `source+id` task cache standing in
  for the `Map<string, Promise<…>>`), so events really do arrive per finished
  entry; what it cannot reproduce is upstream's `controller.desiredSize` check,
  so a client that disconnects mid-refresh does not stop the work — it only
  drops the remaining events.
- `/api/search/ws` pins its content type in `headers` the same way (upstream
  also sent `text/event-stream` bare). Two of its shapes are upstream bugs kept
  byte-exact: `totalResults` in `complete` is always 0 (the per-site `allResults`
  shadows the outer one), and the 20 s `Promise.race` never fires
  (`searchFromApiStream` is an `async function*`, so the race resolves against
  the generator object) — there is no per-source cap here either.
  `/api/tvbox/config` reads the origin via `getRequestOrigin` but
  `/api/tvbox/categories` uses the raw `url.origin`; behind a proxy the two
  disagree exactly as upstream. The `un` base64 decode is a quantum loop that
  never throws (skips non-alphabet bytes, stops at `=`, drops dangling bits,
  U+FFFD for bad UTF-8) — verified byte-identical against node on 11 probes —
  so the `参数 un 非法` branch stays dead like upstream's. Suggestion scoring
  (`_suggest_page`) is verified identical against node on 4 fixture cases; the
  only accepted gap is `len()` vs UTF-16 `.length`.

## Frontend quirks

- `frontend/src/lib` is **24 of upstream's 39** modules: only the browser-reachable
  closure of `src/app` + `src/components` was copied. The server-only ones
  (`config.ts`, `crypto.ts`, `d1.db.ts`, `db.ts`, `douban.ts`, `downstream.ts`,
  `fetchVideoDetail.ts`, `redis*.db.ts`, `request-origin.ts`, …) were deleted
  because `backend/moontv/` owns that logic — do not re-add them to the frontend
  when porting a route.
- `frontend/src/shims/` replaces only `next/navigation`, `next/link` and
  `next/image` (`navigation.ts` → `location.assign` / `history.back`,
  `usePathname` → `location.pathname`, `useSearchParams` →
  `new URLSearchParams(location.search)`); `next-themes` is the real package.
  The link shim passes `onClick` through as a React listener (the nav bars'
  spinner/active updates depend on it); the image shim returns a ref cleanup
  so re-renders do not stack duplicate `load`/`error` listeners.
  Next.js here is genuinely multi-page, which is why those three shims have to
  keep `history` populated the same way.
- The entry is `frontend/index.html`; Vite's build entry defaults to it. The
  backend serves the built `dist/index.html` with `__SITE_NAME__`,
  `__RUNTIME_CONFIG__` and `__RUNTIME_LAYOUT__` substituted at serve time.
- `src/main.tsx` mounts with `createRoot(document.body).render(...)`. In `react-dom`
  `19.0.0` `createRoot` is exported by the **`react-dom/client` subpath**, not by
  `react-dom` (whose public exports are `createPortal`, `flushSync`,
  `__DOM_INTERNALS…`, `version`, …). `import { createRoot } from 'react-dom'`
  typechecks and bundles fine but binds `undefined`, so the mount throws and the
  served document stays an empty white page — the one failure mode the
  `TestClient` harnesses cannot see. `createPortal` *is* top-level, so the
  `createPortal` imports in `src/app/play/PlayClient.tsx` and the components are
  fine as they are.
- The mount **calls** `RootLayout`: `createRoot(document.body).render(RootLayout())`.
  Upstream's `RootLayout` is a page component Next invoked with `{ children }`,
  so passing the function itself makes React treat the render value as a leaf
  and produce **no DOM at all**: the module evaluates with no error, `window.__errs`
  stays empty, and the only body mutation is `childList BODY +0 -1` (the root
  clearing its whitespace text node). A blank page with that one mutation means
  the render value, not an exception.
- React 19 calls an **element-level `ref` twice**: with the DOM node when the
  element is attached, and with **`null`** when it is detached (`safelyDetachRef`
  in `react-dom`). `src/shims/image.tsx` attaches `load`/`error` listeners from
  such a ref, so it must start with `if (element === null) return;`. Without that
  guard any re-render that drops an `<img>` — a douban refresh, a search that
  returns a different set — throws inside the commit phase and blanks the whole
  page **after** it has already painted, which is the symptom "white screen, a
  refresh fixes it": intermittent, only on detach, and the reload renders a fresh
  set with no detach to fail. The `TestClient` and the CDP first-load probe both
  pass; only a re-render reproduces it.
- Every JSX-using `.tsx` needs `import * as React from 'react';` in the source.
  Vite's esbuild TS transform emits bare `React.createElement` / `React.Fragment`
  and injects nothing; Next.js had auto-imported its react polyfill, so the
  copied upstream files never had the binding. Without it the bundle keeps
  ~1600 unresolved `React.` references and the mount throws
  `ReferenceError: React is not defined` — no build error, and no typecheck to
  catch it (`tsc` is not installed). Of the 57 `.tsx` files, 53 needed it: the
  detector must include the fragment shorthand `<>` (`components/PageLayout.tsx`
  contains only `<>`, which a `<[A-Za-z]` regex misses), must skip
  `components/ThemeProvider.tsx` (already has the import, and a duplicate would
  be a redeclaration), and must skip the three effect-only components with no
  JSX (`ServiceWorkerRegistration`, `SubscriptionAutoUpdate`, `UserOnlineUpdate`).
  `esbuild: { jsxInject: … }` exists in Vite 6.4.4 but also fires on the
  vendored `.cjs`/`.js` muxjs tree, so the per-file import is the safe form.
- `@/...` imports are resolved by a Vite plugin `resolveId` hook, not by `alias`
  (an alias skips Vite's extension search, so `@/components/ThemeProvider` would
  never find the `.tsx`).
- `css: { postcss: { plugins: [] } }` is deliberate: `postcss.config.js` belongs
  to the Tailwind CLI, and Vite would otherwise run tailwind over every
  stylesheet in the graph.
- `build.target = 'es2020'` is the esbuild syntax level; output is ESM by
  default for an app build (there is no `build.format` outside library mode,
  and no `build.polyfill` in Vite 6 — both were removed after being silently
  ignored).
- `mux.js` is vendored at `src/vendor/muxjs` (the `Momo707577045/mux.js` fork,
  not on npm) and its entry is `index.cjs` because rollup only gives a CJS
  module a `default` export under that extension.
- Rollup 4.64.3 **never resolves a CJS `require()`** — it leaves every one
  literal in the output (proved with the two-file `/tmp/opencode/rtest.cjs`).
  Vite therefore has to apply `@rollup/plugin-commonjs` to the file, and that
  only happens for paths matching `build.commonjsOptions.include`, whose default
  is `[/node_modules/]`. `vite.config.ts` adds `/src\/vendor\/muxjs\//`; without
  it the shipped bundle still contains live `require()` calls and the player
  dies at mount. Keep the exact `.js` filenames inside those `require()`s —
  Node's extension search is not reproduced, and a hoisting codemod does not
  help without the plugin.
- Pins that must not be "upgraded": `react`/`react-dom` `19.0.0` (copied sources
  use a default plus named imports), `artplayer` and
  `artplayer-plugin-danmuku` `5.3.0` (not npm's 5.4.0 —
  `src/lib/artplayer-setting.ts` patches 5.3.0 internals).
- Never put `isolate` on the player wrapper (`PlayClient.tsx`). Artplayer's web
  fullscreen is `position: fixed; inset: 0; z-index: 9999` on the player node,
  and `isolation: isolate` traps that inside the wrapper's stacking context —
  the `sticky z-50` top nav then paints above the fullscreen player ("网页全屏
  上面导航还在"). Verified live with a CDP `elementFromPoint` probe: with
  `isolate` the hit is inside `<header>`, without it the hit is the player.
- Player audio: default volume is 0.7 (`volume:` option + `lastVolumeRef`, which
  restores it across episodes); "声音增强" is a GainNode 2x boost
  (`src/lib/volume-boost.ts`), default ON, toggled from the settings panel.
  Safe to leave on: the video element already carries
  `crossOrigin='anonymous'`, so anything that plays is CORS-clean and the
  MediaElementSource can't newly silence it; when WebAudio is unavailable
  `ensureVolumeBoost` returns false and the switch flips back. The settings
  panel also has an "音量" range slider — the control-bar volume popup is
  hover-only CSS, unreachable on touch, so this is the mobile volume control.
- Player fullscreen on iPhone: HLS plays through MSE, where
  `video.webkitEnterFullscreen()` is a no-op, so the native fullscreen button
  appears dead. A capture listener on `.art-control-fullscreen` falls back to
  `fullscreenWeb = true` when no fullscreen mode engages within 600ms
  (desktop/Android go native and never hit the fallback). `theme` is `#0071e3`.
- Palette is Apple-style, not Tailwind-default: brand accent is the `apple`
  blue scale in `tailwind.config.ts` (`600 #0071e3` / hover `500 #0077ed` /
  dark `400 #2997ff`; the old `green-*` brand is gone, migrated shade for
  shade). Surfaces: light `#ffffff` page + `#f5f5f7` wells, borders `#d2d2d7`;
  dark `#000000` page + `#1d1d1f` cards + `#2d2d2f` hovers, borders `#424245`.
  The body gradient was removed in `src/app/globals.css`. Semantic colors stay:
  red errors, yellow warnings/ratings, purple source badges. Do not reintroduce
  `green-*`, `emerald-*`, `lime-*` or `teal-*`.
- pnpm 12 gates dependency build scripts. `frontend/pnpm-workspace.yaml` still
  carries the unresolved `allowBuilds: esbuild: set this to true or false`
  placeholder; this machine works because `dangerouslyAllowAllBuilds=true` is set
  globally. On a fresh machine use `install.sh --allow-builds` or set that key.

## Images and douban (what config owns, what the port does not)

- `frontend/src/lib/utils.ts: processImageUrl` decides every douban `<img>` src
  from `getDoubanImageProxyConfig()`: `localStorage.doubanImageProxyType` (the
  in-app `UserMenu` switch) wins over `RUNTIME_CONFIG.DOUBAN_IMAGE_PROXY_TYPE`
  (env `NEXT_PUBLIC_DOUBAN_IMAGE_PROXY_TYPE`, or the admin panel's
  `SiteConfig.DoubanImageProxyType`), default `direct`. The values are
  `direct` / `server` / `img3` / `cmliussss-cdn-tencent` / `cmliussss-cdn-ali` /
  `custom`, and only `doubanio.com` URLs are touched.
- **`direct` is broken by douban, not by this repo.** It points the `<img>`
  straight at `img*.doubanio.com`, which is hotlink/bot protected: `curl` gets
  403 and a bare `httpx` GET gets **418 with 14 bytes**, so the browser shows
  broken images. `server` rewrites to `/api/image-proxy?url=…`, which sends
  upstream's `Referer: https://movie.douban.com/` + Chrome 121 UA
  (`routes/image_proxy.py: IMAGE_HEADERS`) and returns the real JPEG. So "posters
  do not load" is a config question: set `DoubanImageProxyType=server`.
  Independently, any `http://` image URL always goes through `/api/image-proxy`
  (the mixed-content fallback), whatever the mode.
- `/api/image-proxy` on the wire: 200 + upstream `Content-Type` + half-year
  cache headers; no `url` → `{"error":"Missing image URL"}` 400; upstream 404 →
  `{"error":"Not Found"}` 404; unreachable → `{"error":"Error fetching image"}`
  500. It returns raw bytes, not a JSON envelope.
- Douban resilience (added over upstream, same wire shape): server fetches try
  official → `cmliussss.net` → `cmliussss.com` (`moontv/douban.py`); successes
  land in the `douban_cache` sqlite table and failures serve the last good
  `list` with `X-Douban-Stale: 1` instead of a 500 (`moontv/douban_cache.py`,
  cold miss still 500s); `top250` reads rexxar `subject_collection` JSON first,
  HTML scrape as fallback; and the frontend's `direct` branches retry `/api`
  via the Tencent mirror in the browser (`douban.client.ts`). If the site still
  shows stale data, check the header — it means douban itself is down.
- Douban latency is not the problem: `/api/douban/categories` 0.15–0.20 s,
  `/api/douban` 0.74 s, `/api/douban/recommends` 0.12 s. An **empty `list`** from
  `recommends` is douban's own anonymous response, and `/ranking`'s stuck
  "加载中..." is not a port gap either — `getDoubanCollection` is client-side
  only (no server route upstream) and the browser's CORS call to `m.douban.com`
  fails with `DoubanProxyType: direct`, same as upstream.
- `lain.bgm.tv` poster 500s in the server log here are this environment's network
  (`ConnectError [Errno 101] Network is unreachable` for both schemes), not a
  port bug — upstream would 500 identically.

## Verification pattern

Ports are verified by comparing against the TypeScript, not by reading it: a
table of fixtures run through both the verbatim TS (in node) and the Python port,
then byte-compared. The harnesses from earlier sessions were scratch files under
`/tmp/opencode` (`cross_downstream.py` ↔ `cross_downstream.cjs`, `smoke2.py`,
`smoke3.py`) and are **not committed** — expect to recreate them. When you do:

- Name them `.cjs`. A `.mjs` is treated as ESM, where `require` is undefined.
- `NODE_PATH=/home/feifei/develop/MoonTVPlus/frontend/node_modules` is needed to
  `require('he')` and the other installed packages.
- `node -e` sees its args at `argv[1]`; a script file at `argv[2]`.
- Static/catch-all harnesses point the backend at the tree with
  `MOONTV_FRONTEND_DIR` (there are also `MOONTV_FRONTEND_DIST` and
  `MOONTV_INDEX_HTML` overrides in `static.py`).
- Never type non-ASCII into a harness command or file: this machine's I/O path
  silently transcodes CJK in **both** directions, so a `source=暴风` typed into a
  `curl`/`python3 -c` string arrives as two other codepoints and the server
  answers `无效的API来源` while an ASCII `source=IK` passes — the same reason a
  CDP play page reported `未找到匹配结果` for a source that was configured and
  working. Derive the string from data instead (the `SourceConfig` key out of
  `Db.get_admin_config()`, the title out of the source's own API response) and
  pass it on as percent-encoded ASCII, which is never mangled.
- A `/play` URL needs `stype=tv` or `stype=movie`, not the numeric `stype=1`:
  `usePlayEngine.ts` puts `stype` straight into `searchType` and the client's
  `typeMatch` is `(searchType === 'tv' && episodes > 1) || (searchType === 'movie'
  && episodes === 1)`, so any other value filters every search result out and the
  page renders `未找到匹配结果` with no `<video>` and no error in `window.__errs`.
- The CDP browser cannot load these sources' manifests, so the ad filter is not
  measurable in it: the CDN answers the manifest XHR with **404** and no
  `Access-Control-Allow-Origin`, so hls.js fails with
  `corsErrorStatus.corsError = "MissingAllowOriginHeader"` and `hls.levels`
  stays empty (`currentLevel` -1) even with `--ignore-certificate-errors` on a
  second Chrome. That is the source's CORS plus a stale episode URL, not a port
  gap. Measure the filter offline instead: `/tmp/opencode/adfilter_probe.cjs
  <manifest>` prints segments/markers/duration/drops/seams for a saved manifest,
  and `/tmp/opencode/adfilter_cases.cjs` is the fixture table.

`TestClient` checks the wire, not the runtime: it never executes the bundle, so
mount-time JS errors (see the `createRoot` note above) are invisible to every
harness and only show up as a blank page in a browser. The workaround used here
is a CDP harness (`/tmp/opencode/cdp.cjs`, scratch): start headless Chrome with
`--remote-debugging-port`, install a `MutationObserver` with
`Page.addScriptToEvaluateOnNewDocument` so it exists **before** the bundle runs,
`Page.navigate` to the page, then `Runtime.evaluate` to read the recorded
mutations and `document.body.innerHTML` length. A `+0 -1` body mutation means
React cleared the whitespace text node and rendered nothing — the render value,
not an exception. The harness takes an optional second argument, a raw
`name=value` cookie, and sets it with `Network.setCookie` before navigating;
for authenticated pages get one from `curl -i -X POST /api/login` and pass the
`auth=…` half of the `Set-Cookie` header verbatim (prefixing another `auth=`
doubles it and the gate bounces back to `/login?redirect=%2F`). Keep the
browser alive in a background shell; the harness attaches to it and cannot
start one itself.

## Not ported yet

B6 is **in** — `routes/detail.py` (`/api/detail`), `routes/search_sub.py`
(`search/{one,resources,suggestions,ws}`), `routes/tvbox.py`
(`tvbox/{config,categories}`) and `routes/cron.py` (`/api/cron`, fire-and-forget
like upstream), 25 wire checks green plus the node byte-comparisons above.
`/api/m3u8` is **in** — `moontv/m3u8.py` (`parse_m3u8`, `download_ts_segment`,
node byte-compared on durations/URLs) plus `routes/m3u8.py` (`POST` parse +
`GET` proxy, both at `/api/m3u8` as upstream served them), verified live
against `test-streams.mux.dev` (64 segments, 5.3 MB proxied).
B7 still owes the browser side of the `.dat` backup migration; and the
Redis/Kvrocks backend is missing (`create_storage` raises a plain
`RuntimeError` naming PLAN.md instead of a bare `ModuleNotFoundError`).

B4 user state is **in** — `routes/user_state.py` (`/api/user/online`,
`playrecords`, `favorites`, `followings`) and `routes/user_history.py`
(`searchhistory`, `skipconfigs`, `today-updated`), plus
`routes/followings_refresh.py` and `moontv/fetch_video_detail.py`, verified
against the SSE event vocabulary the client parses.

B5 (admin + `/api/config`) is in and verified end to end: every action of every
admin route was exercised with curl against a logged-in cookie, and the CDP
harness on `/admin` renders the full panel menu with an empty `window.__errs`.
