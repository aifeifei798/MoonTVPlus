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
    moontv/storage/redis_base.py src/lib/redis-base.db.ts — the shared backend
    moontv/storage/redis.py    src/lib/redis.db.ts — the REDIS_URL leaf
    moontv/storage/kvrocks.py  src/lib/kvrocks.db.ts — the KVROCKS_URL leaf
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
16. **去广告 drops the ad segments, and the seam marker is now conditional.** Upstream's
    `filterAdsFromM3U8` removed every `#EXT-X-DISCONTINUITY` line and kept all
    the segments, which is the reported "picture freezes, audio keeps playing
    after an ad": measured on a live source with ffprobe, the ad TS is a
    different encoder profile (content h264 1280×536, ad h264 1920×1080), so
    its SPS/PPS and `moof` differ from the content's; hls.js (installed 1.7.3)
    emits a fresh init segment only at a discontinuity, so deleting the marker
    splices the ad's `mdat` under the content's init segment — the video decoder
    produces no frames (frozen picture) while the AAC track, whose params do
    match, keeps decoding (sound). The port instead splits the playlist at the
    markers into groups, signs every segment URI by host + directory (query and
    file name dropped), requires a dominant signature covering ≥60% of the
    segments before it will judge anything, and drops a group only when its
    modal signature differs from the dominant one **and** it owns ≤35% of the
    timeline (durations, falling back to segment counts) — a run that big is
    content, not an ad. What a seam then gets is decided per seam, not blanket:
    the content on both sides of a cut-out ad shares one encoder AND one
    continuous timeline (ffprobe: `0000074.ts` ends at PTS 301.863 and
    `0000075.ts` starts at exactly 301.863, while the ad restarts at ~1.4s), so
    the splice is a plain gap in an otherwise continuous playlist and a
    `#EXT-X-DISCONTINUITY` there is a lie. hls.js 1.7.3 decides it per fragment
    from the counter alone — `discontinuity = frag.cc !== lastFrag.cc`, and a
    true one runs `resetInitialTimestamp` (`demuxer.resetTimeStamp` +
    `remuxer.resetTimeStamp`) and then splits the audio/video anchor
    asymmetrically (`audioTimeOffset += Math.max(0, Δ)`,
    `videoTimeOffset += Math.max(0, -Δ)`), re-anchoring both tracks at the
    incoming file's own PTS; with the ad's ~1.4s restart that is where the
    reported "audio and picture drift 1-3s after the ad, a refresh fixes it"
    comes from. So a marker is emitted at a splice seam only when the two
    surviving groups' signatures differ; a marker the packager declared between
    two untouched groups is never dropped, since that one may be a genuine codec
    change needing a fresh init segment. `#EXT-X-KEY` is inherited by later
    segments until one is redeclared, so a dropped group's last key line is put
    back at the seam instead of silently handing the following segments the
    previous group's key. Markers are matched with
    `#EXT-X-DISCONTINUITY(?!-)` so a `#EXT-X-DISCONTINUITY-SEQUENCE:n`
    directive stays a directive (hls.js's own slow regex tries the `-SEQUENCE`
    alternative first, and eating it would desync its numbering). When nothing
    qualifies the manifest is returned byte-identical, markers included. Verified
    on two live 暴风 manifests (363 segments/3 markers → 354/0 with exactly the
    26.04s of ads removed; 1345/5 → 1327/0 with 18 ad segments and 52.08s removed
    across two seams) and a 17-case fixture table covering no markers, no
    dominant signature, same-directory ads, absolute-URL ads on another host,
    leading/trailing/multiple ad runs, an empty group, a `-SEQUENCE` directive,
    a CRLF manifest, a splice seam with a real encoder change (marker kept), a
    packager marker between untouched groups (stays) and a dropped group that
    declared a key (key re-emitted). Line endings are preserved because hls.js's
    URI capture is `(\S[^\r\n]*)`.

