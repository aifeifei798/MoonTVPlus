# MoonTVPlus — the port plan

MoonTV (`/home/feifei/develop/MoonTV`, Next.js 14, `VERSION.txt` = `4.2.5`) is being
restructured into two packages that speak the same wire protocol:

    backend/    FastAPI (Python) — the whole `/api/*` surface plus the auth gate
    frontend/   Vite + React — the UI, rebuilt rather than kept as a Next.js app

The point of the split is that nothing about the *wire* changes: TVBox/OrionTV
clients, the browser client libs (`src/lib/db.client.ts` and friends), and the
existing docker deployments keep working. What changes is how the code is
organized and which runtime it needs.

## Rules the port follows

* **Path-compatible drop-in.** Same paths, same query parameters, same response
  shapes, same SSE/NDJSON framing. `/api/*` is defined by the client libs, not by
  a spec, so the port is done by reading them.
* **Env-var names are identical.** `PASSWORD`, `USERNAME`,
  `NEXT_PUBLIC_STORAGE_TYPE`, `NEXT_PUBLIC_SITE_NAME`, … keep their names so an
  existing `.env`/docker-compose file works unchanged.
* **The old migration backups must still be readable.** `.dat` files are
  `pako.deflate` + CryptoJS `AES.encrypt(data, passphrase)` — i.e. an OpenSSL
  `Salted__` header, `EVP_BytesToKey` with MD5, AES-256-CBC. `moontv/crypto.py`
  implements that byte-for-byte.
* **Data model stays plain dicts.** The JS code mutates config/db objects in
  place and relies on JSON key order; pydantic models would change both.
  `strip_none()` on serialization reproduces `JSON.stringify` dropping `undefined`.
* **The auth cookie is byte-identical**, including `httpOnly: false`, because
  `db.client.ts` reads it back out of `document.cookie`.

## Backend

    moontv/env.py              process.env accessors (env / env_str / env_bool / env_number)
    moontv/types.py            src/lib/types.ts
    moontv/admin_types.py      src/lib/admin.types.ts
    moontv/config.py           src/lib/config.ts (refine_config / init_config / get_config)
    moontv/config.json         the only file-config source, overridable by MOONTV_CONFIG_PATH
    moontv/storage/db.py       src/lib/db.ts — the `Db`/getStorage() capability checks
    moontv/storage/sqlite.py   src/lib/d1.db.ts — table-based, schema from d1-init.sql
    moontv/storage/redis.py    Redis/Kvrocks backend
    moontv/auth.py             src/lib/auth.ts + src/app/api/login HMAC + src/middleware.ts
    moontv/request_origin.py   src/lib/request-origin.ts (Forwarded / X-Forwarded-*)
    moontv/url_guard.py        src/lib/url-guard.ts (SSRF private-host blacklist)
    moontv/crypto.py           src/lib/crypto.ts (CryptoJS/OpenSSL compatible)
    moontv/yellow.py           src/lib/yellow.ts (content filter)
    moontv/runtime.py          window.RUNTIME_CONFIG + the layout props
    moontv/routes/             src/app/api/**/route.ts, one module per route
    moontv/static.py           the catch-all that serves the built frontend
    moontv/main.py             app assembly + the auth-gate middleware

Storage is **sqlite by default** (`MOONTV_DB_PATH`, default `moontv.db`) with
**Redis/Kvrocks** as the alternative. Upstash REST and Cloudflare D1 bindings are
dropped: D1 is already table-based, so `d1.db.ts` ports to SQLite directly rather
than going through `redis-base.db.ts`.

Routes register themselves: each route module exports `ROUTES` as
`(path, handler, methods)` tuples, and `main.py` adds them in order with the
catch-all last, so API routes always win.

Two scripts sit at the repo root. `install.sh` bootstraps `backend/.venv` with
the dependency list read from `backend/pyproject.toml` (version constraints go
to pip as-is) and runs `pnpm install`/`pnpm build` for the frontend; `server.sh`
is process control (`start`/`stop`/`restart`/`status`/`run`/`log`/`cron`),
honoring the old image's `HOSTNAME`/`PORT` names and defaulting `MOONTV_DB_PATH`
to an absolute path so the SQLite file does not move with the caller's cwd.

## Frontend

`frontend/` is a Vite app whose **entry is `index.html`**. That is the document
the backend serves at every non-API path, and because it is the built
`frontend/dist/index.html`, Vite's hashed script/stylesheet URLs survive into it
— which is what lets the backend cache `/assets/*-<hash>.*` forever.

    frontend/index.html            the app document (Vite entry)
    frontend/src/main.tsx          port of src/app/layout.tsx: the component tree
                                   + the page picked from location.pathname
    frontend/src/shims/            navigation.ts, link.tsx, image.tsx, next.ts
    frontend/src/app, src/components, src/lib   copied from MoonTV, trimmed
    frontend/src/vendor/muxjs      the github:Momo707577045/mux.js clone, pruned to lib/
    frontend/scripts/              generate-manifest.mjs, postbuild.mjs

