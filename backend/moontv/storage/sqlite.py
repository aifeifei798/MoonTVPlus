"""SQLite storage backend — a direct port of `src/lib/d1.db.ts`.

D1 was already a table-based SQLite implementation, so this is the most
faithful translation of MoonTV's storage layer: the table names, column names,
`ON CONFLICT(...) DO UPDATE SET` clauses and the `search_history` created_at
trick all come across unchanged. Two consequences worth keeping:

  * `d1-init.sql`'s schema is reused verbatim (SQLite accepts `UNIQUE(a, b, c)`
    group constraints and `false`/`true` literals), so a D1 dump can be loaded
    into this backend by pointing `MOONTV_DB_PATH` at a converted database.
  * `batch_import_*` exist here for the same reason they exist in D1 — the
    data-migration import route uses them when available and falls back to
    per-item writes otherwise (`moontv/storage/db.py` reports which path ran,
    mirroring DbManager's `return false`). They are one transaction per 100-row
    chunk here, and the chunk size is kept at 100 because the import route's
    SSE progress events are emitted once per chunk.

The sqlite3 calls are synchronous. Every public method is `async` so the
redis/kvrocks backends (real network I/O) and the route handlers share one
shape; the small bodies never `await`, which also means a single call is
atomic with respect to other requests on the event loop. The two bodies that
are not small — the 100-row-chunk batch import and `clear_all_data` — are
offloaded with `asyncio.to_thread`, because a data-migration import of tens of
thousands of rows otherwise blocks every other request for the whole import.
WAL (set in `__init__`) is what makes that safe: a reader on the event loop
does not wait for the writer in the worker thread.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from ..utils import now_ms, split_key, strip_none


SEARCH_HISTORY_LIMIT = 20
BATCH_LIMIT = 100

# ---------------------------------------------------------------------------
# 表结构：d1-init.sql 的 CREATE TABLE 段，逐行（去掉 Cloudflare 的 status SELECTs）
# ---------------------------------------------------------------------------
SCHEMA_TABLES = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT UNIQUE NOT NULL,
  password TEXT NOT NULL,
  role TEXT DEFAULT 'user',
  banned BOOLEAN DEFAULT false,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS play_records (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  source TEXT NOT NULL,
  video_id TEXT NOT NULL,
  title TEXT NOT NULL,
  source_name TEXT NOT NULL,
  year TEXT,
  cover TEXT,
  episode_index INTEGER,
  total_episodes INTEGER,
  play_time INTEGER,
  total_time INTEGER,
  save_time INTEGER,
  search_title TEXT,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(user_id, source, video_id),
  FOREIGN KEY (user_id) REFERENCES users (id)
);

CREATE TABLE IF NOT EXISTS favorites (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  source TEXT NOT NULL,
  video_id TEXT NOT NULL,
  title TEXT NOT NULL,
  source_name TEXT NOT NULL,
  year TEXT,
  cover TEXT,
  total_episodes INTEGER,
  save_time INTEGER,
  search_title TEXT,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(user_id, source, video_id),
  FOREIGN KEY (user_id) REFERENCES users (id)
);

CREATE TABLE IF NOT EXISTS followings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  source TEXT NOT NULL,
  video_id TEXT NOT NULL,
  title TEXT NOT NULL,
  source_name TEXT NOT NULL,
  year TEXT,
  cover TEXT,
  total_episodes INTEGER,
  watched_episodes INTEGER,
  save_time INTEGER,
  search_title TEXT,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(user_id, source, video_id),
  FOREIGN KEY (user_id) REFERENCES users (id)
);

CREATE TABLE IF NOT EXISTS search_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  keyword TEXT NOT NULL,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(user_id, keyword),
  FOREIGN KEY (user_id) REFERENCES users (id)
);

CREATE TABLE IF NOT EXISTS skip_configs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  source TEXT NOT NULL,
  video_id TEXT NOT NULL,
  enable BOOLEAN DEFAULT false,
  intro_time INTEGER DEFAULT 0,
  outro_time INTEGER DEFAULT 0,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(user_id, source, video_id),
  FOREIGN KEY (user_id) REFERENCES users (id)
);

CREATE TABLE IF NOT EXISTS today_updated (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL UNIQUE,
  date TEXT NOT NULL,
  data TEXT NOT NULL,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (user_id) REFERENCES users (id)
);

CREATE TABLE IF NOT EXISTS admin_config (
  id INTEGER PRIMARY KEY DEFAULT 1,
  config TEXT NOT NULL,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""

# ---------------------------------------------------------------------------
# 索引：热点查询（按 user 取列表、按 user+title 去重）在数据量大后是全表扫描。
# 单独一段，`__init__` 每次 connect 都无条件执行——既有库不会因为“表已存在”
# 而跳过索引（executescript 里的 CREATE INDEX IF NOT EXISTS 只在建表那次跑过）。
# ---------------------------------------------------------------------------
SCHEMA_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_play_records_user_updated ON play_records(user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_play_records_user_title ON play_records(user_id, title);
CREATE INDEX IF NOT EXISTS idx_favorites_user ON favorites(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_followings_user ON followings(user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_search_history_user ON search_history(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_skip_user ON skip_configs(user_id, source, video_id);
"""

