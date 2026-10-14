"""Port of `src/app/api/douban/**/route.ts` — the three douban data routes,
plus MoonTVPlus's own `/api/douban/search` (title search, which upstream never
had) that the record cards use to prefer a douban poster over the source's.

`/api/douban` (the `search_subjects` scrape plus `top250`), `/api/douban/categories`
(`recent_hot`) and `/api/douban/recommends`. All three answer `{code, message, list}`
with the same four cache headers, which is what lets the browser keep a page across
reloads — the reason the app's douban pages look empty on a cold start and full
after a refresh.

Resilience additions over upstream (same wire shape): each fetch tries the official
host then the two `cmliussss` mirrors (`moontv/douban.py`); successes are cached in
the `douban_cache` sqlite table and failures serve the last good `list` with
`X-Douban-Stale: 1` (`moontv/douban_cache.py`); `top250` reads the rexxar
`subject_collection` JSON first and only scrapes HTML as a fallback.

`/api/douban` and `/api/douban/categories` are behind the auth gate;
`/api/douban/recommends` is in the matcher's exclusion list and does its own
check (logged in, or TVBox enabled), because an OrionTV-style client reaches
it without a cookie.

The query integers are NaN-shaped on the wire: `parseInt('abc')` is NaN, both
validation comparisons are false against NaN, and the template literal then
interpolates the word `NaN` into the upstream URL. `None` would raise on those
comparisons, so a garbage value is carried as `float("nan")` and printed by
`_num`.
"""

from __future__ import annotations

import re

from fastapi import Request

from ..auth import get_auth_info_from_cookie
from ..config import get_cache_time, get_config
from ..douban import (
    fetch_douban_data,
    fetch_douban_html,
    fetch_subject_collection,
    search_douban,
)
from ..douban_cache import get_douban_cache, set_douban_cache
from ..env import STORAGE_TYPE, env, env_raw
from ..routes import json_response
from ..utils import js_parse_int, json_dump, strip_none, url_search_params

_RATE_FIXED = "{:.1f}"  # `toFixed(1)`; douban ratings are one decimal in practice
MAX_SEARCH_QUERY = 200  # MoonTVPlus-only cap on `/api/douban/search?q=`

# The `moviePattern` of `handleTop250`, verbatim minus the JS `\/` escapes.
TOP250_PATTERN = re.compile(
    r'<div class="item">[\s\S]*?<a[^>]+href="https?://movie\.douban\.com/subject/(\d+)/"'
    r'[\s\S]*?<img[^>]+alt="([^"]+)"[^>]*src="([^"]+)"'
    r'[\s\S]*?<span class="rating_num"[^>]*>([^<]*)</span>[\s\S]*?</div>'
)


def _cache_headers(cache_time) -> dict:
    """The four cache headers all three routes send."""
    return {
        "Cache-Control": f"public, max-age={cache_time}, s-maxage={cache_time}",
        "CDN-Cache-Control": f"public, s-maxage={cache_time}",
        "Vercel-CDN-Cache-Control": f"public, s-maxage={cache_time}",
        "Netlify-Vary": "query",
    }


async def _fresh_or_stale(key: str, fetch_list, error_sentence: str):
    """Run `fetch_list()`, cache its `list_`, or serve the last good one.

    Returns the `json_response` directly: fresh data on success, the stale
    `list` with `X-Douban-Stale: 1` when upstream is down, and the old 500
    only when there is no cache at all (cold start against a dead douban).
    """
    cache_time = await get_cache_time()
    headers = _cache_headers(cache_time)
    try:
        list_ = await fetch_list()
        set_douban_cache(key, list_)
        return json_response(
            {"code": 200, "message": "获取成功", "list": list_},
            headers=headers,
        )
    except Exception as error:  # noqa: BLE001
        cached, _age = get_douban_cache(key)
        if cached is not None:
            return json_response(
                {"code": 200, "message": "获取成功", "list": cached},
                headers={**headers, "X-Douban-Stale": "1"},
            )
        return json_response(
            {"error": error_sentence, "details": str(error or "")}, 500
        )


def _js_int(request: Request, name: str, default: str) -> float:
    """`parseInt(searchParams.get(k) || '16')`, NaN staying NaN."""
    parsed = js_parse_int(request.query_params.get(name) or default)
    return float("nan") if parsed is None else parsed


def _num(value) -> str:
    """`${n}` in a template literal — node spells the NaN one `NaN`."""
    if isinstance(value, float) and value != value:
        return "NaN"
    return str(value)


def _item(item: dict) -> dict:
    """A `search_subjects` item → `DoubanItem`, with `strip_none` for undefined."""
    return strip_none(
        {
            "id": item.get("id"),
            "title": item.get("title"),
            "poster": item.get("cover"),
            "rate": item.get("rate"),
            "year": "",
        }
    )


