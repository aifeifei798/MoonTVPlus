"""`src/app/api/tvbox/{config,categories}/route.ts`.

The TVBox/OrionTV surface: `config` hands out the source list (with a
`douban_custom` entry pointing at `categories` first), and `categories` either
lists the douban classes or proxies one class page through a self-fetch of
`/api/douban/recommends`.

Three things to keep while reading this:

  * `config` reads the origin through `getRequestOrigin` (forwarded headers
    included) but `categories` uses the raw `url.origin` — the port keeps both,
    so behind a proxy the two disagree exactly the way they did upstream.
  * `categories` never checks a password. Only `config` does; `categories`
    just needs `TVBoxEnabled`.
  * `Buffer.from(un, 'base64')` never throws — node skips non-alphabet bytes
    and replaces bad UTF-8 with U+FFFD — so upstream's `参数 un 非法` branch is
    dead code. The port decodes loosely the same way and keeps the branch.
"""

from __future__ import annotations

import datetime
from urllib.parse import urlsplit

from fastapi import Request

from ..config import get_available_api_sites, get_cache_time, get_config
from ..env import PASSWORD, STORAGE_TYPE, TVBOX_ENABLED, env, env_raw, env_str
from ..fetch import fetch_json
from ..request_origin import get_request_origin
from ..routes import json_response
from ..utils import js_parse_int, js_truthy, strip_none, url_search_params

# The douban classes README lists, in the order both routes rebuild.
MOVIE_DEFAULTS = ["热门", "最新", "经典", "豆瓣高分"]
TV_DEFAULTS = ["热门", "美剧", "英剧", "韩剧", "日剧", "国产剧", "日本动画"]