Build: `pnpm build` = generate manifest → `vite build` → Tailwind CLI over
`src/app/globals.css` → `postbuild.mjs` (copies `public/**` and sweetalert2's
stylesheet into `dist/`).

The `@/...` import paths are kept exactly as they were in the MoonTV sources;
`vite.config.ts` resolves them with a small resolver rather than an `alias`,
because an alias only rewrites the path and Vite does not run its extension
search on the rewritten value.

Dependency pins, all of which matter:

  * `react`/`react-dom` are `19.0.0` because the copied sources import as
    `import React, { useEffect, useState } from 'react'` — a default plus named
    imports, which only React 19's ESM packages provide. React 19 also has no
    top-level `ReactDOM.render`, so `src/main.tsx` mounts with
    `createRoot(document.body).render(RootLayout())` — `RootLayout` is *called*,
    because upstream's is a page component Next invoked with `{ children }`.
    And every JSX-using `.tsx` carries an explicit `import * as React from
    'react';` that upstream never needed: Vite's esbuild transform emits bare
    `React.createElement` and injects nothing, while Next.js auto-imported its
    react polyfill.
  * `artplayer` and `artplayer-plugin-danmuku` are pinned to `5.3.0`, not npm's
    latest `5.4.0`: `src/lib/artplayer-setting.ts` patches ArtPlayer 5.3.0's
    DOM internals.
  * `mux.js` is vendored rather than installed — the fork the app depends on
    (`Momo707577045/mux.js`, 5.6.5) is not on npm, whose latest is 6.3.0. Its
    entry is `index.cjs`, because rollup gives a `default` export to CJS files
    only under that extension.
  * `@headlessui/react`, `@heroicons/react`, `framer-motion` and `swiper` are
    absent: nothing in the trimmed tree imports them (swiper arrives
    transitively through artplayer).

## Deliberate behavior changes

These are the places the new shape cannot be identical, called out rather than
papered over:

1. **`localstorage` mode disappears.** There is always a real server DB, so the
   `storageType === 'localstorage'` branch in ~15 admin/user routes becomes
   reachable as a plain 400 and the 9 client-lib branches reading it become dead
   code. `auth.py` keeps the unreachable branch for parity only.
2. **Navigation is a hard reload, not a soft swap.** `router.push` →
   `location.assign`, `router.back` → `history.back`, `usePathname` →
   `location.pathname`, `useSearchParams` → `new URLSearchParams(location.search)`.
   `history.back()` after the login redirect and the `?redirect=` flow therefore
   behave as they did, but the `NavigationLoadingProvider` spinner belongs to the
   document being replaced and does not survive a navigation. It still covers the
   in-page loaders, which call `startLoading()` without navigating.
3. **Unknown paths return the not-found page with status 200**, not 404: the
   server serves one document at every non-API path and does not own the page
   table. The API side is the exception — `static.py: serve_path` answers an
   unmatched `/api/...` path with that same document at **404**, which is what
   Next did and what keeps a client mid-flight on an unported route from
   parsing HTML as a successful JSON body.
4. **`<Link onClick>` callbacks are dropped** (`src/shims/link.tsx`): Next ran
   them right before its soft navigation; a plain anchor navigates on its own and
   the callback cannot run before a hard reload.
5. **`export const metadata` / `export const runtime = 'edge'` in page files are
   inert.** Page metadata lives in `index.html`'s `<head>`; the annotations stay
   so the page files remain the files they were ported from.
6. **`/assets/tailwind.css` and `/assets/sweetalert2.min.css` are not content-hashed**
   (the Tailwind CLI and the sweetalert2 copy write fixed names), so they get the
   short cache window rather than the immutable one.
7. **`manifest.json` is generated at build time from `NEXT_PUBLIC_SITE_NAME`**,
   exactly as MoonTV's `scripts/generate-manifest.js` did; a site renamed at
   runtime keeps the build-time name in the PWA manifest.
8. **`start.js`'s poll-then-hourly-cron loop** becomes a Python boot loop or a
   supervisor cron; `/api/cron` itself is unchanged on the wire.
9. **Deployment matrix collapses** to Docker/self-hosted: Vercel, Netlify, CF
   Pages, Upstash and D1 drop out.
10. **Upstream failure sentences are reproduced only where MoonTV named them.**
    The search routes map three errors explicitly (`请求超时`, `请求失败`,
    `网络错误`) and those are raised verbatim; anything else reaches
    `failedSources[].error` as V8's own message in the old app
    (`Unexpected token '<', "<!DOCTYPE…" is not valid JSON`,
    `Cannot read properties of undefined (reading 'trim')`) and as Python's
    wording here. The array's shape, position and the `Cache-Control` that
    accompanies it are unchanged, so only the human-readable sentence differs.
