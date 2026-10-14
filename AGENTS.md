# AGENTS.md — MoonTVPlus

This repo is a rewrite of MoonTV (Next.js 14, upstream `VERSION.txt` = `4.2.5`) as
a FastAPI backend (`backend/`) plus a Vite + React frontend (`frontend/`). Its own
release line is `5.0.0`: the root `VERSION.txt` is the source of truth, mirrored by
`backend/moontv/version.py` and `frontend/src/lib/version.ts` (asserted by
`backend/tests/test_version.py`). The
original tree lived at `/home/feifei/develop/MoonTV` and is a git repo; it is
**gone from this machine**, so parity is reasoned from the ported code plus real
source data. **This repo is a git repo** pushed to a public `MoonTVPlus` —
verification harnesses live in `.opencode/skills/moontvplus-verification/` and
are committed, not left in `/tmp`.

## Read before porting anything

- `PLAN.md` — the port plan, the rules, the deliberate behavior changes, and
  milestones (B0–B7, `/api/m3u8` and the Redis/Kvrocks backend are all in).
  Module docstrings point at it.
- `/home/feifei/develop/MoonTV/src/lib/**` and `src/app/api/**/route.ts` — the
  source of truth for behavior and the wire format. When docs here disagree with
  the TypeScript, the TypeScript wins. That tree is **not on this machine now**,
  so re-fetch it (or a released copy) before a parity claim that needs a diff;
  until then, parity is argued from the ported code plus real source data.
- `.opencode/skills/moontvplus-verification/` — the committed harnesses and the
  rules that make a harness lie. Load it (`skill` tool, id
  `moontvplus-verification`) before writing a probe or claiming a fix is live.

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
- pytest: `backend/.venv/bin/python -m pytest` **from `backend/`**
  (`testpaths = ["tests"]`, `asyncio_mode = auto`). Running it from the repo root
  makes pytest's rootdir the root, where there is no ini file, so `asyncio_mode`
  is not applied and every async test/fixture errors out. The suite is offline:
  `tests/conftest.py` points `MOONTV_DB_PATH` at a temp file and
  `test_search_stream.py` adds a local `ThreadingHTTPServer` fake `?ac=videolist`
  media server as a source, so search assertions do not depend on the network.

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
- **Three storage backends, two files of substance**: `storage/redis.py` and
  `storage/kvrocks.py` are 14-line leaves over one `storage/redis_base.py`
  (`redis-base.db.ts`), because this 4.2.5 tree's `kvrocks.db.ts` uses plain redis
  commands (no `exh*`); they differ only in env var (`REDIS_URL`/`KVROCKS_URL`),
  the `clientName` log label, and the global client symbol (`Symbol.for('__MOONTV_REDIS_CLIENT__')`
  → `_GLOBAL_CLIENTS` keyed by `REDIS_CLIENT_SYMBOL`/`KVROCKS_CLIENT_SYMBOL`).
  `BaseRedisStorage` deliberately has **no** `batch_import_*` and no `deleteAll*`,
  so `db.py`'s `getattr` capability checks and the data-migration per-item
  fallback (`BATCH_SIZE = 50`) stay reachable exactly as upstream — adding those
  methods would silently change which branch the import takes.
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
- **Source health check (new, not a port)** is a fourth admin module,
  `routes/source_test.py` (`POST /api/admin/source/test`, SSE, POST-only, and a
  `CONCURRENCY = 5` `asyncio.Queue` pool copied from
  `routes/followings_refresh.py`). It is a separate path rather than a new
  `action` on `source()` so that switch stays faithful to upstream. The probe
  is the *same request a search makes* (`api + API_CONFIG["search"]["path"] +
  PROBE_QUERY`, and `PROBE_QUERY` is **empty** — it asks the site's first page,
  because `wd=a` makes one live site answer non-JSON and be called dead), and
  the route is **read-only**: it never calls `Db.save_admin_config`, so testing
  cannot reorder, re-enable or resurrect anything. An **empty** `list` is a
  zero-result warning with `count: 0`, not a death — plenty of live sites
  answer an empty query with no rows (PLAN.md, change 18).
