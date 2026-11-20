#!/usr/bin/env python3
"""Generate the phone app's artwork from the approved VOOL drawing.

The source is the hand-drawn mark the desktop app ships
(installer/assets/approved/vool.icns). Its strokes (the V, the two eyes and the
mouth) are turned into fat, round, soft plush shapes resting on a charcoal
cushion: each stroke gets a round tube profile, cloth-like light and a fine
fabric grain, and casts a soft shadow onto the tile. Pure stdlib,
deterministic: same inputs, same bytes (the grain comes from a fixed seed).

    python mobile/scripts/generate_assets.py

Writes mobile/assets/icon.png, adaptive-icon.png, splash-icon.png and face.png
(the plush face alone with its shadow, clear background, for use inside the app).
Takes a few minutes: everything is computed pixel by pixel in plain Python.
"""
from __future__ import annotations

import binascii
import math
import random
import struct
import zlib
from collections import deque
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE = REPO_ROOT / "installer" / "assets" / "approved" / "vool.icns"

TILE = (30, 31, 36)           # charcoal cushion
PLUSH = (238, 241, 247)       # ice white plush
BG_EDGE = "#040405"           # what app.json uses behind the adaptive icon and splash
LIGHT = (-0.5, -0.62, 0.6)    # from the top left, a little in front
SEED = 5                      # fixed, so the fabric grain is the same on every run
REF = 768.0                   # the sizes below were tuned on a 768 px canvas and scale with it
FAT = 10.5                    # how much fatter than the drawing each stroke becomes
FUZZ = 2.2                    # how lumpy and grainy the plush is


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


def _smooth(a: float, lo: float, hi: float) -> float:
    t = min(1.0, max(0.0, (a - lo) / (hi - lo)))
    return t * t * (3 - 2 * t)


def _gauss_blur(src: list[float], n: int, sigma: float) -> list[float]:
    return src if sigma < 0.7 else _blur(src, n, sigma)


def _noise(rnd: random.Random, n: int, sigma: float) -> list[float]:
    """Smooth random grain with mean 0 and spread 1."""
    raw = [rnd.gauss(0.0, 1.0) for _ in range(n * n)]
    out = _gauss_blur(raw, n, sigma)
    mean = sum(out) / len(out)
    spread = math.sqrt(sum((v - mean) ** 2 for v in out) / len(out)) or 1.0
    return [(v - mean) / spread for v in out]


def _strokes(mask: list[bool], n: int, smallest: int) -> list[list[bool]]:
    """The separate strokes of the drawing (touching pixels, diagonals included)."""
    seen = [False] * (n * n)
    parts = []
    for start in range(n * n):
        if not mask[start] or seen[start]:
            continue
        part = [False] * (n * n)
        queue, count = deque([start]), 0
        seen[start] = True
        while queue:
            i = queue.popleft()
            part[i] = True
            count += 1
            y, x = divmod(i, n)
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    yy, xx = y + dy, x + dx
                    if 0 <= yy < n and 0 <= xx < n:
                        j = yy * n + xx
                        if mask[j] and not seen[j]:
                            seen[j] = True
                            queue.append(j)
        if count >= smallest:
            parts.append(part)
    return parts


def _inside_distance(mask: list[bool], n: int) -> list[float]:
    """How far each pixel inside the shape is from its edge (two-pass chamfer)."""
    big, diag = 1e9, math.sqrt(2.0)
    d = [big if v else 0.0 for v in mask]
    for y in range(n):
        for x in range(n):
            i = y * n + x
            if d[i] == 0.0:
                continue
            best = d[i]
            for dx, dy, w in ((-1, 0, 1.0), (-1, -1, diag), (0, -1, 1.0), (1, -1, diag)):
                xx, yy = x + dx, y + dy
                v = d[yy * n + xx] + w if 0 <= xx < n and 0 <= yy < n else w
                best = min(best, v)
            d[i] = best
    for y in range(n - 1, -1, -1):
        for x in range(n - 1, -1, -1):
            i = y * n + x
            if d[i] == 0.0:
                continue
            best = d[i]
            for dx, dy, w in ((1, 0, 1.0), (1, 1, diag), (0, 1, 1.0), (-1, 1, diag)):
                xx, yy = x + dx, y + dy
                v = d[yy * n + xx] + w if 0 <= xx < n and 0 <= yy < n else w
                best = min(best, v)
            d[i] = best
    return d


def _shifted(src: list[float], n: int, dx: int, dy: int) -> list[float]:
    out = [0.0] * (n * n)
    for y in range(max(0, dy), min(n, n + dy)):
        sy = y - dy
        row, srow = y * n, sy * n
        for x in range(max(0, dx), min(n, n + dx)):
            out[row + x] = src[srow + x - dx]
    return out


