# Harness rules that cost a session each

These are the traps that made an earlier harness lie. Re-read them before
writing a new probe.

## Node harnesses

- **Name them `.cjs`.** A `.mjs` is treated as ESM, where `require` is
  undefined, so every `require('he')` fails at load.
- Installed packages need
  `NODE_PATH=/home/feifei/develop/MoonTVPlus/frontend/node_modules` (or the
  repo-relative equivalent) to be `require`-able.
- `node -e` sees its args at `argv[1]`; a **script file** at `argv[2]`. A
  harness written for one form silently reads `undefined` when run as the other.
- To run the shipped TypeScript instead of a re-typed copy, slice the function
  out of the `.ts` and `esbuild.transformSync(fnSrc, { loader: 'ts' })` it,
  then `new Function(...)`. `adfilter_probe.cjs` and `adfilter_cases.cjs` do
  this against `frontend/src/app/play/play-utils.ts`, so they test the code that
  actually ships, not a paraphrase.
- **A CDP eval that clicks must `await` before it reads.** React's scheduler
  finishes the re-render in a *later* task, so a synchronous read right after
  `element.click()` sees the old DOM and every "the button is now there" probe
  reports 0 — it looks like the click did nothing. `cdp_eval.cjs` passes
  `awaitPromise: true`, so return a Promise (an `async` IIFE) and measure after a
  ~1.5 s `setTimeout`. Two more traps in the same probe: an arrow IIFE with a
  block body needs an explicit `return` (without one the harness prints
  `{"result":{"type":"undefined"}}`, which is not an exception), and the only
  safe buttons to click in the admin panel are the tab headers — match them by
  class (`w-full px-6 py-4 flex items-center justify-between`), never by text,
  because 管理员设置 ships a live `重置配置` button whose class contains `bg-red`.

## Python harnesses

- Always run backend code through `backend/.venv/bin/python`. System `python3`
  has no fastapi/httpx/uvicorn and its `ImportError` looks like a code bug.
- A harness file run directly cannot see the `moontv` package. `_harness.py`
  puts `<repo>/backend` on `sys.path`, so
  `backend/.venv/bin/python <skill>/scripts/x.py` works from any cwd.
- `trust_env=False` on every local call: this machine exports
  `http_proxy=http://127.0.0.1:10808` and `ALL_PROXY=socks://…`, and curl
  through them mangles percent-encoded UTF-8 query params.
- `TestClient` follows redirects by default, so a 307 auth bounce looks like a
  200 `text/html`. Assert with `allow_redirects=False`.
- Static/catch-all harnesses must point the backend at the tree with
  `MOONTV_FRONTEND_DIR` (`static.py` also honors `MOONTV_FRONTEND_DIST` and
  `MOONTV_INDEX_HTML`).
- Vite's content-hash alphabet is base64url, so **`_` is a hash character**:
  `servecheck.py`'s page-token regex must be `page-[A-Za-z0-9_-]{8}`. Without the
  `_` a whole chunk (`page-CC_TwyjE`) is skipped and every probe that lives in it
  reads MISSING — a false regression that looks like the fix was reverted. The
  token count it prints is the tell (7 → 6). `servecheck.py` also takes `!probe`
  to assert a literal is **absent**, the only way to see a guard removal.

## Mutating deployment state

- **Never write `backend/moontv.db` from a second sqlite connection to seed a
  test.** SQLite does not re-read pages through a connection the server already
  has open, so the write is durable on disk but the running server keeps serving
  the old blob — the probe reports the seeded state as absent and silently tests
  nothing. Drive the change through the panel's own routes (`/api/admin/config_file`
  replaces the whole `ConfigFile` string; `/api/admin/source` and `/api/admin/category`
  mutate the lists), or restart the server after the write.
- Wait past `_CONFIG_TTL_S = 5.0` (6s) before re-reading. `invalidate_config_cache()`
  only runs on the server's own saves, so an external change that never reached the
  cache looks like it worked.
- A state-mutating harness must restore what it started from: capture the original
  `ConfigFile` string and the original source order first and replay both at the
  end (`source_delete_check.py` does exactly that), so it is safe on a live site.

## Non-ASCII

Never type CJK into a harness command or file. This machine's I/O path
transcodes CJK in **both** directions, so a typed `source=<暴风 key>` arrives as
two other codepoints and the backend answers `无效的API来源` while an ASCII
`source=IK` passes — and the same reason made a CDP play page report
`未找到匹配结果` for a source that was configured and working.

Derive the string from data instead: the `SourceConfig` key out of
`Db.get_admin_config()`, the title out of the source's own API response, the
episode URL out of the file `mkplayurl.py` wrote. Pass it on percent-encoded —
ASCII percent-encoding is never mangled.

## Play pages

A `/play` URL needs `stype=tv` or `stype=movie`, never the numeric `stype=1`:
`usePlayEngine.ts` puts `stype` straight into `searchType`, and the client's
`typeMatch` is `(searchType === 'tv' && episodes > 1) || (searchType === 'movie'
&& episodes === 1)`. Any other value filters every search result out, so the
page renders `未找到匹配结果` with no `<video>` and no error in `window.__errs`
— a harness artifact, not an app bug. `mkplayurl.py` computes the right one.

## Auth cookie

Get the cookie from `POST /api/login` and pass the **value** half of the
`Set-Cookie` header verbatim. The CDP harnesses call `Network.setCookie` with
`name: 'auth'`, so prefixing another `auth=` doubles it and the gate bounces
back to `/login?redirect=%2F`. `login_cookie.py` prints exactly the value.

## What a harness cannot see

- `TestClient` checks the wire, never the runtime: it does not execute the
  bundle, so mount-time JS errors are invisible to it and show up only as a
  blank page in a browser.
- The CDP browser **cannot load these sources' manifests**: the CDN answers the
  manifest XHR with 404 and no `Access-Control-Allow-Origin`, so hls.js fails
  with `corsErrorStatus.corsError = "MissingAllowOriginHeader"` and `hls.levels`
  stays empty (`currentLevel` -1) even with `--ignore-certificate-errors` on a
  second Chrome. That is the source's CORS plus a stale episode URL, not a port
  gap.
- So the ad filter is measurable **only offline**: `fetch_manifest.py` saves a
  live manifest, `adfilter_probe.cjs <manifest>` prints segments/markers/
  duration/drops/seams, and `adfilter_cases.cjs` is the fixture table (the two
  committed manifests in `fixtures/` are the real-world cases).
- A frontend edit is invisible until `pnpm build` runs. `servecheck.py` compares
  the max mtime of `frontend/src/**` against `frontend/dist/index.html` and then
  greps the served chunk, which is the only way to tell a stale `dist` from a
  real regression.
