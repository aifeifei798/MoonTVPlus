"""Stale-while-revalidate cache for the douban routes.

Douban rate-limits datacenter IPs, so a cold failure used to mean an empty
page. Every douban route now caches its successful `list` under the full
upstream target URL (top250 under `top250:<start>`), and on upstream failure
serves the last good `list` with an `X-Douban-Stale: 1` header instead of a
500. Same `{code, message, list}` shape either way; only the header tells a
stale response apart.

Storage is a `douban_cache(key, body, updated_at)` table in the same sqlite
file (`MOONTV_DB_PATH`), created idempotently here rather than in
`storage/sqlite.py`'s schema so the cache stays an internal detail of this
module. Non-sqlite deployments (redis/kvrocks, still unported) get a no-op:
fresh data when upstream works, the old 500 when it doesn't. Connections are
short-lived per call — the routes already run on the event loop and must not
share a cursor.
"""

from __future__ import annotations

import json
import sqlite3
import time

from .env import DB_PATH, STORAGE_TYPE, env


def _db_path() -> str | None:
    if env(STORAGE_TYPE, "sqlite") not in ("sqlite", "d1", "localstorage"):
        return None
    return env(DB_PATH, "moontv.db")


def _connect():
    path = _db_path()
    if path is None:
        return None
    conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS douban_cache ("
        " `key` TEXT PRIMARY KEY, body TEXT NOT NULL, updated_at INTEGER NOT NULL)"
    )
    return conn


def get_douban_cache(key: str) -> tuple[list | None, int | None]:
    """`(list, age_seconds)` — `(None, None)` on miss or non-sqlite."""
    conn = _connect()
    if conn is None:
        return None, None
    try:
        row = conn.execute(
            "SELECT body, updated_at FROM douban_cache WHERE `key` = ?", (key,)
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None, None
    try:
        return json.loads(row[0]), int(time.time()) - int(row[1])
    except Exception:  # noqa: BLE001 — a corrupt row is a miss
        return None, None


def set_douban_cache(key: str, list_: list) -> None:
    """Best effort: a cache write must never fail the request that made it."""
    conn = _connect()
    if conn is None:
        return
    try:
        conn.execute(
            "INSERT INTO douban_cache (`key`, body, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(`key`) DO UPDATE SET body = excluded.body, "
            "updated_at = excluded.updated_at",
            (key, json.dumps(list_, ensure_ascii=False), int(time.time())),
        )
    except Exception as error:  # noqa: BLE001
        print("douban cache write failed:", error)
    finally:
        conn.close()
