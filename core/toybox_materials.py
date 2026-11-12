"""Application-owned skin materials: authored pixel tiles and vector ornaments.

Contributor: sls_0x. Every image here is a static SVG built from the shapes in this
file and colours from an already validated manifest palette. Manifests never supply
art, CSS, selectors, paths or URLs. The SVGs contain no text, scripts, links or
external references; the only URL-like string is the required SVG namespace, and the
only `url(#…)` is a pattern defined inside the same thumbnail.
"""

from __future__ import annotations

import math

from core.toybox import contrast

SVG_NS = "http://www.w3.org/2000/svg"
TEXT_FLOOR = 4.5
# Blends are checked on rounded hex colours; the margin covers the browser's exact alpha
# compositing so rendered text never lands below TEXT_FLOOR.
MARGIN = 0.05


# ---- colour ------------------------------------------------------------------------------


def _rgb(colour):
    return tuple(int(colour[i : i + 2], 16) for i in (1, 3, 5))


def mix(a, b, weight):
    """`weight` of colour b blended over colour a, as #rrggbb."""
    return "#" + "".join(
        f"{round(x + (y - x) * weight):02x}" for x, y in zip(_rgb(a), _rgb(b), strict=True)
    )


def is_dark(palette):
    return contrast(palette["background"], "#000000") < contrast(palette["ink"], "#000000")


def safe_weight(surface, tint, weight, inks=(), floor=TEXT_FLOOR + MARGIN):
    """Largest blend weight <= `weight` that keeps every ink at `floor` contrast on the blend.

    Ornaments that sit under text use this, so a material never lowers text contrast below
    the floor the manifest was validated against, whatever palette a creator chose.
    """

    def readable(w):
        surface_mix = mix(surface, tint, w)
        return all(contrast(surface_mix, ink) >= floor for ink in inks)

    if readable(weight):
        return weight
    low, high = 0.0, weight
    for _ in range(24):
        middle = (low + high) / 2
        low, high = (middle, high) if readable(middle) else (low, middle)
    return low


def _op(value):
    # rounded down, so an emitted opacity never exceeds the contrast-safe weight
    value = math.floor(max(0.0, min(1.0, value)) * 1000) / 1000
    return f"{value:.3f}".rstrip("0").rstrip(".") or "0"


# ---- encoding ----------------------------------------------------------------------------


def svg(width, height, body, view=None, crisp=False):
    box = view or f"0 0 {width} {height}"
    rendering = " shape-rendering='crispEdges'" if crisp else ""
    # attribute values here never contain quotes, so single quotes keep the URI compact
    body = body.replace('"', "'")
    return f"<svg xmlns='{SVG_NS}' width='{width}' height='{height}' viewBox='{box}'{rendering}>{body}</svg>"


def data_uri(markup):
    """A data: URI for application-built SVG. Attributes use single quotes, so only the
    characters that matter in a URL, a CSS string or an HTML style element are escaped."""
    escaped = markup.replace("%", "%25").replace("#", "%23").replace("<", "%3C").replace(">", "%3E")
    if any(char in escaped for char in '"\\\n\r'):
        raise ValueError("artwork markup must not contain double quotes, backslashes or newlines")
    return "data:image/svg+xml," + escaped


def css_url(markup):
    return 'url("' + data_uri(markup) + '")'


def pixel_rects(rows, inks, x0=0, y0=0):
    """One path per ink for an authored pixel map. `inks` maps a character to (fill, opacity)."""
    runs = {}
    for y, row in enumerate(rows):
        x = 0
        while x < len(row):
            char, run = row[x], 1
            while x + run < len(row) and row[x + run] == char:
                run += 1
            if char in inks:
                runs.setdefault(char, []).append(f"M{x0 + x} {y0 + y}h{run}v1h-{run}z")
            x += run
    return "".join(
        f"<path fill='{inks[char][0]}' fill-opacity='{_op(inks[char][1])}' d='{''.join(parts)}'/>"
        for char, parts in runs.items()
    )


def pixel_svg(rows, inks, scale):
    width, height = len(rows[0]), len(rows)
    return svg(width * scale, height * scale, pixel_rects(rows, inks), view=f"0 0 {width} {height}", crisp=True)


