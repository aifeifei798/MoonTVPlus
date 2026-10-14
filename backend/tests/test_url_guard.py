"""`src/lib/url-guard.ts`, plus the two MoonTVPlus additions it grew.

Upstream guards only `/api/m3u8`, and only the URL the caller named. Two things
were measured on the live wire before this port changed anything:

  * `/api/image-proxy` is in the auth gate's skip list (an `<img>` cannot send a
    cookie), so it was an open SSRF channel: `?url=http://127.0.0.1:3000/logo.svg`
    answered the app's own SVG, and `localhost`, `http://127.0.0.1%23@127.0.0.1`
    and `http://127.0.0.1?a=b` all reached loopback too.
  * `/api/m3u8`'s guard is bypassable with a public host that 302s into the
    cluster — checking only the entry URL is not enough, because `fetch` follows
    redirects.

So the guard here re-runs the blacklist on every hop (`open_guarded`) and the
image proxy gets the same blacklist without the host whitelist
(`validate_public_url`). Everything below runs offline against a stub client.
"""

from __future__ import annotations

import httpx
import pytest

from moontv import m3u8 as m3u8_module
from moontv.routes import image_proxy
from moontv.url_guard import (
    BlockedUrl,
    guard_allows_private,
    is_blocked_host,
    open_guarded,
    validate_media_url,
    validate_public_url,
)


BLOCKED_HOSTS = [    "localhost",
    "LOCALHOST",
    "0.0.0.0",
    "127.0.0.1",
    "127.0.0.5",
    "10.0.0.1",
    "192.168.0.1",
    "169.254.169.254",
    "172.16.0.1",
    "172.31.255.255",
    "100.64.0.1",
    "100.127.255.255",
    "::1",
    "fc00::1",
    "fe80::1",
    "fd00::1",  # the assigned half of fc00::/7, which `^fc00:` cannot match
    "fd12:3456:7890::1",
    "::ffff:127.0.0.1",  # IPv4-mapped loopback: the string list misses it
    "::ffff:169.254.169.254",
    "::ffff:10.0.0.1",
    "::ffff:192.168.1.1",
    "2130706433",  # 127.0.0.1 written as a decimal
    "",
]

ALLOWED_HOSTS = [
    "example.com",
    "8.8.8.8",
    "172.15.0.1",  # just outside the 172.16/12 private block
    "100.63.0.1",
    "100.128.0.1",
    "2001:4860:4860::8888",  # public IPv6 (Google DNS)
    "2606:4700:4700::1111",  # public IPv6 (Cloudflare)
    "test-streams.mux.dev",
]


class StubClient:
    """Answers per URL; a `Location` header makes that answer a redirect hop."""

    def __init__(self, answers: dict[str, tuple[int, dict]]):
        self.answers = answers
        self.attempted: list[str] = []

    def build_request(self, method, url, headers=None, timeout=None):
        return httpx.Request(method, url, headers=headers or {})

    async def send(self, request, stream=True, follow_redirects=False):
        url = str(request.url)
        self.attempted.append(url)
        answer = self.answers.get(url)
        if answer is None:
            raise httpx.ConnectError("stub has no such host")
        status, headers = answer
        body = b"" if status in (301, 302, 303, 307, 308) else b"manifest bytes"
        return httpx.Response(status, headers=headers or {}, content=body)


@pytest.fixture(autouse=True)
def guard_on(monkeypatch):
    """`conftest.py` turns the blacklist off so the offline fake media server and
    the stub upstreams stay reachable; this file is about the blacklist itself."""
    monkeypatch.delenv("MOONTVPLUS_URL_GUARD_ALLOW_PRIVATE", raising=False)


@pytest.mark.parametrize("host", BLOCKED_HOSTS)
def test_private_hosts_are_blocked(host):
    assert is_blocked_host(host) is True, f"{host} should be blocked"


@pytest.mark.parametrize("host", ALLOWED_HOSTS)
def test_public_hosts_are_allowed(host):
    assert is_blocked_host(host) is False, f"{host} should be allowed"


