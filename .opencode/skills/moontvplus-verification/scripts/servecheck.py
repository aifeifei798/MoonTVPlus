"""Prove the SHIPPED bundle carries a change, i.e. close the stale-dist trap.

Usage: backend/.venv/bin/python <skill>/scripts/servecheck.py [probe ...]

A frontend edit is invisible until `pnpm build` runs, so this checks the max
mtime of frontend/src/** against frontend/dist/index.html first, then fetches
the served entry chunk and greps it for the probes (default: the two regex
literals the rewritten filterAdsFromM3U8 must leave in the bundle). Prefix a
probe with `!` to assert the literal is NOT shipped — that is the only way to
see a guard removal (e.g. `!'from!=="config"'`).

Run it after every frontend change you claim is live; a stale dist is the usual
cause of "the fix is in the source but the page did not change".
"""

import pathlib
import re
import sys

import asyncio
import httpx

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from _harness import BASE, ROOT, load_env, auth_cookie

PROBES = sys.argv[1:] or ["EXT-X-DISCONTINUITY(?!-", "EXT-X-KEY\\b/i.test"]


def fresh():
    dist = ROOT / "frontend/dist/index.html"
    if not dist.is_file():
        print("dist/index.html MISSING — run pnpm build in frontend/")
        return False
    newest = max(
        (p.stat().st_mtime for p in (ROOT / "frontend/src").rglob("*") if p.is_file()),
        default=0,
    )
    ok = newest <= dist.stat().st_mtime
    print("dist newer than src:", ok, "| dist", dist.stat().st_mtime, "src max", newest)
    return ok


async def main():
    cookie = auth_cookie()
    async with httpx.AsyncClient(trust_env=False, timeout=30) as cx:
        r = await cx.get(BASE + "/", headers={"Cookie": "auth=" + cookie})
        html = r.text
        print("index", r.status_code, "bytes", len(html))
        entry = re.search(r'/assets/index-[^"]+\.js', html)
        print("entry chunk", entry and entry.group(0))
        if not entry:
            return
        e = await cx.get(BASE + entry.group(0))
        print("entry", e.status_code, "bytes", len(e.content))
        # Page chunks are referenced by bare token (page-DnsKth3D), never with
        # the /assets/ prefix — the prefix is added at serve time. Vite's hash
        # alphabet is base64url, so `_` is a hash character: leaving it out of
        # this class silently drops a chunk (page-CC_TwyjE) and makes every
        # probe that lives in it read MISSING.
        tokens = sorted({m.group(0) for m in re.finditer(rb"page-[A-Za-z0-9_-]{8}", e.content)})
        print("page tokens", len(tokens))
        hits = {}
        for t in tokens:
            # The token has no extension: asking for /assets/page-X alone hits the
            # catch-all, which answers the app document at 200, not a 404.
            c = await cx.get(BASE + "/assets/" + t.decode() + ".js")
            if c.status_code != 200 or "javascript" not in c.headers.get("content-type", ""):
                print("  miss", t, c.status_code, c.headers.get("content-type"))
                continue
            for probe in PROBES:
                # A leading ! means the literal must NOT be shipped (the guard
                # removals are only visible as an absence).
                needle = (probe[1:] if probe.startswith("!") else probe).encode()
                if needle in c.content:
                    hits.setdefault(probe, []).append((t.decode(), len(c.content)))
        for probe in PROBES:
            negated = probe.startswith("!")
            got = hits.get(probe)
            if negated:
                print("  lacks", probe[1:], "ok" if got is None else "PRESENT — must not be shipped")
            else:
                print("  has", probe, got or "MISSING")


fresh()
asyncio.run(main())
