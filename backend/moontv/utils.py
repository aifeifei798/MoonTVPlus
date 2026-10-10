"""Port of `src/lib/utils.ts` — the server-side half.

Only `cleanHtmlTags` is used by both halves; the rest of `utils.ts`
(`processImageUrl`, `getVideoResolutionFromM3u8`, `getRequestTimeout`) reads
`localStorage` and `window.RUNTIME_CONFIG` and lives in the frontend copy
(`frontend/src/lib/utils.ts`), which is byte-identical to the original.

The two helpers here that stand in for a JS builtin are the ones every
upstream-facing module needs:

  `encode_uri_component` — the query strings MoonTV built are
      `api + "?ac=videolist&wd=" + encodeURIComponent(query)`, and the safe set
      is exactly `[A-Za-z0-9]` plus `!'()*-._~` (verified against node: `+`,
      `/`, `:`, `@`, `%` and space are all escaped).
  `js_parse_int` — `parseInt(x, 10)`, a leading-number parse (`"12abc"` is 12,
      `"1e3"` is 1, garbage is NaN) that feeds the `?timeout=` arithmetic.

`strip_none` is the JSON.stringify counterpart: TS objects never carry an
explicit `undefined`, so a field MoonTV would have omitted is a `None` here and
has to be dropped before serializing, or the stored blobs and the API bodies
differ from the ones the TS app wrote.
"""

from __future__ import annotations

import html
import json
import math
import re
import time

# ---------------------------------------------------------------------------
# JSON serialization
# ---------------------------------------------------------------------------

# `JSON.stringify(x)` — compact separators, non-ASCII kept literal.
_DUMP_KW = {"ensure_ascii": False, "separators": (",", ":")}


def strip_none(obj):
    """Drop the keys whose value is `None`, recursively.

    `JSON.stringify` omits a property whose value is `undefined`, and every
    place this codebase builds an object reads optional fields with `.get()`,
    which yields `None`. Without this, `{"class": null}` is sent where MoonTV
    sent nothing, and the client libs' `if (x)` checks behave the same but the
    A/B diff of the wire surface would not be empty.

    A genuine JS `null` is indistinguishable from an absent field once it has
    been through `.get()`, so `None` means "absent" everywhere in this port.
    """
    if isinstance(obj, dict):
        return {k: strip_none(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [strip_none(v) for v in obj]
    return obj


def json_dump(obj) -> str:
    """`JSON.stringify(x)`."""
    return json.dumps(obj, **_DUMP_KW)


# ---------------------------------------------------------------------------
# text handling
# ---------------------------------------------------------------------------


def clean_html_tags(text):
    """`cleanHtmlTags`.

    `he.decode` and Python's `html.unescape` share the HTML5 named-entity table
    for everything these feeds contain (`&amp;`, `&nbsp;`, `&#39;`); they differ
    only on malformed entity text, where `he` is more forgiving.
    """
    if not text:
        return ""

    cleaned = re.sub(r"<[^>]+>", "\n", text)
    cleaned = re.sub(r"\n+", "\n", cleaned)
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    # JS's `$` without the m flag is end-of-string only; Python's is also
    # "before a trailing newline", which would keep one.
    cleaned = re.sub(r"^\n+|\n+\Z", "", cleaned)
    return html.unescape(cleaned.strip())


# ---------------------------------------------------------------------------
# URL/query helpers
# ---------------------------------------------------------------------------

_JS_URI_SAFE = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    "!'()*-._~"
)


def encode_uri_component(value):
    """`encodeURIComponent(x)`."""
    if not isinstance(value, str):
        value = "" if value is None else str(value)
    out = []
    for ch in value:
        if ch in _JS_URI_SAFE:
            out.append(ch)
        else:
            out.extend(f"%{b:02X}" for b in ch.encode("utf-8"))
    return "".join(out)


def js_parse_int(raw, radix: int = 10):
    """`parseInt(x, 10)` — leading digits, `None` for NaN.

    `"12abc"` is 12, `"1e3"` is 1 (an exponent is not part of a radix-10
    integer literal), `"0x10"` is 0, `" 7 "` is 7, and anything with no leading
    digit is NaN, which Python spells `None` here because the callers only use
    it in arithmetic that has to stay NaN-shaped.
    """
    if not isinstance(raw, str):
        raw = "" if raw is None else str(raw)
    match = re.match(r"[ \t\v\f\r\n]*([+-]?[0-9]+)", raw)
    if not match:
        return None
    return int(match.group(1))


def js_number(value):
    """`Number(x)` for the values that reach `Math.min`/comparisons.

    Returns `None` for NaN — `Math.min(NaN, 5)` is NaN and `NaN > 1` is false,
    which is how a garbage `pagecount` ends up fetching no extra pages.
    """
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return 0
        try:
            return float(text)
        except ValueError:
            return None
    return None


def js_string_of(value) -> str:
    """`String(x)` — the digits JS wrote (`5.0` prints `5`), not Python's `str`.

    `None` is JS's `null`/`undefined`, which prints `null`/`undefined`; the
    callers that want an empty string say `String(x ?? '')` and check `None`
    before calling.
    """
    if value is None:
        return "undefined"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (dict, list, tuple)):
        # `String({})` is `[object Object]`, `String([1,2])` is `1,2`.
        if isinstance(value, dict):
            return "[object Object]"
        return ",".join(js_string_of(v) for v in value)
    return str(value)


