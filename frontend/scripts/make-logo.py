#!/usr/bin/env python3
"""Regenerate every brand asset from one geometry.

MoonTVPlus is a self-hosted video aggregator with an Apple-style UI, so the
mark is deliberately flat and geometric: a crescent moon (the "Moon") cradling
a play triangle (the "TV"), in the palette `tailwind.config.ts` defines --
apple-600 #0071e3 for the moon, apple-400 #2997ff for the play. The same
silhouette, inverted onto a rounded square, is the app tile used by
`favicon.ico` and the PWA icons, because a solid tile stays legible at 16 px
on both a light and a dark browser chrome.

Requires ImageMagick (`convert`). Run from anywhere:

    python3 frontend/scripts/make-logo.py

It writes `frontend/public/logo.svg`, `logo.png`, `favicon.ico`,
`poster-fallback.svg` (the 2:3 "no poster" glyph the record cards use) and
`frontend/public/icons/icon-{192,256,384,512}.png`, which is what
`index.html`, `manifest.json` and the auth gate's `SKIP_PATHS` refer to.
"""

from __future__ import annotations

import pathlib
import struct
import subprocess
import sys

PUBLIC = pathlib.Path(__file__).resolve().parent.parent / "public"

MOON = "#0071e3"  # apple-600
PLAY = "#2997ff"  # apple-400
TILE = "#0071e3"  # apple-600
MARK_ON_TILE = "#ffffff"

# Everything below is in a 512x512 box. The crescent is the disc
# (MOON_C, R) minus the disc (PLAY_C, r); their intersection is the pair of
# tips, so the arcs below are exact rather than approximated.
MOON_C = (260, 256)
R = 195
PLAY_C = (360, 256)
r = 155
TIP_TOP = (380, 102.3)
TIP_BOTTOM = (380, 409.7)
TRIANGLE = ((287, 156), (287, 356), (447, 256))

# The tile is the mark at 82% inside a rounded square, which is Apple's
# ~21% corner radius.
TILE_SCALE = 0.82
TILE_RADIUS = 104

# The poster fallback is the mark at 55% inside a 300x450 (2:3) box, i.e. the
# same aspect ratio a card's poster well has.
POSTER_SCALE = 0.55


def crescent_path() -> str:
    return (
        f"M {TIP_TOP[0]} {TIP_TOP[1]} "
        f"A {R} {R} 0 1 0 {TIP_BOTTOM[0]} {TIP_BOTTOM[1]} "
        f"A {r} {r} 0 1 1 {TIP_TOP[0]} {TIP_TOP[1]} Z"
    )


def triangle_path() -> str:
    a, b, c = TRIANGLE
    return f"M {a[0]} {a[1]} L {b[0]} {b[1]} L {c[0]} {c[1]} Z"


def mark_svg() -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" '
        'width="512" height="512">\n'
        f'  <path d="{crescent_path()}" fill="{MOON}"/>\n'
        f'  <path d="{triangle_path()}" fill="{PLAY}"/>\n'
        "</svg>\n"
    )


def tile_svg() -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" '
        'width="512" height="512">\n'
        f'  <rect x="0" y="0" width="512" height="512" rx="{TILE_RADIUS}" '
        f'fill="{TILE}"/>\n'
        f'  <g transform="translate(256 256) scale({TILE_SCALE}) '
        'translate(-256 -256)">\n'
        f'    <path d="{crescent_path()}" fill="{MARK_ON_TILE}"/>\n'
        f'    <path d="{triangle_path()}" fill="{MARK_ON_TILE}"/>\n'
        "  </g>\n"
        "</svg>\n"
    )


def poster_svg() -> str:
    """The mark centered in a 2:3 box.

    This is `public/poster-fallback.svg`, the image a card shows when neither
    douban nor the source has a poster — the site icon is the "no poster" glyph
    rather than a blank gray well.
    """
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 450" '
        'width="300" height="450">\n'
        f'  <g transform="translate(150 225) scale({POSTER_SCALE}) '
        'translate(-256 -256)">\n'
        f'    <path d="{crescent_path()}" fill="{MOON}"/>\n'
        f'    <path d="{triangle_path()}" fill="{PLAY}"/>\n'
        "  </g>\n"
        "</svg>\n"
    )


def strip_time(png: bytes) -> bytes:
    """Drop the `tIME` chunk ImageMagick stamps with the render timestamp.

    Without it, re-running the generator changes bytes even when the geometry is
    untouched, so a brand tweak shows up as a diff of binary noise and a "the
    assets are generated, not hand-edited" claim cannot be checked by comparing
    the committed files.
    """
    out = bytearray(png[:8])
    i = 8
    while i + 8 <= len(png):
        length = struct.unpack(">I", png[i : i + 4])[0]
        chunk_type = png[i + 4 : i + 8]
        end = i + 8 + length + 4
        if chunk_type != b"tIME":
            out += png[i:end]
        i = end
    return bytes(out)


def render(svg: str, path: pathlib.Path, size: int | None = None) -> None:
    """Render an SVG string to PNG. `size` downscales the 512 master."""
    tmp = path.with_suffix(".tmp.svg")
    tmp.write_text(svg)
    cmd = ["convert", "-background", "none", str(tmp)]
    if size is not None:
        cmd += ["-resize", f"{size}x{size}"]
    cmd += [str(path)]
    subprocess.run(cmd, check=True)
    tmp.unlink()
    path.write_bytes(strip_time(path.read_bytes()))


def build_ico(path: pathlib.Path, sizes: tuple[int, ...]) -> None:
    """Pack PNGs into an ICO container (modern browsers accept PNG entries)."""
    images = []
    for size in sizes:
        png = path.with_suffix(f".{size}.png")
        render(tile_svg(), png, size)
        images.append((size, strip_time(png.read_bytes())))
        png.unlink()

    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries = b""
    body = b""
    for size, data in images:
        entries += struct.pack(
            "<BBBBHHII", size, size, 0, 0, 0, 32, len(data), offset
        )
        body += data
        offset += len(data)
    path.write_bytes(header + entries + body)


def main() -> int:
    if not PUBLIC.is_dir():
        print(f"no public directory at {PUBLIC}", file=sys.stderr)
        return 1

    logo_svg = PUBLIC / "logo.svg"
    logo_svg.write_text(mark_svg())
    poster_svg_path = PUBLIC / "poster-fallback.svg"
    poster_svg_path.write_text(poster_svg())

    render(mark_svg(), PUBLIC / "logo.png", 512)
    build_ico(PUBLIC / "favicon.ico", (16, 32, 48))

    icons = PUBLIC / "icons"
    icons.mkdir(exist_ok=True)
    for size in (192, 256, 384, 512):
        render(tile_svg(), icons / f"icon-{size}x{size}.png", size)

    for path in [
        logo_svg,
        poster_svg_path,
        PUBLIC / "logo.png",
        PUBLIC / "favicon.ico",
    ] + [icons / f"icon-{size}x{size}.png" for size in (192, 256, 384, 512)]:
        print(f"{path.relative_to(PUBLIC.parent)}: {path.stat().st_size} B")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