# ---- Blockcraft (voxel) art --------------------------------------------------------------

# Dressed stone courses with staggered joints: ^ lit edge, v shaded edge, # mortar seam.
STONE = (
    "^^^^^#^^^^^^^^^#",
    "^...v#^.......v#",
    "^...v#^.......v#",
    "vvvvv#vvvvvvvvv#",
    "################",
    "^^#^^^^^^#^^^^^^",
    ".v#^....v#^.....",
    ".v#^....v#^.....",
    ".v#^....v#^.....",
    "vv#vvvvvv#vvvvvv",
    "################",
    "^^^^^^^#^^^^^^^#",
    "^.....v#^.....v#",
    "^.....v#^.....v#",
    "vvvvvvv#vvvvvvv#",
    "################",
)

# Oak planks: h lit top edge, g grain, j end joint, s seam.
PLANKS = (
    "hhhhhhhhhhhjhhhh",
    "..gg.......j...g",
    "......ggg..j....",
    "ssssssssssssssss",
    "hhhhjhhhhhhhhhhh",
    "....j...gg......",
    ".gg.j.........gg",
    "ssssssssssssssss",
    "hhhhhhhhhhhhhhjh",
    "......gg......j.",
    "..g...........j.",
    "ssssssssssssssss",
)

# Hanging grass: per-column drip lengths below two solid rows of turf.
_DRIPS = (1, 3, 2, 1, 2, 4, 2, 1, 3, 1, 2, 3, 1, 2, 4, 2)
_BLADES = {1, 6, 10, 13}


def _grass_rows():
    rows = ["G" * len(_DRIPS), "".join("G" if c in _BLADES else "g" for c in range(len(_DRIPS)))]
    for depth in range(1, max(_DRIPS) + 1):
        rows.append("".join("." if n < depth else ("d" if n == depth else "g") for n in _DRIPS))
    return tuple(rows)


GRASS_CAP = _grass_rows()

GRASS_BLOCK = (
    "GGGGGGGG",
    "GgGGgGgG",
    "gdgggdgg",
    "DdDDdDDD",
    "DDDDDDDD",
    "DDsDDDDD",
    "DDDDDsDD",
    "eeeeeeee",
)
STONE_BLOCK = (
    "hhhhhhhs",
    "h......s",
    "h..ss..s",
    "h......s",
    "hss....s",
    "h....s.s",
    "h......s",
    "ssssssss",
)
WOOD_BLOCK = (
    "hhhhhhhh",
    "..g....j",
    "......gj",
    "ssssssss",
    "hhhhhhhh",
    ".j..gg..",
    ".j......",
    "ssssssss",
)
LABEL_BLOCK = (
    "GGGGG",
    "gdggd",
    "DDDDD",
    "DsDDD",
    "DDDsD",
)

EARTH = "#6e4b2c"
OAK = "#8e6a3d"
ROCK = "#8a8b86"


def voxel_colours(palette):
    p, inks = palette, (palette["ink"], palette["muted"])
    stone = mix(p["panel"], ROCK, 0.42)
    dirt_block = mix(p["panel"], EARTH, 0.78)
    oak_block = mix(p["panel"], OAK, 0.8)
    return {
        "grass_hi": mix(p["accent"], "#ffffff", 0.22),
        "grass": p["accent"],
        "grass_lo": mix(p["accent"], "#000000", 0.32),
        # header "dirt" and shelf "wood" sit under text, so their tint is contrast-limited
        "dirt": mix(p["panel"], EARTH, safe_weight(p["panel"], EARTH, 0.42, inks)),
        "wood": mix(p["panel"], OAK, safe_weight(p["panel"], OAK, 0.5, inks)),
        "dirt_block": dirt_block,
        "dirt_lo": mix(dirt_block, "#000000", 0.3),
        "dirt_edge": mix(dirt_block, "#000000", 0.45),
        "stone": stone,
        "stone_hi": mix(stone, "#ffffff", 0.22),
        "stone_lo": mix(stone, "#000000", 0.38),
        "oak": oak_block,
        "oak_hi": mix(oak_block, "#ffffff", 0.18),
        "oak_lo": mix(oak_block, "#000000", 0.4),
    }


