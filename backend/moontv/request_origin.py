"""`src/lib/request-origin.ts` — the origin to use in responses.

MoonTV ran behind reverse proxies (the TVBox/nginx setup is the common one), so
the origin a request was made on is not the origin uvicorn/Next sees: the proxy
terminates TLS and rewrites the Host header. `request-origin.ts` resolved that
from the standard `Forwarded` header first, then `X-Forwarded-*`, then the raw
`host`, and only then the URL Next had already assembled — and it discarded a
`0.0.0.0` host, which is what uvicorn/Next report when the app is bound to a
wildcard address and no proxy told it anything else.

Same order, same fallbacks, same `0.0.0.0` test, because the value ends up in
absolute URLs the client follows (`/api/tvbox/config`, the m3u8 proxy's
`redirect_uri`, the login gate's `Location` header).
"""

from __future__ import annotations

import re

from urllib.parse import urlsplit

# `new RegExp(`${key}=([^;,\\s]+)`, 'i')` — an unanchored search, so a
# `Forwarded` header like `for=...;host=example.com;proto=https` still matches.
FORWARDED_PATTERN = {
    key: re.compile(rf"{key}=([^;,\s]+)", re.IGNORECASE) for key in ("host", "proto")
}

# `/^0\.0\.0\.0(?::\d+)?$/` — a host bound to the wildcard address, which is not
# a host a client can reach.
WILDCARD_HOST = re.compile(r"^0\.0\.0\.0(?::\d+)?$")

# `replace(/^"|"$/g, '')`: one leading or one trailing quote, each independently.
QUOTE_EDGES = re.compile(r'^"|"$')


def get_forwarded_value(forwarded: str | None, key: str) -> str:
    """`getForwardedValue`."""
    if not forwarded:
        return ""

    match = FORWARDED_PATTERN[key].search(forwarded)
    if not match:
        return ""

    return QUOTE_EDGES.sub("", match.group(1)).strip() or ""


def pick_header_value(value: str | None) -> str:
    """`pickHeaderValue`: the first non-empty comma-separated item."""
    if not value:
        return ""

    for item in value.split(","):
        trimmed = item.strip()
        if trimmed:
            return trimmed
    return ""


def get_request_origin(request) -> str:
    """`getRequestOrigin` — `scheme://host` the request was really made on."""
    url = str(request.url)
    headers = request.headers

    forwarded = headers.get("forwarded")
    forwarded_host = get_forwarded_value(forwarded, "host")
    forwarded_proto = get_forwarded_value(forwarded, "proto")
    x_forwarded_host = pick_header_value(headers.get("x-forwarded-host"))
    x_forwarded_proto = pick_header_value(headers.get("x-forwarded-proto"))
    host_header = pick_header_value(headers.get("host"))

    base = urlsplit(url)
    # `url.host` keeps the port (`localhost:3000`), which is `netloc` in Python.
    url_host = base.netloc

    resolved_host = next(
        (
            candidate
            for candidate in (forwarded_host, x_forwarded_host, host_header, url_host)
            if candidate and not WILDCARD_HOST.match(candidate)
        ),
        "",
    )

    if not resolved_host:
        # `url.origin`: scheme + host, port included when non-default.
        return f"{base.scheme}://{base.netloc}"

    resolved_proto = next(
        (
            candidate
            for candidate in (
                forwarded_proto,
                x_forwarded_proto,
                base.scheme,  # `url.protocol.replace(':', '')`
                "http",
            )
            if candidate
        ),
        "http",
    )

    return f"{resolved_proto}://{resolved_host}"
