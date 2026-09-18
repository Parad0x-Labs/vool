#!/usr/bin/env python3
"""Generate the companion app's Expo assets from the OFFICIAL VOOL mark.

The mark itself (bracket-frame with notched bottom) is rasterised by the
product's own pure-stdlib generator, installer/assets/generate_icon.py — this
script composites it onto the companion brand background at the sizes Expo
requires. Deterministic: same inputs, same bytes.

    python mobile/scripts/generate_assets.py
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "installer" / "assets"))

import generate_icon as mark

BG = (11, 13, 16, 255)  # #0b0d10 — the companion background everywhere


def compose(size: int, glyph_fraction: float, out: Path) -> None:
    """Official mark centered on the brand background at `size`."""
    glyph_size = int(size * glyph_fraction)
    glyph = mark._render(glyph_size, (232, 236, 241, 255))  # #e8ecf1 glyph
    canvas = bytearray(size * size * 4)
    for i in range(0, len(canvas), 4):
        canvas[i : i + 4] = bytes(BG)
    off = (size - glyph_size) // 2
    for y in range(glyph_size):
        row = y * glyph_size * 4
        dst = ((off + y) * size + off) * 4
        for x in range(glyph_size):
            a = glyph[row + x * 4 + 3]
            if a:
                canvas[dst + x * 4 : dst + x * 4 + 4] = glyph[row + x * 4 : row + x * 4 + 4]
    out.write_bytes(mark._png(size, canvas))


def main() -> None:
    assets = REPO_ROOT / "mobile" / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    compose(1024, 0.62, assets / "icon.png")            # iOS app icon (opaque)
    compose(1024, 0.62, assets / "adaptive-icon.png")   # Android adaptive foreground
    compose(512, 0.30, assets / "splash-icon.png")      # splash (glyph small)
    print("assets written:", assets)


if __name__ == "__main__":
    main()
