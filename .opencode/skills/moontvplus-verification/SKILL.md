---
name: MoonTVPlus Verification
description: Verify a MoonTVPlus port or fix against the shipped TypeScript and the live wire — byte-comparison fixtures, offline HLS/ad-filter probes, CDP browser probes, auth cookies, derived /play URLs. Use when porting a route, claiming a frontend fix is live, or reproducing the playback desync and white-screen symptoms.
---

# MoonTVPlus Verification

Ports are verified by **running both sides and byte-comparing**, not by reading
the TypeScript. Every harness for this repo lives here, in `scripts/`, so it
survives `/tmp` cleanup and ships with the checkout.

Paths below are relative to this skill's directory. The repo root is
`../../../..`; `scripts/_harness.py` and the `.cjs` headers resolve it
automatically (`MOONTVPLUS_ROOT` overrides).

Read `references/harness-rules.md` **before** writing or editing any probe —
it lists the traps that make a harness lie (`.cjs` vs `.mjs`, `argv` offsets,
CJK transcoding, `stype`, `trust_env`, `allow_redirects`, what a harness
cannot see).

## Verify a ported route

1. Write the fixture table as a `.cjs` that slices the function out of the
   `.ts` and runs it through `esbuild.transformSync`, and the matching Python
   port; byte-compare the two outputs. Copy the shape from
   `scripts/adfilter_cases.cjs`.
2. For wire shape, call the running server with httpx `trust_env=False` and
   assert `allow_redirects=False`.
3. `TestClient` proves the wire only. It never executes the bundle.

## Verify the route wire surface (offline, no network)

```bash
cd backend && backend/.venv/bin/python -m pytest -q      # 232 green, from backend/
```

The committed modules in `backend/tests/` cover the B5/B6 routes that earlier
sessions only curl-checked: `test_search_sub.py` (14), `test_detail.py` (7),
`test_tvbox.py` (14), `test_user_history.py` (18), `test_cron.py` (6),
`test_server_config.py` (5), `test_source_test.py` (14), plus the guard, image
proxy, search stream, auth gate, static, version, storage and backup modules. The
fake media server (`FAKE_PAGE_1`/`FAKE_PAGE_2`, `fake_upstream`, `fake_source`,
`fake_only_source`) lives in `tests/conftest.py` and answers `?ac=videolist` on a
loopback port, which is why `conftest.py` sets `MOONTVPLUS_URL_GUARD_ALLOW_PRIVATE=1`
and `test_url_guard.py` must undo it per test. The suite is deterministic: no
live source, no browser, no redis server (the redis leg uses
`fakeredis.TcpFakeServer`).

## Sweep the live edge cases (read-only, against the running server)

```bash
backend/.venv/bin/python .opencode/skills/moontvplus-verification/scripts/edge_sweep.py
```

