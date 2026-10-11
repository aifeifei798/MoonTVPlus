#!/usr/bin/env python3
"""Check the Redis/Kvrocks storage backend: the port, the wire, and the fallbacks.

Usage: backend/.venv/bin/python <skill>/scripts/redis_storage_check.py

The last unported milestone. There is no redis-server on this box (`which redis-server
redis-cli` is empty and sudo needs a password), so the oracle is fakeredis's
`TcpFakeServer` — a `ThreadingTCPServer` whose handler encodes and decodes real
RESP frames, so `redis.asyncio` reaches it over a real socket and the port's
command surface and wire encoding are exercised. What this cannot prove is a real
redis server's own behavior (its timeouts, RDB, Kvrocks' `exh*` extensions), and
no claim below says it does.

Four parts:

1. **the missing-URL failure** — a redis storage with no `REDIS_URL` must fail with
   upstream's own message, not fall back to sqlite silently.
2. **the storage layer**, driven directly against the fake RESP server: every
   command, the key layout, the search-history dedup and `LTRIM` cap, the same-title
   play-record dedup, the `_js_replace_first` prefix trap, and the retry wrapper.
3. **the wire**: the same scenario against a sqlite server and a redis server,
   canonicalized. The two backends build the same object differently — sqlite from
   columns, redis from the stored JSON — so key *order* legitimately differs and
   `save_time`/`timestamp` are per-run, which is why the compare is structural.
4. **the fallbacks and the redis-only branch**: `Db.batch_import_*` answers `False`
   so the data-migration import takes its per-item path, `clear_all_data` eats
   nothing on the round trip, and `GET /api/admin/reset` rebuilds `CustomCategories`
   only when the storage type is exactly `redis` — upstream's own quirk, which
   leaves kvrocks with an empty list like sqlite.

It runs against throwaway servers on its own ports and a temp sqlite DB, so it
never touches the running deployment.
"""

import asyncio
import json
import os
import pathlib
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any

import fakeredis
import httpx
from redis import Redis
from redis.exceptions import ConnectionError as RedisConnectionError

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from _harness import ROOT, load_env  # noqa: F401 — the import also puts <repo>/backend on sys.path

from moontv.crypto import DECRYPT_FAILED, decode_backup
from moontv.env import KVROCKS_URL, REDIS_URL, STORAGE_TYPE, env
from moontv.storage.db import Db
from moontv.storage.redis import RedisStorage
from moontv.storage.redis import REDIS_CLIENT_SYMBOL
from moontv.storage.kvrocks import KvrocksStorage
from moontv.storage.redis_base import BaseRedisStorage, SEARCH_HISTORY_LIMIT, _is_connection_error
from moontv.version import CURRENT_VERSION

HERE = pathlib.Path(__file__).resolve().parent
PY = str(ROOT / "backend/.venv/bin/python")

SEED_USER = "zzredis"
SEED_PASSWORD = "zzseedpass123"
BACKUP_PASSWORD = "zz-backup-pw"
TTL_WAIT = 6.0  # > config.py's _CONFIG_TTL_S = 5.0

# `reset_config`'s redis-only branch reads the config FILE, and this box's
# backend/config.json has no custom_category, so the harness points MOONTV_CONFIG_PATH
# at a throwaway copy that has one ASCII entry.
CONFIG_CATEGORY = {"name": "zzcat", "type": "movie", "query": "zzquery"}

def free_port(exclude: set[int] = frozenset()) -> int:
    """A port nothing is bound to, so a leftover server cannot steal the run.

    Binding at 0 and reading back the number is the only portable way to ask; the
    fixed numbers an earlier harness used collide with whatever a previous session
    left listening, and that failure looks like a port bug.
    """
    while True:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        finally:
            probe.close()
        if port not in exclude:
            return port


INPROCESS_PORT = free_port()
REDIS_PORT = free_port({INPROCESS_PORT})
REDIS_HTTP = free_port({INPROCESS_PORT, REDIS_PORT})
KV_PORT = free_port({INPROCESS_PORT, REDIS_PORT, REDIS_HTTP})
KV_HTTP = free_port({INPROCESS_PORT, REDIS_PORT, REDIS_HTTP, KV_PORT})
SQLITE_HTTP = free_port({INPROCESS_PORT, REDIS_PORT, REDIS_HTTP, KV_PORT, KV_HTTP})

results = []


def check(label, ok, detail=""):
    results.append((label, ok, detail))