11. **Admin-side nondeterminism is the only real divergence in B5.** The tvbox
    "random password" was `Math.random` upstream (16 chars of
    `ABCDEFGH…z23456789`); this uses `secrets.choice` over the same alphabet,
    because the value is only ever read back by the panel and never reproduced.
    `Promise.all` over the per-user export reads and the batch writes became
    sequential awaits: sqlite serializes writes anyway, and `on_progress(done,
    total)` fires once per 100-row chunk either way, so the SSE progress events
    land in the same places. A garbage `updateInterval` is `Number(x)` → NaN,
    which `JSON.stringify` wrote as `null` while `strip_none` drops the key
    instead; `configSelfCheck` re-fills it on the next read.
12. **The followings refresh cannot see a client hang up.** Upstream checked
    `controller.desiredSize` after each `writer.write` to bail out of the worker
    pool; Starlette's `StreamingResponse` gives no such signal, so a browser that
    closes the connection mid-refresh keeps the remaining items fetching and only
    drops the events nobody is left to read. `Content-Type` is also pinned by
    hand: Starlette appends `; charset=utf-8` to any `text/*` media type, so the
    route passes `text/event-stream` inside `headers` with `media_type=None` to
    stay byte-exact (the data-migration routes legitimately carry the charset).
    And `split_key` yields `""` where JS had `undefined` for a key with no `+`;
    the client's SSE reader cannot tell the two apart, so the payload stays
    compatible.
13. **B6's wire bugs are kept, its storage bug was not.** `/api/search/ws`
    reports `totalResults: 0` because the per-site `allResults` shadows the one
    `complete` reads, and its 20 s `Promise.race` never fires
    (`searchFromApiStream` is an `async function*`, so the race resolves against
    the generator); both are reproduced. The sqlite backend, on the other hand,
    never committed: the default isolation level held every write uncommitted —
    invisible to other connections, blocking their writes with `database is
    locked`, rolled back on restart — so it runs in autocommit now (each
    statement durable on return, as every D1 query was) with `_run_batch` doing
    an explicit `BEGIN`/`COMMIT` per 100-row chunk to keep D1's `batch()`
    atomicity. `/api/tvbox/config` uses `getRequestOrigin` while
    `/api/tvbox/categories` uses the raw `url.origin`, exactly as upstream.
14. **`batch_import_search_history` is deliberately more forgiving than D1.**
    Upstream's version is a bare `INSERT`, so re-importing a history that
    overlaps the stored one fails the whole 100-row chunk on
    `UNIQUE(user_id, keyword)` — including an export→import round trip run
    twice. The port upserts (`created_at` refresh) and dedupes within the
    batch (first/newest occurrence wins), making imports idempotent. The
    import route's other failure shapes are unchanged.
