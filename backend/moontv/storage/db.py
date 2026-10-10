"""Port of `src/lib/db.ts` — storage-instance singleton + `DbManager` wrapper.

`Db` mirrors `export const db = new DbManager()`: it forwards to the active
backend and keeps the TS capability checks (`typeof (storage as any).x ===
'function'`), because the data-migration import route branches on whether the
batch path ran (`true`) or the caller must fall back to per-item writes
(`false`). sqlite implements the batch methods; a future redis/kvrocks backend
may not, and the fallback stays reachable through the same check.
"""

from __future__ import annotations

from ..env import DB_PATH, STORAGE_TYPE, env
from .sqlite import SqliteStorage

_storage_instance = None


def create_storage():
    """`createStorage()` — `localstorage` and `d1` collapse onto sqlite.

    There is no server-side `localstorage` backend in MoonTV either (it returned
    `null`), and `d1` was already SQLite, so both map to the same implementation
    here. `upstash` (REST) and Cloudflare D1 bindings are not ported; see PLAN.md.
    """
    match env(STORAGE_TYPE, "sqlite"):
        case "redis":
            try:
                from .redis import RedisStorage  # B4: not written yet
            except ModuleNotFoundError:
                raise RuntimeError(
                    "STORAGE_TYPE=redis is not ported yet (see PLAN.md); "
                    "use sqlite (default) instead"
                ) from None
            return RedisStorage()
        case "kvrocks":
            try:
                from .kvrocks import KvrocksStorage  # B4: not written yet
            except ModuleNotFoundError:
                raise RuntimeError(
                    "STORAGE_TYPE=kvrocks is not ported yet (see PLAN.md); "
                    "use sqlite (default) instead"
                ) from None
            return KvrocksStorage()
        case _:
            return SqliteStorage(env(DB_PATH, "moontv.db"))


def get_storage():
    """`getStorage()` — one instance per process."""
    global _storage_instance
    if _storage_instance is None:
        _storage_instance = create_storage()
    return _storage_instance


