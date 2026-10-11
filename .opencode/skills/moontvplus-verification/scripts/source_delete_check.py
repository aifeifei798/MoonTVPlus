"""Check that the admin panel's 删除/批量删除 really removes sources.

Usage: backend/.venv/bin/python <skill>/scripts/source_delete_check.py

The trap this harness exists for: `getConfig` re-merges the admin blob's
`ConfigFile.api_site` map into `SourceConfig` on every read, so deleting a
`from == "config"` source from the list alone is a no-op — it is rebuilt on the
next request. Upstream hid that by refusing (400 该源不可删除) for a single
delete and by answering `{"ok":true}` for a batch delete that skipped them, so
the panel looked successful while the count stayed. This harness deletes a
config-declared source and asserts it is still gone after the config cache
expires, which is the only way to see the resurrection.

It mutates the deployment and restores it: the original `ConfigFile` string and
the original source order are captured first and replayed at the end, so it is
safe to run against a live site. Keys it adds are ASCII (`zzdel1`/`zzdel2`);
every key it deletes is derived from the live response, never typed here.
"""

import json
import os
import pathlib
import sys
import time

import httpx

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from _harness import BASE, auth_cookie

COOKIE_NAME = "auth"
TTL_WAIT = 6.0  # > config.py's _CONFIG_TTL_S = 5.0

results = []


def check(label, ok, detail=""):
    results.append((label, ok, detail))


def live():
    r = httpx.get(BASE + "/api/admin/config", headers={"Cookie": "auth=" + AUTH}, timeout=30, trust_env=False)
    if r.status_code != 200:
        raise SystemExit(f"GET /api/admin/config -> {r.status_code}: {r.text[:80]}")
    cfg = r.json()["Config"]
    return cfg, cfg["SourceConfig"], cfg.get("CustomCategories") or []


def post(path, body):
    hdr = {"Cookie": "auth=" + AUTH, "Content-Type": "application/json"}
    r = httpx.post(BASE + path, headers=hdr, json=body, timeout=30, trust_env=False)
    return r.status_code, r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text


AUTH = auth_cookie()

cfg, srcs, cats = live()
baseline = len(srcs)
original_cf = cfg["ConfigFile"]
original_order = [s["key"] for s in srcs]
config_keys = [s["key"] for s in srcs if s.get("from") == "config"]
print(f"baseline: {baseline} sources ({len(config_keys)} from config), {len(cats)} categories")

# 1. custom sources: add, single delete, batch delete
for key in ("zzdel1", "zzdel2"):
    code, body = post("/api/admin/source", {"action": "add", "key": key, "name": key, "api": "https://example.com/api"})
    check(f"add {key}", code == 200 and body == {"ok": True}, f"{code} {body}")
cfg, srcs, _ = live()
check("add -> count +2", len(srcs) == baseline + 2, f"{len(srcs)} vs {baseline + 2}")

code, body = post("/api/admin/source", {"action": "delete", "key": "zzdel1"})
check("delete zzdel1 -> ok", code == 200 and body == {"ok": True}, f"{code} {body}")
cfg, srcs, _ = live()
check("zzdel1 gone", "zzdel1" not in [s["key"] for s in srcs], str([s["key"] for s in srcs]))

code, body = post("/api/admin/source", {"action": "batchDelete", "keys": ["zzdel2"]})
check("batchDelete zzdel2 -> ok", code == 200 and body == {"ok": True}, f"{code} {body}")
cfg, srcs, _ = live()
check("batchDelete -> count back to baseline", len(srcs) == baseline, f"{len(srcs)} vs {baseline}")

# 2. the config-declared source: the resurrection case
victim = config_keys[0]
code, body = post("/api/admin/source", {"action": "delete", "key": victim})
check(f"delete config source {victim!r} -> ok", code == 200 and body == {"ok": True}, f"{code} {body}")
time.sleep(TTL_WAIT)
cfg, srcs, _ = live()
keys = [s["key"] for s in srcs]
check(f"{victim!r} not resurrected after {TTL_WAIT}s", victim not in keys, f"{len(keys)} sources")
check("count is baseline-1", len(srcs) == baseline - 1, f"{len(srcs)} vs {baseline - 1}")
declared = json.loads(cfg["ConfigFile"]).get("api_site") or {}
check("ConfigFile.api_site pruned too", victim not in declared, f"{len(declared)} declared vs {baseline}")

# 3. batchDelete on a config source
victim2 = next(s["key"] for s in srcs if s.get("from") == "config")
code, body = post("/api/admin/source", {"action": "batchDelete", "keys": [victim2]})
check(f"batchDelete config source {victim2!r} -> ok", code == 200 and body == {"ok": True}, f"{code} {body}")
time.sleep(TTL_WAIT)
cfg, srcs, _ = live()
keys = [s["key"] for s in srcs]
check(f"{victim2!r} not resurrected", victim2 not in keys, f"{len(keys)} sources")
check("count is baseline-2", len(srcs) == baseline - 2, f"{len(srcs)} vs {baseline - 2}")

# 4. the same resurrection for config-declared categories
cf = json.loads(original_cf)
cf["custom_category"] = [{"name": "ZZProbe", "type": "tv", "query": "zzprobe"}]
seeded = json.dumps(cf, ensure_ascii=False, separators=(",", ":"))
code, body = post("/api/admin/config_file", {"configFile": seeded})
check("upload ConfigFile with a custom_category", code == 200 and isinstance(body, dict) and body.get("success") is True, f"{code} {body}")
time.sleep(TTL_WAIT)
cfg, _, cats = live()
check("seeded category visible as from=config", any(c.get("from") == "config" for c in cats), str(cats))
code, body = post("/api/admin/category", {"action": "delete", "query": "zzprobe", "type": "tv"})
check("delete config category -> ok", code == 200 and body == {"ok": True}, f"{code} {body}")
time.sleep(TTL_WAIT)
cfg, _, cats = live()
check("category not resurrected", not any(c.get("query") == "zzprobe" for c in cats), str(cats))

# 5. restore: replay the original ConfigFile string, then the original order
code, body = post("/api/admin/config_file", {"configFile": original_cf})
check("restore original ConfigFile", code == 200 and isinstance(body, dict) and body.get("success") is True, f"{code} {body}")
code, body = post("/api/admin/source", {"action": "sort", "order": original_order})
check("restore original order", code == 200 and body == {"ok": True}, f"{code} {body}")
time.sleep(TTL_WAIT)
cfg, srcs, cats = live()
check("state restored", [s["key"] for s in srcs] == original_order and not cats, f"{len(srcs)} sources, {len(cats)} categories")
check("no source left disabled", not any(s.get("disabled") for s in srcs), str([s.get("disabled") for s in srcs]))

failed = [r for r in results if not r[1]]
print(f"\n{len(results) - len(failed)}/{len(results)} checks green")
for label, ok, detail in results:
    print(("  ok   " if ok else "  FAIL ") + label + (f" — {detail}" if detail and not ok else ""))
sys.exit(1 if failed else 0)
