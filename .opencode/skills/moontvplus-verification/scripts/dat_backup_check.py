"""Check the .dat backup end to end: the format, and a live export -> import round trip.

Usage: backend/.venv/bin/python <skill>/scripts/dat_backup_check.py

Two halves, because B7 is a pair: `routes/data_migration.py` writes and reads the
`.dat`, and the panel's 数据迁移 tab (`components/DataMigration.tsx`) is the
browser half that downloads it and posts it back. The format is three layers —
raw DEFLATE, base64, CryptoJS AES with an OpenSSL `Salted__` header — so parity
is checked against the oracles that define it rather than against a re-typed
copy: `crypto-js@4.2.0` (the library upstream's `crypto.ts` used), `openssl enc
-aes-256-cbc -md md5` (whose `EVP_BytesToKey` path is what CryptoJS implements),
and node's `zlib`, which is pako's raw DEFLATE at level 6.

Then the round trip runs against the live server: seed a user and every kind of
record through the panel's own routes, export, decrypt the file locally, import
it back, and assert the deployment is unchanged. That last step is the only way
to see what the import does first — `Db.clear_all_data()` deletes all eight
tables, so a silent loss there looks like a green export.

It mutates the deployment and restores it: the seeded user is ASCII (`zzbackup`),
its records are deleted through `/api/admin/user` `deleteUser` at the end, and the
original `ConfigFile` string and source order are replayed, so it is safe to run
against a live site. Every expected error string is imported from the code
(`crypto.DECRYPT_FAILED`), never typed here.
"""

import base64
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import time

import httpx

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from _harness import BASE, ROOT, auth_cookie

from moontv.crypto import DECRYPT_FAILED, decode_backup, decrypt, encode_backup, encrypt
from moontv.crypto import _b64_parse, pako_deflate
from moontv.version import CURRENT_VERSION

HERE = pathlib.Path(__file__).resolve().parent
NODE_FIXTURE = HERE / "dat_backup_node.cjs"
NODE_PATH = str(ROOT / "frontend/node_modules")

SEED_USER = "zzbackup"
SEED_PASSWORD = "zzseedpass123"
BACKUP_PASSWORD = "zz-backup-pw"
TTL_WAIT = 6.0  # > config.py's _CONFIG_TTL_S = 5.0

results = []


def check(label, ok, detail=""):
    results.append((label, ok, detail))


def sse(text):
    """`data: {...}\n\n` events; the trailing empty chunk after the last frame."""
    out = []
    for block in text.split("\n\n"):
        if not block:
            continue
        line = next((l for l in block.split("\n") if l.startswith("data:")), None)
        if line is None:
            continue
        try:
            out.append(json.loads(line[5:].strip()))
        except json.JSONDecodeError:
            out.append({"_unparsable": line})
    return out


def jpost(path, body, cookie, timeout=60):
    r = httpx.post(BASE + path, headers={"Cookie": "auth=" + cookie}, json=body,
                   timeout=timeout, trust_env=False)
    return r.status_code, (r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text)


def jget(path, cookie, timeout=60):
    r = httpx.get(BASE + path, headers={"Cookie": "auth=" + cookie}, timeout=timeout, trust_env=False)
    return r.status_code, r.text


def live_config(cookie):
    code, body = jget("/api/admin/config", cookie)
    if code != 200:
        raise SystemExit(f"GET /api/admin/config -> {code}: {body[:120]}")
    cfg = json.loads(body)["Config"]
    return (
        [s["key"] for s in cfg.get("SourceConfig") or []],
        [c.get("query") for c in cfg.get("CustomCategories") or []],
        [(u.get("username"), u.get("role")) for u in (cfg.get("UserConfig") or {}).get("Users") or []],
        cfg.get("ConfigFile"),
    )


def run_node(spec):
    """One call into the crypto-js/zlib oracle. ASCII in, ASCII out."""
    with tempfile.TemporaryDirectory(prefix="zzdat-") as d:
        spec_path, out_path = f"{d}/spec.json", f"{d}/out.json"
        with open(spec_path, "w") as f:
            f.write(json.dumps(spec, ensure_ascii=True))
        subprocess.run(
            ["node", str(NODE_FIXTURE), spec_path, out_path],
            env={**os.environ, "NODE_PATH": NODE_PATH},
            check=True,
        )
        return json.loads(pathlib.Path(out_path).read_text(encoding="utf-8"))


