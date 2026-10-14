"""`src/lib/m3u8-downloader.ts` — the server-side half.

Only the two functions the `/api/m3u8` route calls live here: `parse_m3u8`
(master-playlist resolution, segment list, durations, AES key fetch) and
`download_ts_segment` (one segment's bytes). Everything else in that module —
`downloadM3U8Video`, `mergeSegments`, `aesDecrypt`, the transmuxer, the
Range-chunk downloader — runs in the browser and stays in
`frontend/src/lib/m3u8-downloader.ts`, untouched.

`apply_url` is `new URL(target, base).href`, which is `urljoin` here: hand-rolled
string concatenation neither normalizes `../` nor strips the base's query, and
the player (hls.js, same `new URL` rule) would then request different segment
URLs than the ones parsed here.
"""

from __future__ import annotations

import datetime
import re
from urllib.parse import urljoin, urlsplit, parse_qsl

from .fetch import get_http_client
from .url_guard import BlockedUrl, open_guarded, validate_media_url, validate_public_url

# 上游是裸 `fetch(url)`（没传 timeout）；共享 client 的默认 10s（`fetch.py`）就是
# 这里的上限，播放列表/AES key 同样适用——一个 hang 住的上游不该把请求挂住。
_FETCH_TIMEOUT_S = 10.0


def apply_url(target: str, base: str) -> str:
    """`applyURL` — `new URL(targetURL, baseURL).href`."""
    try:
        return urljoin(base, target)
    except Exception:  # noqa: BLE001 — mirrors the TS try/catch
        return target


def _parse_float_prefix(text: str):
    """`parseFloat(x)` — leading number, `None` for NaN.

    `"8.0,"` is 8.0, `"8"` is 8, garbage is NaN. `float()` rejects the trailing
    comma, so the prefix is matched first.
    """
    match = re.match(r"[ \t\v\f\r\n]*([+-]?(?:[0-9]*\.[0-9]+|[0-9]+\.?)(?:[eE][+-]?[0-9]+)?)", text)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _is_master_playlist(content: str) -> bool:
    return "#EXT-X-STREAM-INF" in content


def _extract_sub_playlist_url(content: str, base_url: str) -> str | None:
    """Highest-bandwidth variant — the route never passes a `preferred` height,
    so this is the post-preference fallback, which is the branch that runs."""
    playlists: list[tuple[str, int]] = []
    lines = content.split("\n")
    for i, line in enumerate(lines):
        if line.strip().startswith("#EXT-X-STREAM-INF"):
            bw_match = re.search(r"BANDWIDTH=(\d+)", line)
            if i + 1 < len(lines):
                next_line = lines[i + 1].strip()
                if next_line and not next_line.startswith("#"):
                    playlists.append((
                        apply_url(next_line, base_url),
                        int(bw_match.group(1)) if bw_match else 0,
                    ))
    if not playlists:
        return None
    playlists.sort(key=lambda item: item[1], reverse=True)
    return playlists[0][0]


def _extract_title(url: str) -> str:
    """`extractTitleFromUrl` — `?title=` wins, else `video_YYYYMMDD_HHMMSS`."""
    try:
        query = urlsplit(url).query
        for key, value in parse_qsl(query):
            if key == "title" and value:
                return value
    except Exception:  # noqa: BLE001 — `new URL` throwing means "ignore"
        pass
    now = datetime.datetime.now()
    return f"video_{now.year}{now.month:02d}{now.day:02d}_{now.hour:02d}{now.minute:02d}{now.second:02d}"


async def _fetch_text(url: str, *, validator=validate_media_url) -> str:
    """Plain `fetch(url)` + `.text()`: no status check — like `fetch`, an HTTP
    error page is still a body, and the `#EXTM3U` check below rejects it.

    Redirects are followed hop by hop so the guard sees every hop, not just the
    URL the caller named.
    """
    response = await open_guarded(
        get_http_client(), url, validator=validator, timeout_s=_FETCH_TIMEOUT_S
    )
    try:
        await response.aread()
        return response.text
    finally:
        await response.aclose()


async def _fetch_bytes(url: str, *, validator=validate_media_url) -> tuple[bytes, int]:
    response = await open_guarded(
        get_http_client(), url, validator=validator, timeout_s=_FETCH_TIMEOUT_S
    )
    try:
        await response.aread()
        return response.content, response.status_code
    finally:
        await response.aclose()


