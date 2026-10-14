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

Two things here are MoonTVPlus additions, not ports (PLAN.md change 36):
`open_guarded`, which re-runs the blacklist on every redirect hop — checking
only the URL the caller named is bypassable with a public host that 302s into
the loopback — and `validate_public_url`, the same checks without the whitelist,
which is what `/api/image-proxy` and the manifest's own AES-key/segment hosts
get. The blacklist is also wider than upstream's string list: an IP literal is
additionally decided by `ipaddress`, which is what catches IPv4-mapped IPv6
(`::ffff:127.0.0.1`) and `fd00::/8`. Neither is reachable by a string pattern,
and the mapped form was proved live against the shipped proxies.
"""

from __future__ import annotations

import ipaddress
import os
import re

from urllib.parse import urljoin, urlsplit

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


def guard_allows_private() -> bool:
    """`MOONTVPLUS_URL_GUARD_ALLOW_PRIVATE=1` — turn the blacklist off.

    Upstream has no address check at all, so this is the documented way to get
    that back: posters on a LAN NAS, and the offline test harnesses whose fake
    media server is `127.0.0.1`. Read per call so a harness can set it without
    restarting the process.
    """
    return os.environ.get("MOONTVPLUS_URL_GUARD_ALLOW_PRIVATE", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )

# A WHATWG URL needs a scheme before the first `:`; `urlsplit` would otherwise
# read the whole string as a path and report no error at all.
SCHEME_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:")


def is_blocked_host(hostname: str) -> bool:
    """`isBlockedHost`, plus the two ranges the string list cannot see.

    `urlsplit().hostname` has already dropped the brackets of an IPv6 literal, so
    the string patterns can only ever match the spellings they were written for.
    Two families survive that:

    * **IPv4-mapped IPv6** — `::ffff:127.0.0.1` is loopback to the resolver, and
      `http://[::ffff:127.0.0.1]:3000/logo.svg` reaches this app's own logo
      through `/api/image-proxy` and `/api/m3u8` with the string list in place
      (proved live). Same for `::ffff:169.254.169.254` and `::ffff:10.0.0.1`.
    * **`fd00::/8`** — `fc00::/7` is unique-local space, and the half that is
      actually assigned is `fd00::/8`, which `^fc00:` does not match.

    So an IP literal is decided by `ipaddress` as well as by the patterns. A
    hostname is not: the blacklist is a string check, and a public name whose A
    record points at loopback is the one thing it cannot catch (see the PLAN.md
    note). CGNAT `100.64.0.0/10` is not `is_private` in Python, so its pattern
    stays.
    """
    host = hostname.strip().lower()
    if not host:
        return True
    if DECIMAL_IPV4.match(host):
        return True
    if any(pattern.match(host) for pattern in PRIVATE_HOST_PATTERNS):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_private


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


def _basic_url_check(raw: object) -> tuple[str | None, str]:
    """The shared part of both validators: `(reason, hostname)`, `reason` None
    when the URL passes.

    The scheme test stands in for `new URL(raw)`'s throw, the length cap is
    upstream's, and the private-host blacklist is the SSRF part.
    """
    if not isinstance(raw, str) or not raw:
        return "缺少 URL 参数", ""
    if len(raw) > 4096:
        return "URL 过长", ""

    # `new URL(raw)` throws for anything without a scheme; `urlsplit` does not,
    # so the scheme test is what stands in for the try/catch.
    normalized = URL_WHITESPACE.sub("", raw).strip()
    if not SCHEME_PATTERN.match(normalized):
        return "URL 格式非法", ""

    try:
        parsed = urlsplit(normalized)
    except ValueError:
        return "URL 格式非法", ""

    protocol = parsed.scheme
    if protocol not in ("http", "https"):
        return "仅支持 http/https 协议", ""

    # `urlsplit` reports no host as `None` where `new URL` reports `''`, and
    # `isBlockedHost('')` is true either way.
    hostname = parsed.hostname or ""
    if not guard_allows_private() and is_blocked_host(hostname):
        return "不允许访问该地址", hostname

    return None, hostname


def validate_media_url(raw: object, extra_allow_hosts=None) -> dict:
    """`validateMediaUrl` — `{ok}` or `{ok: false, reason}`."""
    reason, hostname = _basic_url_check(raw)
    if reason is not None:
        return {"ok": False, "reason": reason}

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


def validate_public_url(raw: object) -> dict:
    """The same checks without the host whitelist.

    `/api/image-proxy` has no whitelist upstream, and a redirect target or an
    AES-key host is never the host the caller named, so only the private-network
    blacklist applies to them: every poster host a real card can point at is
    public, and no legitimate media CDN lives on a loopback address.
    """
    reason, _ = _basic_url_check(raw)
    return {"ok": True} if reason is None else {"ok": False, "reason": reason}


class BlockedUrl(Exception):
    """`open_guarded` refused a hop. The reason is the route's `message`."""


REDIRECT_STATUSES = (301, 302, 303, 307, 308)
MAX_REDIRECTS = 5


async def open_guarded(client, url, *, validator=validate_media_url, headers=None, timeout_s=None):
    """GET `url` with redirects followed one hop at a time, guarding every hop.

    `fetch` follows redirects, so validating only the URL the client sent leaves
    a bypass: `?url=https://some-allowed-site/redirect-to?url=http://127.0.0.1/`
    passes the guard and then hands back whatever the cluster answers. The entry
    URL is checked with `validator`; every hop it bounces to is checked against
    the private-host blacklist only, because a hop is never the host the caller
    named.

    Returns the final, un-consumed `httpx.Response` — the caller closes it.
    """
    entry = validator(url)
    if not entry.get("ok"):
        raise BlockedUrl(entry.get("reason") or "不允许访问该地址")

    current = url
    for _ in range(MAX_REDIRECTS + 1):
        response = await client.send(
            client.build_request("GET", current, headers=headers, timeout=timeout_s),
            stream=True,
            follow_redirects=False,
        )
        if response.status_code not in REDIRECT_STATUSES:
            return response

        location = response.headers.get("location")
        await response.aclose()
        if not location:
            raise BlockedUrl("重定向缺少 Location 头")

        current = urljoin(current, location)
        hop = validate_public_url(current)
        if not hop.get("ok"):
            raise BlockedUrl(hop.get("reason") or "不允许访问该地址")

    raise BlockedUrl("重定向次数过多")