def openssl(args, inp_bytes):
    """`openssl enc [-d] -aes-256-cbc -md md5`. Returns (bytes, error)."""
    with tempfile.TemporaryDirectory(prefix="zzossl-") as d:
        inp, outp = f"{d}/in", f"{d}/out"
        with open(inp, "wb") as f:
            f.write(inp_bytes)
        r = subprocess.run(args + ["-in", inp, "-out", outp], capture_output=True, text=True)
        if r.returncode != 0:
            return None, (r.stdout + r.stderr).strip()
        with open(outp, "rb") as f:
            return f.read(), None


AUTH = auth_cookie()

# ---------------------------------------------------------------------------
# 1. the format, against crypto-js@4.2.0 and node's zlib
# ---------------------------------------------------------------------------

cjk = "".join(chr(0x4E00 + i) for i in range(8))
plains = ["", "a", "hello world", cjk, cjk + " \u2014 " + "\u00e9" * 3, "\U0001F9AA emoji"]
passes = ["pw", "hunter2 hunter2", cjk[:4]]
pairs = [(p, passes[i % len(passes)]) for i, p in enumerate(plains)]
py_ct = [encrypt(p, pw) for p, pw in pairs]

got = run_node({
    "encrypt": [{"plain": p, "pass": pw} for p, pw in pairs],
    "decrypt": [{"cipher": ct, "pass": pw} for (p, pw), ct in zip(pairs, py_ct)],
    "deflate": plains,
    "inflate": [base64.b64encode(pako_deflate(p.encode("utf-8"))).decode("ascii") for p in plains],
    "b64parse": ["QUx5", "QU==x5", "QU!x5", "QUx", ""],
})

for (p, pw), r in zip(pairs, got["decrypt"]):
    check(f"js decrypts python's AES ({p[:12]!r})", r.get("ok") and r.get("plain") == p, str(r)[:120])
for (p, pw), ct in zip(pairs, got["encrypt"]):
    try:
        ok = decrypt(ct, pw) == p
        detail = ""
    except Exception as e:
        ok, detail = False, f"{type(e).__name__}: {e}"
    check(f"python decrypts crypto-js's AES ({p[:12]!r})", ok, detail)

for p, theirs in zip(plains, got["deflate"]):
    mine = base64.b64encode(pako_deflate(p.encode("utf-8"))).decode("ascii")
    check(f"pako_deflate == node zlib ({p[:12]!r})", mine == theirs, f"{len(mine)} vs {len(theirs)}")
for p, theirs in zip(plains, got["inflate"]):
    check(f"node inflates python's DEFLATE ({p[:12]!r})", theirs == p, str(theirs)[:80])

for s, theirs in zip(["QUx5", "QU==x5", "QU!x5", "QUx", ""], got["b64parse"]):
    check(f"_b64_parse == CryptoJS parse ({s!r})", _b64_parse(s).hex() == theirs, f"{_b64_parse(s).hex()} vs {theirs}")

check("CURRENT_VERSION is the backup's serverVersion", isinstance(CURRENT_VERSION, str) and bool(CURRENT_VERSION), repr(CURRENT_VERSION))

# ---------------------------------------------------------------------------
# 2. the format, against openssl's EVP_BytesToKey path
# ---------------------------------------------------------------------------

for p, pw in pairs:
    if not p.isascii() or not pw.isascii():
        continue  # openssl takes the passphrase on argv; the CJK cases are crypto-js's
    blob, err = openssl(["openssl", "enc", "-aes-256-cbc", "-md", "md5", "-pass", "pass:" + pw], p.encode("utf-8"))
    if blob is None:
        check("openssl enc ran", False, err)
        continue
    check("openssl writes the Salted__ header", blob[:8] == b"Salted__", repr(blob[:8]))
    ct_b64 = base64.b64encode(blob).decode("ascii")
    try:
        ok = decrypt(ct_b64, pw) == p
        detail = ""
    except Exception as e:
        ok, detail = False, f"{type(e).__name__}: {e}"
    check(f"python decrypts openssl's ciphertext ({p[:12]!r})", ok, detail)
    back, err = openssl(["openssl", "enc", "-d", "-aes-256-cbc", "-md", "md5", "-pass", "pass:" + pw],
                        base64.b64decode(py_ct[pairs.index((p, pw))]))
    check(f"openssl decrypts python's ciphertext ({p[:12]!r})", back is not None and back.decode("utf-8") == p, str(err))

