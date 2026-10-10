"""`src/app/api/search/route.ts` — the aggregation endpoint.

Two response modes share one body of work, and the difference is only how the
results are framed:

  non-stream (`?stream` absent or `0`) → one JSON object
      `{"results":[…],"failedSources":[…]}`
  stream (`?stream=1`) → NDJSON, one `JSON.stringify(x)+"\\n"` per event:
      `{"site":key,"pageResults":[…]}` per page, `{"failedSources":[…]}`
      written every time a failure is appended (the same cumulative array, so
      it repeats), then a final `{"failedSources":[…]}` and
      `{"aggregatedResults":[…]}`.

The client lib picks a mode by the `stream` parameter, so both framings have to
stay exactly as they were, including the repeated `failedSources` events and
the order `aggregatedResults` is appended in (site completion order, not the
order `sources` listed them).

`Cache-Control` is `private, max-age=<SiteConfig.SiteInterfaceCacheTime>` on a
search that found something and `no-store, no-cache, must-revalidate` on one
that did not, which is what keeps the browser from re-searching every page turn.
"""

from __future__ import annotations

import asyncio

from fastapi import Request, Response
from fastapi.responses import StreamingResponse

from ..auth import get_auth_info_from_cookie
from ..config import get_available_api_sites, get_cache_time, get_config
from ..downstream import search_from_api_stream
from ..env import STORAGE_TYPE, env
from ..utils import json_dump, js_parse_int
from ..yellow import yellow_words

NO_STORE = {
    "Cache-Control": "no-store, no-cache, must-revalidate",
    "Pragma": "no-cache",
    "Expires": "0",
}


def _error_message(err) -> str:
    """`err.message || '未知的错误'`, then the three-way simplification."""
    message = str(err) or "未知的错误"
    if message == "请求超时":
        return "请求超时"
    if message == "请求失败":
        return "请求失败"
    if "网络错误" in message:
        return "网络错误"
    return message


def _yellow_filter(results) -> list:
    """`pageResults.filter(result => !yellowWords.some(word => typeName.includes(word)))`."""
    return [r for r in results if not any(word in (r.get("type_name") or "") for word in yellow_words)]


def _failure(site, error: str) -> dict:
    return {"name": site.get("name"), "key": site.get("key"), "error": error}


async def _search_one_site(site: dict, query: str, disable_yellow: bool, timeout_ms):
    """One site's whole run: `(results, failed_or_None)`.

    `hasResults` is set from the *unfiltered* page, which is what makes the
    `结果被过滤` error distinguish "the site answered with nothing we keep" from
    "the site answered with nothing at all".
    """
    site_results: list = []
    has_results = False

    try:
        async for page_results in search_from_api_stream(site, query, True, timeout_ms):
            filtered_results = page_results
            if len(filtered_results) != 0:
                has_results = True
            if not disable_yellow:
                filtered_results = _yellow_filter(page_results)
            if has_results and len(filtered_results) == 0:
                raise Exception("结果被过滤")
            site_results.extend(filtered_results)

        if not has_results:
            raise Exception("无搜索结果")
        return site_results, None
    except Exception as err:  # noqa: BLE001 — the TS catch-all
        return [], _failure(site, _error_message(err))