# 全量脚本（表 + 索引），保留给想一次跑完的调用方。`__init__` 不执行这一份，
# 而是分两段执行：见那里的注释。
SCHEMA = SCHEMA_TABLES + SCHEMA_INDEXES

# `JSON.stringify` — no spaces, raw unicode. `strip_none` first: TS objects never
# carry an explicit `undefined`, and every writer here reads optional fields with
# `.get()`, so a `None` is a field MoonTV would have omitted.
_dump = lambda obj: json.dumps(strip_none(obj), ensure_ascii=False, separators=(",", ":"))


def _or(value: Any, default: Any) -> Any:
    """TS `x || default`."""
    return value or default


def _coalesce(value: Any, default: Any) -> Any:
    """TS `x ?? default` — only null/undefined fall back, so 0 survives."""
    return default if value is None else value


class SqliteStorage:
    """Implements the `Storage` contract in `moontv/storage/__init__.py`."""

    def __init__(self, db_path: str = "moontv.db") -> None:
        # check_same_thread=False: uvicorn may run different requests on
        # different worker threads. Bodies never await, so calls stay atomic.
        # isolation_level=None (autocommit): every statement is durable as soon
        # as it returns, the way each D1 query was. The implicit transactions
        # of the default isolation level would otherwise hold every write
        # uncommitted until a commit that never came — invisible to other
        # connections, blocking their writes with "database is locked", and
        # rolled back on restart. Only `_run_batch` manages its own
        # transaction (one per 100-row chunk, like D1's `batch()`).
        self._conn = sqlite3.connect(db_path, check_same_thread=False, isolation_level=None)
        # WAL: readers never block on a writer and vice versa, which is what
        # makes the `asyncio.to_thread` offloads below safe to run while the
        # event loop keeps answering reads. `journal_mode` is persistent (it
        # is stored in the database file), `synchronous=NORMAL` is not, so both
        # are set on every connect. NORMAL still fsyncs at commit; it only
        # drops the intermediate fsyncs that DELETE mode paid per chunk.
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        # 两段都跑，且都在每次构造时跑（不判断库文件是否新建）：`SCHEMA_TABLES`
        # 对既有库是 no-op，`SCHEMA_INDEXES` 的 CREATE INDEX IF NOT EXISTS 才是
        # 升级路径——老库缺的这些索引必须在进程启动时就补上，而不是等某张表被
        # 重建。`SCHEMA`（表+索引合成的一份）只是给外部一次跑完用的入口。
        self._conn.executescript(SCHEMA_TABLES)
        self._conn.executescript(SCHEMA_INDEXES)

    # ---------- 用户相关 ----------
    def _get_user_id(self, username: str) -> int | None:
        row = self._conn.execute(
            "SELECT id FROM users WHERE username = ?", (username,)
        ).fetchone()
        return row[0] if row else None

    def _ensure_user(self, username: str) -> int:
        user_id = self._get_user_id(username)
        if user_id:
            return user_id

        self._conn.execute(
            "INSERT INTO users (username, password, role) VALUES (?, ?, ?)",
            (username, "", "user"),
        )
        user_id = self._get_user_id(username)
        if not user_id:
            raise RuntimeError("Failed to create user")
        return user_id

    async def register_user(self, user_name: str, password: str) -> None:
        self._conn.execute(
            "INSERT INTO users (username, password) VALUES (?, ?)", (user_name, password)
        )

    async def verify_user(self, user_name: str, password: str) -> bool:
        return (
            self._conn.execute(
                "SELECT id FROM users WHERE username = ? AND password = ?",
                (user_name, password),
            ).fetchone()
            is not None
        )

    async def check_user_exist(self, user_name: str) -> bool:
        return self._get_user_id(user_name) is not None

    async def change_password(self, user_name: str, new_password: str) -> None:
        user_id = self._get_user_id(user_name)
        if not user_id:
            raise LookupError("User not found")
        self._conn.execute("UPDATE users SET password = ? WHERE id = ?", (new_password, user_id))

    async def delete_user(self, user_name: str) -> None:
        user_id = self._get_user_id(user_name)
        if not user_id:
            return
        # 单事务包裹 7 条 DELETE（autocommit 下显式 BEGIN/COMMIT），避免中途失败留半删用户。
        self._conn.execute("BEGIN")
        try:
            for table in (
                "play_records",
                "favorites",
                "followings",
                "search_history",
                "skip_configs",
                "today_updated",
            ):
                self._conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
            self._conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        except Exception:
            self._conn.execute("ROLLBACK")
            raise
        else:
            self._conn.execute("COMMIT")

    async def get_all_users(self) -> list[str]:
        return [row[0] for row in self._conn.execute("SELECT username FROM users").fetchall()]

    async def get_user_password(self, user_name: str) -> str | None:
        """The D1 branch of the export route's local `getUserPassword` helper."""
        row = self._conn.execute(
            "SELECT password FROM users WHERE username = ?", (user_name,)
        ).fetchone()
        return _or(row[0] if row else None, None)

    # ---------- 播放记录 ----------
    def _record_from_row(self, row) -> dict:
        (
            _id,
            _user_id,
            _source,
            _video_id,
            title,
            source_name,
            year,
            cover,
            episode_index,
            total_episodes,
            play_time,
            total_time,
            save_time,
            search_title,
            _created_at,
            _updated_at,
        ) = row
        return {
            "title": title,
            "source_name": source_name,
            "year": year,
            "cover": cover,
            "index": episode_index,
            "total_episodes": total_episodes,
            "play_time": play_time,
            "total_time": total_time,
            "save_time": save_time,
            "search_title": search_title,
        }

    async def get_play_record(self, user_name: str, key: str) -> Any:
        source, video_id = split_key(key)
        if not source or not video_id:
            return None
        user_id = self._get_user_id(user_name)
        if not user_id:
            return None
        row = self._conn.execute(
            "SELECT * FROM play_records WHERE user_id = ? AND source = ? AND video_id = ?",
            (user_id, source, video_id),
        ).fetchone()
        return self._record_from_row(row) if row else None

    async def set_play_record(self, user_name: str, key: str, record: Any) -> None:
        source, video_id = split_key(key)
        if not source or not video_id:
            raise ValueError("Invalid key format for play record")
        user_id = self._ensure_user(user_name)

        # 删除同名的旧记录
        if record.get("title"):
            self._conn.execute(
                "DELETE FROM play_records WHERE user_id = ? AND title = ? "
                "AND NOT (source = ? AND video_id = ?)",
                (user_id, record["title"], source, video_id),
            )

        self._conn.execute(
            """
            INSERT INTO play_records
            (user_id, source, video_id, title, source_name, year, cover, episode_index,
             total_episodes, play_time, total_time, save_time, search_title)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, source, video_id)
            DO UPDATE SET
              title = excluded.title,
              source_name = excluded.source_name,
              year = excluded.year,
              cover = excluded.cover,
              episode_index = excluded.episode_index,
              total_episodes = excluded.total_episodes,
              play_time = excluded.play_time,
              total_time = excluded.total_time,
              save_time = excluded.save_time,
              search_title = excluded.search_title,
              updated_at = CURRENT_TIMESTAMP
            """,
            (
                user_id,
                source,
                video_id,
                _or(record.get("title"), ""),
                _or(record.get("source_name"), ""),
                _or(record.get("year"), ""),
                _or(record.get("cover"), ""),
                _coalesce(record.get("index"), 0),
                _coalesce(record.get("total_episodes"), 0),
                _coalesce(record.get("play_time"), 0),
                _coalesce(record.get("total_time"), 0),
                _coalesce(record.get("save_time"), now_ms()),
                _or(record.get("search_title"), ""),
            ),
        )

    async def get_all_play_records(self, user_name: str) -> dict[str, Any]:
        user_id = self._get_user_id(user_name)
        if not user_id:
            return {}
        records: dict[str, Any] = {}
        for row in self._conn.execute(
            "SELECT * FROM play_records WHERE user_id = ?", (user_id,)
        ).fetchall():
            key = f"{row[2]}+{row[3]}"
            records[key] = self._record_from_row(row)
        return records

    async def delete_play_record(self, user_name: str, key: str) -> None:
        source, video_id = split_key(key)
        if not source or not video_id:
            return
        user_id = self._get_user_id(user_name)
        if not user_id:
            return
        self._conn.execute(
            "DELETE FROM play_records WHERE user_id = ? AND source = ? AND video_id = ?",
            (user_id, source, video_id),
        )

    async def delete_all_play_records(self, user_name: str) -> None:
        """单条 DELETE 清空用户全部播放记录（替代 get_all + 逐条删）。"""
        user_id = self._get_user_id(user_name)
        if not user_id:
            return
        self._conn.execute("DELETE FROM play_records WHERE user_id = ?", (user_id,))

    # ---------- 收藏 ----------
    def _favorite_from_row(self, row) -> dict:
        (_id, _uid, _src, _vid, title, source_name, year, cover, total_episodes, save_time, search_title, *_rest) = row
        return {
            "title": title,
            "source_name": source_name,
            "year": year,
            "cover": cover,
            "total_episodes": total_episodes,
            "save_time": save_time,
            "search_title": search_title,
        }

    async def get_favorite(self, user_name: str, key: str) -> Any:
        source, video_id = split_key(key)
        if not source or not video_id:
            return None
        user_id = self._get_user_id(user_name)
        if not user_id:
            return None
        row = self._conn.execute(
            "SELECT * FROM favorites WHERE user_id = ? AND source = ? AND video_id = ?",
            (user_id, source, video_id),
        ).fetchone()
        return self._favorite_from_row(row) if row else None

    async def set_favorite(self, user_name: str, key: str, favorite: Any) -> None:
        source, video_id = split_key(key)
        if not source or not video_id:
            raise ValueError("Invalid key format for favorite")
        user_id = self._ensure_user(user_name)
        self._conn.execute(
            """
            INSERT INTO favorites
            (user_id, source, video_id, title, source_name, year, cover, total_episodes, save_time, search_title)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, source, video_id)
            DO UPDATE SET
              title = excluded.title,
              source_name = excluded.source_name,
              year = excluded.year,
              cover = excluded.cover,
              total_episodes = excluded.total_episodes,
              save_time = excluded.save_time,
              search_title = excluded.search_title
            """,
            (
                user_id,
                source,
                video_id,
                _or(favorite.get("title"), ""),
                _or(favorite.get("source_name"), ""),
                _or(favorite.get("year"), ""),
                _or(favorite.get("cover"), ""),
                _coalesce(favorite.get("total_episodes"), 0),
                _coalesce(favorite.get("save_time"), now_ms()),
                _or(favorite.get("search_title"), ""),
            ),
        )

    async def get_all_favorites(self, user_name: str) -> dict[str, Any]:
        user_id = self._get_user_id(user_name)
        if not user_id:
            return {}
        favorites: dict[str, Any] = {}
        for row in self._conn.execute("SELECT * FROM favorites WHERE user_id = ?", (user_id,)):
            favorites[f"{row[2]}+{row[3]}"] = self._favorite_from_row(row)
        return favorites

    async def delete_favorite(self, user_name: str, key: str) -> None:
        source, video_id = split_key(key)
        if not source or not video_id:
            return
        user_id = self._get_user_id(user_name)
        if not user_id:
            return
        self._conn.execute(
            "DELETE FROM favorites WHERE user_id = ? AND source = ? AND video_id = ?",
            (user_id, source, video_id),
        )

    async def delete_all_favorites(self, user_name: str) -> None:
        """单条 DELETE 清空用户全部收藏。"""
        user_id = self._get_user_id(user_name)
        if not user_id:
            return
        self._conn.execute("DELETE FROM favorites WHERE user_id = ?", (user_id,))

    # ---------- 追更 ----------
    def _following_from_row(self, row) -> dict:
        (_id, _uid, _src, _vid, title, source_name, year, cover, total_episodes, watched_episodes, save_time, search_title, *_rest) = row
        return {
            "title": title,
            "source_name": source_name,
            "year": year,
            "cover": cover,
            "total_episodes": total_episodes,
            "watched_episodes": watched_episodes,
            "save_time": save_time,
            "search_title": search_title,
        }

    async def get_following(self, user_name: str, key: str) -> Any:
        source, video_id = split_key(key)
        if not source or not video_id:
            return None
        user_id = self._get_user_id(user_name)
        if not user_id:
            return None
        row = self._conn.execute(
            "SELECT * FROM followings WHERE user_id = ? AND source = ? AND video_id = ?",
            (user_id, source, video_id),
        ).fetchone()
        return self._following_from_row(row) if row else None

    async def set_following(self, user_name: str, key: str, following: Any) -> None:
        source, video_id = split_key(key)
        if not source or not video_id:
            raise ValueError("Invalid key format for following")
        user_id = self._ensure_user(user_name)
        self._conn.execute(
            """
            INSERT INTO followings
            (user_id, source, video_id, title, source_name, year, cover, total_episodes, watched_episodes, save_time, search_title)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, source, video_id)
            DO UPDATE SET
              title = excluded.title,
              source_name = excluded.source_name,
              year = excluded.year,
              cover = excluded.cover,
              total_episodes = excluded.total_episodes,
              watched_episodes = excluded.watched_episodes,
              save_time = excluded.save_time,
              search_title = excluded.search_title,
              updated_at = CURRENT_TIMESTAMP
            """,
            (
                user_id,
                source,
                video_id,
                _or(following.get("title"), ""),
                _or(following.get("source_name"), ""),
                _or(following.get("year"), ""),
                _or(following.get("cover"), ""),
                _coalesce(following.get("total_episodes"), 0),
                _coalesce(following.get("watched_episodes"), 0),
                _coalesce(following.get("save_time"), now_ms()),
                _or(following.get("search_title"), ""),
            ),
        )

    async def get_all_followings(self, user_name: str) -> dict[str, Any]:
        user_id = self._get_user_id(user_name)
        if not user_id:
            return {}
        followings: dict[str, Any] = {}
        for row in self._conn.execute("SELECT * FROM followings WHERE user_id = ?", (user_id,)):
            followings[f"{row[2]}+{row[3]}"] = self._following_from_row(row)
        return followings

    async def delete_following(self, user_name: str, key: str) -> None:
        source, video_id = split_key(key)
        if not source or not video_id:
            return
        user_id = self._get_user_id(user_name)
        if not user_id:
            return
        self._conn.execute(
            "DELETE FROM followings WHERE user_id = ? AND source = ? AND video_id = ?",
            (user_id, source, video_id),
        )

    async def delete_all_followings(self, user_name: str) -> None:
        """单条 DELETE 清空用户全部追更。"""
        user_id = self._get_user_id(user_name)
        if not user_id:
            return
        self._conn.execute("DELETE FROM followings WHERE user_id = ?", (user_id,))

    # ---------- 搜索历史 ----------
    async def get_search_history(self, user_name: str) -> list[str]:
        user_id = self._get_user_id(user_name)
        if not user_id:
            return []
        return [
            row[0]
            for row in self._conn.execute(
                "SELECT keyword FROM search_history WHERE user_id = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (user_id, SEARCH_HISTORY_LIMIT),
            )
        ]

    async def add_search_history(self, user_name: str, keyword: str) -> None:
        user_id = self._ensure_user(user_name)

        # 先删除已存在的相同关键词
        self._conn.execute(
            "DELETE FROM search_history WHERE user_id = ? AND keyword = ?", (user_id, keyword)
        )
        self._conn.execute(
            "INSERT INTO search_history (user_id, keyword) VALUES (?, ?)", (user_id, keyword)
        )
        # 保持搜索历史不超过限制
        self._conn.execute(
            "DELETE FROM search_history WHERE user_id = ? AND id NOT IN ("
            "SELECT id FROM search_history WHERE user_id = ? "
            "ORDER BY created_at DESC LIMIT ?)",
            (user_id, user_id, SEARCH_HISTORY_LIMIT),
        )

    async def delete_search_history(self, user_name: str, keyword: str | None = None) -> None:
        user_id = self._get_user_id(user_name)
        if not user_id:
            return
        if keyword:
            self._conn.execute(
                "DELETE FROM search_history WHERE user_id = ? AND keyword = ?", (user_id, keyword)
            )
        else:
            self._conn.execute("DELETE FROM search_history WHERE user_id = ?", (user_id,))

    # ---------- 管理员配置 ----------
    async def get_admin_config(self) -> Any:
        row = self._conn.execute(
            "SELECT config FROM admin_config WHERE id = 1"
        ).fetchone()
        if not row:
            return None
        try:
            return json.loads(row[0])
        except Exception as error:  # noqa: BLE001 — mirrors the TS catch-and-rethrow
            print("Failed to get admin config:", error)
            raise

    async def set_admin_config(self, config: Any) -> None:
        try:
            self._conn.execute(
                "INSERT OR REPLACE INTO admin_config (id, config) VALUES (1, ?)",
                (_dump(config),),
            )
        except Exception as error:  # noqa: BLE001
            print("Failed to set admin config:", error)
            raise

    # ---------- 跳过片头片尾配置 ----------
    def _skip_from_row(self, row) -> dict:
        (_id, _uid, _src, _vid, enable, intro_time, outro_time, *_rest) = row
        return {"enable": bool(enable), "intro_time": intro_time, "outro_time": outro_time}

    async def get_skip_config(self, user_name: str, source: str, id_: str) -> Any:
        user_id = self._get_user_id(user_name)
        if not user_id:
            return None
        row = self._conn.execute(
            "SELECT * FROM skip_configs WHERE user_id = ? AND source = ? AND video_id = ?",
            (user_id, source, id_),
        ).fetchone()
        return self._skip_from_row(row) if row else None

    async def set_skip_config(self, user_name: str, source: str, id_: str, config: Any) -> None:
        user_id = self._ensure_user(user_name)
        self._conn.execute(
            """
            INSERT INTO skip_configs (user_id, source, video_id, enable, intro_time, outro_time)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, source, video_id)
            DO UPDATE SET
              enable = excluded.enable,
              intro_time = excluded.intro_time,
              outro_time = excluded.outro_time,
              updated_at = CURRENT_TIMESTAMP
            """,
            (
                user_id,
                source,
                id_,
                1 if config.get("enable") else 0,
                _coalesce(config.get("intro_time"), 0),
                _coalesce(config.get("outro_time"), 0),
            ),
        )

    async def delete_skip_config(self, user_name: str, source: str, id_: str) -> None:
        user_id = self._get_user_id(user_name)
        if not user_id:
            return
        self._conn.execute(
            "DELETE FROM skip_configs WHERE user_id = ? AND source = ? AND video_id = ?",
            (user_id, source, id_),
        )

    async def get_all_skip_configs(self, user_name: str) -> dict[str, Any]:
        user_id = self._get_user_id(user_name)
        if not user_id:
            return {}
        configs: dict[str, Any] = {}
        for row in self._conn.execute("SELECT * FROM skip_configs WHERE user_id = ?", (user_id,)):
            configs[f"{row[2]}+{row[3]}"] = self._skip_from_row(row)
        return configs

    # ---------- “今日新更” ----------
    async def get_today_updated(self, user_name: str) -> Any:
        user_id = self._get_user_id(user_name)
        if not user_id:
            return None
        row = self._conn.execute("SELECT data FROM today_updated WHERE user_id = ?", (user_id,)).fetchone()
        if not row or not row[0]:
            return None
        try:
            return json.loads(row[0])
        except Exception as error:  # noqa: BLE001
            print('解析"今日新更"记录失败:', error)
            return None

    async def set_today_updated(self, user_name: str, record: Any) -> None:
        user_id = self._ensure_user(user_name)
        self._conn.execute(
            """
            INSERT INTO today_updated (user_id, date, data, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id)
            DO UPDATE SET
              date = excluded.date,
              data = excluded.data,
              updated_at = CURRENT_TIMESTAMP
            """,
            (user_id, record.get("date"), _dump(record)),
        )

    # ---------- 数据清理 ----------
    async def clear_all_data(self) -> None:
        # Same offload as `_run_batch_async`: this is the first thing the
        # data-migration import does, and on a database with real data the
        # eight DELETEs are the slowest single statement sequence in the route.
        await asyncio.to_thread(self._clear_all_data)

    def _clear_all_data(self) -> None:
        for table in (
            "play_records",
            "favorites",
            "followings",
            "search_history",
            "skip_configs",
            "today_updated",
            "users",
            "admin_config",
        ):
            self._conn.execute(f"DELETE FROM {table}")

    # ============================================================
    # 批量导入（数据迁移专用）
    # ============================================================

    def _run_batch(self, statements: list[tuple[str, tuple]], on_progress=None) -> None:
        """`runBatch` — one sqlite transaction per 100-row chunk.

        `on_progress(done, total)` fires once per chunk; the import route's SSE
        progress events depend on that callback, so the chunk size stays 100.
        The connection runs in autocommit mode (see `__init__`), so the chunk
        transaction is explicit: BEGIN/COMMIT here, ROLLBACK on failure, which
        is what D1's `batch()` gave each chunk.
        """
        done = 0
        for i in range(0, len(statements), BATCH_LIMIT):
            chunk = statements[i : i + BATCH_LIMIT]
            self._conn.execute("BEGIN")
            try:
                for sql, params in chunk:
                    self._conn.execute(sql, params)
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")
            done += len(chunk)
            if on_progress:
                on_progress(done, len(statements))

    async def _run_batch_async(self, statements: list[tuple[str, tuple]], on_progress=None) -> None:
        """`_run_batch` off the event loop.

        A data-migration import is tens of thousands of INSERTs; keeping it on
        the loop stalls every other request for the whole import. The body is
        sqlite3 C calls that release the GIL, and the import route's
        `on_progress` only appends to a plain list, so running it in a worker
        thread is safe. WAL (see `__init__`) is what lets the loop keep serving
        reads while this thread writes.
        """
        await asyncio.to_thread(self._run_batch, statements, on_progress)

    async def batch_import_play_records(
        self, user_name: str, entries: list[tuple[str, Any]], on_progress=None
    ) -> None:
        user_id = self._ensure_user(user_name)
        sql = """
            INSERT INTO play_records
            (user_id, source, video_id, title, source_name, year, cover, episode_index,
             total_episodes, play_time, total_time, save_time, search_title)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, source, video_id)
            DO UPDATE SET
              title = excluded.title,
              source_name = excluded.source_name,
              year = excluded.year,
              cover = excluded.cover,
              episode_index = excluded.episode_index,
              total_episodes = excluded.total_episodes,
              play_time = excluded.play_time,
              total_time = excluded.total_time,
              save_time = excluded.save_time,
              search_title = excluded.search_title,
              updated_at = CURRENT_TIMESTAMP
        """
        statements = []
        for key, record in entries:
            source, video_id = split_key(key)
            if not source or not video_id:
                continue
            statements.append(
                (
                    sql,
                    (
                        user_id,
                        source,
                        video_id,
                        _or(record.get("title"), ""),
                        _or(record.get("source_name"), ""),
                        _or(record.get("year"), ""),
                        _or(record.get("cover"), ""),
                        _coalesce(record.get("index"), 0),
                        _coalesce(record.get("total_episodes"), 0),
                        _coalesce(record.get("play_time"), 0),
                        _coalesce(record.get("total_time"), 0),
                        _coalesce(record.get("save_time"), now_ms()),
                        _or(record.get("search_title"), ""),
                    ),
                )
            )
        await self._run_batch_async(statements, on_progress)

    async def batch_import_favorites(
        self, user_name: str, entries: list[tuple[str, Any]], on_progress=None
    ) -> None:
        user_id = self._ensure_user(user_name)
        sql = """
            INSERT INTO favorites
            (user_id, source, video_id, title, source_name, year, cover, total_episodes, save_time, search_title)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, source, video_id)
            DO UPDATE SET
              title = excluded.title,
              source_name = excluded.source_name,
              year = excluded.year,
              cover = excluded.cover,
              total_episodes = excluded.total_episodes,
              save_time = excluded.save_time,
              search_title = excluded.search_title
        """
        statements = []
        for key, favorite in entries:
            source, video_id = split_key(key)
            if not source or not video_id:
                continue
            statements.append(
                (
                    sql,
                    (
                        user_id,
                        source,
                        video_id,
                        _or(favorite.get("title"), ""),
                        _or(favorite.get("source_name"), ""),
                        _or(favorite.get("year"), ""),
                        _or(favorite.get("cover"), ""),
                        _coalesce(favorite.get("total_episodes"), 0),
                        _coalesce(favorite.get("save_time"), now_ms()),
                        _or(favorite.get("search_title"), ""),
                    ),
                )
            )
        await self._run_batch_async(statements, on_progress)

    async def batch_import_followings(
        self, user_name: str, entries: list[tuple[str, Any]], on_progress=None
    ) -> None:
        user_id = self._ensure_user(user_name)
        sql = """
            INSERT INTO followings
            (user_id, source, video_id, title, source_name, year, cover, total_episodes, watched_episodes, save_time, search_title)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, source, video_id)
            DO UPDATE SET
              title = excluded.title,
              source_name = excluded.source_name,
              year = excluded.year,
              cover = excluded.cover,
              total_episodes = excluded.total_episodes,
              watched_episodes = excluded.watched_episodes,
              save_time = excluded.save_time,
              search_title = excluded.search_title,
              updated_at = CURRENT_TIMESTAMP
        """
        statements = []
        for key, following in entries:
            source, video_id = split_key(key)
            if not source or not video_id:
                continue
            statements.append(
                (
                    sql,
                    (
                        user_id,
                        source,
                        video_id,
                        _or(following.get("title"), ""),
                        _or(following.get("source_name"), ""),
                        _or(following.get("year"), ""),
                        _or(following.get("cover"), ""),
                        _coalesce(following.get("total_episodes"), 0),
                        _coalesce(following.get("watched_episodes"), 0),
                        _coalesce(following.get("save_time"), now_ms()),
                        _or(following.get("search_title"), ""),
                    ),
                )
            )
        await self._run_batch_async(statements, on_progress)

    async def batch_import_skip_configs(
        self, user_name: str, entries: list[tuple[str, Any]], on_progress=None
    ) -> None:
        user_id = self._ensure_user(user_name)
        sql = """
            INSERT INTO skip_configs (user_id, source, video_id, enable, intro_time, outro_time)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, source, video_id)
            DO UPDATE SET
              enable = excluded.enable,
              intro_time = excluded.intro_time,
              outro_time = excluded.outro_time,
              updated_at = CURRENT_TIMESTAMP
        """
        statements = []
        for key, config in entries:
            source, video_id = split_key(key)
            if not source or not video_id:
                continue
            statements.append(
                (
                    sql,
                    (
                        user_id,
                        source,
                        video_id,
                        1 if config.get("enable") else 0,
                        _coalesce(config.get("intro_time"), 0),
                        _coalesce(config.get("outro_time"), 0),
                    ),
                )
            )
        await self._run_batch_async(statements, on_progress)

    async def batch_import_search_history(
        self, user_name: str, keywords: list[str], on_progress=None
    ) -> None:
        """`created_at` is written explicitly, newest first, 1s apart.

        `get_search_history` orders by `created_at DESC`; if a bulk insert left
        every row on the same timestamp the original order would be unrecoverable.
        SQLite compares the `'YYYY-MM-DD HH:MM:SS'` UTC strings lexicographically,
        which matches the D1 port's `toISOString()` formatting.
        """
        user_id = self._ensure_user(user_name)
        base_time = now_ms()
        statements = []
        seen: set[str] = set()
        for i, keyword in enumerate(keywords):
            if not keyword or keyword in seen:
                continue
            # First occurrence wins (index 0 is the newest): a duplicate later
            # in the payload must not backdate the row to an older timestamp.
            seen.add(keyword)
            created = _iso_utc(base_time - i * 1000)
            statements.append(
                (
                    "INSERT INTO search_history (user_id, keyword, created_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(user_id, keyword) DO UPDATE SET created_at = excluded.created_at",
                    (user_id, keyword, created),
                )
            )
        await self._run_batch_async(statements, on_progress)

    async def batch_import_today_updated(self, user_name: str, record: Any) -> None:
        user_id = self._ensure_user(user_name)
        self._conn.execute(
            """
            INSERT INTO today_updated (user_id, date, data, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id)
            DO UPDATE SET
              date = excluded.date,
              data = excluded.data,
              updated_at = CURRENT_TIMESTAMP
            """,
            (user_id, record.get("date"), _dump(record)),
        )


def _iso_utc(ms: int) -> str:
    """`new Date(ms).toISOString().replace('T', ' ').slice(0, 19)`."""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat(sep=" ")[0:19]