def js_truthy(value) -> bool:
    """JS `if (x)` — `{}` and `[]` are truthy, which is not Python's `bool`.

    The body checks in the routes (`if (!key || !favorite)`) are written this
    way, and an empty object the client sent has to fall through to the field
    validation rather than to the "missing parameter" error.
    """
    if value is None or value is False:
        return False
    if isinstance(value, (bool, dict, list, tuple)):
        return True
    if isinstance(value, str):
        return value != ""
    return value != 0 and value == value  # 0 and NaN are falsy


def js_min(a, b):
    """`Math.min(a, b)` with JS's coercion, or `None` for NaN."""
    x, y = js_number(a), js_number(b)
    if x is None or y is None:
        return None
    return min(x, y)


# ---------------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------------


def now_ms() -> int:
    """`Date.now()` — the epoch milliseconds every stored record stamps."""
    return math.floor(time.time() * 1000)


# `URLSearchParams`' serialization, which is *not* `encodeURIComponent`: only
# ASCII alphanumerics and `*`, `-`, `.`, `_` survive, a space becomes `+`, and
# everything else is percent-encoded from its UTF-8 bytes. Verified against
# node's `URLSearchParams`, and the douban/tvbox routes build their upstream
# URLs with it, so a Chinese tag has to leave the server the way the browser
# built it.
_URL_SEARCH_SAFE = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789*-._")


def url_search_value(value) -> str:
    """One value as `URLSearchParams` writes it (`NaN` stays the word `NaN`)."""
    if value is None:
        text = "NaN"
    elif isinstance(value, bool):
        text = "true" if value else "false"
    elif isinstance(value, float) and value != value:
        text = "NaN"
    else:
        text = str(value)
    out = []
    for ch in text:
        if ch == " ":
            out.append("+")
        elif ch in _URL_SEARCH_SAFE:
            out.append(ch)
        else:
            out.extend(f"%{b:02X}" for b in ch.encode("utf-8"))
    return "".join(out)


def url_search_params(pairs) -> str:
    """`new URLSearchParams(...)`.toString()` — `&`-joined, `+` for spaces."""
    return "&".join(f"{url_search_value(k)}={url_search_value(v)}" for k, v in pairs)


# ---------------------------------------------------------------------------
# Storage keys
# ---------------------------------------------------------------------------


def split_key(key: str) -> tuple[str, str]:
    """`const [source, videoId] = key.split('+')`.

    Destructuring takes only the first two elements, so `"a+b+c"` yields
    `("a", "b")` — not `("a", "b+c")`. Kept, because the same shape is what
    `generateStorageKey` produces and what the delete paths match on.
    """
    parts = key.split("+")
    return parts[0] if parts else "", parts[1] if len(parts) > 1 else ""
