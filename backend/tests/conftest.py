"""Wire-level tests for the FastAPI backend.

These are the parts of the verification pattern that can run without a network
or a browser: the auth gate, the static/document surface, the search route's
stream framing and projection, the heartbeat, the backup crypto pipeline, the
m3u8 parser and the sqlite storage layer. Upstream sites are replaced by a
local fake media-server so the assertions are deterministic; the live probes in
`.opencode/skills/moontvplus-verification/scripts/` still cover the real wire.

Environment is set at import time, before `moontv` is imported: the storage
singleton and the auth gate read these on first use.
"""

from __future__ import annotations

import json
import os
import pathlib
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

BACKEND = pathlib.Path(__file__).resolve().parents[1]
REPO = BACKEND.parent

_TMP = os.environ.get("MOONTVPLUS_TEST_TMP") or tempfile.mkdtemp(prefix="moontvplus-tests-")

os.environ["MOONTV_DB_PATH"] = os.path.join(_TMP, "test.db")
os.environ["PASSWORD"] = "test-password"
os.environ["USERNAME"] = "test-owner"
os.environ["NEXT_PUBLIC_STORAGE_TYPE"] = "sqlite"
# The fake media server and the stub image upstreams in these tests are on
# 127.0.0.1, which the SSRF guard blocks by default. The guard itself is
# covered by test_url_guard.py.
os.environ["MOONTVPLUS_URL_GUARD_ALLOW_PRIVATE"] = "1"

from moontv.main import create_app  # noqa: E402
from moontv.storage.db import Db  # noqa: E402


DIST = REPO / "frontend" / "dist"


@pytest.fixture()
def client():
    """A fresh app per test; the storage singleton and its DB file are shared."""
    with TestClient(create_app(), follow_redirects=False) as test_client:
        yield test_client


@pytest.fixture()
def cookie(client):
    """The owner's `auth` cookie, exactly as the panel receives it."""
    response = client.post("/api/login", json={"username": "test-owner", "password": "test-password"})
    assert response.status_code == 200
    return "auth=" + response.headers["set-cookie"].split(";")[0][5:]


@pytest.fixture()
async def clean_db():
    """Empty every data table, including `admin_config`, so config seeding reruns.

    `moontv.config` also keeps the last resolved config in a module-level
    `cached_config` (upstream's `let cachedConfig`), which survives a cleared
    table — `getConfig` falls back to it when `getAdminConfig()` is null. A test
    that wants a seeded-from-file config has to drop that too.
    """
    from moontv import config as config_module

    await Db.clear_all_data()
    config_module.cached_config = None
    config_module.invalidate_config_cache()
    yield


@pytest.fixture()
def dist_available():
    if not DIST.is_dir() or not (DIST / "index.html").is_file():
        pytest.skip("frontend/dist is not built — run `pnpm build` in frontend/ first")


# ---------------------------------------------------------------------------
# The fake `?ac=videolist` media server
# ---------------------------------------------------------------------------
#
# Every route that reaches a source is tested against this, not against a live
# site: the assertions have to be deterministic, and the configured sources fail
# from this box with `请求失败` for network reasons. `test_search_stream.py` was
# the only module with the fixture; the routes in `detail.py`, `search_sub.py`,
# `tvbox.py`, `user_history.py` and `source_test.py` need the same stand-in, so
# it lives here.


FAKE_PAGE_1 = [
    {
        "vod_id": 1,
        "vod_name": "  Fake  Show  ",
        "vod_pic": "https://example.invalid/p1.jpg",
        "vod_year": "2020",
        "type_name": "剧情",
        "vod_content": "<p>first page description</p>",
        "vod_play_url": "1$https://example.invalid/a.m3u8#2$https://example.invalid/b.m3u8",
    },
    {
        "vod_id": 2,
        "vod_name": "Fake Movie",
        "vod_year": "2021",
        "vod_content": "no episodes here",
        "vod_play_url": "x$https://example.invalid/c.m3u8",
    },
]

FAKE_PAGE_2 = [
    {
        "vod_id": 3,
        "vod_name": "Fake Season Two",
        "vod_year": "2022",
        "type_name": "喜剧",
        "vod_content": "second page description",
        "vod_play_url": (
            "1$https://example.invalid/d.m3u8#2$https://example.invalid/e.m3u8"
            "#3$https://example.invalid/f.m3u8"
        ),
    },
]


class FakeMediaHandler(BaseHTTPRequestHandler):
    """A minimal `?ac=videolist` media server: two pages, three records."""

    def do_GET(self):  # noqa: N802 — BaseHTTPRequestHandler's name
        params = parse_qs(urlparse(self.path).query)
        page = int(params.get("pg", ["1"])[0])
        payload = {"list": FAKE_PAGE_1 if page == 1 else FAKE_PAGE_2, "pagecount": 2, "total": 3}
        self._respond(payload)

    def _respond(self, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):  # noqa: A002 — silence the stdlib logger
        pass


def serve_fake(handler_class):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_class)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


@pytest.fixture()
def fake_upstream():
    server, thread = serve_fake(FakeMediaHandler)
    try:
        yield f"http://127.0.0.1:{server.server_port}/api.php/provide/vod"
    finally:
        server.shutdown()
        thread.join(timeout=2)


@pytest.fixture()
def fake_source(clean_db, client, cookie, fake_upstream):
    """Start from an empty config so only the fake site is searched."""
    response = client.post(
        "/api/admin/source",
        headers={"Cookie": cookie},
        json={"action": "add", "key": "FAKE", "name": "FAKE", "api": fake_upstream},
    )
    assert response.status_code == 200, response.text
    return "FAKE"


@pytest.fixture()
def fake_only_source(clean_db, client, cookie, fake_upstream):
    """The fake site is the **only** source, so a route that fans out over every
    source has exactly one to fan out to.

    `clean_db` empties the admin blob, but `refine_config` re-injects the
    `ConfigFile.api_site` map on every read, so the ten sources in
    `backend/config.json` come back and a ws/suggestions probe measures them
    (they fail from this box with `请求失败` / `Event loop is closed`, which is
    network, not the port). Replacing the `ConfigFile` string is not enough
    either: `get_config` injects the map into `SourceConfig` **before** the
    panel writes the blob back, so the injected rows are already in the stored
    blob. The only sequence that empties the list is the panel's own
    `batchDelete` over the keys the read produced — that is the path the panel
    uses, and it prunes `ConfigFile.api_site` with it (PLAN.md, change 17).
    """
    inner = client.get("/api/admin/config", headers={"Cookie": cookie}).json()["Config"]
    if isinstance(inner, str):
        inner = json.loads(inner)
    keys = [s.get("key") for s in inner.get("SourceConfig") or []]
    if keys:
        response = client.post(
            "/api/admin/source",
            headers={"Cookie": cookie},
            json={"action": "batchDelete", "keys": keys},
        )
        assert response.status_code == 200, response.text

    response = client.post(
        "/api/admin/source",
        headers={"Cookie": cookie},
        json={"action": "add", "key": "FAKE", "name": "FAKE", "api": fake_upstream},
    )
    assert response.status_code == 200, response.text
    return "FAKE"
