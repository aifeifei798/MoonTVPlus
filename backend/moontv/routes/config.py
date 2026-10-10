"""`src/app/api/config/{sources,custom_category}/route.ts`.

Both are the unauthenticated-ish reads the app does on every page: the source
list is what the search and detail pages aggregate over, and the custom
categories are what the ranking page renders. Neither has an auth check of its
own — the auth gate already decided whether the request is allowed — and both
answer with `Cache-Control: public, max-age=<SiteInterfaceCacheTime>` so the
browser keeps them across page turns.
"""

from __future__ import annotations

from fastapi import Request

from ..auth import get_auth_info_from_cookie
from ..config import get_available_api_sites, get_cache_time, get_config
from ..routes import json_response


async def sources(request: Request):
    try:
        auth = get_auth_info_from_cookie(request)
        username = (auth or {}).get("username")
        sites = await get_available_api_sites(username)
        cache_time = await get_cache_time()
        return json_response(
            sites, headers={"Cache-Control": f"public, max-age={cache_time}, s-maxage=0"}
        )
    except Exception as error:  # noqa: BLE001
        print("获取按用户过滤的视频源失败:", error)
        return json_response({"error": "获取视频源失败"}, 500)


async def custom_category(request: Request):
    print("custom_category", request.url)
    config = await get_config()
    cache_time = await get_cache_time()
    return json_response(
        config.get("CustomCategories") or [],
        headers={"Cache-Control": f"public, max-age={cache_time}, s-maxage=0"},
    )


ROUTES = [
    ("/api/config/sources", sources, ["GET"]),
    ("/api/config/custom_category", custom_category, ["GET"]),
]
