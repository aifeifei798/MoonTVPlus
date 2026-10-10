"""Route registry — `src/app/api/**/route.ts`, one module per route group.

Every MoonTV route file exports `GET`/`POST`/`DELETE` handlers under exactly
those names plus `export const runtime = 'edge'`; the App Router turns the
directory name into the URL path. Here the path is stated explicitly in each
module's `ROUTES` list, and `moontv/main.py` registers them, so the mapping is
visible instead of implied by a directory layout.

Only the explicitly named HTTP methods are registered, matching MoonTV: an
unlisted method on a MoonTV route is a 405, not a fallthrough.

The per-user opening (`cookie` → `config` → `banned?`) and the `key=source+id`
split are shared here rather than repeated per route, but they keep the
wording as an argument because the routes disagree on it: the stores say
`Unauthorized`, the skip-config and history ones say `未登录`.
"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from ..auth import get_auth_info_from_cookie
from ..config import get_config
from ..utils import split_key


def json_response(payload, status: int = 200, headers: dict | None = None):
    """`NextResponse.json(...)`.

    Starlette's `JSONResponse` already serializes with
    `ensure_ascii=False, separators=(",", ":")`, which is `JSON.stringify`, and
    sends `application/json` without a charset — the same headers Next.js set.
    `headers` carries the per-route extras (`Cache-Control: no-store` on the
    admin routes, `public, max-age=<cacheTime>` on the config ones).
    """
    return JSONResponse(payload, status_code=status, headers=headers)


def find_user(admin_config: dict, username) -> dict | None:
    """`adminConfig.UserConfig.Users.find((u) => u.username === username)`.

    The entry is returned by reference, which is what lets the admin routes
    mutate it in place and then save the whole config.
    """
    if username is None:
        return None
    return next(
        (
            u
            for u in (admin_config.get("UserConfig") or {}).get("Users") or []
            if u.get("username") == username
        ),
        None,
    )


async def user_guard(request: Request, unauthorized: str) -> tuple[str, dict, tuple[dict, int] | None]:
    """The cookie + ban opening every per-user route has.

    Returns `(username, config, rejected)`; `rejected` is the payload and
    status the caller has to answer with, or `None` to keep going. A ban is a
    401 even though it is a different condition than "no cookie", which is
    what upstream did.
    """
    auth_info = get_auth_info_from_cookie(request)
    if not auth_info or not auth_info.get("username"):
        return "", {}, ({"error": unauthorized}, 401)
    username = auth_info["username"]
    config = await get_config()
    entry = find_user(config, username)
    if entry and entry.get("banned"):
        return username, config, ({"error": "用户已被封禁"}, 401)
    return username, config, None