def render(mark: tuple[int, list[float]], size: int, scale: float, tile: bool) -> bytes:
    """One picture: the plush face, its shadow, and (when `tile`) the cushion behind it."""
    n, cover = mark
    k = size / REF
    offset = (1 - scale) / 2
    lines = _place(cover, n, size, scale, offset, offset - 0.02 * scale)
    core = [_smooth(v, 0.22, 0.62) for v in lines]
    # Smooth out the pen wobble, then split the drawing into its strokes.
    calm = _gauss_blur(core, size, 4.0 * k)
    strokes = _strokes([v > 0.34 for v in calm], size, int(200 * k * k))
    rnd = random.Random(SEED)
    grain_fine = _noise(rnd, size, 0.5 * k)
    grain_soft = _noise(rnd, size, 0.8 * k)
    lumps = _noise(rnd, size, 2.2 * k)
    height = [0.0] * (size * size)
    inside = [False] * (size * size)
    for stroke in strokes:
        fat = _gauss_blur([1.0 if v else 0.0 for v in stroke], size, FAT * k)
        shape = [v > 0.15 for v in fat]
        dist = _inside_distance(shape, size)
        dist = [d + lumps[i] * 0.7 * FUZZ * k if shape[i] else 0.0 for i, d in enumerate(dist)]
        inner = sorted(d for i, d in enumerate(dist) if shape[i])
        radius = inner[int(0.96 * (len(inner) - 1))] * 0.95 if inner else 1.0
        for i, d in enumerate(dist):
            if shape[i]:
                t = min(1.0, max(0.0, d / radius))
                # a round tube; where two strokes touch, the taller wins and a crease stays
                height[i] = max(height[i], radius * math.sqrt(1 - (1 - t) ** 2))
                inside[i] = True
    height = _gauss_blur(height, size, 1.3 * k)
    height = [h + grain_fine[i] * 0.10 * FUZZ * k if inside[i] else h for i, h in enumerate(height)]
    solid = [1.0 if v else 0.0 for v in inside]
    edge = _gauss_blur(solid, size, 1.0 * k)
    press = _gauss_blur(solid, size, 16 * k)
    cast = _gauss_blur(_shifted(solid, size, round(12 * k), round(20 * k)), size, 11 * k)
    lx, ly, lz = LIGHT
    ll = math.sqrt(lx * lx + ly * ly + lz * lz)
    lx, ly, lz = lx / ll, ly / ll, lz / ll
    out = bytearray(size * size * 4)
    for y in range(size):
        v = y / (size - 1)
        for x in range(size):
            u = x / (size - 1)
            i = y * size + x
            shadow = min(1.0, cast[i])
            if tile:
                light = (1.10 - 0.42 * math.hypot(u - 0.34, v - 0.26)) * (1 + 0.04 * grain_fine[i] + 0.025 * lumps[i])
                sink = (1 - 0.28 * min(1.0, press[i])) * (1 - 0.62 * shadow)
                r, g, b = (c * light * sink for c in TILE)
                alpha = 1.0
            else:
                r = g = b = 0.0
                alpha = 0.55 * shadow  # the shadow alone, so the face still sits on whatever is behind it
            cover_here = _smooth(edge[i] + grain_fine[i] * 0.06 * FUZZ, 0.36, 0.64)
            if cover_here > 0:
                xl, xr = max(0, x - 1), min(size - 1, x + 1)
                yu, yd = max(0, y - 1), min(size - 1, y + 1)
                gx = (height[y * size + xr] - height[y * size + xl]) / (xr - xl)
                gy = (height[yd * size + x] - height[yu * size + x]) / (yd - yu)
                nx, ny, nz = -gx * 1.2, -gy * 1.2, 1.0
                nl = math.sqrt(nx * nx + ny * ny + nz * nz)
                nx, ny, nz = nx / nl, ny / nl, nz / nl
                diffuse = ((nx * lx + ny * ly + nz * lz) * 0.5 + 0.5) ** 1.3  # wrapped light: soft like cloth
                sheen = (1 - nz) ** 2.0                                       # velvet edge at grazing angles
                cloth = (0.34 + 0.80 * diffuse) * (1 + 0.05 * FUZZ * grain_fine[i] + 0.02 * FUZZ * grain_soft[i])
                fr, fg, fb = (min(255.0, c * cloth + c * 0.14 * sheen) for c in PLUSH)
                keep = alpha * (1 - cover_here)
                total = cover_here + keep
                r = (fr * cover_here + r * keep) / total
                g = (fg * cover_here + g * keep) / total
                b = (fb * cover_here + b * keep) / total
                alpha = total
            o = i * 4
            out[o] = max(0, min(255, round(r)))
            out[o + 1] = max(0, min(255, round(g)))
            out[o + 2] = max(0, min(255, round(b)))
            out[o + 3] = max(0, min(255, round(alpha * 255)))
    return _png(size, out)


def main() -> None:
    assets = REPO_ROOT / "mobile" / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    mark = _approved_mark()
    for name, size, scale, tile in (
        ("icon.png", 1024, 1.24, True),            # home-screen icon
        ("adaptive-icon.png", 1024, 0.85, True),   # Android trims the edges, so the face stays inside the middle
        ("splash-icon.png", 512, 1.24, False),     # shown while the app opens
        ("face.png", 384, 1.24, False),            # inside the app: lock, pairing, first chat
    ):
        (assets / name).write_bytes(render(mark, size, scale, tile))
        print("wrote", assets / name, flush=True)


if __name__ == "__main__":
    main()
