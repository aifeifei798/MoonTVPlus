"""Environment variable accessors.

Every MoonTV route reads `process.env.X`, and TypeScript's `||` treats both a
missing variable and an empty string as falsy. `env()` reproduces that, so
`NEXT_PUBLIC_SITE_NAME=""` behaves the same as unset, exactly as in the TS code.
"""

from __future__ import annotations

import os
from typing import Any

_TRUTHY = "true"


def env(name: str, default: Any = None) -> Any:
    """`process.env.X || default`."""
    value = os.environ.get(name)
    return value if value else default


def env_str(name: str) -> str:
    """`process.env.X || ''`."""
    return os.environ.get(name) or ""


def env_bool(name: str) -> bool:
    """`process.env.X === 'true'` (strict string equality, not truthiness)."""
    return os.environ.get(name) == "true"


def env_number(name: str, default: Any = None) -> Any:
    """`Number(process.env.X) || default` — NaN and 0 both fall back to default."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        number = float(raw)
    except ValueError:
        return default
    if number == 0:
        return default
    if number.is_integer():
        return int(number)
    return number


def env_raw(name: str) -> str | None:
    """Raw lookup, no falsy coercion. Needed where TS distinguishes unset from ''."""
    return os.environ.get(name)


# ---------------------------------------------------------------------------
# The knobs MoonTV reads. Names are kept identical so existing deployments
# (docker -e flags, docker-compose environment: blocks) work unchanged.
# ---------------------------------------------------------------------------

STORAGE_TYPE = "NEXT_PUBLIC_STORAGE_TYPE"
PASSWORD = "PASSWORD"  # noqa: S105
USERNAME = "USERNAME"  # noqa: S105

SITE_NAME = "NEXT_PUBLIC_SITE_NAME"
ANNOUNCEMENT = "ANNOUNCEMENT"  # not NEXT_PUBLIC_-prefixed in MoonTV, kept as-is
SEARCH_MAX_PAGE = "NEXT_PUBLIC_SEARCH_MAX_PAGE"
ENABLE_REGISTER = "NEXT_PUBLIC_ENABLE_REGISTER"
DOUBAN_PROXY_TYPE = "NEXT_PUBLIC_DOUBAN_PROXY_TYPE"
DOUBAN_PROXY = "NEXT_PUBLIC_DOUBAN_PROXY"
DOUBAN_IMAGE_PROXY_TYPE = "NEXT_PUBLIC_DOUBAN_IMAGE_PROXY_TYPE"
DOUBAN_IMAGE_PROXY = "NEXT_PUBLIC_DOUBAN_IMAGE_PROXY"
DISABLE_YELLOW_FILTER = "NEXT_PUBLIC_DISABLE_YELLOW_FILTER"
DANMU_API_BASE_URL = "NEXT_PUBLIC_DANMU_API_BASE_URL"
PLAYBACK_SAVE_INTERVAL = "NEXT_PUBLIC_PLAYBACK_SAVE_INTERVAL"
TVBOX_ENABLED = "TVBOX_ENABLED"
M3U8_PROXY_ALLOW_HOSTS = "M3U8_PROXY_ALLOW_HOSTS"

REDIS_URL = "REDIS_URL"
KVROCKS_URL = "KVROCKS_URL"
DB_PATH = "MOONTV_DB_PATH"
