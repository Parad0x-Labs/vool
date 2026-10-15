"""Optional skill pictures: one square icon and one wide banner, carried inside the signed package.

Only PNG is accepted, never SVG, GIF, JPEG, WebP or a link to another site. Every picture is parsed
here without any image library and must decode to exactly the pixel rows its header declares. Size
limits are checked from the header before any decoding. Animation, hidden notes, camera data,
colour profiles and every chunk outside a short fixed list are refused, and the allowed ones must
have their exact lengths, so nothing can ride along hidden in the file. WebP stays off until VOOL
can decode its picture data, because a check of the headers alone cannot stop a bad bitstream from
reaching a decoder. The package checksum covers the bytes, and the registry shows a picture only
after a person has approved that exact file for that skill.
"""
from __future__ import annotations

import hashlib
import struct
import zlib

from core.registry.codec import HEX, require

KINDS = ("icon", "banner")

# kind: (max bytes, min width, max width, min height, max height, min ratio, max ratio)
LIMITS = {
    "icon": (256 * 1024, 128, 1024, 128, 1024, 1.0, 1.0),
    "banner": (1024 * 1024, 960, 3000, 240, 1200, 2.5, 4.0),
}
HELP = {
    "icon": "the icon must be a square PNG between 128 and 1024 pixels wide, at most 256 KB",
    "banner": "the banner must be a PNG 960 to 3000 pixels wide and 2.5 to 4 times as wide as tall "
              "(1500 x 500 works), at most 1 MB",
}

_PNG = b"\x89PNG\r\n\x1a\n"
_PNG_ANIMATION = {b"acTL", b"fcTL", b"fdAT"}
_PNG_TEXT = {b"tEXt", b"zTXt", b"iTXt", b"eXIf", b"tIME"}
# colour type: (channels, bit depths, sBIT length, bKGD length, tRNS length: None = up to the palette size, -1 = none)
_PNG_COLOURS = {0: (1, {1, 2, 4, 8, 16}, 1, 2, 2), 2: (3, {8, 16}, 3, 6, 6), 3: (1, {1, 2, 4, 8}, 3, 1, None),
                4: (2, {8, 16}, 2, 2, -1), 6: (4, {8, 16}, 4, 6, -1)}
_ADAM7 = ((0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4), (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2))
_STRIP = "; export it again without metadata"


def reference(kind: str, value: object) -> dict:
    """Shape of a picture named in package metadata: {"path": ..., "sha256": ...}."""
    from core.registry.artifact import path_name

    require(isinstance(value, dict) and set(value) == {"path", "sha256"}, f"invalid {kind}: name its path and sha256")
    path_name(value["path"])
    require(bool(HEX.fullmatch(str(value["sha256"]))), f"invalid {kind} sha256")
    require(value["path"].lower().endswith(".png"), f"{kind} must be a .png file")
    return value


def bind(info: dict, read) -> dict:
    """Author convenience: a picture given as a bare path gets the checksum of that file."""
    result = dict(info)
    for kind in KINDS:
        value = result.get(kind)
        if isinstance(value, str):
            result[kind] = {"path": value, "sha256": hashlib.sha256(read(value)).hexdigest()}
    return result


def describe(kind: str, path: str, data: bytes) -> dict:
    """Check one picture against its kind's limits; returns what a page needs to show it."""
    limit, min_w, max_w, min_h, max_h, min_ratio, max_ratio = LIMITS[kind]
    require(len(data) <= limit, f"{kind} is too large: {HELP[kind]}")
    require(path.lower().endswith(".png"), f"{kind} must be a .png file")

    def fits(width: int, height: int) -> None:
        # Called with the header's size, before any image data is decoded.
        require(min_w <= width <= max_w and min_h <= height <= max_h and min_ratio <= width / height <= max_ratio,
                f"{kind} is {width} x {height} pixels: {HELP[kind]}")

    media, width, height = sniff(data, fits)
    return {"sha256": hashlib.sha256(data).hexdigest(), "type": media, "width": width, "height": height,
            "bytes": len(data)}


def sniff(data: bytes, fits=None) -> tuple[str, int, int]:
    require(data.startswith(_PNG), "pictures must be PNG files")
    return ("image/png", *_png(data, fits))


def raise_refusal(message: str):
    require(False, message)


