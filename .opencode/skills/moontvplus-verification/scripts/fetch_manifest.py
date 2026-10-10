"""Fetch a live episode manifest WITHOUT touching its URL in the shell.

Usage: backend/.venv/bin/python <skill>/scripts/fetch_manifest.py <url|@file|-> [out]

The URL usually contains non-ASCII (this machine's I/O path transcodes CJK, and
hand-percent-encoding a raw path 404s), so pass @/tmp/opencode/episode.txt — the
file mkplayurl.py wrote — rather than the URL itself.

Saves the manifest and prints the structural census the ad filter is judged on.
"""

import pathlib
import sys

import asyncio
import httpx

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))


async def main():
    arg = sys.argv[1]
    if arg == "-":
        url = sys.stdin.read().strip()
    elif arg.startswith("@"):
        url = pathlib.Path(arg[1:]).read_text(encoding="utf-8").strip()
    else:
        url = arg
    out = pathlib.Path(sys.argv[2] if len(sys.argv) > 2 else "/tmp/opencode/live.m3u8")

    async with httpx.AsyncClient(trust_env=False, timeout=60, follow_redirects=True) as cx:
        try:
            r = await cx.get(url)
        except httpx.ConnectError as exc:
            # The source's own doing: these CDNs rotate the host and expire the
            # episode URL, so a stale one does not resolve. Upstream 500s
            # identically; it is not a port gap. Get a fresh URL from a fresh
            # search (mkplayurl.py) or use the committed fixtures instead.
            print("connect error", exc, "|", url[:60])
            return
        except httpx.TooManyRedirects as exc:
            print("too many redirects", "|", url[:60])
            return
        print("status", r.status_code, "bytes", len(r.content))
        if r.status_code != 200:
            print("note: a raw non-ASCII path that was hand-percent-encoded 404s;"
                  " use the URL string from data, unmodified")
            return
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(r.content)
        txt = r.text
        segs = [ln for ln in txt.splitlines() if ln.strip() and not ln.startswith("#")]
        markers = [ln for ln in txt.splitlines() if "#EXT-X-DISCONTINUITY" in ln]
        dur = sum(float(ln[8:].split(",")[0]) for ln in txt.splitlines() if ln.startswith("#EXTINF"))
        print("saved", out)
        print("segs", len(segs), "markers", len(markers), "duration", round(dur, 2))
        for ln in segs[:3]:
            print("  head", ln)
        for ln in segs:
            if "adjump" in ln or "/ad/" in ln:
                print("  ad-shaped", ln)
                break


asyncio.run(main())
