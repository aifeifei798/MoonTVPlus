"""FastAPI app assembly — `next.config.js` + `src/middleware.ts` for the server side.

Run it from `backend/`:

    uvicorn moontv.main:app --host 0.0.0.0 --port 3000

One origin serves both halves of the app: `/api/*` is the same wire surface
MoonTV had, and every other path is the built frontend. That is what keeps the
client libs unmodified — no CORS preflights, cookies work across the whole app,
and StreamSaver's same-origin `/mitm.html` + `/sw.js` pair still resolves.

The auth gate is an HTTP middleware rather than per-route checks, because
`middleware.ts` was one global function with a matcher; FastAPI's middleware
sees the path and the `auth` cookie before routing, so the 307 → `/login?redirect=…`
behavior and the matcher exclusion list translate directly.
"""

from __future__ import annotations

from fastapi import FastAPI

from .auth import auth_gate_response
from .routes import auth as auth_routes, server_config as server_config_routes
from .routes import search as search_routes
from .routes import admin as admin_routes, config as config_routes
from .routes import data_migration as data_migration_routes
from .routes import douban as douban_routes, image_proxy as image_proxy_routes
from .routes import user_history as user_history_routes, user_state as user_state_routes
from .routes import followings_refresh as followings_refresh_routes
from .routes import detail as detail_routes, search_sub as search_sub_routes
from .routes import tvbox as tvbox_routes, cron as cron_routes
from .routes import m3u8 as m3u8_routes
from .routes import source_test as source_test_routes
from .static import serve_path

# Route modules register themselves by exporting a `ROUTES` list of
# (path, handler, methods). Adding a milestone is adding a line here.
ROUTE_MODULES = (
    auth_routes,
    server_config_routes,
    search_routes,
    config_routes,
    admin_routes,
    data_migration_routes,
    douban_routes,
    image_proxy_routes,
    user_state_routes,
    user_history_routes,
    followings_refresh_routes,
    detail_routes,
    search_sub_routes,
    tvbox_routes,
    cron_routes,
    m3u8_routes,
    source_test_routes,
)


def create_app() -> FastAPI:
    app = FastAPI(
        title="MoonTVPlus",
        # MoonTV's routes are not documented and neither is the catch-all; the
        # surface is defined by the client libs, not by an API page.
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.middleware("http")
    async def auth_gate(request, call_next):
        """`src/middleware.ts`'s `middleware()`."""
        gated = auth_gate_response(request, request.url.path)
        if gated is not None:
            return gated
        return await call_next(request)

    for module in ROUTE_MODULES:
        for path, handler, methods in module.ROUTES:
            app.add_api_route(path, handler, methods=methods, include_in_schema=False)

    # Registered last so the API routes above win; `{full_path:path}` also
    # matches the empty path, so this covers `/` as well as `/search/x`.
    app.add_api_route("/{full_path:path}", serve_path, methods=["GET"], include_in_schema=False)

    return app


app = create_app()


def _run() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=3000)


if __name__ == "__main__":  # pragma: no cover
    _run()