async def douban(request: Request):
    """`/api/douban?tag=&type=&pageSize=&pageStart=`."""
    type_ = request.query_params.get("type")
    tag = request.query_params.get("tag")
    page_size = _js_int(request, "pageSize", "16")
    page_start = _js_int(request, "pageStart", "0")

    if not type_ or not tag:
        return json_response({"error": "缺少必要参数: type 或 tag"}, 400)

    if type_ not in ("tv", "movie"):
        return json_response({"error": "type 参数必须是 tv 或 movie"}, 400)

    # NaN fails both comparisons in JS, so a garbage pageSize gets through.
    if page_size == page_size and (page_size < 1 or page_size > 100):
        return json_response({"error": "pageSize 必须在 1-100 之间"}, 400)

    if page_start == page_start and page_start < 0:
        return json_response({"error": "pageStart 不能小于 0"}, 400)

    if tag == "top250":
        return await douban_top250(page_start)

    target = (
        "https://movie.douban.com/j/search_subjects"
        f"?type={type_}&tag={tag}&sort=recommend"
        f"&page_limit={_num(page_size)}&page_start={_num(page_start)}"
    )

    async def _fetch():
        douban_data = await fetch_douban_data(target)
        return [_item(item) for item in douban_data["subjects"]]

    return await _fresh_or_stale(f"douban:{target}", _fetch, "获取豆瓣数据失败")


async def douban_top250(page_start) -> json_response:
    """`handleTop250` — rexxar JSON first, the HTML scrape as fallback.

    The HTML scrape was the only source upstream; it breaks on any markup or
    bot-check change. `subject_collection/movie_top250/items` carries the same
    fields (id/title/cover/rating/year) as JSON and is what the ranking page's
    `getDoubanCollection` already reads, so the server agrees with the client.
    """
    key = f"top250:{_num(page_start)}"

    async def _fetch():
        try:
            data = await fetch_subject_collection(
                "movie_top250", int(page_start) if page_start == page_start else 0, 25
            )
            return [_collection_item(item) for item in data.get("subject_collection_items") or []]
        except Exception:
            html = await fetch_douban_html(
                f"https://movie.douban.com/top250?start={_num(page_start)}&filter="
            )
            # 正则回溯昂贵，只喂前 512KB（Top250 条目全在页首）。
            html = html[: 512 * 1024]
            list_ = []
            for match in TOP250_PATTERN.finditer(html):
                subject_id, title, cover, rate = match.groups()
                list_.append(
                    {
                        "id": subject_id,
                        "title": title,
                        # `cover.replace(/^http:/, 'https:')`
                        "poster": re.sub(r"^http:", "https:", cover),
                        "rate": rate or "",
                        "year": "",
                    }
                )
            return list_

    return await _fresh_or_stale(key, _fetch, "获取豆瓣 Top250 数据失败")


def _collection_item(item: dict) -> dict:
    """A `subject_collection` item → `DoubanItem` (same shape as the scrape)."""
    pic = item.get("pic") or {}
    rating = item.get("rating") or {}
    value = rating.get("value")
    year_match = re.search(r"(\d{4})", item.get("card_subtitle") or "")
    return strip_none(
        {
            "id": item.get("id"),
            "title": item.get("title"),
            "poster": item.get("cover_url") or pic.get("large") or pic.get("normal") or "",
            "rate": _RATE_FIXED.format(value) if value else "",
            "year": year_match.group(1) if year_match else "",
        }
    )


async def douban_categories(request: Request):
    """`/api/douban/categories?kind=&category=&type=&limit=&start=`."""
    kind = request.query_params.get("kind") or "movie"
    category = request.query_params.get("category")
    type_ = request.query_params.get("type")
    page_limit = _js_int(request, "limit", "20")
    page_start = _js_int(request, "start", "0")

    if not kind or not category or not type_:
        return json_response({"error": "缺少必要参数: kind 或 category 或 type"}, 400)

    if kind not in ("tv", "movie"):
        return json_response({"error": "kind 参数必须是 tv 或 movie"}, 400)

    if page_limit == page_limit and (page_limit < 1 or page_limit > 100):
        return json_response({"error": "pageSize 必须在 1-100 之间"}, 400)

    if page_start == page_start and page_start < 0:
        return json_response({"error": "pageStart 不能小于 0"}, 400)

    target = (
        "https://m.douban.com/rexxar/api/v2/subject/recent_hot/"
        f"{kind}?start={_num(page_start)}&limit={_num(page_limit)}"
        f"&category={category}&type={type_}"
    )

    async def _fetch():
        douban_data = await fetch_douban_data(target)
        return [_category_item(item) for item in douban_data["items"]]

    return await _fresh_or_stale(f"douban:{target}", _fetch, "获取豆瓣数据失败")