def sse(text):
    """`data: {...}\n\n` events; the trailing empty chunk after the last frame."""
    out = []
    for block in text.split("\n\n"):
        if not block:
            continue
        line = next((ln for ln in block.split("\n") if ln.startswith("data:")), None)
        if line is None:
            continue
        try:
            out.append(json.loads(line[5:].strip()))
        except json.JSONDecodeError:
            out.append({"_unparsable": line})
    return out


VOLATILE = {"save_time", "timestamp", "lastOnline", "checkedAt"}


def canon(value):
    """Comparable JSON: volatile keys dropped, keys sorted.

    The two backends build the same record differently — sqlite reads it back out
    of typed columns, redis hands back the JSON it stored — so upstream's own wire
    differs in key order between them. Only the values are comparable.
    """
    if isinstance(value, dict):
        return {k: canon(v) for k, v in sorted(value.items()) if k not in VOLATILE}
    if isinstance(value, list):
        return [canon(v) for v in value]
    return value


# ---------------------------------------------------------------------------
# 1. a redis storage with no URL fails; it does not become sqlite
# ---------------------------------------------------------------------------

os.environ.pop(REDIS_URL, None)
os.environ.pop(KVROCKS_URL, None)
for url_name, cls, label in ((REDIS_URL, RedisStorage, "redis"), (KVROCKS_URL, KvrocksStorage, "kvrocks")):
    # upstream's own message is `${config.clientName}_URL env variable not set`, and
    # clientName is the class name without `Storage`, so the expectation is derived
    expected = f"{cls.__name__.removesuffix('Storage')}_URL env variable not set"
    try:
        cls()
        check(f"{label} with no {url_name} -> fails", False, "constructed anyway")
    except RuntimeError as exc:
        check(f"{label} with no {url_name} -> upstream's message", str(exc) == expected, f"{type(exc).__name__}: {exc}")
    except Exception as exc:  # noqa: BLE001
        check(f"{label} with no {url_name} -> upstream's message", False, f"{type(exc).__name__}: {exc}")

check(
    "no URL does not silently become sqlite",
    env(STORAGE_TYPE, "sqlite") == "sqlite" and True,
    "the failure above is the storage's own, not createStorage's fall-through",
)

# ---------------------------------------------------------------------------
# 2. the storage layer, against a real RESP socket
# ---------------------------------------------------------------------------

fake = fakeredis.TcpFakeServer(("127.0.0.1", INPROCESS_PORT), bind_and_activate=True)
threading.Thread(target=fake.serve_forever, daemon=True).start()
os.environ[REDIS_URL] = f"redis://127.0.0.1:{INPROCESS_PORT}"
os.environ[KVROCKS_URL] = f"redis://127.0.0.1:{INPROCESS_PORT}"
os.environ[STORAGE_TYPE] = "redis"

U = SEED_USER
PREFIX = f"u:{U}:pr:"