15. **The perf/robustness pass trades a few microseconds of shape for bounded
    work.** (a) `/api/image-proxy` and `/api/m3u8`'s segment verb now
    `StreamingResponse` in 64KB chunks instead of buffering the whole body, so a
    response carries `Transfer-Encoding: chunked` where it used to carry
    `Content-Length`; statuses, the four cache/CDN headers and the upstream's
    `Content-Type` are unchanged, and the image proxy still mirrors a non-2xx/3xx
    upstream status verbatim. (b) Both proxies cap their bodies (8MB per image,
    2MB per TS segment) and fail fast rather than buffer without limit; an
    image whose `Content-Length` alone declares more than 8MB answers the
    route's existing `{"error": "Error fetching image"}` 500, and an oversized
    TS stream that trips the cap while streaming ends mid-body, which is the one
    case the buffered version could have answered instead. (c) `get_config` has a
    5s TTL cache (invalidated on every write path — `Db.save_admin_config`,
    `init_config`, `reset_config`, `set_cached_config`), so a page load no longer
    re-reads and re-merges `admin_config`; it returns a deep copy, so a caller
    mutating what it got cannot poison the cache. `/api/user/online` is the one
    handler that read-modify-writes the whole config, so it invalidates before
    reading rather than accepting a 5s-old snapshot. (d) `TIMEOUT_MS` for douban
    dropped 10s→4s and the three hosts are raced with `asyncio.wait(FIRST_COMPLETED)`
    instead of serial fallback (the first 2xx/3xx wins, losers are cancelled, the
    raised error is still the last candidate's); the top250 HTML scrape is fed
    only its first 512KB. (e) The shared `httpx` client now has
    `Limits(max_connections=100, max_keepalive_connections=20)` and a 10s
    default timeout, and the two `detailCache` dicts evict FIFO at 500 entries.
16. **去广告 now deletes the ad segments instead of the seam marker.** Upstream's
    `filterAdsFromM3U8` removed every `#EXT-X-DISCONTINUITY` line and kept all
    the segments, which is the reported "picture freezes, audio keeps playing
    after an ad": measured on a live source with ffprobe, the ad TS is a
    different encoder profile (content h264 1280×536, ad h264 1920×1080), so
    its SPS/PPS and `moof` differ from the content's; hls.js 1.5.17 emits a
    fresh init segment only at a discontinuity, so deleting the marker splices
    the ad's `mdat` under the content's init segment — the video decoder
    produces no frames (frozen picture) while the AAC track, whose params do
    match, keeps decoding (sound). The port instead splits the playlist at the
    markers into groups, signs every segment URI by host + directory (query and
    file name dropped), requires a dominant signature covering ≥60% of the
    segments before it will judge anything, and drops a group only when its
    modal signature differs from the dominant one **and** it owns ≤35% of the
    timeline (durations, falling back to segment counts) — a run that big is
    content, not an ad. One `#EXT-X-DISCONTINUITY` is re-emitted at every seam
    that still has segments on both sides, so hls.js resets its codec state
    there instead of splicing. Markers are matched with
    `#EXT-X-DISCONTINUITY(?!-)` so a `#EXT-X-DISCONTINUITY-SEQUENCE:n`
    directive stays a directive (hls.js's own slow regex tries the `-SEQUENCE`
    alternative first, and eating it would desync its numbering). When nothing
    qualifies the manifest is returned byte-identical, markers included, so a
    source without ads is untouched. Verified on the live 暴风 manifest (363
    segments, 3 markers → 354 segments, 1 marker, exactly the 26.04s of ads
    removed, zero content segments lost) plus a 14-case fixture table covering
    no markers, no dominant signature, same-directory ads, absolute-URL ads on
    another host, leading/trailing/multiple ad runs, an empty group, a
    `-SEQUENCE` directive and a CRLF manifest (hls.js's URI capture is
    `(\S[^\r\n]*)`, so line endings are preserved rather than normalised).

## Milestones

Backend: B0 foundation, B1 storage, B2 auth + server-config and B3 search
aggregation (`downstream.py`, `utils.py`, `fetch.py`, `routes/search.py`) are in.
B5 admin is in: `routes/admin.py` (config/reset/config_file/user/group/source/
category/site/subscription/tvbox), `routes/data_migration.py` (SSE export +
import over the `pako.deflate` → base64 → AES pipeline) and `routes/config.py`
(`/api/config/{sources,custom_category}`), plus `version.py` for the backup's
`serverVersion` stamp and `Db.get_user_password` for the export's per-user read.
B4 user state is in: `routes/user_state.py` (playrecords, favorites, followings,
`/api/user/online`) and `routes/user_history.py` (searchhistory, skipconfigs,
today-updated), and `routes/followings_refresh.py` over the new
`fetch_video_detail.py` helper — 46 checks green on the record stores, 25 on the
refresh. B6 is in: `moontv/douban.py` plus `routes/douban.py` (the three
douban endpoints; top250 reads rexxar `subject_collection` JSON first, HTML scrape
as fallback) and `routes/image_proxy.py`,
verified against live douban (top250 returns 25 with 肖申克 first); fetches fall
back official → `cmliussss.net` → `cmliussss.com`, successes are cached in the
`douban_cache` sqlite table and failures serve stale with `X-Douban-Stale: 1`; then `routes/detail.py` (`/api/detail`),
`routes/search_sub.py` (`search/{one,resources,suggestions,ws}`),
`routes/tvbox.py` (`tvbox/{config,categories}`) and `routes/cron.py`
(fire-and-forget, always success) — 25 wire checks green, the suggestion scorer
byte-identical to node on 4 fixture cases, the `un` base64 decoder
byte-identical on 11 probes, and the tvbox enable → restart → still-enabled
round trip proving sqlite writes are durable. `/api/m3u8` is in as well:
`moontv/m3u8.py` (`parse_m3u8` with master-playlist resolution, `download_ts_segment`)
plus `routes/m3u8.py` (`POST` parse + `GET` proxy, both at `/api/m3u8` as upstream
served them) — durations/URLs byte-compared against node, and verified live
against `test-streams.mux.dev` (64 segments parsed, one 5.3 MB segment proxied
with `application/octet-stream` + year cache). Remaining: B7 browser-side `.dat`
migration, then the Redis/Kvrocks
backends (`create_storage` raises a clear `RuntimeError` until they exist).

Frontend: F0 scaffold + F1 build are in and the built document is served by the
catch-all. Remaining: the A/B diff harness that boots old MoonTV next to the new
backend and replays endpoint fixtures (including SSE event sequences and one real
`.dat` backup).
