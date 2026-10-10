"""`src/app/api/search/{one,resources,suggestions,ws}/route.ts`.

Four neighbours of `/api/search`, kept in one module because they share its
vocabulary (`SourceConfig`, `getAvailableApiSites`, the yellow filter) even
though upstream had one file each:

  `/api/search/one`         OrionTV's single-source lookup: exact title match
                            against one `resourceId`, and the "no result" case
                            is a **404** with `{error, result: null}` rather
                            than an empty list.
  `/api/search/resources`   OrionTV's source list. No auth check of its own —
                            the cookie only narrows which sources appear.
  `/api/search/suggestions` NDJSON of `{suggestions: [...]}`, one line per
                            result page, scored the way the client-side
                            suggestion list did.
  `/api/search/ws`          SSE (`data: {...}` + blank line), despite the
                            directory being called `ws`. One `start`, then one
                            `source_result` or `source_error` per source, then
                            `complete` once every source has answered.

Three shapes are easy to "fix" and must not be, because the clients read them:

  * `result: null` in `/api/search/one` is an explicit JS `null`, not an
    absent key, so it serializes as `null` (`strip_none` is not applied).
  * `ws`'s `totalResults` is always 0. Upstream declared `allResults` twice —
    the per-site one at line 91 shadows the one the `complete` event reads at
    line 155, so nothing is ever appended to the outer array.
  * `ws`'s 20 s `Promise.race` never fires. `searchFromApiStream` is an
    `async function*`, so calling it hands back a generator synchronously and
    the race resolves before any request is made; the only timeout that applies
    is `fetchWithTimeout`'s 30 s. The `${name} timeout` error string is
    therefore unreachable, and there is no per-source cap here either.
"""

from __future__ import annotations

import asyncio
import math
import re
from functools import cmp_to_key

from fastapi import Request
from fastapi.responses import StreamingResponse

from ..auth import get_auth_info_from_cookie
from ..config import get_available_api_sites, get_cache_time, get_config
from ..downstream import search_from_api_stream
from ..env import STORAGE_TYPE, env
from ..routes import json_response
from ..utils import json_dump, js_parse_int, js_string_of, js_truthy, now_ms
from ..yellow import yellow_words

# `title.split(/[ -:：·、-]/)` — both `-`s in that class are literal (leading
# and trailing position), so the separators are space, hyphen and the three
# colon/middle-dot variants CJK titles use.
SPLIT_KEYWORD = re.compile(r"[ \-:：·、]")

_SENTINEL = object()


def _cache_headers(cache_time) -> dict:
    """The four CDN-flavoured headers the OrionTV-compat routes all set."""
    return {
        "Cache-Control": f"public, max-age={cache_time}, s-maxage={cache_time}",
        "CDN-Cache-Control": f"public, s-maxage={cache_time}",
        "Vercel-CDN-Cache-Control": f"public, s-maxage={cache_time}",
        "Netlify-Vary": "query",
    }


def _timeout_ms(request: Request):
    """`timeoutParam ? parseInt(timeoutParam,10)*1000 : undefined`."""
    raw = request.query_params.get("timeout")
    if not raw:
        return None
    parsed = js_parse_int(raw)
    # Garbage is NaN, which `fetch_with_timeout` turns into an immediate
    # `请求超时` — the same thing `setTimeout(abort, NaN)` did upstream.
    return float("nan") if parsed is None else parsed * 1000


def _yellow_filter(results) -> list:
    """`results.filter(r => !yellowWords.some(w => (r.type_name||'').includes(w)))`."""
    return [
        r
        for r in results
        if not any(word in (r.get("type_name") or "") for word in yellow_words)
    ]


# ---------------------------------------------------------------------------
# /api/search/one
# ---------------------------------------------------------------------------


async def search_one(request: Request):
    """OrionTV 兼容接口（JSON 非流式）."""
    # localstorage mode is unreachable with the ported backends; kept so the
    # shape of the guard stays the same as upstream's.
    is_local_storage = env(STORAGE_TYPE, "sqlite") == "localstorage"

    auth_info = None
    if not is_local_storage:
        auth_info = get_auth_info_from_cookie(request)
        if not auth_info or not auth_info.get("username"):
            return json_response({"error": "Unauthorized"}, 401)

    query = request.query_params.get("q")
    resource_id = request.query_params.get("resourceId")
    timeout_ms = _timeout_ms(request)

    if not query or not resource_id:
        cache_time = await get_cache_time()
        return json_response(
            {"result": None, "error": "缺少必要参数: q 或 resourceId"},
            headers=_cache_headers(cache_time),
        )

    config = await get_config()
    api_sites = await get_available_api_sites((auth_info or {}).get("username"))

    try:
        target_site = next((s for s in api_sites if s.get("key") == resource_id), None)
        if not target_site:
            return json_response(
                {"error": f"未找到指定的视频源: {resource_id}", "result": None},
                404,
            )

        # 聚合搜索（使用流式实现做非流式聚合）
        all_results: list = []
        async for batch in search_from_api_stream(target_site, query, True, timeout_ms):
            all_results.extend(batch)

        # OrionTV 行为：按标题完全匹配过滤
        result = [r for r in all_results if r.get("title") == query]
        if not (config.get("SiteConfig") or {}).get("DisableYellowFilter"):
            result = _yellow_filter(result)

        cache_time = await get_cache_time()

        if len(result) == 0:
            return json_response({"error": "未找到结果", "result": None}, 404)

        return json_response({"results": result}, headers=_cache_headers(cache_time))
    except Exception as error:  # noqa: BLE001
        print(f"搜索失败: {error}")
        return json_response({"error": "搜索失败", "result": None}, 500)


