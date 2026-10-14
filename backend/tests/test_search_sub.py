"""`src/app/api/search/{one,resources,suggestions,ws}/route.ts` — the wire shapes
the OrionTV clients read.

These four routes were verified end to end with curl in the B6 session, and the
claims were never committed, so they could not be re-run. This module pins the
shapes that a client parses:

  * `/api/search/one`'s "no result" is a **404** with `result: null` present as
    an explicit key, and the missing-params case is a **200** with the same key
    first — `strip_none` is not applied here.
  * `/api/search/suggestions` is NDJSON: one line per result page, each line a
    complete `{"suggestions":[…]}` object, body ends with `\n`.
  * `/api/search/ws` is SSE (`data: …` + blank line) with `start`, one
    `source_result` per source, and `complete` whose `totalResults` is **always
    0** — upstream's `allResults` is shadowed by the per-site one, so the outer
    array is never appended to. That is a kept upstream bug, not a port gap.

The source is the shared fake `?ac=videolist` server from `conftest.py`, and the
config is `fake_only_source`: `clean_db` empties the admin blob, but
`refine_config` re-injects the `ConfigFile.api_site` map on every read, so the
ten sources in `backend/config.json` come back and a fan-out probe measures them
instead of the fake one.

The `cookie` fixture logs in through the shared `client`, and `TestClient` keeps
the returned `auth` cookie in its jar, so an "unauthenticated" case has to drop
it from the jar explicitly — passing no `Cookie` header is not enough. And the
gate answers unauthenticated `/api/*` with **plain text** `Unauthorized` at 401
(`auth.py: _auth_failure`), so the routes' own `{"error": "Unauthorized"}`
branches are unreachable in the ported app exactly as they were behind Next's
middleware; the tests assert the gate's bytes, not the dead branch.
"""

from __future__ import annotations

import json


def _get(client, cookie, path, **params):
    url = path
    if params:
        url += "?" + "&".join(f"{k}={v}" for k, v in params.items())
    return client.get(url, headers={"Cookie": cookie})


def _anon(client):
    """Send the request with no cookie: drop the one the login fixture left in the jar."""
    client.cookies.pop("auth", None)


# ---------------------------------------------------------------------------
# /api/search/one
# ---------------------------------------------------------------------------


def test_search_one_requires_q_and_resource_id(client, cookie, fake_source):
    # Upstream answers 200 here, not 400, and `result` comes first in the body.
    response = _get(client, cookie, "/api/search/one", q="Fake Movie")
    assert response.status_code == 200
    assert list(response.json()) == ["result", "error"]
    assert response.json() == {"result": None, "error": "缺少必要参数: q 或 resourceId"}


def test_search_one_is_unauthenticated_401(client, cookie, fake_source):
    _anon(client)
    response = client.get(
        "/api/search/one", params={"q": "Fake Movie", "resourceId": fake_source}
    )
    # The gate answers before the route's own check can, and its body is plain text.
    assert response.status_code == 401
    assert response.content == b"Unauthorized"


def test_search_one_unknown_resource_is_404_with_null_result(client, cookie, fake_source):
    response = _get(client, cookie, "/api/search/one", q="Fake Movie", resourceId="NOPE")
    assert response.status_code == 404
    assert response.json() == {"error": "未找到指定的视频源: NOPE", "result": None}


def test_search_one_is_exact_title_match(client, cookie, fake_source):
    response = _get(client, cookie, "/api/search/one", q="Fake Movie", resourceId=fake_source)
    assert response.status_code == 200
    results = response.json()["results"]
    # `vod_name` is "Fake Movie" exactly; "  Fake  Show  " is trimmed to
    # "Fake Show" by the mapper, so a prefix query must not match it.
    assert [r["title"] for r in results] == ["Fake Movie"]
    assert results[0]["id"] == "2"
    assert "max-age=" in response.headers["cache-control"]
    assert response.headers["netlify-vary"] == "query"

    # A prefix is not a match: OrionTV filters on `title === q`.
    response = _get(client, cookie, "/api/search/one", q="Fake", resourceId=fake_source)
    assert response.status_code == 404
    assert response.json() == {"error": "未找到结果", "result": None}


# ---------------------------------------------------------------------------
# /api/search/resources
# ---------------------------------------------------------------------------


def test_search_resources_lists_the_enabled_sources(client, cookie, fake_only_source):
    body = _get(client, cookie, "/api/search/resources").json()
    assert isinstance(body, list)
    assert [s["key"] for s in body] == [fake_only_source]
    assert body[0]["api"].startswith("http://127.0.0.1:")
    # `detail` is part of the shape the OrionTV client reads, even when null.
    assert "detail" in body[0]


