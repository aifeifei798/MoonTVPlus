"""Check the /api/admin/source/test health route end to end.

Usage: backend/.venv/bin/python <skill>/scripts/source_health_check.py

Two halves, because the two questions are different:

  * The deployment's own sources are only checked for *consistency* — the
    events must be exactly one per source, framed as `data: {...}\n\n`,
    and `okCount + failedCount == total`. Their verdicts are not asserted,
    because whether a third-party `?ac=videolist` site answers is a property
    of the network, not of this repo (this box has no direct access to most
    of them, which is the same reason `/api/search` reports them failed).

  * The classification itself is asserted against a local server the harness
    starts, so alive / HTTP 404 / non-JSON body / timeout are deterministic.
    The expected reason strings are imported from the code (`FORMAT_ERROR`,
    `FetchTimeout`'s message) rather than typed here — CJK typed into a
    harness command arrives as different codepoints and the comparison
    silently tests nothing.

It mutates the deployment and restores it: the seeded keys are ASCII
(`zzok`/`zzbad`/`zzmalformed`/`zzslow`), they are deleted afterwards, and the
original source order is replayed, so it is safe to run against a live site.
"""

import json
import pathlib
import sys
import threading
import time

from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

import httpx

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from _harness import BASE, auth_cookie

# The route's own vocabulary, so no CJK literal is typed into this file.
from moontv.routes.source_test import FORMAT_ERROR
from moontv.fetch import FetchTimeout

TTL_WAIT = 6.0  # > config.py's _CONFIG_TTL_S = 5.0
SEEDED = ("zzok", "zzbad", "zzmalformed", "zzslow")
KINDS = ("ok", "bad", "malformed", "slow")

results = []


def check(label, ok, detail=""):
    results.append((label, ok, detail))


