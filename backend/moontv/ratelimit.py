"""IP sliding-window rate limiting — the brute-force gate on `/api/login`.

The red-team pass (redteam-report.md, round 4) found the login endpoint answering
20 rapid attempts with no backoff: an attacker on the LAN can run a password list
indefensively. This module is deliberately small and in-memory: one deque per
client per endpoint, so the limit survives an attacker cycling usernames but not
a restart — a restart resetting the window is acceptable because the reset costs
them a redeploy, not a retry.

The client key is the TCP peer address (`request.client.host`), not
`X-Forwarded-For`: uvicorn is run directly here (server.sh), and behind no proxy
the header is whatever the caller claims — counting that would let one attacker
spray one IP per spoofed header. If a reverse proxy is ever fronted, pass the
trusted proxy IP through uvicorn's `--proxy-headers` handling rather than
trusting the header here.
"""

from __future__ import annotations

import os
import time
from collections import deque

from fastapi import Request


def _disabled() -> bool:
    """Test-harness escape hatch (MOONTVPLUS_RATE_LIMIT_DISABLED=1, set by tests/conftest.py):
    the suite logs in once per test from one shared client IP and would trip the limit.
    Read per call so tests can flip it. Production leaves it unset."""
    return os.environ.get("MOONTVPLUS_RATE_LIMIT_DISABLED", "").strip() == "1"

# Per client: login gets 10 attempts / 60 s (enough for a fat-fingered human,
# hopeless for a wordlist); register is gated the same way while AllowRegister is
# open, since registration is the other credential-bearing POST.
LOGIN_ATTEMPTS = 10
LOGIN_WINDOW_S = 60.0
REGISTER_ATTEMPTS = 10
REGISTER_WINDOW_S = 60.0

# Cap the table so a spoofed-peer flood cannot grow memory unbounded; entries
# past their window are dropped on eviction.
_MAX_KEYS = 10_000


class SlidingWindowLimiter:
    def __init__(self, attempts: int, window_s: float) -> None:
        self.attempts = attempts
        self.window_s = window_s
        self._hits: dict[str, deque[float]] = {}

    def allow(self, key: str) -> bool:
        if _disabled():
            return True
        now = time.monotonic()
        cutoff = now - self.window_s
        hits = self._hits.get(key)
        if hits is None:
            if len(self._hits) >= _MAX_KEYS:
                self._hits = {k: v for k, v in self._hits.items() if v and v[-1] > cutoff}
            hits = deque()
            self._hits[key] = hits
        while hits and hits[0] <= cutoff:
            hits.popleft()
        if len(hits) >= self.attempts:
            return False
        hits.append(now)
        return True


login_limiter = SlidingWindowLimiter(LOGIN_ATTEMPTS, LOGIN_WINDOW_S)
register_limiter = SlidingWindowLimiter(REGISTER_ATTEMPTS, REGISTER_WINDOW_S)


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"
