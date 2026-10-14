"""`src/app/api/search/route.ts` — stream framing and the `compact` projection.

The upstream sites are replaced by a local `?ac=videolist` media server so the
assertions are deterministic. The stream is NDJSON (one JSON object per line),
and the byte order of the events is what the client parses, so the queue-based
drain in `routes/search.py` is checked against the shape the list-based version
wrote: per-site page events first, then the accumulated `failedSources`, then
`aggregatedResults`.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

PAGE_1 = [
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

PAGE_2 = [
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


class _Handler(BaseHTTPRequestHandler):
    """A minimal `?ac=videolist` media server: two pages, three records."""

    def do_GET(self):  # noqa: N802 — BaseHTTPRequestHandler's name
        params = parse_qs(urlparse(self.path).query)
        page = int(params.get("pg", ["1"])[0])
        payload = {"list": PAGE_1 if page == 1 else PAGE_2, "pagecount": 2, "total": 3}
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


def _serve(handler_class):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_class)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


@pytest.fixture()
def fake_upstream():
    server, thread = _serve(_Handler)
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


def _search(client, cookie, query="fake", **params):
    url = "/api/search?q=" + query
    for key, value in params.items():
        url += f"&{key}={value}"
    return client.get(url, headers={"Cookie": cookie})


def test_non_stream_search_returns_full_records(client, cookie, fake_source):
    body = _search(client, cookie, stream="0", sources=fake_source).json()
    results = body["results"]
    assert len(results) == 3
    assert body["failedSources"] == []
    assert [r["id"] for r in results] == ["1", "2", "3"]
    assert results[0]["title"] == "Fake Show"
    assert len(results[0]["episodes"]) == 2
    assert results[0]["episodes_titles"] == ["1", "2"]
    assert results[0]["desc"] == "first page description"
    assert results[1]["year"] == "2021"
    assert results[2]["episodes"] == [
        "https://example.invalid/d.m3u8",
        "https://example.invalid/e.m3u8",
        "https://example.invalid/f.m3u8",
    ]
    # Results are cacheable for the site interface cache time; the fan-out is
    # bounded by SearchDownstreamMaxPage, so page 2 was fetched and page 3 was not.
    assert "max-age=" in _search(client, cookie, stream="0", sources=fake_source).headers["cache-control"]


def test_compact_projection_drops_the_body_keeps_the_count(client, cookie, fake_source):
    full = _search(client, cookie, stream="0", sources=fake_source).json()["results"]
    compact = _search(client, cookie, stream="0", compact="1", sources=fake_source).json()["results"]

    assert len(full) == len(compact)
    for before, after in zip(full, compact):
        assert before["id"] == after["id"]
        assert before["title"] == after["title"]
        assert "episodes" not in after
        assert "episodes_titles" not in after
        assert "desc" not in after
        assert after["episodes_count"] == len(before["episodes"])
        assert set(after) == (set(before) - {"episodes", "episodes_titles", "desc"}) | {"episodes_count"}

    assert sum(r["episodes_count"] for r in compact) == sum(len(r["episodes"]) for r in full)
    assert len(_search(client, cookie, stream="0", compact="1", sources=fake_source).text) < len(
        _search(client, cookie, stream="0", sources=fake_source).text
    )


def test_compact_is_opt_in(client, cookie, fake_source):
    for value in ("0", "", "2"):
        body = _search(client, cookie, stream="0", compact=value, sources=fake_source).json()
        assert "episodes" in body["results"][0]
        assert "episodes_count" not in body["results"][0]


def test_stream_framing_is_unchanged_by_the_queue(client, cookie, fake_source):
    text = _search(client, cookie, stream="1", sources=fake_source).text
    lines = [line for line in text.split("\n") if line.strip()]
    events = [json.loads(line) for line in lines]

    assert len(events) == 3
    assert events[0]["site"] == "FAKE" and len(events[0]["pageResults"]) == 2
    assert events[1]["site"] == "FAKE" and len(events[1]["pageResults"]) == 1
    assert events[-1]["aggregatedResults"] == _search(client, cookie, stream="0", sources=fake_source).json()["results"]
    assert text.endswith("\n")
    assert all(line.startswith('{"') for line in lines)


def test_stream_page_events_are_projected_too(client, cookie, fake_source):
    text = _search(client, cookie, stream="1", compact="1", sources=fake_source).text
    events = [json.loads(line) for line in text.split("\n") if line.strip()]
    for event in events[:-1]:
        for result in event["pageResults"]:
            assert "episodes" not in result
            assert result["episodes_count"] > 0
    assert events[-1]["aggregatedResults"] == _search(
        client, cookie, stream="0", compact="1", sources=fake_source
    ).json()["results"]


def test_empty_query_is_not_searched(client, cookie, fake_source):
    response = _search(client, cookie, query="", stream="1", sources=fake_source)
    assert response.json() == {"results": []}
    assert "no-store" in response.headers["cache-control"]


def test_no_results_reports_the_failed_sources(client, cookie, clean_db):
    class EmptyHandler(_Handler):
        def do_GET(self):  # noqa: N802
            self._respond({"list": [], "pagecount": 0, "total": 0})

    server, thread = _serve(EmptyHandler)
    try:
        client.post(
            "/api/admin/source",
            headers={"Cookie": cookie},
            json={
                "action": "add",
                "key": "EMPTY",
                "name": "EMPTY",
                "api": f"http://127.0.0.1:{server.server_port}/api.php/provide/vod",
            },
        )
        body = _search(client, cookie, stream="0", sources="EMPTY").json()
        assert body["results"] == []
        assert body["failedSources"] == [{"name": "EMPTY", "key": "EMPTY", "error": "无搜索结果"}]
        assert body["failedSources"][0]["error"] == "无搜索结果"
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_yellow_filter_fails_the_site(client, cookie, clean_db):
    yellow_page = [
        {
            "vod_id": 9,
            "vod_name": "Fake Yellow",
            "type_name": "伦理片",
            "vod_play_url": "1$https://example.invalid/y.m3u8",
        }
    ]

    class YellowHandler(_Handler):
        def do_GET(self):  # noqa: N802
            self._respond({"list": yellow_page, "pagecount": 1, "total": 1})

    server, thread = _serve(YellowHandler)
    try:
        client.post(
            "/api/admin/source",
            headers={"Cookie": cookie},
            json={
                "action": "add",
                "key": "YELLOW",
                "name": "YELLOW",
                "api": f"http://127.0.0.1:{server.server_port}/api.php/provide/vod",
            },
        )
        body = _search(client, cookie, stream="0", sources="YELLOW").json()
        assert body["results"] == []
        assert body["failedSources"] == [{"name": "YELLOW", "key": "YELLOW", "error": "结果被过滤"}]

        # The filter is opt-out, and opting out keeps the record. `/api/admin/site`
        # replaces the whole SiteConfig, so the panel's own blob is sent back with
        # one field flipped.
        site_config = client.get("/api/admin/config", headers={"Cookie": cookie}).json()["Config"]["SiteConfig"]
        site_config["DisableYellowFilter"] = True
        client.post("/api/admin/site", headers={"Cookie": cookie}, json=site_config)
        body = _search(client, cookie, stream="0", sources="YELLOW").json()
        assert body["results"] and body["failedSources"] == []
    finally:
        server.shutdown()
        thread.join(timeout=2)
