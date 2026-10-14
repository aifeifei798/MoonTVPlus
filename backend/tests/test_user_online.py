"""`/api/user/online` — the heartbeat debounce (PLAN.md deliberate change).

Upstream rewrites the whole admin config blob on every heartbeat, and the panel
posts it on every page view, so a busy deployment writes the config once per
request. The debounce keeps the wire shape identical and only skips the write;
the value returned is the one already stored rather than a fabricated timestamp.
"""

from __future__ import annotations

from moontv.routes import user_state
from moontv.storage.db import Db


def _counting_saves(monkeypatch) -> list:
    calls: list = []
    original = Db.save_admin_config

    def counting(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(Db, "save_admin_config", counting)
    return calls


def test_heartbeat_writes_once_per_window(client, cookie, clean_db, monkeypatch):
    calls = _counting_saves(monkeypatch)

    first = client.post("/api/user/online", headers={"Cookie": cookie}).json()
    for _ in range(9):
        again = client.post("/api/user/online", headers={"Cookie": cookie}).json()

    assert len(calls) == 1, "ten page views should not mean ten config writes"
    assert again == {"ok": True, "lastOnline": first["lastOnline"]}
    assert isinstance(first["lastOnline"], int)

    stored = client.get("/api/admin/config", headers={"Cookie": cookie}).json()["Config"]
    entry = next(u for u in stored["UserConfig"]["Users"] if u["username"] == "test-owner")
    assert entry["lastOnline"] == first["lastOnline"]


def test_debounce_can_be_turned_off(client, cookie, clean_db, monkeypatch):
    monkeypatch.setattr(user_state, "ONLINE_DEBOUNCE_S", 0)
    calls = _counting_saves(monkeypatch)

    stamps = [
        client.post("/api/user/online", headers={"Cookie": cookie}).json()["lastOnline"]
        for _ in range(3)
    ]
    assert len(calls) == 3
    assert stamps[0] < stamps[1] <= stamps[2]


def test_the_window_elapses_and_the_write_happens(client, cookie, clean_db, monkeypatch):
    monkeypatch.setattr(user_state, "ONLINE_DEBOUNCE_S", 60)
    calls = _counting_saves(monkeypatch)

    base = client.post("/api/user/online", headers={"Cookie": cookie}).json()["lastOnline"]
    assert len(calls) == 1

    monkeypatch.setattr(user_state, "now_ms", lambda: base + 61_000)
    after = client.post("/api/user/online", headers={"Cookie": cookie}).json()["lastOnline"]
    assert len(calls) == 2
    assert after == base + 61_000


def test_unknown_and_banned_users_are_still_rejected(client, cookie, clean_db):
    assert client.post("/api/user/online", headers={"Cookie": "auth=garbage"}).status_code == 401

    client.post(
        "/api/admin/user",
        headers={"Cookie": cookie},
        json={"action": "add", "targetUsername": "zzprobe", "targetPassword": "probe123"},
    )
    client.post("/api/login", json={"username": "zzprobe", "password": "probe123"})
    banned_cookie = f"auth={client.cookies.get('auth')}"
    assert client.post("/api/user/online", headers={"Cookie": banned_cookie}).status_code == 200

    client.post(
        "/api/admin/user",
        headers={"Cookie": cookie},
        json={"action": "ban", "targetUsername": "zzprobe"},
    )
    config = client.get("/api/admin/config", headers={"Cookie": cookie}).json()["Config"]
    entry = next(u for u in config["UserConfig"]["Users"] if u["username"] == "zzprobe")
    assert entry.get("banned") is True

    response = client.post("/api/user/online", headers={"Cookie": banned_cookie})
    assert response.status_code == 401
    assert response.json() == {"error": "用户已被封禁"}