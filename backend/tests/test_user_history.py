"""`src/app/api/{searchhistory,skipconfigs,today-updated}/route.ts`.

The three per-user stores the app keeps beside the record tables. Verified with
curl in the B4 session; this module pins the shapes offline:

  * `searchhistory` answers with the list the server just wrote, not with what
    the client sent, and `add_search_history` deletes the keyword before
    re-inserting it, so a repeat is a move-to-front, not a duplicate;
  * a non-string `keyword` has no `.trim()` upstream, so the route throws and the
    `catch` answers 500 — the value is not accepted;
  * `skipconfigs` rebuilds the config field by field, so extra keys the client
    sent never reach storage, and `split_key` takes only the first two `+`
    parts (`"a+b+c"` → `("a", "b")`);
  * `today-updated` *normalizes* the record server-side, so a stored row carries
    every field with `""`/`0` where the client sent nothing, and the GET reads
    that normalized shape back. A non-list `items` is normalized to `[]`, not
    rejected; only a bad `date` (or no `date`) is a 400.

The gate covers all three paths, so the routes' own `未登录` / `Unauthorized`
branches are unreachable: an unauthenticated call is answered by the gate's
plain-text 401.

Two harness rules this module depends on:

  * every test takes `clean_db`, because the storage singleton and its DB file
    are shared across tests — without it the search history leaks from one test
    into the next and the assertions measure the leak;
  * query values are percent-encoded (`quote(v, safe="")`). A raw `+` in a query
    string decodes to a **space**, so `key=FAKE+2` reaches the route as
    `FAKE 2` and answers `无效的key格式` for a key that is well formed. The
    frontend sends `encodeURIComponent`, which is the same encoding.
"""

from __future__ import annotations

from urllib.parse import quote

DATE = "2026-10-14"


def _url(path, params):
    if not params:
        return path
    return path + "?" + "&".join(f"{k}={quote(str(v), safe='')}" for k, v in params.items())


def _get(client, cookie, path, **params):
    return client.get(_url(path, params), headers={"Cookie": cookie})


def _post(client, cookie, path, body):
    return client.post(path, headers={"Cookie": cookie}, json=body)


def _delete(client, cookie, path, **params):
    return client.delete(_url(path, params), headers={"Cookie": cookie})


# ---------------------------------------------------------------------------
# /api/searchhistory
# ---------------------------------------------------------------------------


def test_searchhistory_starts_empty(client, cookie, clean_db):
    assert _get(client, cookie, "/api/searchhistory").json() == []


def test_searchhistory_trims_and_returns_the_stored_list(client, cookie, clean_db):
    response = _post(client, cookie, "/api/searchhistory", {"keyword": "  spider man  "})
    assert response.status_code == 200
    # The answer is the server's list, newest first, not the keyword it received.
    assert response.json() == ["spider man"]
    assert _get(client, cookie, "/api/searchhistory").json() == ["spider man"]


def test_searchhistory_rejects_an_empty_keyword(client, cookie, clean_db):
    for keyword in ("", "   ", None):
        response = _post(client, cookie, "/api/searchhistory", {"keyword": keyword})
        assert response.status_code == 400
        assert response.json() == {"error": "Keyword is required"}
    assert _get(client, cookie, "/api/searchhistory").json() == []


def test_searchhistory_500s_on_a_non_string_keyword(client, cookie, clean_db):
    # `"123".trim()` is fine, `123.trim()` is not: upstream threw into the catch.
    response = _post(client, cookie, "/api/searchhistory", {"keyword": 123})
    assert response.status_code == 500
    assert response.json() == {"error": "Internal Server Error"}
    assert _get(client, cookie, "/api/searchhistory").json() == []


def test_searchhistory_dedupes(client, cookie, clean_db):
    for keyword in ("a", "b", "a"):
        _post(client, cookie, "/api/searchhistory", {"keyword": keyword})
    # Re-adding `a` deletes the old row and re-inserts, so the order is b, a.
    assert _get(client, cookie, "/api/searchhistory").json() == ["b", "a"]


