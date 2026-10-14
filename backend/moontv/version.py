"""`src/lib/version.ts` — the version constant only.

Upstream's file also fetches `VERSION.txt` from GitHub and compares, but that
half runs in the browser (`'use client'`), so it already lives in the ported
frontend at `frontend/src/lib/version.ts`. The server only needs the constant:
the data-migration export stamps it into the backup so an import can report
which version wrote it.

MoonTVPlus is its own release line: the root `VERSION.txt` is the single source
of truth and this constant must match it. Upstream MoonTV is still 4.2.5 — the
rewrite is 5.0.0.
"""

CURRENT_VERSION = "5.0.0"