payload = json.dumps({"data": {"adminConfig": {"SourceConfig": []}, "userData": {}}}, ensure_ascii=True)
deflated = base64.b64encode(pako_deflate(payload.encode("utf-8"))).decode("ascii")
blob, err = openssl(["openssl", "enc", "-aes-256-cbc", "-md", "md5", "-pass", "pass:pw"], deflated.encode("ascii"))
check("python reads openssl's whole .dat (inflate <- base64 <- AES)", blob is not None and decode_backup(base64.b64encode(blob).decode("ascii"), "pw") == payload, str(err))
back, err = openssl(["openssl", "enc", "-d", "-aes-256-cbc", "-md", "md5", "-pass", "pass:pw"],
                    base64.b64decode(encode_backup(payload, "pw")))
check("openssl decrypts python's whole .dat layer", back is not None and back.decode("ascii") == deflated, str(err))

# ---------------------------------------------------------------------------
# 3. the wire's error branches (all of them fail before the destructive clear)
# ---------------------------------------------------------------------------

code, body = jpost("/api/admin/data_migration/export", {}, AUTH)
check("export without a password -> 400", code == 400 and isinstance(body, dict) and "error" in body, f"{code} {body}")

code, body = jpost("/api/admin/data_migration/import", {}, AUTH)
check("import with no file -> 400", code == 400 and isinstance(body, dict) and "error" in body, f"{code} {body}")

r = httpx.post(BASE + "/api/admin/data_migration/export", headers={"Cookie": "auth=zzforged"},
               json={"password": BACKUP_PASSWORD}, timeout=30, trust_env=False, follow_redirects=False)
check("forged cookie -> the gate's 401, not the route's", r.status_code == 401, f"{r.status_code} {r.text[:80]}")

# ---------------------------------------------------------------------------
# 4. the live round trip
# ---------------------------------------------------------------------------

before = live_config(AUTH)
print(f"baseline: {len(before[0])} sources, {len(before[1])} categories, {len(before[2])} users")

code, body = jpost("/api/admin/user", {"action": "add", "targetUsername": SEED_USER, "targetPassword": SEED_PASSWORD}, AUTH)
check(f"seed user {SEED_USER} -> ok", code == 200 and body == {"ok": True}, f"{code} {body}")

r = httpx.post(BASE + "/api/login", json={"username": SEED_USER, "password": SEED_PASSWORD}, timeout=30, trust_env=False)
check(f"login as {SEED_USER}", r.status_code == 200, f"{r.status_code} {r.text[:80]}")
seed_cookie = r.headers.get("set-cookie", "").removeprefix("auth=").split(";")[0]
check("login sets an auth cookie", r.headers.get("set-cookie", "").startswith("auth="), seed_cookie[:20])

for path in ("/api/admin/data_migration/export", "/api/admin/data_migration/import"):
    r = httpx.post(BASE + path, headers={"Cookie": "auth=" + seed_cookie},
                   json={"password": BACKUP_PASSWORD}, timeout=30, trust_env=False)
    check(f"{path.split('/')[-1]} by a non-owner -> 401", r.status_code == 401, f"{r.status_code} {r.text[:80]}")

now_ms = int(time.time() * 1000)
title = "ZZTitle"
seeded = {
    "playrecords": {"key": "zzsrc+zzid1", "record": {"title": title, "source_name": "zzsrc", "image": "", "episode": "1/2", "watching": "1/2", "index": 1, "save_time": now_ms}},
    "favorites": {"key": "zzsrc+zzid2", "favorite": {"title": title, "source_name": "zzsrc", "image": "", "save_time": now_ms}},
    "followings": {"key": "zzsrc+zzid3", "following": {"title": title, "source_name": "zzsrc", "episode": "1/2", "totalEpisode": "2/2", "totalEpisodes": ["2/2"], "poster": "", "year": "", "save_time": now_ms}},
    "skipconfigs": {"key": "zzsrc+zzid4", "config": {"enable": True, "intro_time": 12, "outro_time": 34}},
}
for path, body in seeded.items():
    code, got_body = jpost("/api/" + path, body, seed_cookie)
    check(f"seed {path} -> success", code == 200 and got_body == {"success": True}, f"{code} {got_body}")

code, body = jpost("/api/searchhistory", {"keyword": "zzkeyword"}, seed_cookie)
check("seed searchhistory -> list", code == 200 and isinstance(body, list) and "zzkeyword" in body, f"{code} {body}")

today = time.strftime("%Y-%m-%d")
code, body = jpost("/api/today-updated", {"date": today, "items": [{"source": "zzsrc", "id": "zzid5", "title": title, "poster": "", "episodes": 2, "watchedEpisodes": 1, "unwatchedEpisodes": 1, "source_name": "zzsrc", "year": "", "save_time": now_ms, "oldEpisodes": 1, "newEpisodes": 1}]}, seed_cookie)
check("seed today-updated -> success", code == 200 and isinstance(body, dict) and body.get("success") is True, f"{code} {body}")

