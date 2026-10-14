"""Edge-case sweep over the routes that have no committed offline tests.

Run it against the running server:

    backend/.venv/bin/python .opencode/skills/moontvplus-verification/scripts/edge_sweep.py

Read-only: every case is a GET, so the sweep cannot reorder, re-enable or
resurrect anything. The source key, api url, id and title are **derived** from
the app's own responses and sent percent-encoded, because this machine's I/O
path transcodes CJK typed into a command: a `source=暴风` typed into the string
arrives as two other codepoints and the server answers `无效的API来源` while an
ASCII key passes, so a hand-typed sweep reports failures for routes that are
fine. The credentials come from ./.env (or the shell the server was started
from) through `_harness.load_env`, never hard-coded here.

Exit code is non-zero when any case answers 500, or when a non-API path answers
404 (the catch-all serves the app document at 200, so a 404 there means the
catch-all is not last in main.py). A live source that is down answers its own
reason string (`请求失败` / non-JSON HTML); that is the network, not a port bug,
so the sweep reports shapes and only fails on 500/404.
"""

import json
import pathlib
import sys
import urllib.parse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _harness import BASE, auth_cookie  # noqa: E402

import httpx  # noqa: E402

client = httpx.Client(trust_env=False, timeout=25)
client.headers["Cookie"] = "auth=" + auth_cookie()

# --- derive the source key / api / a real id from the app's own data ---
cfg = client.get(BASE + "/api/admin/config").json()
inner = json.loads(cfg["Config"]) if isinstance(cfg.get("Config"), str) else cfg["Config"]
config_file = inner["ConfigFile"]
if isinstance(config_file, str):
    config_file = json.loads(config_file)
api_site = config_file["api_site"]

key = list(api_site.keys())[0]
api_url = api_site[key]["api"]

real_id, real_title = "1", ""
try:
    r = client.get(api_url, params={"ac": "videolist", "limit": 1}, timeout=20)
    rows = r.json().get("list") or []
    if rows:
        real_id = str(rows[0]["vod_id"])
        real_title = rows[0].get("vod_name", "")
except Exception:
    pass

print("derived source key:", repr(key), "id:", real_id)

CASES = [
    ("/api/detail", {"id": real_id, "source": key}),
    ("/api/detail", {"id": "", "source": key}),
    ("/api/detail", {"id": real_id, "source": ""}),
    ("/api/detail", {"id": "a!b", "source": key}),
    ("/api/detail", {"id": real_id, "source": "nope"}),
    ("/api/detail", {"source": key}),
    ("/api/detail", {"id": real_id}),
    ("/api/detail", {"id": "中文id", "source": key}),
    ("/api/search/resources", {}),
    ("/api/search/suggestions", {"q": ""}),
    ("/api/search/suggestions", {"q": "a"}),
    ("/api/search/suggestions", {"q": "x" * 400}),
    ("/api/search/suggestions", {}),
    ("/api/search/one", {"q": real_title, "resourceId": key}),
    ("/api/search/one", {"q": real_title}),
    ("/api/search/one", {"resourceId": key}),
    ("/api/search/one", {"q": real_title, "resourceId": "nope"}),
    ("/api/tvbox/config", {"un": ""}),
    ("/api/tvbox/config", {"un": "!!!"}),
    ("/api/tvbox/config", {"un": "a"}),
    ("/api/tvbox/config", {"un": "MTkyLjE2OC4xLjE6ODAK"}),  # base64 of a private host
    ("/api/tvbox/config", {}),
    ("/api/tvbox/categories", {}),
    ("/api/user/online", {}),
    ("/api/playrecords", {}),
    ("/api/favorites", {}),
    ("/api/followings", {}),
    ("/api/searchhistory", {}),
    ("/api/skipconfigs", {}),
    ("/api/today-updated", {}),
    ("/api/server-config", {}),
    ("/api/config/sources", {}),
    ("/api/config/custom_category", {}),
]

suspects = []
for path, params in CASES:
    try:
        r = client.get(BASE + path, params=params, follow_redirects=False)
    except Exception as e:
        suspects.append((path, params, "EXCEPTION", repr(e)[:100]))
        continue
    if r.status_code >= 500:
        suspects.append((path, params, r.status_code, r.text[:140]))
    elif r.status_code == 404 and not path.startswith("/api/"):
        suspects.append((path, params, r.status_code, "catch-all should serve the document"))

print("500/404 suspects:", len(suspects))
for s in suspects:
    print(s)

# what each route answers for a well-formed request: shape census
print("--- well-formed shapes ---")
for path, params in [
    ("/api/detail", {"id": real_id, "source": key}),
    ("/api/search/resources", {}),
    ("/api/search/suggestions", {"q": real_title[:6] if real_title else "a"}),
    ("/api/search/one", {"q": real_title, "resourceId": key}),
    ("/api/tvbox/config", {"un": ""}),
    ("/api/tvbox/categories", {}),
    ("/api/user/online", {}),
    ("/api/playrecords", {}),
    ("/api/today-updated", {}),
    ("/api/server-config", {}),
]:
    r = client.get(BASE + path, params=params, follow_redirects=False)
    body = r.text[:110].replace("\n", " ")
    print("%-26s %s %s" % (path, r.status_code, body))

sys.exit(1 if suspects else 0)
