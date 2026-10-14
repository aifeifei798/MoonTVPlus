"""`storage/sqlite.py` — autocommit durability, WAL, the batch path.

These are the three things that are easy to get wrong and silent when they are:
a write that is not durable when the function returns (default isolation level),
a batch import that blocks the event loop, and the search-history cap.
"""

from __future__ import annotations

import asyncio
import sqlite3

import pytest

from moontv.storage.db import Db
from moontv.storage.sqlite import BATCH_LIMIT, SEARCH_HISTORY_LIMIT


def _db_path() -> str:
    return Db.storage._conn.execute("PRAGMA database_list").fetchone()[2]


def test_wal_and_busy_timeout_are_active():
    """WAL is what lets the offloaded batch writes coexist with live reads."""
    mode = Db.storage._conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"
    assert Db.storage._conn.execute("PRAGMA synchronous").fetchone()[0] == 1  # NORMAL
    assert Db.storage._conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000


async def test_a_write_is_durable_for_another_connection_immediately(clean_db):
    """Autocommit: every statement is durable on return, like a D1 query was.

    With the default isolation level this row would sit uncommitted — invisible
    to a second connection, and gone on restart.
    """
    await Db.storage.set_play_record("alice", "IK+1", {"title": "T", "source_name": "IK", "index": 1})
    with sqlite3.connect(_db_path()) as other:
        row = other.execute(
            "SELECT title FROM play_records WHERE source = ? AND video_id = ?", ("IK", "1")
        ).fetchone()
    assert row == ("T",)


async def test_play_record_is_rebuilt_from_columns(clean_db):
    await Db.storage.set_play_record(
        "alice",
        "IK+1",
        {
            "title": "Show",
            "source_name": "IK",
            "year": "2020",
            "cover": "https://example.invalid/p.jpg",
            "index": 2,
            "total_episodes": 8,
            "play_time": 100,
            "total_time": 600,
            "save_time": 1700000000000,
            "search_title": "show",
        },
    )
    record = await Db.storage.get_play_record("alice", "IK+1")
    assert record == {
        "title": "Show",
        "source_name": "IK",
        "year": "2020",
        "cover": "https://example.invalid/p.jpg",
        "index": 2,
        "total_episodes": 8,
        "play_time": 100,
        "total_time": 600,
        "save_time": 1700000000000,
        "search_title": "show",
    }
    # A key that is not source+id is not a record, not an error.
    assert await Db.storage.get_play_record("alice", "garbage") is None


async def test_search_history_is_deduplicated_and_capped(clean_db):
    for keyword in ["a", "b", "c", "a", "b"]:
        await Db.storage.add_search_history("alice", keyword)
    history = await Db.storage.get_search_history("alice")
    # Re-adding a keyword deletes the old row, so the history stays unique.
    assert sorted(history) == ["a", "b", "c"]

    for i in range(SEARCH_HISTORY_LIMIT + 10):
        await Db.storage.add_search_history("alice", f"kw{i}")
    assert len(await Db.storage.get_search_history("alice")) == SEARCH_HISTORY_LIMIT

    await Db.storage.delete_search_history("alice", "kw0")
    assert "kw0" not in await Db.storage.get_search_history("alice")
    await Db.storage.delete_search_history("alice")
    assert await Db.storage.get_search_history("alice") == []


async def test_batch_import_chunks_at_100_and_reports_progress(clean_db):
    # The import route hands the storage `[key, record]` pairs, which is what the
    # `.dat` payload contains.
    entries = [
        (f"IK+{i}", {"title": f"T{i}", "source_name": "IK", "index": 1}) for i in range(250)
    ]
    progress = []
    await Db.storage.batch_import_play_records(
        "alice", entries, lambda done, total: progress.append((done, total))
    )
    assert len(progress) == 3
    assert [p[0] for p in progress] == [100, 200, 250]
    assert progress[-1][1] == 250
    assert len(await Db.storage.get_all_play_records("alice")) == 250


