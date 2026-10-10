"""`src/lib/url-guard.ts` — the SSRF guard in front of `/api/m3u8`.

`/api/m3u8` is a pass-through proxy for a user-supplied URL, so without this it
is an open SSRF channel: `?url=http://169.254.169.254/...` hands back cloud
metadata, and `?url=http://127.0.0.1:6379/` reaches the Kvrocks behind the app.
The three layers are the protocol whitelist, the private-network host blacklist,
and the optional `M3U8_PROXY_ALLOW_HOSTS` host whitelist (`*.example.com`
wildcards, leading dot kept so `evil-example.com` does not match).

The result is a plain dict, `{ok: true}` or `{ok: false, reason: ...}`, because
the reason string is what the route puts in the response body — the strings are
part of the wire surface, not decoration.

Where Python's URL parser and WHATWG's disagree, the verdicts were checked
against each other and Python is never the more permissive one: `http://evil.com\\@127.0.0.1`
is `evil.com` to `new URL` and `127.0.0.1` to `urlsplit` (blocked here, and it
is the host httpx would connect to), and `http://127.0.0.1%2devil.com` stays
literal in Python rather than normalizing to `127.0.0.1-evil.com`.
"""

from __future__ import annotations

import re

from urllib.parse import urlsplit

from .env import M3U8_PROXY_ALLOW_HOSTS, env_str

# loopback / 私有 / 链路本地 / 保留网段, verbatim from the TS list
PRIVATE_HOST_PATTERNS = [
    re.compile(r"^localhost$", re.IGNORECASE),
    re.compile(r"^0\.0\.0\.0$"),
    re.compile(r"^127\."),
    re.compile(r"^10\."),
    re.compile(r"^192\.168\."),
    re.compile(r"^169\.254\."),  # 含 169.254.169.254 云元数据地址
    re.compile(r"^172\.(1[6-9]|2[0-9]|3[01])\."),
    re.compile(r"^100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\."),  # 运营商级 NAT (100.64/10)
    re.compile(r"^::1$"),
    re.compile(r"^fc00:", re.IGNORECASE),
    re.compile(r"^fe80:", re.IGNORECASE),
    re.compile(r"^\[::1\]$"),
]

# 十进制 / 十六进制混淆写法（如 2130706433 表示 127.0.0.1）
DECIMAL_IPV4 = re.compile(r"^[0-9]{1,10}$")

# WHATWG's URL parser drops these before it parses, which is why
# `http://127.0.0.1\n.evil.com` is one host to `new URL`, not two.
URL_WHITESPACE = re.compile(r"[\t\n\r]|\u000c")

# A WHATWG URL needs a scheme before the first `:`; `urlsplit` would otherwise
# read the whole string as a path and report no error at all.
SCHEME_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:")


def is_blocked_host(hostname: str) -> bool:
    """`isBlockedHost`."""
    host = hostname.strip().lower()
    if not host:
        return True
    if DECIMAL_IPV4.match(host):
        return True
    return any(pattern.match(host) for pattern in PRIVATE_HOST_PATTERNS)


def read_allow_hosts() -> list[str]:
    """`readAllowHosts`."""
    raw = env_str(M3U8_PROXY_ALLOW_HOSTS)
    return [item.strip().lower() for item in raw.split(",") if item.strip()]


def matches_allow_list(hostname: str, allow_hosts: list[str]) -> bool:
    """`matchesAllowList`."""
    host = hostname.lower()
    for pattern in allow_hosts:
        if pattern.startswith("*."):
            # 保留前导点，避免 evil-example.com 误判
            if host.endswith(pattern[1:]):
                return True
        elif host == pattern:
            return True
    return False


def validate_media_url(raw: object, extra_allow_hosts=None) -> dict:
    """`validateMediaUrl` — `{ok}` or `{ok: false, reason}`."""
    if not isinstance(raw, str) or not raw:
        return {"ok": False, "reason": "缺少 URL 参数"}
    if len(raw) > 4096:
        return {"ok": False, "reason": "URL 过长"}

    # `new URL(raw)` throws for anything without a scheme; `urlsplit` does not,
    # so the scheme test is what stands in for the try/catch.
    normalized = URL_WHITESPACE.sub("", raw).strip()
    if not SCHEME_PATTERN.match(normalized):
        return {"ok": False, "reason": "URL 格式非法"}

    try:
        parsed = urlsplit(normalized)
    except ValueError:
        return {"ok": False, "reason": "URL 格式非法"}

    protocol = parsed.scheme
    if protocol not in ("http", "https"):
        return {"ok": False, "reason": "仅支持 http/https 协议"}

    # `urlsplit` reports no host as `None` where `new URL` reports `''`, and
    # `isBlockedHost('')` is true either way.
    hostname = parsed.hostname or ""
    if is_blocked_host(hostname):
        return {"ok": False, "reason": "不允许访问该地址"}

    allow_hosts = read_allow_hosts()
    extra = [
        item.strip().lower()
        for item in (extra_allow_hosts or [])
        if isinstance(item, str) and item.strip()
    ]
    effective_allow = allow_hosts + extra

    if effective_allow and not matches_allow_list(hostname, effective_allow):
        return {"ok": False, "reason": "该域名不在允许列表中"}

    return {"ok": True}
