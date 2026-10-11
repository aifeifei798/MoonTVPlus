# MoonTVPlus

A rewrite of MoonTV (Next.js 14, upstream version `4.2.5`,
`https://github.com/Stardm0/MoonTV`) as two separate packages:

```
backend/    FastAPI (Python) — the whole /api/* surface plus the auth gate
frontend/   Vite + React     — the UI, rebuilt rather than kept as a Next.js app
```

The split is only about code organization and runtime. **The wire protocol does
not change**: same paths, same query parameters, same response shapes, same
SSE/NDJSON framing, same auth cookie format, same environment variable names.
Browser client libs, TVBox/OrionTV style external clients and existing
`docker run -e ...` / docker-compose files keep working against this server.

> Work in progress. Backend milestones B0–B3, B5 (the admin panel surface and
> data migration) and frontend F0/F1 are in; B4, B6 and B7 (user state,
> external services, m3u8 proxy + browser-side backup migration) are not.
> See [`PLAN.md`](PLAN.md) for the full port plan and the list of deliberate
> behavior changes.

## Requirements

| Component | Need |
|---|---|
| Backend | CPython ≥ 3.11, `fastapi`, `uvicorn`, `httpx`, `cryptography`, `redis`, `python-multipart` (installed into `backend/.venv`) |
| Frontend | Node.js ≥ 20 and `pnpm` (only for building the UI; the backend just serves `frontend/dist`) |
| Storage | SQLite (stdlib, default) — Redis or Kvrocks optional, backend not ported yet |

## Getting started

```bash
./install.sh                 # backend/.venv + dependencies, then pnpm install
./install.sh --build         # also build frontend/dist, which the backend serves
```

`install.sh` reads the dependency list from `backend/pyproject.toml`, so there is
one source of truth for what the backend needs. It is idempotent: an existing
venv is reused, and `--recreate` is the only path that removes one. Use
`--dry-run` to see the commands first.

Then start it:

```bash
PASSWORD=secret USERNAME=admin ./server.sh start
./server.sh status
./server.sh log
```

Without `server.sh`, the plain invocation is:

```bash
backend/.venv/bin/python -m uvicorn moontv.main:app --app-dir backend --host 0.0.0.0 --port 3000
```

## Process control — `server.sh`

| Command | What it does |
|---|---|
| `start` | background; pid in `.run/server.pid`, log in `.run/server.log`; confirms startup before returning |
| `stop` | `SIGTERM`, waits `MOONTVPLUS_STOP_TIMEOUT` (10s), then `SIGKILL` |
| `restart` | `stop` + `start` |
| `status` | pid, elapsed time, RSS, cmdline, url/db/log paths, last log lines (exit 3 when stopped) |
| `run` | foreground with `exec` — for containers and for debugging |
| `log` | `tail -f` the log |
| `build` | rebuild `frontend/dist`, then start |
| `cron` | the old `start.js` poll-then-hourly `/api/cron` loop, in the foreground |
| `test` | `pytest` under `backend/` |

Options (before or after the command): `--env FILE`, `--host HOST`, `--port PORT`,
`--log FILE`, and `--` to forward extra uvicorn arguments.

Precedence is `--host/--port` > env file > exported `HOSTNAME`/`PORT` > `0.0.0.0:3000`.
`HOSTNAME` and `PORT` are the names the old MoonTV Docker image exported, so
existing deployments keep their meaning; only an **exported** `HOSTNAME` counts,
because bash sets it to the machine name otherwise.

## Configuration

Every variable keeps the name MoonTV used. Note that `PASSWORD` and `USERNAME`
are **not** `NEXT_PUBLIC_`-prefixed — that is upstream's inconsistency, preserved
on purpose.

| Variable | Default | Meaning |
|---|---|---|
| `PASSWORD` | — | required; unset means every page redirects to `/warning` |
| `USERNAME` | — | the owner/admin user |
| `NEXT_PUBLIC_STORAGE_TYPE` | `sqlite` | `sqlite` (also covers the old `localstorage`/`d1` values); `redis`/`kvrocks` pending |
| `MOONTV_DB_PATH` | `backend/moontv.db` | SQLite file; `server.sh` sets it absolutely so it does not follow the caller's cwd |
| `NEXT_PUBLIC_SITE_NAME` | `MoonTV` | site name, substituted into the served document |
| `ANNOUNCEMENT` | built-in default | banner text |
| `NEXT_PUBLIC_SEARCH_MAX_PAGE` | `5` | how many downstream pages search walks |
| `NEXT_PUBLIC_ENABLE_REGISTER` | `false` | allow self-registered users |
| `NEXT_PUBLIC_DISABLE_YELLOW_FILTER` | `false` | disable the content filter |
| `NEXT_PUBLIC_DANMU_API_BASE_URL` | `""` | danmaku API base |
| `NEXT_PUBLIC_PLAYBACK_SAVE_INTERVAL` | computed | play-record save interval |
| `NEXT_PUBLIC_DOUBAN_PROXY_TYPE` / `NEXT_PUBLIC_DOUBAN_PROXY` | `direct` / `""` | Douban text proxy |
| `NEXT_PUBLIC_DOUBAN_IMAGE_PROXY_TYPE` / `NEXT_PUBLIC_DOUBAN_IMAGE_PROXY` | `direct` / `""` | Douban image proxy |
| `TVBOX_ENABLED`, `M3U8_PROXY_ALLOW_HOSTS` | — | TVBox and m3u8 proxy policy (routes pending) |
| `REDIS_URL`, `KVROCKS_URL` | — | alternative storage backends (pending) |
| `MOONTV_CONFIG_PATH` | `backend/config.json` | the only file-config source |