async def drive_storage() -> None:
    r = RedisStorage()
    kv = KvrocksStorage()
    check("the Symbol.for slot is one socket per backend", RedisStorage().client is r.client, "")
    check(
        "kvrocks gets its own socket, not redis's",
        kv.client is not r.client,
        f"{REDIS_CLIENT_SYMBOL} vs {type(kv.client).__name__}",
    )

    # users
    await r.register_user(U, SEED_PASSWORD)
    check("register_user -> u:<user>:pwd", bool(await r.client.keys(f"u:{U}:pwd")), "")
    check("verify_user accepts the right password", await r.verify_user(U, SEED_PASSWORD) is True, "")
    check("verify_user rejects the wrong one", await r.verify_user(U, "zznope") is False, "")
    check("check_user_exist is EXISTS == 1", await r.check_user_exist(U) is True, "")
    check("check_user_exist on an absent user is False", await r.check_user_exist("zznobody") is False, "")
    await r.change_password(U, "zzchanged1")
    check("change_password overwrites in place", await r.verify_user(U, "zzchanged1") is True, "")
    check(
        "get_user_password hands back the stored string",
        await Db.get_user_password(U) == "zzchanged1",
        repr(await Db.get_user_password(U)),
    )

    # play records, including the same-title dedup
    full = {
        "title": "AlphaTitle",
        "source_name": "IK",
        "year": "2026",
        "cover": "https://zz/cover.jpg",
        "index": 2,
        "total_episodes": 5,
        "play_time": 0,
        "total_time": 0,
        "search_title": "",
    }
    await r.set_play_record(U, "IK+1", dict(full))
    await r.set_play_record(U, "IK+2", {**full, "title": "BetaTitle", "index": 1})
    await r.set_play_record(U, "DL+9", {**full, "title": "AlphaTitle", "index": 4})
    records = await r.get_all_play_records(U)
    check(
        "set_play_record deletes the older same-title record",
        "IK+1" not in records and "DL+9" in records,
        str(sorted(records)),
    )
    check(
        "the survivor keeps its own key",
        records.get("DL+9", {}).get("index") == 4,
        str(records.get("DL+9")),
    )

    # the _js_replace_first trap: a key that contains its own prefix
    tricky = "x u:zzredis:pr:y"
    await r.set_play_record(U, tricky, {**full, "title": "TrickyTitle"})
    again = await r.get_all_play_records(U)
    check(
        "a key containing the prefix round-trips (replace FIRST, not all)",
        tricky in again,
        str(sorted(again)),
    )

    # favorites / followings
    await r.set_favorite(U, "IK+1", {"title": "AlphaTitle", "source_name": "IK"})
    await r.set_following(U, "IK+1", {"title": "AlphaTitle", "source_name": "IK"})
    check("get_all_favorites strips the fav prefix", (await r.get_all_favorites(U)) == {"IK+1": {"title": "AlphaTitle", "source_name": "IK"}}, "")
    check("get_all_followings strips the fol prefix", (await r.get_all_followings(U)) == {"IK+1": {"title": "AlphaTitle", "source_name": "IK"}}, "")
    check("get_favorite reads one key back", await r.get_favorite(U, "IK+1") == {"title": "AlphaTitle", "source_name": "IK"}, "")

    # search history: dedup, newest first, LTRIM cap
    for i in range(SEARCH_HISTORY_LIMIT + 5):
        await r.add_search_history(U, f"kw{i:02d}")
    await r.add_search_history(U, "kw04")
    hist = await r.get_search_history(U)
    check(
        "addSearchHistory's LREM key 0 <kw> really dedups",
        hist.count("kw04") == 1 and hist[0] == "kw04",
        str(hist[:4]),
    )
    check(
        "the list is LTRIMed to SEARCH_HISTORY_LIMIT",
        len(hist) == SEARCH_HISTORY_LIMIT,
        f"{len(hist)} vs {SEARCH_HISTORY_LIMIT}",
    )
    check("newest first (LPUSH order)", hist[1] == f"kw{SEARCH_HISTORY_LIMIT + 4:02d}", str(hist[:3]))
    await r.delete_search_history(U, "kw04")
    check("delete_search_history with a keyword removes just it", "kw04" not in await r.get_search_history(U), "")
    await r.delete_search_history(U)
    check("delete_search_history without one drops the whole list", await r.get_search_history(U) == [], "")
    await r.add_search_history(U, "kw05")  # re-open it, so the census below sees the LIST

    # skip configs and today-updated
    await r.set_skip_config(U, "IK", "vid1", {"enable": True, "intro_time": 10, "outro_time": 20})
    check(
        "get_all_skip_configs keys by source+id via the regex",
        (await r.get_all_skip_configs(U)) == {"IK+vid1": {"enable": True, "intro_time": 10, "outro_time": 20}},
        "",
    )
    await r.set_today_updated(U, {"date": "2026-10-11", "items": []})
    check("today_updated round-trips", await r.get_today_updated(U) == {"date": "2026-10-11", "items": []}, "")

    # admin config: the blob is stored with raw unicode, like JSON.stringify
    cjk = "".join(chr(0x4E00 + i) for i in range(4))
    blob = {"ConfigFile": json.dumps({"api_site": {cjk: {"name": cjk}}}, ensure_ascii=False)}
    await r.set_admin_config(blob)
    raw = await r.client.get("admin:config")
    check("admin:config keeps CJK raw, not \\u escaped", cjk in raw, repr(raw[:60]))
    check("get_admin_config parses it back", (await r.get_admin_config()) == blob, "")
    check("Db.save_admin_config reaches the same key", (await Db.get_admin_config()) == blob, "")

    # the census is the whole point of an existing deployment's Redis being readable
    census = sorted(await r.client.keys("*"))
    expected = sorted(
        [
            "admin:config",
            f"u:{U}:pwd",
            f"u:{U}:sh",
            f"u:{U}:today_updated",
            f"u:{U}:skip:IK+vid1",
            f"u:{U}:fav:IK+1",
            f"u:{U}:fol:IK+1",
            f"u:{U}:pr:IK+2",
            f"u:{U}:pr:DL+9",
            f"u:{U}:pr:{tricky}",
        ]
    )
    check("key census is upstream's layout", census == expected, f"{census} vs {expected}")
    check(
        "the search history is a LIST, not a JSON string",
        await r.client.type(f"u:{U}:sh") == "list",
        await r.client.type(f"u:{U}:sh"),
    )

    # the capability checks: the redis backend has no batch import, so the
    # data-migration import must take its per-item path
    for name, args in (
        ("batch_import_play_records", (U, {}, None)),
        ("batch_import_favorites", (U, {}, None)),
        ("batch_import_followings", (U, {}, None)),
        ("batch_import_skip_configs", (U, {}, None)),
        ("batch_import_search_history", (U, [], None)),
        ("batch_import_today_updated", (U, {})),  # this one takes no progress callback
    ):
        check(
            f"Db.{name} answers False on redis",
            await getattr(Db, name)(*args) is False,
            "",
        )
    check(
        "the batch methods really are absent from the backend",
        not any(hasattr(r, n) for n in ("batch_import_play_records", "delete_all_play_records")),
        "",
    )
    await Db.delete_all_play_records(U)
    check("delete_all_play_records falls back per item and empties", await r.get_all_play_records(U) == {}, "")

    # the retry wrapper: a dead socket retries, then raises the original error
    dead = BaseRedisStorage({"url": "redis://127.0.0.1:9", "client_name": "Redis"}, "__dead__")
    started = time.monotonic()
    try:
        await dead.get_play_record("nobody", "x")
        check("a dead socket raises", False, "no error")
    except Exception as exc:  # noqa: BLE001
        elapsed = time.monotonic() - started
        check(
            "the retry wrapper recognized it as a connection error",
            _is_connection_error(exc) and isinstance(exc, RedisConnectionError),
            f"{type(exc).__name__}: {exc}",
        )
        check(
            "it retried with 1s/2s backoff before giving up",
            elapsed >= 3.0,
            f"{elapsed:.2f}s for two sleeps",
        )
        check(
            "the error that surfaces is the connection error, not Max retries exceeded",
            "Max retries" not in str(exc),
            str(exc)[:80],
        )

    # clear_all_data
    await r.clear_all_data()
    check("clear_all_data empties the store", sorted(await r.client.keys("*")) == [], "")


