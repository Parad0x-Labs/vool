#!/usr/bin/env python3
"""Generate the VOOL shortcut/app icons with no third-party deps.

The mark is a square bracket-frame with an open, notched bottom (two short feet at the
bottom corners) -- rendered white on a clear alpha background. Pure stdlib: each size is
rasterised into an RGBA buffer, encoded as PNG (zlib), and the PNGs are packed into the
per-platform container: a Vista+ PNG-in-ICO (Windows .ico), a PNG-in-ICNS (macOS .icns),
and a standalone .png (Linux .desktop Icon=). One glyph, three brandings, so no launcher
ever falls back to a generic OS icon:

    installer/assets/vool.ico  / vool_stop.ico   -- Windows shortcuts
    installer/assets/vool.icns / vool_stop.icns  -- macOS .app bundles
    installer/assets/vool.png  / vool_stop.png    -- Linux .desktop entries

To match the original artwork exactly, replace the assets with a real export of the logo
(clear alpha background). This script only provides a faithful built-in default.

    python installer/assets/generate_icon.py
"""
from __future__ import annotations

import binascii
import struct
import zlib
from pathlib import Path

# White glyph, fully opaque; clear (zero-alpha) elsewhere.
FG = (255, 255, 255, 255)
BG = (0, 0, 0, 0)

# Geometry as fractions of the icon size, so every rendered size stays crisp.
MARGIN = 0.16       # gap from the icon edge to the frame
STROKE = 0.11       # bar thickness
FOOT = 0.31         # length of each bottom foot (fraction of the inner span)


def _render(size: int, fg: tuple[int, int, int, int] = FG) -> bytearray:
    """Return a size*size RGBA buffer for the mark at the given pixel size and colour."""
    px = bytearray(size * size * 4)
    for i in range(0, len(px), 4):
        px[i:i + 4] = bytes(BG)

    m = round(size * MARGIN)
    s = max(2, round(size * STROKE))
    inner = size - 2 * m
    foot = round(inner * FOOT)

    lo, hi = m, size - m           # frame outer bounds
    def fill(x0: int, y0: int, x1: int, y1: int) -> None:
        for y in range(max(0, y0), min(size, y1)):
            row = y * size * 4
            for x in range(max(0, x0), min(size, x1)):
                o = row + x * 4
                px[o:o + 4] = bytes(fg)

    fill(lo, lo, hi, lo + s)               # top bar
    fill(lo, lo, lo + s, hi)               # left bar (full height)
    fill(hi - s, lo, hi, hi)               # right bar (full height)
    fill(lo, hi - s, lo + foot, hi)        # bottom-left foot
    fill(hi - foot, hi - s, hi, hi)        # bottom-right foot -- center stays open
    return px


def _png(size: int, rgba: bytearray) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", binascii.crc32(tag + data) & 0xFFFFFFFF))

    raw = bytearray()
    stride = size * 4
    for y in range(size):
        raw.append(0)  # filter type 0 (None)
        raw.extend(rgba[y * stride:(y + 1) * stride])

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)  # 8-bit RGBA
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + chunk(b"IEND", b""))


STOP_FG = (222, 42, 42, 255)  # red, for the "Stop VOOL" shortcut so it can't be mistaken for launch


def build_ico(sizes=(16, 32, 48, 64, 128, 256), fg: tuple[int, int, int, int] = FG) -> bytes:
    pngs = [(sz, _png(sz, _render(sz, fg))) for sz in sizes]
    header = struct.pack("<HHH", 0, 1, len(pngs))  # reserved, type=icon, count
    offset = len(header) + 16 * len(pngs)
    entries, blobs = bytearray(), bytearray()
    for sz, blob in pngs:
        w = 0 if sz >= 256 else sz  # 0 encodes 256 in the ICO dir entry
        entries += struct.pack("<BBBBHHII", w, w, 0, 0, 1, 32, len(blob), offset)
        blobs += blob
        offset += len(blob)
    return header + bytes(entries) + bytes(blobs)


# macOS ICNS: PNG payloads in modern OSType slots (16pt@2x .. 512pt@2x). Finder reads the PNG
# data directly, so no external iconutil/SetFile is needed.
ICNS_TYPES = (
    (b"ic11", 32),    # 16x16@2x
    (b"ic12", 64),    # 32x32@2x
    (b"ic07", 128),   # 128x128
    (b"ic08", 256),   # 256x256
    (b"ic09", 512),   # 512x512
    (b"ic10", 1024),  # 512x512@2x
)


def build_icns(fg: tuple[int, int, int, int] = FG) -> bytes:
    # Preserve the approved native artwork when regenerating launcher assets.
    approved = Path(__file__).with_name("approved") / "vool.icns"
    if fg == FG and approved.is_file():
        return approved.read_bytes()
    elements = bytearray()
    for ostype, sz in ICNS_TYPES:
        png = _png(sz, _render(sz, fg))
        elements += ostype + struct.pack(">I", 8 + len(png)) + png  # element len includes its 8-byte header
    return b"icns" + struct.pack(">I", 8 + len(elements)) + bytes(elements)


def build_png(size: int = 512, fg: tuple[int, int, int, int] = FG) -> bytes:
    return _png(size, _render(size, fg))


def main() -> None:
    for stem, fg in (("vool", FG), ("vool_stop", STOP_FG)):
        for suffix, blob in ((".ico", build_ico(fg=fg)), (".icns", build_icns(fg=fg)), (".png", build_png(fg=fg))):
            out = Path(__file__).with_name(f"{stem}{suffix}")
            out.write_bytes(blob)
            print(f"wrote {out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
