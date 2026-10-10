"""`src/app/api/{user/online,playrecords,favorites,followings}/route.ts`.

The three record stores — play records, favorites, followings — are the same
handler three times upstream: a cookie check, a ban check, a `key=source+id`
split, and a `DbManager` call whose column set is fixed by the storage schema
(`d1-init.sql`), which is why a client that sends an extra field gets it
dropped rather than stored. `/api/user/online` lives here too because it is the
request the app fires on every page load, and it is the one of these that
writes the admin config instead of a per-user row.

The sentences are what the client renders, so they are copied per route rather
than unified: the stores answer `Unauthorized`, `/api/user/online` answers
`未登录`, and each save path names its own noun (`Missing key or favorite`,
`Invalid following data`). `/api/followings/refresh` is the odd one out and
lives in `followings_refresh.py`.
"""

from __future__ import annotations

from fastapi import Request

from ..auth import get_auth_info_from_cookie
from ..config import config_self_check, get_config
from ..env import STORAGE_TYPE, env
from ..routes import find_user, json_response, user_guard
from ..storage.db import Db
from ..utils import js_number, js_truthy, now_ms, split_key

LOCALSTORAGE_WRITE_ERROR = "localstorage 模式不支持管理员配置写入"


def _index_below_one(record: dict) -> bool:
    """`record.index < 1` — a missing index is `undefined`, which is not `< 1`."""
    index = js_number(record.get("index"))
    return index is not None and index < 1


def _with_save_time(record: dict) -> dict:
    """`{ ...record, save_time: record.save_time ?? Date.now() }`."""
    return {**record, "save_time": record["save_time"] if record.get("save_time") is not None else now_ms()}


# ---------------------------------------------------------------------------
# /api/user/online
# ---------------------------------------------------------------------------


async def user_online(request: Request):
    if env(STORAGE_TYPE, "sqlite") == "localstorage":
        return json_response({"ok": False, "reason": LOCALSTORAGE_WRITE_ERROR}, 400)

    try:
        auth = get_auth_info_from_cookie(request)
        if not auth or not auth.get("username"):
            return json_response({"error": "未登录"}, 401)
        username = auth["username"]

        admin_config = await get_config()
        user_entry = find_user(admin_config, username)
        if not user_entry:
            return json_response({"error": "用户不存在"}, 404)
        if user_entry.get("banned"):
            return json_response({"error": "用户已被封禁"}, 401)

        user_entry["lastOnline"] = now_ms()
        await Db.save_admin_config(config_self_check(admin_config))

        return json_response({"ok": True, "lastOnline": user_entry["lastOnline"]})
    except Exception as error:  # noqa: BLE001
        print("更新用户上线时间失败:", error)
        return json_response({"error": "更新用户上线时间失败", "details": str(error)}, 500)


# ---------------------------------------------------------------------------
# /api/playrecords
# ---------------------------------------------------------------------------


async def playrecords_get(request: Request):
    try:
        username, _, rejected = await user_guard(request, "Unauthorized")
        if rejected:
            return json_response(rejected[0], rejected[1])
        records = await Db.get_all_play_records(username)
        return json_response(records)
    except Exception as error:  # noqa: BLE001
        print("获取播放记录失败:", error)
        return json_response({"error": "Internal Server Error"}, 500)


async def playrecords_post(request: Request):
    try:
        username, _, rejected = await user_guard(request, "Unauthorized")
        if rejected:
            return json_response(rejected[0], rejected[1])

        body = await request.json()
        key, record = body.get("key"), body.get("record")
        if not js_truthy(key) or not js_truthy(record):
            return json_response({"error": "Missing key or record"}, 400)
        if not record.get("title") or not record.get("source_name") or _index_below_one(record):
            return json_response({"error": "Invalid record data"}, 400)

        source, id_ = split_key(key)
        if not source or not id_:
            return json_response({"error": "Invalid key format"}, 400)

        await Db.set_play_record(username, f"{source}+{id_}", _with_save_time(record))
        return json_response({"success": True})
    except Exception as error:  # noqa: BLE001
        print("保存播放记录失败:", error)
        return json_response({"error": "Internal Server Error"}, 500)


async def playrecords_delete(request: Request):
    try:
        username, _, rejected = await user_guard(request, "Unauthorized")
        if rejected:
            return json_response(rejected[0], rejected[1])

        key = request.query_params.get("key")
        if key:
            source, id_ = split_key(key)
            if not source or not id_:
                return json_response({"error": "Invalid key format"}, 400)
            await Db.delete_play_record(username, f"{source}+{id_}")
        else:
            # Upstream fanned the sweep out with `Promise.all`; the sqlite and
            # redis backends both serialize writes, so the loop is the same
            # work without a hundred coroutines competing for the cursor.
            all = await Db.get_all_play_records(username)
            for k in all:
                source, id_ = split_key(k)
                if source and id_:
                    await Db.delete_play_record(username, f"{source}+{id_}")

        return json_response({"success": True})
    except Exception as error:  # noqa: BLE001
        print("删除播放记录失败:", error)
        return json_response({"error": "Internal Server Error"}, 500)


# ---------------------------------------------------------------------------
# /api/favorites
# ---------------------------------------------------------------------------


