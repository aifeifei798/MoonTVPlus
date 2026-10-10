"""MoonTVPlus backend package.

Layout mirrors the MoonTV (Next.js) source it is ported from:

    moontv/env.py              process.env accessors
    moontv/types.py            src/lib/types.ts
    moontv/admin_types.py      src/lib/admin.types.ts
    moontv/config.py           src/lib/config.ts
    moontv/storage/            src/lib/db.ts + src/lib/d1.db.ts (+ redis/kvrocks later)
    moontv/auth.py             src/lib/auth.ts + the HMAC in src/app/api/login + src/middleware.ts
    moontv/request_origin.py   src/lib/request-origin.ts
    moontv/url_guard.py        src/lib/url-guard.ts
    moontv/crypto.py           src/lib/crypto.ts (CryptoJS/OpenSSL compatible)
    moontv/yellow.py           src/lib/yellow.ts
    moontv/routes/             src/app/api/**/route.ts, one module per route, same URL paths
    moontv/static.py           serves the built frontend at / (catch-all, see PLAN.md)
    moontv/main.py             FastAPI app assembly + the src/middleware.ts auth gate
"""
