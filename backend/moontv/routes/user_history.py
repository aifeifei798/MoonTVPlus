"""`src/app/api/{searchhistory,skipconfigs,today-updated}/route.ts`.

The three per-user reads the app keeps alongside the record stores: the search
keywords it has looked up, the per-video skip-intro/outro offsets, and the
"今日新更" snapshot it recomputes once a day. They differ from
`user_state.py`'s stores in the wording they answer with (`未登录`,
`缺少必要参数`, `无效的key格式`) and in the shape of a body — `today-updated`
validates and *normalizes* the whole record server-side, so the stored rows
always carry every field with a number where the client sent nothing.

`SEARCH_HISTORY_LIMIT` is the client's own cap, repeated here because the POST
answers with the list it just wrote and the client renders whatever it gets.
"""

from __future__ import annotations

import re

from fastapi import Request

from ..routes import json_response, user_guard
from ..storage.db import Db
from ..utils import js_number, js_string_of, js_truthy, split_key

SEARCH_HISTORY_LIMIT = 20

# `^\d{4}-\d{2}-\d{2}$` — `\d` in JS is ASCII digits, Python's is not, so the
# class is spelled out or a Devanagari date would validate.
_DATE_PATTERN = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


def _string_or_empty(value) -> str:
    """`String(it.x ?? '')`."""
    return "" if value is None else js_string_of(value)


def _number_or_zero(value) -> float:
    """`Number(it.x) || 0`."""
    return js_number(value) or 0


# ---------------------------------------------------------------------------
# /api/searchhistory
# ---------------------------------------------------------------------------


async def searchhistory_get(request: Request):
    try:
        username, _, rejected = await user_guard(request, "Unauthorized")
        if rejected:
            return json_response(rejected[0], rejected[1])
        return json_response(await Db.get_search_history(username))
    except Exception as error:  # noqa: BLE001
        print("获取搜索历史失败:", error)
        return json_response({"error": "Internal Server Error"}, 500)


async def searchhistory_post(request: Request):
    try:
        username, _, rejected = await user_guard(request, "Unauthorized")
        if rejected:
            return json_response(rejected[0], rejected[1])

        body = await request.json()
        keyword = body.get("keyword")
        if keyword is not None:
            # A non-string keyword has no `.trim()`, so upstream threw here and
            # the catch answered 500 rather than accepting the value.
            keyword = keyword.strip()
        if not keyword:
            return json_response({"error": "Keyword is required"}, 400)

        await Db.add_search_history(username, keyword)
        # The client keeps its own copy, so the route answers with the list the
        # server just wrote rather than with what the client sent.
        history = await Db.get_search_history(username)
        return json_response(history[:SEARCH_HISTORY_LIMIT])
    except Exception as error:  # noqa: BLE001
        print("添加搜索历史失败:", error)
        return json_response({"error": "Internal Server Error"}, 500)


async def searchhistory_delete(request: Request):
    try:
        username, _, rejected = await user_guard(request, "Unauthorized")
        if rejected:
            return json_response(rejected[0], rejected[1])

        kw = request.query_params.get("keyword")
        if kw is not None:
            kw = kw.strip()

        await Db.delete_search_history(username, kw or None)
        return json_response({"success": True})
    except Exception as error:  # noqa: BLE001
        print("删除搜索历史失败:", error)
        return json_response({"error": "Internal Server Error"}, 500)


# ---------------------------------------------------------------------------
# /api/skipconfigs
# ---------------------------------------------------------------------------


async def skipconfigs_get(request: Request):
    try:
        username, _, rejected = await user_guard(request, "未登录")
        if rejected:
            return json_response(rejected[0], rejected[1])

        source = request.query_params.get("source")
        id_ = request.query_params.get("id")
        if source and id_:
            return json_response(await Db.get_skip_config(username, source, id_))
        return json_response(await Db.get_all_skip_configs(username))
    except Exception as error:  # noqa: BLE001
        print("获取跳过片头片尾配置失败:", error)
        return json_response({"error": "获取跳过片头片尾配置失败"}, 500)


