"""Derive a WORKING /play URL from data, never from typed CJK.

Usage: backend/.venv/bin/python <skill>/scripts/mkplayurl.py [api-substring] [ids]

Writes /tmp/opencode/playurl.txt (the `/play?...` path) and /tmp/opencode/
episode.txt (the first episode URL, which may contain non-ASCII and therefore
must never be retyped into a shell command).

Why it has to be derived: this machine's I/O path silently transcodes CJK in
both directions, so a typed `source=<暴风 key>` arrives as two other codepoints
and the backend answers `无效的API来源`. The key comes out of
Db.get_admin_config()['SourceConfig'] and the title out of the source's own
API response, and both go on the wire percent-encoded, which is never mangled.

The URL also needs stype=tv or stype=movie, not a numeric stype: usePlayEngine.ts
puts stype straight into searchType and the client's typeMatch is
(searchType === 'tv' && episodes > 1) || (searchType === 'movie' && episodes === 1).
"""

import json
import pathlib
import sys
import urllib.parse

import asyncio
import httpx

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from _harness import BASE, load_env

API_MATCH = sys.argv[1] if len(sys.argv) > 1 else "bfzyapi"
IDS = sys.argv[2] if len(sys.argv) > 2 else "42484"
OUT = pathlib.Path("/tmp/opencode")


async def main():
    from moontv.storage.db import Db
    from moontv.config import get_config

    # get_admin_config() is None until the panel saves a blob; the app's own
    # source list comes from getConfig(), which merges the env defaults.
    cfg = await Db.get_admin_config() or await get_config()
    src = next(
        (s for s in (cfg.get("SourceConfig") or []) if API_MATCH in (s.get("api") or "")),
        None,
    )
    if not src:
        raise SystemExit(f"no SourceConfig api contains {API_MATCH!r}")
    key = src["key"]
    print("source key codepoints", [hex(ord(c)) for c in key])

    env = load_env()
    async with httpx.AsyncClient(trust_env=False, timeout=40, follow_redirects=True) as cx:
        r = await cx.get(src["api"], params={"ac": "detail", "ids": IDS})
        d = r.json()
        item = (d.get("data") or d.get("list") or [{}])[0]
        name = item.get("vod_name")
        print("title codepoints", [hex(ord(c)) for c in (name or "")])

        lr = await cx.post(
            BASE + "/api/login",
            json={"username": env.get("USERNAME", ""), "password": env.get("PASSWORD", "")},
        )
        sc = lr.headers.get("set-cookie", "")
        cookie = {"auth": sc[len("auth=") :].split(";")[0]} if sc.startswith("auth=") else {}

        rr = await cx.get(BASE + "/api/search", params={"q": name, "timeout": 30, "stream": 1}, cookies=cookie)
        print("search", rr.status_code, "bytes", len(rr.text))
        match = None
        for line in rr.text.splitlines():
            raw = line[5:].strip() if line.startswith("data:") else line.strip()
            if not raw.startswith("{"):
                continue
            try:
                dd = json.loads(raw)
            except Exception:
                continue
            for x in dd.get("pageResults") or []:
                if str(x.get("id")) == IDS:
                    match = x
        if not match:
            print(f"NO MATCH for {IDS} in the search stream")
            return

        eps = match.get("episodes") or []
        stype = "tv" if len(eps) > 1 else "movie"
        print("source", repr(match.get("source")), "eps", len(eps),
              "title", repr(match.get("title")), "year", repr(match.get("year")))
        q = urllib.parse.urlencode(
            {
                "source": key,
                "id": IDS,
                "title": match.get("title"),
                "stitle": match.get("title"),
                "stype": stype,
            }
        )
        OUT.mkdir(exist_ok=True)
        (OUT / "playurl.txt").write_text("/play?" + q)
        (OUT / "episode.txt").write_text(eps[0] if eps else "")
        print("PLAYURL /play?" + q)
        print("first episode written to", OUT / "episode.txt")


asyncio.run(main())
