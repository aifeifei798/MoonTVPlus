"""`/api/admin/source/test` — the panel's source health check (MoonTVPlus addition).

The probe is the search request itself (`api + API_CONFIG["search"]["path"] +
encodeURIComponent('')`), so "alive" means "can answer a search". The empty `wd=`
is deliberate: it asks for the site's first page, so a live site does not have to
contain anything in particular. Offline, the fetch is stubbed at
`source_test.fetch_with_timeout`, which lets every branch of `_probe` be measured
without a network: HTTP status, non-JSON body, a JSON body with no `list`, and an
empty `list`.

The rules the harness pins:

  * an **empty** `list` is a zero-result *warning* (`count: 0`, `okCount + 1`),
    not a death — plenty of live sites answer an empty query with no rows
    (PLAN.md, change 18);
  * a JSON body without a `list` array, a non-JSON body, and a non-2xx status are
    deaths, with reasons `接口格式错误` / `HTTP <code>`;
  * a source with no `api` fails with `缺少 API 地址` and `ms: 0`, and the probe
    never runs (the stub records no call);
  * `CONCURRENCY = 5` is a real pool: with six sources, five are in flight at the
    first moment and the sixth waits for a slot;
  * the route is **read-only**: a test run cannot reorder, disable, re-enable or
    resurrect anything, so the SourceConfig projection is identical before and
    after;
  * `timeout` is seconds, clamped to `MAX_TIMEOUT_S = 60`; 0, NaN, and a value
    over 60 all fall back to `DEFAULT_TIMEOUT_S * 1000` (10000 ms);
  * the SSE framing is the panel's `split('\\n\\n')` + `data: ` reader, and the
    content type is pinned in the header map, so it is exactly
    `text/event-stream` with no charset.

The route is under the auth gate (`api/admin/source/test` is not in the matcher's
exclusion list), so an unauthenticated POST is the gate's plain-text 401.

The disconnect half is measured by driving `_stream` directly: take the `start`
event, give the loop a turn (the driver is an `ensure_future`, so it only runs
once the generator's `await out.get()` yields control), close the generator while
the five workers are in flight, and count the stub's *started* and *completed*
fetches. Five start, none finish, and closing does not start a sixth.
"""

from __future__ import annotations

import asyncio
import json

from moontv.routes import source_test
from moontv.storage.db import Db

# ---------------------------------------------------------------------------
# The stubbed fetch
# ---------------------------------------------------------------------------


class FakeResponse:
    """The two members `_probe` reads off an httpx response."""

    def __init__(self, payload, status: int = 200, broken_json: bool = False):
        self.status_code = status
        self._payload = payload
        self._broken_json = broken_json

    def json(self):
        if self._broken_json:
            raise ValueError("Unexpected token '<'")
        return self._payload


class Stub:
    """Records every probe, and can hold workers in flight for the pool probe."""

    def __init__(self, payload=None, status=200, broken_json=False, delay=0.0):
        self.payload = payload if payload is not None else {"list": [{"a": 1}, {"b": 2}], "pagecount": 2}
        self.status = status
        self.broken_json = broken_json
        self.delay = delay
        self.calls: list[tuple[str, int]] = []
        self.inflight = 0
        self.max_inflight = 0
        self.completed = 0

    async def fetch(self, url, headers, timeout_ms):
        self.calls.append((url, timeout_ms))
        self.inflight += 1
        self.max_inflight = max(self.max_inflight, self.inflight)
        if self.delay:
            await asyncio.sleep(self.delay)
        self.inflight -= 1
        self.completed += 1
        return FakeResponse(self.payload, self.status, self.broken_json)


def _stub(monkeypatch, **kwargs) -> Stub:
    stub = Stub(**kwargs)
    monkeypatch.setattr(source_test, "fetch_with_timeout", stub.fetch)
    return stub


def _post(client, cookie, body):
    return client.post("/api/admin/source/test", headers={"Cookie": cookie}, json=body)


def _events(response) -> list[dict]:
    """The panel's reader: `split('\\n\\n')` and `data: ` prefix."""
    text = response.content.decode("utf-8")
    events = []
    for block in text.split("\n\n"):
        if not block:
            continue
        assert block.startswith("data: ")
        events.append(json.loads(block[5:]))
    return events


