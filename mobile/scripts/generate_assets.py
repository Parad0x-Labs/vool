#!/usr/bin/env python3
"""Generate the phone app's artwork from the approved VOOL drawing.

The source is the hand-drawn mark the desktop app ships
(installer/assets/approved/vool.icns). Its lines are kept exactly as drawn; this
script only enlarges them inside the tile and adds a soft ice-white glow, a dark
spotlight background and a light edge on top. Pure stdlib, deterministic: same
inputs, same bytes.

    python mobile/scripts/generate_assets.py

Writes mobile/assets/icon.png, adaptive-icon.png, splash-icon.png and face.png
(the drawing alone with its glow, clear background, for use inside the app).
"""
from __future__ import annotations

import binascii
import math
import struct
import zlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE = REPO_ROOT / "installer" / "assets" / "approved" / "vool.icns"

GLOW = (207, 224, 255)        # ice white halo
CORE = (255, 255, 255)        # the lines themselves
BG_STOPS = ((0.0, (29, 32, 38)), (0.6, (14, 15, 19)), (1.0, (4, 4, 5)))
BG_EDGE = "#040405"           # what app.json uses behind the adaptive icon and splash
SMALL = 256                   # the glow is soft, so it is blurred at about this size and scaled up


def _unfilter(data: bytes, width: int, height: int, bpp: int) -> bytearray:
    stride = width * bpp
    out = bytearray(height * stride)
    pos = 0
    for y in range(height):
        kind = data[pos]
        pos += 1
        row = bytearray(data[pos:pos + stride])
        pos += stride
        prev = out[(y - 1) * stride:y * stride] if y else bytes(stride)
        if kind == 1:
            for i in range(bpp, stride):
                row[i] = (row[i] + row[i - bpp]) & 255
        elif kind == 2:
            for i in range(stride):
                row[i] = (row[i] + prev[i]) & 255
        elif kind == 3:
            for i in range(stride):
                left = row[i - bpp] if i >= bpp else 0
                row[i] = (row[i] + ((left + prev[i]) >> 1)) & 255
        elif kind == 4:
            for i in range(stride):
                a = row[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                row[i] = (row[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        elif kind != 0:
            raise ValueError(f"unknown PNG filter {kind}")
        out[y * stride:(y + 1) * stride] = row
    return out


def _decode_png(blob: bytes) -> tuple[int, bytearray]:
    """Return (size, RGBA bytes) of a square 8-bit RGBA PNG."""
    if blob[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    pos, idat, header = 8, bytearray(), None
    while pos < len(blob):
        length, tag = struct.unpack(">I4s", blob[pos:pos + 8])
        body = blob[pos + 8:pos + 8 + length]
        if tag == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif tag == b"IDAT":
            idat += body
        pos += 12 + length
    if header is None:
        raise ValueError("PNG has no header")
    width, height, depth, colour, _, _, interlace = header
    if width != height or depth != 8 or colour != 6 or interlace:
        raise ValueError("the approved mark must be a square 8-bit RGBA PNG")
    return width, _unfilter(zlib.decompress(bytes(idat)), width, height, 4)


def _approved_mark() -> tuple[int, list[float]]:
    """The largest picture in the approved .icns, as line coverage 0..1 per pixel."""
    data = SOURCE.read_bytes()
    if data[:4] != b"icns":
        raise ValueError(f"{SOURCE} is not an .icns file")
    best, pos = b"", 8
    while pos < len(data):
        length = struct.unpack(">I", data[pos + 4:pos + 8])[0]
        body = data[pos + 8:pos + length]
        if body[:4] == b"\x89PNG" and len(body) > len(best):
            best = body
        pos += length
    size, rgba = _decode_png(best)
    # White lines on a black tile: brightness times opacity is how much line a pixel holds.
    cover = [(rgba[i] + rgba[i + 1] + rgba[i + 2]) * rgba[i + 3] / (765.0 * 255.0) for i in range(0, len(rgba), 4)]
    # The tile behind the drawing is a very dark grey, not black: take that floor away.
    cover = [max(0.0, (value - 0.06) / 0.94) for value in cover]
    # Only the drawing in the middle counts; the tile's own rim is left out.
    lo, hi = int(size * 0.2), int(size * 0.8)
    for y in range(size):
        for x in range(size):
            if not (lo <= x < hi and lo <= y < hi):
                cover[y * size + x] = 0.0
    return size, cover


def _sample(src: list[float], n: int, x: float, y: float) -> float:
    if x < 0 or y < 0 or x > n - 1 or y > n - 1:
        return 0.0
    x0, y0 = int(x), int(y)
    x1, y1 = min(x0 + 1, n - 1), min(y0 + 1, n - 1)
    fx, fy = x - x0, y - y0
    top = src[y0 * n + x0] * (1 - fx) + src[y0 * n + x1] * fx
    bottom = src[y1 * n + x0] * (1 - fx) + src[y1 * n + x1] * fx
    return top * (1 - fy) + bottom * fy


def _place(src: list[float], n: int, size: int, scale: float, dx: float, dy: float) -> list[float]:
    """The drawing enlarged by `scale` and moved, on a size*size canvas (dx, dy as fractions)."""
    out = [0.0] * (size * size)
    ratio = n / (size * scale)
    for y in range(size):
        sy = (y - dy * size) * ratio
        if sy < 0 or sy > n - 1:
            continue
        row = y * size
        for x in range(size):
            out[row + x] = _sample(src, n, (x - dx * size) * ratio, sy)
    return out


def _shrink(src: list[float], size: int, small: int) -> list[float]:
    step = size // small
    area = float(step * step)
    out = [0.0] * (small * small)
    for y in range(small):
        for x in range(small):
            total = 0.0
            for j in range(step):
                base = (y * step + j) * size + x * step
                total += sum(src[base:base + step])
            out[y * small + x] = total / area
    return out


def _box(src: list[float], n: int, radius: int) -> list[float]:
    """One horizontal then vertical box blur."""
    width = 2 * radius + 1
    tmp = [0.0] * (n * n)
    for y in range(n):
        row = src[y * n:(y + 1) * n]
        total = sum(row[:radius + 1])
        for x in range(n):
            tmp[y * n + x] = total / width
            if x + radius + 1 < n:
                total += row[x + radius + 1]
            if x - radius >= 0:
                total -= row[x - radius]
    out = [0.0] * (n * n)
    for x in range(n):
        col = tmp[x::n]
        total = sum(col[:radius + 1])
        for y in range(n):
            out[y * n + x] = total / width
            if y + radius + 1 < n:
                total += col[y + radius + 1]
            if y - radius >= 0:
                total -= col[y - radius]
    return out


def _blur(src: list[float], n: int, sigma: float) -> list[float]:
    """Three box blurs stand in for a soft round blur of the given width."""
    radius = max(1, round((math.sqrt(4 * sigma * sigma + 1) - 1) / 2))
    out = src
    for _ in range(3):
        out = _box(out, n, radius)
    return out


def _png(size: int, rgba: bytearray) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", binascii.crc32(tag + data) & 0xFFFFFFFF)

    raw = bytearray()
    stride = size * 4
    for y in range(size):
        raw.append(0)
        raw.extend(rgba[y * stride:(y + 1) * stride])
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


def _background(t: float) -> tuple[float, float, float]:
    for (a, ca), (b, cb) in zip(BG_STOPS, BG_STOPS[1:]):
        if t <= b:
            f = (t - a) / (b - a)
            return ca[0] + (cb[0] - ca[0]) * f, ca[1] + (cb[1] - ca[1]) * f, ca[2] + (cb[2] - ca[2]) * f
    return tuple(float(v) for v in BG_STOPS[-1][1])


def render(mark: tuple[int, list[float]], size: int, scale: float, tile: bool, gloss: bool) -> bytes:
    """One picture: the drawing, its glow, and (when `tile`) the dark spotlight tile behind it."""
    n, cover = mark
    offset = (1 - scale) / 2
    # The drawing sits a touch high in the approved tile; nudge it so it reads centred.
    lines = _place(cover, n, size, scale, offset, offset - 0.02 * scale)
    # Blur on a smaller copy: an exact divisor of the size, so the glow sits right under the lines.
    n_small = size // max(1, size // SMALL)
    small = _shrink(lines, size, n_small)
    wide = _blur(small, n_small, 0.035 * n_small)
    near = _blur(small, n_small, 0.015 * n_small)
    up = (n_small - 1) / (size - 1)
    out = bytearray(size * size * 4)
    for y in range(size):
        v = y / (size - 1)
        for x in range(size):
            u = x / (size - 1)
            g1 = min(1.0, _sample(wide, n_small, x * up, y * up) * 1.15) * 0.35
            g2 = min(1.0, _sample(near, n_small, x * up, y * up) * 1.1) * 0.7
            core = min(1.0, lines[y * size + x])
            if tile:
                r, g, b = _background(min(1.0, math.hypot(u - 0.5, v - 0.52) / 0.7))
                alpha = 1.0
            else:
                r = g = b = 0.0
                alpha = 0.0
            for amount, colour in ((g1, GLOW), (g2, CORE), (core, CORE)):
                if amount <= 0:
                    continue
                keep = alpha * (1 - amount)
                total = amount + keep
                r = (colour[0] * amount + r * keep) / total
                g = (colour[1] * amount + g * keep) / total
                b = (colour[2] * amount + b * keep) / total
                alpha = total
            if gloss:
                edge = 0.34 + 0.36 * u * (1 - u)
                if v < edge:
                    shine = 0.13 * (1 - v / 0.43)
                    r, g, b = r + (255 - r) * shine, g + (255 - g) * shine, b + (255 - b) * shine
            o = (y * size + x) * 4
            out[o] = round(r)
            out[o + 1] = round(g)
            out[o + 2] = round(b)
            out[o + 3] = round(alpha * 255)
    return _png(size, out)


def main() -> None:
    assets = REPO_ROOT / "mobile" / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    mark = _approved_mark()
    for name, size, scale, tile, gloss in (
        ("icon.png", 1024, 1.43, True, True),            # home-screen icon
        ("adaptive-icon.png", 1024, 0.98, True, False),  # Android trims the edges, so the drawing stays inside the middle
        ("splash-icon.png", 512, 1.43, False, False),    # shown while the app opens
        ("face.png", 384, 1.43, False, False),           # inside the app: lock, pairing, first chat
    ):
        (assets / name).write_bytes(render(mark, size, scale, tile, gloss))
        print("wrote", assets / name)


if __name__ == "__main__":
    main()
