"""`/api/admin/data_migration/{export,import}` — the size cap and an offline round trip.

The `.dat` pipeline itself is `test_crypto_backup.py`'s job (AES/DEFLATE parity
against openssl and crypto-js). What is tested here is the two things the routes
add around it:

  * the import no longer reads the whole upload into memory before decrypting it
    (PLAN.md change 39). `MOONTVPLUS_IMPORT_MAX_BYTES` sets the ceiling, default
    50 MB, and the check happens *while* reading, so an oversized upload is
    refused before any of it is held;
  * an export → import round trip through the real sqlite storage loses nothing,
    which is the assertion the export's `Db.clear_all_data()` makes non-trivial:
    the import deletes all eight data tables before replaying the backup, so a
    dropped table is invisible unless the census is taken on both sides.

No network: the fake media server is irrelevant here, and the guard is off in
`conftest.py` so nothing else interferes.
"""

from __future__ import annotations

import io
import json

from moontv.crypto import encode_backup
from moontv.routes import data_migration
from moontv.storage.db import Db
from moontv.version import CURRENT_VERSION

SOURCE = "豪华"
KEY = f"{SOURCE}+57630"

PLAY_RECORD = {
    "title": "英雄人物",
    "source_name": SOURCE,
    "year": "2001",
    "cover": "https://img9.doubanio.com/view/photo/s_ratio_poster/public/p2515774776.webp",
    "index": 0,
    "total_episodes": 1,
    "play_time": 120,
    "total_time": 900,
    "save_time": 1700000000000,
    "search_title": "英雄人物",
}

FAVORITE = {
    "title": "英雄人物",
    "source_name": SOURCE,
    "year": "2001",
    "cover": "https://img9.doubanio.com/view/photo/s_ratio_poster/public/p2515774776.webp",
    "total_episodes": 1,
    "save_time": 1700000000001,
    "search_title": "英雄人物",
}

FOLLOWING = {
    "title": "英雄人物",
    "source_name": SOURCE,
    "year": "2001",
    "cover": "https://img9.doubanio.com/view/photo/s_ratio_poster/public/p2515774776.webp",
    "total_episodes": 12,
    "watched_episodes": 3,
    "save_time": 1700000000002,
    "search_title": "英雄人物",
}

SKIP_CONFIG = {"enable": True, "intro_time": 60, "outro_time": 30}

TODAY_UPDATED = {"date": "2026-10-14", "items": [{"title": "英雄人物", "episodes": 12}]}

SEARCH_HISTORY = ["英雄人物", "追更剧"]


def _events(response) -> list[dict]:
    """The SSE frames, exactly as `components/DataMigration.tsx` reads them."""
    events = []
    for line in response.iter_lines():
        if not line:
            continue
        text = line if isinstance(line, str) else line.decode("utf-8")
        assert text.startswith("data: "), f"unexpected SSE frame: {text!r}"
        events.append(json.loads(text[6:].strip()))
    return events


async def _seed(client, cookie):
    """Records written through the storage layer the routes themselves use."""
    # The export refuses to run without an admin config, so let the panel's own
    # route create it before exporting.
    client.get("/api/admin/config", headers={"Cookie": cookie})

    await Db.set_play_record("test-owner", KEY, PLAY_RECORD)
    await Db.set_favorite("test-owner", KEY, FAVORITE)
    await Db.set_following("test-owner", KEY, FOLLOWING)
    await Db.set_skip_config("test-owner", SOURCE, "57630", SKIP_CONFIG)
    await Db.set_today_updated("test-owner", TODAY_UPDATED)
    for keyword in SEARCH_HISTORY:
        await Db.add_search_history("test-owner", keyword)


async def _census() -> dict:
    return {
        "play": await Db.get_all_play_records("test-owner"),
        "favorites": await Db.get_all_favorites("test-owner"),
        "followings": await Db.get_all_followings("test-owner"),
        "skip": await Db.get_all_skip_configs("test-owner"),
        "today": await Db.get_today_updated("test-owner"),
        "history": await Db.get_search_history("test-owner"),
    }


def test_oversized_upload_is_refused_before_it_is_decrypted(client, cookie, monkeypatch):
    monkeypatch.setenv("MOONTVPLUS_IMPORT_MAX_BYTES", "1048576")

    response = client.post(
        "/api/admin/data_migration/import",
        headers={"Cookie": cookie},
        files={"file": ("backup.dat", io.BytesIO(b"x" * 2 * 1024 * 1024))},
        data={"password": "s3cret"},
    )
    assert response.status_code == 400
    assert response.json() == {"error": "备份文件过大，最大 1 MB"}


