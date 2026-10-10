"""Port of `src/lib/admin.types.ts`.

Kept as plain dict shapes rather than pydantic models: `refineConfig` and
`configSelfCheck` mutate the object in place and re-serialize it to storage,
and JSON.stringify in TS drops `undefined` fields. `moontv.storage.sqlite`
therefore serializes with `strip_none()` (see `moontv.utils`) so the stored blob
matches what the TS app wrote.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

SiteConfig = TypedDict(
    "SiteConfig",
    {
        "SiteName": str,
        "Announcement": str,
        "SearchDownstreamMaxPage": int,
        "SiteInterfaceCacheTime": int,
        "DoubanProxyType": str,
        "DoubanProxy": str,
        "DoubanImageProxyType": str,
        "DoubanImageProxy": str,
        "DisableYellowFilter": bool,
        # 弹幕接口配置
        "DanmakuApiBaseUrl": str,
        # TVBox 接口开关与访问密码
        "TVBoxEnabled": bool,
        "TVBoxPassword": str,
        # 播放进度自动保存间隔（秒）
        "PlaybackSaveInterval": int,
    },
    total=False,
)

UserConfig = TypedDict(
    "UserConfig",
    {"AllowRegister": bool, "Users": list, "Groups": list},
    total=False,
)

SubscriptionConfig = TypedDict(
    "SubscriptionConfig",
    {
        "subscriptionUrl": str,
        "autoUpdate": bool,
        "updateInterval": int,  # seconds
        "lastUpdated": int,  # timestamp in seconds
        "importMode": Literal["overwrite", "merge"],
    },
    total=False,
)

AdminConfig = TypedDict(
    "AdminConfig",
    {
        "ConfigFile": str,
        "SiteConfig": dict,
        "UserConfig": dict,
        "SourceConfig": list,
        "CustomCategories": list,
        "SubscriptionConfig": dict,
    },
    total=False,
)

AdminConfigResult = TypedDict("AdminConfigResult", {"Role": Literal["owner", "admin"], "Config": dict})

# `config.json` / AdminConfig.ConfigFile shape (private `ConfigFileStruct` in config.ts)
ConfigFileStruct = TypedDict(
    "ConfigFileStruct", {"cache_time": int, "api_site": dict, "custom_category": list}, total=False
)

AnyConfig = Any