def stone_tile(palette, surface="panel"):
    base, inks = palette[surface], (palette["ink"], palette["muted"])
    seam = safe_weight(base, "#000000", 0.17, inks)
    shade = safe_weight(base, "#000000", 0.08, inks)
    lit = safe_weight(base, "#ffffff", 0.035, inks)
    return pixel_svg(STONE, {"#": ("#000000", seam), "v": ("#000000", shade), "^": ("#ffffff", lit)}, 3)


def plank_tile(palette, base):
    inks = (palette["ink"], palette["muted"])
    seam = safe_weight(base, "#000000", 0.32, inks)
    grain = safe_weight(base, "#000000", 0.14, inks)
    lit = safe_weight(base, "#ffffff", 0.08, inks)
    return pixel_svg(
        PLANKS, {"s": ("#000000", seam), "j": ("#000000", seam), "g": ("#000000", grain), "h": ("#ffffff", lit)}, 2
    )


def grass_cap(palette):
    c = voxel_colours(palette)
    return pixel_svg(GRASS_CAP, {"G": (c["grass_hi"], 1), "g": (c["grass"], 1), "d": (c["grass_lo"], 1)}, 2)


def _block_inks(c):
    return {
        "G": (c["grass_hi"], 1),
        "g": (c["grass"], 1),
        "d": (c["grass_lo"], 1),
        "D": (c["dirt_block"], 1),
        "s": (c["dirt_lo"], 1),
        "e": (c["dirt_edge"], 1),
    }


def block_row(palette):
    """A three-block crafting row (turf, stone, oak): the Blockcraft heading divider."""
    c = voxel_colours(palette)
    body = pixel_rects(GRASS_BLOCK, _block_inks(c))
    body += pixel_rects(STONE_BLOCK, {".": (c["stone"], 1), "h": (c["stone_hi"], 1), "s": (c["stone_lo"], 1)}, x0=10)
    body += pixel_rects(
        WOOD_BLOCK,
        {".": (c["oak"], 1), "g": (c["oak_lo"], 1), "j": (c["oak_lo"], 1), "h": (c["oak_hi"], 1), "s": (c["oak_lo"], 1)},
        x0=20,
    )
    return svg(56, 16, body, view="0 0 28 8", crisp=True)


def label_block(palette):
    return pixel_svg(LABEL_BLOCK, _block_inks(voxel_colours(palette)), 2)


# ---- Woodland relic (leaf) art -----------------------------------------------------------


def _blob(cx, cy, r, squash=1.0):
    k = r * squash

    def pt(dx, dy):
        return f"{cx + dx * r:.1f} {cy + dy * k:.1f}"

    return (
        f"M{pt(-1, 0)} C{pt(-1, -0.9)} {pt(-0.2, -1.1)} {pt(0.3, -0.95)} "
        f"C{pt(1.05, -0.75)} {pt(1.1, 0.2)} {pt(0.7, 0.65)} "
        f"C{pt(0.3, 1.1)} {pt(-0.6, 1.0)} {pt(-0.85, 0.55)} "
        f"C{pt(-1.05, 0.3)} {pt(-1, 0.1)} {pt(-1, 0)} Z"
    )


def _drift(y, a, b, c):
    """A contour that leaves the tile at the height and slope it entered, so tiles join."""
    return f"M0 {y} C60 {y - a} 100 {y + b} 160 {y + c} S260 {y + a} 320 {y}"


def contour_paths():
    hill = [_blob(196 + 7 * k, 158 - 3 * k, r, squash) for k, (r, squash) in enumerate(((30, 0.62), (54, 0.66), (80, 0.7)))]
    return [*hill, _drift(34, 12, 10, -6), _drift(52, 9, 14, -2), _drift(270, -10, 8, 12), _drift(292, -6, 12, 9)]


def contour_tile(palette, surface="panel"):
    """Survey-map contours: one elongated hill and drifting lines that run on across tiles."""
    base, inks = palette[surface], (palette["ink"], palette["muted"])
    weight = safe_weight(base, palette["accent"], 0.13, inks)
    body = "".join(f'<path d="{d}"/>' for d in contour_paths())
    return svg(
        320,
        320,
        f'<g fill="none" stroke="{palette["accent"]}" stroke-opacity="{_op(weight)}" stroke-width="1">{body}</g>',
    )


