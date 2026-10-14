"""`src/app/api/tvbox/{config,categories}/route.ts` — the TVBox/OrionTV surface.

Verified with curl in the B6 session; this module pins the shapes offline:

  * `config` gates on `SiteConfig.TVBoxEnabled` + `TVBoxPassword`, decodes `un`
    with node's **never-throwing** base64 (the `参数 un 非法` branch is dead),
    and puts `douban_custom` first with `api` built from `getRequestOrigin`.
  * `categories` uses the raw `url.origin` for its self-fetch, so behind a proxy
    the two routes disagree exactly as they did upstream. The self-fetch cannot
    reach `http://testserver` from an httpx client, so it is stubbed here: the
    stub records the query string, which is the part of this route that has real
    logic (the 热门/最新 omissions, the `t` index, the `pg`/`pagesize` clamp).
  * `categories` never checks a password; only `config` does.

Both paths are in the middleware's negative lookahead, so unlike the search
routes their own 401/403 branches are reachable: the gate never answers them.

The fake media server is the shared one from `conftest.py`, and the config is
`fake_only_source` so the site list is exactly one entry.
"""

from __future__ import annotations

import datetime

from moontv.routes import tvbox as tvbox_module

PASSWORD = "tvbox-pass"
UN = "dGVzdC1vd25lcg=="  # base64 of "test-owner"


def _tvbox_on(client, cookie, fake_only_source):
    """Enable TVBox with a custom password through the panel's own route."""
    response = client.post(
        "/api/admin/tvbox",
        headers={"Cookie": cookie},
        json={"enabled": True, "mode": "custom", "password": PASSWORD},
    )
    assert response.status_code == 200, response.text
    return PASSWORD


def _get(client, cookie, path, **params):
    url = path
    if params:
        url += "?" + "&".join(f"{k}={v}" for k, v in params.items())
    return client.get(url, headers={"Cookie": cookie})


# ---------------------------------------------------------------------------
# /api/tvbox/config
# ---------------------------------------------------------------------------


def test_tvbox_routes_bypass_the_auth_gate(client, fake_only_source):
    """`api/tvbox/config` and `api/tvbox/categories` are in the matcher's negative
    lookahead, so an unauthenticated call reaches the route: the flag check wins,
    and the password check is the 401 the client sees. The gate's plain-text
    `Unauthorized` never appears on these two paths.
    """
    client.cookies.pop("auth", None)
    response = client.get("/api/tvbox/config", params={"un": UN})
    assert response.status_code == 403
    assert response.json() == {"error": "TVBox 接口未开启"}

    response = client.get("/api/tvbox/categories")
    assert response.status_code == 403
    assert response.json() == {"error": "TVBox 接口未开启"}


def test_tvbox_config_requires_un(client, cookie, fake_only_source):
    _tvbox_on(client, cookie, fake_only_source)
    response = _get(client, cookie, "/api/tvbox/config")
    assert response.status_code == 400
    assert response.json() == {"error": "缺少参数 un"}


def test_tvbox_config_refuses_when_disabled(client, cookie, fake_only_source):
    response = _get(client, cookie, "/api/tvbox/config", un=UN)
    assert response.status_code == 403
    assert response.json() == {"error": "TVBox 接口未开启"}


def test_tvbox_config_password_check(client, cookie, fake_only_source):
    _tvbox_on(client, cookie, fake_only_source)
    # No password at all, and a wrong one, are the same 401.
    for params in ({"un": UN}, {"un": UN, "pwd": "nope"}):
        response = _get(client, cookie, "/api/tvbox/config", **params)
        assert response.status_code == 401
        assert response.json() == {"error": "密码错误或未提供"}


def test_tvbox_config_payload(client, cookie, fake_only_source):
    password = _tvbox_on(client, cookie, fake_only_source)
    response = _get(client, cookie, "/api/tvbox/config", un=UN, pwd=password)
    assert response.status_code == 200
    body = response.json()
    assert list(body) == ["sites", "parses", "lives", "ads"]
    assert body["parses"] == [] and body["lives"] == [] and body["ads"] == []

    first = body["sites"][0]
    assert first["key"] == "douban_custom"
    assert first["api"] == "http://testserver/api/tvbox/categories"
    assert first["searchable"] == 0
    assert first["ext"] == ""

    site = body["sites"][1]
    assert site == {
        "key": "FAKE",
        "api": site["api"],
        "name": "FAKE",
        "type": 1,
        "searchable": 1,
        "quickSearch": 1,
        "ext": "",
    }
    assert site["api"].startswith("http://127.0.0.1:")
    assert response.headers["cache-control"].startswith("public, max-age=")


def test_tvbox_config_decodes_loose_base64(client, cookie, fake_only_source):
    """Node's decoder skips non-alphabet bytes and stops at `=`, so garbage `un`
    never reaches the dead `参数 un 非法` branch. A `un` that decodes to a
    username with no group is accepted and sees every enabled source.
    """
    password = _tvbox_on(client, cookie, fake_only_source)
    response = _get(client, cookie, "/api/tvbox/config", un="!!!MTkyLjE2OC4xLjE6ODAK", pwd=password)
    assert response.status_code == 200
    assert len(response.json()["sites"]) == 2


# ---------------------------------------------------------------------------
# /api/tvbox/categories
# ---------------------------------------------------------------------------


def _stub_fetch(monkeypatch, records: list):
    """Stand in for the route's self-fetch of `/api/douban/recommends`.

    `TestClient`'s origin is `http://testserver`, which no real httpx client can
    reach, so the self-fetch is stubbed: the recorded URL is the query the route
    built, which is where its logic lives.
    """

    async def fetch_json(url):
        records.append(url)
        return {
            "list": [
                {"id": "1", "title": "Stub Show", "poster": "https://example.invalid/p.jpg",
                 "year": "2020", "rate": "8.5"},
                {"id": "2", "title": "Stub Movie", "poster": None, "year": "", "rate": ""},
            ]
        }

    monkeypatch.setattr(tvbox_module, "fetch_json", fetch_json)


