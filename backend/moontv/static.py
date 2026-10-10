"""Serves the built frontend at `/` — the catch-all that replaces Next.js's HTML routes.

MoonTV's App Router here is genuinely multi-page: `/search`, `/news`, `/login`
etc. are separate HTML documents, and that is what makes two client behaviors
work — `history.back()` after the login redirect returns to the page you were
on, and every navigation re-parses the bundle. MoonTVPlus serves one HTML
document at every non-API path, which reproduces both behaviors: the document
is the app, and the in-app navigation shim (`router.push` → `location.assign`)
keeps `history` populated the same way.

Two things the document needs from the server, both of which used to come from
`src/app/layout.tsx`:

  * `window.RUNTIME_CONFIG`, injected as an inline script. Same timing as the
    old layout (it runs before the app bundle), so none of the nine client-side
    readers change and there is no boot-fetch race.
  * The built bundle itself, at `/assets/...`. That prefix is added to the
    auth gate's skip list: the login page is rendered by the app, so the bundle
    has to load before anyone is authenticated. It is the Vite stand-in for
    MoonTV's `/_next`, which was in the list for the same reason.

Static files are resolved against the Vite build output first, then the HTML
fallback runs, so `/favicon.ico`, `/manifest.json`, `/mitm.html`, `/sw.js` and
the icon set keep working from the same place they did before.
"""

from __future__ import annotations

import html
import json
import os
from pathlib import Path

from fastapi import Request

from .runtime import runtime_config, runtime_layout

# `frontend/index.html` is the Vite entry, so the file that is served is the
# built one (`frontend/dist/index.html`): Vite rewrites the bundle and
# stylesheet URLs into it and hashes their names, and it leaves these markers
# untouched inside the inline script and the `<title>`. Substituting at serve
# time is what lets one built document serve every site configuration.
SITE_NAME_MARKER = "__SITE_NAME__"
RUNTIME_MARKER = "__RUNTIME_CONFIG__"
RUNTIME_LAYOUT_MARKER = "__RUNTIME_LAYOUT__"

# Media types for what the frontend package actually ships. `mimetypes.guess_type`
# is not used because it answers `text/plain` for `.mjs` and `webmanifest`.
MEDIA_TYPES = {
    ".js": "application/javascript",
    ".mjs": "application/javascript",
    ".css": "text/css",
    ".json": "application/json",
    ".jsonld": "application/ld+json",
    ".map": "application/json",
    ".webmanifest": "application/manifest+json",
    ".txt": "text/plain",
    ".xml": "application/xml",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".avif": "image/avif",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".html": "text/html",
}

# A filename like `index-7I7EttFQ.js` is content-hashed by the build, so it can
# be cached forever; anything else gets a short window. Vite hashes with the
# base64url alphabet, not hex, so the character class has to cover upper case
# and `_`/`-` — with a hex-only pattern every bundle was served as unhashed.
HASHED_NAME = "-[0-9A-Za-z_-]{8,}."


def _frontend_root() -> Path:
    """`frontend/` next to `backend/`, overridable for tests."""
    return Path(os.environ.get("MOONTV_FRONTEND_DIR", Path(__file__).parents[2] / "frontend"))


def _dist_dir() -> Path:
    return Path(os.environ.get("MOONTV_FRONTEND_DIST", _frontend_root() / "dist"))


def _index_html() -> Path:
    """The built document, not the source template."""
    return Path(os.environ.get("MOONTV_INDEX_HTML", _dist_dir() / "index.html"))


def _asset_cache_control(path: Path) -> str:
    import re

    if re.search(HASHED_NAME, path.name):
        return "public, max-age=31536000, immutable"
    return "public, max-age=300"


async def serve_path(request: Request, full_path: str):
    """The catch-all handler: static file if one exists, otherwise the app HTML."""
    from fastapi import Response

    candidate = _resolve_asset(full_path)
    if candidate is not None:
        body = candidate.read_bytes()
        media_type = MEDIA_TYPES.get(candidate.suffix.lower(), "application/octet-stream")
        return Response(
            content=body,
            media_type=media_type,
            headers={"Cache-Control": _asset_cache_control(candidate)},
        )

    if _is_api_path(full_path):
        # An `/api/*` path no route matched. Next.js answered those with its
        # not-found page at status 404, and this has to do the same rather than
        # the 200 the non-API branch gives: a client that is mid-flight on a
        # route this port has not implemented yet would otherwise read the app
        # document as a successful JSON body (`Unexpected token '<'`).
        page = await render_app_html(request)
        page.status_code = 404
        return page

    return await render_app_html(request)


def _is_api_path(full_path: str) -> bool:
    """`api` or `api/...` — the namespace the API routes own."""
    return full_path == "api" or full_path.startswith("api/")


def _resolve_asset(full_path: str) -> Path | None:
    """Path → built asset, or `None`.

    `full_path` comes from the URL, so it is resolved and then checked to still
    be inside `dist/`; without that check `/..%2f..%2fetc/passwd` would read
    arbitrary files.
    """
    dist = _dist_dir()
    if not full_path:
        return None
    candidate = (dist / full_path).resolve()
    if not candidate.is_file():
        return None
    try:
        candidate.relative_to(dist.resolve())
    except ValueError:
        return None
    return candidate


async def render_app_html(request) -> Response:
    """The app document, with the server-side values substituted in."""
    from fastapi import Response

    template = _index_html().read_text(encoding="utf-8")
    config = await runtime_config()
    layout = await runtime_layout()

    injected = (
        template.replace(
            RUNTIME_MARKER,
            # JSON.stringify of the runtime object; the marker sits inside a
            # script, and JSON never emits a raw `</script`, so a plain
            # substitution is safe.
            json.dumps(config, ensure_ascii=False, separators=(",", ":")),
        )
        .replace(
            RUNTIME_LAYOUT_MARKER,
            json.dumps(layout, ensure_ascii=False, separators=(",", ":")),
        )
        .replace(
            SITE_NAME_MARKER,
            # The marker is in `<title>`, so the value has to be escaped the way
            # Next escaped it; `getConfig()` does not constrain site names.
            html.escape(layout["siteName"], quote=False),
        )
    )
    return Response(
        content=injected.encode("utf-8"),
        media_type="text/html",
        # The body embeds RUNTIME_CONFIG and the gate's outcome, so a cached copy
        # must be revalidated rather than reused.
        headers={"Cache-Control": "no-cache"},
    )
