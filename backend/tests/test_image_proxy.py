"""The image proxy's host chain, offline.

`/api/image-proxy` is the reason posters render at all: `processImageUrl` sends
every `http://` image (and every douban poster in `server` mode) through it. The
MoonTVPlus addition is the mirror chain — the 动漫「每日放送」 covers come from
`lain.bgm.tv`, whose TLS handshake is intermittently reset from mainland China,
and the douban posters come from `img<N>.doubanio.com`, which answers a
referer-less request with 418. In both cases the proxy retries the same path on a
mirror.

These tests never touch the network: a stub client records the URLs the proxy
tries and answers with canned responses, so the assertions are about the order
and the error shapes, not about a live host.
"""

from __future__ import annotations

import httpx

from moontv.routes import image_proxy


class StubClient:
    """Answers per host: `None` means "the connection died"."""

    def __init__(self, responses: dict[str, object]):
        self.responses = responses
        self.attempted: list[str] = []

    def build_request(self, method, url, headers=None, timeout=None):
        return httpx.Request(method, url, headers=headers or {})

    async def send(self, request, stream=True):
        self.attempted.append(str(request.url))
        answer = self.responses.get(request.url.host)
        if answer is None:
            raise httpx.ConnectError("Connection reset by peer")
        status, body = answer
        return httpx.Response(
            status,
            content=body,
            headers={"content-type": "image/jpeg", "content-length": str(len(body))},
        )


def _stub(monkeypatch, responses):
    client = StubClient(responses)
    monkeypatch.setattr(image_proxy, "get_http_client", lambda: client)
    return client


def test_candidate_chain_is_official_first():
    candidates = image_proxy._image_candidates(
        "http://lain.bgm.tv/pic/cover/l/1c/b4/390200_Z9jPp.jpg"
    )
    assert candidates == [
        "http://lain.bgm.tv/pic/cover/l/1c/b4/390200_Z9jPp.jpg",
        "https://lain.bgm.tv/pic/cover/l/1c/b4/390200_Z9jPp.jpg",
        "https://lain.fxbgm.tv/pic/cover/l/1c/b4/390200_Z9jPp.jpg",
    ]


def test_mirrors_can_be_turned_off(monkeypatch):
    monkeypatch.setattr(image_proxy, "_BANGUMI_MIRROR_HOSTS", ())
    assert image_proxy._image_candidates(
        "http://lain.bgm.tv/pic/cover/l/1c/b4/390200_Z9jPp.jpg"
    ) == [
        "http://lain.bgm.tv/pic/cover/l/1c/b4/390200_Z9jPp.jpg",
        "https://lain.bgm.tv/pic/cover/l/1c/b4/390200_Z9jPp.jpg",
    ]


def test_douban_poster_chain_is_official_first():
    candidates = image_proxy._image_candidates(
        "https://img3.doubanio.com/view/photo/s_ratio_poster/public/p2561037850.jpg"
    )
    assert candidates == [
        "https://img3.doubanio.com/view/photo/s_ratio_poster/public/p2561037850.jpg",
        "https://img.doubanio.cmliussss.net/view/photo/s_ratio_poster/public/p2561037850.jpg",
        "https://img.doubanio.cmliussss.com/view/photo/s_ratio_poster/public/p2561037850.jpg",
    ]


def test_douban_mirrors_can_be_turned_off(monkeypatch):
    monkeypatch.setattr(image_proxy, "_DOUBAN_MIRROR_HOSTS", ())
    assert image_proxy._image_candidates(
        "http://img2.doubanio.com/view/photo/s_ratio_poster/public/p2561037850.jpg"
    ) == [
        "http://img2.doubanio.com/view/photo/s_ratio_poster/public/p2561037850.jpg",
        "https://img2.doubanio.com/view/photo/s_ratio_poster/public/p2561037850.jpg",
    ]


def test_other_hosts_get_one_candidate():
    for url in (
        "https://img.bfzypic.com/upload/vod/20230424-43/574cef591171edc25a4b48545a091f74.jpg",
        "http://example.com/a.jpg",
        "https://www.bing.com/op?q=1",
        "https://img3.doubanio.com.example.com/a.jpg",
    ):
        assert image_proxy._image_candidates(url) == [url]


def test_query_and_path_survive_the_host_swap():
    candidates = image_proxy._image_candidates(
        "https://lain.bgm.tv/pic/cover/m/12/34/567_AbCdE.jpg?x=1#frag"
    )
    assert candidates[-1] == "https://lain.fxbgm.tv/pic/cover/m/12/34/567_AbCdE.jpg?x=1#frag"


def test_official_failure_falls_through_to_the_mirror(client, monkeypatch):
    stub = _stub(
        monkeypatch,
        {
            "lain.bgm.tv": None,
            "lain.fxbgm.tv": (200, b"\xff\xd8\xff\xd0fake-jpeg"),
        },
    )
    response = client.get(
        "/api/image-proxy",
        params={"url": "http://lain.bgm.tv/pic/cover/l/1c/b4/390200_Z9jPp.jpg"},
    )
    assert response.status_code == 200
    assert response.content == b"\xff\xd8\xff\xd0fake-jpeg"
    assert response.headers["content-type"] == "image/jpeg"
    assert stub.attempted == image_proxy._image_candidates(
        "http://lain.bgm.tv/pic/cover/l/1c/b4/390200_Z9jPp.jpg"
    )


def test_cloudflare_5xx_falls_through_but_404_does_not(client, monkeypatch):
    stub = _stub(
        monkeypatch,
        {
            "lain.bgm.tv": (502, b"bad gateway"),
            "lain.fxbgm.tv": (200, b"mirror bytes"),
        },
    )
    response = client.get(
        "/api/image-proxy",
        params={"url": "https://lain.bgm.tv/pic/cover/l/1c/b4/390200_Z9jPp.jpg"},
    )
    assert response.status_code == 200
    assert response.content == b"mirror bytes"
    assert stub.attempted == [
        "https://lain.bgm.tv/pic/cover/l/1c/b4/390200_Z9jPp.jpg",
        "https://lain.fxbgm.tv/pic/cover/l/1c/b4/390200_Z9jPp.jpg",
    ]

    stub = _stub(monkeypatch, {"lain.bgm.tv": (404, b"nope")})
    response = client.get(
        "/api/image-proxy",
        params={"url": "https://lain.bgm.tv/pic/cover/l/1c/b4/0.jpg"},
    )
    assert response.status_code == 404
    assert response.json() == {"error": "Not Found"}
    assert stub.attempted == ["https://lain.bgm.tv/pic/cover/l/1c/b4/0.jpg"]


def test_every_host_dead_is_the_upstream_500(client, monkeypatch):
    _stub(monkeypatch, {})
    response = client.get(
        "/api/image-proxy",
        params={"url": "http://lain.bgm.tv/pic/cover/l/1c/b4/390200_Z9jPp.jpg"},
    )
    assert response.status_code == 500
    assert response.json() == {"error": "Error fetching image"}


def test_missing_url_is_400(client):
    response = client.get("/api/image-proxy")
    assert response.status_code == 400
    assert response.json() == {"error": "Missing image URL"}
