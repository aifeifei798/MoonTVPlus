# Harness rules that cost a session each

These are the traps that made an earlier harness lie. Re-read them before
writing a new probe.

## Node harnesses

- **Name them `.cjs`.** A `.mjs` is treated as ESM, where `require` is
  undefined, so every `require('he')` fails at load.
- Installed packages need
  `NODE_PATH=<repo>/frontend/node_modules` to be `require`-able. The committed
  Python harnesses derive it from `_harness.ROOT`, so it follows the checkout
  instead of a hard-coded absolute path.
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
- **Inside a CDP expression written as a template literal, every regex escape needs
  doubling.** `` const EXPR = `...replace(/\s+/g, ' ')...` `` evaluates as `/s+/g`,
  because a template literal turns the unknown escape `\s` into the letter `s`.
  `drawer_layout_probe.cjs` printed 换源 tile text as `source E144.7 MB/` for exactly
  this reason — the regex ate the `s` of every `MB/s`. The geometry assertions were
  still correct, which is what makes the trap dangerous: only the human-readable
  echo lies, so the harness reports ALL PASS while its own evidence is garbled.
  Write `\\s` (or `new RegExp('\\s+')`) inside backticks.

## Python harnesses

- Always run backend code through `backend/.venv/bin/python`. System `python3`
  has no fastapi/httpx/uvicorn and its `ImportError` looks like a code bug.
- A harness file run directly cannot see the `moontv` package. `_harness.py`
  puts `<repo>/backend` on `sys.path`, so
  `backend/.venv/bin/python <skill>/scripts/x.py` works from any cwd.
- `trust_env=False` on every local call: this machine exports
  `http_proxy=http://127.0.0.1:10808` and `ALL_PROXY=socks://…`, and curl
  through them mangles percent-encoded UTF-8 query params. So **a host that
  `curl` reaches is not evidence the app can**: the backend never uses those
  proxies. Before blaming a route for a dead host, reproduce it with
  `httpx.AsyncClient(trust_env=False)` — `lain.bgm.tv` here connects on TCP but
  the TLS handshake is `ConnectionResetError(104)` (an SNI-selective middlebox;
  `api.bgm.tv` on the *same* IP handshakes fine), which is why the 每日放送
  posters were blank and why "but curl works" is not a counterexample.
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
- **A disconnect probe on an SSE generator needs a loop turn before it closes.**
  The worker pool is an `ensure_future`, and the generator yields `start`
  immediately, so closing at the first event measures zero starts: take `start`,
  `await asyncio.sleep(0.01)` to get the workers in flight, assert the started
  count, then `aclose()` and assert completions stay 0. Counting *starts* is the
  same trap as counting requests in the search route: the signal is completions.
- **`TestClient` keeps the login cookie in its jar** once any fixture logged in, so
  an "unauthenticated" assertion must `client.cookies.pop("auth", None)`; without
  the pop the gate lets the request through and the 401 check passes on a logged-in
  session. The gate answers unauthenticated `/api/*` with plain text `Unauthorized`,
  so a route's own `{"error": "Unauthorized"}` branch is unreachable on gated paths
  and the matcher-excluded paths (`api/tvbox/*`, `api/server-config`, `api/cron`,
  `api/m3u8`, `api/image-proxy`, …) assert the route's own answer instead.
- **Percent-encode every query value** (`quote(v, safe="")`): a raw `+` decodes to a
  space, so `key=FAKE+2` reaches `skipconfigs` as `FAKE 2` and answers
  `无效的key格式` for a well-formed key.
- **`await` binds looser than subscript**: `await Db.get_all_play_records(U)["K"]`
  subscripts the coroutine and raises `TypeError`. Every `DbManager` method is async,
  and `Db.get_admin_config()` returns a str **or** a dict depending on the backend.
- **Tests share the storage singleton and the DB file**, so any test that touches
  user data takes `clean_db`; without it state leaks from one test into the next
  (search history leaked and made three tests fail). `fake_only_source` is the only
  way to get a one-source list: `clean_db` empties the blob, `refine_config`
  re-injects `ConfigFile.api_site` on every read, and replacing the `ConfigFile`
  string does NOT clear `SourceConfig` — the sequence that empties the list is
  `batchDelete` over the keys the read produced.
