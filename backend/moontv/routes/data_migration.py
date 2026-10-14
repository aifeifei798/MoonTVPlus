"""`src/app/api/admin/data_migration/{export,import}/route.ts`.

The `.dat` backup format is the point of this pair, so it is reproduced rather
than redesigned: JSON → `pako.deflate` → base64 → CryptoJS AES (OpenSSL
`Salted__` framing) → base64, streamed as SSE so the panel can show progress
and so a multi-megabyte backup never exists as one SSE event. The import side
reads the multipart form back through the same pipeline and replays it through
the storage batch writers.

Both routes answer with `text/event-stream` and one `data: {...}` event per
progress step. The event vocabulary (`stage`/`user`/`detail`/`progress`/
`file_start`/`chunk`/`file_end`/`done`/`error`) and the percentage formulas are
what `components/DataMigration.tsx` parses, including the fact that export
progress is per-user (5%→90%) while import progress is per-record.

Upstream ran the per-user reads and the batch writes with `Promise.all`; here
they are awaited one at a time. sqlite serializes writes anyway, and the
`on_progress(done, total)` callback fires per 100-row chunk either way, so the
progress events land in the same places.
"""

from __future__ import annotations

import base64
import json
import math
import os
import time
from datetime import datetime
from typing import Any

from fastapi import Request
from fastapi.responses import StreamingResponse

from ..auth import get_auth_info_from_cookie
from ..config import config_self_check, set_cached_config
from ..crypto import DecryptFailed, decode_backup, encode_backup
from ..env import PASSWORD, STORAGE_TYPE, USERNAME, env, env_raw
from ..routes import json_response
from ..storage.db import Db
from ..utils import json_dump
from ..version import CURRENT_VERSION

CHUNK_SIZE = 64 * 1024  # `64 * 1024` characters of the encrypted string
BATCH_SIZE = 50  # the non-batch fallback's parallel width

# MoonTVPlus addition (PLAN.md change 39): the import route used to read the whole
# upload into memory before decrypting it, so a 500 MB "backup" was a memory
# explosion on a box with a few hundred MB of RAM. `MOONTVPLUS_IMPORT_MAX_BYTES`
# raises the ceiling; 0 or garbage keeps the default.
IMPORT_MAX_BYTES = 50 * 1024 * 1024
_READ_CHUNK = 1024 * 1024


def _import_limit() -> int:
    raw = os.environ.get("MOONTVPLUS_IMPORT_MAX_BYTES", "")
    try:
        value = int(raw)
    except ValueError:
        return IMPORT_MAX_BYTES
    return value if value > 0 else IMPORT_MAX_BYTES


def _size_label(limit: int) -> str:
    return f"{limit // (1024 * 1024)} MB" if limit % (1024 * 1024) == 0 else f"{limit} 字节"


