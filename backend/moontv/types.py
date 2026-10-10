"""Port of `src/lib/types.ts`.

MoonTV's data model is plain JSON objects, and the code mutates them in place
(`refineConfig`, `configSelfCheck`, the admin routes). Python dicts keep both
properties — insertion order and in-place mutation — so the wire format stays
byte-comparable with the TS app. The TypedDicts below are documentation only;
nothing validates at runtime.
"""

from __future__ import annotations

from typing import Any, TypedDict, Literal

# ---------------------------------------------------------------------------
# 播放记录
# ---------------------------------------------------------------------------

PlayRecord = TypedDict(
    "PlayRecord",
    {
        "title": str,
        "source_name": str,
        "cover": str,
        "year": str,
        "index": int,  # 第几集
        "total_episodes": int,  # 总集数
        "play_time": int,  # 播放进度（秒）
        "total_time": int,  # 总进度（秒）
        "save_time": int,  # 记录保存时间（时间戳，ms epoch）
        "search_title": str,  # 搜索时使用的标题
    },
)

# ---------------------------------------------------------------------------
# 收藏
# ---------------------------------------------------------------------------

Favorite = TypedDict(
    "Favorite",
    {
        "source_name": str,
        "total_episodes": int,
        "title": str,
        "year": str,
        "cover": str,
        "save_time": int,
        "search_title": str,
    },
)

# ---------------------------------------------------------------------------
# 追更
# ---------------------------------------------------------------------------

Following = TypedDict(
    "Following",
    {
        "source_name": str,
        "total_episodes": int,
        "watched_episodes": int,
        "title": str,
        "year": str,
        "cover": str,
        "save_time": int,
        "search_title": str,
    },
    total=False,
)
FollowingKeys = Literal["source", "id"]  # optional extras on Following

# ---------------------------------------------------------------------------
# 搜索结果
# ---------------------------------------------------------------------------

SearchResult = TypedDict(
    "SearchResult",
    {
        "id": str,
        "title": str,
        "poster": str,
        "episodes": list,
        "episodes_titles": list,
        "source": str,
        "source_name": str,
        "year": str,
    },
    total=False,
)
SearchResultOptional = Literal["class", "desc", "type_name", "douban_id"]

# ---------------------------------------------------------------------------
# 豆瓣
# ---------------------------------------------------------------------------

DoubanItem = TypedDict(
    "DoubanItem", {"id": str, "title": str, "poster": str, "rate": str, "year": str}
)

DoubanResult = TypedDict("DoubanResult", {"code": int, "message": str, "list": list})

# ---------------------------------------------------------------------------
# 跳过片头片尾
# ---------------------------------------------------------------------------

SkipConfig = TypedDict("SkipConfig", {"enable": bool, "intro_time": int, "outro_time": int})

# ---------------------------------------------------------------------------
# “今日新更”
# ---------------------------------------------------------------------------

TodayUpdatedItem = TypedDict(
    "TodayUpdatedItem",
    {
        "source": str,
        "id": str,
        "title": str,
        "poster": str,
        "episodes": int,
        "watchedEpisodes": int,
        "unwatchedEpisodes": int,
        "source_name": str,
        "year": str,
        "save_time": int,
        "oldEpisodes": int,
        "newEpisodes": int,
    },
)

TodayUpdatedRecord = TypedDict("TodayUpdatedRecord", {"date": str, "items": list})

# ---------------------------------------------------------------------------
# 弹幕（browser-only in MoonTV; kept here for the /api/v2 danmaku proxy if it moves
# server-side later）
# ---------------------------------------------------------------------------

DanmakuItem = TypedDict("DanmakuItem", {"time": int, "type": int, "color": int, "text": str}, total=False)
DanmakuComment = TypedDict("DanmakuComment", {"cid": int, "p": str, "m": str, "t": int})

# ---------------------------------------------------------------------------
# 用户记录（AdminConfig.UserConfig.Users 元素）
# ---------------------------------------------------------------------------

UserEntry = TypedDict(
    "UserEntry",
    {
        "username": str,
        "role": Literal["user", "admin", "owner"],
        "banned": bool,
        "group": str,
        "lastOnline": int,
    },
    total=False,
)

ApiSite = TypedDict("ApiSite", {"key": str, "api": str, "name": str, "detail": str}, total=False)

SourceEntry = TypedDict(
    "SourceEntry",
    {
        "key": str,
        "name": str,
        "api": str,
        "detail": str,
        "from": Literal["config", "custom"],
        "disabled": bool,
    },
    total=False,
)

CategoryEntry = TypedDict(
    "CategoryEntry",
    {
        "name": str,
        "type": Literal["movie", "tv"],
        "query": str,
        "from": Literal["config", "custom"],
        "disabled": bool,
    },
    total=False,
)

GroupEntry = TypedDict("GroupEntry", {"name": str, "sourceKeys": list})

# Auth cookie payload (`src/lib/auth.ts` + the HMAC in the login route).
AuthInfo = TypedDict(
    "AuthInfo",
    {
        "role": Literal["owner", "admin", "user"],
        "password": str,
        "username": str,
        "signature": str,
        "timestamp": int,
    },
    total=False,
)

AnyDict = dict[str, Any]
