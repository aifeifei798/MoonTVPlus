"""`src/app/api/detail/route.ts` — one source's detail by `id`.

The detail page's fallback and the TVBox/OrionTV clients' direct call. Verified
with curl in the B6 session; this module pins the shapes offline:

  * the `id` format check is upstream's `/^[\\w-]+$/`, spelled `[A-Za-z0-9_-]`
    because Python's `\\w` also accepts accented letters;
  * `getDetailFromApi` copies `vod_name` **raw** (no trim, unlike the search
    mapper), and the optional fields (`poster`, `class`, `type_name`,
    `douban_id`) are present only when the source sent the key — so record 2 of
    the fake server has no `poster` key at all, not a null one;
  * the four CDN cache headers are part of the 200, and the three error shapes
    are 400s with the exact upstream strings.

The fake media server answers `?ac=videolist&ids=…` with page 1 regardless of
the id, so the detail is always record 1 — which is also what a real site does
when the id is not in its list.
"""

from __future__ import annotations


def _detail(client, cookie, **params):
    url = "/api/detail?" + "&".join(f"{k}={v}" for k, v in params.items())
    return client.get(url, headers={"Cookie": cookie})


def test_detail_requires_id_and_source(client, cookie, fake_source):
    for params in ({}, {"id": "1"}, {"source": fake_source}):
        response = _detail(client, cookie, **params)
        assert response.status_code == 400
        assert response.json() == {"error": "缺少必要参数"}


def test_detail_rejects_an_id_with_punctuation(client, cookie, fake_source):
    # `/^[\w-]+$/` is a full-string test, so `a!b` is not an id.
    response = _detail(client, cookie, id="a!b", source=fake_source)
    assert response.status_code == 400
    assert response.json() == {"error": "无效的视频ID格式"}

    # An accented letter is not JS's `\w`.
    response = _detail(client, cookie, id="café", source=fake_source)
    assert response.status_code == 400


def test_detail_rejects_an_unknown_source(client, cookie, fake_source):
    response = _detail(client, cookie, id="1", source="NOPE")
    assert response.status_code == 400
    assert response.json() == {"error": "无效的API来源"}


def test_detail_maps_the_source_record(client, cookie, fake_source):
    response = _detail(client, cookie, id="1", source=fake_source)
    assert response.status_code == 200
    body = response.json()
    # `vod_name` is "  Fake  Show  " in the source payload and detail does not
    # trim it, unlike the search mapper.
    assert body["title"] == "  Fake  Show  "
    assert body["id"] == "1"
    assert body["source"] == fake_source
    assert body["source_name"] == "FAKE"
    assert body["year"] == "2020"
    assert body["poster"] == "https://example.invalid/p1.jpg"
    assert body["type_name"] == "剧情"
    assert body["desc"] == "first page description"
    assert body["episodes"] == [
        "https://example.invalid/a.m3u8",
        "https://example.invalid/b.m3u8",
    ]
    assert body["episodes_titles"] == ["1", "2"]
    # `vod_class` is absent from the fake record, so the key is absent, not null.
    assert "class" not in body
    assert "douban_id" not in body


def test_detail_cache_headers(client, cookie, fake_source):
    response = _detail(client, cookie, id="1", source=fake_source)
    cache_time = 7200  # backend/config.json's cache_time
    assert response.headers["cache-control"] == f"public, max-age={cache_time}, s-maxage={cache_time}"
    assert response.headers["cdn-cache-control"] == f"public, s-maxage={cache_time}"
    assert response.headers["vercel-cdn-cache-control"] == f"public, s-maxage={cache_time}"
    assert response.headers["netlify-vary"] == "query"


def test_detail_optional_keys_track_the_source_payload(client, cookie, fake_source):
    # Record 2 has no vod_pic / type_name / vod_class, so those keys are absent.
    # The fake server ignores `ids`, so the detail is record 1 for every id;
    # the record-2 shape is asserted through the search route's mapper, which
    # reads the same payload.
    response = _detail(client, cookie, id="2", source=fake_source)
    assert response.status_code == 200
    assert response.json()["id"] == "2"
    assert response.json()["title"] == "  Fake  Show  "


def test_detail_is_gated(client, fake_source):
    client.cookies.pop("auth", None)
    response = client.get("/api/detail", params={"id": "1", "source": fake_source})
    assert response.status_code == 401
    assert response.content == b"Unauthorized"
