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
- **An SSE parser must skip the empty trailing chunk.** `text.split("\n\n")` on a
  body that ends with the framing leaves a final `""`, which does not start with
  `data: `; a strict parser returns `None` and every check under `if events:` is
  silently skipped, so the harness prints a green-looking count that never ran.
  `source_health_check.py`'s `sse()` `continue`s on empty chunks.
- **The auth gate runs before routing**, so an unauthenticated GET of a POST-only
  path answers 401, not the 404 the route's own fall-through would. Assert the
  404 with the cookie, and the 401 as a separate check — conflating them hides a
  route that was never registered.
- `http.server` has no `BaseHandler` — the class is `BaseHTTPRequestHandler`, and
  silencing its access log means overriding `log_message`/`log_error`, not
  `log` (no such method, so a `log` override is dead code and the harness output
  becomes a wall of request lines that buries the real result).
- Deterministic classification needs a **local** server, not the network: seed
  sources whose `api` points at a `ThreadingHTTPServer` the harness starts, and
  dispatch on a path marker inside the URL, because the probe appends
  `?ac=videolist&wd=` to whatever api the panel was given.

## External oracles (the `.dat` backup)

The `.dat` format has no upstream tree to diff against on this machine, so it is
checked against the libraries that define it. Three traps in that leg:

- **httpx spells it `follow_redirects=`**; `allow_redirects=` is `requests`' name
  and raises `TypeError` mid-harness, which looks like the route under test broke.
- **openssl 3.0.13**: `-S <hexsalt>` **suppresses** the `Salted__` header it
  would otherwise write, so a python blob built with a forced salt can never
  match openssl's output — compare against openssl's *own* salt, or drive both
  directions through `enc -aes-256-cbc -md md5 -pass pass:…`. `openssl dec` is
  not a command (`enc -d` is), and `-md md5` prints a deprecation warning on
  **stderr while still succeeding**, so a harness must test the exit code, never
  "stderr is empty".
- **`readFileSync(2)` means fd 2**, not argv 2 — `EBADF`/`EINVAL` on a script file.
  Always `process.argv[2]`, and remember a script file's args start at `argv[2]`.
- `crypto.encode_backup(payload, password)` takes the **JSON string** and does
  deflate → base64 → AES itself. Feeding it an already-deflated string deflates
  twice and the openssl comparison fails for a reason that reads like a port gap.
  `encrypt`/`decrypt` are the AES layer alone; `base64.b64encode(pako_deflate(...))`
  is the middle layer to compare an openssl decrypt against.
- The export's `done` event carries `message`/`exportedUsers`/`filename`/`percent`
  and **no** `serverVersion` — upstream stamps `timestamp` and `serverVersion` in
  the backup **payload**, so the claim is `backup["serverVersion"] == CURRENT_VERSION`.

## Redis backend (the storage oracle)

- **`fakeredis.TcpFakeServer(addr, bind_and_activate=True)` only BINDS.** Nothing
  answers until `serve_forever` runs, so start it in a daemon
  `threading.Thread(..., daemon=True)`; there is no `stop_all()`, so the process
  exits with the app. Without the thread every redis call hangs into the client's
  10 s connect timeout and the harness looks like a broken port.
- **Pick free ports, never fixed ones.** `TcpFakeServer` raises
  `OSError: [Errno 98] Address already in use`, and a leftover scratch server from
  an earlier session silently steals a run — the harness then measures the old
  store. Use `socket(...).bind(("127.0.0.1", 0))` + `getsockname()[1]` with
  sequential `exclude` sets, and kill leftovers with the bracket trick
  (`pgrep -f "redis_serve_fake[.]py"`, which does not match the `pgrep` itself).
- **The redis-only `reset_config` branch is vacuous on this box** until
  `MOONTV_CONFIG_PATH` points at a config file that HAS a `custom_category`
  (`backend/config.json` has only `api_site` + `cache_time`). Copy the `api_site`
  map verbatim into the throwaway file, or the source census stops agreeing across
  backends for a reason that reads like a port gap.
- **Take a store census BEFORE the scenario's `deleteUser`.** After it the seed
  user's keys are gone, so "the search history is the only LIST" reads `[]` and
  passes without testing anything.
- Reading sqlite's admin blob from a second connection is fine — the trap that
  makes a harness lie is **writing** from one while the server holds its own.
- The export SSE chunk field is **`data`** (`{"type":"chunk","data":…}`), not
  `chunk`; a reader that guesses the name collects `""` and every downstream
  decrypt check silently passes on an empty blob.
- `Db.batch_import_today_updated(username, record)` takes **no** `on_progress`
  callback, unlike the other five `batch_import_*` — arity, not behavior.
- The missing-URL message is literally `${clientName}_URL env variable not set`,
  so the port's mixed-case `Redis_URL …` / `Kvrocks_URL …` **is** faithful; an
  all-uppercase expectation is the harness bug, not a port gap.
- `_is_connection_error` must match redis-python's spelling, not node's: the wire
  message is `Error 111 connecting to 127.0.0.1:9. Connect call failed …`, whose
  word is lowercase and whose code is a bare errno. A case-sensitive `Connection`
  test silently kills the retry wrapper, and the failure then looks like a dead
  port rather than a dead matcher.
- Cross-backend compares must skip the upstream-faithful differences
  (`UPSTREAM_DIFFERS`) and assert them explicitly as differences; comparing whole
  record objects makes them fail for reasons that read like port gaps.

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

## Player-state probes

- Artplayer 5.3.0 hides the bottom bar inside `video:timeupdate`
  (`!setting.show && !controls.isHover && !isInput && playing && show &&
  Date.now() - controls.timer >= CONTROL_HIDE_TIME`), so a probe watching
  `controls.show` measures **nothing** on a page where `playing` is false. On a
  real `/play` page that is the normal state here (the manifest XHR 404s), so
  point the `<video>` at a local mp4 first: `inst.video.hls.destroy()` —
  otherwise hls.js's `sourceopen` handler puts its blob `src` back — and
  `v.removeAttribute('crossOrigin')`, because the app sets
  `crossOrigin="anonymous"` through `moreVideoAttr` and the local probe server
  sends no ACAO, so the swap silently fails and Artplayer's reconnect logic
  restores the blob.
- `controls.isHover` is refreshed only by a `document` mousemove that actually
  lands on `$bottom` (only `.art-progress` and `.art-controls` carry
  `pointer-events: auto`), so a probe that guesses a y inside the bar's
  bounding rect can report `isHover=false` while the bar is visible. Install a
  capture-phase `mousemove` listener with `Page.addScriptToEvaluateOnNewDocument`
  and sweep y until `isHover` is true before concluding anything.
- `PlayStatusView`'s `VideoLoadingMask` (`z-[120]`) covers the player whenever
  the source is still "loading" and swallows every mouse event. Take it out of
  the way before probing the bar, and report whether it was there.
- A counterfactual has to be pinned **without** a following mousemove: setting
  `controls.isHover = true` and then moving the pointer re-runs the
  `document:mousemove` handler, which sets `isHover` from the hit test and
  quietly undoes the pin.

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