snapshot = {}
for path in ("playrecords", "favorites", "followings", "skipconfigs", "searchhistory", "today-updated"):
    code, body = jget("/api/" + path, seed_cookie)
    snapshot[path] = json.loads(body) if code == 200 else None
    check(f"GET {path} after seeding", code == 200, f"{code} {str(body)[:80]}")

# export
r = httpx.post(BASE + "/api/admin/data_migration/export", headers={"Cookie": "auth=" + AUTH},
               json={"password": BACKUP_PASSWORD}, timeout=180, trust_env=False)
check("export -> 200 text/event-stream", r.status_code == 200 and r.headers.get("content-type") == "text/event-stream; charset=utf-8", f"{r.status_code} {r.headers.get('content-type')}")
events = sse(r.text)
types = [e.get("type") for e in events]
check("export event vocabulary", {"stage", "user", "detail", "file_start", "chunk", "file_end", "done"} <= set(types), str(sorted(set(types))))
check("no export error event", "error" not in types, str(types))
filename = next((e.get("filename") for e in events if e.get("type") == "file_start"), None)
check("filename is a .dat name", bool(re.fullmatch(r"moontv-backup-\d{8}-\d{6}\.dat", filename or "")), repr(filename))
done = next((e for e in events if e.get("type") == "done"), {})
check("done carries exportedUsers + filename + percent", isinstance(done.get("exportedUsers"), int) and done.get("exportedUsers") >= 2 and done.get("filename") == filename and done.get("percent") == 100, str(done))

chunks = "".join(e.get("data", "") for e in events if e.get("type") == "chunk")
check("chunks re-encode to a whole base64 blob", len(chunks) % 4 == 0 and base64.b64encode(base64.b64decode(chunks)).decode("ascii") == chunks, f"{len(chunks)} chars")
workdir = pathlib.Path(tempfile.mkdtemp(prefix="zzdat-"))
dat_path = workdir / (filename or "moontv-backup.dat")
dat_path.write_text(chunks, encoding="ascii")
check(f"the saved .dat is {len(chunks)} chars", dat_path.stat().st_size == len(chunks), str(dat_path.stat().st_size))

try:
    plain = decode_backup(dat_path.read_text(encoding="ascii"), BACKUP_PASSWORD)
    ok = True
except Exception as e:
    plain, ok = "", f"{type(e).__name__}: {e}"
check("the saved file decrypts with the panel password", ok, plain if not ok else "")

backup = json.loads(plain) if ok else {}
check("backup has a timestamp", isinstance(backup.get("timestamp"), str), str(backup.get("timestamp")))
check("backup's serverVersion is the shipped version", backup.get("serverVersion") == CURRENT_VERSION, repr(backup.get("serverVersion")))
check("backup has adminConfig + userData", isinstance(backup.get("data", {}).get("adminConfig"), dict) and isinstance(backup.get("data", {}).get("userData"), dict), str(sorted(backup.get("data", {}))))
users_in_backup = backup.get("data", {}).get("userData", {})
check("backup covers every user", set(users_in_backup) >= {u for u, _ in before[2]} | {SEED_USER}, str(sorted(users_in_backup)))
check("backup carries the seeded records", all(users_in_backup.get(SEED_USER, {}).get(k) for k in ("playRecords", "favorites", "followings", "searchHistory", "skipConfigs", "todayUpdated")), str({k: bool(v) for k, v in users_in_backup.get(SEED_USER, {}).items()}))
check("backup's adminConfig keeps the source order", [s["key"] for s in backup["data"]["adminConfig"].get("SourceConfig") or []] == before[0], str([s["key"] for s in backup["data"]["adminConfig"].get("SourceConfig") or []]))

# wrong password, against the real file
r = httpx.post(BASE + "/api/admin/data_migration/import", headers={"Cookie": "auth=" + AUTH},
               files={"file": (filename or "moontv-backup.dat", chunks.encode("ascii"), "application/octet-stream")},
               data={"password": "zzwrongpassword"}, timeout=60, trust_env=False)
check("wrong password -> 400", r.status_code == 400, f"{r.status_code} {r.text[:80]}")
try:
    check("wrong password -> crypto.DECRYPT_FAILED", json.loads(r.text).get("error") == DECRYPT_FAILED, json.loads(r.text).get("error"))