def test_the_cap_is_exclusive_and_the_label_follows_it(client, cookie, monkeypatch):
    """Exactly at the limit is not "too big"; a non-MB limit is spelled in bytes."""
    monkeypatch.setenv("MOONTVPLUS_IMPORT_MAX_BYTES", "1024")

    response = client.post(
        "/api/admin/data_migration/import",
        headers={"Cookie": cookie},
        files={"file": ("backup.dat", io.BytesIO(b"x" * 1024))},
        data={"password": "s3cret"},
    )
    assert response.status_code == 400
    # It got past the size check and died on the decrypt, which is the branch a
    # file at the limit should reach.
    assert response.json() == {"error": "解密失败，请检查密码是否正确"}

    monkeypatch.setenv("MOONTVPLUS_IMPORT_MAX_BYTES", "1500")
    response = client.post(
        "/api/admin/data_migration/import",
        headers={"Cookie": cookie},
        files={"file": ("backup.dat", io.BytesIO(b"x" * 1501))},
        data={"password": "s3cret"},
    )
    assert response.json() == {"error": "备份文件过大，最大 1500 字节"}


def test_a_bad_limit_falls_back_to_the_default(monkeypatch):
    for value in ("", "0", "-1", "abc", "50MB"):
        monkeypatch.setenv("MOONTVPLUS_IMPORT_MAX_BYTES", value)
        assert data_migration._import_limit() == data_migration.IMPORT_MAX_BYTES
    monkeypatch.setenv("MOONTVPLUS_IMPORT_MAX_BYTES", "1048576")
    assert data_migration._import_limit() == 1048576


def test_import_needs_a_cookie(client):
    """Unauthenticated reaches the gate, not the route: the gate's bare string,
    not the route's JSON envelope."""
    response = client.post(
        "/api/admin/data_migration/import",
        files={"file": ("backup.dat", io.BytesIO(b"not a backup"))},
        data={"password": "s3cret"},
    )
    assert response.status_code == 401
    assert response.text == "Unauthorized"


async def test_export_import_round_trip_eats_nothing(client, cookie, clean_db):
    await _seed(client, cookie)
    before = await _census()

    export = client.post(
        "/api/admin/data_migration/export",
        headers={"Cookie": cookie},
        json={"password": "s3cret"},
    )
    assert export.status_code == 200
    events = _events(export)
    assert events[-1]["type"] == "done", events[-1]

    encrypted = "".join(event["data"] for event in events if event["type"] == "chunk")
    assert encrypted, "the export streamed no file"

    # The payload, not the SSE envelope, carries the version stamp — the panel's
    # "已是最新版本" logic and the `.dat` filename both come from it.
    from moontv.crypto import decode_backup

    payload = json.loads(decode_backup(encrypted, "s3cret"))
    assert payload["serverVersion"] == CURRENT_VERSION
    assert payload["data"]["adminConfig"], "the backup must carry the config blob"

    import_response = client.post(
        "/api/admin/data_migration/import",
        headers={"Cookie": cookie},
        files={"file": ("backup.dat", io.BytesIO(encrypted.encode("ascii")))},
        data={"password": "s3cret"},
    )
    assert import_response.status_code == 200
    import_events = _events(import_response)
    assert import_events[-1]["type"] == "done", import_events[-1]
    assert import_events[-1]["importedUsers"] == 1

    after = await _census()
    assert after == before, "the round trip dropped records"

    # Belt and braces: the tables the import replays are the ones the census reads.
    assert after["play"][KEY]["title"] == "英雄人物"
    assert after["play"][KEY]["play_time"] == 120
    assert after["favorites"][KEY]["total_episodes"] == 1
    assert after["followings"][KEY]["watched_episodes"] == 3
    assert after["skip"][SOURCE + "+57630"]["intro_time"] == 60
    assert after["today"]["date"] == "2026-10-14"
    assert after["history"] == SEARCH_HISTORY


async def test_a_wrong_password_is_a_400_not_a_500(client, cookie, clean_db):
    await _seed(client, cookie)
    export = client.post(
        "/api/admin/data_migration/export",
        headers={"Cookie": cookie},
        json={"password": "s3cret"},
    )
    encrypted = "".join(
        event["data"] for event in _events(export) if event["type"] == "chunk"
    )

    response = client.post(
        "/api/admin/data_migration/import",
        headers={"Cookie": cookie},
        files={"file": ("backup.dat", io.BytesIO(encrypted.encode("ascii")))},
        data={"password": "wrong"},
    )
    assert response.status_code == 400
    assert response.json() == {"error": "解密失败，请检查密码是否正确"}

    # The failed import must not have cleared anything.
    assert (await Db.get_all_play_records("test-owner"))[KEY]["title"] == "英雄人物"


async def test_a_backup_that_deflates_to_nothing_is_rejected(client, cookie, clean_db):
    """`encode_backup` of an empty payload is a valid `.dat`; the shape check is
    what stops it, not the decrypt."""
    body = encode_backup(json.dumps({"timestamp": "x", "data": {"adminConfig": {}}}), "s3cret")
    response = client.post(
        "/api/admin/data_migration/import",
        headers={"Cookie": cookie},
        files={"file": ("backup.dat", io.BytesIO(body.encode("ascii")))},
        data={"password": "s3cret"},
    )
    assert response.status_code == 400
    assert response.json() == {"error": "备份文件格式无效"}