`server.sh` also loads `./.env` when it exists (dotenv-style: `KEY=value`,
`#` comments, optional quotes, no shell expansion). The copy-paste-ready
example at the repo root documents the parser's edge cases — no inline
comments, no indented assignments, values not trimmed — so read it before
editing.

## Layout

```
backend/
  moontv/main.py            app assembly + the auth-gate middleware
  moontv/routes/            one module per src/app/api/**/route.ts
  moontv/storage/           db.py (capability checks) + sqlite.py (from d1.db.ts)
  moontv/{auth,config,crypto,fetch,downstream,utils,...}.py
  config.json, pyproject.toml, .venv/
frontend/
  index.html                the Vite entry — the document the backend serves
  src/main.tsx              port of src/app/layout.tsx
  src/shims/                navigation, link, image, next
  src/app, src/components, src/lib
  src/vendor/muxjs          the github:Momo707577045/mux.js fork, vendored
  dist/                     build output, served under /assets
```

The backend serves the built document at **every** non-API path and the API at
`/api/*`, so there is one origin: no CORS, cookies work, and StreamSaver's
same-origin `/mitm.html` + `/sw.js` keep working.

## API surface

Every upstream route is in, plus the addition called out below:

```
POST /api/login            POST /api/register       POST /api/logout
POST /api/change-password  GET   /api/server-config GET  /api/search
GET  /api/config/sources   GET  /api/config/custom_category

POST /api/admin/user       POST /api/admin/group    POST /api/admin/source
POST /api/admin/source/test  POST /api/admin/category  POST /api/admin/site
POST /api/admin/config_file  GET  /api/admin/config  GET  /api/admin/reset
POST /api/admin/subscription  GET /api/admin/subscription
POST /api/admin/tvbox      GET  /api/admin/tvbox
POST /api/admin/data_migration/export
POST /api/admin/data_migration/import

GET  /api/detail           GET  /api/search/{one,resources,suggestions,ws}
POST /api/m3u8             GET  /api/m3u8
GET  /api/douban           GET  /api/douban/{categories,recommends}
GET  /api/image-proxy      GET  /api/tvbox/{config,categories}
GET  /api/cron             POST /api/followings/refresh   POST /api/user/online
GET|POST /api/{favorites,followings,playrecords,searchhistory,skipconfigs,today-updated}
```

`/api/admin/source/test` is a MoonTVPlus addition, not an upstream route: it
probes every configured API site with the same request a search makes and
streams the verdicts back as SSE, so the admin panel can show which sources
went dead.

Everything upstream had is ported. Still missing: the browser side of the old
`.dat` backup migration, and the Redis/Kvrocks storage backends (`create_storage`
raises until they exist). Until a route exists, an unauthenticated `/api/...`
path still gets the gate's `401`, while a path the gate deliberately does not
cover falls through to the app document at status 404.

## Data and backups

State lives in the SQLite database (`favorites`, `followings`, `playrecords`,
`searchhistory`, users, …) using the schema from upstream's `d1-init.sql`, with
`SEARCH_HISTORY_LIMIT = 20` and `BATCH_LIMIT = 100` unchanged. Old MoonTV
migration backups (`.dat`) are `pako.deflate` followed by CryptoJS
`AES.encrypt(data, passphrase)` — an OpenSSL `Salted__` header, `EVP_BytesToKey`
with MD5, AES-256-CBC — and `moontv/crypto.py` reproduces that byte-for-byte in
both directions. The `/api/admin/data_migration/{export,import}` pair round-trips
that format — the export streams the ciphertext as SSE `chunk` events, and
reassembling them and posting the result back through the import route restores
the same state. The browser-side StreamSaver migration flow (`/mitm.html` +
`/sw.js`) is milestone B7.

## Frontend development

```bash
cd frontend
pnpm build      # generate manifest -> vite build -> Tailwind CLI -> postbuild
pnpm dev        # vite build --watch
pnpm typecheck  # tsc --noEmit
```

The app document is `frontend/index.html`, and the backend serves the built
`frontend/dist/index.html` with `__SITE_NAME__`, `__RUNTIME_CONFIG__` and
`__RUNTIME_LAYOUT__` substituted at serve time — one built document serves every
site configuration. Dependency pins (`react` 19.0.0, `artplayer` 5.3.0, vendored
`mux.js`) are load-bearing; see PLAN.md before changing them.

## Testing

`backend/.venv/bin/python -m pytest` is wired up through `backend/pyproject.toml`
(`asyncio_mode = auto`); there are no test files yet. Cross-checks against the
original TypeScript are done with node harnesses and fixture comparisons
described in PLAN.md. The reusable ones are committed in
`.opencode/skills/moontvplus-verification/scripts/` — `adfilter_cases.cjs` and
`adfilter_probe.cjs` (the ad filter, measured offline), `cdp_eval.cjs` and
`cdp_net.cjs` (the browser probes), `servecheck.py` (stale-`dist` check),
`source_delete_check.py` and `source_health_check.py` (the admin panel's source
routes) — with the traps that make a harness lie in `references/harness-rules.md`.

## License

Apache-2.0 — see [`LICENSE`](LICENSE).

This tree is a rewrite of MoonTV, which is MIT (© 2025 LunaTechLab). The files
copied from those sources (`frontend/src/app`, `src/components`, `src/lib`,
`src/vendor/muxjs`) keep that upstream attribution, recorded in
[`NOTICE`](NOTICE); everything else here is Apache-2.0.
