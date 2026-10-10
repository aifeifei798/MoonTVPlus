"""Port of `src/lib/downstream.ts` — talking to the upstream API sites.

The upstreams are ordinary `?ac=videolist` media-server APIs, and the shape
this module returns is the shape the client libs and the TVBox/OrionTV
external clients expect, so `SearchResult`'s key order and its optional
fields are preserved rather than normalized: `class`, `desc`, `type_name` and
`douban_id` are `undefined` on a MoonTV result when the upstream did not send
them, and `JSON.stringify` dropped them — `strip_none()` does the same here.

Two things are worth knowing before changing this file:

  * `parseEpisodes` is where the two wire dialects of `vod_play_url` are
      understood (`$$$` source groups, `#` episodes, `title$ url` pairs), and
      the fallback that scrapes `.m3u8` links out of `vod_content` only runs
      when the first path produced nothing.
  * the page fetches in the parallel branch reject the whole site, not just
      the page — `Promise.all` rejects, the generator throws, and the search
      route records the site in `failedSources`. `asyncio.gather` is asked for
      exceptions and re-raises the first one so that stays true.
"""

from __future__ import annotations

import asyncio
import re

from .config import API_CONFIG, get_config
from .fetch import fetch_with_timeout
from .utils import clean_html_tags, encode_uri_component, js_min

# 匹配 m3u8 链接的正正则（`match` with /g returns whole matches, so the group
# is non-capturing to keep `findall` returning the same strings）
M3U8_PATTERN = re.compile(r"(?:https?://[^\"'\s]+?\.m3u8)")

# 特定站点规则（优先）：ffzy 的 m3u8 地址带一个 8-digit 目录和 a-f0-9 后缀
FFZY_M3U8_PATTERN = re.compile(
    r"\$(?:https?://[^\"'\s]+?/[0-9]{8}/[0-9]+_[a-f0-9]+/index\.m3u8)"
)
GENERIC_M3U8_WITH_DOLLAR = re.compile(r"\$(?:https?://[^\"'\s]+?\.m3u8)")

DETAIL_TITLE_PATTERN = re.compile(r"<h1[^>]*>([^<]+)</h1>")
DETAIL_DESC_PATTERN = re.compile(r"<div[^>]*class=[\"']sketch[\"'][^>]*>([\s\S]*?)</div>")
DETAIL_COVER_PATTERN = re.compile(r"(?:https?://[^\"'\s]+?\.jpg)")
DETAIL_YEAR_PATTERN = re.compile(r">([0-9]{4})<")


