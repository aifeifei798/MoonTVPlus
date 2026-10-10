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
single-shot fetch is an empty page): every fetch races the official host and
the two `cmliussss` mirrors the frontend already uses for its non-`direct`
modes (`m.douban.cmliussss.net` / `.com`, same for `movie.`) concurrently,
first 2xx wins, each with a 4s timeout. The error raised is the last
candidate's, unchanged in shape; the tried hosts go to the server log,
not the response body.
"""

from __future__ import annotations

import asyncio
from urllib.parse import urlsplit, urlunsplit

from .fetch import FetchFailed, FetchNetworkError, FetchTimeout, fetch_with_timeout

TIMEOUT_MS = 4000

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


async def _race_candidates(candidates: list[str], headers: dict):
    """官源与镜像并发竞速：首个 2xx/3xx 胜出，全败则抛最后一个错误。

    原串行 fallback（带 0.3s 间隔）在官源 hang 住时把延迟拉满；并发后慢者
    被 cancel，快者决定延迟。同一批 `done` 里既有成功也有失败时，按候选顺序
    （官源优先）取成功者——否则官源与镜像同一时刻返回时，可能把失败者当成
    竞速结果。错误形状不变（最后一个候选的错误）。
    """
    if len(candidates) == 1:
        return await _fetch_one(candidates[0], headers)

    # 每个候选一个 task，`order` 记住它的位次：官源赢平局。
    order = {
        asyncio.ensure_future(_fetch_one(url, headers)): index
        for index, url in enumerate(candidates)
    }
    pending = set(order)
    last_error: Exception | None = None
    try:
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            winner = None
            for task in sorted(done, key=lambda item: order[item]):
                try:
                    result = task.result()
                except Exception as error:  # noqa: BLE001 — 该候选失败，记下继续
                    last_error = error
                    continue
                if winner is None:
                    winner = result
            if winner is not None:
                return winner
    finally:
        for task in pending:
            task.cancel()
        if pending:
            # 等 cancel 真正送达，别把"Task was destroyed pending"留在循环里。
            await asyncio.gather(*pending, return_exceptions=True)
    raise last_error if last_error is not None else FetchFailed()


async def fetch_douban_data(url: str):
    """`fetchDoubanData(url)` — the parsed JSON body, via the mirror race."""
    candidates = _candidate_urls(url)
    try:
        return (await _race_candidates(candidates, DOUBAN_HEADERS)).json()
    except Exception as error:  # noqa: BLE001
        print(f"douban fetch failed ({len(candidates)} hosts):", error)
        raise


async def fetch_douban_html(url: str) -> str:
    """`handleTop250`'s fetch — the page's HTML, same 4s timeout, same race."""
    candidates = _candidate_urls(url)
    try:
        return (await _race_candidates(candidates, DOUBAN_HTML_HEADERS)).text
    except Exception as error:  # noqa: BLE001
        print(f"douban html fetch failed ({len(candidates)} hosts):", error)
        raise


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