# The base64 alphabet, for the quantum decoder below.
_B64_VALUE = {ch: i for i, ch in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/")}


def _loose_b64(text: str) -> str:
    """`Buffer.from(un, 'base64').toString('utf8')`.

    Node's decoder never throws: bytes outside the alphabet are skipped,
    decoding stops at the first `=`, dangling bits short of a byte are
    dropped, and bad UTF-8 becomes U+FFFD. A quantum loop reproduces that
    directly, with no padding edge cases to catch.
    """
    bits = 0
    nbits = 0
    out = bytearray()
    for ch in text:
        if ch == "=":
            break
        value = _B64_VALUE.get(ch)
        if value is None:
            continue
        bits = (bits << 6) | value
        nbits += 6
        while nbits >= 8:
            nbits -= 8
            out.append((bits >> nbits) & 0xFF)
    return bytes(out).decode("utf-8", "replace")


def _number(text: str):
    """`Number(s)` for the `t` param: blank is 0, garbage is NaN.

    `float()` covers decimals, exponents and `Infinity`; the `int(..., 0)`
    fallback covers the hex/octal/binary literals `float()` rejects.
    """
    s = text.strip()
    if s == "":
        return 0
    try:
        return float(s)
    except ValueError:
        pass
    try:
        return int(s, 0)
    except ValueError:
        return float("nan")


def _json_num(value):
    """A payload number as `JSON.stringify` writes it: NaN becomes `null`."""
    if isinstance(value, float) and value != value:
        return None
    return value


def _tvbox_enabled(admin_config: dict) -> bool:
    """The `enabled` ternary both routes share (localstorage arm is parity-only)."""
    if env(STORAGE_TYPE, "sqlite") == "localstorage":
        raw = env_raw(TVBOX_ENABLED)
        return raw is None or str(raw).lower() == "true"
    return (admin_config.get("SiteConfig") or {}).get("TVBoxEnabled") is True


def _tvbox_password(admin_config: dict) -> str:
    if env(STORAGE_TYPE, "sqlite") == "localstorage":
        return env_str(PASSWORD)
    return (admin_config.get("SiteConfig") or {}).get("TVBoxPassword") or ""


# ---------------------------------------------------------------------------
# /api/tvbox/config
# ---------------------------------------------------------------------------


async def tvbox_config(request: Request):
    try:
        params = request.query_params
        input_password = params.get("pwd") or params.get("password") or ""
        un = params.get("un") or ""

        admin_config = await get_config()
        storage_type = env(STORAGE_TYPE, "sqlite")

        if storage_type != "localstorage" and not un.strip():
            return json_response({"error": "缺少参数 un"}, 400)

        username = ""
        if un.strip():
            try:
                username = _loose_b64(un)
            except Exception:  # noqa: BLE001 — dead branch, kept like upstream's
                return json_response({"error": "参数 un 非法"}, 400)

        if storage_type == "localstorage" and not input_password:
            input_password = env_str(PASSWORD)

        if not _tvbox_enabled(admin_config):
            return json_response({"error": "TVBox 接口未开启"}, 403)

        password = _tvbox_password(admin_config)
        if not password or input_password != password:
            return json_response({"error": "密码错误或未提供"}, 401)

        sites = await get_available_api_sites(username or None)
        cache_time = await get_cache_time()

        tvbox_sites = [
            {
                "key": s.get("key"),
                "api": s.get("api"),
                "name": s.get("name"),
                "type": 1,
                "searchable": 1,
                "quickSearch": 1,
                "ext": s.get("detail") or "",
            }
            for s in sites
        ]

        origin = get_request_origin(request)
        payload = {
            "sites": [
                {
                    "key": "douban_custom",
                    "api": f"{origin}/api/tvbox/categories",
                    "name": "豆瓣｜自定义",
                    "type": 1,
                    "searchable": 0,
                    "ext": "",
                },
                *tvbox_sites,
            ],
            "parses": [],
            "lives": [],
            "ads": [],
        }
        return json_response(
            payload,
            headers={"Cache-Control": f"public, max-age={cache_time}, s-maxage=0"},
        )
    except Exception:  # noqa: BLE001
        return json_response({"sites": [], "parses": [], "lives": [], "ads": []}, 500)


# ---------------------------------------------------------------------------
# /api/tvbox/categories
# ---------------------------------------------------------------------------


def _classes(custom: list) -> tuple[list, list]:
    """`classes` and the same-order `selectors` the `t` param indexes into."""
    classes: list = []
    selectors: list = []
    next_id = 1
    for name in MOVIE_DEFAULTS:
        classes.append({"type_id": next_id, "type_name": f"电影·{name}"})
        selectors.append({"kind": "movie", "category": name})
        next_id += 1
    for name in TV_DEFAULTS:
        classes.append({"type_id": next_id, "type_name": f"剧集·{name}"})
        selectors.append({"kind": "tv", "category": name})
        next_id += 1
    for entry in custom:
        classes.append({"type_id": next_id, "type_name": f"{entry.get('name')}"})
        selectors.append({"kind": entry.get("type"), "label": entry.get("query")})
        next_id += 1
    return classes, selectors


async def tvbox_categories(request: Request):
    params = request.query_params
    input_password = params.get("pwd") or params.get("password") or ""

    admin_config = await get_config()
    if env(STORAGE_TYPE, "sqlite") == "localstorage" and not input_password:
        input_password = env_str(PASSWORD)

    if not _tvbox_enabled(admin_config):
        return json_response({"error": "TVBox 接口未开启"}, 403)

    try:
        # Upstream re-fetched both here (`Promise.all([getConfig(), getCacheTime()])`);
        # the values are the same ones read above.
        cfg = admin_config
        cache_time = await get_cache_time()

        custom = [
            {"name": c.get("name") or c.get("query"), "type": c.get("type"), "query": c.get("query")}
            for c in (cfg.get("CustomCategories") or [])
        ]
        classes, selectors = _classes(custom)

        t_param = _number(params.get("t") or "")
        wd_param = params.get("wd") or ""
        parsed_pg = js_parse_int(params.get("pg") or "1")
        pg_param = float("nan") if parsed_pg is None else max(1, parsed_pg)
        parsed_size = js_parse_int(params.get("pagesize") or "20")
        page_size = float("nan") if parsed_size is None else max(1, min(50, parsed_size))

        if js_truthy(t_param) or wd_param:
            kind = "movie"
            category = ""
            label = ""
            sort = ""
            if js_truthy(t_param) and t_param >= 1 and t_param <= len(selectors):
                idx = t_param - 1
                if isinstance(idx, float) and idx.is_integer():
                    idx = int(idx)
                # A fractional `t` indexes `undefined` upstream and dies on
                # `.kind`; indexing a list with a float raises here instead —
                # both land in the `catch` below as a 500.
                sel = selectors[idx]
                kind = sel.get("kind")
                category = sel.get("category") or ""
                label = sel.get("label") or ""
            if wd_param:
                label = wd_param

            pairs = [("kind", kind)]
            # 热门: omit category/label, backend falls back to its recommends.
            # 最新: omit category/label, sort=time, pinned to the current year.
            if category == "最新":
                sort = "time"
                category = ""
                label = ""
                year = datetime.datetime.now(datetime.timezone.utc).year
                pairs.append(("year", str(year)))
            elif category == "热门":
                category = ""
                label = ""

            if category:
                pairs.append(("category", category))
            if label:
                pairs.append(("label", label))
            pairs.append(("start", (pg_param - 1) * page_size))
            pairs.append(("limit", page_size))
            if sort:
                pairs.append(("sort", sort))

            # `url.origin`, not `getRequestOrigin` — raw scheme://host.
            parts = urlsplit(str(request.url))
            origin = f"{parts.scheme}://{parts.netloc}"
            data = await fetch_json(
                f"{origin}/api/douban/recommends?{url_search_params(pairs)}"
            )
            raw_list = data.get("list") if isinstance(data, dict) else None
            list_ = raw_list if isinstance(raw_list, list) else []

            payload = {
                "code": 1,
                "msg": "success",
                "page": _json_num(pg_param),
                "pagecount": 999,
                "limit": _json_num(page_size),
                "total": 0,
                "list": [
                    strip_none(
                        {
                            "vod_id": item.get("id"),
                            "vod_name": item.get("title"),
                            "vod_pic": item.get("poster"),
                            "vod_year": item.get("year") or "",
                            "vod_remarks": item.get("rate") or "",
                        }
                    )
                    for item in list_
                ],
            }
            return json_response(
                payload,
                headers={"Cache-Control": f"public, max-age={cache_time}, s-maxage=0"},
            )

        return json_response(
            {"code": 1, "msg": "success", "class": classes, "list": []},
            headers={"Cache-Control": f"public, max-age={cache_time}, s-maxage=0"},
        )
    except Exception:  # noqa: BLE001
        return json_response({"code": 0, "msg": "error", "class": [], "list": []}, 500)


ROUTES = [
    ("/api/tvbox/config", tvbox_config, ["GET"]),
    ("/api/tvbox/categories", tvbox_categories, ["GET"]),
]
