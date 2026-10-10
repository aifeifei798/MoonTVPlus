"""`window.RUNTIME_CONFIG` — the object `src/app/layout.tsx` injected server-side.

MoonTV injected this into every page's `<head>` as an inline script because the
App Router renders HTML. MoonTVPlus serves the built frontend from FastAPI, so
the same injection happens in the same place: FastAPI reads `AdminConfig` and
writes the script into the HTML it returns. Same timing as the old layout, and
none of the nine client-side readers change.
"""

from __future__ import annotations

from .config import get_config
from .env import STORAGE_TYPE, env
from .playback_settings import get_default_playback_save_interval

DEFAULT_ANNOUNCEMENT = (
    "本网站仅提供影视信息搜索服务，所有内容均来自第三方网站。"
    "本站不存储任何视频资源，不对任何内容的准确性、合法性、完整性负责。"
)


async def runtime_config() -> dict:
    config = await get_config()
    site = config.get("SiteConfig") or {}
    storage_type = env(STORAGE_TYPE, "sqlite")

    interval = site.get("PlaybackSaveInterval")
    playback_save_interval = (
        interval
        if isinstance(interval, (int, float)) and not isinstance(interval, bool) and interval > 0
        else _env_number("NEXT_PUBLIC_PLAYBACK_SAVE_INTERVAL")
        or get_default_playback_save_interval(storage_type)
    )

    return {
        "STORAGE_TYPE": storage_type,
        "ENABLE_REGISTER": (config.get("UserConfig") or {}).get("AllowRegister"),
        "DOUBAN_PROXY_TYPE": site.get("DoubanProxyType"),
        "DOUBAN_PROXY": site.get("DoubanProxy"),
        "DOUBAN_IMAGE_PROXY_TYPE": site.get("DoubanImageProxyType"),
        "DOUBAN_IMAGE_PROXY": site.get("DoubanImageProxy"),
        "DISABLE_YELLOW_FILTER": site.get("DisableYellowFilter"),
        "DANMU_API_BASE_URL": site.get("DanmakuApiBaseUrl"),
        "PLAYBACK_SAVE_INTERVAL": playback_save_interval,
    }


async def runtime_layout() -> dict:
    """The three values `layout.tsx` computed and passed down as props.

    `siteName` and `announcement` go to `SiteProvider`, and `autoUpdateEnabled`
    gates `SubscriptionAutoUpdate`, which POSTs `/api/admin/subscription` on
    every document load — the gate is what kept that request from happening
    whenever the subscription config did not ask for auto-update. The env
    fallbacks are the ones `layout.tsx` had; they only mattered on the
    `localstorage` branch, which this backend never takes.
    """
    config = await get_config()
    site = config.get("SiteConfig") or {}
    subscription = config.get("SubscriptionConfig") or {}

    return {
        "siteName": site.get("SiteName") or env("NEXT_PUBLIC_SITE_NAME", "MoonTV"),
        "announcement": (
            site["Announcement"]
            if site.get("Announcement") is not None
            else env("ANNOUNCEMENT", DEFAULT_ANNOUNCEMENT)
        ),
        "autoUpdateEnabled": subscription.get("autoUpdate") is True,
    }


def _env_number(name: str):
    import os

    raw = os.environ.get(name)
    try:
        value = float(raw)  # type: ignore
    except (TypeError, ValueError):
        return None
    return int(value) if value and value.is_integer() else (value or None)