class DbManager:
    """`class DbManager` — forwards to the active backend."""

    @property
    def storage(self):
        return get_storage()

    # ---------- 播放记录 ----------
    async def get_play_record(self, user_name, key):
        return await get_storage().get_play_record(user_name, key)

    async def set_play_record(self, user_name, key, record):
        await get_storage().set_play_record(user_name, key, record)

    async def get_all_play_records(self, user_name):
        return await get_storage().get_all_play_records(user_name)

    async def delete_play_record(self, user_name, key):
        await get_storage().delete_play_record(user_name, key)

    # ---------- 收藏 ----------
    async def get_favorite(self, user_name, key):
        return await get_storage().get_favorite(user_name, key)

    async def set_favorite(self, user_name, key, favorite):
        await get_storage().set_favorite(user_name, key, favorite)

    async def get_all_favorites(self, user_name):
        return await get_storage().get_all_favorites(user_name)

    async def delete_favorite(self, user_name, key):
        await get_storage().delete_favorite(user_name, key)

    # ---------- 追更 ----------
    async def get_following(self, user_name, key):
        return await get_storage().get_following(user_name, key)

    async def set_following(self, user_name, key, following):
        await get_storage().set_following(user_name, key, following)

    async def get_all_followings(self, user_name):
        return await get_storage().get_all_followings(user_name)

    async def delete_following(self, user_name, key):
        await get_storage().delete_following(user_name, key)

    # ---------- 用户 ----------
    async def register_user(self, user_name, password):
        await get_storage().register_user(user_name, password)

    async def verify_user(self, user_name, password):
        return await get_storage().verify_user(user_name, password)

    async def check_user_exist(self, user_name):
        return await get_storage().check_user_exist(user_name)

    async def change_password(self, user_name, new_password):
        await get_storage().change_password(user_name, new_password)

    async def delete_user(self, user_name):
        await get_storage().delete_user(user_name)

    # ---------- 搜索历史 ----------
    async def get_search_history(self, user_name):
        return await get_storage().get_search_history(user_name)

    async def add_search_history(self, user_name, keyword):
        await get_storage().add_search_history(user_name, keyword)

    async def delete_search_history(self, user_name, keyword=None):
        await get_storage().delete_search_history(user_name, keyword)

    # ---------- 用户列表 ----------
    async def get_all_users(self):
        storage = get_storage()
        method = getattr(storage, "get_all_users", None)
        return await method() if method else []

    async def get_user_password(self, user_name):
        """`getUserPassword` — the data-migration export's per-user read.

        Upstream reached through `(db as any).storage.client.get('u:<name>:pwd')`
        for the redis backends and ran a D1 `SELECT`; sqlite has the typed column,
        so the wrapper asks the backend and keeps the helper's "a failure is not
        an error, it is no password" contract the export relies on.
        """
        storage = get_storage()
        method = getattr(storage, "get_user_password", None)
        if not method:
            return None
        try:
            return await method(user_name)
        except Exception as error:  # noqa: BLE001 — the TS catch, which logs and returns null
            print(f"获取用户 {user_name} 密码失败:", error)
            return None

    # ---------- 管理员配置 ----------
    async def get_admin_config(self):
        storage = get_storage()
        method = getattr(storage, "get_admin_config", None)
        return await method() if method else None

    async def save_admin_config(self, config):
        """`saveAdminConfig` — the admin routes call this name, not `setAdminConfig`."""
        storage = get_storage()
        method = getattr(storage, "set_admin_config", None)
        if method:
            await method(config)

    # ---------- 跳过片头片尾配置 ----------
    async def get_skip_config(self, user_name, source, id_):
        storage = get_storage()
        method = getattr(storage, "get_skip_config", None)
        return await method(user_name, source, id_) if method else None

    async def set_skip_config(self, user_name, source, id_, config):
        storage = get_storage()
        method = getattr(storage, "set_skip_config", None)
        if method:
            await method(user_name, source, id_, config)

    async def delete_skip_config(self, user_name, source, id_):
        storage = get_storage()
        method = getattr(storage, "delete_skip_config", None)
        if method:
            await method(user_name, source, id_)

    async def get_all_skip_configs(self, user_name):
        storage = get_storage()
        method = getattr(storage, "get_all_skip_configs", None)
        return await method(user_name) if method else {}

    # ---------- “今日新更” ----------
    async def get_today_updated(self, user_name):
        storage = get_storage()
        method = getattr(storage, "get_today_updated", None)
        return await method(user_name) if method else None

    async def set_today_updated(self, user_name, record):
        storage = get_storage()
        method = getattr(storage, "set_today_updated", None)
        if method:
            await method(user_name, record)

    # ---------- 数据清理 ----------
    async def clear_all_data(self):
        storage = get_storage()
        method = getattr(storage, "clear_all_data", None)
        if method:
            await method()
        else:
            raise RuntimeError("存储类型不支持清空数据操作")

    # ---------- 批量导入（数据迁移专用） ----------
    async def batch_import_play_records(self, username, entries, on_progress=None):
        storage = get_storage()
        method = getattr(storage, "batch_import_play_records", None)
        if method:
            await method(username, entries, on_progress)
            return True
        return False

    async def batch_import_favorites(self, username, entries, on_progress=None):
        storage = get_storage()
        method = getattr(storage, "batch_import_favorites", None)
        if method:
            await method(username, entries, on_progress)
            return True
        return False

    async def batch_import_followings(self, username, entries, on_progress=None):
        storage = get_storage()
        method = getattr(storage, "batch_import_followings", None)
        if method:
            await method(username, entries, on_progress)
            return True
        return False

    async def batch_import_skip_configs(self, username, entries, on_progress=None):
        storage = get_storage()
        method = getattr(storage, "batch_import_skip_configs", None)
        if method:
            await method(username, entries, on_progress)
            return True
        return False

    async def batch_import_search_history(self, username, keywords, on_progress=None):
        storage = get_storage()
        method = getattr(storage, "batch_import_search_history", None)
        if method:
            await method(username, keywords, on_progress)
            return True
        return False

    async def batch_import_today_updated(self, username, record):
        storage = get_storage()
        method = getattr(storage, "batch_import_today_updated", None)
        if method:
            await method(username, record)
            return True
        return False


# `export const db = new DbManager()`. The instance, not the class: every route
# module calls `Db.get_play_record(user, key)` the way the TS did, and a class
# would have eaten the first argument as `self`.
Db = DbManager()