except Exception:
    check("wrong password -> crypto.DECRYPT_FAILED", False, r.text[:80])

# malformed file
r = httpx.post(BASE + "/api/admin/data_migration/import", headers={"Cookie": "auth=" + AUTH},
               files={"file": ("zzbad.dat", b"not a backup at all", "application/octet-stream")},
               data={"password": BACKUP_PASSWORD}, timeout=60, trust_env=False)
check("malformed file -> 400", r.status_code == 400, f"{r.status_code} {r.text[:80]}")

# import the real file
r = httpx.post(BASE + "/api/admin/data_migration/import", headers={"Cookie": "auth=" + AUTH},
               files={"file": (filename or "moontv-backup.dat", chunks.encode("ascii"), "application/octet-stream")},
               data={"password": BACKUP_PASSWORD}, timeout=180, trust_env=False)
check("import -> 200 text/event-stream", r.status_code == 200 and r.headers.get("content-type") == "text/event-stream; charset=utf-8", f"{r.status_code} {r.headers.get('content-type')}")
events = sse(r.text)
types = [e.get("type") for e in events]
check("no import error event", "error" not in types, str(types))
check("import event vocabulary", {"stage", "user", "detail", "progress", "done"} <= set(types), str(sorted(set(types))))
done = next((e for e in events if e.get("type") == "done"), {})
check("done.importedUsers == users in the backup", done.get("importedUsers") == len(users_in_backup), f"{done.get('importedUsers')} vs {len(users_in_backup)}")
check("done.timestamp round-trips", done.get("timestamp") == backup.get("timestamp"), f"{done.get('timestamp')} vs {backup.get('timestamp')}")
check("done.serverVersion round-trips", done.get("serverVersion") == backup.get("serverVersion"), f"{done.get('serverVersion')} vs {backup.get('serverVersion')}")
check("percent reaches 100", done.get("percent") == 100, str(done.get("percent")))

time.sleep(TTL_WAIT)
after = live_config(AUTH)
check("source order survives the round trip", after[0] == before[0], f"{after[0]} vs {before[0]}")
check("categories survive the round trip", after[1] == before[1], f"{after[1]} vs {before[1]}")
check("users survive the round trip", sorted(after[2]) == sorted(before[2] + [(SEED_USER, "user")]), f"{sorted(after[2])}")
check("ConfigFile survives the round trip", after[3] == before[3], f"{str(after[3])[:80]} vs {str(before[3])[:80]}")

r = httpx.post(BASE + "/api/login", json={"username": SEED_USER, "password": SEED_PASSWORD}, timeout=30, trust_env=False)
check(f"{SEED_USER}'s password survived (login works)", r.status_code == 200, f"{r.status_code} {r.text[:80]}")
seed_cookie = r.headers.get("set-cookie", "").removeprefix("auth=").split(";")[0]
for path in ("playrecords", "favorites", "followings", "skipconfigs", "searchhistory", "today-updated"):
    code, body = jget("/api/" + path, seed_cookie)
    same = code == 200 and json.loads(body) == snapshot[path]
    check(f"{path} survives the round trip", same, f"{code} {str(body)[:120]} vs {str(snapshot[path])[:120]}")

# ---------------------------------------------------------------------------
# 5. restore what was seeded
# ---------------------------------------------------------------------------

code, body = jpost("/api/admin/user", {"action": "deleteUser", "targetUsername": SEED_USER}, AUTH)
check(f"delete {SEED_USER} -> ok", code == 200 and body == {"ok": True}, f"{code} {body}")
code, body = jpost("/api/admin/config_file", {"configFile": before[3]}, AUTH)
check("restore the original ConfigFile string", code == 200 and isinstance(body, dict) and body.get("success") is True, f"{code} {body}")
code, body = jpost("/api/admin/source", {"action": "sort", "order": before[0]}, AUTH)
check("restore the original source order", code == 200 and body == {"ok": True}, f"{code} {body}")
time.sleep(TTL_WAIT)
restored = live_config(AUTH)
check("state restored", restored == before, f"{restored} vs {before}")

for d in (workdir,):
    for f in d.rglob("*"):
        if f.is_file():
            f.unlink()
    d.rmdir()

failed = [x for x in results if not x[1]]
print(f"\n{len(results) - len(failed)}/{len(results)} checks green")
for label, ok, detail in results:
    print(("  ok   " if ok else "  FAIL ") + label + (f" — {detail}" if detail and not ok else ""))
sys.exit(1 if failed else 0)
