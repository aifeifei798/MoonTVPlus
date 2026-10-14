"""`src/middleware.ts` — the gate, its matcher, and the login cookie.

Asserted with `allow_redirects=False` (TestClient follows redirects by default,
which hides a 307 as a 200 HTML body).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

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


def _cookie_attributes(response):
    return [part.strip().lower() for part in response.headers["set-cookie"].split(";")]


def test_login_cookie_expires_in_seven_days(client):
    """`expires` is an absolute date, not a relative one.

    Starlette passes a non-`datetime` `expires` straight to `Morsel`, which reads
    an int as "seconds from now", so the previous
    `int(time.time()) + COOKIE_MAX_AGE` stamped an expiry 57 years out — measured
    on the live wire as `expires=Tue, 03 Aug 2083 18:21:26 GMT`. Upstream's
    `expires.setDate(getDate() + 7)` is an absolute date, which is what a
    `datetime` gives.
    """
    response = client.post(
        "/api/login", json={"username": "test-owner", "password": "test-password"}
    )
    attributes = _cookie_attributes(response)
    assert "max-age=604800" in attributes

    expires = next(part for part in attributes if part.startswith("expires="))
    when = datetime.strptime(expires.split("=", 1)[1], "%a, %d %b %Y %H:%M:%S %Z").replace(
        tzinfo=timezone.utc
    )
    delta = (when - datetime.now(timezone.utc)).total_seconds()
    assert 6 * 86400 < delta < 8 * 86400, f"cookie expires in {delta} seconds"


def test_logout_cookie_is_already_expired(client, cookie):
    """`new Date(0)` is the epoch; `expires=0` would mean "expires now"."""
    response = client.post("/api/logout", headers={"Cookie": cookie})
    attributes = _cookie_attributes(response)
    assert "max-age=0" in attributes

    expires = next(part for part in attributes if part.startswith("expires="))
    when = datetime.strptime(expires.split("=", 1)[1], "%a, %d %b %Y %H:%M:%S %Z").replace(
        tzinfo=timezone.utc
    )
    assert when < datetime.now(timezone.utc)
