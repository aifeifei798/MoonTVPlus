"""`src/middleware.ts` — the gate, its matcher, and the login cookie.

Asserted with `allow_redirects=False` (TestClient follows redirects by default,
which hides a 307 as a 200 HTML body).
"""

from __future__ import annotations

import os

from conftest import DIST


def test_unauthenticated_page_redirects_to_login(client):
    response = client.get("/search?q=abc")
    assert response.status_code == 307
    assert response.headers["location"] == "http://testserver/login?redirect=%2Fsearch%3Fq%3Dabc"


def test_unauthenticated_api_returns_401(client):
    """Upstream's `handleAuthFailure` writes the bare string, not a JSON envelope."""
    response = client.get("/api/search?q=abc")
    assert response.status_code == 401
    assert response.text == "Unauthorized"


def test_gate_excluded_paths_are_open(client):
    for path in ("/api/server-config", "/api/cron", "/favicon.ico", "/manifest.json", "/assets"):
        response = client.get(path)
        assert response.status_code == 200, f"{path} should not be gated, got {response.status_code}"


def test_bundle_loads_before_login(client, dist_available):
    """`/assets` is in the skip list so the login page can render at all."""
    assets = sorted(p.name for p in DIST.joinpath("assets").glob("index-*.js"))
    assert assets
    response = client.get(f"/assets/{assets[0]}")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/javascript"


def test_missing_password_goes_to_warning(client):
    saved = os.environ.get("PASSWORD")
    os.environ["PASSWORD"] = ""
    try:
        response = client.get("/")
        assert response.status_code == 307
        assert response.headers["location"].endswith("/warning")
    finally:
        if saved is not None:
            os.environ["PASSWORD"] = saved


def test_login_with_wrong_password_is_401(client):
    response = client.post("/api/login", json={"username": "test-owner", "password": "nope"})
    assert response.status_code == 401
    assert "set-cookie" not in response.headers


def test_cookie_round_trip(client, cookie):
    response = client.post("/api/user/online", headers={"Cookie": cookie})
    assert response.status_code == 200
    body = response.json()
    assert body == {"ok": True, "lastOnline": body["lastOnline"]}
    assert isinstance(body["lastOnline"], int)


def test_logout_clears_the_gate(client, cookie):
    client.post("/api/logout", headers={"Cookie": cookie})
    response = client.get("/")
    assert response.status_code == 307
