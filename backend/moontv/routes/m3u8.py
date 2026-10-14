"""`src/app/api/m3u8/route.ts` — parse (`POST`) and segment proxy (`GET`).

Both verbs live at `/api/m3u8` (the `route.ts` comments naming `/parse` and
`/proxy` are aspirational; the App Router serves the file's directory, and the
frontend's `video-cache.ts` calls `GET /api/m3u8?url=...`). The route is in the
auth-gate exclusion list, so no cookie is needed — same as upstream's matcher.

The SSRF guard runs before any fetch on both verbs: without it either one is an
open proxy into the cluster (`?url=http://169.254.169.254/...`).
"""

from __future__ import annotations

import math

from fastapi import Request
from fastapi.responses import StreamingResponse

from ..m3u8 import parse_m3u8, stream_ts_segment
from ..url_guard import BlockedUrl, validate_media_url
from . import json_response


def _finite(value):
    """`JSON.stringify` writes `null` for NaN/Infinity; Python's `json` would
    emit the bare words `NaN`/`Infinity`, which are not JSON at all."""
    if isinstance(value, float) and (value != value or value in (math.inf, -math.inf)):
        return None
    return value


async def m3u8_parse(request: Request):
    try:
        try:
            body = await request.json()
        except Exception as error:  # noqa: BLE001 — `await request.json()` throwing is a 500 upstream too
            return json_response(
                {"error": "解析M3U8文件失败", "message": str(error) or "未知错误"}, 500
            )
        url = body.get("url") if isinstance(body, dict) else None

        if not url:
            return json_response({"error": "缺少 m3u8 URL"}, 400)

        guard = validate_media_url(url)
        if not guard.get("ok"):
            return json_response({"error": "地址不被允许", "message": guard.get("reason")}, 403)

        try:
            task = await parse_m3u8(url)
        except BlockedUrl as error:
            # A redirect hop, or the manifest's own AES-key host, is private.
            return json_response({"error": "地址不被允许", "message": str(error)}, 403)

        return json_response({
            "success": True,
            "data": {
                "title": task["title"],
                "type": task["type"],
                "totalSegments": len(task["ts_url_list"]),
                "duration": _finite(task["duration_second"]),
                "hasAes": task["aes"]["key"] is not None,
                "segments": [
                    {"index": index + 1, "url": segment}
                    for index, segment in enumerate(task["ts_url_list"])
                ],
            },
        })
    except Exception as error:  # noqa: BLE001
        print("解析M3U8失败:", error)
        return json_response(
            {
                "error": "解析M3U8文件失败",
                "message": str(error) if isinstance(error, Exception) else "未知错误",
            },
            500,
        )


async def _with_head(head, segments):
    """把已取出的首块还回流里，供 `StreamingResponse` 接着发。

    `m3u8_proxy` 先推进一次生成器，是为了让"上游 4xx/超时"在 200 提交之前就
    以原来的 JSON 500 冒出去（buffered 版就是这样）；之后的失败则和 buffered
    版一样无法回收——字节已经上路了。
    """
    if head is not None:
        yield head
    async for chunk in segments:
        yield chunk


async def m3u8_proxy(request: Request):
    try:
        url = request.query_params.get("url")

        if not url:
            return json_response({"error": "缺少 URL 参数"}, 400)

        guard = validate_media_url(url)
        if not guard.get("ok"):
            return json_response({"error": "地址不被允许", "message": guard.get("reason")}, 403)

        segments = stream_ts_segment(url)
        try:
            head = await segments.__anext__()
        except StopAsyncIteration:
            head = None
        except BlockedUrl as error:
            # The entry URL passed the guard; a redirect hop did not.
            return json_response({"error": "地址不被允许", "message": str(error)}, 403)
        except Exception as error:  # noqa: BLE001 — 首块之前失败，状态码还能改
            print("代理下载失败:", error)
            return json_response(
                {
                    "error": "下载失败",
                    "message": str(error) if isinstance(error, Exception) else "未知错误",
                },
                500,
            )

        return StreamingResponse(
            _with_head(head, segments),
            media_type="application/octet-stream",
            headers={"Cache-Control": "public, max-age=31536000"},
        )
    except Exception as error:  # noqa: BLE001
        print("代理下载失败:", error)
        return json_response(
            {
                "error": "下载失败",
                "message": str(error) if isinstance(error, Exception) else "未知错误",
            },
            500,
        )


ROUTES = [
    ("/api/m3u8", m3u8_parse, ["POST"]),
    ("/api/m3u8", m3u8_proxy, ["GET"]),
]