def test_verdicts_the_route_puts_on_the_wire():
    """The `reason` strings are part of the response body, not decoration."""
    assert validate_media_url("") == {"ok": False, "reason": "缺少 URL 参数"}
    assert validate_media_url(None) == {"ok": False, "reason": "缺少 URL 参数"}
    assert validate_media_url("x" * 4097) == {"ok": False, "reason": "URL 过长"}
    assert validate_media_url("example.com/x.m3u8") == {"ok": False, "reason": "URL 格式非法"}
    assert validate_media_url("ftp://example.com/x.m3u8") == {
        "ok": False,
        "reason": "仅支持 http/https 协议",
    }
    assert validate_media_url("http://127.0.0.1:6379/") == {
        "ok": False,
        "reason": "不允许访问该地址",
    }
    assert validate_media_url("http://169.254.169.254/latest/meta-data/") == {
        "ok": False,
        "reason": "不允许访问该地址",
    }
    assert validate_media_url("https://example.com/x.m3u8") == {"ok": True}


def test_whitelist_applies_to_media_urls_only(monkeypatch):
    monkeypatch.setenv("M3U8_PROXY_ALLOW_HOSTS", "cdn.example.com,*.mux.dev")
    assert validate_media_url("https://other.example.com/x.m3u8") == {
        "ok": False,
        "reason": "该域名不在允许列表中",
    }
    assert validate_media_url("https://cdn.example.com/x.m3u8") == {"ok": True}
    # The wildcard keeps its leading dot, so `evil-example.com` does not match.
    assert validate_media_url("https://evil-example.com/x.m3u8") == {
        "ok": False,
        "reason": "该域名不在允许列表中",
    }
    assert validate_media_url("https://test-streams.mux.dev/x.m3u8") == {"ok": True}

    # A hop is never the host the caller named, so the whitelist does not apply
    # to it — only the private-host blacklist does.
    assert validate_public_url("https://other.example.com/x.jpg") == {"ok": True}


def test_extra_allow_hosts_extend_the_whitelist(monkeypatch):
    monkeypatch.delenv("M3U8_PROXY_ALLOW_HOSTS", raising=False)
    assert validate_media_url("https://cdn.example.com/x.m3u8") == {"ok": True}
    monkeypatch.setenv("M3U8_PROXY_ALLOW_HOSTS", "cdn.example.com")
    assert validate_media_url("https://cdn.example.com/x.m3u8") == {"ok": True}
    assert validate_media_url("https://other.example.com/x.m3u8") == {
        "ok": False,
        "reason": "该域名不在允许列表中",
    }


def test_escape_hatch_restores_upstreams_behaviour(monkeypatch):
    monkeypatch.setenv("MOONTVPLUS_URL_GUARD_ALLOW_PRIVATE", "1")
    assert guard_allows_private() is True
    assert validate_media_url("http://127.0.0.1:6379/") == {"ok": True}
    assert validate_public_url("http://localhost:3000/logo.svg") == {"ok": True}

    monkeypatch.setenv("MOONTVPLUS_URL_GUARD_ALLOW_PRIVATE", "")
    assert guard_allows_private() is False
    assert validate_media_url("http://127.0.0.1:6379/") == {
        "ok": False,
        "reason": "不允许访问该地址",
    }


async def test_entry_url_is_checked_before_any_request_is_sent():
    stub = StubClient({})
    with pytest.raises(BlockedUrl) as error:
        await open_guarded(stub, "http://127.0.0.1:6379/")
    assert str(error.value) == "不允许访问该地址"
    assert stub.attempted == [], "the guard must not touch the network first"


async def test_a_redirect_into_loopback_is_blocked():
    """The bypass the entry-only check allowed: public host, private hop."""
    entry = "https://cdn.example.com/manifest.m3u8"
    stub = StubClient({entry: (302, {"location": "http://127.0.0.1:6379/"})})
    with pytest.raises(BlockedUrl) as error:
        await open_guarded(stub, entry)
    assert str(error.value) == "不允许访问该地址"
    assert stub.attempted == [entry], "the hop must be checked before it is fetched"


async def test_relative_hops_are_resolved_against_the_entry_url():
    entry = "https://cdn.example.com/a/manifest.m3u8"
    stub = StubClient(
        {
            entry: (302, {"location": "/b/manifest.m3u8"}),
            "https://cdn.example.com/b/manifest.m3u8": (200, {}),
        }
    )
    response = await open_guarded(stub, entry)
    assert response.status_code == 200
    assert stub.attempted == [entry, "https://cdn.example.com/b/manifest.m3u8"]