- **Config-file sources/categories are re-injected on every read**: `refine_config`,
  `get_config` and `reset_config` all merge the blob's `ConfigFile.api_site` map
  (and `custom_category` list) into `SourceConfig`/`CustomCategories` and mark the
  result `from: "config"`. Deleting one from the list alone is therefore a no-op —
  it is rebuilt on the next request. Upstream hid that by refusing a single delete
  (`该源不可删除`) and by having `batchDelete` skip them while still answering
  `{"ok":true}`; the port drops both and prunes the key out of the blob's `ConfigFile`
  string too (`config.py: drop_config_api_site` / `drop_config_custom_category`),
  which is what makes the panel's 删除/批量删除 stick (PLAN.md, change 17). The
  panel also refused them **in the browser** — the 删除 button was rendered only
  when `from !== 'config'`, and `handleBatchDelete` filtered the selected keys the
  same way while promising `系统默认源无法删除，将被跳过` — so a backend-only fix
  leaves the UI unchanged and the key never sent; both guards are gone in
  `frontend/src/app/admin/page.tsx` (and the row renderers now emit the button
  unconditionally). A frontend edit is invisible until `pnpm build` runs. While an
  admin blob exists its `ConfigFile` is the source of truth — `backend/config.json`
  on disk is only read by `init_config`/`reset_config`.
