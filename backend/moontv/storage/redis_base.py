"""Port of `src/lib/redis-base.db.ts` — the Redis/Kvrocks storage layer.

Upstream's `redis.db.ts` and `kvrocks.db.ts` are 14 lines each and differ only in
the env var they read (`REDIS_URL` / `KVROCKS_URL`), the log label and the
`Symbol.for` slot, so every command lives here and the two subclasses only pick
those three values.

Key layout (this is what makes an existing MoonTV deployment's Redis readable):

    u:<user>:pr:<source+id>   play record   (JSON string)
    u:<user>:fav:<source+id>  favorite      (JSON string)
    u:<user>:fol:<source+id>  following     (JSON string)
    u:<user>:pwd              password, stored in the clear like upstream's
    u:<user>:sh               search history, a LIST, newest first, LTRIMed to 20
    u:<user>:skip:<source>+<id>
    u:<user>:today_updated
    admin:config              the whole AdminConfig blob

Client-level differences that are *not* behavior changes: node's client has to be
`connect()`ed before a command works, so upstream opens it eagerly and re-opens it
every 5 s on failure; python's client opens itself on the first command and its
connection pool throws away a dead socket, so the socket heals one command
earlier and needs no background loop. node's `pingInterval` is python's
`health_check_interval`, and node's `reconnectStrategy` (exponential backoff to
30 s) is subsumed by the same `with_retry` wrapper the TS runs every command
through. `clientName` is a log label in upstream's config, not the `CLIENT NAME`
command, so it is never sent.

One upstream quirk is kept byte-exact, and it is a trap worth naming: `addSearchHistory`
calls `lRem(key, 0, keyword)`, which is the three-argument `LREM key count element`
form. `count = 0` is *not* "remove nothing" — per the command's own spec it means
"remove all matches", which is what makes the dedup work. Writing `lrem(key, keyword)`
instead would send the two-argument form and, for a keyword that happens to be an
integer, be parsed as `LREM key count <missing-value>`; the port keeps the three
arguments so the same bytes go on the wire.

`BaseRedisStorage` has no `deleteAll*` and no `batchImport*` methods, so `db.ts`
  falls back to per-item deletes and the data-migration import takes its
  non-batch path. The port keeps that by *not* defining them, and `db.py`'s
  `getattr` checks are what make the fallback reachable.
"""

from __future__ import annotations

import asyncio
import errno as _errno
import json
import re
from typing import Any

from ..utils import strip_none
from redis.asyncio import Redis

# 搜索历史最大条数
SEARCH_HISTORY_LIMIT = 20

# `Symbol.for('__MOONTV_REDIS_CLIENT__')` is one process-wide slot on node's
# `global`, so two storages built with the same symbol share one socket. A dict
# keyed by the symbol's description is the same registry.
_GLOBAL_CLIENTS: dict[str, Redis] = {}

_USER_PWD_RE = re.compile(r"^u:(.+?):pwd$")
_SKIP_KEY_RE = re.compile(r"^u:.+?:skip:(.+)$")


def ensure_string(value: Any) -> str:
    """`String(value)`."""
    return str(value)


def ensure_string_array(value: Any) -> list[str]:
    """`value.map((item) => String(item))`."""
    return [ensure_string(item) for item in value]


def _js_replace_first(text: str, old: str, new: str) -> str:
    """JS `String.replace(str, str)` replaces the FIRST occurrence, not all."""
    return text.replace(old, new, 1)


def _dump(obj: Any) -> str:
    """`JSON.stringify(x)` — no spaces, raw unicode, `undefined` dropped."""
    return json.dumps(strip_none(obj), ensure_ascii=False, separators=(",", ":"))


def _is_connection_error(err: BaseException) -> bool:
    """The TS test: message has Connection/ECONNREFUSED/ENOTFOUND, or the code is
    ECONNRESET/EPIPE.

    Node's own message is `Connection refused (Connection refused)` and the port
    has to recognize the same set of failures, not the same spelling: redis-python
    writes `Error 111 connecting to 127.0.0.1:9. Connect call failed ...`, whose
    word is lowercase and whose code is the bare errno, so the name test is
    case-insensitive and the errno is looked up in both directions.
    """
    message = str(err).lower()
    if "connection" in message or "connecting" in message or "econnrefused" in message or "enotfound" in message:
        return True
    code = getattr(err, "code", None)
    if code in ("ECONNRESET", "EPIPE", "ENOTFOUND", "ECONNREFUSED"):
        return True
    number = getattr(err, "errno", None)
    if isinstance(number, int):
        return _errno.errorcode.get(number) in ("ECONNRESET", "EPIPE", "ENOTFOUND", "ECONNREFUSED")
    return False


