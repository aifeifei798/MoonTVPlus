"""`/api/douban/search` — the title search the record cards use, offline.

Upstream MoonTV has no title search anywhere, so this route is additive surface
rather than a parity question: it exists so 继续观看 / 追更 / 收藏 cards can
prefer a douban poster over whatever the source's own cover is. The only
anonymous surface douban still leaves is the `subject_search` HTML page and its
`window.__DATA__ = {...}` literal, so the test feeds a canned page and asserts
the parse, the `{code, message, list}` shape, and the stale-cache behaviour the
other douban routes already have.

No network: `fetch_douban_html` is replaced with a fixture string.
"""

from __future__ import annotations

from moontv import douban
from moontv.douban import DoubanHttpError, _search_item, search_douban
from moontv.routes import douban as douban_route

FIXTURE = """
<!DOCTYPE html><html><head><script>
window.__DATA__ = {"count":15,"items":[
  {"id":3203143,"title":"\u82f1\u96c4\u4eba\u7269\u200e (2001)","cover_url":"https://img9.doubanio.com/view/photo/s_ratio_poster/public/p2515774776.webp","rating":{"value":4.4}},
  {"id":30190595,"title":"\u6843\u82b1\u5c0f\u9707\u738b\u200e (2017)","cover_url":"https://img2.doubanio.com/view/photo/s_ratio_poster/public/p2731814611.webp","rating":{"value":2.6}},
  {"id":25702995,"title":"\u83ab\u5c0f\u795d","rating":{"value":0}},
  {"id":1,"title":"\u65e0\u6570\u5b57"}
]};
</script></head><body>results</body></html>
"""


def _stub_html(monkeypatch, html: str) -> list[str]:
    attempted: list[str] = []

    async def fake(url: str) -> str:
        attempted.append(url)
        return html

    monkeypatch.setattr(douban, "fetch_douban_html", fake)
    return attempted


def test_search_item_maps_the_douban_shape():
    assert _search_item(
        {
            "id": 3203143,
            "title": "英雄人物‎ (2001)",
            "cover_url": "https://img9.doubanio.com/x.webp",
            "rating": {"value": 4.4},
        }
    ) == {
        "id": 3203143,
        "title": "英雄人物‎ (2001)",
        "poster": "https://img9.doubanio.com/x.webp",
        "rate": "4.4",
        "year": "2001",
    }


def test_search_item_tolerates_missing_cover_and_rating():
    # A rating of 0 is falsy in JS too, so `rate` is empty, not "0.0"; an absent
    # cover and an absent year are empty strings, which `strip_none` keeps.
    assert _search_item({"id": 25702995, "title": "无数字"}) == {
        "id": 25702995,
        "title": "无数字",
        "poster": "",
        "rate": "",
        "year": "",
    }


async def test_search_parses_the_data_literal(monkeypatch):
    attempted = _stub_html(monkeypatch, FIXTURE)
    items = await search_douban("英雄人物")
    assert attempted == [
        "https://search.douban.com/movie/subject_search?search_text=%E8%8B%B1%E9%9B%84%E4%BA%BA%E7%89%A9&cat=movie"
    ]
    assert items[0] == {
        "id": 3203143,
        "title": "英雄人物‎ (2001)",
        "poster": "https://img9.doubanio.com/view/photo/s_ratio_poster/public/p2515774776.webp",
        "rate": "4.4",
        "year": "2001",
    }
    assert len(items) == 4


async def test_page_without_the_literal_is_a_douban_error(monkeypatch):
    _stub_html(monkeypatch, "<html><body>login required</body></html>")
    try:
        await search_douban("英雄人物")
    except DoubanHttpError as error:
        assert str(error) == "HTTP error! Status: 0"
    else:
        raise AssertionError("a page we cannot parse must not count as a hit")


def test_missing_query_is_400(client, cookie):
    response = client.get("/api/douban/search", headers={"Cookie": cookie})
    assert response.status_code == 400
    assert response.json() == {"error": "缺少必要参数: q"}


def test_search_is_behind_the_auth_gate(client):
    response = client.get("/api/douban/search", params={"q": "x"})
    assert response.status_code == 401


def test_route_shape_and_cache_headers(client, cookie, monkeypatch):
    async def fake(query: str) -> list[dict]:
        return [{"id": 1, "title": query, "poster": "https://img3.doubanio.com/a.jpg", "rate": "9.0"}]

    # The route imported `search_douban` by name, so patch the route module.
    monkeypatch.setattr(douban_route, "search_douban", fake)
    response = client.get("/api/douban/search", params={"q": "英雄人物"}, headers={"Cookie": cookie})
    assert response.status_code == 200
    assert response.json() == {
        "code": 200,
        "message": "获取成功",
        "list": [
            {"id": 1, "title": "英雄人物", "poster": "https://img3.doubanio.com/a.jpg", "rate": "9.0"}
        ],
    }
    assert "max-age=" in response.headers["cache-control"]
    assert response.headers["cdn-cache-control"].startswith("public, s-maxage=")
    assert "X-Douban-Stale" not in response.headers


def test_a_dead_douban_serves_the_cached_list(client, cookie, monkeypatch):
    async def good(query: str) -> list[dict]:
        return [{"id": 1, "title": query, "poster": "https://img3.doubanio.com/a.jpg"}]

    async def dead(query: str) -> list[dict]:
        raise DoubanHttpError(403)

    monkeypatch.setattr(douban_route, "search_douban", good)
    first = client.get("/api/douban/search", params={"q": "英雄人物"}, headers={"Cookie": cookie})
    assert first.status_code == 200

    monkeypatch.setattr(douban_route, "search_douban", dead)
    second = client.get("/api/douban/search", params={"q": "英雄人物"}, headers={"Cookie": cookie})
    assert second.status_code == 200
    assert second.headers["x-douban-stale"] == "1"
    assert second.json()["list"] == first.json()["list"]


def test_cold_failure_is_the_old_500(client, cookie, monkeypatch):
    async def dead(query: str) -> list[dict]:
        raise DoubanHttpError(403)

    monkeypatch.setattr(douban_route, "search_douban", dead)
    response = client.get("/api/douban/search", params={"q": "从未搜索过的标题"}, headers={"Cookie": cookie})
    assert response.status_code == 500
    assert response.json()["error"] == "获取豆瓣搜索数据失败"


async def test_an_absurd_query_is_refused_before_douban_is_hit(client, cookie, monkeypatch):
    """MoonTVPlus-only cap (PLAN.md change 39).

    `q` goes straight into a douban URL and into a `douban_cache` key, so an
    unbounded one is both a way to hammer a third party with a 4 KB query and
    unbounded cache growth. The check runs before the fetch, so nothing is
    attempted.
    """
    attempted = _stub_html(monkeypatch, FIXTURE)

    response = client.get(
        "/api/douban/search",
        params={"q": "英雄人物" * 100},
        headers={"Cookie": cookie},
    )
    assert response.status_code == 400
    assert response.json() == {"error": "搜索关键词过长"}
    assert attempted == []

    # Exactly at the cap is still a title-length query, and it works.
    response = client.get(
        "/api/douban/search", params={"q": "x" * douban_route.MAX_SEARCH_QUERY}, headers={"Cookie": cookie}
    )
    assert response.status_code == 200
    assert len(attempted) == 1

    # An empty query is refused by the existing parameter check, not the cap.
    response = client.get("/api/douban/search", params={"q": ""}, headers={"Cookie": cookie})
    assert response.status_code == 400
    assert response.json() == {"error": "缺少必要参数: q"}
