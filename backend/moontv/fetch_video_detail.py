"""Port of `src/lib/fetchVideoDetail.ts` — detail by `source` + `id`.

The episode-count priority is search over detail, and that order is why the
followings refresh works: several sources' detail endpoint returns no episode
list while their search one does, so refreshing from the detail endpoint alone
would zero out every 追更 entry. The search match is exact on `source`+`id`
rather than fuzzy on the title, which is what keeps a re-branded title from
picking up another source's episode count.

The search failure is swallowed on purpose — upstream's comment says so — so a
dead source falls through to the detail endpoint instead of failing the entry.
"""

from __future__ import annotations

from .config import get_available_api_sites
from .downstream import get_detail_from_api, search_from_api_stream
from .utils import js_string_of, js_truthy


async def fetch_video_detail(
    source,
    id_,
    fallback_title: str = "",
    timeout_ms=None,
) -> dict:
    """`fetchVideoDetail({ source, id, fallbackTitle, timeout })`.

    Returns a `SearchResult` from the stream search or a `getDetailFromApi`
    result, which are the same shape. Raises `无效的API来源` when `source` is
    not one of the enabled sites and `获取视频详情失败` when the detail
    endpoint returned nothing.
    """
    api_sites = await get_available_api_sites()
    api_site = next((s for s in api_sites if s.get("key") == source), None)
    if not api_site:
        raise Exception("无效的API来源")

    # 使用流式搜索获取集数（优先级最高），仅接受 source 一致的精确匹配
    if js_truthy(fallback_title):
        try:
            async for results in search_from_api_stream(
                api_site, fallback_title.strip(), True, timeout_ms
            ):
                for item in results:
                    # `item.source.toString() === source.toString()`: both sides
                    # are already strings (`map_item_to_result` stringifies `id`
                    # and copies `api_site.key`), so this only has to bridge a
                    # numeric `key` in config.json against the string the caller
                    # split out of the storage key.
                    # `item.episodes && item.episodes.length > 0` is `bool(...)`
                    # because `parse_episodes` always yields an array, and for
                    # arrays JS's truthiness and `.length > 0` agree.
                    if (
                        js_string_of(item.get("source")) == js_string_of(source)
                        and js_string_of(item.get("id")) == js_string_of(id_)
                        and bool(item.get("episodes"))
                    ):
                        return item
        except Exception:  # 流式搜索失败时忽略，继续走详情兜底
            pass

    # 搜索未精确命中或未提供 fallbackTitle，则调用详情接口兜底
    detail = await get_detail_from_api(api_site, id_)
    if not js_truthy(detail):
        raise Exception("获取视频详情失败")

    return detail