# --- the deterministic probe targets: one local server, four shapes ---------


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if "/ok/" in self.path:
            body = json.dumps(
                {"code": 0, "list": [{"v": 1}, {"v": 2}, {"v": 3}], "pagecount": 7, "total": 3}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif "/malformed/" in self.path:
            body = b"<html><body>not json</body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif "/slow/" in self.path:
            time.sleep(5)  # longer than the 1 s timeout the harness asks for
            self.send_response(200)
            self.end_headers()
        else:
            self.send_error(404)

    def log_message(self, *args):  # keep the harness output clean
        pass

    def log_error(self, *args):
        pass


probe = None


def start_probe_server():
    global probe
    for candidate in (8911, 8912, 8913, 8914):
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", candidate), Handler)
        except OSError:
            continue
        probe = srv
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return candidate
    raise SystemExit("could not bind a probe server")


def api_for(kind):
    return f"http://127.0.0.1:{probe.server_port}/{kind}/api.php/provide/vod"


def post(path, body, timeout=90, cookie=True):
    hdr = {"Content-Type": "application/json"}
    if cookie:
        hdr["Cookie"] = "auth=" + AUTH
    r = httpx.post(BASE + path, headers=hdr, json=body, timeout=timeout, trust_env=False)
    return r


def sse(r):
    """The `data: {...}` + blank line framing, as the panel's reader sees it."""
    if not r.text.endswith("\n\n"):
        return None
    events = []
    for chunk in r.text.split("\n\n"):
        if not chunk:
            continue
        if not chunk.startswith("data: "):
            return None
        try:
            events.append(json.loads(chunk[6:]))
        except ValueError:
            return None
    return events


def live():
    r = httpx.get(
        BASE + "/api/admin/config",
        headers={"Cookie": "auth=" + AUTH},
        timeout=30,
        trust_env=False,
    )
    if r.status_code != 200:
        raise SystemExit(f"GET /api/admin/config -> {r.status_code}: {r.text[:80]}")
    cfg = r.json()["Config"]
    return cfg, cfg["SourceConfig"]


AUTH = auth_cookie()
start_probe_server()
TIMEOUT_REASON = str(FetchTimeout())  # upstream's timeout string, taken from the code

# 1. the wire surface: POST-only, auth-gated, SSE-framed
r = httpx.get(BASE + "/api/admin/source/test", headers={"Cookie": "auth=" + AUTH}, timeout=30, trust_env=False)
check("GET /api/admin/source/test -> 404 (POST only)", r.status_code == 404, str(r.status_code))
r = httpx.post(BASE + "/api/admin/source/test", json={}, timeout=90, trust_env=False)
check("no cookie -> 401", r.status_code == 401, str(r.status_code))

cfg, srcs = live()
baseline = [s["key"] for s in srcs]
print(f"baseline: {len(baseline)} sources")

r = post("/api/admin/source/test", {})
check("content-type is text/event-stream (no charset)", r.headers.get("content-type") == "text/event-stream", repr(r.headers.get("content-type")))
events = sse(r)
check("every chunk is a `data: {...}` event", events is not None, r.text[:80])
if events:
    types = [e.get("type") for e in events]
    check("first event is start", types[:1] == ["start"], str(types[:3]))
    check("start.total == source count", events[0].get("total") == len(baseline), f"{events[0].get('total')} vs {len(baseline)}")
    check("last event is complete", types[-1:] == ["complete"], str(types[-3:]))
    items = [e for e in events if e.get("type") in ("item_result", "item_failed")]
    check("one item event per source", len(items) == len(baseline), f"{len(items)} vs {len(baseline)}")
    check("item keys are exactly the source keys", sorted(e["key"] for e in items) == sorted(baseline), "")
    done = events[-1]
    check("okCount + failedCount == total", done.get("okCount", -1) + done.get("failedCount", -1) == done.get("total", -2), str(done))
    check("item_result carries ms/count/pagecount", all(isinstance(e.get("ms"), int) and isinstance(e.get("count"), int) and isinstance(e.get("pagecount"), int) for e in items if e["type"] == "item_result"), "")
    check("item_failed carries a non-empty reason", all(bool(e.get("reason")) for e in items if e["type"] == "item_failed"), "")

# 2. the deterministic classification, against the local server
for key, kind in zip(SEEDED, KINDS):
    r = post("/api/admin/source", {"action": "add", "key": key, "name": key, "api": api_for(kind)})
    check(f"seed {key}", r.status_code == 200 and r.json() == {"ok": True}, f"{r.status_code} {r.text[:60]}")

time.sleep(TTL_WAIT)
cfg, srcs = live()
check("seeded sources visible", all(any(s["key"] == k for s in srcs) for k in SEEDED), str([s["key"] for s in srcs]))

r = post("/api/admin/source/test", {"keys": list(SEEDED), "timeout": 1})
events = sse(r)
check("keys filter -> total 4", events is not None and events[0].get("total") == len(SEEDED), str(events[0] if events else None))
if events:
    got = {e["key"]: e for e in events if e.get("type") in ("item_result", "item_failed")}
    ok = got.get("zzok")
    check("zzok -> item_result", ok is not None and ok["type"] == "item_result", str(ok))
    check("zzok count/pagecount read", ok is not None and ok.get("count") == 3 and ok.get("pagecount") == 7, str(ok))
    bad = got.get("zzbad")
    check("zzbad (404) -> item_failed HTTP 404", bad is not None and bad.get("reason") == "HTTP 404", str(bad))
    mal = got.get("zzmalformed")
    check("zzmalformed (200 HTML) -> FORMAT_ERROR", mal is not None and mal.get("reason") == FORMAT_ERROR, str(mal))
    slow = got.get("zzslow")
    check("zzslow (5s) with timeout=1 -> timeout string", slow is not None and slow.get("reason") == TIMEOUT_REASON, str(slow))
    check("complete counts the seeded run", events[-1].get("okCount") == 1 and events[-1].get("failedCount") == 3, str(events[-1]))

r = post("/api/admin/source/test", {"keys": ["zzok"], "timeout": 1})
events = sse(r)
check("single-key run -> total 1", events is not None and events[0].get("total") == 1, str(events[0] if events else None))
check("single-key run -> zzok ok", events is not None and events[-1].get("okCount") == 1, str(events[-1] if events else None))

# 3. restore
for key in SEEDED:
    r = post("/api/admin/source", {"action": "delete", "key": key})
    check(f"delete {key}", r.status_code == 200 and r.json() == {"ok": True}, f"{r.status_code} {r.text[:60]}")
r = post("/api/admin/source", {"action": "sort", "order": baseline})
check("restore order", r.status_code == 200 and r.json() == {"ok": True}, f"{r.status_code} {r.text[:60]}")
time.sleep(TTL_WAIT)
cfg, srcs = live()
check("state restored", [s["key"] for s in srcs] == baseline, f"{len(srcs)} sources")
check("no source left disabled", not any(s.get("disabled") for s in srcs), str([s.get("disabled") for s in srcs]))

probe.shutdown()
failed = [r for r in results if not r[1]]
print(f"\n{len(results) - len(failed)}/{len(results)} checks green")
for label, ok, detail in results:
    print(("  ok   " if ok else "  FAIL ") + label + (f" — {detail}" if detail and not ok else ""))
sys.exit(1 if failed else 0)
