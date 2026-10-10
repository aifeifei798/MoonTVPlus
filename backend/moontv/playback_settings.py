"""Port of `src/lib/playback-settings.ts`.

`upstash` is not ported to Python (see PLAN.md: storage backends are sqlite
default + redis/kvrocks), but the branch is kept so an existing deployment's
saved interval survives a round-trip if the value is ever revisited.
"""

from __future__ import annotations

PLAYBACK_SAVE_DEFAULT_SECONDS = 5
PLAYBACK_SAVE_UPSTASH_SECONDS = 20

PLAYBACK_SAVE_RECOMMEND_MIN = 5
PLAYBACK_SAVE_RECOMMEND_MAX = 60

PLAYBACK_SAVE_ALLOWED_MIN = 1
PLAYBACK_SAVE_ALLOWED_MAX = 3600


def get_default_playback_save_interval(storage_type: str | None) -> int:
    return PLAYBACK_SAVE_UPSTASH_SECONDS if storage_type == "upstash" else PLAYBACK_SAVE_DEFAULT_SECONDS