def test_searchhistory_caps_at_twenty(client, cookie, clean_db):
    for i in range(25):
        _post(client, cookie, "/api/searchhistory", {"keyword": f"kw{i}"})
    history = _get(client, cookie, "/api/searchhistory").json()
    # The cap is the client's 20, applied on read and on write. `CURRENT_TIMESTAMP`
    # has second resolution, so 25 inserts in one test land on one timestamp and
    # the write-side prune (`id NOT IN (… ORDER BY created_at DESC LIMIT 20)`)
    # keeps the **first** 20 rows on the tie — the same SQL D1 ran, so this is
    # upstream's behavior, not a port gap. The read-side `ORDER BY … LIMIT 20`
    # is what keeps the newest 20 when the inserts are spread over time.
    assert len(history) == 20
    assert set(history) == {f"kw{i}" for i in range(20)}


def test_searchhistory_delete_one_and_all(client, cookie, clean_db):
    for keyword in ("a", "b"):
        _post(client, cookie, "/api/searchhistory", {"keyword": keyword})

    response = _delete(client, cookie, "/api/searchhistory", keyword="a")
    assert response.status_code == 200
    assert response.json() == {"success": True}
    assert _get(client, cookie, "/api/searchhistory").json() == ["b"]

    # No keyword clears the whole store.
    _delete(client, cookie, "/api/searchhistory")
    assert _get(client, cookie, "/api/searchhistory").json() == []


# ---------------------------------------------------------------------------
# /api/skipconfigs
# ---------------------------------------------------------------------------


def test_skipconfigs_round_trip(client, cookie, clean_db):
    response = _post(
        client,
        cookie,
        "/api/skipconfigs",
        {
            "key": "FAKE+2",
            "config": {"enable": True, "intro_time": "30", "outro_time": 45, "extra": "x"},
        },
    )
    assert response.status_code == 200
    assert response.json() == {"success": True}

    body = _get(client, cookie, "/api/skipconfigs", source="FAKE", id="2").json()
    # Field-by-field rebuild: `extra` never reaches storage, and the string
    # "30" becomes the number 30 (INTEGER affinity keeps it an int on the wire).
    assert body == {"enable": True, "intro_time": 30, "outro_time": 45}


def test_skipconfigs_get_all_is_keyed_by_source_plus_id(client, cookie, clean_db):
    _post(client, cookie, "/api/skipconfigs", {"key": "FAKE+2", "config": {"enable": True, "intro_time": 1, "outro_time": 2}})
    _post(client, cookie, "/api/skipconfigs", {"key": "OTHER+7", "config": {"enable": False, "intro_time": 0, "outro_time": 0}})
    all_configs = _get(client, cookie, "/api/skipconfigs").json()
    assert sorted(all_configs) == ["FAKE+2", "OTHER+7"]
    assert all_configs["FAKE+2"]["intro_time"] == 1


def test_skipconfigs_missing_key_is_400(client, cookie, clean_db):
    for body in ({}, {"key": "FAKE+2"}, {"config": {"enable": True}}):
        response = _post(client, cookie, "/api/skipconfigs", body)
        assert response.status_code == 400
        assert response.json() == {"error": "缺少必要参数"}


def test_skipconfigs_key_without_a_separator_is_400(client, cookie, clean_db):
    # An empty key fails the truthiness check first; a key with no `+` (or an
    # empty half) reaches the format check.
    response = _post(client, cookie, "/api/skipconfigs", {"key": "", "config": {"enable": True}})
    assert response.status_code == 400
    assert response.json() == {"error": "缺少必要参数"}

    for key in ("FAKE", "FAKE+"):
        response = _post(client, cookie, "/api/skipconfigs", {"key": key, "config": {"enable": True}})
        assert response.status_code == 400
        assert response.json() == {"error": "无效的key格式"}


