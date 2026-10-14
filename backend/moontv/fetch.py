"""The upstream-facing HTTP layer — `downstream.ts`'s `fetchWithTimeout`.

MoonTV calls the API sites with the browser `fetch`, so the port has to agree
with `fetch`, not with `requests`: redirects are followed, and the three error
strings are part of the wire surface — the search routes map them into
`failedSources[].error`, which the client shows, so `请求超时` / `请求失败` /
`网络错误: …` have to be raised for the same failures they were raised for
originally.

`douban.ts` and `m3u8-downloader.ts` each wrote their own fetch wrapper with
different error strings (`HTTP error! Status: 404`); those stay with the
modules that use them and reuse only the shared client here.
"""

from __future__ import annotations

import os

import httpx

DEFAULT_TIMEOUT_MS = 30000  # `timeout = 30000` default parameter

# Upper bound on how many upstream requests may be in flight at once. A search
# fans out over every configured site and, per site, over up to
# `SearchDownstreamMaxPage` pages, so one search is already ~50 requests and ten
# concurrent searches are 500 — enough to get the app rate-limited or blocked by
# the media servers. The pool blocks when it is exhausted, so this is a real
# gate rather than a hint.
UPSTREAM_MAX_CONNECTIONS = int(os.environ.get("MOONTVPLUS_UPSTREAM_MAX_CONNECTIONS", "64"))

# The Chrome UA MoonTV sent in `API_CONFIG.search.headers`/`detail.headers` is
# spelled out in `config.py`; nothing is added here, so the upstream sites see
# the same request they saw before.


class FetchTimeout(Exception):
    """`throw new Error('请求超时')`."""

    def __init__(self, message: str = "请求超时"):
        super().__init__(message)


class FetchFailed(Exception):
    """`throw new Error('请求失败')` — undici's "fetch failed"/"NetworkError"."""

    def __init__(self, message: str = "请求失败"):
        super().__init__(message)


class FetchNetworkError(Exception):
    r"""`throw new Error(`网络错误: ${err.message || '未知错误'}`)`."""

    def __init__(self, message: str = "网络错误: 未知错误"):
        super().__init__(message)


_client: httpx.AsyncClient | None = None


def get_http_client() -> httpx.AsyncClient:
    """One shared client per process, so upstream sites reuse connections.

    `trust_env=False` is deliberate: browser `fetch` ignores `HTTP_PROXY`/
    `NO_PROXY`, and so must this — otherwise a deployment that exports a proxy
    for the container (the common Docker recipe) would send upstream requests
    somewhere the old app never sent them, and a `socks://` value would fail
    outright.
    """
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            follow_redirects=True,
            trust_env=False,
            limits=httpx.Limits(
                max_connections=UPSTREAM_MAX_CONNECTIONS,
                max_keepalive_connections=min(20, UPSTREAM_MAX_CONNECTIONS),
            ),
            timeout=httpx.Timeout(10.0),
        )
    return _client


async def fetch_with_timeout(url: str, headers: dict | None = None, timeout_ms=None):
    """`fetchWithTimeout(url, options, timeout)` → an `httpx.Response`.

    `timeout_ms` is milliseconds, as the callers pass it; `None` is the 30000
    default. A NaN timeout reproduces `setTimeout(abort, NaN)`, which is 0 ms
    and therefore aborts immediately, so it raises `FetchTimeout` without
    contacting the site — the same `请求超时` the old route reported.
    """
    if timeout_ms is None:
        timeout_ms = DEFAULT_TIMEOUT_MS
    if isinstance(timeout_ms, float) and timeout_ms != timeout_ms:  # NaN
        raise FetchTimeout()
    if timeout_ms is False or timeout_ms == 0:
        # `setTimeout(abort, 0)` also aborts before the request starts.
        raise FetchTimeout()

    client = get_http_client()
    try:
        client = get_http_client()
        return await client.request(
            "GET", url, headers=headers, timeout=timeout_ms / 1000
        )
    except httpx.ConnectTimeout:
        # A failed connect is "fetch failed" in undici, not an abort.
        raise FetchFailed() from None
    except httpx.TimeoutException:
        raise FetchTimeout() from None
    except httpx.ConnectError:
        raise FetchFailed() from None
    except httpx.RequestError:
        raise FetchFailed() from None
    except Exception as error:  # noqa: BLE001 — the TS catch-all
        raise FetchNetworkError(f"网络错误: {error or '未知错误'}") from None


async def fetch_text(url: str, headers: dict | None = None, timeout_ms=None) -> str:
    """`response.text` after `fetchWithTimeout`."""
    response = await fetch_with_timeout(url, headers, timeout_ms)
    return response.text


async def fetch_json(url: str, headers: dict | None = None, timeout_ms=None):
    """`response.json` after `fetchWithTimeout`.

    A body that is not JSON raises `ValueError` with Python's message rather
    than V8's; the search routes only surface it in `failedSources[].error`, so
    the shape of the response is unchanged and only the sentence differs.
    """
    response = await fetch_with_timeout(url, headers, timeout_ms)
    return response.json()
