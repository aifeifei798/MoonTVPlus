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

Resilience (MoonTVPlus addition, not a port): two host families get a candidate
chain — `*.bgm.tv` (the 动漫「每日放送」 covers, whose official host's TLS handshake
is intermittently reset from mainland China) and `img<N>.doubanio.com` (every
poster the douban rows render). Each is tried on the official host, its https form,
then its mirrors. Every other host is fetched exactly once, exactly as upstream
did, and the error shapes (`{"error": ...}` at the upstream status, or 500
`Error fetching image`) are unchanged.

Hardening (MoonTVPlus addition, PLAN.md change 36): the route is unauthenticated
and takes a user-supplied URL, so it runs through `url_guard`'s private-network
blacklist and follows redirects hop by hop, re-checking each hop. Without it
`?url=http://127.0.0.1:6379/` is a free read of whatever is on the box. A blocked
URL answers 403 `{"error": "地址不被允许", "message": ...}`; `MOONTVPLUS_URL_GUARD_ALLOW_PRIVATE=1`
restores upstream's "fetch anything" behavior.
"""

from __future__ import annotations

import os
import re
from urllib.parse import urlsplit, urlunsplit

from fastapi import Request
from fastapi.responses import StreamingResponse

from ..fetch import get_http_client
from ..routes import json_response
from ..url_guard import BlockedUrl, guard_allows_private, open_guarded, validate_public_url

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

# 上游的 `fetch(imageUrl)` 不做任何地址校验，而这条路由在 auth gate 的 skip 列表
# 里（`<img>` 带不了 cookie），所以它是一个开放的 SSRF 通道：`?url=http://127.0.0.1:6379/`
# 会把内网服务的响应吐出来，重定向也能从公网域名跳进内网。这里复用 `url_guard` 的
# 私有网段黑名单 —— 真实海报域名都是公网的，默认开启不影响任何卡片。
# `MOONTVPLUS_URL_GUARD_ALLOW_PRIVATE=1` 恢复上游的"任意 URL 都取"（海报放在内网
# NAS 上的自托管者才需要）。
def _image_proxy_guard() -> bool:
    return not guard_allows_private()

# 番组计划（Bangumi）的封面域名 `lain.bgm.tv` 走 Cloudflare，TLS 握手在部分网络
# 里被中间设备间歇性重置 —— 动漫「每日放送」的海报因此一直加载不出来。这里按
# `douban.py` 的镜像思路换 host（path/query 不动），官源优先，失败才试镜像。
# 镜像是社区反代，同一 `/pic/cover/...` 路径返回的字节与官源一致。
# `MOONTVPLUS_BANGUMI_IMAGE_MIRRORS` 覆盖列表（逗号分隔，空值 = 只用官源）。
_BANGUMI_MIRROR_HOSTS = tuple(
    host
    for host in (
        item.strip()
        for item in os.environ.get(
            "MOONTVPLUS_BANGUMI_IMAGE_MIRRORS", "lain.fxbgm.tv"
        ).split(",")
    )
    if host
)

# 豆瓣图片站对**没有 Referer** 的请求直接回 418（浏览器卡片是
# `referrerPolicy='no-referrer'`，所以 `direct` 模式的海报全部显示不出来），代理
# 带 `Referer: https://movie.douban.com/` 则正常。官源若连代理也拒绝，退到前端
# `cmliussss-cdn-*` 模式已经使用的同一对镜像。
# `MOONTVPLUS_DOUBAN_IMAGE_MIRRORS` 覆盖列表（逗号分隔，空值 = 只用官源）。
_DOUBAN_MIRROR_HOSTS = tuple(
    host
    for host in (
        item.strip()
        for item in os.environ.get(
            "MOONTVPLUS_DOUBAN_IMAGE_MIRRORS",
            "img.doubanio.cmliussss.net,img.doubanio.cmliussss.com",
        ).split(",")
    )
    if host
)

_DOUBAN_IMAGE = re.compile(r"img\d+\.doubanio\.com\Z")


def _allow_any(_url):
    """The validator `open_guarded` uses when the guard is turned off."""
    return {"ok": True}


def _mirrors_for(host: str) -> tuple[str, ...] | None:
    """Mirror hosts for a poster family.

    `None` means the chain does not apply to this host at all; an empty tuple
    means the family is recognised but its mirrors were turned off, which still
    leaves the http -> https upgrade in the chain.
    """
    if host.endswith("bgm.tv"):
        return _BANGUMI_MIRROR_HOSTS
    if _DOUBAN_IMAGE.match(host):
        return _DOUBAN_MIRROR_HOSTS
    return None


def _image_candidates(url: str) -> list[str]:
    """`[original, https 版本, *镜像]` — only for the two poster families.

    Anything else gets the single URL back, so the proxy behaves exactly as
    upstream for every other host.
    """
    try:
        parts = urlsplit(url)
    except Exception:  # noqa: BLE001 — unparseable means no mirrors
        return [url]

    mirrors = _mirrors_for(parts.hostname or "")
    if mirrors is None:
        return [url]

    candidates = [url]
    if parts.scheme == "http":
        # 上游日历里的封面是 http，先升到 https 再换镜像：省一次 301，也让镜像
        # 与官源在同一 scheme 上可比。
        candidates.append(
            urlunsplit(("https", parts.hostname, parts.path, parts.query, parts.fragment))
        )
    for mirror in mirrors:
        candidates.append(
            urlunsplit(("https", mirror, parts.path, parts.query, parts.fragment))
        )
    return list(dict.fromkeys(candidates))


async def image_proxy(request: Request):
    """`/api/image-proxy?url=<encoded image url>`."""
    image_url = request.query_params.get("url")

    if not image_url:
        return json_response({"error": "Missing image URL"}, 400)

    guard = _image_proxy_guard()
    if guard:
        check = validate_public_url(image_url)
        if not check["ok"]:
            return json_response({"error": "地址不被允许", "message": check["reason"]}, 403)

    candidates = _image_candidates(image_url)
    client = get_http_client()

    for index, candidate in enumerate(candidates):
        try:
            # Redirects are followed hop by hop so a public host cannot bounce
            # the request into the cluster.
            upstream = await open_guarded(
                client,
                candidate,
                validator=validate_public_url if guard else _allow_any,
                headers=IMAGE_HEADERS,
                timeout_s=_FETCH_TIMEOUT_S,
            )
        except BlockedUrl:
            if index == len(candidates) - 1:
                return json_response(
                    {"error": "地址不被允许", "message": "不允许访问该地址"}, 403
                )
            continue
        except Exception:  # noqa: BLE001 — upstream's catch-all, try the next host
            if index == len(candidates) - 1:
                return json_response({"error": "Error fetching image"}, 500)
            continue

        # `!imageResponse.ok` → pass the upstream's status and sentence through.
        # A 5xx from the official host is the Cloudflare flake the mirrors exist
        # for, so it falls through; every other status is the answer.
        if not 200 <= upstream.status_code < 400:
            await upstream.aclose()
            if upstream.status_code >= 500 and index < len(candidates) - 1:
                continue
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

    # 每个候选都试过仍失败 —— 与上游的 catch-all 同一个形状。
    return json_response({"error": "Error fetching image"}, 500)


ROUTES = [("/api/image-proxy", image_proxy, ["GET"])]
