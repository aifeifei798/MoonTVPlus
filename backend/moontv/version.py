"""`src/lib/version.ts` — the version constant only.

Upstream's file also fetches `VERSION.txt` from GitHub and compares, but that
half runs in the browser (`'use client'`), so it already lives in the ported
frontend at `frontend/src/lib/version.ts`. The server only needs the constant:
the data-migration export stamps it into the backup so an import can report
which version wrote it.
"""

CURRENT_VERSION = "4.2.5"