def _js_to_string(value):
    """`x.toString()` — a JSON number becomes the digits JS wrote, not `123.0`."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if value is None:
        return "undefined"
    return str(value)


# ---------------------------------------------------------------------------
# 通用的播放源解析
# ---------------------------------------------------------------------------


def parse_episodes(vod_play_url=None, fallback_content=None):
    """`parseEpisodes` → `(episodes, titles)`.

    `vod_play_url` is `src1$$$src2`, each source `t1$u1#t2$u2`; the source with
    the most episodes wins, and an entry whose url does not end in `.m3u8` is
    dropped. With nothing parsed, the `.m3u8` links in `vod_content` are used
    and numbered `1..n`.
    """
    episodes: list = []
    titles: list = []

    if vod_play_url:
        for source in vod_play_url.split("$$$"):
            current_episodes: list = []
            current_titles: list = []

            for entry in source.split("#"):
                parts = entry.split("$")
                title = parts[0]
                url = parts[1] if len(parts) > 1 else None
                if url and url.endswith(".m3u8"):
                    current_titles.append(title)
                    current_episodes.append(url)

            # 选用分集最多的播放源
            if len(current_episodes) > len(episodes):
                episodes = current_episodes
                titles = current_titles

    # 2. 如果没有解析到，尝试 fallback 内容
    if not episodes and fallback_content:
        episodes = [re.sub(r"^\$", "", link) for link in M3U8_PATTERN.findall(fallback_content)]
        titles = [str(i + 1) for i in range(len(episodes))]

    return episodes, titles


# ---------------------------------------------------------------------------
# 映射 API 数据到 SearchResult
# ---------------------------------------------------------------------------


def _four_digit_year(vod_year):
    r"""`vod_year?.match(/\d{4}/)?.[0] || 'unknown'`."""
    if not vod_year:
        return "unknown"
    match = re.search(r"[0-9]{4}", vod_year)
    return match.group(0) if match else "unknown"


def map_item_to_result(item: dict, api_site: dict, api_name: str) -> dict:
    """`mapItemToResult`.

    The optional fields are copied only when the upstream object actually had
    the key: `JSON.stringify` drops a property whose value is `undefined`
    (absent key) but keeps one whose value is `null` (present key), and Python
    collapses the two. Testing `"k" in item` keeps them apart.
    """
    episodes, titles = parse_episodes(item.get("vod_play_url"), item.get("vod_content"))

    vod_name = item.get("vod_name")
    if not isinstance(vod_name, str):
        # `item.vod_name.trim()` — V8 throws, the search route reports it as a
        # failed source, so the failure has to happen here too.
        raise AttributeError(
            f"Cannot read properties of {'null' if 'vod_name' in item else 'undefined'} (reading 'trim')"
        )

    vod_id = item.get("vod_id")
    if vod_id is None:
        raise TypeError(
            f"Cannot read properties of {'null' if 'vod_id' in item else 'undefined'} (reading 'toString')"
        )

    result: dict = {
        "id": _js_to_string(vod_id),
        "title": re.sub(r"\s+", " ", vod_name.strip()),
    }
    if "vod_pic" in item:
        result["poster"] = item["vod_pic"]
    result["episodes"] = episodes
    result["episodes_titles"] = titles
    result["source"] = api_site.get("key")
    result["source_name"] = api_name
    if "vod_class" in item:
        result["class"] = item["vod_class"]
    result["year"] = _four_digit_year(item.get("vod_year"))
    result["desc"] = clean_html_tags(item.get("vod_content") or "")
    if "type_name" in item:
        result["type_name"] = item["type_name"]
    if "vod_douban_id" in item:
        result["douban_id"] = item["vod_douban_id"]
    return result


# ---------------------------------------------------------------------------
# API 搜索流
# ---------------------------------------------------------------------------


async def search_from_api_stream(api_site: dict, query: str, parallel: bool = True, timeout_ms=None):
    """`searchFromApiStream` — an async generator of `SearchResult[]` pages.

    `timeout_ms` is the `?timeout=` seconds the search route multiplied by 1000;
    `None` is `fetchWithTimeout`'s 30s.
    """
    search_cfg = API_CONFIG["search"]
    api_url = api_site["api"] + search_cfg["path"] + encode_uri_component(query)

    response = await fetch_with_timeout(api_url, search_cfg["headers"], timeout_ms)
    if response.status_code >= 400:  # `!response.ok`
        return

    data = response.json()
    first_page = data.get("list") if isinstance(data, dict) else None
    if not isinstance(first_page, list):
        return

    # 第一页
    yield [map_item_to_result(item, api_site, api_site.get("name")) for item in first_page]

    # 分页
    config = await get_config()
    max_pages = (config.get("SiteConfig") or {}).get("SearchDownstreamMaxPage")
    page_count = data.get("pagecount") or 1
    pages_to_fetch = js_min(page_count, max_pages)

    if pages_to_fetch is not None and pages_to_fetch > 1:
        last_page = int(pages_to_fetch)

        async def fetch_page(page: int):
            page_url = (
                api_site["api"]
                + search_cfg["pagePath"]
                .replace("{query}", encode_uri_component(query))
                .replace("{page}", str(page))
            )
            page_response = await fetch_with_timeout(page_url, search_cfg["headers"], timeout_ms)
            if page_response.status_code >= 400:
                return None

            page_data = page_response.json()
            page_list = page_data.get("list") if isinstance(page_data, dict) else None
            if not isinstance(page_list, list):
                return None

            results = [map_item_to_result(item, api_site, api_site.get("name")) for item in page_list]
            return {"page": page, "results": results}

        if parallel:
            # ------------------ 并行模式 ------------------
            settled = await asyncio.gather(
                *(fetch_page(page) for page in range(2, last_page + 1)),
                return_exceptions=True,
            )
            for settled_page in settled:
                # `Promise.all` rejected: the whole site fails, not just a page.
                if isinstance(settled_page, BaseException):
                    raise settled_page

            ordered = sorted(
                (r for r in settled if isinstance(r, dict) and r["results"]),
                key=lambda r: r["page"],
            )
            for settled_page in ordered:
                yield settled_page["results"]
        else:
            # ------------------ 顺序模式 ------------------
            for page in range(2, last_page + 1):
                settled_page = await fetch_page(page)
                if settled_page and settled_page["results"]:
                    yield settled_page["results"]


# ---------------------------------------------------------------------------
# 获取详情
# ---------------------------------------------------------------------------


async def get_detail_from_api(api_site: dict, video_id) -> dict:
    """`getDetailFromApi`."""
    if api_site.get("detail"):
        return await handle_special_source_detail(video_id, api_site)

    detail_cfg = API_CONFIG["detail"]
    detail_url = f"{api_site['api']}{detail_cfg['path']}{video_id}"
    response = await fetch_with_timeout(detail_url, detail_cfg["headers"])

    if response.status_code >= 400:
        raise Exception(f"详情请求失败: {response.status_code}")

    data = response.json()
    detail_list = data.get("list") if isinstance(data, dict) else None
    if not isinstance(detail_list, list) or len(detail_list) == 0:
        raise Exception("获取到的详情内容无效")

    video = detail_list[0]
    episodes, titles = parse_episodes(video.get("vod_play_url"), video.get("vod_content"))

    result: dict = {
        "id": _js_to_string(video_id),
        "title": video.get("vod_name"),
    }
    if "vod_pic" in video:
        result["poster"] = video["vod_pic"]
    result["episodes"] = episodes
    result["episodes_titles"] = titles
    result["source"] = api_site.get("key")
    result["source_name"] = api_site.get("name")
    if "vod_class" in video:
        result["class"] = video["vod_class"]
    result["year"] = _four_digit_year(video.get("vod_year"))
    result["desc"] = clean_html_tags(video.get("vod_content"))
    if "type_name" in video:
        result["type_name"] = video["type_name"]
    if "vod_douban_id" in video:
        result["douban_id"] = video["vod_douban_id"]
    return result


async def handle_special_source_detail(video_id, api_site: dict) -> dict:
    """`handleSpecialSourceDetail` — scrape an HTML detail page.

    These sites have no JSON endpoint, only `/index.php/vod/detail/id/N.html`,
    so the episode list is whatever the page body happens to contain: the
    site-specific pattern first (`ffzy`), then the generic `$…m3u8` one, then
    `parseEpisodes`'s content fallback. The `$` is part of the match in JS,
    which is why the port strips a leading character here rather than
    capturing the url.
    """
    detail_cfg = API_CONFIG["detail"]
    detail_url = f"{api_site['detail']}/index.php/vod/detail/id/{video_id}.html"
    response = await fetch_with_timeout(detail_url, detail_cfg["headers"])

    if response.status_code >= 400:
        raise Exception(f"详情页请求失败: {response.status_code}")

    html = response.text

    # 特定站点规则（优先）
    matches: list = []
    if api_site.get("key") == "ffzy":
        matches = FFZY_M3U8_PATTERN.findall(html)

    # 通用正则
    if not matches:
        matches = GENERIC_M3U8_WITH_DOLLAR.findall(html)

    # 去重并清理（first-seen order, as `Array.from(new Set(...))` was）
    seen: set[str] = set()
    cleaned: list = []
    for link in matches:
        if link in seen:
            continue
        seen.add(link)
        clean = link[1:]  # 去掉 $
        paren_index = clean.find("(")
        cleaned.append(clean[:paren_index] if paren_index > 0 else clean)
    matches = cleaned

    # 如果依旧没解析到，用 parseEpisodes fallback
    if not matches:
        episodes, _ = parse_episodes(None, html)
        matches = episodes

    episodes_titles = [str(i + 1) for i in range(len(matches))]

    title_match = DETAIL_TITLE_PATTERN.search(html)
    title = title_match.group(1).strip() if title_match else ""

    desc_match = DETAIL_DESC_PATTERN.search(html)
    desc = clean_html_tags(desc_match.group(1) if desc_match else "")

    cover_match = DETAIL_COVER_PATTERN.search(html)
    cover = cover_match.group(0).strip() if cover_match else ""

    year_match = DETAIL_YEAR_PATTERN.search(html)
    year = year_match.group(1) if year_match else "unknown"

    return {
        "id": video_id,
        "title": title,
        "poster": cover,
        "episodes": matches,
        "episodes_titles": episodes_titles,
        "source": api_site.get("key"),
        "source_name": api_site.get("name"),
        "class": "",
        "year": year,
        "desc": desc,
        "type_name": "",
        "douban_id": 0,
    }
