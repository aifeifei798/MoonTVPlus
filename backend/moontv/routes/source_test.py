"""`/api/admin/source/test` — MoonTVPlus addition, not an upstream route.

Upstream had no way to find out that an API site went dead: a dead site is
simply missing from every search, and the only trace is one line in
`failedSources[]` for the one query that happened to ask for it. Sources
expire constantly (`?ac=videolist` media APIs are third-party and get
registered, sold off, and re-pointed), so the panel needs a health check that
asks the same question the search route already answers.

That is why the probe is *the search request itself* — `api +
API_CONFIG["search"]["path"] + encode_uri_component(query)`, the same URL, the
same Chrome UA, the same `fetchWithTimeout` (so the same `trust_env=False`)
`/api/search` makes. "Alive" here means "this source can answer a search",
which is what the user is asking about; a probe that only checked the
domain's DNS would call a source alive that answers 403 to every search.

There are two stages, because an empty-query probe cannot see a source that is
alive but refuses keyword search. Stage 1 asks `wd=` — the site's first page —
so a live site answers without depending on what its library happens to
contain; a JSON body without a `list` array is not alive for MoonTV's purposes
even when the browser can open it, so it is reported as `接口格式错误` the way
`searchFromApiStream` bails out. Stage 2 re-requests the same endpoint with a
title taken from stage 1's own first item, which is exactly the URL
`downstream.py:170` builds for search page 1. Two failures only show up on
stage 2: a site that answers a keyword with HTML (天涯影视 and 牛牛 answer
`暂不支持搜索` at 200, which the search route turns into a JSON parse error) and
a site behind a WAF that challenges keyword queries (极速 and 光速 answer 403
`Just a moment...`). Both are reachable and play fine from the first page but
are useless in the UI, so they are reported as `item_warning` — alive, not dead
— and are never selected for 批量禁用/批量删除. The keyword comes from the
source's own data rather than from a typed string, so no CJK crosses a command
line (AGENTS.md: this machine's I/O path transcodes it).

The framing is the SSE vocabulary `followings_refresh.py` already uses
(`start` / `item_result` / `item_failed` / `complete`) plus the new
`item_warning`, and the same `CONCURRENCY = 5` worker pool, so ten sources
finish in two rounds trips rather than ten, and the panel can paint each row as
soon as it answers.
"""

from __future__ import annotations

import asyncio

from fastapi import Request
from fastapi.responses import StreamingResponse

from ..auth import get_auth_info_from_cookie
from ..config import API_CONFIG, get_config
from ..fetch import fetch_with_timeout
from ..routes import json_response
from ..routes.admin import _localstorage_guard, _operator_role
from ..utils import encode_uri_component, js_number, js_truthy, json_dump, now_ms

CONCURRENCY = 5  # same pool size as /api/followings/refresh
DEFAULT_TIMEOUT_S = 10  # a search's 30s is too slow for a panel button
MAX_TIMEOUT_S = 60
FORMAT_ERROR = "接口格式错误"  # `searchFromApiStream`'s bail-out, as a reason
KEYWORD_ERROR = "关键词搜索失败"  # stage 2 failed: alive, but search does not work
PROBE_QUERY = ""  # `?ac=videolist&wd=` — the site's first page, not a title

_SENTINEL = object()


def _first_truthy(*values):
    """`a || b || c` — JS's `||` yields the value, not a bool."""
    for value in values[:-1]:
        if js_truthy(value):
            return value
    return values[-1]


def _first_title(first_page: list) -> str | None:
    """Stage 2's keyword: the first item's own `vod_name`, or None.

    `map_item_to_result` raises when `vod_name` is not a string, so a source
    whose names are not strings is not searchable in MoonTV either — None here
    means "nothing to search with", not "this source is dead".
    """
    for item in first_page:
        if isinstance(item, dict):
            name = item.get("vod_name")
            if isinstance(name, str) and js_truthy(name):
                return name
    return None


