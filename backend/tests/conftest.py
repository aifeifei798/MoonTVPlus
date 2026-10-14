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

import os
import pathlib
import tempfile

import pytest
from fastapi.testclient import TestClient

BACKEND = pathlib.Path(__file__).resolve().parents[1]
REPO = BACKEND.parent

_TMP = os.environ.get("MOONTVPLUS_TEST_TMP") or tempfile.mkdtemp(prefix="moontvplus-tests-")

os.environ["MOONTV_DB_PATH"] = os.path.join(_TMP, "test.db")
os.environ["PASSWORD"] = "test-password"
os.environ["USERNAME"] = "test-owner"
os.environ["NEXT_PUBLIC_STORAGE_TYPE"] = "sqlite"

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