class BaseRedisStorage:
    """`BaseRedisStorage implements IStorage`."""

    def __init__(self, config: dict, global_symbol: str) -> None:
        self.client: Redis = _create_redis_client(config["url"], config["client_name"], global_symbol)
        self._client_name = config["client_name"]
        self.with_retry = _create_retry_wrapper(config["client_name"], lambda: self.client)

    # ---------- 播放记录 ----------
    def _pr_key(self, user: str, key: str) -> str:
        return f"u:{user}:pr:{key}"  # u:username:pr:source+id

    async def get_play_record(self, user_name: str, key: str) -> Any:
        val = await self.with_retry(lambda: self.client.get(self._pr_key(user_name, key)))
        return json.loads(val) if val else None

    async def set_play_record(self, user_name: str, key: str, record: Any) -> None:
        # 删除同名的旧记录
        if record.get("title"):
            pattern = f"u:{user_name}:pr:*"
            all_keys = await self.with_retry(lambda: self.client.keys(pattern))
            for full_key in all_keys:
                val = await self.with_retry(lambda: self.client.get(full_key))
                if not val:
                    continue
                existing = json.loads(val)
                if existing.get("title") == record["title"] and full_key != self._pr_key(
                    user_name, key
                ):
                    await self.with_retry(lambda: self.client.delete(full_key))
        await self.with_retry(
            lambda: self.client.set(self._pr_key(user_name, key), _dump(record))
        )

    async def get_all_play_records(self, user_name: str) -> dict[str, Any]:
        pattern = f"u:{user_name}:pr:*"
        keys = await self.with_retry(lambda: self.client.keys(pattern))
        if not keys:
            return {}
        values = await self.with_retry(lambda: self.client.mget(keys))
        result: dict[str, Any] = {}
        prefix = f"u:{user_name}:pr:"
        for full_key, raw in zip(keys, values):
            if raw:
                result[ensure_string(_js_replace_first(full_key, prefix, ""))] = json.loads(raw)
        return result

    async def delete_play_record(self, user_name: str, key: str) -> None:
        await self.with_retry(lambda: self.client.delete(self._pr_key(user_name, key)))

    # ---------- 收藏 ----------
    def _fav_key(self, user: str, key: str) -> str:
        return f"u:{user}:fav:{key}"

    async def get_favorite(self, user_name: str, key: str) -> Any:
        val = await self.with_retry(lambda: self.client.get(self._fav_key(user_name, key)))
        return json.loads(val) if val else None

    async def set_favorite(self, user_name: str, key: str, favorite: Any) -> None:
        await self.with_retry(
            lambda: self.client.set(self._fav_key(user_name, key), _dump(favorite))
        )

    async def get_all_favorites(self, user_name: str) -> dict[str, Any]:
        pattern = f"u:{user_name}:fav:*"
        keys = await self.with_retry(lambda: self.client.keys(pattern))
        if not keys:
            return {}
        values = await self.with_retry(lambda: self.client.mget(keys))
        result: dict[str, Any] = {}
        prefix = f"u:{user_name}:fav:"
        for full_key, raw in zip(keys, values):
            if raw:
                result[ensure_string(_js_replace_first(full_key, prefix, ""))] = json.loads(raw)
        return result

    async def delete_favorite(self, user_name: str, key: str) -> None:
        await self.with_retry(lambda: self.client.delete(self._fav_key(user_name, key)))

    # ---------- 追更 ----------
    def _fol_key(self, user: str, key: str) -> str:
        return f"u:{user}:fol:{key}"

    async def get_following(self, user_name: str, key: str) -> Any:
        val = await self.with_retry(lambda: self.client.get(self._fol_key(user_name, key)))
        return json.loads(val) if val else None

    async def set_following(self, user_name: str, key: str, following: Any) -> None:
        await self.with_retry(
            lambda: self.client.set(self._fol_key(user_name, key), _dump(following))
        )

    async def get_all_followings(self, user_name: str) -> dict[str, Any]:
        pattern = f"u:{user_name}:fol:*"
        keys = await self.with_retry(lambda: self.client.keys(pattern))
        if not keys:
            return {}
        values = await self.with_retry(lambda: self.client.mget(keys))
        result: dict[str, Any] = {}
        prefix = f"u:{user_name}:fol:"
        for full_key, raw in zip(keys, values):
            if raw:
                result[ensure_string(_js_replace_first(full_key, prefix, ""))] = json.loads(raw)
        return result

    async def delete_following(self, user_name: str, key: str) -> None:
        await self.with_retry(lambda: self.client.delete(self._fol_key(user_name, key)))

    # ---------- 用户注册 / 登录 ----------
    def _user_pwd_key(self, user: str) -> str:
        return f"u:{user}:pwd"

    async def register_user(self, user_name: str, password: str) -> None:
        # 简单存储明文密码，生产环境应加密
        await self.with_retry(lambda: self.client.set(self._user_pwd_key(user_name), password))

    async def verify_user(self, user_name: str, password: str) -> bool:
        stored = await self.with_retry(lambda: self.client.get(self._user_pwd_key(user_name)))
        if stored is None:
            return False
        return ensure_string(stored) == password

    async def check_user_exist(self, user_name: str) -> bool:
        exists = await self.with_retry(lambda: self.client.exists(self._user_pwd_key(user_name)))
        return exists == 1

    async def change_password(self, user_name: str, new_password: str) -> None:
        # 简单存储明文密码，生产环境应加密
        await self.with_retry(lambda: self.client.set(self._user_pwd_key(user_name), new_password))

    async def delete_user(self, user_name: str) -> None:
        # 删除用户及其所有数据
        await self.with_retry(lambda: self.client.delete(self._user_pwd_key(user_name)))
        await self.with_retry(lambda: self.client.delete(self._sh_key(user_name)))

        for pattern in (
            f"u:{user_name}:pr:*",
            f"u:{user_name}:fav:*",
            f"u:{user_name}:fol:*",
            f"u:{user_name}:skip:*",
        ):
            keys = await self.with_retry(lambda: self.client.keys(pattern))
            if keys:
                await self.with_retry(lambda: self.client.delete(*keys))

        await self.with_retry(lambda: self.client.delete(self._today_updated_key(user_name)))

    # ---------- 搜索历史 ----------
    def _sh_key(self, user: str) -> str:
        return f"u:{user}:sh"  # u:username:sh

    async def get_search_history(self, user_name: str) -> list[str]:
        result = await self.with_retry(lambda: self.client.lrange(self._sh_key(user_name), 0, -1))
        return ensure_string_array(result)

    async def add_search_history(self, user_name: str, keyword: str) -> None:
        key = self._sh_key(user_name)
        # 先去重（count 0 = remove every match, so this really is the dedup）
        await self.with_retry(lambda: self.client.lrem(key, 0, ensure_string(keyword)))
        # 插入到最前
        await self.with_retry(lambda: self.client.lpush(key, ensure_string(keyword)))
        # 限制最大长度
        await self.with_retry(lambda: self.client.ltrim(key, 0, SEARCH_HISTORY_LIMIT - 1))

    async def delete_search_history(self, user_name: str, keyword: str | None = None) -> None:
        key = self._sh_key(user_name)
        if keyword:
            await self.with_retry(lambda: self.client.lrem(key, 0, ensure_string(keyword)))
        else:
            await self.with_retry(lambda: self.client.delete(key))

    # ---------- 获取全部用户 ----------
    async def get_all_users(self) -> list[str]:
        keys = await self.with_retry(lambda: self.client.keys("u:*:pwd"))
        users = []
        for k in keys:
            match = _USER_PWD_RE.match(k)
            if match:
                users.append(ensure_string(match.group(1)))
        return users

    # ---------- 管理员配置 ----------
    def _admin_config_key(self) -> str:
        return "admin:config"

    async def get_admin_config(self) -> Any:
        val = await self.with_retry(lambda: self.client.get(self._admin_config_key()))
        return json.loads(val) if val else None

    async def set_admin_config(self, config: Any) -> None:
        await self.with_retry(
            lambda: self.client.set(self._admin_config_key(), _dump(config))
        )

    # ---------- 跳过片头片尾配置 ----------
    def _skip_config_key(self, user: str, source: str, id_: str) -> str:
        return f"u:{user}:skip:{source}+{id_}"

    async def get_skip_config(self, user_name: str, source: str, id_: str) -> Any:
        val = await self.with_retry(
            lambda: self.client.get(self._skip_config_key(user_name, source, id_))
        )
        return json.loads(val) if val else None

    async def set_skip_config(
        self, user_name: str, source: str, id_: str, config: Any
    ) -> None:
        await self.with_retry(
            lambda: self.client.set(
                self._skip_config_key(user_name, source, id_), _dump(config)
            )
        )

    async def delete_skip_config(self, user_name: str, source: str, id_: str) -> None:
        await self.with_retry(
            lambda: self.client.delete(self._skip_config_key(user_name, source, id_))
        )

    async def get_all_skip_configs(self, user_name: str) -> dict[str, Any]:
        pattern = f"u:{user_name}:skip:*"
        keys = await self.with_retry(lambda: self.client.keys(pattern))
        if not keys:
            return {}
        values = await self.with_retry(lambda: self.client.mget(keys))
        configs: dict[str, Any] = {}
        for key, value in zip(keys, values):
            if not value:
                continue
            match = _SKIP_KEY_RE.match(key)
            if match:
                configs[match.group(1)] = json.loads(value)
        return configs

    # ---------- “今日新更” ----------
    def _today_updated_key(self, user: str) -> str:
        return f"u:{user}:today_updated"  # u:username:today_updated

    async def get_today_updated(self, user_name: str) -> Any:
        val = await self.with_retry(
            lambda: self.client.get(self._today_updated_key(user_name))
        )
        return json.loads(val) if val else None

    async def set_today_updated(self, user_name: str, record: Any) -> None:
        await self.with_retry(
            lambda: self.client.set(self._today_updated_key(user_name), _dump(record))
        )

    # ---------- 数据清理 ----------
    async def clear_all_data(self) -> None:
        try:
            all_users = await self.get_all_users()
            for username in all_users:
                await self.delete_user(username)
            await self.with_retry(lambda: self.client.delete(self._admin_config_key()))
            print("所有数据已清空")
        except Exception as error:  # noqa: BLE001 — the TS catch, which logs and rethrows
            print("清空数据失败:", error)
            raise RuntimeError("清空数据失败") from error

    # ---------- 用户密码（db.ts 直读 client.get） ----------
    async def get_user_password(self, user_name: str) -> Any:
        """`getUserPassword` — upstream reached `(db as any).storage.client.get(...)`.

        `db.py` keeps the helper's "a failure is not an error, it is no password"
        contract, so only the read itself lives here.
        """
        return await self.with_retry(lambda: self.client.get(self._user_pwd_key(user_name)))