# ---------------------------------------------------------------------------
# /api/search/resources
# ---------------------------------------------------------------------------


async def search_resources(request: Request):
    """OrionTV 兼容接口 — the source list, narrowed by the cookie if there is one."""
    try:
        auth_info = get_auth_info_from_cookie(request)
        username = (auth_info or {}).get("username")
        api_sites = await get_available_api_sites(username)
        cache_time = await get_cache_time()

        return json_response(api_sites, headers=_cache_headers(cache_time))
    except Exception as error:  # noqa: BLE001
        print("获取资源失败:", error)
        return json_response({"error": "获取资源失败"}, 500)


# ---------------------------------------------------------------------------
# /api/search/suggestions
# ---------------------------------------------------------------------------


def _suggest_page(results: list, query: str) -> list:
    """One page's `generateSuggestionsStream` body: count, score, sort, keep `text`."""
    query_lower = query.lower()
    keyword_frequency: dict[str, int] = {}

    all_keywords = [
        word
        for title in (r.get("title") for r in results)
        if js_truthy(title)
        for word in SPLIT_KEYWORD.split(js_string_of(title))
        if len(word) > 1 and query_lower in word.lower()
    ]

    for word in all_keywords:
        lower = word.lower()
        keyword_frequency[lower] = keyword_frequency.get(lower, 0) + 1

    real_keywords = list(dict.fromkeys(all_keywords))[:8]
    query_words = SPLIT_KEYWORD.split(query_lower)

    scored = []
    for word in real_keywords:
        word_lower = word.lower()
        score = 1.0
        if word_lower == query_lower:
            score = 3.0  # 完全匹配 - 最高优先级
        elif word_lower.startswith(query_lower):
            score = 2.5  # 开头匹配 - 高优先级
        elif word_lower.endswith(query_lower):
            score = 2.0  # 结尾匹配 - 中高优先级
        elif any(word_lower.startswith(qw) for qw in query_words):
            score = 1.8  # 包含查询词开头
        elif any(qw in word_lower for qw in query_words):
            score = 1.5  # 包含查询词

        # 长度相似度加分（长度接近查询的更相关）
        length_diff = abs(len(word_lower) - len(query_lower))
        score += (1 / (1 + length_diff * 0.1)) * 0.3

        # 频率加分（出现次数多的更相关，但使用对数避免过度影响）
        frequency = keyword_frequency.get(word_lower, 1)
        score += math.log(frequency + 1) * 0.2

        # 长度惩罚（过长的关键词稍微降权）
        if len(word_lower) > len(query_lower) * 2:
            score -= 0.2

        scored.append({"text": word, "score": score, "frequency": frequency})

    def compare(a: dict, b: dict) -> int:
        """`sort((a, b) => …)`: score, then frequency, then shorter text."""
        if abs(a["score"] - b["score"]) > 0.01:
            return -1 if b["score"] - a["score"] < 0 else 1
        if a["frequency"] != b["frequency"]:
            return -1 if b["frequency"] - a["frequency"] < 0 else 1
        length_diff = len(a["text"]) - len(b["text"])  # 频率相同时，按长度排序（较短的优先）
        return -1 if length_diff < 0 else 1 if length_diff > 0 else 0

    ranked = sorted(scored, key=cmp_to_key(compare))
    return [{"text": s["text"]} for s in ranked]  # 只保留 text 字段


