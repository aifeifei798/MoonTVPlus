"""Live probe for the 动漫「每日放送」 poster chain.

Usage: backend/.venv/bin/python <skill>/scripts/bangumi_images_check.py [weekday]

The 每日放送 cards are the only posters this app does not own: the calendar comes
from Bangumi in the browser and every poster is an `http://lain.bgm.tv/pic/cover/…`
URL, which `processImageUrl` sends through `/api/image-proxy` (mixed-content
fallback). `lain.bgm.tv` is Cloudflare-fronted and its TLS handshake is
intermittently reset by middleboxes on mainland-China networks, so a single-shot
fetch is what makes those cards blank.

The probe therefore measures three things, in the order the app actually uses them:

  1. which calendar endpoint answers (api.bgm.tv, then the fxbgm.tv mirror the
     frontend falls back to);
  2. whether every poster of the selected weekday renders through the app's own
     `/api/image-proxy` — status, bytes, content-type;
  3. whether the host that answered is the official one or a mirror, and whether
     the mirror's bytes are identical to the official host's when the official
     host is reachable at all (the chain must not silently serve different art).

Exit code 1 if any poster fails to load.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import date
from urllib.parse import quote

import httpx

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))

from _harness import BASE  # noqa: E402

CALENDAR_ENDPOINTS = ("https://api.bgm.tv/calendar", "https://fxbgm.tv/calendar")
OFFICIAL_HOST = "lain.bgm.tv"
MIRROR_HOSTS = ("lain.fxbgm.tv",)
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


async def _get(cx: httpx.AsyncClient, url: str, referer: str = "https://movie.douban.com/"):
    return await cx.get(
        url,
        headers={
            "Referer": referer,
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
            ),
        },
        timeout=15.0,
    )


async def main():
    weekday = sys.argv[1] if len(sys.argv) > 1 else WEEKDAYS[(date.today().weekday()) % 7]

    async with httpx.AsyncClient(trust_env=False, follow_redirects=True) as cx:
        calendar = None
        used = None
        for endpoint in CALENDAR_ENDPOINTS:
            try:
                r = await _get(cx, endpoint)
                if r.status_code == 200 and isinstance(r.json(), list):
                    calendar = r.json()
                    used = endpoint
                    break
                print(f"calendar {endpoint} -> {r.status_code}")
            except Exception as exc:  # noqa: BLE001 — the point is which host died
                print(f"calendar {endpoint} -> EXC {type(exc).__name__} {repr(exc)[:60]}")

        if calendar is None:
            print("FAIL: no calendar endpoint answered")
            return 1
        print(f"calendar: {used} ({len(calendar)} weekday groups)")

        group = next((g for g in calendar if g.get("weekday", {}).get("en") == weekday), None)
        if not group:
            print(f"FAIL: no weekday group for {weekday}")
            return 1
        posters = [
            item["images"]["large"]
            for item in group.get("items", [])
            if item.get("images") and item["images"].get("large")
        ]
        print(f"{weekday}: {len(posters)} posters")
        if not posters:
            print("FAIL: weekday has no poster entries")
            return 1

        ok = 0
        hosts = {}
        for url in posters:
            proxied = await _get(cx, f"{BASE}/api/image-proxy?url={quote(url, safe='')}")
            body = proxied.content
            ctype = proxied.headers.get("content-type", "")
            if proxied.status_code == 200 and len(body) > 1000 and "image" in ctype:
                ok += 1
                # Which host actually served it: the proxy does not say, so re-run
                # the same candidate list and take the first that answers.
                host = None
                for candidate_host in (OFFICIAL_HOST, *MIRROR_HOSTS):
                    try:
                        probe = await _get(
                            cx, url.replace(OFFICIAL_HOST, candidate_host)
                        )
                        if probe.status_code == 200:
                            host = candidate_host
                            if probe.content != body:
                                print(
                                    f"  WARNING {url[:60]}: proxy bytes ({len(body)}) "
                                    f"!= {candidate_host} bytes ({len(probe.content)})"
                                )
                            break
                    except Exception:  # noqa: BLE001
                        continue
                hosts[host or "unknown"] = hosts.get(host or "unknown", 0) + 1
                print(f"  ok {url[:70]} -> {len(body)}B via {host}")
            else:
                print(f"  FAIL {url[:70]} -> {proxied.status_code} {len(body)}B {ctype}")

        print(f"posters loaded: {ok}/{len(posters)} | hosts: {hosts}")
        return 0 if ok == len(posters) else 1


sys.exit(asyncio.run(main()))
