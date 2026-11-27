"""url_guard + security hardening tests (redteam-report.md round 2/3 findings)."""

import socket

import httpx
import pytest

from moontv.ratelimit import SlidingWindowLimiter
from moontv.url_guard import (
    BlockedUrl,
    _basic_url_check,
    guard_allows_private,
    is_blocked_host,
    open_guarded,
    parse_host_address,
    resolves_to_private,
    validate_media_url,
    validate_public_url,
)


@pytest.fixture(autouse=True)
def guard_closed(monkeypatch):
    """conftest opens the guard and the limiter (its fake upstreams are on 127.0.0.1 and it
    authenticates once per test); these tests need both closed."""
    monkeypatch.setenv("MOONTVPLUS_URL_GUARD_ALLOW_PRIVATE", "0")
    monkeypatch.setenv("MOONTVPLUS_RATE_LIMIT_DISABLED", "0")


# --- literal spellings that glibc/inet_aton resolve to loopback ---

HEX_LOOPBACKS = [
    "http://0x7f000001:19001/props",
    "http://0x7f.0.0.1/",
    "http://0177.0.0.1/",
    "http://127.1/",
    "http://2130706433/",
    "http://[::ffff:7f00:1]/",
]


@pytest.mark.parametrize("url", HEX_LOOPBACKS)
def test_numeric_loopback_spellings_blocked(url):
    reason, _ = _basic_url_check(url)
    assert reason == "不允许访问该地址"


def test_classic_blocklist_still_blocks():
    for host in ["localhost", "127.0.0.1", "169.254.169.254", "10.0.0.1", "192.168.31.11", "100.64.0.1", "fd00::1"]:
        assert is_blocked_host(host)


def test_public_literal_allowed():
    reason, _ = _basic_url_check("https://example.com/poster.jpg")
    assert reason is None


def test_parse_host_address_normalizes_hex():
    assert str(parse_host_address("0x7f000001")) == "127.0.0.1"
    assert str(parse_host_address("0177.0.0.1")) == "127.0.0.1"
    assert parse_host_address("example.com") is None


# --- DNS-backed bypasses ---

def test_dns_rebind_name_blocked(monkeypatch):
    def fake_getaddrinfo(host, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    assert resolves_to_private("localtest.me")
    reason, _ = _basic_url_check("http://localtest.me:19001/props")
    assert reason == "不允许访问该地址"


def test_dns_public_name_allowed(monkeypatch):
    def fake_getaddrinfo(host, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    assert not resolves_to_private("example.com")
    reason, _ = _basic_url_check("http://example.com/x")
    assert reason is None


def test_unresolvable_name_not_blocked(monkeypatch):
    def boom(host, *args, **kwargs):
        raise socket.gaierror("no such host")

    monkeypatch.setattr(socket, "getaddrinfo", boom)
    reason, _ = _basic_url_check("http://no-such-host.example/x")
    assert reason is None


def test_media_url_allowlist_checks_dns_too(monkeypatch):
    monkeypatch.setenv("M3U8_PROXY_ALLOW_HOSTS", "*.localtest.me")

    def fake_getaddrinfo(host, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    result = validate_media_url("https://a.localtest.me/x.m3u8")
    assert result.get("ok") is False
    assert result.get("reason") == "不允许访问该地址"


def test_guard_allows_private_env_reopens_dns_check(monkeypatch):
    """The documented escape hatch reopens every private check, DNS included.

    Production never sets the env, so the rebinding block stays on there; this
    pins the hatch contract the repo suite asserts for dev/test upstreams.
    """
    monkeypatch.setenv("MOONTVPLUS_URL_GUARD_ALLOW_PRIVATE", "1")
    assert guard_allows_private()

    def fake_getaddrinfo(host, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    reason, _ = _basic_url_check("http://localtest.me/x")
    assert reason is None


# --- redirect hop re-validation ---

def test_open_guarded_blocks_redirect_to_loopback(monkeypatch):
    def fake_getaddrinfo(host, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "ok.example":
            return httpx.Response(302, headers={"location": "http://0x7f000001:19001/props"})
        return httpx.Response(200, content=b"secret")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(BlockedUrl) as exc:
        import asyncio

        asyncio.run(open_guarded(client, "https://ok.example/a", validator=validate_public_url))
    assert "不允许访问该地址" in str(exc.value)


# --- rate limit ---

def test_sliding_window_limits():
    limiter = SlidingWindowLimiter(3, 60.0)
    assert [limiter.allow("ip") for _ in range(4)] == [True, True, True, False]


def test_login_rate_limit_429():
    from fastapi.testclient import TestClient

    from moontv.main import create_app

    app = create_app()
    client = TestClient(app)
    codes = []
    for _ in range(11):
        codes.append(client.post("/api/login", json={"username": "u", "password": "wrong"}).status_code)
    assert codes[:10] == [401] * 10
    assert codes[10] == 429


# --- security headers ---

def test_security_headers_present():
    from fastapi.testclient import TestClient

    from moontv.main import create_app

    client = TestClient(create_app())
    response = client.get("/")
    assert response.headers["x-frame-options"] == "SAMEORIGIN"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
