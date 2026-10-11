"""Port of `src/lib/redis.db.ts` — the plain-Redis storage instance.

Upstream is 14 lines: a `RedisConnectionConfig` of `process.env.REDIS_URL` plus a
log label, and the `Symbol.for('__MOONTV_REDIS_CLIENT__')` slot that makes the
socket process-wide. Everything else is `BaseRedisStorage`.
"""

from __future__ import annotations

from ..env import REDIS_URL, env
from .redis_base import BaseRedisStorage

# `Symbol.for('__MOONTV_REDIS_CLIENT__')`
REDIS_CLIENT_SYMBOL = "__MOONTV_REDIS_CLIENT__"


class RedisStorage(BaseRedisStorage):
    def __init__(self) -> None:
        super().__init__(
            {"url": env(REDIS_URL), "client_name": "Redis"},
            REDIS_CLIENT_SYMBOL,
        )