- **The panel's source health badges are client state, not wire state**:
  `frontend/src/app/admin/page.tsx` reads the `localStorage` key
  `moontv_source_health` (`{at, results}`, the snake-case convention of
  `db.client.ts`'s `PLAY_RECORDS_KEY`) on mount, so the badges and the summary
  banner survive a reload with no server help. The 测试源 button's SSE reader is
  the one copied from `components/DataMigration.tsx` (`getReader` +
  `TextDecoder` + `split('\n\n')` + `dataLine.slice(5).trim()`), so it only
  works against the exact `data: {...}\n\n` framing. Healthy is `apple-*`, dead
  is `red-*`, a zero-result site is `yellow-*`; the badge spans carry
  `whitespace-nowrap` in their **own** class so a CDP probe can tell them from
  the enable/disable badges that share `bg-apple-100 ... rounded-full`, and the
  测试源 button carries it for the same reason (the batch-enable button
  otherwise matches the selector).
- `Db` in `storage/db.py` is an **instance** (`Db = DbManager()`), mirroring
  `export const db = new DbManager()`. As a class it silently ate the first
  argument as `self`, so every non-owner login/register path answered
  `数据库错误` 500 — the owner path hid it because it short-circuits on env vars.
- The `storageType === 'localstorage'` guard is kept as a plain 400
  (`不支持本地存储进行管理员配置` / `不支持本地存储进行数据迁移`): unreachable with
  the ported backends, but it is on the wire and the panel branches on it.

## Gotchas

- **The version is MoonTVPlus's own, not upstream's.** `VERSION.txt` at the repo
  root is the source of truth (`5.0.0`); `backend/moontv/version.py` and
  `frontend/src/lib/version.ts` must both match it, and `backend/tests/test_version.py`
  asserts that plus the changelog entry. `frontend/src/lib/version.ts` checks
  `raw.githubusercontent.com/aifeifei798/MoonTVPlus/{main,master}/VERSION.txt`, so a
  root `VERSION.txt` that drifts from the constant makes the version panel claim an
  update is available (or report `已是最新版本` when it is not). The panel's
  前往仓库 links and the login page badge point at `aifeifei798/MoonTVPlus`, so
  pushing `VERSION.txt` to `main` is part of a release bump. The `.dat` backup's
  `serverVersion` comes from the backend constant, so a backup written by 5.0.0 says
  `5.0.0` even though upstream is `4.2.5`.
- Env names are upstream's, including the inconsistency: `PASSWORD` and
  `USERNAME` are **not** `NEXT_PUBLIC_`-prefixed; `NEXT_PUBLIC_SITE_NAME`,
  `NEXT_PUBLIC_STORAGE_TYPE`, … are. A wrong name silently turns the gate into
  a 307 → `/warning`.
- **Compression is on, and it makes a size probe lie.** `main.py` registers
  `GZipMiddleware(minimum_size=1000, compresslevel=6)` before the auth gate, and
  `static.py` gzips the files it serves from a cache keyed by
  `(path, size, mtime_ns)` (so the middleware sees `Content-Encoding: gzip` and
  does not compress twice) and answers `If-None-Match` with a 304. httpx and
  `TestClient` send `Accept-Encoding: gzip, deflate` and auto-inflate, so a test
  that wants the raw bytes must send `Accept-Encoding: identity` and a test that
  wants the wire size must read `content-length`, not `len(response.content)`.
  Starlette's exclusion list means SSE, images, fonts, video and audio are never
  compressed — `/api/admin/source/test` and `/api/followings/refresh` stay
  byte-exact.
- **`?compact=1` is opt-in, per caller.** `routes/search.py` drops `desc`,
  `episodes_titles` and `episodes` and adds `episodes_count` only when the query
  says so; the search page does, the play page's re-search does not. A probe that
  compares two search payloads must pass the same `compact` value to both or the
  episode arrays will disagree.
- **Two knobs are MoonTVPlus-only and default on:** `MOONTVPLUS_ONLINE_DEBOUNCE_S`
  (60; `0` restores upstream's write-per-heartbeat) and
  `MOONTVPLUS_UPSTREAM_MAX_CONNECTIONS` (64, the shared httpx pool's
  `max_connections`; keepalive is `min(20, that)`).
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
- **The redis-only `reset_config` branch is a STORE difference, not a wire one**:
  `config.py` rebuilds `CustomCategories` from the config file only when
  `storage_type == "redis"` (kvrocks gets `[]` like sqlite, upstream's `=== 'redis'`),
  but `get_config` re-injects the blob's `ConfigFile.custom_category` on every read,
  so all three backends serve the same `/api/admin/config` after a reset — only the
  stored blob differs. Proving it needs a config file that HAS a `custom_category`,
  and this box's does not: point `MOONTV_CONFIG_PATH` at a throwaway copy that does
  (the path is `MOONTV_CONFIG_PATH` or `dirname(dirname(config.py))/config.json`).
- **The redis backend's oracle here is `fakeredis`'s `TcpFakeServer`, not a redis server**
  (`which redis-server redis-cli` is empty and sudo needs a password). It is a real
  RESP TCP server, so the real `redis.asyncio` client's wire encoding and the port's
  command surface are exercised; what no harness here can claim is a real server's
  own behavior (timeouts, RDB, Kvrocks' `exh*`). `TcpFakeServer(addr, bind_and_activate=True)`
  only BINDS — you must run `serve_forever` in a thread — and it has no `stop_all()`.
- `addSearchHistory`'s `lRem(key, 0, keyword)` is **not** a no-op: per the LREM spec
  `count = 0` means "remove ALL matches", so the redis backends really do dedup
  search history like sqlite/D1. The port keeps all three arguments so the same
  bytes go on the wire — the two-argument form would misparse an integer keyword as
  `LREM key count <missing>`.
- The redis backend round-trips the stored JSON verbatim, so `/api/favorites` etc.
  hand back exactly what was POSTed, while sqlite rebuilds its column shape
  (`_record_from_row`). That is upstream's own difference (D1 stored typed columns),
  so a sqlite-vs-redis byte comparison must send complete records and compare a
  projection, not whole objects.
- A harness must not seed state by writing `backend/moontv.db` from a second
  `sqlite3` connection: SQLite never re-reads pages through a connection another
  process already has open, so the write is durable on disk but the running server
  keeps serving the old blob and the probe silently tests nothing (a fresh process
  would see it). Drive the change through the panel's own routes — `/api/admin/config_file`
  replaces the whole `ConfigFile` string, `/api/admin/source` and `/api/admin/category`
  mutate the lists — or restart the server after the write.
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
- **The `.dat` backup is three layers, and a harness must not confuse them**:
  `crypto.encode_backup(payload, password)` takes the **JSON string** and does
  `pako.deflate` → base64 → AES itself, so an openssl probe that wants the middle
  layer decrypts `encode_backup(payload, pw)` and compares against
  `base64(pako_deflate(payload))` — feeding `encode_backup` an already-deflated
  string silently deflates twice and the comparison fails for a reason that looks
  like a port gap. `encrypt`/`decrypt` are the AES layer alone. The export's `done`
  event has **no** `serverVersion` — upstream stamps `timestamp` and `serverVersion`
  in the backup **payload**, so the claim is `backup["serverVersion"] == CURRENT_VERSION`
  (`moontv/version.py`, `5.0.0` — MoonTVPlus's own release line, which must match the
  root `VERSION.txt`; upstream MoonTV is still `4.2.5`). And `import_` calls
  `Db.clear_all_data()`, which deletes all eight data tables (`douban_cache` stays),
  so a round trip that eats something is invisible unless the harness seeds records
  through the panel's own routes first and asserts the census afterwards.

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
- **`fill` in the image shim must be CSS.** `width="100%" height="100%"` are not
  valid HTML attribute values (percentages are rejected), so a `fill` image falls
  back to Tailwind preflight's `img, video { max-width: 100%; height: auto }` and
  is sized **by its width**: a poster whose natural ratio is not 2/3 leaves a gray
  strip at the bottom of the card or is clipped by the box's `overflow: hidden`,
  and a failed poster renders as the 16 px broken-image glyph. `shims/image.tsx`
  therefore sets `position: absolute; inset: 0; width: 100%; height: 100%` (Next's
  own fill semantics) merged with the caller's `style`. `scripts/cdp_image_fill.cjs
  <auth-cookie> <path> …` censuses every poster `<img>` on a live page and fails if
  any rendered box is smaller than its `overflow: hidden` + `aspect-*` container;
  it also inserts the attribute-fill and CSS-fill variants side by side as the
  counterfactual. Plain `<img className='w-full h-full object-cover'>` usages
  (`VideoDetailPanel`, `EpisodeSelector`) were never affected.
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
- **The brand assets are generated, not hand-edited**: `python3 frontend/scripts/make-logo.py`
  draws the crescent-and-play mark from one set of geometry constants and writes
  `public/logo.svg`, `public/logo.png`, `public/favicon.ico` and
  `public/icons/icon-{192,256,384,512}.png`. It needs ImageMagick (`convert`) and
  packs the ICO by hand with `struct`, because IM 6 writes only one frame per
  `.ico`. The nav bars and the login page render `/logo.svg` next to the site
  name, so both `/logo.png` and `/logo.svg` are in `auth.py`'s `SKIP_PATHS`;
  changing the mark without rebuilding the frontend leaves the old tile in
  `dist/` (postbuild copies `public/**` verbatim).
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
- **Rotation-driven fullscreen is ours, not Artplayer's.** `autoOrientation` is a
  control button that calls `screen.orientation.lock()`; nothing upstream enters
  fullscreen because the phone turned. `usePlayEngine.ts` listens for
  `screen.orientation` `change` (fallback `window` `orientationchange`) on touch
  devices only, and the entry prefers native fullscreen with the same 600ms
  `fullscreenWeb` fallback. Only fullscreen the orientation itself entered is
  exited by rotating back, and a manual exit while landscape suppresses re-entry
  until the next portrait→landscape cycle. **Never test "is it fullscreen" with
  `art.fullscreen`**: Artplayer defines that property as a getter only inside
  `once("video:loadedmetadata")`, so assigning before then leaves a plain `true`
  on the instance and every later check reads it as fullscreen — the exact failure
  when the phone is rotated before the video loads. `isFullscreenEngaged()` reads
  the DOM (`art-fullscreen`, `art-fullscreen-web`, `document.fullscreenElement`)
  instead, and the manual-exit listeners are registered right after the player is
  constructed rather than in `ready` for the same reason.
- **The 锁定 (lock) progress bar is a CSS fix, not a JS one.** When locked,
  Artplayer pushes `.art-bottom`'s contents down by
  `--art-control-height + --art-bottom-gap + --art-progress-height/4`; the controls
  are clipped by `.art-bottom`'s `overflow: hidden` but the progress bar leaks a
  sliver inside the player's bottom edge. `globals.css` hides
  `.art-video-player.art-lock .art-bottom .art-progress` — `!important` is required
  because Artplayer injects its stylesheet into `head` at runtime, after the
  Tailwind one.
- **The control bar that never hides is an `isHover` leak, not a CSS one.**
  Artplayer 5.3.0 collapses the bottom bar inside `video:timeupdate` on
  `!setting.show && !controls.isHover && !isInput && playing && show && Date.now() - controls.timer >= CONTROL_HIDE_TIME (3000)`, and
  `controls.isHover` is refreshed **only** by a `document` mousemove that lands on
  `$bottom` (only `.art-progress` and `.art-controls` carry `pointer-events: auto`).
  A pointer that exits the window straight from the bar produces no such move, so
  `isHover` stays `true` forever and the bar stays up. In web fullscreen the player
  fills the viewport, so "leaving the player" *is* "leaving the page" — which is why
  the symptom ("网页全屏后鼠标离开,播放条没隐藏") only shows there. `usePlayEngine.ts`
  registers `artPlayerRef.current.on('hover', ...)` (Artplayer fires `hover` on the
  player's `mouseleave`) and sets `controls.isHover = false`; the collapse still
  follows Artplayer's own 3 s timer, so the bar does not vanish instantly. Proving
  it needs a **playing** video: the source CDN answers the manifest XHR with 404 +
  no ACAO in this browser, so `scripts/cdp_control_hide.cjs` destroys `video.hls`
  (otherwise hls.js's `sourceopen` handler puts its blob `src` back), drops
  `crossOrigin` (the app sets `anonymous` via `moreVideoAttr` and there is no ACAO),
  and inlines `fixtures/probe.mp4` as a data URL. It reports 2794 ms on the shipped
  bundle; `scripts/artplayer_hide_probe.cjs` is the counterfactual, running the same
  gesture against the pinned `node_modules/artplayer` with the listener toggled off
  by `?nofix=1` (fix → 3000 ms, no-fix → never).
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
- **动漫「每日放送」 posters are one of two image paths with a mirror chain.** That
  section's data is client-side Bangumi (`frontend/src/lib/bangumi.client.ts` →
  `api.bgm.tv/calendar`), and every poster is an `http://lain.bgm.tv/pic/cover/…`
  URL, so it always goes through `/api/image-proxy` (the mixed-content fallback).
  `routes/image_proxy.py: _image_candidates` expands a `*.bgm.tv` URL into
  `[as-given, https form, *mirrors]` and tries them in order, falling through only
  on a connection error or a 5xx — a 404 from the official host is still the
  answer, so a bad id does not go hunting. Default mirror `lain.fxbgm.tv`
  (community reverse proxy, byte-identical covers);
  `MOONTVPLUS_BANGUMI_IMAGE_MIRRORS` replaces the list, empty value = official
  only. `bangumi.client.ts` likewise tries `api.bgm.tv/calendar` then
  `fxbgm.tv/calendar`, each with a 10 s `AbortController`. Offline coverage is
  `backend/tests/test_image_proxy.py` (a stub client records the attempted URLs);
  live coverage is a CDP census counting `img[src*="lain.bgm.tv"]` with
  `naturalWidth > 0`.
- **Every poster has a server-side rescue, because it is the browser that fails,
  not the server.** `img*.doubanio.com` answers a referer-less request with **418 /
  14 bytes**, and every card is `referrerPolicy='no-referrer'`, so with the default
  `DoubanImageProxyType: direct` all douban posters are broken (home page census:
  60 of 76 `<img>`s with `naturalWidth === 0`). The referer matrix is measured: no
  Referer → 418; `movie.douban.com`, `www.douban.com`, `www.google.com` and even
  `Referer: ""` → 200; the app's own origin → 403 — so `referrerPolicy='origin'` is
  not a fix. `frontend/src/lib/utils.ts: retryImageThroughProxy` is the fix: on
  `<img>` error it retries the absolute URL once through `/api/image-proxy?url=…`
  (the dataset flag stores the URL it retried, so a poster that later changes can
  still fall back). The `next/image` shim calls it **before** the caller's `onError`,
  and `VideoDetailPanel`/`EpisodeSelector` call it before hiding their plain
  `<img>`s; `VideoCard` no longer retries the same failing URL and instead walks an
  ordered poster chain (`posterIndex` advances on every `onError`), so
  `ImagePlaceholder`'s `failed` prop — which stops the pulsing — means "the whole
  chain is exhausted". The proxy's candidate chain
  covers two families — `*.bgm.tv` and `img<N>.doubanio.com`
  (`MOONTVPLUS_DOUBAN_IMAGE_MIRRORS`, default
  `img.doubanio.cmliussss.net,img.doubanio.cmliussss.com`) — and every other host is
  still fetched exactly once, so upstream's wire behavior is unchanged for them. A
  census that counts `naturalWidth === 0` without making the lazy posters attempt a
  fetch measures nothing (below-the-fold `loading='lazy'` images never errored): set
  `img.loading = 'eager'` first, or scroll the page.
- **Record cards (继续观看 / 追更 / 收藏) prefer a douban poster.** The chain is
  豆瓣 → 源 → 站点图标, and only `from='playrecord'`/`from='favorite'` cards try it —
  douban and search cards skip it because their posters already come from douban or
  from the search aggregation. `/api/douban/search?q=…` is **additive surface, not a
  port**: upstream MoonTV has no title search, and every anonymous JSON surface is
  gone (`movie.douban.com/j/search` 404, `j/search_suggest` 404,
  `m.douban.com/rexxar/api/v2/search` and `api.douban.com/v2/movie/search` 403), so
  `douban.py: search_douban` scrapes `search.douban.com/movie/subject_search` and
  parses the `window.__DATA__ = {...}` literal. It shares the other three douban
  routes' `{code, message, list}` shape, cache headers and stale cache. Matching is
  scored in `frontend/src/lib/poster.client.ts` (exact 3, prefix 2, substring 1,
  year agreement +1, year conflict -2, and anything below 2 is not a hit — source
  titles carry `HD`/`更新至X集` noise); misses are cached too, in memory and in
  `localStorage` under `moontv_douban_poster` (7-day TTL, 500-entry cap), with 4
  concurrent lookups so a page of 30 records cannot hammer douban. When the whole
  chain fails the card renders `public/poster-fallback.svg` — the brand mark at 55%
  inside a 300x450 box, generated by `frontend/scripts/make-logo.py` from the same
  geometry as the logo — on top of the gray well, and that path is in `auth.py`'s
  `SKIP_PATHS` like `/logo.svg`.

## Verification pattern

Ports are verified by comparing against the TypeScript, not by reading it: a
table of fixtures run through both the verbatim TS (in node) and the Python port,
then byte-compared. The reusable harnesses are **committed** in
`.opencode/skills/moontvplus-verification/scripts/` — `adfilter_cases.cjs` (the
fixture table), `adfilter_probe.cjs` (the structural census of any manifest),
`cdp_eval.cjs` and `cdp_net.cjs` (the browser probes), `cdp_orientation.cjs`
(the mobile landscape/portrait lifecycle and the 锁定 progress-bar check),
`mkplayurl.py` (derives a
working `/play` URL from data), `login_cookie.py` (the auth cookie), `servecheck.py`
(stale-dist + shipped-chunk check), `chrome_start.sh` (the headless Chrome the CDP
harnesses attach to), `_harness.py` (the `sys.path` + `.env` bootstrap) — with the
captured manifests in `fixtures/live-source-*.m3u8` and the trap list in
`references/harness-rules.md`. The one-off probes from earlier sessions
(`cross_downstream.py` ↔ `cross_downstream.cjs`, `smoke2.py`, `smoke3.py`) were
scratch and are gone. When you write a new one:

- Name them `.cjs`. A `.mjs` is treated as ESM, where `require` is undefined.
- `NODE_PATH=<repo root>/frontend/node_modules` is needed to
  `require('he')` and the other installed packages (the committed Python
  harnesses derive it from `_harness.ROOT`).
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
  gap. Measure the filter offline instead: `scripts/adfilter_probe.cjs
  <manifest>` prints segments/markers/duration/drops/seams for a saved manifest
  (the committed `fixtures/live-source-*.m3u8` are the durable cases; a live
  fetch may fail because these CDNs rotate hosts and expire episode URLs),
  and `scripts/adfilter_cases.cjs` is the fixture table.

`TestClient` checks the wire, not the runtime: it never executes the bundle, so
mount-time JS errors (see the `createRoot` note above) are invisible to every
harness and only show up as a blank page in a browser. The committed CDP harnesses
(`scripts/cdp_eval.cjs`, `scripts/cdp_net.cjs`) close that gap: start headless Chrome
with `scripts/chrome_start.sh <port>` (they only attach, they cannot start one),
install a `MutationObserver` with
`Page.addScriptToEvaluateOnNewDocument` so it exists **before** the bundle runs,
`Page.navigate` to the page, then `Runtime.evaluate` to read the recorded
mutations and `document.body.innerHTML` length. A `+0 -1` body mutation means
React cleared the whitespace text node and rendered nothing — the render value,
not an exception. `cdp_eval.cjs` takes the cookie **value** as argv[2] (it sets
`name: 'auth'` itself) and `scripts/login_cookie.py` prints exactly that:
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
B7 is **in** — `routes/data_migration.py` (SSE export + multipart import over
the `pako.deflate` → base64 → AES pipeline) and the browser half,
`components/DataMigration.tsx`, which is rendered as the owner-only collapsible
tab in the admin panel and downloads the `.dat` back to `/api/admin/data_migration/import`.
`scripts/dat_backup_check.py` is 97 checks green: crypto parity against crypto-js
and openssl both ways, and a live export → file → import round trip that proves
the import's `Db.clear_all_data()` (it deletes all eight tables before replaying
the backup) eats nothing. The Redis/Kvrocks backend is **in** too — `storage/redis_base.py`
(`BaseRedisStorage`) plus the `redis.py`/`kvrocks.py` leaves, verified by
`scripts/redis_storage_check.py` (96 checks green: the storage layer against a real
RESP socket, one wire scenario run against sqlite/redis/kvrocks and canonicalized,
and the data-migration fallback that only the absent `batch_import_*` can reach).
Nothing in the milestone list is unported now.

B4 user state is **in** — `routes/user_state.py` (`/api/user/online`,
`playrecords`, `favorites`, `followings`) and `routes/user_history.py`
(`searchhistory`, `skipconfigs`, `today-updated`), plus
`routes/followings_refresh.py` and `moontv/fetch_video_detail.py`, verified
against the SSE event vocabulary the client parses.

B5 (admin + `/api/config`) is in and verified end to end: every action of every
admin route was exercised with curl against a logged-in cookie, and the CDP
harness on `/admin` renders the full panel menu with an empty `window.__errs`.