async def GET(request: Request):  # noqa: F402 — mirrors the TS export name
    storage_type = env(STORAGE_TYPE, "sqlite")
    is_local_storage = storage_type == "localstorage"  # not reachable; kept for parity

    auth_info = None
    if not is_local_storage:
        auth_info = get_auth_info_from_cookie(request)
        if not auth_info or not auth_info.get("username"):
            return Response(
                content=json_dump({"error": "Unauthorized"}),
                status_code=401,
                headers={"Content-Type": "application/json"},
            )

    query = request.query_params.get("q")
    stream_param = request.query_params.get("stream")
    enable_stream = stream_param != "0" if stream_param else False

    timeout_param = request.query_params.get("timeout")
    timeout_ms = None
    if timeout_param:
        parsed = js_parse_int(timeout_param)
        # `parseInt(x,10) * 1000`; a garbage value is NaN, and a NaN timeout
        # aborts immediately in the old code, which `fetch_with_timeout` maps
        # back to `请求超时`.
        timeout_ms = float("nan") if parsed is None else parsed * 1000

    config = await get_config()

    # 获取用户可用的搜索源
    api_sites = await get_available_api_sites((auth_info or {}).get("username"))

    # 如果指定了搜索源，只使用选中的搜索源
    selected_sources_param = request.query_params.get("sources")
    if selected_sources_param:
        selected_sources = selected_sources_param.split(",")
        api_sites = [site for site in api_sites if site.get("key") in selected_sources]

    if not query:
        # 空查询，明确不缓存
        return Response(
            content=json_dump({"results": []}),
            headers={
                "Content-Type": "application/json",
                **NO_STORE,
            },
        )

    disable_yellow = bool((config.get("SiteConfig") or {}).get("DisableYellowFilter"))

    # -------------------------
    # 非流式：并发
    # -------------------------
    if not enable_stream:
        results = await asyncio.gather(
            *(_search_one_site(site, query, disable_yellow, timeout_ms) for site in api_sites)
        )
        aggregated_results = [item for pair in results for item in pair[0]]
        failed_sources = [pair[1] for pair in results if pair[1]]

        if not aggregated_results:
            return Response(
                content=json_dump({"results": [], "failedSources": failed_sources}),
                headers={"Content-Type": "application/json; charset=utf-8", **NO_STORE},
            )

        cache_time = await get_cache_time()
        return Response(
            content=json_dump({"results": aggregated_results, "failedSources": failed_sources}),
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Cache-Control": f"private, max-age={cache_time}",
            },
        )

    # -------------------------
    # 流式：并发
    # -------------------------
    aggregated_results: list = []
    failed_sources: list = []
    events: list = []

    def safe_write(obj) -> None:
        """`writer.write(encoder.encode(JSON.stringify(obj) + '\\n'))`."""
        events.append(json_dump(obj).encode("utf-8") + b"\n")

    async def run_site(site: dict) -> None:
        has_results = False
        try:
            async for page_results in search_from_api_stream(site, query, True, timeout_ms):
                filtered_results = page_results
                if len(filtered_results) != 0:
                    has_results = True
                if not disable_yellow:
                    filtered_results = _yellow_filter(page_results)

                if has_results and len(filtered_results) == 0:
                    failed_sources.append(_failure(site, "结果被过滤"))
                    safe_write({"failedSources": failed_sources})
                    return

                aggregated_results.extend(filtered_results)
                safe_write({"site": site.get("key"), "pageResults": filtered_results})

            if not has_results:
                failed_sources.append(_failure(site, "无搜索结果"))
                safe_write({"failedSources": failed_sources})
        except Exception as err:  # noqa: BLE001
            print(f"搜索失败 {site.get('name')}: {err}")
            failed_sources.append(_failure(site, _error_message(err)))
            safe_write({"failedSources": failed_sources})

    async def generate():
        tasks = [asyncio.create_task(run_site(site)) for site in api_sites]
        pending = set(tasks)
        index = 0
        while pending:
            while index < len(events):
                yield events[index]
                index += 1
            await asyncio.sleep(0)
            pending = {task for task in pending if not task.done()}
        while index < len(events):
            yield events[index]
            index += 1

        if failed_sources:
            yield json_dump({"failedSources": failed_sources}).encode("utf-8") + b"\n"
        yield json_dump({"aggregatedResults": aggregated_results}).encode("utf-8") + b"\n"

    cache_time = await get_cache_time()
    return StreamingResponse(
        generate(),
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Cache-Control": f"private, max-age={cache_time}",
        },
    )


ROUTES = [("/api/search", GET, ["GET"])]
