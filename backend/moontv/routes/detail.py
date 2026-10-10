r"""`src/app/api/detail/route.ts` — one source's detail by `id`.

This is the endpoint the detail page falls back to when the search aggregation
did not carry the episode list, and the TVBox/OrionTV clients call it directly
after picking a source out of `/api/search/resources`. The `id` format check is
upstream's, not a tightening: `/^[\w-]+$/` in JS is `[A-Za-z0-9_-]`, spelled out
here because Python's `\w` would also accept every accented letter.

`getAvailableApiSites()` is called without a username upstream, so this route
sees every enabled source regardless of who asked.
"""

from __future__ import annotations

import re

from fastapi import Request

from ..config import get_available_api_sites, get_cache_time
from ..downstream import get_detail_from_api
from ..routes import json_response

# `/^[\w-]+$/` — JS's `\w` is ASCII-only, spelled out rather than relying on
# Python's `re.ASCII` flag so the class is visible.
VIDEO_ID = re.compile(r"[A-Za-z0-9_-]+\Z")


def _cache_headers(cache_time) -> dict:
    return {
        "Cache-Control": f"public, max-age={cache_time}, s-maxage={cache_time}",
        "CDN-Cache-Control": f"public, s-maxage={cache_time}",
        "Vercel-CDN-Cache-Control": f"public, s-maxage={cache_time}",
        "Netlify-Vary": "query",
    }


async def GET(request: Request):  # noqa: F402 — mirrors the TS export name
    video_id = request.query_params.get("id")
    source_code = request.query_params.get("source")

    if not video_id or not source_code:
        return json_response({"error": "缺少必要参数"}, 400)

    if not VIDEO_ID.match(video_id):
        return json_response({"error": "无效的视频ID格式"}, 400)

    try:
        api_sites = await get_available_api_sites()
        api_site = next((s for s in api_sites if s.get("key") == source_code), None)

        if not api_site:
            return json_response({"error": "无效的API来源"}, 400)

        result = await get_detail_from_api(api_site, video_id)
        cache_time = await get_cache_time()

        return json_response(result, headers=_cache_headers(cache_time))
    except Exception as error:  # noqa: BLE001 — upstream's `(error as Error).message`
        return json_response({"error": str(error)}, 500)


ROUTES = [("/api/detail", GET, ["GET"])]