async def search_suggestions(request: Request):
    """NDJSON: one `{"suggestions":[…]}` line per result page."""
    try:
        raw_query = request.query_params.get("q")
        query = raw_query.strip() if raw_query is not None else None

        if not query:
            return json_response({"suggestions": []})

        timeout_ms = _timeout_ms(request)
        cache_time = await get_cache_time()
    except Exception as error:  # noqa: BLE001
        print("获取搜索建议失败:", error)
        return json_response({"error": "获取搜索建议失败"}, 500)

    async def generate():
        # Upstream's `start()` runs after the Response was already returned, so
        # a failure here truncates the body rather than turning it into a 500 —
        # the same thing Starlette does when a generator raises mid-stream.
        config = await get_config()
        api_sites = [s for s in (config.get("SourceConfig") or []) if not s.get("disabled")]

        if not api_sites:
            return

        # 使用第一个可用的数据源进行流式搜索
        site = api_sites[0]
        async for results in search_from_api_stream(site, query, True, timeout_ms):
            yield json_dump({"suggestions": _suggest_page(results, query)}).encode() + b"\n"

    return StreamingResponse(
        generate(),
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Cache-Control": f"private, max-age={cache_time}",
        },
    )


# ---------------------------------------------------------------------------
# /api/search/ws
# ---------------------------------------------------------------------------


async def search_ws(request: Request):
    """SSE search: one event per source as it finishes, then `complete`."""
    auth_info = get_auth_info_from_cookie(request)
    if not auth_info or not auth_info.get("username"):
        return json_response({"error": "Unauthorized"}, 401)

    query = request.query_params.get("q")
    if not query:
        return json_response({"error": "搜索关键词不能为空"}, 400)

    config = await get_config()
    api_sites = await get_available_api_sites(auth_info["username"])

    return StreamingResponse(
        _stream(api_sites, query, config),
        headers={
            # Upstream set the content type in the header map, so Starlette's
            # `text/*` → `text/*; charset=utf-8` rewrite has to be skipped.
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET",
            "Access-Control-Allow-Headers": "Content-Type",
        },
    )


async def _stream(api_sites: list, query: str, config: dict):
    """The `ReadableStream`'s `start()` — one SSE event per yielded chunk.

    Every source starts at once (upstream's `apiSites.map(async …)`), so the
    events land in completion order. As in `followings_refresh`, a client that
    hangs up mid-stream is not detected: the remaining sources keep fetching and
    their events are simply dropped.
    """
    out: asyncio.Queue = asyncio.Queue()
    closed = False
    completed_sources = 0
    all_results: list = []  # upstream's `complete` reads THIS one; see the shadowing note

    def emit(payload) -> bool:
        """`safeEnqueue(...)` — false once the stream is closed."""
        nonlocal closed
        if closed:
            return False
        out.put_nowait(f"data: {json_dump(payload)}\n\n".encode())
        return True

    async def collect(site: dict) -> list:
        """`for await (const batch of searchFromApiStream(site, query))`.

        No 20 s cap: upstream's `Promise.race` resolved against the generator
        object itself, so the timeout it installed was dead code.
        """
        collected: list = []
        async for batch in search_from_api_stream(site, query, True, None):
            collected.extend(batch)
        return collected

    async def run_site(site: dict) -> None:
        nonlocal completed_sources, closed
        try:
            results = await collect(site)
        except Exception as error:  # noqa: BLE001
            print(f"搜索失败 {site.get('name')}:", error)
            completed_sources += 1
            if not emit(
                {
                    "type": "source_error",
                    "source": site.get("key"),
                    "sourceName": site.get("name"),
                    "error": str(error) or "搜索失败",
                    "timestamp": now_ms(),
                }
            ):
                closed = True
                return
        else:
            filtered_results = results
            if not (config.get("SiteConfig") or {}).get("DisableYellowFilter"):
                filtered_results = _yellow_filter(results)

            completed_sources += 1
            if not emit(
                {
                    "type": "source_result",
                    "source": site.get("key"),
                    "sourceName": site.get("name"),
                    "results": filtered_results,
                    "timestamp": now_ms(),
                }
            ):
                closed = True
                return

            if len(filtered_results) > 0:
                # Upstream appended to the shadowed per-site array here, which is
                # why the `complete` event below still reports 0.
                pass

        if completed_sources == len(api_sites) and not emit(
            {
                "type": "complete",
                "totalResults": len(all_results),
                "completedSources": completed_sources,
                "timestamp": now_ms(),
            }
        ):
            closed = True

    async def run_workers():
        try:
            await asyncio.gather(*(run_site(site) for site in api_sites))
        except Exception as error:  # noqa: BLE001
            print("搜索流处理异常:", error)
        finally:
            out.put_nowait(_SENTINEL)

    if not emit(
        {"type": "start", "query": query, "totalSources": len(api_sites), "timestamp": now_ms()}
    ):
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
    ("/api/search/one", search_one, ["GET"]),
    ("/api/search/resources", search_resources, ["GET"]),
    ("/api/search/suggestions", search_suggestions, ["GET"]),
    ("/api/search/ws", search_ws, ["GET"]),
]