async def test_a_hop_to_an_unparseable_url_is_blocked():
    entry = "https://cdn.example.com/manifest.m3u8"
    stub = StubClient({entry: (302, {"location": "ftp://127.0.0.1/x.m3u8"})})
    with pytest.raises(BlockedUrl) as error:
        await open_guarded(stub, entry)
    assert str(error.value) == "仅支持 http/https 协议"


async def test_redirect_without_location_is_not_followed():
    entry = "https://cdn.example.com/manifest.m3u8"
    stub = StubClient({entry: (302, {})})
    with pytest.raises(BlockedUrl) as error:
        await open_guarded(stub, entry)
    assert str(error.value) == "重定向缺少 Location 头"
    assert stub.attempted == [entry]


async def test_redirect_chain_stops_after_five_hops():
    entry = "https://cdn.example.com/manifest.m3u8"
    hop = "https://cdn.example.com/hop"
    stub = StubClient({entry: (302, {"location": hop}), hop: (302, {"location": hop})})
    with pytest.raises(BlockedUrl) as error:
        await open_guarded(stub, entry)
    assert str(error.value) == "重定向次数过多"
    assert len(stub.attempted) == 6  # entry + 5 hops, then give up


async def test_guard_off_follows_redirects_like_upstream(monkeypatch):
    monkeypatch.setenv("MOONTVPLUS_URL_GUARD_ALLOW_PRIVATE", "1")
    entry = "http://127.0.0.1:3000/logo.svg"
    stub = StubClient({entry: (200, {"content-type": "image/svg+xml"})})
    response = await open_guarded(stub, entry, validator=validate_public_url)
    assert response.status_code == 200
    assert stub.attempted == [entry]


# ---------------------------------------------------------------------------
# The wire: what a blocked URL actually answers
# ---------------------------------------------------------------------------


def test_image_proxy_refuses_a_loopback_url(client, monkeypatch):
    monkeypatch.delenv("MOONTVPLUS_URL_GUARD_ALLOW_PRIVATE", raising=False)
    response = client.get(
        "/api/image-proxy", params={"url": "http://127.0.0.1:3000/logo.svg"}
    )
    assert response.status_code == 403
    assert response.json() == {"error": "地址不被允许", "message": "不允许访问该地址"}

    for url in (
        "http://localhost:3000/logo.svg",
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1%23@127.0.0.1:3000/logo.svg",
        "http://10.0.0.1/x.jpg",
        "ftp://127.0.0.1/x.jpg",
    ):
        response = client.get("/api/image-proxy", params={"url": url})
        assert response.status_code == 403, f"{url} should be refused"


def test_image_proxy_still_fetches_public_posters(client, monkeypatch):
    """The guard must not silence a real card: the mirror chain runs as before."""
    monkeypatch.delenv("MOONTVPLUS_URL_GUARD_ALLOW_PRIVATE", raising=False)
    url = "http://lain.bgm.tv/pic/cover/l/1c/b4/390200_Z9jPp.jpg"
    stub = StubClient(
        {
            candidate: (200, {"content-type": "image/jpeg"})
            for candidate in image_proxy._image_candidates(url)
        }
    )
    monkeypatch.setattr(image_proxy, "get_http_client", lambda: stub)

    response = client.get("/api/image-proxy", params={"url": url})
    assert response.status_code == 200
    assert response.content == b"manifest bytes"
    # The chain stops at the first host that answers, so only the official one is
    # attempted; the mirrors are only reached when the official host dies.
    assert stub.attempted == image_proxy._image_candidates(url)[:1]


def test_image_proxy_follows_a_public_redirect_but_not_a_private_one(client, monkeypatch):
    monkeypatch.delenv("MOONTVPLUS_URL_GUARD_ALLOW_PRIVATE", raising=False)

    entry = "https://poster.example.com/a.jpg"
    hop = "https://cdn.example.com/a.jpg"
    stub = StubClient({entry: (302, {"location": hop}), hop: (200, {"content-type": "image/jpeg"})})
    monkeypatch.setattr(image_proxy, "get_http_client", lambda: stub)
    response = client.get("/api/image-proxy", params={"url": entry})
    assert response.status_code == 200
    assert stub.attempted == [entry, hop]

    stub = StubClient({entry: (302, {"location": "http://127.0.0.1:3000/logo.svg"})})
    monkeypatch.setattr(image_proxy, "get_http_client", lambda: stub)
    response = client.get("/api/image-proxy", params={"url": entry})
    assert response.status_code == 403
    assert stub.attempted == [entry]