def crest(palette):
    a, p = palette["accent"], palette["panel"]
    leaf = "M38 8 C34 3 29 3 25 6 C29 9.5 34 10.5 38 8 Z"
    tendril = "M24 7 C18 9.5 13 10 6 8.5"
    body = (
        f'<g fill="none" stroke="{a}" stroke-width="1.1" stroke-linecap="round">'
        f'<path d="{leaf}"/><path d="M37 8 L27 6.2"/><path d="{tendril}" stroke-opacity=".7"/>'
        f'<g transform="translate(96 0) scale(-1 1)"><path d="{leaf}"/><path d="M37 8 L27 6.2"/>'
        f'<path d="{tendril}" stroke-opacity=".7"/></g></g>'
        f'<path d="M48 1 L54.5 7.5 L48 14 L41.5 7.5 Z" fill="{p}" stroke="{a}" stroke-width="1.2"/>'
        f'<path d="M48 4.6 L50.9 7.5 L48 10.4 L45.1 7.5 Z" fill="{a}"/>'
    )
    return svg(96, 15, body)


def bracket(palette, corner):
    transform = {
        "tl": "",
        "tr": "translate(18 0) scale(-1 1)",
        "bl": "translate(0 18) scale(1 -1)",
        "br": "translate(18 18) scale(-1 -1)",
    }[corner]
    a = palette["accent"]
    body = (
        f'<g transform="{transform}" fill="none" stroke="{a}" stroke-linecap="round">'
        f'<path d="M1.5 17 V6 Q1.5 1.5 6 1.5 H17" stroke-width="1.4"/>'
        f'<path d="M5 12 V7.5 Q5 5 7.5 5 H12" stroke-width=".9" stroke-opacity=".6"/>'
        f'<path d="M1.5 1.5 L4 4" stroke-width="1.1"/></g>'
    )
    return svg(18, 18, body)


def leaf_marker(palette):
    a, p = palette["accent"], palette["field"]
    return svg(
        12,
        12,
        f'<path d="M1.5 10.5 C1.5 5 5 1.5 10.5 1.5 C10.5 7 7 10.5 1.5 10.5 Z" fill="{a}"/>'
        f'<path d="M2.6 9.4 L8.6 3.4" stroke="{p}" stroke-width=".9" stroke-linecap="round"/>',
    )


def fleuron(palette):
    a, p = palette["accent"], palette["background"]
    leaf = "M66 7 C62 2.5 57 2.5 53 5.5 C57 9 62 9.5 66 7 Z"
    body = (
        f'<g stroke="{a}" stroke-linecap="round" fill="none">'
        f'<path d="M2 7 H50" stroke-width="1" stroke-opacity=".55"/><path d="M110 7 H158" stroke-width="1" stroke-opacity=".55"/>'
        f'<path d="{leaf}" stroke-width="1.1"/><path d="M65 7 L55 5.6" stroke-width=".9"/>'
        f'<g transform="translate(160 0) scale(-1 1)"><path d="{leaf}" stroke-width="1.1"/>'
        f'<path d="M65 7 L55 5.6" stroke-width=".9"/></g></g>'
        f'<path d="M80 1.5 L85.5 7 L80 12.5 L74.5 7 Z" fill="{p}" stroke="{a}" stroke-width="1.1"/>'
        f'<path d="M80 4.6 L82.4 7 L80 9.4 L77.6 7 Z" fill="{a}"/>'
    )
    return svg(160, 14, body)


def diamond(palette):
    return svg(8, 8, f'<path d="M4 .5 L7.5 4 L4 7.5 L.5 4 Z" fill="{palette["accent"]}"/>')


# ---- thumbnails ---------------------------------------------------------------------------


