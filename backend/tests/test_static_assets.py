"""`static.py` — the catch-all document, gzip, ETag revalidation.

Everything here is measured against the built `frontend/dist`, so a stale `dist`
shows up as a failure rather than as "the fix is in the source but the page did
not change".

Note on the client: httpx (and therefore `TestClient`) transparently inflates a
gzipped body, so the wire size is asserted from `content-length` and the bytes
are compared against the file on disk after inflation.
"""

from __future__ import annotations

import struct

import pytest

from conftest import DIST


def _assets(suffix: str):
    return sorted(p for p in DIST.joinpath("assets").glob(f"*{suffix}"))


def test_document_is_served_with_runtime_config_injected(client, cookie, dist_available):
    response = client.get("/", headers={"Cookie": cookie})
    assert response.status_code == 200
    body = response.text
    assert "__RUNTIME_CONFIG__" not in body
    assert "__SITE_NAME__" not in body
    assert "window.RUNTIME_CONFIG" in body
    assert response.headers["cache-control"] == "no-cache"


def test_unknown_api_path_is_404_not_the_document(client, cookie):
    """Without this the admin panel reads the HTML document as a JSON body."""
    response = client.get("/api/this-route-does-not-exist", headers={"Cookie": cookie})
    assert response.status_code == 404
    assert "<html" in response.text


def test_unknown_page_path_falls_through_to_the_document(client, cookie):
    response = client.get("/some/spa/path", headers={"Cookie": cookie})
    assert response.status_code == 200
    assert "window.RUNTIME_CONFIG" in response.text


def test_text_assets_are_gzipped_when_the_client_accepts_it(client, dist_available):
    for asset in _assets(".js")[:3] + _assets(".css")[:2]:
        raw = asset.read_bytes()
        response = client.get(f"/assets/{asset.name}", headers={"Accept-Encoding": "gzip"})
        assert response.status_code == 200
        if len(raw) >= 1000:
            assert response.headers.get("content-encoding") == "gzip", asset.name
            assert response.headers.get("vary") == "Accept-Encoding"
            assert int(response.headers["content-length"]) < len(raw)
            assert response.content == raw
        else:
            assert response.headers.get("content-encoding") is None
            assert response.content == raw


def test_binary_assets_are_not_gzipped(client, dist_available):
    for asset in _assets(".woff2")[:1] + _assets(".png")[:1]:
        response = client.get(f"/assets/{asset.name}", headers={"Accept-Encoding": "gzip"})
        assert response.headers.get("content-encoding") is None
        assert response.content == asset.read_bytes()


def test_client_without_accept_encoding_gets_raw_bytes(client, dist_available):
    asset = _assets(".js")[0]
    response = client.get(f"/assets/{asset.name}", headers={"Accept-Encoding": "identity"})
    assert response.headers.get("content-encoding") is None
    assert response.content == asset.read_bytes()
    assert int(response.headers["content-length"]) == len(asset.read_bytes())


def test_etag_revalidation_returns_304(client, dist_available):
    asset = _assets(".js")[0]
    first = client.get(f"/assets/{asset.name}", headers={"Accept-Encoding": "gzip"})
    etag = first.headers["etag"]
    second = client.get(
        f"/assets/{asset.name}",
        headers={"Accept-Encoding": "gzip", "If-None-Match": etag},
    )
    assert second.status_code == 304
    assert second.headers["etag"] == etag
    assert not second.content


def test_content_hashed_assets_are_immutable_and_others_are_short_lived(client, dist_available):
    hashed = _assets(".js")[0]
    response = client.get(f"/assets/{hashed.name}")
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"

    response = client.get("/favicon.ico")
    assert response.headers["cache-control"] == "public, max-age=300"
    assert response.headers["etag"]


def test_gzip_cache_is_reused_across_requests(client, dist_available):
    """The compressed blob is built once per (path, size, mtime), not per request."""
    from moontv.static import _GZIP_CACHE

    asset = _assets(".js")[0]
    client.get(f"/assets/{asset.name}", headers={"Accept-Encoding": "gzip"})
    entries = [key for key in _GZIP_CACHE if str(key[0]) == str(asset)]
    assert len(entries) == 1
    key = entries[0]
    client.get(f"/assets/{asset.name}", headers={"Accept-Encoding": "gzip"})
    assert len([k for k in _GZIP_CACHE if str(k[0]) == str(asset)]) == 1
    assert key[1] == asset.stat().st_size


def test_path_traversal_is_not_served_as_an_asset(client, cookie):
    response = client.get("/..%2f..%2fbackend%2fconfig.json", headers={"Cookie": cookie})
    assert "api_site" not in response.text
    assert "<html" in response.text


def test_brand_assets_are_public_and_the_ico_is_well_formed(client, dist_available):
    """The login page is drawn by the app itself, so the mark must load unauthenticated.

    `make-logo.py` packs the ICO by hand (ImageMagick writes a single frame), so
    the container is parsed here: three PNG entries, each offset inside the file
    and each sized to the bytes that follow it.
    """
    for path in ("/favicon.ico", "/logo.svg", "/logo.png", "/icons/icon-192x192.png"):
        response = client.get(path)  # deliberately no cookie
        assert response.status_code == 200, path

    svg = (DIST / "logo.svg").read_text()
    assert svg.count("<path") == 2  # crescent + play triangle
    assert "image/svg+xml" in client.get("/logo.svg").headers["content-type"]

    ico = (DIST / "favicon.ico").read_bytes()
    reserved, kind, count = struct.unpack("<HHH", ico[:6])
    assert (reserved, kind) == (0, 1)
    assert count == 3
    for index in range(count):
        width, height, _, _, planes, depth, size, offset = struct.unpack(
            "<BBBBHHII", ico[6 + 16 * index : 22 + 16 * index]
        )
        assert (width, height) in {(16, 16), (32, 32), (48, 48)}
        assert planes == 0 and depth == 32
        assert ico[offset : offset + 8] == b"\x89PNG\r\n\x1a\n"
        assert offset + size <= len(ico)