def test_search_resources_needs_no_cookie(client, cookie, fake_only_source):
    # The route has no auth check of its own, and the cookie only narrows which
    # sources appear. Behind the gate an unauthenticated call is answered by the
    # gate, so the "no cookie" case is asserted as the gate's 401, and the
    # narrowing is asserted with the owner's cookie.
    _anon(client)
    assert client.get("/api/search/resources").content == b"Unauthorized"

    body = _get(client, cookie, "/api/search/resources").json()
    assert [s["key"] for s in body] == [fake_only_source]


# ---------------------------------------------------------------------------
# /api/search/suggestions
# ---------------------------------------------------------------------------


def test_suggestions_empty_query_is_a_json_object_not_a_stream(client, cookie, fake_only_source):
    response = _get(client, cookie, "/api/search/suggestions", q="")
    assert response.status_code == 200
    assert response.json() == {"suggestions": []}


def test_suggestions_is_ndjson_one_line_per_page(client, cookie, fake_only_source):
    response = _get(client, cookie, "/api/search/suggestions", q="fake")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json; charset=utf-8"
    text = response.text
    lines = [line for line in text.split("\n") if line.strip()]
    # pagecount is 2, so two pages, each a complete object on its own line.
    assert len(lines) == 2
    assert text.endswith("\n")
    for line in lines:
        parsed = json.loads(line)
        assert list(parsed) == ["suggestions"]
        assert all(list(item) == ["text"] for item in parsed["suggestions"])


def test_suggestions_scores_the_exact_keyword_first(client, cookie, fake_only_source):
    lines = [
        json.loads(line)
        for line in _get(client, cookie, "/api/search/suggestions", q="fake").text.split("\n")
        if line.strip()
    ]
    # The candidate set is keywords that *contain* the query, so page 1
    # ("Fake Show", "Fake Movie") contributes only "Fake", and page 2
    # ("Fake Season Two") likewise — "Show", "Movie", "Season", "Two" never
    # enter the scoring at all.
    assert [s["text"] for s in lines[0]["suggestions"]] == ["Fake"]
    assert [s["text"] for s in lines[1]["suggestions"]] == ["Fake"]


def test_suggestions_order_is_score_then_frequency(client, cookie, fake_only_source):
    """A query that matches several keywords, so the ranking is observable.

    `q=o` hits Show and Movie on page 1, Season and Two on page 2. All four get
    the same base 1.5 (substring match) and the same frequency bonus, so the
    order is decided by the length bonus `1/(1+diff*0.1)*0.3` — the shorter
    keyword wins: Show(4) > Movie(5), Two(3) > Season(6).
    """
    lines = [
        json.loads(line)
        for line in _get(client, cookie, "/api/search/suggestions", q="o").text.split("\n")
        if line.strip()
    ]
    assert [s["text"] for s in lines[0]["suggestions"]] == ["Show", "Movie"]
    assert [s["text"] for s in lines[1]["suggestions"]] == ["Two", "Season"]


# ---------------------------------------------------------------------------
# /api/search/ws
# ---------------------------------------------------------------------------


def _sse_events(text: str) -> list:
    blocks = [b for b in text.split("\n\n") if b.strip()]
    assert all(b.startswith("data: ") for b in blocks), "every event is `data: ` + JSON"
    return [json.loads(b[5:].strip()) for b in blocks]


def test_search_ws_needs_a_cookie(client, fake_only_source):
    _anon(client)
    response = client.get("/api/search/ws", params={"q": "fake"})
    assert response.status_code == 401
    assert response.content == b"Unauthorized"


def test_search_ws_event_vocabulary(client, cookie, fake_only_source):
    response = _get(client, cookie, "/api/search/ws", q="fake")
    assert response.status_code == 200
    # Upstream sent `text/event-stream` with no charset, and Starlette appends
    # `; charset=utf-8` to any `text/*` media_type, so the header is pinned.
    assert response.headers["content-type"] == "text/event-stream"

    events = _sse_events(response.text)
    types = [e["type"] for e in events]
    assert types[0] == "start"
    assert events[0]["query"] == "fake" and events[0]["totalSources"] == 1
    assert types[-1] == "complete"
    assert "source_result" in types
    result = [e for e in events if e["type"] == "source_result"][0]
    assert result["source"] == fake_only_source and result["sourceName"] == "FAKE"
    assert len(result["results"]) == 3


def test_search_ws_complete_reports_zero_total_results(client, cookie, fake_only_source):
    """The kept upstream bug: `totalResults` is always 0.

    Upstream declared `allResults` twice, and the per-site one shadows the array
    the `complete` event reads, so nothing is ever appended. A "fix" here would
    change what the client sees, so the 0 is asserted, not repaired.
    """
    events = _sse_events(_get(client, cookie, "/api/search/ws", q="fake").text)
    complete = events[-1]
    assert complete["totalResults"] == 0
    assert complete["completedSources"] == 1


def test_search_ws_empty_query_is_400(client, cookie, fake_only_source):
    response = _get(client, cookie, "/api/search/ws", q="")
    assert response.status_code == 400
    assert response.json() == {"error": "搜索关键词不能为空"}
