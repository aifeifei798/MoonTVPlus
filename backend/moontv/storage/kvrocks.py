"""Port of `src/lib/kvrocks.db.ts` — the Kvrocks storage instance.

Identical to `redis.py` except for the three constructor values, exactly as
upstream's two 14-line files were: Kvrocks implements the standard Redis
commands (`GET`/`SET`/`KEYS`/`MGET`/`LREM`/`LPUSH`/`LTRIM`), so the whole
`BaseRedisStorage` command surface runs unchanged against it — no `exh*`
extended-hash commands anywhere.
"""

from __future__ import annotations

from ..env import KVROCKS_URL, env
from .redis_base import BaseRedisStorage

# `Symbol.for('__MOONTV_KVROCKS_CLIENT__')`
KVROCKS_CLIENT_SYMBOL = "__MOONTV_KVROCKS_CLIENT__"


class KvrocksStorage(BaseRedisStorage):
    def __init__(self) -> None:
        super().__init__(
            {"url": env(KVROCKS_URL), "client_name": "Kvrocks"},
            KVROCKS_CLIENT_SYMBOL,
        )