def _category_item(item: dict) -> dict:
    """`recent_hot`'s item → `DoubanItem`."""
    pic = item.get("pic") or {}
    rating = item.get("rating") or {}
    value = rating.get("value")
    year_match = re.search(r"(\d{4})", item.get("card_subtitle") or "")
    return strip_none(
        {
            "id": item.get("id"),
            "title": item.get("title"),
            "poster": pic.get("normal") or pic.get("large") or "",
            "rate": _RATE_FIXED.format(value) if value else "",
            "year": year_match.group(1) if year_match else "",
        }
    )


async def douban_recommends(request: Request):
    """`/api/douban/recommends` — ungated, so it checks the cookie itself."""
    auth = get_auth_info_from_cookie(request)
    if not auth or not auth.get("username"):
        config = await get_config()
        storage_type = env(STORAGE_TYPE, "sqlite")
        if storage_type == "localstorage":  # not reachable in MoonTVPlus; parity only
            raw = env_raw("TVBOX_ENABLED")
            enabled = raw is None or str(raw).lower() == "true"
        else:
            enabled = (config.get("SiteConfig") or {}).get("TVBoxEnabled") is True
        if not enabled:
            return json_response({"error": "Forbidden"}, 403)

    kind = request.query_params.get("kind")

    def _param(name: str, default: str = "0") -> float:
        parsed = js_parse_int(request.query_params.get(name) or default)
        return float("nan") if parsed is None else parsed

    page_limit = _param("limit", "20")
    page_start = _param("start")

    def _all_or(name: str) -> str | None:
        """`searchParams.get(k) === 'all' ? '' : searchParams.get(k)`."""
        value = request.query_params.get(name)
        return None if value == "all" else value

    category = _all_or("category")
    format_ = _all_or("format")
    region = _all_or("region")
    year = _all_or("year")
    platform = _all_or("platform")
    sort = "" if request.query_params.get("sort") == "T" else request.query_params.get("sort")
    label = _all_or("label")

    if not kind:
        return json_response({"error": "缺少必要参数: kind"}, 400)

    selected_categories = {"类型": category}
    if format_:
        selected_categories["形式"] = format_
    if region:
        selected_categories["地区"] = region

    tags = []
    if category:
        tags.append(category)
    if not category and format_:
        tags.append(format_)
    if label:
        tags.append(label)
    if region:
        tags.append(region)
    if year:
        tags.append(year)
    if platform:
        tags.append(platform)

    pairs = [
        ("refresh", "0"),
        ("start", _num(page_start)),
        ("count", _num(page_limit)),
        ("selected_categories", json_dump(selected_categories)),
        ("uncollect", "false"),
        ("score_range", "0,10"),
        ("tags", ",".join(tags)),
    ]
    if sort:
        pairs.append(("sort", sort))

    target = (
        f"https://m.douban.com/rexxar/api/v2/{kind}/recommend"
        f"?{url_search_params(pairs)}"
    )
    print(target)  # upstream's `console.log(target)`

    async def _fetch():
        douban_data = await fetch_douban_data(target)
        return [
            strip_none(
                {
                    "id": item.get("id"),
                    "title": item.get("title"),
                    "poster": (item.get("pic") or {}).get("normal")
                    or (item.get("pic") or {}).get("large")
                    or "",
                    "rate": _RATE_FIXED.format((item.get("rating") or {}).get("value"))
                    if (item.get("rating") or {}).get("value")
                    else "",
                    "year": item.get("year"),
                }
            )
            for item in douban_data["items"]
            if item.get("type") in ("movie", "tv")
        ]

    return await _fresh_or_stale(f"douban:{target}", _fetch, "获取豆瓣数据失败")


async def douban_search(request: Request):
    """`/api/douban/search?q=<title>` — MoonTVPlus-only title search.

    Upstream has no title search, so this is additive surface rather than a
    parity question. It exists because a play record's `cover` is whatever the
    source's own poster is, and the record cards want a douban poster first.
    Same `{code, message, list}` shape and cache headers as the other three
    douban routes, and the same stale-while-revalidate behaviour.
    """
    query = (request.query_params.get("q") or "").strip()
    if not query:
        return json_response({"error": "缺少必要参数: q"}, 400)
    # MoonTVPlus addition (PLAN.md change 39): the query goes straight into a
    # douban URL and into a `douban_cache` key, so an unbounded one is both a
    # hammering vector and unbounded cache growth. A title is never longer than
    # this; anything past it is not a title.
    if len(query) > MAX_SEARCH_QUERY:
        return json_response({"error": "搜索关键词过长"}, 400)

    async def _fetch():
        return await search_douban(query)

    return await _fresh_or_stale(
        f"douban:search:{query}", _fetch, "获取豆瓣搜索数据失败"
    )


ROUTES = [
    ("/api/douban", douban, ["GET"]),
    ("/api/douban/categories", douban_categories, ["GET"]),
    ("/api/douban/recommends", douban_recommends, ["GET"]),
    ("/api/douban/search", douban_search, ["GET"]),
]