async def _read_upload(upload):
    """The uploaded `.dat` as bytes, or `None` when it exceeds the size cap.

    Starlette spools a file part to a temp file, so reading it in 1 MB slices is
    what keeps the process from holding the whole thing at once — the cap has to
    be checked while reading, not after.
    """
    if not hasattr(upload, "read"):
        return upload if isinstance(upload, bytes) else str(upload).encode("utf-8")

    limit = _import_limit()
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(_READ_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            return None
        chunks.append(chunk)
    return b"".join(chunks)

# `Math.round` rounds half away from zero; Python's `round` is banker's rounding,
# and the progress bar the panel draws depends on which one the fraction lands in.
def _js_round(value: float) -> int:
    return math.floor(value + 0.5)


def _iso_now() -> str:
    """`new Date().toISOString()` — UTC, milliseconds, trailing `Z`."""
    now = datetime.now().astimezone()
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def _backup_filename(now) -> str:
    """`moontv-backup-YYYYMMDD-HHMMSS.dat`, in the server's local time like upstream."""
    return (
        "moontv-backup-"
        f"{now.year:04d}{now.month:02d}{now.day:02d}-"
        f"{now.hour:02d}{now.minute:02d}{now.second:02d}"
        ".dat"
    )


def _sse(data) -> bytes:
    """`encodeSSE(data)` — `data: ${JSON.stringify(data)}\\n\\n`."""
    return f"data: {json_dump(data)}\n\n".encode("utf-8")


def _split_key(key: str) -> tuple[str, str]:
    """`const [source, id] = key.split('+')` — the first two parts only."""
    parts = key.split("+")
    return (parts[0] if parts else "", parts[1] if len(parts) > 1 else "")


def _keyed_entries(items) -> list[tuple[str, Any]]:
    """`Object.entries(x)` over a dict, tolerating anything else as empty."""
    return list(items.items()) if isinstance(items, dict) else []


def _source_id_entries(items) -> list[tuple[str, Any]]:
    """`Object.entries(x).filter(([source, id]) => source && id)`."""
    return [
        (key, value)
        for key, value in _keyed_entries(items)
        if all(_split_key(key))
    ]


def _count_user_records(user) -> int:
    """`countUserRecords` — the denominator of the import progress bar."""
    if not isinstance(user, dict):
        return 0
    count = 0
    for field in ("playRecords", "favorites"):
        if user.get(field):
            count += len(_keyed_entries(user[field]))
    for field in ("followings", "skipConfigs"):
        if user.get(field):
            count += len(_source_id_entries(user[field]))
    if user.get("todayUpdated"):
        count += 1  # 今日新更整体算 1 条
    if isinstance(user.get("searchHistory"), list):
        count += len(user["searchHistory"])
    return count


async def _run_in_batches(items, task, on_progress) -> None:
    """`runInBatches` — the fallback path's parallel-per-batch writes."""
    done = 0
    for i in range(0, len(items), BATCH_SIZE):
        for item in items[i : i + BATCH_SIZE]:
            await task(item)
        done += len(items[i : i + BATCH_SIZE])
        if on_progress:
            on_progress(done)


# ---------------------------------------------------------------------------
# POST /api/admin/data_migration/export
# ---------------------------------------------------------------------------


async def export(request: Request):
    try:
        if env(STORAGE_TYPE, "sqlite") == "localstorage":
            return json_response({"error": "不支持本地存储进行数据迁移"}, 400)

        auth_info = get_auth_info_from_cookie(request)
        if not auth_info or not auth_info.get("username"):
            return json_response({"error": "未登录"}, 401)
        if auth_info["username"] != env_raw(USERNAME):
            return json_response({"error": "权限不足，只有站长可以导出数据"}, 401)

        config = await Db.get_admin_config()
        if not config:
            return json_response({"error": "无法获取配置"}, 500)

        body = await request.json()
        password = body.get("password")
        if not password or not isinstance(password, str):
            return json_response({"error": "请提供加密密码"}, 400)

        admin_username = env_raw(USERNAME)

        # 获取所有用户（站长 included, insertion order kept like `new Set`)
        all_users = list(dict.fromkeys([*(await Db.get_all_users()), admin_username]))
        total_users = len(all_users)

        export_data = {
            "timestamp": _iso_now(),
            "serverVersion": CURRENT_VERSION,
            "data": {"adminConfig": config, "userData": {}},
        }

        async def events():
            try:
                yield _sse({"type": "stage", "message": "正在导出管理员配置...", "percent": 0})

                user_index = 0
                for username in all_users:
                    user_index += 1
                    percent = _js_round(5 + (user_index / total_users) * 85) if total_users else 5

                    yield _sse(
                        {
                            "type": "user",
                            "message": f"正在导出用户 {user_index}/{total_users}: {username}",
                            "username": username,
                            "userIndex": user_index,
                            "totalUsers": total_users,
                            "percent": percent,
                        }
                    )

                    play_records = await Db.get_all_play_records(username)
                    favorites = await Db.get_all_favorites(username)
                    followings = await Db.get_all_followings(username)
                    today_updated = await Db.get_today_updated(username)
                    search_history = await Db.get_search_history(username)
                    skip_configs = await Db.get_all_skip_configs(username)
                    user_password = await Db.get_user_password(username)

                    play_count = len(play_records or {})
                    fav_count = len(favorites or {})
                    fol_count = len(followings or {})
                    search_count = len(search_history) if isinstance(search_history, list) else 0
                    skip_count = len(skip_configs or {})

                    counts = []
                    if play_count:
                        counts.append(f"播放记录{play_count}")
                    if fav_count:
                        counts.append(f"收藏{fav_count}")
                    if fol_count:
                        counts.append(f"追更{fol_count}")
                    if today_updated:
                        counts.append("今日新更")
                    if search_count:
                        counts.append(f"搜索历史{search_count}")
                    if skip_count:
                        counts.append(f"跳过配置{skip_count}")

                    yield _sse(
                        {
                            "type": "detail",
                            "message": f"用户 {username}: {('、'.join(counts) if counts else '无数据')}",
                            "percent": percent,
                        }
                    )

                    export_data["data"]["userData"][username] = {
                        "playRecords": play_records,
                        "favorites": favorites,
                        "followings": followings,
                        "todayUpdated": today_updated,
                        "searchHistory": search_history,
                        "skipConfigs": skip_configs,
                        "password": user_password,
                    }

                # 覆盖站长密码
                admin_entry = export_data["data"]["userData"].get(admin_username)
                if admin_entry is not None:
                    admin_entry["password"] = env_raw(PASSWORD)

                yield _sse({"type": "stage", "message": "正在压缩并加密数据...", "percent": 92})

                encrypted_data = encode_backup(json_dump(export_data), password)
                filename = _backup_filename(datetime.now())

                yield _sse({"type": "file_start", "filename": filename, "percent": 95})

                for i in range(0, len(encrypted_data), CHUNK_SIZE):
                    yield _sse({"type": "chunk", "data": encrypted_data[i : i + CHUNK_SIZE]})

                yield _sse({"type": "file_end", "percent": 100})
                yield _sse(
                    {
                        "type": "done",
                        "message": "数据导出成功",
                        "exportedUsers": total_users,
                        "filename": filename,
                        "percent": 100,
                    }
                )
            except Exception as error:  # noqa: BLE001 — the TS catch inside the stream
                print("数据导出失败:", error)
                yield _sse({"type": "error", "message": str(error) or "导出失败"})

        return StreamingResponse(
            events(),
            media_type="text/event-stream; charset=utf-8",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    except Exception as error:  # noqa: BLE001
        print("数据导出失败:", error)
        return json_response({"error": str(error) or "导出失败"}, 500)


# ---------------------------------------------------------------------------
# POST /api/admin/data_migration/import
# ---------------------------------------------------------------------------


async def import_(request: Request):
    if env(STORAGE_TYPE, "sqlite") == "localstorage":
        return json_response({"error": "不支持本地存储进行数据迁移"}, 400)

    auth_info = get_auth_info_from_cookie(request)
    if not auth_info or not auth_info.get("username"):
        return json_response({"error": "未登录"}, 401)
    if auth_info["username"] != env_raw(USERNAME):
        return json_response({"error": "权限不足，只有站长可以导入数据"}, 401)

    form = await request.form()
    upload = form.get("file")
    password = form.get("password")

    if upload is None:
        return json_response({"error": "请选择备份文件"}, 400)
    if not password:
        return json_response({"error": "请提供解密密码"}, 400)

    raw = await _read_upload(upload)
    if raw is None:
        return json_response(
            {"error": f"备份文件过大，最大 {_size_label(_import_limit())}"}, 400
        )
    encrypted_data = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)

    try:
        decompressed_data = decode_backup(encrypted_data, str(password))
    except DecryptFailed:
        return json_response({"error": "解密失败，请检查密码是否正确"}, 400)
    except Exception:  # noqa: BLE001 — not base64-of-DEFLATE, see crypto.py
        return json_response({"error": "备份文件格式错误"}, 400)

    try:
        import_data = json.loads(decompressed_data)
    except Exception:  # noqa: BLE001
        return json_response({"error": "备份文件格式错误"}, 400)

    if not isinstance(import_data, dict) or not isinstance(import_data.get("data"), dict):
        return json_response({"error": "备份文件格式无效"}, 400)
    payload = import_data["data"]
    if not payload.get("adminConfig") or not payload.get("userData"):
        return json_response({"error": "备份文件格式无效"}, 400)

    user_data = payload["userData"]
    usernames = list(user_data)
    total_users = len(usernames)

    total_records = sum(_count_user_records(user_data[name]) for name in usernames)

    async def events():
        processed_records = 0
        pending: list[bytes] = []

        def send_progress(data_type: str, done: int, total: int):
            nonlocal processed_records
            percent = (
                min(100, _js_round(((processed_records + done) / total_records) * 100))
                if total_records > 0
                else 0
            )
            return _sse(
                {
                    "type": "progress",
                    "dataType": data_type,
                    "done": done,
                    "total": total,
                    "percent": percent,
                    "message": f"{data_type} {done}/{total}",
                }
            )

        try:
            yield _sse({"type": "stage", "message": "正在清空现有数据...", "percent": 0})
            await Db.clear_all_data()

            yield _sse({"type": "stage", "message": "正在导入管理员配置...", "percent": 2})
            payload["adminConfig"] = config_self_check(payload["adminConfig"])
            await Db.save_admin_config(payload["adminConfig"])
            await set_cached_config(payload["adminConfig"])

            user_index = 0
            for username in usernames:
                user = user_data[username]
                user_index += 1

                yield _sse(
                    {
                        "type": "user",
                        "message": f"正在处理用户 {user_index}/{total_users}: {username}",
                        "username": username,
                        "userIndex": user_index,
                        "totalUsers": total_users,
                    }
                )

                if not isinstance(user, dict):
                    continue

                if user.get("password"):
                    yield _sse({"type": "detail", "message": f"注册用户: {username}"})
                    await Db.register_user(username, str(user["password"]))

                if user.get("playRecords"):
                    entries = _keyed_entries(user["playRecords"])
                    yield _sse(
                        {
                            "type": "detail",
                            "message": f"导入播放记录 ({len(entries)} 条)...",
                            "dataType": "播放记录",
                            "total": len(entries),
                        }
                    )
                    batched = await Db.batch_import_play_records(
                        username, entries, lambda done, total: pending.append(
                            send_progress("播放记录", done, total)
                        )
                    )
                    if not batched:
                        await _run_in_batches(
                            entries,
                            lambda key_record: Db.set_play_record(username, key_record[0], key_record[1]),
                            lambda done: pending.append(send_progress("播放记录", done, len(entries))),
                        )
                    for _event in pending:
                        yield _event
                    pending.clear()
                    processed_records += len(entries)

                if user.get("favorites"):
                    entries = _keyed_entries(user["favorites"])
                    yield _sse(
                        {
                            "type": "detail",
                            "message": f"导入收藏夹 ({len(entries)} 条)...",
                            "dataType": "收藏夹",
                            "total": len(entries),
                        }
                    )
                    batched = await Db.batch_import_favorites(
                        username, entries, lambda done, total: pending.append(
                            send_progress("收藏夹", done, total)
                        )
                    )
                    if not batched:
                        await _run_in_batches(
                            entries,
                            lambda key_favorite: Db.set_favorite(username, key_favorite[0], key_favorite[1]),
                            lambda done: pending.append(send_progress("收藏夹", done, len(entries))),
                        )
                    for _event in pending:
                        yield _event
                    pending.clear()
                    processed_records += len(entries)

                if user.get("followings"):
                    entries = _source_id_entries(user["followings"])
                    yield _sse(
                        {
                            "type": "detail",
                            "message": f"导入追更 ({len(entries)} 条)...",
                            "dataType": "追更",
                            "total": len(entries),
                        }
                    )
                    batched = await Db.batch_import_followings(
                        username, entries, lambda done, total: pending.append(
                            send_progress("追更", done, total)
                        )
                    )
                    if not batched:
                        await _run_in_batches(
                            entries,
                            lambda key_following: Db.set_following(username, key_following[0], key_following[1]),
                            lambda done: pending.append(send_progress("追更", done, len(entries))),
                        )
                    for _event in pending:
                        yield _event
                    pending.clear()
                    processed_records += len(entries)

                if user.get("todayUpdated"):
                    today_updated = user["todayUpdated"]
                    if isinstance(today_updated, dict) and not today_updated.get("date"):
                        # 兜底处理：确保 date 字段存在
                        today_updated["date"] = _iso_now()[:10]
                    today_date = today_updated.get("date") if isinstance(today_updated, dict) else None
                    yield _sse(
                        {
                            "type": "detail",
                            "message": f"导入今日新更 (date={today_date})...",
                            "dataType": "今日新更",
                        }
                    )
                    batched = await Db.batch_import_today_updated(username, today_updated)
                    if not batched:
                        await Db.set_today_updated(username, today_updated)
                    processed_records += 1
                    yield send_progress("今日新更", 1, 1)

                if isinstance(user.get("searchHistory"), list):
                    history = list(user["searchHistory"])
                    yield _sse(
                        {
                            "type": "detail",
                            "message": f"导入搜索历史 ({len(history)} 条)...",
                            "dataType": "搜索历史",
                            "total": len(history),
                        }
                    )
                    batched = await Db.batch_import_search_history(
                        username, history, lambda done, total: pending.append(
                            send_progress("搜索历史", done, total)
                        )
                    )
                    if not batched:
                        history_done = 0
                        for keyword in reversed(history):  # 反转以保持顺序
                            await Db.add_search_history(username, keyword)
                            history_done += 1
                            yield send_progress("搜索历史", history_done, len(history))
                    for _event in pending:
                        yield _event
                    pending.clear()
                    processed_records += len(history)

                if user.get("skipConfigs"):
                    entries = _source_id_entries(user["skipConfigs"])
                    yield _sse(
                        {
                            "type": "detail",
                            "message": f"导入跳过配置 ({len(entries)} 条)...",
                            "dataType": "跳过配置",
                            "total": len(entries),
                        }
                    )
                    batched = await Db.batch_import_skip_configs(
                        username, entries, lambda done, total: pending.append(
                            send_progress("跳过配置", done, total)
                        )
                    )
                    if not batched:
                        await _run_in_batches(
                            entries,
                            lambda key_config: Db.set_skip_config(
                                username, *_split_key(key_config[0]), key_config[1]
                            ),
                            lambda done: pending.append(send_progress("跳过配置", done, len(entries))),
                        )
                    for _event in pending:
                        yield _event
                    pending.clear()
                    processed_records += len(entries)

                yield _sse({"type": "detail", "message": f"用户 {username} 处理完成"})

            yield _sse(
                {
                    "type": "done",
                    "message": "数据导入成功",
                    "importedUsers": total_users,
                    "timestamp": import_data.get("timestamp"),
                    "serverVersion": import_data.get("serverVersion")
                    if isinstance(import_data.get("serverVersion"), str)
                    else "未知版本",
                    "percent": 100,
                }
            )
        except Exception as error:  # noqa: BLE001 — the TS catch inside the stream
            print("数据导入失败:", error)
            yield _sse({"type": "error", "message": str(error) or "导入失败"})

    return StreamingResponse(
        events(),
        media_type="text/event-stream; charset=utf-8",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


ROUTES = [
    ("/api/admin/data_migration/export", export, ["POST"]),
    ("/api/admin/data_migration/import", import_, ["POST"]),
]
