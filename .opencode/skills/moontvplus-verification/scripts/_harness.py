"""Shared bootstrap for the committed Python harnesses.

Import it from a sibling harness:

    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    from _harness import ROOT, BASE, load_env, auth_cookie

It puts <repo>/backend on sys.path so `from moontv.storage.db import Db` works
when the script is run as `backend/.venv/bin/python <this-dir>/x.py` from any
cwd, and it reads credentials out of ./.env instead of carrying them here.
"""

import os
import pathlib
import sys


def _root():
    if os.environ.get("MOONTVPLUS_ROOT"):
        return pathlib.Path(os.environ["MOONTVPLUS_ROOT"])
    p = pathlib.Path(__file__).resolve()
    for _ in range(8):
        p = p.parent
        if (p / "backend/moontv").is_dir():
            return p
    raise SystemExit(
        "not inside a MoonTVPlus checkout — set MOONTVPLUS_ROOT to the repo root"
    )


ROOT = _root()
sys.path.insert(0, str(ROOT / "backend"))

# The app-facing overrides in static.py; harnesses that talk to the served UI
# must point at the same tree the running server does.
BASE = "http://" + os.environ.get("MOONTVPLUS_SERVE", "127.0.0.1:3000")


def load_env():
    """Parse ./.env the way server.sh does, so a harness sees the same values.

    Four rules fail silently in that format, so replay them exactly: no inline
    comments, no indented assignments, values are NOT trimmed, and KEY= sets an
    empty value that beats anything exported in the shell.
    """
    out = {}
    p = ROOT / ".env"
    if p.is_file():
        for line in p.read_text(encoding="utf-8").split("\n"):
            if line and not line.startswith("#") and "=" in line and not line[0].isspace():
                k, v = line.split("=", 1)
                out[k] = v
    # A deployment started with `USERNAME=... ./server.sh start` has no ./.env at
    # all; the harnesses then have to read the same shell the server got its
    # credentials from, or every authenticated probe fails with
    # `{"error":"用户名不能为空"}`.
    for name in ("USERNAME", "PASSWORD"):
        out.setdefault(name, os.environ.get(name, ""))
    return out


def auth_cookie():
    """Log in and return the raw VALUE of the auth cookie (no `auth=` prefix).

    The CDP harnesses pass this to Network.setCookie with name 'auth'; prefixing
    it again doubles the name and the gate bounces to /login?redirect=%2F.
    """
    import httpx

    env = load_env()
    r = httpx.post(
        BASE + "/api/login",
        json={"username": env.get("USERNAME", ""), "password": env.get("PASSWORD", "")},
        trust_env=False,
        timeout=20,
    )
    if r.status_code != 200:
        raise SystemExit(f"login failed: {r.status_code} {r.text[:80]} — is the server running?")
    sc = r.headers.get("set-cookie", "")
    if not sc.startswith("auth="):
        raise SystemExit("no auth cookie — PASSWORD is unset, the gate answers 307 /warning")
    return sc[len("auth=") :].split(";")[0]