Every case is a GET, so the sweep cannot reorder, re-enable or resurrect config.
It derives the source key, api url, id and title from the app's own responses
(`/api/admin/config` → `ConfigFile.api_site`, then the source's `?ac=videolist`)
and sends them percent-encoded, because this machine's I/O path transcodes CJK
typed into a command — a hand-typed `source=暴风` answers `无效的API来源` and the
sweep reports a defect that is not there. Credentials come from `./.env` (or the
shell the server was started from) through `_harness.load_env`. It fails
(exit non-zero) on any 500, and on a 404 at a **non-API** path (the catch-all
serves the app document at 200 there; an unmatched `/api/*` path serves the
document at 404, which is what Next's not-found page did, so `GET
/api/user/online` — a POST-only route — correctly reports 404 + HTML). A live
source that is down answers its own reason string, so the shape census prints
what each route answers and only the 500/404 census is a verdict.

## Verify the ad filter (offline only)

```bash
node scripts/adfilter_cases.cjs                       # fixture table, must be all PASS
node scripts/adfilter_probe.cjs fixtures/live-source-b.m3u8
backend/.venv/bin/python scripts/fetch_manifest.py @/tmp/opencode/episode.txt /tmp/opencode/live.m3u8
node scripts/adfilter_probe.cjs /tmp/opencode/live.m3u8
```

The invariants: content segments are never dropped, a marker survives **only**
where the two surviving groups' signatures differ, and a dropped group's last
`#EXT-X-KEY` line is re-emitted at the seam. The manifests in `fixtures/` are
captured live sources (URLs expire; the shape does not).

## Verify the browser side

```bash
bash scripts/chrome_start.sh 9334                     # keep it alive; harnesses only attach
export CDP_PORT=9334
AUTH=$(backend/.venv/bin/python scripts/login_cookie.py)
backend/.venv/bin/python scripts/mkplayurl.py         # writes /tmp/opencode/playurl.txt
backend/.venv/bin/python scripts/servecheck.py        # dist fresh + chunk carries the change
node scripts/cdp_eval.cjs "$AUTH" "$(cut -c1- /tmp/opencode/playurl.txt)" '<expr>' 8000
node scripts/cdp_net.cjs "$AUTH" /play/<from-playurl> <hostFilter> 12000
```

A blank page with a single `+0 -1` body mutation means React cleared the root's
whitespace text node and rendered nothing — the **render value**, not an
exception. `cdp_eval.cjs` installs the error listeners with
`Page.addScriptToEvaluateOnNewDocument` so they exist before the bundle runs,
and reads `window.__errs`; an empty `__errs` with a blank body is a render-value
bug, not a thrown one.

## Verify the control-bar auto-hide

```bash
bash scripts/chrome_start.sh 9334
AUTH=$(backend/.venv/bin/python scripts/login_cookie.py)
node scripts/artplayer_hide_probe.cjs --port=8899      # synthetic oracle, no server needed
node scripts/cdp_control_hide.cjs "$AUTH" "$(cut -c1- /tmp/opencode/playurl.txt)"
```

Artplayer 5.3.0 hides the bottom bar inside `video:timeupdate`, gated on
`controls.isHover`, which only a `document` mousemove landing on `$bottom` can
refresh — a pointer that exits the window straight from the bar leaves it stuck at
`true` and the bar never collapses. The synthetic probe runs the same gesture
against the pinned `node_modules/artplayer` with the listener toggled off by
`?nofix=1`: **fix → 3000 ms, no-fix → never**. The live probe drives the real
`/play` page (see `references/harness-rules.md`, "Player-state probes", for the two
things that make the local-clip swap stick) and measured **2794 ms** on the shipped
bundle.

## Verify the mobile control bar (PiP / 网页全屏)

```bash
bash scripts/chrome_start.sh 9334
AUTH=$(backend/.venv/bin/python scripts/login_cookie.py)
node scripts/cdp_bar_overflow.cjs "$AUTH" "$(cut -c1- /tmp/opencode/playurl.txt)"
node scripts/cdp_mobile_player.cjs "$AUTH" "$(cut -c1- /tmp/opencode/playurl.txt)" --width=320 --height=568
node scripts/cdp_mobile_player.cjs "$AUTH" "$(cut -c1- /tmp/opencode/playurl.txt)" --landscape
```

`cdp_bar_overflow.cjs` censuses the control row at 320/360/414/430/440/480 and exits
non-zero if the right-hand controls hang outside the player; it also re-adds the
middle group (`display: flex !important`) so the pre-fix numbers (92px clipped at
320px, 52px at 360px) are reproducible on the shipped CSS. `cdp_mobile_player.cjs`
taps 网页全屏 and 画中画 with `Input.dispatchTouchEvent` and asserts the browser
actually enters web fullscreen and PiP — see `references/harness-rules.md`,
"Player-state probes", for the two traps (a synthetic click is not a gesture, and
the bar is only up after a tap on the video).

## Verify the PC 选集/换源 drawer

```bash
bash scripts/chrome_start.sh 9334
AUTH=$(backend/.venv/bin/python scripts/login_cookie.py)
node scripts/drawer_layout_probe.cjs "$AUTH" "$(cat /tmp/opencode/playurl_tv.txt)" --shots=/tmp/opencode/drawer-shots
```

`drawer_layout_probe.cjs` walks 1440x900 / 1280x800 / 1024x720 / 900x600 / 768x600 /
767x600 / 375x667 and exits non-zero on any FAIL. What it pins: the drawer is a child of
`.art-video-player` (so it survives native and web fullscreen), it is open by
default, its bottom clears `.art-controls` (21px at every desktop size), the
video/poster layers are narrowed to `calc(100% - var(--mntv-drawer-width))` so the
drawer covers the letterbox rather than the picture, the episode grid renders
78x36 buttons in a 380px drawer, the control-bar toggle closes and reopens it (the
video returns to the full player width), the 换源 tab renders `[data-source-key]`
tiles that sit **several per row** (>= 2 tiles share the first row's `top`) and are
compact (<= 60px tall, less than half the panel width — 81x48 in a 380px drawer,
109x48 at 375px), every tile has a second line with the measured speed (`...` while
unprobed, `测速失败` in red when the probe failed), a click moves `data-active`, the
tab row is not clipped by the panel, the loading mask is narrowed to the video area,
and the drawer's z-index (70) stays below Artplayer's settings panel (90). At
767x600 and 375x667 it asserts the opposite: no drawer, toggle hidden, panel below
the player — and the same compact 换源 grid still applies there.

Two traps: the drawer element is **recreated** by the portal on every toggle, so a
captured reference goes stale — re-query `.mntv-episode-drawer` after toggling; and
`/assets/*.css` is served with `cache-control: public, max-age=300`, so a Chrome that
probed the previous build answers from its own cache and the probe measures the OLD
layout. The harness runs `Network.clearBrowserCache` before navigating for exactly
this reason.


```bash
node scripts/cdp_image_fill.cjs "$AUTH" / /douban?type=movie "/search?q=Spider"
```

Every list card is a `relative aspect-[2/3] overflow-hidden` box with an
`object-cover` poster. The probe censuses each poster `<img>` on a live page and
requires its rendered box to equal the container's, then inserts the two `fill`
mechanisms side by side in one 180x270 box with a 270x337 poster: the attribute
form (`width="100%" height="100%"`, which HTML rejects as a percentage and which
therefore falls back to Tailwind preflight's `max-width: 100%; height: auto`)
renders **180x225** — the missing bottom chunk — while the CSS form renders
**180x270**. Exit code is non-zero if any poster box is not covered.

## Verify the 每日放送 poster chain

```bash
backend/.venv/bin/python scripts/bangumi_images_check.py        # weekday defaults to today
backend/.venv/bin/python -m pytest tests/test_image_proxy.py    # offline, no network
```

The 每日放送 cards are the only posters this app does not own: the calendar is
fetched by the browser from Bangumi and every poster is an
`http://lain.bgm.tv/pic/cover/…` URL, which `processImageUrl` sends through
`/api/image-proxy` (the mixed-content fallback). The probe reports which calendar
endpoint answered, then loads every poster of the selected weekday through the
app's own proxy and re-runs the candidate list to name the host that actually
served the bytes — it fails if the proxy's bytes differ from the host it fell back
to. Offline, `backend/tests/test_image_proxy.py` drives the same handler with a
stub client, so the assertions are about **order and error shapes** (official
first, fall through only on a connection error or a 5xx, a 404 from the official
host is still the answer) without touching a network.

## Verify the poster fallback chain

```bash
node scripts/cdp_eval.cjs "$AUTH" "/" @scripts/poster_census.js 12000
node scripts/cdp_eval.cjs "$AUTH" "/douban?type=anime" @scripts/poster_census.js 12000
```

`img*.doubanio.com` answers a referer-less request with **418 / 14 bytes**, and
every card is `referrerPolicy='no-referrer'`, so with the default
`DoubanImageProxyType: direct` the browser cannot load a douban poster at all —
before the fix the home page census reported 60 broken of 76. `retryImageThroughProxy`
(`frontend/src/lib/utils.ts`) retries a failed `<img>` once through
`/api/image-proxy?url=…`, which sends the upstream `Referer` and, for the douban
family, walks `img.doubanio.cmliussss.net` / `img.doubanio.cmliussss.com`
(`MOONTVPLUS_DOUBAN_IMAGE_MIRRORS`). The census forces every lazy poster to
attempt a fetch (`img.loading = 'eager'`) and then counts only images the browser
actually tried; it reports per-section `broken` and `viaProxy`, so a rescue that
silently did nothing is visible. Green means `brokenCount: 0` with the douban rows
showing `viaProxy` equal to the number of posters in the row.

## Verify the record poster chain (豆瓣 → 源 → 站点图标)

```bash
node scripts/cdp_eval.cjs "$AUTH" "/" @scripts/record_poster_census.js 60000
```

`poster_census.js` only knows whether an image painted; it cannot say **which link**
of the chain the card chose. `record_poster_census.js` scopes to the record rows
(继续观看 / 追更 / 收藏 — the only cards that do the douban lookup) and classifies each
poster as `douban`, `source` or `fallback-logo`, so a card that fell back to the
source cover because the lookup silently 500'd is distinguishable from one that fell
back because douban had no match. The home page is a `CapsuleSwitch` with tabs
首页 / 历史 / 追更 / 收藏夹 and only one view is mounted at a time, so the probe
**clicks each record tab** (the button whose text is exactly the label) and sleeps
2.5 s for React's scheduler to mount the view plus 9 s for the douban lookups (4
concurrent, `poster.client.ts`) to advance the chain — a census of the default view
sees only the 继续观看 cards, which is the failure mode the 14 s timeout hid. Seed
the third link through the panel's own route (the script's header has the curl) —
never by writing `backend/moontv.db` from a second connection, since the running
server keeps serving the blob it already has open. Green is `unpainted: 0` with the
seeded trio reporting one of each link: 继续观看 → `douban` direct, 收藏 → `douban`
with `proxied: true, retried: true` (the direct-mode doubanio poster retried through
`/api/image-proxy` and painted), 追更 → `fallback-logo` (title douban does not know
plus a cover that 404s, so the chain is exhausted). The 追更 view renders the same
card twice (有新未观看集数 + 全部追更), so `recordCards` is 4, not 3.

The route behind it is additive surface, not a port: `/api/douban/search?q=…` scrapes
`search.douban.com/movie/subject_search` and parses `window.__DATA__ = {...}`. Offline
coverage is `backend/tests/test_douban_search.py` (fixture page, the 400/401 paths,
the stale-cache branch); live coverage is this census plus a curl of the route with
the auth cookie.

## Reproduce the desync

The desync is not measurable in the CDP browser (the manifest XHR fails CORS).
Prove it offline instead: `adfilter_probe.cjs` must report **0 markers** at
splice seams for a live manifest, and the PTS evidence comes from
`ffprobe -show_entries stream=pts_*` on the two TS segments that straddle a
seam — content files carry absolute, continuous PTS across the seam, so a
`#EXT-X-DISCONTINUITY` there is a lie and hls.js 1.7.3 re-anchors the two
tracks asymmetrically (`audioTimeOffset += Math.max(0, Δ)` /
`videoTimeOffset += Math.max(0, -Δ)`).

## Verify the admin panel's source/category deletes

```bash
backend/.venv/bin/python scripts/source_delete_check.py   # 22/22, restores the state it started from
```

The trap this exists for: `getConfig` re-merges the admin blob's `ConfigFile.api_site`
map into `SourceConfig` on every read, so a `from == "config"` source removed from the
list is rebuilt on the next request. Upstream hid that by refusing a single delete
(`该源不可删除`) and by having `batchDelete` skip them while still answering
`{"ok":true}` — the panel promised a deletion that never happened and the count stayed.
The harness deletes a config-declared source and asserts the count is **still down 6s
later**, past `_CONFIG_TTL_S = 5.0`, which is the only way to see the resurrection; it
seeds a `custom_category` through `/api/admin/config_file` and checks the same for
categories. It replays the original `ConfigFile` string and source order at the end, so
it is safe to run against a live deployment.

## Verify the source health check

```bash
backend/.venv/bin/python scripts/source_health_check.py   # 33/33, restores the state it started from
```

Two halves, because the two questions are different. The deployment's own sources
are checked only for **consistency** — exactly one `item_result`/`item_failed`
event per source, `data: {...}\n\n` framing, `okCount + failedCount == total` —
because whether a third-party `?ac=videolist` site answers is a property of the
network, not of this repo (this box has no direct access to most of them, which
is the same reason `/api/search` reports them failed). The **classification** is
asserted against a local `ThreadingHTTPServer` the harness starts, so alive /
HTTP 404 / 200-with-HTML / 5 s-with-`timeout: 1` are deterministic. The expected
reason strings are imported from the code (`moontv.routes.source_test.FORMAT_ERROR`,
`str(moontv.fetch.FetchTimeout())`) — typing them into the harness compares
transcoded codepoints against transcoded codepoints and silently tests nothing.
Seeded keys are ASCII (`zzok`/`zzbad`/`zzmalformed`/`zzslow`), deleted at the end,
and the original source order is replayed. `backend/tests/test_source_test.py` (14
wire tests, no network) pins the same route offline: the SSE framing and content
type, the zero-result warning (`count: 0` counts as ok, PLAN.md change 18), the
format/HTTP/no-api reasons, the `CONCURRENCY = 5` pool (max in-flight is 5 with six
sources), the disconnect cancel (five starts, zero completions), the seconds-clamped
`timeout`, and the read-only promise (the SourceConfig projection is identical
before and after a run).

## Verify the `.dat` backup migration (B7)

```bash
backend/.venv/bin/python scripts/dat_backup_check.py   # 97/97, restores the state it started from
node scripts/dat_backup_node.cjs /tmp/opencode/dat_spec.json /tmp/opencode/dat_out.json
```

The format has no upstream tree to diff against here, so it is checked against the
libraries that **define** it: `crypto-js@4.2.0` (what upstream's `crypto.ts` used,
installed in `frontend/node_modules`) for the AES layer both directions, `openssl
enc -aes-256-cbc -md md5` for the same layer through an independent `EVP_BytesToKey`
implementation, and node's `zlib.deflateRawSync(..., {level: 6})` for pako's DEFLATE
both directions. Then a **live** export → saved `.dat` file → multipart import round
trip against the running server, which is the only way to see that the import's
`Db.clear_all_data()` — it deletes all eight data tables before replaying the
backup — eats nothing: the harness seeds one of every record kind through the
panel's own routes, asserts each survives, and restores the original `ConfigFile`
string, source order and users at the end.

The browser half is `components/DataMigration.tsx`, rendered as the owner-only
collapsible tab in the admin panel. It is collapsed on load (`expandedTabs` has no
`dataMigration` key, so it is `undefined` → falsy), so a CDP probe clicks the **last**
tab header (`w-full px-6 py-4 flex items-center justify-between`) and then reads
`input[type=file]` with `accept=".dat"` plus the two migration buttons
(`px-4 py-2.5 rounded-lg font-medium transition-colors mt-10`). Both are
`disabled` until a password/file is typed, so assert the shared class, not
`bg-blue-600`/`bg-red-600` — the disabled variant swaps those for
`bg-[#f5f5f7] … cursor-not-allowed`.

## Verify the Redis/Kvrocks backend

```bash
backend/.venv/bin/python scripts/redis_storage_check.py   # 96/96, touches no deployment state
backend/.venv/bin/python scripts/redis_serve_fake.py      # one app + one fake RESP socket, for ad hoc probes
```

There is no redis-server on this box (`which redis-server redis-cli` is empty and
sudo needs a password), so the oracle is `fakeredis`'s `TcpFakeServer`: a real
RESP **TCP** server, which is why the real `redis.asyncio` client's wire encoding
and the port's command surface are exercised. No claim here is made about a real
server's own behavior (timeouts, RDB, Kvrocks' `exh*` extensions).

`redis_serve_fake.py` runs both in one process — uvicorn owns the main loop, the
fake server runs in its own thread — and reads `FAKE_REDIS_PORT`, `HTTP_PORT` and
`NEXT_PUBLIC_STORAGE_TYPE`; it points `REDIS_URL` or `KVROCKS_URL` at the fake
socket for whichever of the two is asked for. A key census taken from an **outside**
`redis.Redis(host, port)` is honest here, unlike the sqlite case: the server never
holds a connection to that socket.

The harness runs one scenario against sqlite/redis/kvrocks servers it spawns
itself (fresh temp db, `MOONTV_CONFIG_PATH` at a throwaway `config.json` that has
a `custom_category`, free ports) and canonicalizes the responses. The upstream
differences are asserted **as differences** on the right axis, not as equality:
redis's `LREM key 0 <kw>` dedup is always newest-first while D1's
`CURRENT_TIMESTAMP` ties inside a second, and redis hands back the stored JSON
while D1 rebuilds typed columns. The redis-only `reset_config` branch is visible
only in the **stored** blob — on the wire all three show the category again,
because `get_config` re-injects the blob's `ConfigFile.custom_category` on read.