def test_tvbox_categories_needs_no_password(client, cookie, fake_only_source):
    # Enabled, no password: categories checks only the flag, not the password.
    _tvbox_on(client, cookie, fake_only_source)
    response = _get(client, cookie, "/api/tvbox/categories")
    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 1 and body["msg"] == "success"
    assert body["list"] == []
    names = [c["type_name"] for c in body["class"]]
    assert names[:4] == ["电影·热门", "电影·最新", "电影·经典", "电影·豆瓣高分"]
    assert names[4:] == ["剧集·热门", "剧集·美剧", "剧集·英剧", "剧集·韩剧",
                         "剧集·日剧", "剧集·国产剧", "剧集·日本动画"]
    assert [c["type_id"] for c in body["class"]] == list(range(1, 12))


def test_tvbox_categories_refuses_when_disabled(client, cookie, fake_only_source):
    response = _get(client, cookie, "/api/tvbox/categories")
    assert response.status_code == 403
    assert response.json() == {"error": "TVBox 接口未开启"}


def test_tvbox_categories_hot_omits_the_category(client, cookie, fake_only_source, monkeypatch):
    records: list = []
    _stub_fetch(monkeypatch, records)
    _tvbox_on(client, cookie, fake_only_source)
    response = _get(client, cookie, "/api/tvbox/categories", t="1")
    assert response.status_code == 200
    # t=1 is 电影·热门: category and label are omitted, so the backend falls
    # back to its recommends; only kind/start/limit remain.
    query = records[0].split("?", 1)[1]
    assert query.startswith("kind=movie")
    assert "category" not in query
    assert "start=0" in query and "limit=20" in query
    assert "sort" not in query

    body = response.json()
    assert body["page"] == 1 and body["limit"] == 20 and body["pagecount"] == 999
    assert body["total"] == 0
    assert body["list"][0] == {
        "vod_id": "1",
        "vod_name": "Stub Show",
        "vod_pic": "https://example.invalid/p.jpg",
        "vod_year": "2020",
        "vod_remarks": "8.5",
    }
    # `strip_none`: the stub's null poster is absent, not null.
    assert "vod_pic" not in body["list"][1]
    assert body["list"][1]["vod_year"] == "" and body["list"][1]["vod_remarks"] == ""


def test_tvbox_categories_newest_pins_the_year_and_sorts(client, cookie, fake_only_source, monkeypatch):
    records: list = []
    _stub_fetch(monkeypatch, records)
    _tvbox_on(client, cookie, fake_only_source)
    _get(client, cookie, "/api/tvbox/categories", t="2")
    query = records[0].split("?", 1)[1]
    year = datetime.datetime.now(datetime.timezone.utc).year
    assert f"year={year}" in query
    assert "sort=time" in query
    assert "category" not in query


def test_tvbox_categories_wd_overrides_label(client, cookie, fake_only_source, monkeypatch):
    records: list = []
    _stub_fetch(monkeypatch, records)
    _tvbox_on(client, cookie, fake_only_source)
    _get(client, cookie, "/api/tvbox/categories", t="3", wd="spider")
    query = records[0].split("?", 1)[1]
    # t=3 is 电影·经典, and `wd` replaces its label. The category itself is kept:
    # only 热门 and 最新 drop it, so the backend still filters by 经典.
    assert "label=spider" in query
    assert "category=%E7%BB%8F%E5%85%B8" in query


def test_tvbox_categories_clamps_pg_and_pagesize(client, cookie, fake_only_source, monkeypatch):
    records: list = []
    _stub_fetch(monkeypatch, records)
    _tvbox_on(client, cookie, fake_only_source)
    _get(client, cookie, "/api/tvbox/categories", t="1", pg="2", pagesize="80")
    query = records[0].split("?", 1)[1]
    # pg=2 with the clamped pagesize 50 → start 50.
    assert "start=50" in query and "limit=50" in query

    # pg=0 is clamped up to 1, pagesize=0 to 1.
    _get(client, cookie, "/api/tvbox/categories", t="1", pg="0", pagesize="0")
    query = records[1].split("?", 1)[1]
    assert "start=0" in query and "limit=1" in query


def test_tvbox_categories_custom_category_appends_a_selector(client, cookie, fake_only_source, monkeypatch):
    add = client.post(
        "/api/admin/category",
        headers={"Cookie": cookie},
        json={"action": "add", "name": "Anime", "type": "tv", "query": "animation"},
    )
    assert add.status_code == 200, add.text
    _tvbox_on(client, cookie, fake_only_source)

    listing = _get(client, cookie, "/api/tvbox/categories").json()
    assert listing["class"][-1] == {"type_id": 12, "type_name": "Anime"}

    records: list = []
    _stub_fetch(monkeypatch, records)
    _get(client, cookie, "/api/tvbox/categories", t="12")
    query = records[0].split("?", 1)[1]
    assert "kind=tv" in query and "label=animation" in query


def test_tvbox_categories_out_of_range_t_falls_back_to_movie(client, cookie, fake_only_source, monkeypatch):
    records: list = []
    _stub_fetch(monkeypatch, records)
    _tvbox_on(client, cookie, fake_only_source)
    response = _get(client, cookie, "/api/tvbox/categories", t="99")
    assert response.status_code == 200
    # `t` past the selector list keeps the defaults: kind=movie, no category.
    query = records[0].split("?", 1)[1]
    assert query.startswith("kind=movie")
    assert "label" not in query