def test_skipconfigs_deletes(client, cookie, clean_db):
    _post(client, cookie, "/api/skipconfigs", {"key": "FAKE+2", "config": {"enable": True, "intro_time": 1, "outro_time": 2}})
    response = _delete(client, cookie, "/api/skipconfigs", key="FAKE+2")
    assert response.status_code == 200
    assert response.json() == {"success": True}
    assert _get(client, cookie, "/api/skipconfigs", source="FAKE", id="2").json() is None

    # The delete paths share the key checks.
    response = _delete(client, cookie, "/api/skipconfigs")
    assert response.status_code == 400
    assert response.json() == {"error": "缺少必要参数"}

    response = _delete(client, cookie, "/api/skipconfigs", key="FAKE")
    assert response.status_code == 400
    assert response.json() == {"error": "无效的key格式"}


def test_skipconfigs_extra_plus_parts_take_the_first_two(client, cookie, clean_db):
    # `"a+b+c"` destructures to ("a", "b"), so the config lands under a+b.
    _post(client, cookie, "/api/skipconfigs", {"key": "a+b+c", "config": {"enable": True, "intro_time": 5, "outro_time": 6}})
    assert _get(client, cookie, "/api/skipconfigs", source="a", id="b").json() == {
        "enable": True,
        "intro_time": 5,
        "outro_time": 6,
    }


# ---------------------------------------------------------------------------
# /api/today-updated
# ---------------------------------------------------------------------------


def test_today_updated_starts_null(client, cookie, clean_db):
    assert _get(client, cookie, "/api/today-updated").json() is None


def test_today_updated_normalizes_the_record(client, cookie, clean_db):
    response = _post(
        client,
        cookie,
        "/api/today-updated",
        {
            "date": DATE,
            "items": [
                {"source": "FAKE", "id": "3", "title": "Show", "episodes": 5, "oldEpisodes": 4},
                "not an object",
            ],
        },
    )
    assert response.status_code == 200
    assert response.json() == {"success": True}

    record = _get(client, cookie, "/api/today-updated").json()
    assert record["date"] == DATE
    # The non-dict item is dropped, and every missing field is filled with
    # "" / 0, so the stored row is complete.
    assert record["items"] == [
        {
            "source": "FAKE",
            "id": "3",
            "title": "Show",
            "poster": "",
            "episodes": 5,
            "watchedEpisodes": 0,
            "unwatchedEpisodes": 0,
            "source_name": "",
            "year": "",
            "save_time": 0,
            "oldEpisodes": 4,
            "newEpisodes": 0,
        }
    ]


def test_today_updated_rejects_a_bad_date(client, cookie, clean_db):
    for body in (
        {},
        {"date": "2026-10-1", "items": []},
        {"date": 2026, "items": []},
        {"date": "2026-10-14T00:00", "items": []},
    ):
        response = _post(client, cookie, "/api/today-updated", body)
        assert response.status_code == 400, body
        assert response.json() == {"error": "无效的记录格式"}


def test_today_updated_normalizes_a_non_list_items(client, cookie, clean_db):
    # `items` is read as `[]` when it is not a list, so this is a 200 with an
    # empty snapshot, not a 400.
    response = _post(client, cookie, "/api/today-updated", {"date": DATE, "items": "not a list"})
    assert response.status_code == 200
    assert _get(client, cookie, "/api/today-updated").json() == {"date": DATE, "items": []}

    # `items` missing entirely is the same.
    response = _post(client, cookie, "/api/today-updated", {"date": DATE})
    assert response.status_code == 200
    assert _get(client, cookie, "/api/today-updated").json() == {"date": DATE, "items": []}


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


def test_user_history_paths_are_gated(client, clean_db):
    client.cookies.pop("auth", None)
    for method, path in (
        ("get", "/api/searchhistory"),
        ("post", "/api/searchhistory"),
        ("get", "/api/skipconfigs"),
        ("get", "/api/today-updated"),
    ):
        response = getattr(client, method)(path)
        assert response.status_code == 401, (method, path)
        assert response.content == b"Unauthorized", (method, path)
