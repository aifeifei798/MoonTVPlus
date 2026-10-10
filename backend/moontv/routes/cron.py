"""`src/app/api/cron/route.ts` — the scheduled refresh of play records and favorites.

The handler answers immediately and refreshes in the background: upstream
called `refreshRecordAndFavorites()` without `await`, so the port fires it off
with `asyncio.create_task`. The 500 branch is unreachable for the same reason
it was upstream (nothing between the logs and the response can throw), and is
kept for parity.

The refresh itself is sequential — one user after another, one record after
another — so the `detailCache` is a plain dict rather than a task map. Only
successes stay cached, like upstream's `successPromise` replace: a key whose
detail fetch failed is retried for the next record that needs it, and each
retry logs its own `获取视频详情失败`.
"""

from __future__ import annotations

import asyncio
import datetime

from fastapi import Request

from ..env import STORAGE_TYPE, USERNAME, env
from ..fetch_video_detail import fetch_video_detail
from ..routes import json_response
from ..storage.db import Db
from ..utils import split_key

# `timeout: 30000` — the cron refresh passes an explicit 30 s, unlike the
# search routes, which leave `fetchWithTimeout`'s default in place.
DETAIL_TIMEOUT_MS = 30000

# `detailCache` 上限：一次刷新里最多留 500 条详情，FIFO 淘汰最早的 key。
# 这个 dict 本来只在一次 `_refresh_record_and_favorites` 内存活，上限只是兜底。
DETAIL_CACHE_MAX = 500


def _cache_put(cache: dict, key: str, value) -> None:
    """FIFO 淘汰至上限内的 put（同一 key 重复 put 只刷新值，不动顺序）。"""
    if key not in cache:
        while len(cache) >= DETAIL_CACHE_MAX:
            cache.pop(next(iter(cache)))
    cache[key] = value


def _iso_now() -> str:
    """`new Date().toISOString()` — UTC, milliseconds, trailing `Z`."""
    now = datetime.datetime.now(datetime.timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


async def cron(request: Request):
    print(str(request.url))  # upstream's `console.log(request.url)`
    try:
        print(f"Cron job triggered: {_iso_now()}")

        # Not awaited upstream either — the response goes out while the refresh
        # keeps running behind it.
        asyncio.create_task(_refresh_record_and_favorites())

        return json_response(
            {
                "success": True,
                "message": "Cron job executed successfully",
                "timestamp": _iso_now(),
            }
        )
    except Exception as error:  # noqa: BLE001 — unreachable, kept for parity
        print("Cron job failed:", error)
        return json_response(
            {
                "success": False,
                "message": "Cron job failed",
                "error": str(error) if isinstance(error, Exception) else "Unknown error",
                "timestamp": _iso_now(),
            },
            500,
        )


async def _refresh_record_and_favorites() -> None:
    if env(STORAGE_TYPE, "sqlite") == "localstorage":
        print("跳过刷新：当前使用 localstorage 存储模式")
        return

    try:
        users = await Db.get_all_users()
        username_env = env(USERNAME)
        if username_env and username_env not in users:
            users.append(username_env)

        detail_cache: dict[str, dict] = {}

        async def get_detail(source: str, id_: str, fallback_title: str):
            """`getDetail` — cached successes, retried failures."""
            key = f"{source}+{id_}"
            if key in detail_cache:
                return detail_cache[key]
            try:
                detail = await fetch_video_detail(
                    source, id_, fallback_title.strip(), DETAIL_TIMEOUT_MS
                )
            except Exception as err:  # noqa: BLE001
                print(f"获取视频详情失败 ({source}+{id_}):", err)
                return None
            _cache_put(detail_cache, key, detail)
            return detail

        for user in users:
            print(f"开始处理用户: {user}")

            # 播放记录
            try:
                play_records = await Db.get_all_play_records(user)
                total_records = len(play_records)
                processed_records = 0

                for key, record in play_records.items():
                    try:
                        source, id_ = split_key(key)
                        if not source or not id_:
                            print(f"跳过无效的播放记录键: {key}")
                            continue

                        detail = await get_detail(source, id_, record.get("title") or "")
                        if not detail:
                            print(f"跳过无法获取详情的播放记录: {key}")
                            continue

                        episode_count = len(detail.get("episodes") or [])
                        if episode_count > 0 and episode_count != record.get("total_episodes"):
                            await Db.set_play_record(
                                user,
                                f"{source}+{id_}",
                                {
                                    "title": detail.get("title") or record.get("title"),
                                    "source_name": record.get("source_name"),
                                    "cover": detail.get("poster") or record.get("cover"),
                                    "index": record.get("index"),
                                    "total_episodes": episode_count,
                                    "play_time": record.get("play_time"),
                                    "year": detail.get("year") or record.get("year"),
                                    "total_time": record.get("total_time"),
                                    "save_time": record.get("save_time"),
                                    "search_title": record.get("search_title"),
                                },
                            )
                            print(
                                f"更新播放记录: {record.get('title')} "
                                f"({record.get('total_episodes')} -> {episode_count})"
                            )

                        processed_records += 1
                    except Exception as err:  # noqa: BLE001
                        print(f"处理播放记录失败 ({key}):", err)
                        # 继续处理下一个记录

                print(f"播放记录处理完成: {processed_records}/{total_records}")
            except Exception as err:  # noqa: BLE001
                print(f"获取用户播放记录失败 ({user}):", err)

            # 收藏
            try:
                favorites = await Db.get_all_favorites(user)
                total_favorites = len(favorites)
                processed_favorites = 0

                for key, fav in favorites.items():
                    try:
                        source, id_ = split_key(key)
                        if not source or not id_:
                            print(f"跳过无效的收藏键: {key}")
                            continue

                        fav_detail = await get_detail(source, id_, fav.get("title") or "")
                        if not fav_detail:
                            print(f"跳过无法获取详情的收藏: {key}")
                            continue

                        fav_episode_count = len(fav_detail.get("episodes") or [])
                        if fav_episode_count > 0 and fav_episode_count != fav.get("total_episodes"):
                            await Db.set_favorite(
                                user,
                                f"{source}+{id_}",
                                {
                                    "title": fav_detail.get("title") or fav.get("title"),
                                    "source_name": fav.get("source_name"),
                                    "cover": fav_detail.get("poster") or fav.get("cover"),
                                    "year": fav_detail.get("year") or fav.get("year"),
                                    "total_episodes": fav_episode_count,
                                    "save_time": fav.get("save_time"),
                                    "search_title": fav.get("search_title"),
                                },
                            )
                            print(
                                f"更新收藏: {fav.get('title')} "
                                f"({fav.get('total_episodes')} -> {fav_episode_count})"
                            )

                        processed_favorites += 1
                    except Exception as err:  # noqa: BLE001
                        print(f"处理收藏失败 ({key}):", err)
                        # 继续处理下一个收藏

                print(f"收藏处理完成: {processed_favorites}/{total_favorites}")
            except Exception as err:  # noqa: BLE001
                print(f"获取用户收藏失败 ({user}):", err)

        print("刷新播放记录/收藏任务完成")
    except Exception as err:  # noqa: BLE001
        print("刷新播放记录/收藏任务启动失败", err)


ROUTES = [("/api/cron", cron, ["GET"])]