def _sources(client, cookie) -> list[tuple]:
    """The SourceConfig projection the read-only assertion compares."""
    inner = client.get("/api/admin/config", headers={"Cookie": cookie}).json()["Config"]
    if isinstance(inner, str):
        inner = json.loads(inner)
    return [(s.get("key"), s.get("name"), s.get("api"), s.get("disabled")) for s in inner["SourceConfig"]]


def _add(client, cookie, key, api):
    response = client.post(
        "/api/admin/source",
        headers={"Cookie": cookie},
        json={"action": "add", "key": key, "name": key, "api": api},
    )
    assert response.status_code == 200, response.text


# ---------------------------------------------------------------------------
# Gate, targets, framing
# ---------------------------------------------------------------------------


def test_source_test_is_gated(client, clean_db):
    client.cookies.pop("auth", None)
    response = client.post("/api/admin/source/test", json={})
    assert response.status_code == 401
    assert response.content == b"Unauthorized"


def test_no_targets_answers_json_not_sse(client, cookie, fake_only_source):
    # The only source is deleted, so there is nothing to stream.
    response = client.post(
        "/api/admin/source",
        headers={"Cookie": cookie},
        json={"action": "batchDelete", "keys": [fake_only_source]},
    )
    assert response.status_code == 200

    response = _post(client, cookie, {})
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {"ok": True, "total": 0}
    assert response.headers["cache-control"] == "no-store"


def test_unknown_keys_answer_total_zero(client, cookie, fake_only_source):
    response = _post(client, cookie, {"keys": ["NOPE"]})
    assert response.status_code == 200
    assert response.json() == {"ok": True, "total": 0}


def test_sse_framing_and_vocabulary(client, cookie, fake_only_source, monkeypatch):
    stub = _stub(monkeypatch)
    response = _post(client, cookie, {})
    assert response.status_code == 200
    # Pinned in the header map, so Starlette's `text/*` charset rewrite is skipped.
    assert response.headers["content-type"] == "text/event-stream"
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"

    events = _events(response)
    assert [e["type"] for e in events] == ["start", "item_result", "complete"]
    assert events[0] == {"type": "start", "total": 1}
    result = events[1]
    assert result["key"] == "FAKE"
    assert result["name"] == "FAKE"
    assert result["count"] == 2
    assert result["pagecount"] == 2
    assert isinstance(result["ms"], int)
    complete = events[2]
    assert complete["okCount"] == 1
    assert complete["failedCount"] == 0
    assert complete["total"] == 1
    assert isinstance(complete["checkedAt"], int)

    # The probe is the search request with an empty `wd=`.
    url, timeout = stub.calls[0]
    assert url.endswith("?ac=videolist&wd=")
    assert timeout == 10000


def test_keys_filter_selects_a_subset(client, cookie, fake_only_source, monkeypatch):
    _add(client, cookie, "FAKE2", "http://127.0.0.1:9/api.php/provide/vod")
    stub = _stub(monkeypatch)
    response = _post(client, cookie, {"keys": ["FAKE2"]})
    events = _events(response)
    assert [e["type"] for e in events] == ["start", "item_result", "complete"]
    assert events[0]["total"] == 1
    assert events[1]["key"] == "FAKE2"
    assert len(stub.calls) == 1


# ---------------------------------------------------------------------------
# The probe's branches
# ---------------------------------------------------------------------------


def test_zero_result_is_a_warning_not_a_death(client, cookie, fake_only_source, monkeypatch):
    stub = _stub(monkeypatch, payload={"list": [], "pagecount": 0})
    events = _events(_post(client, cookie, {}))
    # PLAN.md change 18: an empty list is `count: 0` and counts as ok.
    assert events[1]["type"] == "item_result"
    assert events[1]["count"] == 0
    assert events[1]["pagecount"] == 0
    assert events[2]["okCount"] == 1
    assert events[2]["failedCount"] == 0


def test_non_json_body_is_a_format_error(client, cookie, fake_only_source, monkeypatch):
    _stub(monkeypatch, broken_json=True)
    events = _events(_post(client, cookie, {}))
    assert events[1]["type"] == "item_failed"
    assert events[1]["reason"] == "接口格式错误"
    assert events[2]["failedCount"] == 1


