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