def _box(x, y, w, h, fill, frame, edge, radius, extra=""):
    if frame == "pixel":
        s = 2
        return (
            f'<path d="M{x + s} {y} H{x + w - s} V{y + s} H{x + w} V{y + h - s} H{x + w - s} V{y + h} '
            f'H{x + s} V{y + h - s} H{x} V{y + s} H{x + s} Z" fill="{fill}" stroke="{edge}" stroke-width="1.5"/>'
            f'<path d="M{x + 2.5} {y + h - 3} V{y + 2.5} H{x + w - 3}" stroke="#fff" stroke-opacity=".18" fill="none"/>'
            f'<path d="M{x + 3} {y + h - 2.5} H{x + w - 2.5} V{y + 3}" stroke="#000" stroke-opacity=".35" fill="none"/>{extra}'
        )
    if frame == "toon":
        return (f'<rect x="{x + 2}" y="{y + 2}" width="{w}" height="{h}" rx="{radius}" fill="{edge}"/>'
                f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{radius}" fill="{fill}" stroke="{edge}" stroke-width="1.5"/>{extra}')
    if frame == "chamfer":
        c = min(6, h / 3)
        pts = f"{x + c},{y} {x + w - c},{y} {x + w},{y + c} {x + w},{y + h - c} {x + w - c},{y + h} {x + c},{y + h} {x},{y + h - c} {x},{y + c}"
        return f'<polygon points="{pts}" fill="{fill}" stroke="{edge}" stroke-width="1.2"/>{extra}'
    if frame == "capsule":
        rx = min(h / 2, 14)
        return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" stroke="{edge}" stroke-width="1.2"/>'
                f'<rect x="{x + 3}" y="{y + 1.5}" width="{w - 6}" height="{h * 0.4:.1f}" rx="{max(0.0, rx - 2)}" fill="#fff" fill-opacity=".08"/>{extra}')
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{radius}" fill="{fill}" stroke="{edge}" stroke-width="1"/>{extra}'