def _create_redis_client(url: Any, client_name: str, global_symbol: str) -> Redis:
    """`createRedisClient(config, globalSymbol)`."""
    client = _GLOBAL_CLIENTS.get(global_symbol)
    if client is None:
        if not url:
            raise RuntimeError(f"{client_name}_URL env variable not set")

        # node's clientConfig, translated: connectTimeout 10 s, pingInterval 30 s
        # (= python's health_check_interval), noDelay has no python equivalent.
        # `clientName` stays a log label — sending CLIENT NAME would change the
        # socket's identity, which upstream never did.
        client = Redis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=10,
            health_check_interval=30,
        )

        _GLOBAL_CLIENTS[global_symbol] = client
    return client


def _create_retry_wrapper(client_name: str, get_client):
    """`createRetryWrapper(clientName, getClient)` — the `withRetry` every command runs through."""

    async def with_retry(operation, max_retries=3):
        for i in range(max_retries):
            try:
                return await operation()
            except Exception as err:  # noqa: BLE001 — the TS catch, which inspects and rethrows
                is_last_attempt = i == max_retries - 1
                if _is_connection_error(err) and not is_last_attempt:
                    print(f"{client_name} operation failed, retrying... ({i + 1}/{max_retries})")
                    print("Error:", err)

                    # 等待一段时间后重试
                    await asyncio.sleep(1.0 * (i + 1))

                    # 尝试重新连接（python's client reopens on the next command,
                    # so a PING is the equivalent probe of node's `if (!client.isOpen)`）
                    try:
                        client = get_client()
                        await client.ping()
                    except Exception as reconnect_error:  # noqa: BLE001
                        print("Failed to reconnect:", reconnect_error)

                    continue

                raise

        raise RuntimeError("Max retries exceeded")

    return with_retry
