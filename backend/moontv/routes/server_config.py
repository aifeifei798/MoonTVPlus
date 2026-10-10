"""`src/app/api/server-config/route.ts`.

This is one of the routes the auth matcher deliberately leaves open, because the
frontend calls it before anyone has logged in: the login page needs `SiteName`
to render and `StorageType` to decide which storage path to take.
"""

from __future__ import annotations

from fastapi import Request, Response

from ..config import get_config
from ..env import STORAGE_TYPE, env
from ..routes import json_response

async def GET(request: Request) -> Response:  # noqa: F402 — mirrors the TS export name
    print("server-config called: ", request.url)

    config = await get_config()
    result = {
        "SiteName": (config.get("SiteConfig") or {}).get("SiteName"),
        "StorageType": env(STORAGE_TYPE, "sqlite"),
    }
    return json_response(result)


ROUTES = [("/api/server-config", GET, ["GET"])]