17. **`/api/admin/source` and `/api/admin/category` really delete config-declared
    entries.** Upstream refused them — `该源不可删除`/`该分类不可删除` for a single
    delete, and `batchDelete` skipped them while still answering `{"ok":true}` —
    so the panel promised a deletion that never happened and the count stayed.
    The refusal was already half a lie: `getConfig` re-merges the admin blob's
    `ConfigFile.api_site` map into `SourceConfig` on every read (config.py:394-416,
    with the same loops in `refine_config` and `reset_config`), so an entry removed
    from the list is rebuilt as `from == "config"` on the next request even with
    the guard gone — a forced removal was resurrected (10 → 9 in the blob → 10
    served). The port drops the guard and prunes the same key out of the blob's
    `ConfigFile` string (`config.py: drop_config_api_site` /
    `drop_config_custom_category`), which is what makes the deletion stick: while
    an admin config exists, `getConfig` reads that blob, not `backend/config.json`
    on disk. A `reset_config`, or uploading a config file that still declares the
    key, brings it back — that is the file's meaning, unchanged. Verified by
    `scripts/source_delete_check.py` (22/22): the count stays down 6s after the
    delete, past `_CONFIG_TTL_S = 5.0`, the pruned key is absent from
    `ConfigFile.api_site`, and a seeded `custom_category` does not come back.
    The panel refused the same thing **on the client**, which is why a backend-
    only fix changes nothing the user sees: `frontend/src/app/admin/page.tsx`
    rendered the 删除 button only when `from !== 'config'` (no button at all on
    these rows) and `handleBatchDelete` filtered the selected keys the same way
    while promising `系统默认源无法删除，将被跳过`. Both guards are gone and the
    batch payload maps every selected key, so the browser actually sends them;
    the shipped chunk proves it (`"config"` survives only in the two `useState`
    defaults, the 删除 button is a direct array child of the row,
    `keys: y.map(O => O.key)`), and clicking the 视频源管理 tab in the CDP
    browser renders 10 删除 buttons for the 10 config sources with an empty
    `window.__errs`.
