#!/usr/bin/env python3
"""Run the app next to a real-RESP fake Redis, in one process.

Usage: backend/.venv/bin/python <skill>/scripts/redis_serve_fake.py

There is no redis-server on this box (`which redis-server redis-cli` is empty and
sudo needs a password), so the redis backend's oracle is fakeredis's `TcpFakeServer`:
a `ThreadingTCPServer` whose handler encodes and decodes real RESP frames, so
`redis.asyncio` talks to it over a real socket and the port's command surface and
wire encoding are exercised. What it cannot prove is a real redis server's own
behavior (timeouts, RDB, Kvrocks' `exh*` extensions), and no claim here says it does.

Env it reads: `FAKE_REDIS_PORT` (the RESP socket), `HTTP_PORT` (the app), and
`NEXT_PUBLIC_STORAGE_TYPE` — it points `REDIS_URL` or `KVROCKS_URL` at the fake
server for whichever of the two is asked for, and leaves the other alone.

Both servers live in one process because the fake Redis is Python, not a system
daemon: uvicorn owns the main loop, the fake server runs in its own thread.
"""

import os
import sys
import threading

import fakeredis
import uvicorn

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from _harness import ROOT  # the sibling bootstrap; also puts <repo>/backend on sys.path

port = int(os.environ.get("FAKE_REDIS_PORT", "8921"))
http_port = int(os.environ.get("HTTP_PORT", "3102"))
kind = os.environ.get("NEXT_PUBLIC_STORAGE_TYPE", "redis")

fake = fakeredis.TcpFakeServer(("127.0.0.1", port), bind_and_activate=True)
threading.Thread(target=fake.serve_forever, daemon=True).start()

url = f"redis://127.0.0.1:{port}"
if kind == "kvrocks":
    os.environ["KVROCKS_URL"] = url
else:
    os.environ["REDIS_URL"] = url

from moontv.main import app  # noqa: E402 — after the env is in place

uvicorn.run(app, host="127.0.0.1", port=http_port, log_level="warning")
