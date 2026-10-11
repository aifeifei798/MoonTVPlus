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
and the original source order is replayed.

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