18. **`POST /api/admin/source/test` is new — upstream had no source health
     surface at all.** A dead source was invisible until a search happened to
     ask for it and reported a `failedSources` entry, so a deployment with half
     its sources dead looked healthy and the panel gave no way to find out.
     It is a separate module rather than a new `action` on `source()`, so that
     switch stays faithful to upstream and the repo's "each milestone module
     exports `ROUTES`" shape holds. The probe is the *same first request a
     search makes* — `api_site["api"] + API_CONFIG["search"]["path"] +
     encode_uri_component(query)` with `API_CONFIG["search"]["headers"]` — so
     a source the check calls alive is a source `/api/search` can actually use,
     and the verdicts agree with the `failedSources` the search already reports
     on this box. `PROBE_QUERY` is **empty**: it asks the site's first page
     rather than a title, which is empirically better than `wd=a` (one source
     answers non-JSON for `a` and would be called dead while it is fine).
     Classification: alive = 2xx + a JSON object with a `list` array — an
     **empty** `list` is a yellow zero-result warning with `count: 0`, not a
     death, because plenty of live sites answer an empty query with no rows;
     dead = `FetchTimeout`/`FetchFailed`/`FetchNetworkError` (upstream's own
     timeout/failure/network sentences, reused verbatim), status ≥ 400 (reason
     `HTTP {status}`), or a body that is not that shape (reason
     `接口格式错误`). The wire is SSE in `followings_refresh.py`'s vocabulary
     (`start` / `item_result` / `item_failed` / `complete`, with `key`, `name`,
     `ms`, `count`, `pagecount` / `reason` / `okCount`, `failedCount`, `total`,
     `checkedAt`) over the same `CONCURRENCY = 5` `asyncio.Queue` pool, so
     events arrive per finished source; the route is **read-only** — it never
     calls `Db.save_admin_config`, so testing cannot reorder or re-enable
     anything. Body is optional `{keys: [...], timeout: seconds}` with
     `DEFAULT_TIMEOUT_S = 10` clamped to `MAX_TIMEOUT_S = 60`. The panel
     persists the run in the `localStorage` key `moontv_source_health`
     (`{at, results}`, the snake-case convention of `db.client.ts`'s
     `PLAY_RECORDS_KEY`) so the badges and the summary banner survive a reload,
     and after a test loads the dead keys into `selectedSources` so the panel's
     batch disable/delete acts on exactly those rows; the banner only hints
     that the dead ones *can* be batch-disabled/deleted rather than claiming
     they are selected, which would be a lie after a reload. Verified by
     `scripts/source_health_check.py` (33/33): framing, one event per source,
     `okCount + failedCount == total`, and all four classifications against a
     local server the harness starts (alive with `count`/`pagecount` read off
     the body, 404 → `HTTP 404`, 200-with-HTML → the route's own format-error
     constant, 5 s with `timeout: 1` → `FetchTimeout`'s message) — the expected
     reason strings are imported from the code, never typed here, and the
     seeded keys are ASCII and deleted afterwards, so the harness restores the
     state it started from.

19. **The mobile player enters fullscreen when the phone turns sideways, and
     锁定 hides the progress bar.** Upstream's `autoOrientation` is only a control
     button that calls `screen.orientation.lock()` — nothing in MoonTV enters
     fullscreen because the device rotated. `usePlayEngine.ts` registers a
     `screen.orientation` `change` listener (with the `orientationchange` fallback
     for old iOS) on touch devices only: landscape enters fullscreen, portrait
     restores it, and the entry prefers native fullscreen with Artplayer's CSS
     `fullscreenWeb` after 600 ms — the same fallback the fullscreen button already
     uses, because HLS on iOS plays through MSE where `webkitEnterFullscreen` is a
     no-op. Two guards keep it from fighting the user: only fullscreen the
     orientation itself entered is exited by rotating back, and a manual exit while
     landscape suppresses re-entry until the next portrait→landscape cycle. Whether
     anything is *actually* fullscreen is read from the DOM
     (`art-fullscreen` / `art-fullscreen-web` / `document.fullscreenElement`), not
     from `art.fullscreen`: Artplayer defines that property as a getter only after
     `video:loadedmetadata`, so assigning to it earlier leaves a plain `true`
     behind and the fallback then believes the player is already fullscreen — which
     is exactly the case when the phone is rotated before the video has loaded. The
     listeners are registered right after the player is constructed rather than in
     `ready` for the same reason. Separately, tapping the player's 锁定 on mobile
     left a stray progress bar: Artplayer pushes the controls and the progress bar
     down by `--art-control-height + --art-bottom-gap + --art-progress-height/4`,
     the controls are clipped by `.art-bottom`'s `overflow: hidden`, and the
     progress bar leaks a sliver inside the player's bottom edge. `globals.css`
     hides `.art-progress` under `.art-lock`, with `!important` because Artplayer
     injects its stylesheet into `head` at runtime, after the Tailwind one. Verified
     by `scripts/cdp_orientation.cjs` (10/10, plus 4/4 with
     `--landscape-at-load`) at 390x844 with an iPhone UA: landscape → 390x844,
     portrait → 390x300, manual exit in landscape then another rotation → stays
     out, next portrait→landscape → in again, and with `art-lock` the progress rect
     is 0x0. Native fullscreen cannot be exercised in headless Chrome
     (`requestFullscreen()` answers "Permissions check failed"), so the harness
     asserts the `fullscreenWeb` fallback — the path iPhone needs anyway.

20. **Responses are gzipped, and the shipped assets are compressed at the source.**
    `main.py` adds `GZipMiddleware(minimum_size=1000, compresslevel=6)` **before**
    the auth gate, so every JSON body over 1KB is compressed. Starlette's own
    exclusion list already leaves `text/event-stream`, images, fonts, video and
    audio alone, which is why the SSE routes keep their exact framing (and why
    `/api/admin/source/test` arrives uncompressed). `static.py` compresses the
    files it serves itself and caches the blob keyed by `(path, size, mtime_ns)`,
    so the middleware sees a `Content-Encoding: gzip` body and passes it through
    rather than compressing twice, and the same response answers `If-None-Match`
    with a 304 (`Vary: Accept-Encoding` on every compressed one). Measured against
    the live server: the assets a first page load asks for are 1,532,061 B on disk
    and 452,869 B on the wire (~70% less), `gunzip` reproduces the file byte for
    byte, and a request with no `Accept-Encoding` still gets the raw bytes.
21. **The search stream no longer busy-polls.** The port's stream generator used to
    drain the per-site tasks with `await asyncio.sleep(0)` in a loop, which burned
    CPU the upstream requests never got. It now pushes events onto an
    `asyncio.Queue` and each site puts a `None` sentinel after its own events, so
    the generator blocks on the next event and draining the queue yields exactly
    the same order. Framing and event vocabulary are unchanged (NDJSON, one JSON
    object per line). Measured over five `stream=1` searches against the ten
    configured sources: 4,615,314 `sleep(0)` wakeups and 11.35 s CPU before, 468
    wakeups and 0.28 s after.
22. **`?compact=1` is an opt-in projection for the search page.** A result carries
    `desc`, `episodes_titles` and the full `episodes` array — on a real query that
    is 347,506 B of which 37,669 B is `desc` and 1,006 entries are episode URLs,
    none of which the search list renders. With `compact=1` those three keys are
    dropped and `episodes_count` is added so the row can still show the episode
    count. It is opt-in per caller because the play page re-searches without it
    (`usePlayEngine.ts`) and needs the full `episodes`. Same 234 results, same ids
    and titles, same 1006 episode total, 85,021 B vs 15,153 B gz. The consequence
    for the panel: `components/VideoCard.tsx` aggregates several sources under one
    card and derived both the episode badge and the `stype` from
    `item.episodes?.length`, so a compact payload would have made every aggregate
    0 episodes and typed every one `stype=tv` — the play page's `typeMatch` then
    filters a 1-episode movie out of its re-search and renders 未找到匹配结果. It
    now reads `item.episodes?.length ?? item.episodes_count ?? 0`, and the probe
    that replays that formula over a real query (3 results, 2 movie aggregates)
    agrees with the full payload on ids, per-item counts and group keys.
23. **`POST /api/user/online` writes the config once per 60 s, not once per page
    view.** Upstream's heartbeat is a read-modify-write of the whole admin blob,
    and the panel posts it on every page load. The debounce skips the write inside
    the window and returns the `lastOnline` already stored rather than a fabricated
    timestamp, so the wire shape and key order are identical.
    `MOONTVPLUS_ONLINE_DEBOUNCE_S=0` restores upstream's write-per-request.
    Verified: ten heartbeats produce one `save_admin_config` call and the same
    `lastOnline` ten times; with the window monkeypatched past 60 s the second write
    happens.
24. **sqlite runs in WAL and the heavy writes leave the event loop.**
    `journal_mode=WAL`, `synchronous=NORMAL` and `busy_timeout=5000` are set on
    every connect (WAL is persistent in the file, `synchronous` is not), and the
    five `batch_import_*` methods plus `clear_all_data` run through
    `asyncio.to_thread`. Without WAL the offloaded import would stall every other
    request; the test that imports 500 records while 50 reads are in flight is what
    proves it does not.
25. **Upstream fan-out is bounded by the httpx pool, not a semaphore.** The shared
    client's `Limits` are now `MOONTVPLUS_UPSTREAM_MAX_CONNECTIONS` (default 64,
    keepalive `min(20, that)`) instead of the hard-coded 100/20 from change 15(e).
    An `asyncio.Semaphore` around per-site fetches was tried and rejected: it is
    `_LoopBoundMixin`-bound, so a harness that builds a new event loop per request
    (`TestClient`) breaks with "attached to a different loop". The pool limit is the
    same bound expressed where the requests actually queue.