def test_m3u8_refuses_a_loopback_url(client, monkeypatch):
    monkeypatch.delenv("MOONTVPLUS_URL_GUARD_ALLOW_PRIVATE", raising=False)
    response = client.get("/api/m3u8", params={"url": "http://127.0.0.1:3000/x.m3u8"})
    assert response.status_code == 403
    assert response.json() == {"error": "地址不被允许", "message": "不允许访问该地址"}

    response = client.post("/api/m3u8", json={"url": "http://127.0.0.1:3000/x.m3u8"})
    assert response.status_code == 403
    assert response.json() == {"error": "地址不被允许", "message": "不允许访问该地址"}


def test_a_private_hop_is_a_403_not_the_catch_all_500(client, monkeypatch):
    """`?url=<public>` that bounces into loopback must answer the guard, not
    the route's `下载失败` 500 — otherwise the bypass reads as a transient error."""
    monkeypatch.delenv("MOONTVPLUS_URL_GUARD_ALLOW_PRIVATE", raising=False)
    entry = "https://cdn.example.com/manifest.m3u8"
    stub = StubClient({entry: (302, {"location": "http://127.0.0.1:6379/"})})
    monkeypatch.setattr(m3u8_module, "get_http_client", lambda: stub)

    response = client.get("/api/m3u8", params={"url": entry})
    assert response.status_code == 403
    assert response.json() == {"error": "地址不被允许", "message": "不允许访问该地址"}

    response = client.post("/api/m3u8", json={"url": entry})
    assert response.status_code == 403
    assert response.json() == {"error": "地址不被允许", "message": "不允许访问该地址"}


def test_ipv4_mapped_ipv6_is_refused_on_both_proxies(client, monkeypatch):
    """The bypass this fix exists for: `http://[::ffff:127.0.0.1]:3000/logo.svg`
    reaches the app's own logo through `/api/image-proxy` when the blacklist is
    only the string list, because `urlsplit().hostname` is `::ffff:127.0.0.1` and
    no pattern in `PRIVATE_HOST_PATTERNS` matches it. The guard must answer 403
    **without connecting**, so no stub is installed: a stub that has no answer for
    the URL raises `ConnectError`, and a 500 here would mean the fetch happened.
    """
    monkeypatch.delenv("MOONTVPLUS_URL_GUARD_ALLOW_PRIVATE", raising=False)
    mapped = "http://[::ffff:127.0.0.1]:3000/logo.svg"

    response = client.get("/api/image-proxy", params={"url": mapped})
    assert response.status_code == 403, "IPv4-mapped loopback must be refused"
    assert response.json() == {"error": "地址不被允许", "message": "不允许访问该地址"}

    for url in (
        "http://[::ffff:169.254.169.254]/latest/meta-data/",
        "http://[::ffff:10.0.0.1]/x.jpg",
        "http://[fd00::1]/x.jpg",
    ):
        response = client.get("/api/image-proxy", params={"url": url})
        assert response.status_code == 403, f"{url} should be refused"

    response = client.get("/api/m3u8", params={"url": "http://[::ffff:127.0.0.1]:3000/x.m3u8"})
    assert response.status_code == 403
    response = client.post("/api/m3u8", json={"url": "http://[::ffff:127.0.0.1]:3000/x.m3u8"})
    assert response.status_code == 403

    # A public IPv6 literal must stay reachable: the fix is about private ranges,
    # not about IPv6 in general.
    hop = "https://[2606:4700:4700::1111]/a.jpg"
    stub = StubClient({hop: (200, {"content-type": "image/jpeg"})})
    monkeypatch.setattr(image_proxy, "get_http_client", lambda: stub)
    response = client.get("/api/image-proxy", params={"url": hop})
    assert response.status_code == 200
    assert stub.attempted == [hop]
