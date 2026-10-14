"""`src/app/api/m3u8/route.ts` — playlist parsing and the segment proxy.

Served from a local HTTP server so the assertions are deterministic; the
playlists are the real manifests the live probes captured, kept in
`.opencode/skills/moontvplus-verification/fixtures/`.
"""

from __future__ import annotations

import json
import pathlib
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from moontv.m3u8 import apply_url, parse_m3u8

FIXTURES = pathlib.Path(__file__).resolve().parents[2] / ".opencode" / "skills" / "moontvplus-verification" / "fixtures"

MASTER = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-STREAM-INF:BANDWIDTH=1200,RESOLUTION=1280x720
/stream.m3u8
"""

SUB = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:10
#EXTINF:9.0,
seg0.ts
#EXTINF:10.5,
https://cdn.example.invalid/seg1.ts
#EXT-X-ENDLIST
"""

KEYED = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-KEY:METHOD=AES-128,URI="key.bin",IV=0x1234567890abcdef
#EXTINF:5.0,
seg.ts
"""

_SEGMENT = b"\x00" * 188


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        path = self.path
        if path.endswith(".m3u8") and path in self.server.playlists:
            body = self.server.playlists[path].encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.apple.mpegurl")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path.endswith("key.bin"):
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(_SEGMENT)))
            self.end_headers()
            self.wfile.write(_SEGMENT)
        elif path.endswith(".ts"):
            self.send_response(200)
            self.send_header("Content-Type", "video/mp2t")
            self.send_header("Content-Length", str(len(_SEGMENT)))
            self.end_headers()
            self.wfile.write(_SEGMENT)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):  # noqa: A002
        pass


@pytest.fixture()
def playlist_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.playlists = {
        "/master.m3u8": MASTER,
        "/stream.m3u8": SUB,
        "/keyed.m3u8": KEYED,
        "/fixture-a.m3u8": (FIXTURES / "live-source-a.m3u8").read_text(encoding="utf-8"),
    }
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        yield base
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_relative_segments_are_resolved_against_the_playlist_url(playlist_server):
    task = _run(parse_m3u8(f"{playlist_server}/stream.m3u8"))
    assert task["url"] == f"{playlist_server}/stream.m3u8"
    assert task["type"] == "TS"
    assert task["ts_url_list"] == [
        f"{playlist_server}/seg0.ts",
        "https://cdn.example.invalid/seg1.ts",
    ]
    assert task["segment_durations"] == [9.0, 10.5]
    assert task["duration_second"] == pytest.approx(19.5)
    assert task["finish_list"] == [{"title": "seg0.ts", "status": ""}, {"title": "https://cdn.example.invalid/seg1.ts", "status": ""}]


def test_master_playlist_is_followed_one_level(playlist_server):
    task = _run(parse_m3u8(f"{playlist_server}/master.m3u8"))
    assert task["url"] == f"{playlist_server}/stream.m3u8"
    assert len(task["ts_url_list"]) == 2


def test_aes_key_is_fetched_from_the_uri(playlist_server):
    task = _run(parse_m3u8(f"{playlist_server}/keyed.m3u8"))
    assert task["aes"]["method"] == "AES-128"
    assert task["aes"]["uri"] == f"{playlist_server}/key.bin"
    assert task["aes"]["iv"] == "0x1234567890abcdef"
    assert task["aes"]["key"] == _SEGMENT


def test_a_real_manifest_parses(playlist_server):
    task = _run(parse_m3u8(f"{playlist_server}/fixture-a.m3u8"))
    assert task["ts_url_list"], "the captured manifest has segments"
    assert all(url.startswith("http") for url in task["ts_url_list"])
    assert len(task["segment_durations"]) == len(task["ts_url_list"])
    assert task["duration_second"] > 0


def test_not_a_playlist_is_rejected(playlist_server):
    with pytest.raises(Exception) as exc:
        _run(parse_m3u8(f"{playlist_server}/nope.m3u8"))
    assert "m3u8" in str(exc.value).lower()


def test_apply_url_matches_browser_resolution():
    assert apply_url("seg.ts", "https://a.example/playlist/index.m3u8") == "https://a.example/playlist/seg.ts"
    assert apply_url("/seg.ts", "https://a.example/playlist/index.m3u8") == "https://a.example/seg.ts"
    assert apply_url("https://b.example/seg.ts", "https://a.example/playlist/index.m3u8") == "https://b.example/seg.ts"


def _run(coroutine):
    import asyncio

    return asyncio.run(coroutine)