- **A panel form harness must send the whole form.** `POST /api/admin/site`
  validates nine required fields before writing, so a partial body is a 400
  `参数格式错误` and the harness measures nothing; `strip_none` then drops the
  optional fields it omitted, so the stored blob carries exactly the fields sent.
- A stubbed fetch only has to answer what the route reads: `source_test._probe`
  reads `.status_code` and `.json()`, so a two-member `FakeResponse` exercises every
  branch (HTTP status, broken JSON, missing `list`, empty `list`) with no network,
  and the same stub records `timeout_ms` and in-flight concurrency.
- **Send the params the route reads, not the params its name suggests.**
  `/api/search/one` takes `q` + `resourceId` (the source key), not `source` + `id`;
  a sweep that sends the latter measures only the `缺少必要参数: q 或 resourceId`
  branch and reports the route as covered. `scripts/edge_sweep.py` derives the key,
  api, id and title from `/api/admin/config` and the source's own `?ac=videolist`
  response, percent-encoded, and asserts the 404 `未找到结果` shape for a live
  title. The same rule is why the sweep's GET `/api/user/online` is a case: the
  route is POST-only, so the answer must be the app document at **404**, and a
  probe that reads 200 + HTML as "the route works" measures the catch-all.

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
transcodes CJK in **both** directions, so a typed `source=<source B key>` arrives as
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
- **A mobile probe must tap, not click.** `element.click()` from `Runtime.evaluate`
  grants no transient activation, and `video.requestPictureInPicture()` answers
  `NotAllowedError: … Must be handling a user gesture if there isn't already an
  element in Picture-in-Picture`, so a probe that "clicks" the 画中画 button reports
  PiP as broken. Use `Input.dispatchTouchEvent` with `type: "touchStart"` +
  `touchPoints: [{x, y}]` followed by `type: "touchEnd"` with `touchPoints: []`.
- **The bar is down until the video is tapped.** On a phone Artplayer's control
  module registers `click -> isMobile ? this.toggle() : this.show = true`, and while
  neither `art-control-show` nor `art-hover` is present the controls are translated by
  `--art-bottom-offset` (20px) below the player. A probe that measures or taps a
  control before that first tap measures a bar the user never sees.
- `tailwind.css` keeps its URL across builds, so a probe that wants the CSS of the
  build it just made must call `Network.setCacheDisabled` first — the Chrome profile
  otherwise serves the previous build's stylesheet and the probe silently measures
  stale rules.

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
- `naturalWidth === 0` is not a failed image. Every card poster is
  `loading="lazy"`, so below the fold nothing has attempted a fetch yet and the
  count of "broken" posters is just the count of unscrolled ones — a census that
  reads it without scrolling or setting `img.loading = 'eager'` measures nothing
  and can report a fix as working (or as absent) for the wrong reason.
  `scripts/poster_census.js` does the eager pass and then counts only
  `img.complete` images.
- "The poster loads" is not the same claim as "the poster is the one the card
  chose". `VideoCard` walks an ordered chain (豆瓣 → 源 → 站点图标) and advances
  `posterIndex` on every `onError`, so a card can render a perfectly healthy source
  cover while the douban lookup behind it is broken. `scripts/record_poster_census.js`
  classifies each record poster by its URL and reports the counts of each link; to
  see the third link you must seed a record whose cover 404s and whose title douban
  does not know, through `POST /api/playrecords` — writing the db file from a second
  `sqlite3` connection leaves the running server serving the blob it already has
  open, so the probe silently tests nothing.
- **A census of the default view is not a census of the page.** The home page is a
  `CapsuleSwitch` (首页 / 历史 / 追更 / 收藏夹) and only one view is mounted at a time,
  so a probe that navigates to `/` and counts record cards sees only 继续观看 and
  reports `recordCards: 1` for a page that has all three rows. `record_poster_census.js`
  clicks each tab (the button whose `innerText` is exactly the label), sleeps 2.5 s for
  React's scheduler to mount the view, then 9 s for the douban lookups to advance the
  chain, and censuses each view. `element.click()` is enough for a tab switch (no
  transient activation needed, unlike PiP), but reading the DOM in the same task as the
  click measures the old view.