26. **The download manager is a lazy chunk, and a 4.8MB unused image is gone.**
    `GlobalDownloadManager.tsx` dynamic-imports `DownloadManager` on the first
    `showDownloadManager`/`addDownloadTask` event and buffers the tasks that arrive
    before the chunk loads (replay deferred one tick so the component is mounted).
    The entry chunk went 692,825 B → 456,555 B; `AddDownloadModal` (207,976 B) and
    `DownloadManager` (28,265 B) are now separate chunks fetched only when someone
    opens the panel. `frontend/public/screenshot1.png` (4,785,287 B) was referenced
    by nothing in the tree and is deleted. `@fontsource/inter`'s `.woff` fallbacks
    were left alone — browsers pick the `.woff2` listed first.
27. **`js_number` returns `None` for NaN.** The docstring already promised it, but a
    Python `float('nan')` is truthy, so every caller's `js_number(x) or 0` kept the
    NaN where JS's `Number(x) || 0` would have produced `0`, and `js_min(NaN, 5)`
    returned 5 where `Math.min` returns NaN. Both now behave like JS.
28. **There is a pytest suite, and CI runs it.** `backend/tests/` (70 tests) covers
    the auth gate, the static/gzip surface, the stream framing and projection, the
    heartbeat, the three-layer `.dat` pipeline cross-checked against
    `openssl -md md5` and node's raw DEFLATE, the m3u8 parser against a local
    playlist server and the committed live fixture, and the sqlite layer. Upstream
    sites are replaced by a local fake `?ac=videolist` media server so the
    assertions are deterministic and offline. `.github/workflows/ci.yml` runs it on
    3.11 and 3.12 and builds the frontend, asserting `dist/` contains the files a
    bare `vite build` would have left out.