def test_json_without_a_list_is_a_format_error(client, cookie, fake_only_source, monkeypatch):
    _stub(monkeypatch, payload={"code": 0, "msg": "ok"})
    events = _events(_post(client, cookie, {}))
    assert events[1]["reason"] == "接口格式错误"


def test_http_status_is_a_failure(client, cookie, fake_only_source, monkeypatch):
    _stub(monkeypatch, status=403)
    events = _events(_post(client, cookie, {}))
    assert events[1]["type"] == "item_failed"
    assert events[1]["reason"] == "HTTP 403"


async def test_a_source_without_an_api_fails_without_a_fetch(client, cookie, fake_only_source, monkeypatch):
    # `add` refuses an empty api, so the row is seeded through the in-process
    # storage singleton (the same connection the running server uses).
    blob = await Db.get_admin_config()
    if isinstance(blob, str):
        blob = json.loads(blob)
    for source in blob["SourceConfig"]:
        if source["key"] == "FAKE":
            source["api"] = ""
    await Db.save_admin_config(blob)

    stub = _stub(monkeypatch)
    events = _events(_post(client, cookie, {}))
    assert events[1] == {
        "type": "item_failed",
        "key": "FAKE",
        "name": "FAKE",
        "ms": 0,
        "reason": "缺少 API 地址",
    }
    assert stub.calls == []


# ---------------------------------------------------------------------------
# Timeout, pool, disconnect, read-only
# ---------------------------------------------------------------------------


def test_timeout_is_seconds_and_clamped(client, cookie, fake_only_source, monkeypatch):
    for timeout, expected in ((3, 3000), (0, 10000), (99, 10000), (None, 10000), ("5", 5000)):
        stub = _stub(monkeypatch)
        _post(client, cookie, {"timeout": timeout})
        assert stub.calls[0][1] == expected, timeout


async def test_concurrency_is_five_workers(client, cookie, fake_only_source, monkeypatch):
    for i in range(2, 7):
        _add(client, cookie, f"FAKE{i}", f"http://127.0.0.1:9/api.php/provide/vod")

    stub = _stub(monkeypatch, delay=0.05)
    targets = [
        {"key": f"FAKE{i}", "name": f"FAKE{i}", "api": "http://127.0.0.1:9/api.php"}
        for i in range(1, 7)
    ]
    stream = source_test._stream(targets, 10000)
    events = []
    async for chunk in stream:
        events.append(chunk)
        if len(events) == 7:  # start + 6 results + complete
            break
    assert len(stub.calls) == 6
    # Five workers start at once; the sixth only gets a slot after one finishes.
    assert stub.max_inflight == 5


async def test_disconnect_cancels_the_remaining_work(client, cookie, fake_only_source, monkeypatch):
    stub = _stub(monkeypatch, delay=5.0)
    targets = [
        {"key": f"FAKE{i}", "name": f"FAKE{i}", "api": "http://127.0.0.1:9/api.php"}
        for i in range(1, 7)
    ]
    stream = source_test._stream(targets, 10000)
    first = await stream.__anext__()  # start
    assert first == b"data: {\"type\":\"start\",\"total\":6}\n\n"
    # The driver is an `ensure_future`, so it only runs once the generator's
    # `await out.get()` gives the loop a turn. Let the five workers get in
    # flight, then close the generator mid-flight.
    await asyncio.sleep(0.01)
    assert len(stub.calls) == 5  # the pool started five, the sixth never got a slot
    assert stub.completed == 0

    await stream.aclose()
    await asyncio.sleep(0.01)
    # The `finally` cancelled the driver: no new starts, no completions.
    assert len(stub.calls) == 5
    assert stub.completed == 0  # and none of them finished


async def test_the_route_is_read_only(client, cookie, fake_only_source, monkeypatch):
    _add(client, cookie, "FAKE2", "http://127.0.0.1:9/api.php/provide/vod")
    _add(client, cookie, "FAKE3", "http://127.0.0.1:9/api.php/provide/vod")
    client.post(
        "/api/admin/source",
        headers={"Cookie": cookie},
        json={"action": "disable", "key": "FAKE2"},
    )
    before = _sources(client, cookie)

    _stub(monkeypatch, status=403)
    _post(client, cookie, {})

    # A test run cannot reorder, re-enable, disable, delete, or resurrect.
    assert _sources(client, cookie) == before