async def favorites_get(request: Request):
    try:
        username, _, rejected = await user_guard(request, "Unauthorized")
        if rejected:
            return json_response(rejected[0], rejected[1])

        key = request.query_params.get("key")
        if key:
            source, id_ = split_key(key)
            if not source or not id_:
                return json_response({"error": "Invalid key format"}, 400)
            return json_response(await Db.get_favorite(username, f"{source}+{id_}"))

        return json_response(await Db.get_all_favorites(username))
    except Exception as error:  # noqa: BLE001
        print("获取收藏失败:", error)
        return json_response({"error": "Internal Server Error"}, 500)


async def favorites_post(request: Request):
    try:
        username, _, rejected = await user_guard(request, "Unauthorized")
        if rejected:
            return json_response(rejected[0], rejected[1])

        body = await request.json()
        key, favorite = body.get("key"), body.get("favorite")
        if not js_truthy(key) or not js_truthy(favorite):
            return json_response({"error": "Missing key or favorite"}, 400)
        if not favorite.get("title") or not favorite.get("source_name"):
            return json_response({"error": "Invalid favorite data"}, 400)

        source, id_ = split_key(key)
        if not source or not id_:
            return json_response({"error": "Invalid key format"}, 400)

        await Db.set_favorite(username, f"{source}+{id_}", _with_save_time(favorite))
        return json_response({"success": True})
    except Exception as error:  # noqa: BLE001
        print("保存收藏失败:", error)
        return json_response({"error": "Internal Server Error"}, 500)


async def favorites_delete(request: Request):
    try:
        username, _, rejected = await user_guard(request, "Unauthorized")
        if rejected:
            return json_response(rejected[0], rejected[1])

        key = request.query_params.get("key")
        if key:
            source, id_ = split_key(key)
            if not source or not id_:
                return json_response({"error": "Invalid key format"}, 400)
            await Db.delete_favorite(username, f"{source}+{id_}")
        else:
            all = await Db.get_all_favorites(username)
            for k in all:
                source, id_ = split_key(k)
                if source and id_:
                    await Db.delete_favorite(username, f"{source}+{id_}")

        return json_response({"success": True})
    except Exception as error:  # noqa: BLE001
        print("删除收藏失败:", error)
        return json_response({"error": "Internal Server Error"}, 500)


# ---------------------------------------------------------------------------
# /api/followings
# ---------------------------------------------------------------------------


async def followings_get(request: Request):
    try:
        username, _, rejected = await user_guard(request, "Unauthorized")
        if rejected:
            return json_response(rejected[0], rejected[1])

        key = request.query_params.get("key")
        if key:
            source, id_ = split_key(key)
            if not source or not id_:
                return json_response({"error": "Invalid key format"}, 400)
            return json_response(await Db.get_following(username, f"{source}+{id_}"))

        return json_response(await Db.get_all_followings(username))
    except Exception as error:  # noqa: BLE001
        print("获取追更失败:", error)
        return json_response({"error": "Internal Server Error"}, 500)


async def followings_post(request: Request):
    try:
        username, _, rejected = await user_guard(request, "Unauthorized")
        if rejected:
            return json_response(rejected[0], rejected[1])

        body = await request.json()
        key, following = body.get("key"), body.get("following")
        if not js_truthy(key) or not js_truthy(following):
            return json_response({"error": "Missing key or following"}, 400)
        if not following.get("title") or not following.get("source_name"):
            return json_response({"error": "Invalid following data"}, 400)

        source, id_ = split_key(key)
        if not source or not id_:
            return json_response({"error": "Invalid key format"}, 400)

        await Db.set_following(username, f"{source}+{id_}", _with_save_time(following))
        return json_response({"success": True})
    except Exception as error:  # noqa: BLE001
        print("保存追更失败:", error)
        return json_response({"error": "Internal Server Error"}, 500)


async def followings_delete(request: Request):
    try:
        username, _, rejected = await user_guard(request, "Unauthorized")
        if rejected:
            return json_response(rejected[0], rejected[1])

        key = request.query_params.get("key")
        if key:
            source, id_ = split_key(key)
            if not source or not id_:
                return json_response({"error": "Invalid key format"}, 400)
            await Db.delete_following(username, f"{source}+{id_}")
        else:
            all = await Db.get_all_followings(username)
            for k in all:
                source, id_ = split_key(k)
                if source and id_:
                    await Db.delete_following(username, f"{source}+{id_}")

        return json_response({"success": True})
    except Exception as error:  # noqa: BLE001
        print("删除追更失败:", error)
        return json_response({"error": "Internal Server Error"}, 500)


ROUTES = [
    ("/api/user/online", user_online, ["POST"]),
    ("/api/playrecords", playrecords_get, ["GET"]),
    ("/api/playrecords", playrecords_post, ["POST"]),
    ("/api/playrecords", playrecords_delete, ["DELETE"]),
    ("/api/favorites", favorites_get, ["GET"]),
    ("/api/favorites", favorites_post, ["POST"]),
    ("/api/favorites", favorites_delete, ["DELETE"]),
    ("/api/followings", followings_get, ["GET"]),
    ("/api/followings", followings_post, ["POST"]),
    ("/api/followings", followings_delete, ["DELETE"]),
]
