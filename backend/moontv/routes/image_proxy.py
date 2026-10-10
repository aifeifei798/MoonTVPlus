"""Port of `src/app/api/image-proxy/route.ts` — the OrionTV-compatible image proxy.

This is what `utils.ts: processImageUrl` rewrites a douban poster to when
`DoubanImageProxyType` is `server` (and always for an `http://` image, which
would otherwise be blocked as mixed content), so a missing route here is the
reason posters come back broken. It is in the auth gate's skip list on both
sides — `SKIP_PATHS` and the matcher — because an `<img>` tag cannot send an
`auth` cookie reliably and OrionTV-style clients fetch it bare.

Upstream's `fetch(imageUrl)` has no timeout; the port takes `fetch.py`'s 30s
default so one dead upstream cannot pin a request forever. The response is the
image bytes with the upstream's `Content-Type` and the half-year cache headers,
not a JSON envelope.
"""

from __future__ import annotations

from fastapi import Response, Request

from ..fetch import fetch_with_timeout
from ..routes import json_response

# The browser headers the proxy presents to the image host; douban checks the
# referer before it serves a poster.
IMAGE_HEADERS = {
    "Referer": "https://movie.douban.com/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
    ),
}

# `缓存半年`, and the CDN variants Next's deployment targets used alongside it.
CACHE_HEADERS = {
    "Cache-Control": "public, max-age=15720000, s-maxage=15720000",
    "CDN-Cache-Control": "public, s-maxage=15720000",
    "Vercel-CDN-Cache-Control": "public, s-maxage=15720000",
    "Netlify-Vary": "query",
}


async def image_proxy(request: Request):
    """`/api/image-proxy?url=<encoded image url>`."""
    image_url = request.query_params.get("url")

    if not image_url:
        return json_response({"error": "Missing image URL"}, 400)

    try:
        upstream = await fetch_with_timeout(image_url, headers=IMAGE_HEADERS)

        # `!imageResponse.ok` → pass the upstream's status and sentence through.
        if not 200 <= upstream.status_code < 400:
            return json_response(
                {"error": upstream.reason_phrase or ""}, upstream.status_code
            )

        # `imageResponse.body` is never null in `fetch`, so this branch stayed
        # unreachable upstream and is kept here for the same reason.
        headers = dict(CACHE_HEADERS)
        content_type = upstream.headers.get("content-type")
        if content_type:
            headers["Content-Type"] = content_type

        return Response(content=upstream.content, status_code=200, headers=headers)
    except Exception:  # noqa: BLE001 — upstream's catch-all, message unused
        return json_response({"error": "Error fetching image"}, 500)


ROUTES = [("/api/image-proxy", image_proxy, ["GET"])]
