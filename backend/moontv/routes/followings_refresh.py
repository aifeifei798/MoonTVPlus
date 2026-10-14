"""`src/app/api/followings/refresh/route.ts` — SSE batch refresh of 追更.

The route re-derives each 追更 entry's episode count through
`fetch_video_detail.py` (search first, detail only as a fallback) and writes
back only when the count actually changed, which is what keeps a client that
refreshes on every visit from rewriting the whole table.

The framing is the wire surface: one `data: {...}` event per entry as soon as
that entry finishes, so `db.client.ts`'s reader can update the UI mid-flight.
Upstream wrote into a `ReadableStream` and watched `controller.desiredSize` to
notice a client that stopped reading; here the events go into a queue this
generator drains, and Starlette races that drain against `http.disconnect`, so a
hung-up client closes the generator and the `finally` cancels the driver. What
cannot be reproduced is upstream's `desiredSize` check itself: the drain notices
the hang-up at the next chunk boundary, not in the middle of a fetch.

The worker pool keeps upstream's `CONCURRENCY = 5` rather than running the
entries one at a time, because the whole point of the route is that a user with
a hundred 追更 entries finishes in hundred/fetches rather than hundred round
trips.
"""

from __future__ import annotations

import asyncio

from fastapi import Request
from fastapi.responses import StreamingResponse

from ..fetch_video_detail import fetch_video_detail
from ..routes import json_response, user_guard
from ..storage.db import Db
from ..utils import js_truthy, json_dump, split_key

CONCURRENCY = 5  # `const CONCURRENCY = 5`
DETAIL_TIMEOUT_MS = 30000  # `timeout: 30000`
ZERO_EPISODES = "接口返回 0 集（可能资源已下架或未收录）"
DETAIL_CACHE_MAX = 500  # `detailCache` 上限，FIFO 淘汰最旧 key

_SENTINEL = object()


def _first_truthy(*values):
    """`a || b || c` — JS's `||` yields the value, not a bool."""
    for value in values[:-1]:
        if js_truthy(value):
            return value
    return values[-1]


def _js_object_entries(value) -> list[tuple[str, object]]:
    """`Object.entries(x)` — strings index characters, arrays index elements."""
    if isinstance(value, dict):
        return list(value.items())
    if isinstance(value, (list, str)):
        return [(str(i), v) for i, v in enumerate(value)]
    return []


async def followings_refresh(request: Request):
    try:
        username, _, rejected = await user_guard(request, "Unauthorized")
        if rejected:
            return json_response(rejected[0], rejected[1])

        # 解析请求体（可选）
        try:
            body = await request.json()
        except Exception:  # 无 body 时忽略，从数据库读取
            body = {}

        if body is None:
            # `null.followings` is a TypeError outside the inner try, so it
            # reaches the outer handler and answers 500, as upstream did.
            raise AttributeError("Cannot read properties of null (reading 'followings')")
        raw = body.get("followings") if isinstance(body, dict) else None

        # 确定要刷新的追更列表
        entries = _js_object_entries(raw) if js_truthy(raw) else []
        if len(entries) == 0:
            entries = list((await Db.get_all_followings(username)).items())
    except Exception as error:  # noqa: BLE001
        print("批量刷新追更失败:", error)
        return json_response({"error": "Internal Server Error"}, 500)

    return StreamingResponse(
        _stream(username, entries),
        headers={
            # Upstream set the content type in the header map, so Starlette's
            # `text/*` → `text/*; charset=utf-8` rewrite has to be skipped.
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST",
            "Access-Control-Allow-Headers": "Content-Type",
        },
    )


