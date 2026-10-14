"""Auth layer — `src/lib/auth.ts` + the HMAC in `src/app/api/login` + `src/middleware.ts`.

The `auth` cookie stays exactly as MoonTV wrote it: URL-encoded JSON
`{role, username?, signature?, timestamp?, password?}` with `httpOnly: false`,
because `db.client.ts` reads it back out of `document.cookie` on the client.
Moving it server-side would break the client libs, and path-compatibility is
the point of this refactor, so the format is preserved rather than improved.

`middleware.ts` ports to a FastAPI middleware rather than to client-side 401
handling: FastAPI knows both the request path and the `auth` cookie, so the
307 → `/login?redirect=<path>` gate and the matcher exclusion list translate
directly, and the browser-visible behavior (unauthenticated navigation lands
on the login page and returns to the original URL after logging in) is
unchanged.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.parse

from .env import PASSWORD, USERNAME, STORAGE_TYPE, env, env_raw
from .utils import url_search_value

COOKIE_NAME = "auth"
COOKIE_MAX_AGE = 7 * 86400  # `expires.setDate(getDate() + 7)`

# ---------------------------------------------------------------------------
# cookie parsing (`getAuthInfoFromCookie`)
# ---------------------------------------------------------------------------


def get_auth_info_from_cookie(request) -> dict | None:
    raw = request.cookies.get(COOKIE_NAME)
    if not raw:
        return None
    try:
        decoded = urllib.parse.unquote(raw)
        parsed = json.loads(decoded)
        return parsed if isinstance(parsed, dict) else None
    except Exception:  # noqa: BLE001 — the TS version returns null on any throw
        return None


# ---------------------------------------------------------------------------
# HMAC signatures (`generateSignature` / `verifySignature`)
# HMAC-SHA256 keyed by env PASSWORD over the username.
# ---------------------------------------------------------------------------


def generate_signature(data: str, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), data.encode("utf-8"), hashlib.sha256).digest()
    return digest.hex()


def verify_signature(data: str, signature: str, secret: str) -> bool:
    expected = generate_signature(data, secret)
    return hmac.compare_digest(expected, signature or "")


# ---------------------------------------------------------------------------
# cookie generation (`generateAuthCookie` in the login/register routes)
# ---------------------------------------------------------------------------


def generate_auth_cookie(
    username: str | None = None,
    password: str | None = None,
    role: str | None = None,
    include_password: bool = False,
) -> str:
    auth_data: dict = {"role": role or "user"}

    if include_password and password:
        auth_data["password"] = password

    env_password = env_raw(PASSWORD)
    if username and env_password:
        auth_data["username"] = username
        auth_data["signature"] = generate_signature(username, env_password)
        auth_data["timestamp"] = int(time.time() * 1000)

    payload = json.dumps(auth_data, ensure_ascii=False, separators=(",", ":"))
    return urllib.parse.quote(payload, safe="")


def set_auth_cookie(response, username=None, password=None, role=None, include_password=False):
    """The `response.cookies.set('auth', ...)` block shared by login/register."""
    response.set_cookie(
        COOKIE_NAME,
        generate_auth_cookie(username, password, role, include_password),
        max_age=COOKIE_MAX_AGE,
        expires=int(time.time()) + COOKIE_MAX_AGE,
        path="/",
        httponly=False,  # the client libs read document.cookie
        secure=False,
        samesite="lax",
    )
    return response


def clear_auth_cookie(response):
    """`response.cookies.set('auth', '', { expires: new Date(0) })`."""
    response.set_cookie(
        COOKIE_NAME,
        "",
        max_age=0,
        expires=0,
        path="/",
        httponly=False,
        secure=False,
        samesite="lax",
    )
    return response


# ---------------------------------------------------------------------------
# the gate (`middleware.ts`)
# ---------------------------------------------------------------------------

SKIP_PATHS = (
    "/_next",
    # Vite's build output replaces Next's `/_next`; the app bundle has to load
    # before anyone is authenticated because the login page is rendered by the
    # app itself.
    "/assets",
    "/favicon.ico",
    "/robots.txt",
    "/manifest.json",
    "/icons/",
    "/logo.png",
    "/logo.svg",
    "/screenshot.png",
    "/api/m3u8",
    "/api/image-proxy",
)

# The `matcher` negative lookahead: paths whose remainder after the leading
# slash starts with one of these is NOT gated. Ported as a prefix test, which
# is what the lookahead does.
MATCHER_EXCLUDED = (
    "_next/static",
    "_next/image",
    "favicon.ico",
    "login",
    "warning",
    "api/login",
    "api/register",
    "api/logout",
    "api/cron",
    "api/server-config",
    "api/tvbox/config",
    "api/tvbox/categories",
    "api/douban/recommends",
    "api/admin/tvbox",
    "api/m3u8",
    "api/image-proxy",
)


def should_skip_auth(pathname: str) -> bool:
    return any(pathname.startswith(p) for p in SKIP_PATHS)


def matcher_excludes(pathname: str) -> bool:
    remainder = pathname[1:] if pathname.startswith("/") else pathname
    return any(remainder.startswith(p) for p in MATCHER_EXCLUDED)


def auth_gate_response(request, pathname: str):
    """Returns a Response to short-circuit with, or `None` to let the request through."""
    if should_skip_auth(pathname) or matcher_excludes(pathname):
        return None

    if not env_raw(PASSWORD):
        # 没有设置密码，重定向到警告页面
        return _redirect(request, "/warning")

    auth_info = get_auth_info_from_cookie(request)
    if not auth_info:
        return _auth_failure(request, pathname)

    storage_type = env(STORAGE_TYPE, "sqlite")
    if storage_type == "localstorage":  # not reachable in MoonTVPlus; kept for parity
        if not auth_info.get("password") or auth_info["password"] != env_raw(PASSWORD):
            return _auth_failure(request, pathname)
        return None

    if not auth_info.get("username") or not auth_info.get("signature"):
        return _auth_failure(request, pathname)

    if auth_info.get("signature") and verify_signature(
        auth_info["username"], auth_info["signature"], env(PASSWORD, "")
    ):
        return None

    return _auth_failure(request, pathname)


def _redirect(request, path: str):
    from fastapi import Response

    location = _origin(request) + path
    return Response(status_code=307, headers={"Location": location})


def _origin(request) -> str:
    """Origin of `request.url` — `new URL(x, request.url)` keeps scheme+host."""
    from urllib.parse import urlsplit

    base = urlsplit(str(request.url))
    return f"{base.scheme}://{base.netloc}"


def _search_param_value(value: str) -> str:
    """`URLSearchParams` serialization of one value.

    Lives in `utils.py` now (`url_search_value`): the douban and tvbox routes
    build upstream URLs with the same serializer, so there is one table of
    which characters survive.
    """
    return url_search_value(value)


def _auth_failure(request, pathname: str):
    """`handleAuthFailure` — 401 for /api, else 307 to /login?redirect=<full url>.

    The `redirect` value is serialized the way `URLSearchParams` does it
    (`/`, `=`, `?` percent-encoded, space as `+`), so the login page's own
    `new URLSearchParams(location.search).get('redirect')` sees the same string
    Next.js put there.
    """
    from fastapi import Response

    if pathname.startswith("/api"):
        return Response(content=b"Unauthorized", status_code=401)

    # `URL.query` is the part after `?`, without the `?` itself; `request.query_string`
    # is not available on the `_CachedRequest` a base middleware hands over.
    query = request.url.query
    full_url = pathname + (f"?{query}" if query else "")
    location = _origin(request) + "/login?redirect=" + _search_param_value(full_url)
    return Response(status_code=307, headers={"Location": location})


def is_owner(username: str | None) -> bool:
    """`authInfo.username === process.env.USERNAME`."""
    owner = env_raw(USERNAME)
    return bool(owner) and username == owner


def is_admin(username: str | None, users: list[dict]) -> bool:
    """`role === 'admin' && !banned` among `config.UserConfig.Users`."""
    if not username:
        return False
    return any(
        u.get("username") == username and u.get("role") == "admin" and not u.get("banned") for u in users
    )
