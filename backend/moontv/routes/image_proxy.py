"""Port of `src/app/api/image-proxy/route.ts` — the OrionTV-compatible image proxy.

This is what `utils.ts: processImageUrl` rewrites a douban poster to when
`DoubanImageProxyType` is `server` (and always for an `http://` image, which
would otherwise be blocked as mixed content), so a missing route here is the
reason posters come back broken. It is in the auth gate's skip list on both
sides — `SKIP_PATHS` and the matcher — because an `<img>` tag cannot send an
`auth` cookie reliably and OrionTV-style clients fetch it bare.

Upstream's `fetch(imageUrl)` has no timeout; the port takes `fetch.py`'s 10s
client default so one dead upstream cannot pin a request forever. The response
streams the image bytes in 64KB chunks with the upstream's `Content-Type` and the
half-year cache headers, capped at 8MB, not a JSON envelope.
"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import StreamingResponse

from ..fetch import get_http_client
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


# 单张图片上限 8MB：超限即断流，避免超大上游拖满内存/带宽。
IMAGE_MAX_BYTES = 8 * 1024 * 1024
_CHUNK = 64 * 1024
_FETCH_TIMEOUT_S = 10.0


async def image_proxy(request: Request):
    """`/api/image-proxy?url=<encoded image url>`."""
    image_url = request.query_params.get("url")

    if not image_url:
        return json_response({"error": "Missing image URL"}, 400)

    try:
        client = get_http_client()
        upstream = await client.send(
            client.build_request(
                "GET", image_url, headers=IMAGE_HEADERS, timeout=_FETCH_TIMEOUT_S
            ),
            stream=True,
        )

        # `!imageResponse.ok` → pass the upstream's status and sentence through.
        if not 200 <= upstream.status_code < 400:
            await upstream.aclose()
            return json_response(
                {"error": upstream.reason_phrase or ""}, upstream.status_code
            )

        try:
            total = int(upstream.headers.get("content-length") or 0)
        except ValueError:
            total = 0
        if total > IMAGE_MAX_BYTES:
            await upstream.aclose()
            return json_response({"error": "Error fetching image"}, 500)

        # `imageResponse.body` is never null in `fetch`, so this branch stayed
        # unreachable upstream and is kept here for the same reason.
        headers = dict(CACHE_HEADERS)
        content_type = upstream.headers.get("content-type")
        if content_type:
            headers["Content-Type"] = content_type

        async def _gen():
            # 真流式：64KB 分块转发上游，超 8MB 断流，结束即关连接。
            try:
                sent = 0
                async for chunk in upstream.aiter_bytes(_CHUNK):
                    sent += len(chunk)
                    if sent > IMAGE_MAX_BYTES:
                        break
                    yield chunk
            finally:
                await upstream.aclose()

        return StreamingResponse(_gen(), status_code=200, headers=headers)
    except Exception:  # noqa: BLE001 — upstream's catch-all, message unused
        return json_response({"error": "Error fetching image"}, 500)


ROUTES = [("/api/image-proxy", image_proxy, ["GET"])]
