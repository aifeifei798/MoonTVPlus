"""Port of `src/lib/douban.ts` — the server-side douban fetcher.

`fetchDoubanData` is that file's only export, and it is a separate wrapper from
`fetch.py`'s `fetchWithTimeout` for two reasons that both matter on the wire:
it names its own failure (`HTTP error! Status: 404`, which the three douban
routes copy verbatim into `details`), and it sends the browser headers douban
inspects (`Referer`/`Origin`) instead of the API-site ones.

The abort and the network failure keep `fetch.py`'s sentences (`请求超时` /
`请求失败`) rather than undici's (`This operation was aborted` / `fetch failed`);
they only ever reach `details` of a 500, which is the documented divergence in
`PLAN.md`.

Resilience (douban rate-limits datacenter IPs with 403/418/timeouts, so a
single-shot fetch is an empty page): every fetch tries the official host first,
then the two `cmliussss` mirrors the frontend already uses for its non-`direct`
modes (`m.douban.cmliussss.net` / `.com`, same for `movie.`). One attempt per
candidate, ~0.3s between candidates, first 2xx wins. The error raised is the
last candidate's, unchanged in shape; the tried hosts go to the server log,
not the response body.
"""

from __future__ import annotations

import asyncio
from urllib.parse import urlsplit, urlunsplit

from .fetch import FetchFailed, FetchNetworkError, FetchTimeout, fetch_with_timeout

TIMEOUT_MS = 10000  # `setTimeout(() => controller.abort(), 10000)`

# The exact header set `fetchDoubanData` passed to `fetch`.
DOUBAN_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
    ),
    "Referer": "https://movie.douban.com/",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://movie.douban.com",
}

# `handleTop250` scrapes the HTML page and sends the browser's navigation
# headers rather than the JSON ones, so it needs its own set.
DOUBAN_HTML_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
    ),
    "Referer": "https://movie.douban.com/",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
    "image/webp,*/*;q=0.8",
}

# Official host → mirrors, in try order. These are the same two hosts the
# frontend's `douban.client.ts` uses for its `cmliussss-cdn-*` modes, so the
# server fallback and the client fallback agree on where to go.
_MIRROR_HOSTS = ("m.douban.cmliussss.net", "m.douban.cmliussss.com")
_MOVIE_MIRROR_HOSTS = ("movie.douban.cmliussss.net", "movie.douban.cmliussss.com")


def _candidate_urls(url: str) -> list[str]:
    """`[official, *mirrors]` — host swap only, path/query untouched."""
    try:
        parts = urlsplit(url)
    except Exception:  # noqa: BLE001 — unparseable means no mirrors
        return [url]
    host = parts.hostname or ""
    if host == "m.douban.com":
        mirrors = _MIRROR_HOSTS
    elif host == "movie.douban.com":
        mirrors = _MOVIE_MIRROR_HOSTS
    else:
        return [url]
    urls = [url]
    for mirror in mirrors:
        urls.append(urlunsplit((parts.scheme, mirror, parts.path, parts.query, parts.fragment)))
    return urls


class DoubanHttpError(Exception):
    """`throw new Error(`HTTP error! Status: ${response.status}`)`."""

    def __init__(self, status: int):
        super().__init__(f"HTTP error! Status: {status}")


async def _fetch_one(url: str, headers: dict):
    response = await fetch_with_timeout(url, headers=headers, timeout_ms=TIMEOUT_MS)
    # `response.ok` is 200..399 in `fetch`, not httpx's 2xx-only `is_success`.
    if not 200 <= response.status_code < 400:
        raise DoubanHttpError(response.status_code)
    return response


async def fetch_douban_data(url: str):
    """`fetchDoubanData(url)` — the parsed JSON body, via mirror fallback."""
    last_error: Exception | None = None
    candidates = _candidate_urls(url)
    for index, candidate in enumerate(candidates):
        if index:
            await asyncio.sleep(0.3)  # don't hammer all three at once
        try:
            return (await _fetch_one(candidate, DOUBAN_HEADERS)).json()
        except Exception as error:  # noqa: BLE001 — any failure tries the next mirror
            last_error = error
    print(f"douban fetch failed ({len(candidates)} hosts):", last_error)
    raise last_error if last_error is not None else FetchFailed()


async def fetch_douban_html(url: str) -> str:
    """`handleTop250`'s fetch — the page's HTML, same 10s abort, same fallback."""
    last_error: Exception | None = None
    for index, candidate in enumerate(_candidate_urls(url)):
        if index:
            await asyncio.sleep(0.3)
        try:
            return (await _fetch_one(candidate, DOUBAN_HTML_HEADERS)).text
        except Exception as error:  # noqa: BLE001
            last_error = error
    print("douban html fetch failed:", last_error)
    raise last_error if last_error is not None else FetchFailed()


async def fetch_subject_collection(collection_id: str, start: int, count: int):
    """rexxar `subject_collection/<id>/items` — the JSON Top250.

    This is what the frontend's `getDoubanCollection` already reads from the
    browser; the server uses the same endpoint (with mirror fallback) as the
    primary Top250 source so the HTML scrape is only a fallback.
    """
    official = (
        f"https://m.douban.com/rexxar/api/v2/subject_collection/{collection_id}/items"
        f"?start={start}&count={count}"
    )
    return await fetch_douban_data(official)