def test_run_batch_rolls_back_a_bad_chunk(clean_db):
    """One sqlite transaction per 100 rows, like D1's `batch()`.

    A bad statement in the second chunk must not undo the first chunk, and must
    not leave the second chunk half-written.
    """
    storage = Db.storage
    user_id = storage._ensure_user("dave")
    # `search_history` has a UNIQUE(user_id, keyword), so the chunk rows differ.
    good = [
        ("INSERT INTO search_history (user_id, keyword) VALUES (?, ?)", (user_id, f"kw{i}"))
        for i in range(150)
    ]
    bad = ("INSERT INTO no_such_table (a) VALUES (?)", (1,))

    with pytest.raises(Exception):
        storage._run_batch(good[:100] + [bad] * 50)

    count = storage._conn.execute(
        "SELECT COUNT(*) FROM search_history WHERE user_id = ?", (user_id,)
    ).fetchone()[0]
    assert count == 100


async def test_batch_writes_do_not_block_concurrent_reads(clean_db):
    """The batch path runs in a worker thread; the loop keeps answering reads.

    If `_run_batch_async` were still inline, every `get_all_play_records` here
    would have to wait for the whole import — and with the old isolation level it
    would hit `database is locked`.
    """
    entries = [(f"IK+{i}", {"title": f"T{i}", "source_name": "IK", "index": 1}) for i in range(500)]

    importer = asyncio.create_task(Db.batch_import_play_records("bob", entries))
    reads = await asyncio.gather(*(Db.get_all_play_records("bob") for _ in range(50)))
    await importer

    assert all(isinstance(r, dict) for r in reads)
    assert len(await Db.get_all_play_records("bob")) == 500


async def test_skip_config_and_today_updated_round_trip(clean_db):
    await Db.storage.set_skip_config("alice", "IK", "1", {"enable": True, "intro_time": 10, "outro_time": 20})
    assert await Db.storage.get_skip_config("alice", "IK", "1") == {
        "enable": True,
        "intro_time": 10,
        "outro_time": 20,
    }
    # Missing fields coalesce to 0/False, the way the client's defaults do.
    await Db.storage.set_skip_config("alice", "IK", "2", {})
    assert await Db.storage.get_skip_config("alice", "IK", "2") == {
        "enable": False,
        "intro_time": 0,
        "outro_time": 0,
    }
    await Db.storage.delete_skip_config("alice", "IK", "1")
    assert await Db.storage.get_skip_config("alice", "IK", "1") is None

    await Db.storage.set_today_updated("alice", {"date": "2026-10-14", "items": [{"id": 1}]})
    assert (await Db.storage.get_today_updated("alice"))["date"] == "2026-10-14"

    await Db.storage.clear_all_data()
    assert await Db.storage.get_all_play_records("alice") == {}
    assert await Db.storage.get_all_favorites("alice") == {}
    assert await Db.storage.get_all_followings("alice") == {}
    assert await Db.storage.get_search_history("alice") == []
    assert await Db.storage.get_all_skip_configs("alice") == {}
    assert await Db.storage.get_admin_config() is None


async def test_users_round_trip(clean_db):
    await Db.storage.register_user("carol", "pw123")
    assert await Db.storage.verify_user("carol", "pw123") is True
    assert await Db.storage.verify_user("carol", "wrong") is False
    assert await Db.storage.check_user_exist("carol") is True
    await Db.storage.change_password("carol", "pw456")
    assert await Db.storage.verify_user("carol", "pw456") is True
    assert "carol" in await Db.storage.get_all_users()
    await Db.storage.delete_user("carol")
    assert await Db.storage.check_user_exist("carol") is False


def test_indexes_exist_on_a_fresh_db(clean_db):
    names = {r[1] for r in Db.storage._conn.execute("PRAGMA index_list(play_records)").fetchall()}
    assert names, "the upgrade path adds indexes to pre-existing databases"


def test_batch_limit_is_upstreams_chunk_size():
    assert BATCH_LIMIT == 100