async def parse_m3u8(url: str, depth: int = 0) -> dict:
    """`parseM3U8(url)` — the task object the route projects into its `data`."""
    if depth > 5:
        raise Exception("M3U8 解析层级过深，可能存在循环引用")

    m3u8_str = await _fetch_text(url)

    if m3u8_str[:7].upper() != "#EXTM3U":
        raise Exception("无效的 m3u8 链接")

    if _is_master_playlist(m3u8_str):
        sub_url = _extract_sub_playlist_url(m3u8_str, url)
        if not sub_url:
            raise Exception("无法从主播放列表中提取子播放列表")
        return await parse_m3u8(sub_url, depth + 1)

    ts_urls: list[str] = []
    finish_list: list[dict] = []
    segment_durations: list[float] = []
    duration_second = 0.0
    last_duration: float | None = None

    for line in m3u8_str.split("\n"):
        if line.startswith("#EXTINF:"):
            last_duration = _parse_float_prefix(line.split("#EXTINF:", 1)[1])
            duration_second += last_duration if last_duration is not None else float("nan")
        elif line[:1] and line[0] != "#" and line.strip():
            ts_url = apply_url(line.strip(), url)
            ts_urls.append(ts_url)
            finish_list.append({"title": line.strip(), "status": ""})
            segment_durations.append(last_duration if last_duration is not None else 0)
            last_duration = None

    aes_method = ""
    aes_uri = ""
    aes_iv = ""
    aes_key: bytes | None = None
    if "#EXT-X-KEY" in m3u8_str:
        method_match = re.search(r"METHOD=([^,\s]+)", m3u8_str)
        uri_match = re.search(r'URI="([^"]+)"', m3u8_str)
        iv_match = re.search(r"IV=([^,\s]+)", m3u8_str)
        aes_method = method_match.group(1) if method_match else ""
        aes_uri = apply_url(uri_match.group(1), url) if uri_match else ""
        aes_iv = iv_match.group(1) if iv_match else ""
        if aes_uri:
            # A throw here fails the whole parse, exactly like upstream's
            # `await fetch(...)` inside `parseM3U8`.
            # The key URI comes from the manifest, not from the caller, so it is
            # checked against the private-host blacklist only — a CDN that is not
            # in M3U8_PROXY_ALLOW_HOSTS must still be able to serve it.
            key_bytes, _status = await _fetch_bytes(aes_uri, validator=validate_public_url)
            aes_key = key_bytes

    return {
        "url": url,
        "title": _extract_title(url),
        "type": "TS",
        "ts_url_list": ts_urls,
        "finish_list": finish_list,
        "duration_second": duration_second,
        "segment_durations": segment_durations,
        "aes": {"method": aes_method, "uri": aes_uri, "iv": aes_iv, "key": aes_key},
    }


async def download_ts_segment(url: str) -> bytes:
    """`downloadTsSegment(url)` — `fetch` + `.arrayBuffer()`, throwing
    `下载失败: <status>` on HTTP errors."""
    data, status = await _fetch_bytes(url)
    if status >= 400:
        raise Exception(f"下载失败: {status}")
    return data


_TS_MAX_BYTES = 2 * 1024 * 1024  # 单个 ts 分片上限，超限断流
_TS_CHUNK = 64 * 1024
_TS_TIMEOUT_S = 10.0


async def stream_ts_segment(url: str):
    """`downloadTsSegment` 的流式版：64KB 分块 yield，超 ~2MB 断流，10s 超时。

    状态码语义与 buffered 版一致（>=400 即抛 `下载失败: <status>`）。重定向逐跳
    跟随，每一跳都重新跑 guard，所以一个允许过的公网域名不能 302 到内网。
    """
    client = get_http_client()
    response = await open_guarded(client, url, timeout_s=_TS_TIMEOUT_S)
    if response.status_code >= 400:
        await response.aclose()
        raise Exception(f"下载失败: {response.status_code}")
    try:
        sent = 0
        async for chunk in response.aiter_bytes(_TS_CHUNK):
            sent += len(chunk)
            if sent > _TS_MAX_BYTES:
                break
            yield chunk
    finally:
        await response.aclose()