def _png(data: bytes, fits=None) -> tuple[int, int]:
    offset, chunks, idat, header, palette, ended, seen = len(_PNG), 0, [], None, 0, False, set()
    while offset < len(data):
        require(not ended, "PNG has data after its end" + _STRIP)
        require(offset + 12 <= len(data), "PNG is cut short")
        length, kind = struct.unpack(">I4s", data[offset:offset + 8])
        require(offset + 12 + length <= len(data), "PNG is cut short")
        body = data[offset + 8:offset + 8 + length]
        (crc,) = struct.unpack(">I", data[offset + 8 + length:offset + 12 + length])
        require(zlib.crc32(kind + body) & 0xFFFFFFFF == crc, "PNG is damaged (checksum mismatch)")
        require(kind not in _PNG_ANIMATION, "animated pictures are not accepted")
        require(kind not in _PNG_TEXT, "the picture carries hidden notes (camera details, location or editor notes)" + _STRIP)
        require((chunks == 0) == (kind == b"IHDR"), "PNG must start with its header")
        require(kind == b"IDAT" or kind not in seen, "PNG repeats a section" + _STRIP)
        if kind == b"IHDR":
            require(length == 13, "PNG header is damaged")
            header = struct.unpack(">IIBBBBB", body)
            width, height, depth, colour, compression, filtering, interlace = header
            require(width > 0 and height > 0 and compression == 0 and filtering == 0 and interlace in {0, 1},
                    "PNG header is damaged")
            require(colour in _PNG_COLOURS and depth in _PNG_COLOURS[colour][1], "PNG colour format is not supported")
            if fits:
                fits(width, height)
        elif kind == b"IDAT":
            idat.append(body)  # every other section after the first image data is refused, so the pieces are adjacent
        elif kind == b"IEND":
            require(idat and length == 0, "PNG end is damaged")
            ended = True
        else:
            # Only these small sections, each once, with its exact length, before the image data.
            _, _, sbit, background, transparency = _PNG_COLOURS[header[3]]
            exact = {b"PLTE": length if 0 < length <= 768 and length % 3 == 0 and header[3] in {2, 3, 6} else -1,
                     b"sRGB": 1, b"pHYs": 9, b"sBIT": sbit, b"bKGD": background,
                     b"tRNS": transparency if transparency is not None else (length if 0 < length <= palette else -1)}
            require(kind in exact, "the picture carries a colour profile or other extra data" + _STRIP)
            require(not idat and length == exact[kind], "PNG " + kind.decode("ascii") + " section is damaged" + _STRIP)
            require(kind != b"sRGB" or body[0] <= 3, "PNG sRGB section is damaged")
            require(kind != b"pHYs" or body[8] <= 1, "PNG pHYs section is damaged")
            require(kind not in {b"tRNS", b"bKGD"} or header[3] != 3 or palette, "PNG palette must come first")
            if kind == b"PLTE":
                palette = length // 3
        seen.add(kind)
        chunks += 1
        offset += 12 + length
    require(header is not None and ended, "PNG is incomplete")
    width, height, depth, colour, _, _, interlace = header
    require(colour != 3 or palette, "PNG palette is missing")
    require(colour != 3 or not depth < 8 or palette <= 1 << depth, "PNG palette is damaged")
    bits = _PNG_COLOURS[colour][0] * depth
    passes = [(width, height)] if interlace == 0 else [
        (-(-(width - x0) // dx), -(-(height - y0) // dy)) for x0, y0, dx, dy in _ADAM7 if width > x0 and height > y0]
    rows = [(-(-(pass_width * bits) // 8), pass_height) for pass_width, pass_height in passes]
    expected = sum(pass_height * (1 + row_bytes) for row_bytes, pass_height in rows)
    require(expected <= 32 * 1024 * 1024, "picture is too large to check")
    inflater = zlib.decompressobj()
    try:
        pixels = inflater.decompress(b"".join(idat), expected + 1)
    except zlib.error:
        raise_refusal("PNG image data is damaged")
    require(len(pixels) == expected and inflater.eof and not inflater.unused_data and not inflater.unconsumed_tail,
            "PNG image data does not match its size")
    position = 0
    for row_bytes, pass_height in rows:
        for _ in range(pass_height):
            require(pixels[position] <= 4, "PNG image data is damaged")
            position += 1 + row_bytes
    return width, height