async def _probe(
    api: str, timeout_ms: int, query: str = PROBE_QUERY
) -> tuple[dict | None, dict]:
    """One request to a source's search endpoint, with or without a keyword.

    Returns `(result, failure)`; exactly one of the two is set, and both carry
    the elapsed milliseconds so the panel can show a slow source as slow
    instead of silently green. `title` is only filled on the empty-query stage,
    so stage 2 can search for a title the site itself returned.
    """
    url = api + API_CONFIG["search"]["path"] + encode_uri_component(query)
    started = now_ms()
    try:
        response = await fetch_with_timeout(
            url, API_CONFIG["search"]["headers"], timeout_ms
        )
    except Exception as error:  # noqa: BLE001 — fetchWithTimeout's three strings
        return None, {
            "ms": now_ms() - started,
            "reason": _first_truthy(str(error), "请求失败"),
        }

    if response.status_code >= 400:  # `!response.ok`
        return None, {
            "ms": now_ms() - started,
            "reason": f"HTTP {response.status_code}",
        }

    try:
        data = response.json()
    except Exception:  # noqa: BLE001 — `response.json()` on a non-JSON body
        return None, {"ms": now_ms() - started, "reason": FORMAT_ERROR}

    if not isinstance(data, dict):
        return None, {"ms": now_ms() - started, "reason": FORMAT_ERROR}

    first_page = data.get("list")
    if not isinstance(first_page, list):
        return None, {"ms": now_ms() - started, "reason": FORMAT_ERROR}

    pagecount = js_number(data.get("pagecount"))
    return {
        "ms": now_ms() - started,
        "count": len(first_page),
        "pagecount": int(pagecount) if pagecount and pagecount > 0 else 0,
        # Only the first-page stage hands a keyword forward; stage 2's own list
        # is the answer to the question, not the source of another one.
        "title": _first_title(first_page) if not query else None,
    }, None