29. **The brand assets are regenerated from one geometry.** The upstream logo was
    a 204 KB raster illustration (crescent + retro TV) that did not match the
    Apple-style palette the UI uses. `frontend/scripts/make-logo.py` draws the
    mark from a single set of constants — a crescent (disc `r=195` at `(260,256)`
    minus disc `r=155` at `(360,256)`, so the two tips are the exact circle
    intersections) cradling a play triangle — in apple-600 `#0071e3` and
    apple-400 `#2997ff`, and renders it four ways: `public/logo.svg` (251 B),
    `public/logo.png` (6 KB instead of 204 KB), `public/favicon.ico` (16/32/48
    PNG entries packed by hand, since ImageMagick writes only one frame), and
    `public/icons/icon-{192,256,384,512}.png`, which is the same silhouette
    inverted onto a rounded square — a solid tile stays legible at 16 px on both
    a light and a dark browser chrome. The mark is now actually used: the nav
    bars and the login page render `/logo.svg` next to the site name, so
    `/logo.svg` joins `SKIP_PATHS` in `auth.py` alongside `/logo.png` (the login
    page is drawn by the app itself, before anyone is authenticated). Wire
    parity is unaffected: the manifest still points at the same four icon paths,
    and `/favicon.ico` is still served at `image/x-icon`.

 30. **The control bar collapses when the pointer leaves the player.** Artplayer
    5.3.0 hides the bottom bar when `!controls.isHover && Date.now() -
    controls.timer >= CONTROL_HIDE_TIME` (3 s), but `isHover` is only ever
    refreshed by a `document` `mousemove` (`includeFromEvent(e, $bottom)`). A
    pointer that exits the window straight from the bar never produces a mousemove
    inside the page, so `isHover` stays `true` and the bar stays on screen
    forever. In web fullscreen the player fills the viewport, so "leaving the
    player" *is* "leaving the page" — which is why the symptom shows up only there
    (in normal mode the pointer usually crosses the page around the player first,
    and the bar hides). `usePlayEngine.ts` adds
    `art.on('hover', v => { if (!v) art.controls.isHover = false })`: Artplayer's
    own `hover` event fires on the player's `mouseleave`, and clearing the flag
    lets Artplayer's own timer do the hiding, so the bar still takes ~3 s instead
    of vanishing instantly. Verified by two committed harnesses:
    `scripts/artplayer_hide_probe.cjs` runs the gesture against the pinned Artplayer
    5.3.0 out of `frontend/node_modules` with the listener toggled off by
    `?nofix=1` and the 30 s clip in `fixtures/probe.mp4` — **fix collapses the bar
    at 3000 ms, no-fix never does**; `scripts/cdp_control_hide.cjs` drives the real
    shipped `/play` page (pointer on the bar → `isHover=true`; pointer off the
    viewport → `isHover=false`; bar collapses 2794 ms later; pinning
    `isHover=true` with no following mousemove reproduces the stuck bar). The live
    probe has to make the page actually play, since the hide gate runs inside
    `video:timeupdate`: it destroys `video.hls` (hls.js's `sourceopen` handler
    otherwise puts its blob `src` back), removes `crossOrigin` (the app sets
    `anonymous` via `moreVideoAttr` and there is no ACAO), and inlines
    `fixtures/probe.mp4` as a data URL.

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
with `application/octet-stream` + year cache). The source health check is in as
well: `routes/source_test.py` (`POST /api/admin/source/test`, SSE, read-only)
plus the panel's 测试源 button, badges and banner — 33 checks green, including
the four classifications replayed against a local server the harness starts.
B7 is in as well: `routes/data_migration.py`
writes and reads the `.dat`, and the panel's data-migration tab
(`components/DataMigration.tsx`) is the browser half that downloads it and
posts it back — `scripts/dat_backup_check.py` closes both halves with 97 checks
green: the AES layer byte-compared against `crypto-js@4.2.0` (the library
upstream's `crypto.ts` used) and against `openssl enc -aes-256-cbc -md md5` in
both directions, the DEFLATE layer against node's `zlib` (pako's algorithm and
level), CryptoJS's forgiving base64 parser replayed word-for-word, and a live
export → saved `.dat` file → import round trip that seeds every kind of record
and asserts the deployment is unchanged — the only way to see that the import's
`Db.clear_all_data()`, which deletes all eight tables first, eats nothing.
The Redis/Kvrocks backends are in as well: `storage/redis_base.py` is
`redis-base.db.ts` (one blob per record, `u:<user>:<kind>:<key>`, the search
history the only LIST) and `redis.py`/`kvrocks.py` are 14-line leaves, because
this 4.2.5 tree's `kvrocks.db.ts` uses plain redis commands rather than the
`exh*` extensions — the two differ only in env var, `clientName` label and the
global client symbol. `BaseRedisStorage` keeps no `batch_import_*` and no
`deleteAll*`, so `db.py`'s capability checks and the data-migration per-item
fallback land exactly as upstream. `scripts/redis_storage_check.py` is 96 checks
green: the storage layer driven against a real RESP socket (`fakeredis`'s
`TcpFakeServer` — there is no redis-server on this box, so no claim is made about
a real server's own behavior), one wire scenario run against sqlite/redis/kvrocks
and canonicalized, and the upstream-faithful differences asserted as differences
on the right axis (redis's `LREM key 0 <kw>` dedup is newest-first, D1's
`CURRENT_TIMESTAMP` ties inside a second; redis hands back the stored JSON, D1
rebuilds typed columns). Nothing in the milestone list is unported now.

Frontend: F0 scaffold + F1 build are in and the built document is served by the
catch-all. Remaining: the A/B diff harness that boots old MoonTV next to the new
backend and replays endpoint fixtures (including SSE event sequences and one real
`.dat` backup).