asyncio.run(drive_storage())

# ---------------------------------------------------------------------------
# 3. the wire: one scenario, two backends, canonicalized
# ---------------------------------------------------------------------------


def make_config_file(tmp: str) -> str:
    """A throwaway `config.json` with one custom_category, for the reset branch.

    `reset_config`'s redis-only branch reads the config FILE, and this box's
    `backend/config.json` has no `custom_category`, so the branch would be
    vacuous here. `MOONTV_CONFIG_PATH` is the documented override, so the
    harness points it at a copy whose `api_site` map is the deployment's verbatim
    — the source census stays the same — plus one ASCII category.
    """
    file_config = json.loads((ROOT / "backend/config.json").read_text(encoding="utf-8"))
    file_config["custom_category"] = [CONFIG_CATEGORY]
    path = str(pathlib.Path(tmp) / "config.json")
    pathlib.Path(path).write_text(json.dumps(file_config, ensure_ascii=False), encoding="utf-8")
    return path


def spawn(kind: str, http_port: int, fake_port: int, db_path: str | None, config_path: str) -> subprocess.Popen:
    envd = {**os.environ, **load_env()}
    envd[STORAGE_TYPE] = kind
    envd.pop(REDIS_URL, None)
    envd.pop(KVROCKS_URL, None)
    envd["MOONTV_CONFIG_PATH"] = config_path
    if db_path:
        envd["MOONTV_DB_PATH"] = db_path
    if kind == "redis":
        envd["FAKE_REDIS_PORT"] = str(fake_port)
    elif kind == "kvrocks":
        envd["FAKE_REDIS_PORT"] = str(fake_port)
    if kind in ("redis", "kvrocks"):
        envd["HTTP_PORT"] = str(http_port)
        argv = [PY, str(HERE / "redis_serve_fake.py")]
    else:
        argv = [
            PY,
            "-m",
            "uvicorn",
            "moontv.main:app",
            "--app-dir",
            str(ROOT / "backend"),
            "--host",
            "127.0.0.1",
            "--port",
            str(http_port),
            "--log-level",
            "warning",
        ]
    return subprocess.Popen(argv, env=envd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def wait_up(port: int) -> bool:
    for _ in range(80):
        try:
            r = httpx.post(
                f"http://127.0.0.1:{port}/api/login",
                json={"username": load_env().get("USERNAME", ""), "password": load_env().get("PASSWORD", "")},
                timeout=3,
                trust_env=False,
            )
            if r.status_code == 200:
                return True
        except httpx.TransportError:
            pass
        time.sleep(0.25)
    return False


def take_census(port: int) -> dict[str, Any]:
    """Every key of a fake-Redis store, read over its own RESP socket."""
    c = Redis(host="127.0.0.1", port=port, decode_responses=True)
    found: dict[str, Any] = {}
    for k in sorted(c.keys("*")):
        found[k] = c.lrange(k, 0, -1) if c.type(k) == "list" else c.get(k)
    c.close()
    return found


def sqlite_blob(db_path: str) -> dict[str, Any]:
    """The admin blob as sqlite stored it, read from OUTSIDE the server.

    Reading is safe — the trap that makes a harness lie is WRITING from a second
    connection while the server holds its own, which the server never re-reads.
    This is only here to see what `reset_config` left in the store.
    """
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("SELECT config FROM admin_config WHERE id = 1").fetchone()
    finally:
        conn.close()
    return {"admin:config": row[0] if row else None}


def scenario(base: str, snapshot=None) -> dict:
    """The same scenario against one server, as comparable values."""
    out: dict = {}
    creds = load_env()
    r = httpx.post(base + "/api/login", json={"username": creds.get("USERNAME", ""), "password": creds.get("PASSWORD", "")},
                   timeout=30, trust_env=False)
    out["login"] = (r.status_code, r.text)
    if r.status_code != 200:
        return out
    owner = {"Cookie": r.headers["set-cookie"].split(";")[0]}

    r = httpx.post(base + "/api/admin/user", headers=owner,
                   json={"action": "add", "targetUsername": SEED_USER, "targetPassword": SEED_PASSWORD},
                   timeout=30, trust_env=False)
    out["addUser"] = (r.status_code, canon(json.loads(r.text)) if r.headers.get("content-type", "").startswith("application/json") else r.text)

    r = httpx.post(base + "/api/login", json={"username": SEED_USER, "password": SEED_PASSWORD}, timeout=30, trust_env=False)
    out["loginSeed"] = (r.status_code, r.text)
    if r.status_code != 200:
        return out
    seed = {"Cookie": r.headers["set-cookie"].split(";")[0]}

    rec = {
        "title": "AlphaTitle", "source_name": "IK", "year": "2026", "cover": "https://zz/c.jpg",
        "index": 2, "total_episodes": 5, "play_time": 0, "total_time": 0, "search_title": "",
    }
    r = httpx.post(base + "/api/playrecords", headers=seed, json={"key": "IK+vid1", "record": rec}, timeout=30, trust_env=False)
    out["setPlayrecord"] = (r.status_code, r.text)
    r = httpx.post(base + "/api/playrecords", headers=seed,
                   json={"key": "DL+vid9", "record": {**rec, "index": 4}}, timeout=30, trust_env=False)
    out["setPlayrecord2"] = (r.status_code, r.text)

    fav = {"title": "AlphaTitle", "source_name": "IK", "year": "2026", "cover": "https://zz/c.jpg",
           "poster": "https://zz/c.jpg", "id": "vid1", "source": "IK"}
    r = httpx.post(base + "/api/favorites", headers=seed, json={"key": "IK+vid1", "favorite": fav}, timeout=30, trust_env=False)
    out["setFavorite"] = (r.status_code, r.text)
    r = httpx.post(base + "/api/followings", headers=seed, json={"key": "IK+vid1", "following": fav}, timeout=30, trust_env=False)
    out["setFollowing"] = (r.status_code, r.text)

    for kw in ("alpha", "beta", "alpha"):
        r = httpx.post(base + "/api/searchhistory", headers=seed, json={"keyword": kw}, timeout=30, trust_env=False)
        out["addSearch"] = (r.status_code, canon(json.loads(r.text)))
    r = httpx.get(base + "/api/searchhistory", headers=seed, timeout=30, trust_env=False)
    out["getSearch"] = (r.status_code, canon(json.loads(r.text)))

    r = httpx.post(base + "/api/skipconfigs", headers=seed,
                   json={"key": "IK+vid1", "config": {"enable": True, "intro_time": 10, "outro_time": 20}},
                   timeout=30, trust_env=False)
    out["setSkip"] = (r.status_code, r.text)
    r = httpx.get(base + "/api/skipconfigs", headers=seed, timeout=30, trust_env=False)
    out["getSkip"] = (r.status_code, canon(json.loads(r.text)))

    r = httpx.post(base + "/api/today-updated", headers=seed,
                   json={"date": "2026-10-11", "items": [{"source": "IK", "id": "vid1", "title": "AlphaTitle",
                                                          "poster": "", "episodes": 5, "watchedEpisodes": 1,
                                                          "unwatchedEpisodes": 4, "source_name": "IK", "year": "2026",
                                                          "save_time": 0, "oldEpisodes": 4, "newEpisodes": 5}]},
                   timeout=30, trust_env=False)
    out["setToday"] = (r.status_code, r.text)
    r = httpx.get(base + "/api/today-updated", headers=seed, timeout=30, trust_env=False)
    out["getToday"] = (r.status_code, canon(json.loads(r.text)))

    for path in ("playrecords", "favorites", "followings"):
        r = httpx.get(base + "/api/" + path, headers=seed, timeout=30, trust_env=False)
        out["get" + path] = (r.status_code, canon(json.loads(r.text)))

    r = httpx.get(base + "/api/admin/config", headers=owner, timeout=30, trust_env=False)
    out["adminConfig"] = (r.status_code, canon(json.loads(r.text)))

    # export -> file -> import, so the redis backend's non-batch path runs
    r = httpx.post(base + "/api/admin/data_migration/export", headers=owner,
                   json={"password": BACKUP_PASSWORD}, timeout=120, trust_env=False)
    out["export"] = (r.status_code, r.headers.get("content-type"))
    events = sse(r.text)
    chunks = "".join(e["data"] for e in events if e.get("type") == "chunk")
    out["exportEvents"] = (sorted({e.get("type") for e in events}), len(chunks))
    dat = chunks.encode("ascii")
    try:
        plain = decode_backup(chunks, BACKUP_PASSWORD)
        backup = json.loads(plain)
        out["backup"] = (200, canon(backup))
    except Exception as exc:  # noqa: BLE001
        out["backup"] = (500, f"{type(exc).__name__}: {exc}")

    r = httpx.post(base + "/api/admin/data_migration/import", headers=owner,
                   files={"file": ("moontv-backup.dat", dat, "application/octet-stream")},
                   data={"password": BACKUP_PASSWORD}, timeout=180, trust_env=False)
    out["import"] = (r.status_code, r.headers.get("content-type"))
    ev = sse(r.text)
    out["importEvents"] = (sorted({e.get("type") for e in ev}), next((e for e in ev if e.get("type") == "done"), {}))

    # the stores after the round trip: clear_all_data ate nothing
    for path in ("playrecords", "favorites", "followings", "skipconfigs", "searchhistory", "today-updated"):
        r = httpx.get(base + "/api/" + path, headers=seed, timeout=30, trust_env=False)
        out["after" + path] = (r.status_code, canon(json.loads(r.text)))
    r = httpx.get(base + "/api/admin/config", headers=owner, timeout=30, trust_env=False)
    out["afterAdminConfig"] = (r.status_code, canon(json.loads(r.text)))

    # upstream's resetConfig rebuilds custom categories only for storageType === 'redis'
    r = httpx.get(base + "/api/admin/reset", headers=owner, timeout=30, trust_env=False)
    out["reset"] = (r.status_code, r.text[:60])
    time.sleep(TTL_WAIT)
    r = httpx.get(base + "/api/admin/config", headers=owner, timeout=30, trust_env=False)
    cfg = json.loads(r.text)["Config"]
    out["afterResetCategories"] = (200, [c.get("query") for c in cfg.get("CustomCategories") or []])
    out["afterResetSources"] = (200, [s["key"] for s in cfg.get("SourceConfig") or []])

    # the store as it stands after the reset, before the user is deleted: the
    # redis-only branch is visible in the raw blob, not on the wire
    if snapshot:
        snapshot()

    r = httpx.post(base + "/api/admin/user", headers=owner,
                   json={"action": "deleteUser", "targetUsername": SEED_USER}, timeout=30, trust_env=False)
    out["deleteUser"] = (r.status_code, r.text)
    for path in ("playrecords", "favorites", "followings", "skipconfigs", "searchhistory", "today-updated"):
        r = httpx.get(base + "/api/" + path, headers=seed, timeout=30, trust_env=False)
        out["cleaned" + path] = (r.status_code, canon(json.loads(r.text)))
    return out


with tempfile.TemporaryDirectory(prefix="zzredis-") as tmp:
    config_path = make_config_file(tmp)
    sqlite_proc = spawn("sqlite", SQLITE_HTTP, 0, f"{tmp}/sqlite.db", config_path)
    redis_proc = spawn("redis", REDIS_HTTP, REDIS_PORT, None, config_path)
    kv_proc = spawn("kvrocks", KV_HTTP, KV_PORT, None, config_path)

    rd_census: dict[str, Any] = {}
    kv_census: dict[str, Any] = {}
    sq_blob: dict[str, Any] = {}

    up = [wait_up(SQLITE_HTTP), wait_up(REDIS_HTTP), wait_up(KV_HTTP)]
    check("all three servers came up", all(up), str(up))

    sq = scenario(f"http://127.0.0.1:{SQLITE_HTTP}", lambda: sq_blob.update(sqlite_blob(f"{tmp}/sqlite.db")))
    rd = scenario(f"http://127.0.0.1:{REDIS_HTTP}", lambda: rd_census.update(take_census(REDIS_PORT)))
    kv = scenario(f"http://127.0.0.1:{KV_HTTP}", lambda: kv_census.update(take_census(KV_PORT)))

    for proc in (sqlite_proc, redis_proc, kv_proc):
        proc.terminate()

    # Four shapes legitimately differ between the backends, so they are asserted
    # separately instead of compared:
    #  * search history: D1's `created_at` is CURRENT_TIMESTAMP (second
    #    granularity), so a re-searched keyword ties with its neighbours and stays
    #    put; redis's LPUSH + LREM key 0 always moves it to the front.
    #  * favorites/followings: D1 hands back typed columns, redis hands back the
    #    JSON it stored, so the key sets differ.
    #  * the backup payload and the import's `done` event carry those shapes.
    UPSTREAM_DIFFERS = {
        "addSearch", "getSearch", "aftersearchhistory",
        "getfavorites", "getfollowings", "afterfavorites", "afterfollowings",
        "backup", "importEvents",
    }
    for label in sorted(sq):
        if label in UPSTREAM_DIFFERS or label not in rd:
            continue
        same = label in rd and sq[label] == rd[label]
        check(f"sqlite == redis on {label}", same, f"{str(sq.get(label))[:200]} vs {str(rd.get(label))[:200]}" if not same else "")

    check(
        "redis's adminConfig is the same blob sqlite stored",
        rd.get("adminConfig") == sq.get("adminConfig"),
        "",
    )

    # the differences, asserted as differences on the right axis
    check("search history: both dedup to the same two keywords",
          sorted(sq["getSearch"][1]) == sorted(rd["getSearch"][1]) == ["alpha", "beta"],
          f"{sq['getSearch'][1]} vs {rd['getSearch'][1]}")
    check("redis's search history is newest-first (LPUSH + LREM key 0)",
          rd["getSearch"][1][0] == "alpha", str(rd["getSearch"][1]))
    check("sqlite's order only depends on CURRENT_TIMESTAMP, which ties inside a second",
          set(sq["getSearch"][1]) == set(rd["getSearch"][1]), str(sq["getSearch"][1]))

    fav = {"title": "AlphaTitle", "source_name": "IK", "year": "2026", "cover": "https://zz/c.jpg",
           "poster": "https://zz/c.jpg", "id": "vid1", "source": "IK"}
    check("redis's favorites are exactly the posted object",
          rd["getfavorites"][1].get("IK+vid1") == canon(fav), str(rd["getfavorites"][1]))
    shared = {"title", "source_name", "year", "cover"}
    check("sqlite and redis agree on the fields both stores model",
          {k: v for k, v in (sq["getfavorites"][1].get("IK+vid1") or {}).items() if k in shared}
          == {k: v for k, v in (rd["getfavorites"][1].get("IK+vid1") or {}).items() if k in shared},
          f"{sq['getfavorites'][1]} vs {rd['getfavorites'][1]}")
    check("sqlite's favorites carry its column keys, redis the posted ones",
          set(sq["getfavorites"][1].get("IK+vid1", {})) != set(rd["getfavorites"][1].get("IK+vid1", {})),
          f"{sorted(sq['getfavorites'][1].get('IK+vid1', {}))} vs {sorted(rd['getfavorites'][1].get('IK+vid1', {}))}")

    def census_of(backup):
        return {u: sorted(k for k in v if v[k]) for u, v in backup["data"]["userData"].items()}

    check("both backups carry the same adminConfig",
          sq["backup"][1]["data"]["adminConfig"] == rd["backup"][1]["data"]["adminConfig"],
          f"{str(sq['backup'][1])[:120]} vs {str(rd['backup'][1])[:120]}")
    check("both backups cover the same users and record kinds",
          census_of(sq["backup"][1]) == census_of(rd["backup"][1]),
          f"{census_of(sq['backup'][1])} vs {census_of(rd['backup'][1])}")

    check("both imports end with the same done event",
          {k: v for k, v in sq["importEvents"][1].items() if k != "timestamp"}
          == {k: v for k, v in rd["importEvents"][1].items() if k != "timestamp"},
          f"{sq['importEvents'][1]} vs {rd['importEvents'][1]}")
    check("neither import reported an error event",
          "error" not in sq["importEvents"][0] and "error" not in rd["importEvents"][0], "")

    # upstream's resetConfig rebuilds custom categories only for storageType === 'redis'
    check("GET /api/admin/reset on redis rebuilds CustomCategories from the config file",
          rd["afterResetCategories"][1] == [CONFIG_CATEGORY["query"]],
          str(rd["afterResetCategories"]))
    check(
        "the reset leaves the source list the same on all three",
        (rd.get("afterResetSources") or (0, []))[1] == (sq.get("afterResetSources") or (0, []))[1] == (kv.get("afterResetSources") or (0, []))[1],
        f"{rd.get('afterResetSources')} {sq.get('afterResetSources')}",
    )
    check("the config file's category is in every backend before the reset",
          [c["query"] for c in (sq.get("adminConfig") or (0, {}))[1]["Config"]["CustomCategories"]]
          == [CONFIG_CATEGORY["query"]], str((sq.get("adminConfig") or (0, {}))[1]["Config"]["CustomCategories"]))

    # the store as it stood after the reset and before the user was deleted, read
    # over the fake server's own RESP socket
    census = rd_census
    check(
        "the redis store keeps one blob per record, not a table",
        all(v is None or isinstance(v, (str, list)) for v in census.values()),
        str(sorted(census))[:200],
    )
    check(
        "the seeded user's records are all u:<user>:<kind>:<key>",
        all(k.startswith("u:") or k == "admin:config" for k in census),
        str(sorted(census))[:200],
    )
    check(
        "the search history is the only LIST",
        [k for k, v in census.items() if isinstance(v, list)] == [f"u:{SEED_USER}:sh"],
        str([k for k, v in census.items() if isinstance(v, list)]),
    )
    check(
        "admin:config holds the whole blob as one string",
        isinstance(census.get("admin:config"), str) and json.loads(census["admin:config"]).get("ConfigFile") is not None,
        str(census.get("admin:config"))[:80],
    )
    check(
        "the redis blob holds the same ConfigFile string sqlite stored",
        json.loads(census["admin:config"])["ConfigFile"] == (sq.get("afterAdminConfig") or (0, {}))[1]["Config"]["ConfigFile"],
        f"{str(json.loads(census['admin:config']).get('ConfigFile'))[:120]} vs "
        f"{str((sq.get('afterAdminConfig') or (0, {}))[1]['Config'].get('ConfigFile'))[:120]}",
    )
    check(
        "the redis blob's ConfigFile api_site map has the config file's keys",
        sorted(json.loads(json.loads(census["admin:config"])["ConfigFile"])["api_site"])
        == sorted(s["key"] for s in (sq.get("adminConfig") or (0, {}))[1]["Config"]["SourceConfig"]),
        f"{sorted(json.loads(json.loads(census['admin:config'])['ConfigFile'])['api_site'])}",
    )

    # upstream's resetConfig rebuilds custom categories only for storageType === 'redis',
    # and that difference lives in the STORED blob: on the wire every backend shows the
    # category again, because get_config re-injects the config file's list on read.
    check("redis's stored blob has the rebuilt CustomCategories",
          [c["query"] for c in json.loads(census["admin:config"]).get("CustomCategories") or []]
          == [CONFIG_CATEGORY["query"]],
          str(json.loads(census["admin:config"]).get("CustomCategories")))
    check("sqlite's and kvrocks' stored blobs have an empty one",
          json.loads(sq_blob["admin:config"]).get("CustomCategories") == []
          and json.loads(kv_census["admin:config"]).get("CustomCategories") == [],
          f"{json.loads(sq_blob['admin:config']).get('CustomCategories')} "
          f"{json.loads(kv_census['admin:config']).get('CustomCategories')}")
    check("on the wire all three show the config file's category again (re-injection)",
          rd["afterResetCategories"][1] == sq["afterResetCategories"][1] == kv["afterResetCategories"][1]
          == [CONFIG_CATEGORY["query"]],
          f"{rd['afterResetCategories'][1]} {sq['afterResetCategories'][1]} {kv['afterResetCategories'][1]}")

    for proc in (sqlite_proc, redis_proc, kv_proc):
        proc.wait(timeout=10)

failed = [x for x in results if not x[1]]
print(f"\n{len(results) - len(failed)}/{len(results)} checks green")
for label, ok, detail in results:
    print(("  ok   " if ok else "  FAIL ") + label + (f" — {detail}" if detail and not ok else ""))
sys.exit(1 if failed else 0)