async def source_test(request: Request):
    gated = _localstorage_guard()
    if gated:
        return json_response(gated, 400)

    try:
        auth_info = get_auth_info_from_cookie(request)
        if not auth_info or not auth_info.get("username"):
            return json_response({"error": "Unauthorized"}, 401)
        username = auth_info["username"]

        admin_config_dict = await get_config()
        if _operator_role(admin_config_dict, username) is None:
            return json_response({"error": "权限不足"}, 401)

        sources = admin_config_dict.get("SourceConfig") or []

        try:
            body = await request.json()
        except Exception:  # 无 body 时忽略，测试全部源
            body = {}
        if body is None:
            body = {}
        raw_keys = body.get("keys") if isinstance(body, dict) else None

        timeout_s = js_number(body.get("timeout")) if isinstance(body, dict) else None
        timeout_ms = (
            int(timeout_s * 1000)
            if timeout_s and timeout_s > 0 and timeout_s <= MAX_TIMEOUT_S
            else DEFAULT_TIMEOUT_S * 1000
        )

        if isinstance(raw_keys, list) and len(raw_keys) > 0:
            wanted = set(raw_keys)
            targets = [s for s in sources if s.get("key") in wanted]
        else:
            targets = list(sources)
    except Exception as error:  # noqa: BLE001
        print("视频源测试准备失败:", error)
        return json_response({"error": "Internal Server Error"}, 500)

    if len(targets) == 0:
        return json_response(
            {"ok": True, "total": 0}, headers={"Cache-Control": "no-store"}
        )

    return StreamingResponse(
        _stream(targets, timeout_ms),
        headers={
            # Upstream's SSE routes set the content type in the header map, so
            # Starlette's `text/*` → `text/*; charset=utf-8` rewrite is skipped
            # here too; the panel's reader does not care, the wire does.
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _stream(targets: list[dict], timeout_ms: int):
    """The `ReadableStream`'s `start()` — one SSE event per finished source."""
    out: asyncio.Queue = asyncio.Queue()
    closed = False
    ok_count = 0
    failed_count = 0
    warning_count = 0
    cursor = 0

    def emit(payload) -> bool:
        """`send(payload)` — false once the stream is closed."""
        nonlocal closed
        if closed:
            return False
        out.put_nowait(f"data: {json_dump(payload)}\n\n".encode())
        return True

    async def process_source(entry: dict):
        nonlocal ok_count, failed_count, warning_count, closed
        if closed:
            return

        key = _first_truthy(entry.get("key"), "")
        name = _first_truthy(entry.get("name"), key)
        api = entry.get("api")
        if not api:
            failed_count += 1
            if not emit(
                {
                    "type": "item_failed",
                    "key": key,
                    "name": name,
                    "ms": 0,
                    "reason": "缺少 API 地址",
                }
            ):
                closed = True
            return

        first, failure = await _probe(api, timeout_ms)
        if closed:
            return

        if first is None:
            failed_count += 1
            if not emit(
                {
                    "type": "item_failed",
                    "key": key,
                    "name": name,
                    "ms": failure["ms"],
                    "reason": failure["reason"],
                }
            ):
                closed = True
            return

        keyword = first.get("title")
        if keyword is None:
            # Nothing to search with: an empty first page is a zero-result
            # warning (PLAN.md change 18), and inventing a keyword would probe
            # the site's library instead of the site.
            ok_count += 1
            if not emit(
                {
                    "type": "item_result",
                    "key": key,
                    "name": name,
                    "ms": first["ms"],
                    "count": first["count"],
                    "pagecount": first["pagecount"],
                }
            ):
                closed = True
            return

        searched, keyword_failure = await _probe(api, timeout_ms, keyword)
        if closed:
            return

        if keyword_failure is not None:
            # Alive, but a real keyword does not work: HTML instead of JSON, a
            # WAF challenge, or an error status. Not a death — the badge is
            # yellow and the row is never auto-selected for 批量禁用.
            warning_count += 1
            if not emit(
                {
                    "type": "item_warning",
                    "key": key,
                    "name": name,
                    "ms": first["ms"] + keyword_failure["ms"],
                    "count": first["count"],
                    "pagecount": first["pagecount"],
                    "query": keyword,
                    "reason": f"{KEYWORD_ERROR}: {keyword_failure['reason']}",
                }
            ):
                closed = True
            return

        ok_count += 1
        if not emit(
            {
                "type": "item_result",
                "key": key,
                "name": name,
                "ms": first["ms"] + searched["ms"],
                "count": searched["count"],
                "pagecount": searched["pagecount"],
                "first_page_count": first["count"],
                "query": keyword,
            }
        ):
            closed = True

    async def _worker():
        nonlocal cursor
        while not closed:
            idx = cursor
            cursor += 1
            if idx >= len(targets):
                break
            await process_source(targets[idx])

    async def run_workers():
        nonlocal ok_count, failed_count, warning_count
        try:
            await asyncio.gather(
                *(_worker() for _ in range(min(CONCURRENCY, len(targets))))
            )
            emit(
                {
                    "type": "complete",
                    "okCount": ok_count,
                    "failedCount": failed_count,
                    "warningCount": warning_count,
                    "total": len(targets),
                    "checkedAt": now_ms(),
                }
            )
        except Exception as error:  # noqa: BLE001
            print("视频源测试流处理异常:", error)
        finally:
            out.put_nowait(_SENTINEL)

    if not emit({"type": "start", "total": len(targets)}):
        return

    driver = asyncio.ensure_future(run_workers())
    try:
        while True:
            chunk = await out.get()
            if chunk is _SENTINEL:
                break
            yield chunk
    finally:
        if not driver.done():
            driver.cancel()


ROUTES = [
    ("/api/admin/source/test", source_test, ["POST"]),
]