async def skipconfigs_post(request: Request):
    try:
        username, _, rejected = await user_guard(request, "未登录")
        if rejected:
            return json_response(rejected[0], rejected[1])

        body = await request.json()
        key, config = body.get("key"), body.get("config")
        if not js_truthy(key) or not js_truthy(config):
            return json_response({"error": "缺少必要参数"}, 400)

        source, id_ = split_key(key)
        if not source or not id_:
            return json_response({"error": "无效的key格式"}, 400)

        # `SkipConfig` is rebuilt field by field, so anything the client sent
        # beyond these three offsets never reaches storage.
        skip_config = {
            "enable": bool(config.get("enable")),
            "intro_time": js_number(config.get("intro_time")) or 0,
            "outro_time": js_number(config.get("outro_time")) or 0,
        }

        await Db.set_skip_config(username, source, id_, skip_config)
        return json_response({"success": True})
    except Exception as error:  # noqa: BLE001
        print("保存跳过片头片尾配置失败:", error)
        return json_response({"error": "保存跳过片头片尾配置失败"}, 500)


async def skipconfigs_delete(request: Request):
    try:
        username, _, rejected = await user_guard(request, "未登录")
        if rejected:
            return json_response(rejected[0], rejected[1])

        key = request.query_params.get("key")
        if not key:
            return json_response({"error": "缺少必要参数"}, 400)

        source, id_ = split_key(key)
        if not source or not id_:
            return json_response({"error": "无效的key格式"}, 400)

        await Db.delete_skip_config(username, source, id_)
        return json_response({"success": True})
    except Exception as error:  # noqa: BLE001
        print("删除跳过片头片尾配置失败:", error)
        return json_response({"error": "删除跳过片头片尾配置失败"}, 500)


# ---------------------------------------------------------------------------
# “今日新更”
# ---------------------------------------------------------------------------


def _string_or_empty(value) -> str:
    """`String(it.x ?? '')`."""
    return "" if value is None else js_string_of(value)


def _number_or_zero(value) -> float:
    """`Number(it.x) || 0`."""
    return js_number(value) or 0


def validate_record(body):
    """`validateRecord` — the normalizer, or `None` when the record is unusable."""
    if not isinstance(body, dict):
        return None

    date = body["date"] if isinstance(body.get("date"), str) else ""
    if not _DATE_PATTERN.fullmatch(date):
        return None

    raw_items = body["items"] if isinstance(body.get("items"), list) else []
    items = [
        {
            "source": _string_or_empty(it.get("source")),
            "id": _string_or_empty(it.get("id")),
            "title": _string_or_empty(it.get("title")),
            "poster": _string_or_empty(it.get("poster")),
            "episodes": _number_or_zero(it.get("episodes")),
            "watchedEpisodes": _number_or_zero(it.get("watchedEpisodes")),
            "unwatchedEpisodes": _number_or_zero(it.get("unwatchedEpisodes")),
            "source_name": _string_or_empty(it.get("source_name")),
            "year": _string_or_empty(it.get("year")),
            "save_time": _number_or_zero(it.get("save_time")),
            "oldEpisodes": _number_or_zero(it.get("oldEpisodes")),
            "newEpisodes": _number_or_zero(it.get("newEpisodes")),
        }
        for it in raw_items
        if isinstance(it, dict)
    ]

    return {"date": date, "items": items}


async def today_updated_get(request: Request):
    try:
        username, _, rejected = await user_guard(request, "未登录")
        if rejected:
            return json_response(rejected[0], rejected[1])
        return json_response(await Db.get_today_updated(username))
    except Exception as error:  # noqa: BLE001
        print("获取“今日新更”记录失败:", error)
        return json_response({"error": "获取“今日新更”记录失败"}, 500)


async def today_updated_post(request: Request):
    try:
        username, _, rejected = await user_guard(request, "未登录")
        if rejected:
            return json_response(rejected[0], rejected[1])

        body = await request.json()
        record = validate_record(body)
        if not record:
            return json_response({"error": "无效的记录格式"}, 400)

        await Db.set_today_updated(username, record)
        return json_response({"success": True})
    except Exception as error:  # noqa: BLE001
        print("保存“今日新更”记录失败:", error)
        return json_response({"error": "保存“今日新更”记录失败"}, 500)


ROUTES = [
    ("/api/searchhistory", searchhistory_get, ["GET"]),
    ("/api/searchhistory", searchhistory_post, ["POST"]),
    ("/api/searchhistory", searchhistory_delete, ["DELETE"]),
    ("/api/skipconfigs", skipconfigs_get, ["GET"]),
    ("/api/skipconfigs", skipconfigs_post, ["POST"]),
    ("/api/skipconfigs", skipconfigs_delete, ["DELETE"]),
    ("/api/today-updated", today_updated_get, ["GET"]),
    ("/api/today-updated", today_updated_post, ["POST"]),
]