async def _stream(username: str, entries: list[tuple[str, dict]]):
    """The `ReadableStream`'s `start()` — one SSE event per yielded chunk."""
    out: asyncio.Queue = asyncio.Queue()
    closed = False
    updated_count = 0
    failed_count = 0
    success_count = 0
    cursor = 0
    detail_cache: dict[str, asyncio.Task] = {}

    def emit(payload) -> bool:
        """`send(payload)` — false once the stream is closed."""
        nonlocal closed
        if closed:
            return False
        out.put_nowait(f"data: {json_dump(payload)}\n\n".encode())
        return True

    async def get_detail(source: str, id_: str, fallback_title: str):
        """`getDetail` — the shared promise cache, so concurrent workers that
        refresh the same `source+id` fetch it once."""
        cache_key = f"{source}+{id_}"
        task = detail_cache.get(cache_key)
        if task is None:
            task = asyncio.ensure_future(_fetch_detail(source, id_, fallback_title))
            if len(detail_cache) >= DETAIL_CACHE_MAX:
                detail_cache.pop(next(iter(detail_cache)))
            detail_cache[cache_key] = task
        return await task

    async def _fetch_detail(source: str, id_: str, fallback_title: str):
        """`fetchVideoDetail(...).then(...).catch(...)` — never raises, so the
        cached task always holds a value."""
        try:
            detail = await fetch_video_detail(
                source=source,
                id_=id_,
                fallback_title=fallback_title.strip(),
                timeout_ms=DETAIL_TIMEOUT_MS,
            )
            episodes = detail.get("episodes")
            episode_count = (
                len(episodes) if isinstance(episodes, (list, tuple, str, dict)) else 0
            )
            if episode_count <= 0:
                return None, ZERO_EPISODES
            return (
                {
                    "title": _first_truthy(detail.get("title"), ""),
                    "cover": _first_truthy(detail.get("poster"), ""),
                    "source_name": _first_truthy(detail.get("source_name"), ""),
                    "year": _first_truthy(detail.get("year"), "unknown"),
                    "total_episodes": episode_count,
                    "search_title": _first_truthy(detail.get("title"), ""),
                },
                None,
            )
        except Exception as error:  # noqa: BLE001
            print(f"获取视频详情失败 ({source}+{id_}):", error)
            return None, _first_truthy(str(error), "获取详情失败")

    async def process_entry(key: str, item: dict):
        nonlocal updated_count, failed_count, success_count, closed
        if closed:
            return

        source, id_ = split_key(key)
        if not source or not id_:
            print(f"跳过无效的追更键: {key}")
            failed_count += 1
            if not emit(
                {
                    "type": "item_failed",
                    "key": key,
                    "source": source,
                    "id": id_,
                    "title": _first_truthy(item.get("title"), ""),
                    "reason": "无效的追更键",
                }
            ):
                closed = True
            return

        fallback_title = _first_truthy(item.get("search_title"), item.get("title"), "")
        if not js_truthy(fallback_title):
            print(f"跳过缺少标题的追更: {key}")
            failed_count += 1
            if not emit(
                {
                    "type": "item_failed",
                    "key": key,
                    "source": source,
                    "id": id_,
                    "title": _first_truthy(item.get("title"), ""),
                    "reason": "缺少标题",
                }
            ):
                closed = True
            return

        detail, reason = await get_detail(source, id_, fallback_title)
        if closed:
            return

        if not detail:
            failed_count += 1
            if not emit(
                {
                    "type": "item_failed",
                    "key": key,
                    "source": source,
                    "id": id_,
                    "title": _first_truthy(item.get("title"), ""),
                    "reason": _first_truthy(reason, "获取集数失败"),
                }
            ):
                closed = True
            return

        episode_count = detail["total_episodes"]
        updated = False
        if episode_count > 0 and episode_count != item.get("total_episodes"):
            await Db.set_following(
                username,
                f"{source}+{id_}",
                {
                    **item,
                    "title": _first_truthy(detail.get("title"), item.get("title")),
                    "cover": _first_truthy(detail.get("cover"), item.get("cover")),
                    "source_name": _first_truthy(
                        detail.get("source_name"), item.get("source_name")
                    ),
                    "year": detail["year"]
                    if js_truthy(detail.get("year")) and detail.get("year") != "unknown"
                    else item.get("year"),
                    "total_episodes": episode_count,
                    "save_time": item.get("save_time"),
                    "search_title": _first_truthy(
                        item.get("search_title"), detail.get("search_title"), item.get("title")
                    ),
                },
            )
            updated = True
            updated_count += 1
            print(
                f"更新追更: {_first_truthy(item.get('title'), '')} "
                f"({item.get('total_episodes')} -> {episode_count})"
            )

        success_count += 1
        if not emit(
            {
                "type": "item_result",
                "key": key,
                "source": source,
                "id": id_,
                "title": _first_truthy(detail.get("title"), item.get("title")),
                "total_episodes": episode_count,
                "updated": updated,
            }
        ):
            closed = True

    async def _worker():
        nonlocal cursor
        while not closed:
            idx = cursor
            cursor += 1
            if idx >= len(entries):
                break
            key, item = entries[idx]
            await process_entry(key, item)

    async def run_workers():
        try:
            await asyncio.gather(
                *(_worker() for _ in range(min(CONCURRENCY, len(entries))))
            )
            emit(
                {
                    "type": "complete",
                    "updatedCount": updated_count,
                    "failedCount": failed_count,
                    "successCount": success_count,
                    "total": len(entries),
                }
            )
        except Exception as error:  # noqa: BLE001
            # 处理过程中任何异常都不应导致 uncaughtException
            print("刷新追更流处理异常:", error)
        finally:
            out.put_nowait(_SENTINEL)

    if not emit({"type": "start", "total": len(entries)}):
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
    ("/api/followings/refresh", followings_refresh, ["POST"]),
]
