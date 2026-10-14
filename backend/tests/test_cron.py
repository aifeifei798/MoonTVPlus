"""`src/app/api/cron/route.ts` — the scheduled refresh of records and favorites.

The route answers immediately and refreshes in the background (upstream did not
`await`), so the wire half is asserted through the GET and the work half is
asserted by awaiting `_refresh_record_and_favorites()` directly: a test cannot
race a fire-and-forget task against the response.

What the refresh does, in the shape the panel reads back:

  * a record whose episode count changed is rewritten with the source's
    `title`/`cover`/`year` and the new `total_episodes`;
  * the stream-search path matches on `source` + `id` (not on the title), and
    its mapper trims `vod_name`, so a record seeded with the trimmed title comes
    back trimmed, while the `getDetailFromApi` fallback would keep the source's
    raw `"  Fake  Show  "`;
  * a record whose source is not in the config is skipped (the detail fetch
    raises `无效的API来源` and the failure is not cached, so the next record that
    needs the same key retries it). A key with no `+` is skipped too, but it
    cannot be seeded: `set_play_record` raises `Invalid key format` for it, so
    the store never holds one.

The route is in the auth matcher's negative lookahead, so `/api/cron` answers
200 with no cookie.
"""

from __future__ import annotations

import re

from moontv.routes import cron as cron_module
from moontv.storage.db import Db

ISO = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z")

USER = "test-owner"


def _record(title="Fake Show", episodes=1, cover="https://old.invalid/c.jpg"):
    return {
        "title": title,
        "source_name": "FAKE",
        "cover": cover,
        "index": 1,
        "total_episodes": episodes,
        "play_time": 100,
        "year": "2019",
        "total_time": 600,
        "save_time": 1700000000000,
        "search_title": "Fake Show",
    }


def _favorite(episodes=1, cover="https://old.invalid/f.jpg"):
    return {
        "title": "Fake Show",
        "source_name": "FAKE",
        "year": "2019",
        "cover": cover,
        "total_episodes": episodes,
        "save_time": 1700000000000,
        "search_title": "Fake Show",
    }


def test_cron_answers_without_a_cookie(client, clean_db):
    client.cookies.pop("auth", None)
    response = client.get("/api/cron")
    assert response.status_code == 200
    body = response.json()
    assert list(body) == ["success", "message", "timestamp"]
    assert body["success"] is True
    assert body["message"] == "Cron job executed successfully"
    # `new Date().toISOString()` — UTC, milliseconds, trailing Z.
    assert ISO.fullmatch(body["timestamp"])


async def test_cron_rewrites_a_play_record_whose_count_changed(client, cookie, fake_source, clean_db):
    await Db.set_play_record(USER, "FAKE+1", _record())

    await cron_module._refresh_record_and_favorites()

    record = (await Db.get_all_play_records(USER))["FAKE+1"]
    # The fake source's record 1 has two episodes, so the count moves 1 -> 2 and
    # the cover/year come from the source. The stream-search mapper trims
    # `vod_name`, so the title is the trimmed one.
    assert record["total_episodes"] == 2
    assert record["cover"] == "https://example.invalid/p1.jpg"
    assert record["year"] == "2020"
    assert record["title"] == "Fake Show"
    # The fields the refresh does not touch keep their values.
    assert record["index"] == 1
    assert record["play_time"] == 100
    assert record["total_time"] == 600
    assert record["save_time"] == 1700000000000


async def test_cron_leaves_a_matching_record_alone(client, cookie, fake_source, clean_db):
    await Db.set_play_record(USER, "FAKE+1", _record(episodes=2, cover="https://old.invalid/c.jpg"))

    await cron_module._refresh_record_and_favorites()

    # `total_episodes` already equals the source's count, so nothing is rewritten
    # and the old cover stays.
    record = (await Db.get_all_play_records(USER))["FAKE+1"]
    assert record["cover"] == "https://old.invalid/c.jpg"
    assert record["year"] == "2019"


async def test_cron_rewrites_a_favorite(client, cookie, fake_source, clean_db):
    await Db.set_favorite(USER, "FAKE+1", _favorite())

    await cron_module._refresh_record_and_favorites()

    fav = (await Db.get_all_favorites(USER))["FAKE+1"]
    assert fav["total_episodes"] == 2
    assert fav["cover"] == "https://example.invalid/p1.jpg"
    assert fav["year"] == "2020"


async def test_cron_skips_a_record_whose_source_is_not_configured(client, cookie, fake_source, clean_db):
    await Db.set_play_record(USER, "GHOST+1", {**_record(), "source_name": "GHOST"})

    await cron_module._refresh_record_and_favorites()

    # `无效的API来源` is raised, the detail is not cached, and the record is left
    # exactly as it was.
    record = (await Db.get_all_play_records(USER))["GHOST+1"]
    assert record["total_episodes"] == 1
    assert record["cover"] == "https://old.invalid/c.jpg"


async def test_cron_detail_cache_is_fifo_capped(client, cookie, clean_db):
    # The cache is a plain dict with FIFO eviction at 500 entries; the same key
    # refreshes its value without moving, and the oldest key goes first.
    cache: dict = {}
    for i in range(500):
        cron_module._cache_put(cache, f"S+{i}", {"n": i})
    assert len(cache) == 500
    cron_module._cache_put(cache, "S+500", {"n": 500})
    assert len(cache) == 500
    assert "S+0" not in cache
    assert "S+500" in cache
    # Re-putting an existing key updates the value and keeps the order.
    cron_module._cache_put(cache, "S+1", {"n": 9})
    assert cache["S+1"] == {"n": 9}
    assert list(cache)[0] == "S+1"