def thumbnail(manifest):
    """A recognisable miniature of the chat window in this skin, built from the same materials."""
    skin, p = manifest["skin"], manifest["skin"]["palette"]
    frame, ornament = skin["frame"], skin["ornament"]
    radius = 0 if frame == "pixel" else min(skin["corners"], 8) / 2
    edge = mix(p["background"], "#000000", 0.55) if frame == "pixel" else p["border"]
    if frame == "toon":
        edge = mix(p["background"], "#000000", 0.6) if is_dark(p) else mix(p["ink"], p["background"], 0.08)
    defs, side, head = "", "", ""
    if ornament == "voxel":
        c = voxel_colours(p)
        tile = pixel_rects(STONE, {"#": ("#000000", 0.17), "v": ("#000000", 0.08), "^": ("#ffffff", 0.035)})
        cap = pixel_rects(GRASS_CAP, {"G": (c["grass_hi"], 1), "g": (c["grass"], 1), "d": (c["grass_lo"], 1)})
        defs = (
            f'<pattern id="m" width="16" height="16" patternUnits="userSpaceOnUse" patternTransform="scale(1.5)">{tile}</pattern>'
            f'<pattern id="t" width="16" height="6" patternUnits="userSpaceOnUse">{cap}</pattern>'
        )
        side = '<rect width="88" height="180" fill="url(#m)"/><rect width="88" height="6" fill="url(#t)"/>'
        head = (
            f'<rect x="88" width="232" height="32" fill="{c["dirt"]}"/><rect x="88" width="232" height="6" fill="url(#t)"/>'
            f'<rect x="88" y="31" width="232" height="2" fill="{edge}"/>'
        )
    elif ornament == "leaf":
        paths = "".join(f'<path d="{d}"/>' for d in contour_paths())
        side = f'<g fill="none" stroke="{p["accent"]}" stroke-opacity=".2" transform="translate(-60 -40) scale(.55)">{paths}</g>'
        crest_body = crest(p).split(">", 1)[1].rsplit("</svg>", 1)[0]
        head = (
            f'<rect x="88" width="232" height="32" fill="{p["panel"]}"/>'
            f'<rect x="88" y="31" width="232" height="1" fill="{p["accent"]}"/>'
            f'<rect x="88" y="28.5" width="232" height="1" fill="{p["border"]}" fill-opacity=".8"/>'
            f'<g transform="translate(180 15) scale(.5)">{crest_body}</g>'
        )
    else:
        from core.toybox_ornaments import ORNAMENTS

        if ornament in ORNAMENTS:
            defs, side, head = ORNAMENTS[ornament]["thumb"](p, edge)
        else:
            head = (
                f'<rect x="88" width="232" height="32" fill="{p["panel"]}"/>'
                f'<rect x="88" y="31" width="232" height="1" fill="{p["border"]}"/>'
            )
    ink, muted, accent = p["ink"], p["muted"], p["accent"]
    rows = ""
    for i, y in enumerate((30, 46, 62)):
        if i == 0:
            if frame == "pixel":
                rows += f'<rect x="7" y="{y - 4}" width="74" height="13" fill="{p["field"]}" stroke="{accent}" stroke-width="1.5"/>'
            elif frame in ("toon", "capsule", "chamfer"):
                rx = 6.5 if frame == "capsule" else radius
                rows += f'<rect x="7" y="{y - 4}" width="74" height="13" rx="{rx}" fill="{p["field"]}" stroke="{edge}" stroke-width="1.2"/>'
            elif frame == "etched":
                rows += f'<rect x="7" y="{y - 4}" width="74" height="13" rx="2" fill="{p["field"]}"/><rect x="7" y="{y - 4}" width="1.5" height="13" fill="{accent}"/>'
            else:
                rows += f'<rect x="7" y="{y - 4}" width="74" height="13" rx="{radius}" fill="{p["field"]}"/>'
        rows += f'<rect x="13" y="{y}" width="{52 - 9 * i}" height="4" rx="1" fill="{ink}" fill-opacity=".7"/>'
    label = f'<rect x="10" y="12" width="26" height="4" fill="{muted}" fill-opacity=".9"/>'
    if ornament == "voxel":
        label = f'<g transform="translate(9 10) scale(1.2)">{pixel_rects(LABEL_BLOCK, _block_inks(voxel_colours(p)))}</g><rect x="18" y="12" width="22" height="4" fill="{muted}"/>'
    elif ornament == "leaf":
        label = f'<path d="M12 10 L15 13 L12 16 L9 13 Z" fill="{accent}"/><rect x="18" y="11" width="24" height="3.5" fill="{muted}"/>'
    foot = _box(8, 154, 72, 18, p["field"], frame, edge, radius)
    brand = f'<rect x="98" y="9" width="14" height="14" rx="{0 if frame == "pixel" else 3}" fill="#000"/><rect x="117" y="13" width="22" height="5" fill="{ink}"/>'
    buttons = _box(256, 9, 26, 14, p["field"], frame, edge, radius) + _box(288, 9, 24, 14, p["field"], frame, edge, radius)
    user = _box(196, 46, 110, 26, p["field"], frame, edge, radius,
                f'<rect x="205" y="53" width="86" height="4" fill="{ink}" fill-opacity=".75"/><rect x="205" y="62" width="54" height="4" fill="{ink}" fill-opacity=".75"/>')
    answer = "".join(
        f'<rect x="102" y="{y}" width="{w}" height="4" fill="{muted}" fill-opacity=".85"/>'
        for y, w in ((86, 196), (96, 182), (106, 154), (116, 120))
    )
    composer = _box(100, 140, 156, 30, p["field"], frame, edge, radius,
                    f'<rect x="110" y="152" width="72" height="4" fill="{muted}" fill-opacity=".8"/>')
    send = _box(264, 140, 46, 30, accent, frame, edge, radius,
                f'<rect x="276" y="153" width="22" height="4" fill="{p["accentInk"]}"/>')
    if frame == "pixel":
        send += f'<rect x="266" y="141.5" width="42" height="3" fill="{mix(accent, "#ffffff", 0.3)}"/>'
    body = (
        f"<defs>{defs}</defs>"
        f'<rect width="320" height="180" fill="{p["background"]}"/>'
        f'<rect width="88" height="180" fill="{p["panel"]}"/>{side}<rect x="88" width="232" height="180" fill="{p["background"]}"/>'
        f'<rect x="87" width="1.5" height="180" fill="{edge}"/>'
        f"{label}{rows}{foot}{head}{brand}{buttons}{user}{answer}{composer}{send}"
    )
    return data_uri(svg(320, 180, body))
