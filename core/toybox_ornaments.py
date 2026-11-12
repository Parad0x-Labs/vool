"""Renderer contract 1.2 ornaments: three illustrated, full-app skins.

Contributor: sls_0x. Same contract as core/toybox_materials.py: static SVG drawn here from a
validated palette (gradients and blur filters are defined inside each image), no text,
raster, links or external references.

Every sidebar carries a full-height illustration. Text never sits on the illustration
directly: rows, labels and blocks sit on glass plates whose opacity is computed so that,
over ANY colour behind them (white or black included), text keeps 4.5:1. Headers use the
same computed veil. Decorative layers and motion sit in text-free bands or behind plates.
"""

from __future__ import annotations

import math

from core.toybox_materials import contrast, is_dark, mix, safe_weight, svg

SIDE_W, SIDE_H = 240, 900


def _f():
    # the renderer imports this module, so its helpers are resolved at call time
    from core import toybox_fragment

    return toybox_fragment


def _rgba(colour, alpha):
    r, g, b = (int(colour[i : i + 2], 16) for i in (1, 3, 5))
    return f"rgba({r},{g},{b},{int(alpha * 1000) / 1000})"


def glass_alpha(p, base):
    """Opacity for a plate of colour `base` so text reads over any backdrop at all."""
    worst = "#ffffff" if is_dark(p) else "#000000"
    see_through = safe_weight(base, worst, 0.6, (p["ink"], p["muted"]))
    return math.ceil((1 - see_through) * 1000) / 1000


def worst_under_glass(p, base):
    """The least readable colour a plate can show (backdrop at its most hostile)."""
    worst = "#ffffff" if is_dark(p) else "#000000"
    return mix(base, worst, 1 - glass_alpha(p, base))


def tint(p, base, colour, weight=1.0):
    """`colour` laid over a palette surface only as far as its text stays readable."""
    return mix(p[base], colour, safe_weight(p[base], colour, weight, (p["ink"], p["muted"])))


def _inner(markup):
    """An SVG's children, for embedding one drawing inside another."""
    return markup.split(">", 1)[1].rsplit("</svg>", 1)[0]


def _star4(x, y, r, fill, opacity=1, thin=0.24):
    k = r * thin
    return (f'<path d="M{x} {y - r} L{x + k:.2f} {y - k:.2f} L{x + r} {y} L{x + k:.2f} {y + k:.2f} L{x} {y + r} '
            f'L{x - k:.2f} {y + k:.2f} L{x - r} {y} L{x - k:.2f} {y - k:.2f} Z" fill="{fill}" fill-opacity="{opacity}"/>')


def _starburst(x, y, r, points, fill, stroke=None, width=1.2, inner=0.42, rotate=0):
    pts = []
    for i in range(points * 2):
        radius = r if i % 2 == 0 else r * inner
        a = math.pi * i / points + math.radians(rotate) - math.pi / 2
        pts.append(f"{x + radius * math.cos(a):.2f} {y + radius * math.sin(a):.2f}")
    line = f' stroke="{stroke}" stroke-width="{width}" stroke-linejoin="round"' if stroke else ""
    return f'<path d="M{" L".join(pts)} Z" fill="{fill}"{line}/>'


class _Seq:
    """A small deterministic sequence, so 'scattered' details are authored and repeatable."""

    def __init__(self, seed):
        self.state = seed

    def next(self):
        self.state = (self.state * 1103515245 + 12345) % 2147483648
        return self.state / 2147483648

    def between(self, low, high):
        return low + (high - low) * self.next()


def _stars(seq, count, top, bottom, colours, max_r=1.3):
    out = []
    for _ in range(count):
        x, y = seq.between(4, SIDE_W - 4), seq.between(top, bottom)
        r, o = seq.between(0.45, max_r), seq.between(0.45, 0.95)
        out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.2f}" fill="{colours[int(seq.next() * len(colours))]}" fill-opacity="{o:.2f}"/>')
    return "".join(out)


# ---- shared rules -------------------------------------------------------------------------

PLATE_ROWS = ("#sessions .session", ".proj-head", "#nav .nav-item", "#back")
PLATE_TEXT = (".side-title", ".nav-sec", "#sessions .side-empty", "#sessions .arch-head", ".nav-empty", "#uiLocaleWrap", ".side-note")


def _plates(radius, extra=""):
    f = _f()
    glass = f"background-color:var(--tb-glass);-webkit-backdrop-filter:blur(7px);backdrop-filter:blur(7px);{extra}"
    out = f._rule(PLATE_ROWS, glass)
    out += f._rule(PLATE_TEXT, glass + f"border-radius:{radius}px")
    out += f._rule((".side-title",), "flex:0 0 auto;margin-inline-end:auto;padding:3px 7px")
    # never past the sidebar edge: tighter spacing, and a second row when the buttons still do not fit
    out += f._rule(("#sidebar .side-head",), "gap:5px;row-gap:6px;flex-wrap:wrap;padding-inline:8px")
    out += f._rule(("#newChat", "#newProject"), "white-space:nowrap;flex:0 0 auto;padding:4px 7px")
    out += f._rule((".nav-sec",), "margin:10px 0 4px;padding:5px 9px")
    out += f._rule(("#sessions .side-empty", "#sessions .arch-head", ".nav-empty", ".side-note"), "margin:4px 0")
    out += f._rule(("#uiLocaleWrap",), "margin:10px 10px 8px;padding:10px 12px 6px;border-bottom:0")
    out += f._rule(("#sessions", "#nav"), "gap:5px;display:flex;flex-direction:column")
    return out


def _stage(selector):
    """A stacking context, so decorative layers can sit behind a surface's content."""
    return _f()._rule((selector,), "position:relative;z-index:0;isolation:isolate")


def _layer(selector, body):
    return _f()._rule((selector,), 'content:"";position:absolute;pointer-events:none;' + body)


def _emblem(width, height, gap=14):
    return _f()._rule((".empty-greet::before",), f'content:"";display:block;width:{width}px;height:{height}px;margin:0 auto {gap}px;'
                      f"background:var(--tb-art-emblem) center/{width}px {height}px no-repeat")


def _divider(width, height, top=10):
    return _f()._rule(("#paneTitle::after",), f'content:"";display:block;width:{width}px;height:{height}px;margin-top:{top}px;'
                      f"background:var(--tb-art-divider) 0 0/{width}px {height}px no-repeat")


def _marks(bullet, marker):
    f = _f()
    out = f._rule(tuple(s + "::before" for s in f.LABELS),
                  f'content:"";display:inline-block;width:{bullet}px;height:{bullet}px;margin-inline-end:7px;vertical-align:-1px;'
                  f"background:var(--tb-art-bullet) center/{bullet}px {bullet}px no-repeat")
    out += f._rule(('.nav-item[aria-current="true"]::after',),
                   f'content:"";flex:0 0 {marker}px;width:{marker}px;height:{marker}px;margin-inline-start:auto;'
                   f"background:var(--tb-art-marker) center/{marker}px {marker}px no-repeat")
    out += f._rule((".msg.assistant::before",), 'content:"";position:absolute;inset-inline-start:-24px;top:18px;width:14px;height:14px;'
                   "background:var(--tb-art-marker) center/14px 14px no-repeat;pointer-events:none")
    return out


def _backdrop(colour_var="--tb-sky-top"):
    """The full-height illustration behind both sidebars, anchored to the bottom."""
    f = _f()
    return f._rule(f.SIDEBARS, f"background:var(--tb-art-backdrop) center bottom/100% auto no-repeat,var({colour_var})")


def _scrollbars(thumb):
    f = _f()
    return f._rule(("#sessions", "#nav", "#log", "#pane", ".popover", "#xpanel"), f"scrollbar-color:{thumb} transparent;scrollbar-width:thin")


# ---- Neon Courier: retro cartoon future -----------------------------------------------------

NEON_PINK, NEON_YELLOW, NEON_CYAN = "#ff4f9a", "#ffd36b", "#7ff7ff"


def _neon_city(p, seq, lit=True, only_lit=False):
    """Googie night city: towers, saucer needle, dome, transit tube, boomerang sign."""
    far, near, ground = "#1a1f4c", "#0b1024", "#070a18"
    out, windows = [], []
    x = -6
    while x < SIDE_W:
        w, h = seq.between(18, 34), seq.between(70, 170)
        top = 820 - h
        out.append(f'<rect x="{x:.1f}" y="{top:.1f}" width="{w:.1f}" height="{h + 80:.1f}" fill="{far}"/>')
        for wy in range(int(top) + 8, 812, 11):
            for wx in range(int(x) + 4, int(x + w) - 4, 7):
                if seq.next() < 0.32:
                    colour = (NEON_YELLOW, NEON_CYAN, NEON_PINK)[int(seq.next() * 3)]
                    windows.append(f'<rect x="{wx}" y="{wy}" width="3" height="4" fill="{colour}" fill-opacity="{seq.between(.45, .9):.2f}"/>')
        x += w + seq.between(2, 6)
    tube = ("M-12 742 C30 742 40 690 64 660 C88 630 120 624 150 640 C190 660 196 712 252 716")
    out.append(f'<path d="{tube}" fill="none" stroke="{NEON_CYAN}" stroke-opacity=".16" stroke-width="16" stroke-linecap="round"/>'
               f'<path d="{tube}" fill="none" stroke="#ffffff" stroke-opacity=".35" stroke-width="2" transform="translate(0 -5)"/>'
               f'<rect x="96" y="626" width="26" height="9" rx="4.5" fill="{NEON_PINK}" transform="rotate(-8 109 630)"/>'
               f'<rect x="100" y="627" width="9" height="4" rx="2" fill="#ffffff" fill-opacity=".7" transform="rotate(-8 109 630)"/>')
    # saucer needle tower
    out.append(f'<path d="M54 900 L60 600 L64 600 L70 900 Z" fill="#141b3d"/><path d="M46 900 L61 610 L63 610 L78 900" fill="none" stroke="#2b3770" stroke-width="2"/>'
               f'<ellipse cx="62" cy="592" rx="34" ry="10" fill="#26336b"/><ellipse cx="62" cy="588" rx="22" ry="7" fill="#33448a"/>'
               f'<path d="M62 520 L62 582" stroke="#8fa6ff" stroke-width="2"/><circle cx="62" cy="518" r="3" fill="{NEON_PINK}"/>'
               + "".join(f'<circle cx="{62 + 30 * math.cos(math.radians(a)):.1f}" cy="{593 + 8 * math.sin(math.radians(a)):.1f}" r="1.6" fill="{NEON_CYAN}"/>'
                         for a in range(0, 360, 30)))
    # dome
    out.append(f'<path d="M126 900 L126 806 A54 44 0 0 1 234 806 L234 900 Z" fill="#1d2354"/>'
               f'<path d="M134 806 A46 36 0 0 1 226 806" fill="none" stroke="{NEON_CYAN}" stroke-opacity=".55" stroke-width="2"/>'
               + "".join(f'<path d="M180 770 L{150 + 15 * i} 806" stroke="#2f3a80" stroke-width="1.5"/>' for i in range(5))
               + f'<rect x="168" y="836" width="24" height="30" rx="12" fill="{NEON_YELLOW}" fill-opacity=".8"/>')
    # boomerang sign
    out.append(f'<path d="M104 900 L104 700" stroke="#2b3770" stroke-width="3"/>'
               f'<path d="M86 690 Q104 660 128 676 Q110 676 104 700 Q100 686 86 690 Z" fill="none" stroke="{NEON_PINK}" stroke-width="3" stroke-linejoin="round"/>'
               f'<path d="M86 690 Q104 660 128 676 Q110 676 104 700 Q100 686 86 690 Z" fill="none" stroke="#ffd1e6" stroke-width="1"/>'
               + _starburst(130, 664, 9, 8, NEON_YELLOW))
    # near silhouettes + ground
    x = -4
    while x < SIDE_W:
        w, h = seq.between(22, 40), seq.between(30, 80)
        out.append(f'<rect x="{x:.1f}" y="{900 - h:.1f}" width="{w:.1f}" height="{h:.1f}" fill="{near}"/>')
        for wy in range(int(900 - h) + 6, 896, 9):
            for wx in range(int(x) + 4, int(x + w) - 3, 6):
                if seq.next() < 0.22:
                    windows.append(f'<rect x="{wx}" y="{wy}" width="2.5" height="3.5" fill="{NEON_YELLOW}" fill-opacity="{seq.between(.5, .95):.2f}"/>')
        x += w
    out.append(f'<rect y="892" width="{SIDE_W}" height="8" fill="{ground}"/>')
    if only_lit:
        return "".join(windows)
    return "".join(out) + ("".join(windows) if lit else "")


def _neon_backdrop(p):
    seq = _Seq(7)
    defs = ('<defs><linearGradient id="nsky" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#0b0a26"/>'
            '<stop offset=".42" stop-color="#16214a"/><stop offset=".68" stop-color="#2a2f6c"/><stop offset=".84" stop-color="#5b2a72"/>'
            '<stop offset="1" stop-color="#2a1240"/></linearGradient>'
            f'<radialGradient id="nhaze" cx=".5" cy=".86" r=".6"><stop offset="0" stop-color="{NEON_PINK}" stop-opacity=".5"/>'
            f'<stop offset="1" stop-color="{NEON_PINK}" stop-opacity="0"/></radialGradient>'
            '<radialGradient id="nmoon" cx=".38" cy=".34" r=".72"><stop offset="0" stop-color="#fffaf0"/><stop offset=".65" stop-color="#ffe2b8"/>'
            '<stop offset="1" stop-color="#efb27e"/></radialGradient>'
            '<filter id="nblur" x="-60%" y="-60%" width="220%" height="220%"><feGaussianBlur stdDeviation="9"/></filter></defs>')
    lanes = (f'<path d="M-10 470 C60 430 150 500 250 440" fill="none" stroke="{NEON_PINK}" stroke-opacity=".35" stroke-width="1.2" stroke-dasharray="2 7"/>'
             f'<path d="M-10 520 C80 490 160 560 250 500" fill="none" stroke="{NEON_CYAN}" stroke-opacity=".3" stroke-width="1.2" stroke-dasharray="2 9"/>'
             + "".join(f'<circle cx="{x}" cy="{y}" r="1.8" fill="{c}"/>' for x, y, c in ((40, 447, NEON_PINK), (150, 478, NEON_PINK), (92, 515, NEON_CYAN), (210, 512, NEON_CYAN))))
    moon = ('<circle cx="176" cy="150" r="46" fill="#ffd9a8" fill-opacity=".35" filter="url(#nblur)"/>'
            '<circle cx="176" cy="150" r="30" fill="url(#nmoon)"/>'
            '<circle cx="166" cy="142" r="5" fill="#e9b884" fill-opacity=".55"/><circle cx="186" cy="160" r="3.4" fill="#e9b884" fill-opacity=".5"/>'
            '<circle cx="182" cy="138" r="2.4" fill="#e9b884" fill-opacity=".45"/>')
    sparkles = _star4(44, 90, 6, "#ffffff", .9) + _star4(120, 230, 4.5, NEON_CYAN, .9) + _star4(212, 300, 3.5, "#ffffff", .8)
    body = (defs + f'<rect width="{SIDE_W}" height="{SIDE_H}" fill="url(#nsky)"/><rect width="{SIDE_W}" height="{SIDE_H}" fill="url(#nhaze)"/>'
            + _stars(seq, 60, 6, 600, ("#ffffff", "#ffe8b0", "#bff6ff")) + sparkles + moon + lanes + _neon_city(p, _Seq(23)))
    return svg(SIDE_W, SIDE_H, body)


def _neon_twinkle(p):
    """Only the lit windows and the beacon, drawn exactly over the city: the twinkle layer."""
    return svg(SIDE_W, SIDE_H, _neon_city(p, _Seq(23), only_lit=True) + f'<circle cx="62" cy="518" r="4" fill="{NEON_PINK}"/>')


def _neon_car(scale=1.0):
    s = scale
    body = (f'<g transform="scale({s})">'
            f'<path d="M4 22 Q2 12 14 12 L22 12 Q24 6 30 6 L40 6 Q50 6 54 14 L62 16 Q66 18 62 22 Z" fill="#ff7a59" stroke="#0a0f22" stroke-width="2" stroke-linejoin="round"/>'
            f'<path d="M24 12 Q26 7 31 7 L39 7 Q47 7 50 13 Z" fill="{NEON_CYAN}" fill-opacity=".85" stroke="#0a0f22" stroke-width="1.5"/>'
            f'<path d="M28 9 L33 9" stroke="#ffffff" stroke-width="1.5" stroke-linecap="round"/>'
            f'<path d="M14 12 L8 4 L18 12" fill="#ffb199" stroke="#0a0f22" stroke-width="1.5" stroke-linejoin="round"/>'
            f'<circle cx="60" cy="18" r="2" fill="{NEON_YELLOW}"/><path d="M4 18 L-14 16 L-14 22 L4 21 Z" fill="{NEON_PINK}" fill-opacity=".8"/>'
            f'<path d="M4 19 L-24 19" stroke="{NEON_YELLOW}" stroke-opacity=".7" stroke-width="2" stroke-linecap="round"/></g>')
    return svg(int(92 * s), int(28 * s), f'<g transform="translate({26 * s:.1f} 0)">{body}</g>')


def _neon_art(p):
    tube = ('<defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#ff4f9a" stop-opacity="0"/>'
            '<stop offset=".5" stop-color="#ff4f9a" stop-opacity=".55"/><stop offset="1" stop-color="#ff4f9a" stop-opacity="0"/></linearGradient></defs>'
            '<rect width="240" height="16" fill="url(#g)"/><rect y="6.5" width="240" height="3.5" rx="1.7" fill="#ff4f9a"/>'
            '<rect y="7.3" width="240" height="1.2" fill="#ffd1e6"/>' + _starburst(200, 8, 7, 8, NEON_YELLOW) + _star4(70, 8, 4, NEON_CYAN))
    divider = ('<defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#3ff0c4" stop-opacity="0"/>'
               '<stop offset=".5" stop-color="#3ff0c4" stop-opacity=".5"/><stop offset="1" stop-color="#3ff0c4" stop-opacity="0"/></linearGradient></defs>'
               '<rect x="24" y="6" width="200" height="16" fill="url(#g)"/><rect x="24" y="12" width="196" height="4" rx="2" fill="#3ff0c4"/>'
               '<rect x="24" y="13" width="196" height="1.3" fill="#e8fff8"/>' + _starburst(13, 14, 12, 8, NEON_YELLOW, "#0a0f22", 1.4)
               + "".join(_star4(60 + 50 * i, 5, 2.6, NEON_PINK) for i in range(3)))
    emblem = ('<defs><radialGradient id="g" cx=".5" cy=".5" r=".5"><stop offset="0" stop-color="#ffd36b" stop-opacity=".55"/>'
              '<stop offset="1" stop-color="#ffd36b" stop-opacity="0"/></radialGradient></defs>'
              '<circle cx="60" cy="40" r="38" fill="url(#g)"/>'
              f'<ellipse cx="60" cy="40" rx="52" ry="13" fill="none" stroke="{NEON_PINK}" stroke-width="3" transform="rotate(-12 60 40)"/>'
              + _starburst(60, 40, 26, 8, NEON_YELLOW, "#0a0f22", 2.2, inner=0.38)
              + f'<circle cx="60" cy="40" r="7" fill="#0a0f22"/><circle cx="60" cy="40" r="4" fill="{NEON_CYAN}"/>'
              + _star4(14, 12, 5, NEON_CYAN) + _star4(108, 66, 4, "#ffffff"))
    corner = _starburst(9, 9, 8, 8, NEON_YELLOW, "#0a0f22", 1.2) + f'<circle cx="9" cy="9" r="2" fill="{NEON_PINK}"/>'
    return {"backdrop": _neon_backdrop(p), "twinkle": _neon_twinkle(p), "band": svg(240, 16, tube), "divider": svg(230, 28, divider),
            "emblem": svg(120, 80, emblem), "car": _neon_car(1.0), "corner": svg(18, 18, corner),
            "bullet": svg(12, 12, _starburst(6, 6, 5.6, 8, NEON_YELLOW, "#0a0f22", .8)),
            "marker": svg(14, 14, _starburst(7, 7, 6.5, 8, p["accent"], "#0a0f22", .8))}


def _neon_tokens(p):
    return {"tb-sky-top": "#0b0a26", "tb-glass": _rgba(p["panel"], glass_alpha(p, p["panel"])),
            "tb-header-a": tint(p, "panel", "#122444"), "tb-header-b": p["panel"], "tb-log-glow": tint(p, "background", NEON_PINK, 0.1)}


def _neon_css(skin):
    f = _f()
    out = _backdrop() + _plates(skin["corners"], "box-shadow:inset 0 0 0 1px rgba(127,247,255,.10)")
    out += f._rule((f.HEADER,), "position:relative;background:linear-gradient(180deg,var(--tb-header-a),var(--tb-header-b));padding-bottom:22px")
    out += _layer(f.HEADER + "::after", "left:0;right:0;bottom:0;height:16px;background:var(--tb-art-band) 0 0/240px 16px repeat-x")
    out += f._rule(f.PANEL_HEADS, "background:linear-gradient(180deg,var(--tb-header-a),var(--tb-header-b));box-shadow:inset 0 -3px 0 #ff4f9a")
    out += f._rule(f.BRACKETED, "background:var(--tb-art-corner) right 8px top 8px/18px 18px no-repeat,linear-gradient(180deg,rgba(127,247,255,.05),transparent 40%),var(--panel)")
    out += f._rule(("#log",), "background:radial-gradient(ellipse 70% 45% at 50% 115%,var(--tb-log-glow),transparent 70%),var(--chat)")
    out += _emblem(120, 80) + _divider(230, 28) + _marks(12, 14) + _scrollbars("#3a4f7a")
    out += f._rule(("h1", ".empty-greet"), "text-shadow:0 0 18px rgba(63,240,196,.35)")
    return out


def _neon_effects(skin):
    f = _f()
    out = "@keyframes tb-flicker{0%,100%{opacity:1}7%{opacity:.55}8%{opacity:1}52%{opacity:1}53%{opacity:.4}55%{opacity:1}}\n"
    out += f._rule((f.HEADER + "::after",), "animation:tb-flicker 6s steps(1,end) infinite")
    out += "@keyframes tb-twinkle{0%,100%{opacity:.25}30%{opacity:1}60%{opacity:.55}}\n"
    for side in f.SIDEBARS:
        out += _stage(side)
        out += _layer(side + "::before", "inset:0;z-index:-1;background:var(--tb-art-twinkle) center bottom/100% auto no-repeat;"
                      "animation:tb-twinkle 3.6s steps(4,end) infinite")
    out += "@keyframes tb-fly{0%{transform:translateX(-60vw) translateY(10px);opacity:0}8%{opacity:1}50%{transform:translateX(0) translateY(-6px)}92%{opacity:1}100%{transform:translateX(60vw) translateY(4px);opacity:0}}\n"
    out += _stage(".empty")
    out += _layer(".empty::after", "left:50%;top:-30px;width:92px;height:28px;margin-left:-46px;z-index:-1;"
                  "background:var(--tb-art-car) center/92px 28px no-repeat;animation:tb-fly 11s ease-in-out infinite")
    return out


def _neon_under_text(p, tokens):
    return [worst_under_glass(p, p["panel"]), tokens["tb-header-a"], tokens["tb-header-b"], tokens["tb-log-glow"]]


def _neon_thumb(p, edge):
    art = _neon_art(p)
    side = (f'<defs><clipPath id="sc"><rect width="88" height="180"/></clipPath></defs>'
            f'<g clip-path="url(#sc)"><g transform="translate(0 -150) scale(.3667)">{_inner(art["backdrop"])}</g></g>'
            + "".join(f'<rect x="6" y="{y}" width="76" height="12" rx="4" fill="{_rgba(p["panel"], .9)}"/>' for y in (26, 42, 58)))
    head = (f'<defs><linearGradient id="hb" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#122444"/><stop offset="1" stop-color="{p["panel"]}"/></linearGradient></defs>'
            f'<rect x="88" width="232" height="34" fill="url(#hb)"/><rect x="88" y="30" width="232" height="3" fill="{NEON_PINK}"/>'
            f'<rect x="88" y="30.6" width="232" height="1" fill="#ffd1e6"/>' + _starburst(300, 31, 5, 8, NEON_YELLOW))
    return "", side, head


# ---- Sakura Skies: anime golden hour ------------------------------------------------------

BLOSSOM, BLOSSOM_DEEP, BRANCH = "#ffb7d5", "#ff7eb6", "#6b3f5c"


def _cloud(x, y, s, shade="#d9cdf6", body="#ffffff"):
    """A cel-shaded cumulus: crisp body, flat base, a lavender shade band and a highlight."""
    path = (f"M{x} {y} a{14 * s} {14 * s} 0 0 1 {16 * s} {-15 * s} a{20 * s} {20 * s} 0 0 1 {36 * s} {-10 * s} "
            f"a{16 * s} {16 * s} 0 0 1 {28 * s} {8 * s} a{12 * s} {12 * s} 0 0 1 {16 * s} {17 * s} Z")
    shade_path = (f"M{x + 6 * s} {y} a{12 * s} {8 * s} 0 0 1 {24 * s} {-6 * s} a{16 * s} {9 * s} 0 0 1 {34 * s} {-2 * s} "
                  f"a{12 * s} {7 * s} 0 0 1 {28 * s} {8 * s} Z")
    return (f'<path d="{path}" fill="{body}"/><path d="{shade_path}" fill="{shade}" fill-opacity=".85"/>'
            f'<ellipse cx="{x + 40 * s:.1f}" cy="{y - 18 * s:.1f}" rx="{9 * s:.1f}" ry="{3 * s:.1f}" fill="#ffffff"/>')


def _blossom(x, y, r, petal=BLOSSOM, centre=BLOSSOM_DEEP, rotate=0):
    out = []
    for a in range(-90, 270, 72):
        cx = x + r * 0.62 * math.cos(math.radians(a + rotate))
        cy = y + r * 0.62 * math.sin(math.radians(a + rotate))
        out.append(f'<ellipse cx="{cx:.2f}" cy="{cy:.2f}" rx="{r * 0.5:.2f}" ry="{r * 0.36:.2f}" transform="rotate({a + rotate:.1f} {cx:.2f} {cy:.2f})" fill="{petal}"/>')
    return "".join(out) + f'<circle cx="{x}" cy="{y}" r="{r * 0.24:.2f}" fill="{centre}"/>'


def _petal(cx, cy, s, fill, rotate=0, opacity=1):
    return (f'<path transform="rotate({rotate:.1f} {cx:.1f} {cy:.1f})" d="M{cx:.1f} {cy + 5 * s:.1f} C{cx - 5 * s:.1f} {cy + 1 * s:.1f} {cx - 4 * s:.1f} {cy - 4 * s:.1f} {cx - 1 * s:.1f} {cy - 5 * s:.1f} '
            f'L{cx:.1f} {cy - 3.6 * s:.1f} L{cx + 1 * s:.1f} {cy - 5 * s:.1f} C{cx + 4 * s:.1f} {cy - 4 * s:.1f} {cx + 5 * s:.1f} {cy + 1 * s:.1f} {cx:.1f} {cy + 5 * s:.1f} Z" '
            f'fill="{fill}" fill-opacity="{opacity:.2f}"/>')


def _sakura_backdrop(p):
    seq = _Seq(11)
    defs = ('<defs><linearGradient id="ssky" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#8fb6ff"/><stop offset=".3" stop-color="#b4b3f5"/>'
            '<stop offset=".58" stop-color="#f1bfe0"/><stop offset=".8" stop-color="#ffd0b6"/><stop offset="1" stop-color="#ffe6c8"/></linearGradient>'
            '<radialGradient id="ssun" cx=".62" cy=".78" r=".5"><stop offset="0" stop-color="#fffbe8" stop-opacity=".95"/>'
            '<stop offset=".35" stop-color="#ffe3c2" stop-opacity=".6"/><stop offset="1" stop-color="#ffe3c2" stop-opacity="0"/></radialGradient></defs>')
    clouds = (_cloud(-30, 230, 1.6) + _cloud(120, 330, 1.1) + _cloud(10, 470, 1.25, shade="#e9c9ee") + _cloud(150, 560, 0.9, shade="#f2cde2")
              + _cloud(-20, 640, 0.8, shade="#f6d3d6"))
    sparkles = "".join(_star4(round(seq.between(10, 230), 1), round(seq.between(20, 420), 1), round(seq.between(2.5, 6), 1), "#ffffff", round(seq.between(.6, 1), 2)) for _ in range(14))
    city = ('<path d="M0 900 L0 770 L18 770 L18 742 L30 742 L30 770 L52 770 L56 706 L60 770 L84 770 L84 752 L104 752 L104 726 L116 712 L128 726 '
            'L128 760 L150 760 L150 736 L170 736 L170 768 L196 768 L200 728 L204 768 L240 768 L240 900 Z" fill="#c9b6e8"/>'
            '<path d="M0 900 L0 800 L40 800 L40 786 L70 786 L70 806 L120 806 L120 790 L160 790 L160 810 L240 810 L240 900 Z" fill="#b39fdc"/>'
            + "".join(f'<rect x="{x}" y="{y}" width="3" height="4" fill="#fff4d6"/>' for x, y in ((22, 750), (108, 732), (120, 740), (154, 744), (176, 748), (90, 760))))
    branch = (f'<path d="M-6 870 C40 850 70 830 104 812 C130 798 150 800 176 786" fill="none" stroke="{BRANCH}" stroke-width="6" stroke-linecap="round"/>'
              f'<path d="M60 836 C70 812 86 800 100 790" fill="none" stroke="{BRANCH}" stroke-width="3" stroke-linecap="round"/>'
              f'<path d="M128 800 C140 780 150 772 166 768" fill="none" stroke="{BRANCH}" stroke-width="2.5" stroke-linecap="round"/>')
    blossoms = "".join(_blossom(x, y, r, rotate=seq.between(0, 72)) for x, y, r in
                       ((30, 852, 9), (52, 840, 7), (76, 826, 10), (98, 792, 8), (104, 812, 7), (134, 796, 9), (160, 772, 8), (176, 786, 7), (88, 806, 6)))
    petals = "".join(_petal(seq.between(10, 230), seq.between(560, 880), seq.between(.8, 1.3), BLOSSOM, seq.between(0, 180), seq.between(.7, 1)) for _ in range(12))
    body = (defs + f'<rect width="{SIDE_W}" height="{SIDE_H}" fill="url(#ssky)"/><rect width="{SIDE_W}" height="{SIDE_H}" fill="url(#ssun)"/>'
            + sparkles + clouds + city + branch + blossoms + petals)
    return svg(SIDE_W, SIDE_H, body)


def _sakura_art(p):
    seq = _Seq(5)
    drift = "".join(_petal(seq.between(5, 235), seq.between(5, 295), seq.between(.9, 1.4), BLOSSOM, seq.between(0, 180), seq.between(.75, 1)) for _ in range(9))
    ribbon = ('<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#ff9ec8"/><stop offset=".5" stop-color="#c8b4ff"/>'
              '<stop offset="1" stop-color="#9cc8ff"/></linearGradient></defs>')
    band = (ribbon.replace("</defs>", '<filter id="b" x="-20%" y="-200%" width="140%" height="500%"><feGaussianBlur stdDeviation="2"/></filter></defs>')
            + '<rect y="7" width="240" height="6" fill="url(#g)" opacity=".55" filter="url(#b)"/><rect y="8.5" width="240" height="4" rx="2" fill="url(#g)"/>'
            + '<rect y="9" width="240" height="1.2" fill="#ffffff" fill-opacity=".7"/>'
            + _star4(40, 6, 5.5, "#ffd76e") + _star4(40, 6, 2, "#ffffff") + _star4(52, 3, 2.4, "#ff9ec8") + _star4(170, 7, 4.5, "#ffffff") + _blossom(110, 10, 4.5))
    divider = (ribbon + '<path d="M28 15 C70 6 110 22 150 12 C180 5 205 14 228 10" fill="none" stroke="url(#g)" stroke-width="4" stroke-linecap="round"/>'
               + _star4(14, 13, 10, "#ffd76e") + _star4(14, 13, 4, "#ffffff") + _blossom(232, 10, 6) + _star4(120, 4, 3, "#ff9ec8"))
    emblem = ('<defs><radialGradient id="g" cx=".5" cy=".5" r=".5"><stop offset="0" stop-color="#fff6c8" stop-opacity=".95"/>'
              '<stop offset=".5" stop-color="#ffd6ea" stop-opacity=".55"/><stop offset="1" stop-color="#ffd6ea" stop-opacity="0"/></radialGradient></defs>'
              '<circle cx="60" cy="38" r="36" fill="url(#g)"/>' + _star4(60, 38, 26, "#ffd76e", thin=0.2) + _star4(60, 38, 12, "#ffffff", thin=0.2)
              + _star4(96, 14, 7, "#ff9ec8") + _star4(24, 60, 5, "#c8b4ff") + _star4(100, 62, 3.5, "#ffffff") + _blossom(20, 18, 7))
    shooting = ('<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#ffffff" stop-opacity="0"/>'
                '<stop offset="1" stop-color="#ffffff"/></linearGradient></defs>'
                '<path d="M0 30 L110 6" stroke="url(#g)" stroke-width="2.4" stroke-linecap="round"/>' + _star4(112, 6, 6, "#fff6c8"))
    corner = _star4(10, 10, 9, "#ffd76e") + _star4(10, 10, 3.5, "#ffffff") + _star4(19, 3, 2.6, "#ff9ec8")
    return {"backdrop": _sakura_backdrop(p), "petals": svg(240, 300, drift), "band": svg(240, 14, band), "divider": svg(240, 26, divider),
            "emblem": svg(120, 76, emblem), "shooting": svg(124, 36, shooting), "corner": svg(22, 22, corner),
            "bullet": svg(12, 12, _blossom(6, 6, 5.4)), "marker": svg(14, 14, _star4(7, 7, 6.5, "#ffb000") + _star4(7, 7, 2.5, "#ffffff"))}


def _sakura_tokens(p):
    veil = glass_alpha(p, p["panel"])
    return {"tb-sky-top": "#8fb6ff", "tb-glass": _rgba(p["panel"], veil), "tb-veil": _rgba(p["panel"], veil),
            "tb-log-a": tint(p, "background", "#fdf8fc"), "tb-log-b": tint(p, "background", "#f7f2fd"),
            "tb-head-a": tint(p, "panel", "#fff1f8"), "tb-head-b": tint(p, "panel", "#f3efff"), "tb-head-c": tint(p, "panel", "#eef5ff")}


def _sakura_css(skin):
    f = _f()
    out = _backdrop() + _plates(skin["corners"], "box-shadow:inset 0 0 0 1px rgba(255,126,182,.28),0 2px 8px rgba(120,80,170,.12)")
    out += f._rule((f.HEADER,), "position:relative;background:linear-gradient(var(--tb-veil),var(--tb-veil)),"
                   "linear-gradient(90deg,#ffd6ea,#e3d9ff 50%,#cfe4ff);padding-bottom:20px")
    out += _layer(f.HEADER + "::after", "left:0;right:0;bottom:0;height:14px;background:var(--tb-art-band) 0 0/240px 14px repeat-x")
    out += f._rule(f.PANEL_HEADS, "background:linear-gradient(90deg,var(--tb-head-a),var(--tb-head-b) 50%,var(--tb-head-c));box-shadow:inset 0 -2px 0 #ffb7d5")
    out += f._rule(f.BRACKETED, "background:var(--tb-art-corner) right 10px top 10px/22px 22px no-repeat,"
                   "linear-gradient(90deg,#ff9ec8,#c8b4ff 50%,#9cc8ff) left top/100% 4px no-repeat,var(--panel)")
    out += f._rule(("#log", "#pane"), "background:linear-gradient(180deg,var(--tb-log-a),var(--tb-log-b))")
    out += _emblem(120, 76) + _divider(240, 26) + _marks(12, 14) + _scrollbars("#d8c6f0")
    return out


def _sakura_effects(skin):
    f = _f()
    out = "@keyframes tb-petalfall{from{background-position:0 0}to{background-position:-240px 600px}}\n"
    for side in f.SIDEBARS:
        out += _stage(side)
        out += _layer(side + "::before", "inset:0;z-index:-1;background:var(--tb-art-petals) 0 0/240px 300px repeat;opacity:.9;animation:tb-petalfall 38s linear infinite")
    out += "@keyframes tb-twinkle{0%,100%{transform:scale(1);opacity:1}50%{transform:scale(1.12);opacity:.75}}\n"
    out += f._rule((".empty-greet::before",), "animation:tb-twinkle 2.8s ease-in-out infinite")
    out += "@keyframes tb-shoot{0%,62%{transform:translate(-160px,40px);opacity:0}66%{opacity:1}78%{transform:translate(160px,-30px);opacity:0}100%{opacity:0}}\n"
    out += _stage(".empty")
    out += _layer(".empty::after", "left:50%;top:-34px;width:124px;height:36px;margin-left:-62px;z-index:-1;"
                  "background:var(--tb-art-shooting) center/124px 36px no-repeat;animation:tb-shoot 9s ease-out infinite")
    return out


def _sakura_under_text(p, tokens):
    return [worst_under_glass(p, p["panel"])] + [tokens[k] for k in ("tb-log-a", "tb-log-b", "tb-head-a", "tb-head-b", "tb-head-c")]


def _sakura_thumb(p, edge):
    art = _sakura_art(p)
    side = (f'<defs><clipPath id="sc"><rect width="88" height="180"/></clipPath></defs>'
            f'<g clip-path="url(#sc)"><g transform="translate(0 -150) scale(.3667)">{_inner(art["backdrop"])}</g></g>'
            + "".join(f'<rect x="6" y="{y}" width="76" height="12" rx="6" fill="#ffffff" fill-opacity=".9"/>' for y in (26, 42, 58)))
    head = ('<defs><linearGradient id="hb" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#ff9ec8"/><stop offset=".5" stop-color="#c8b4ff"/>'
            '<stop offset="1" stop-color="#9cc8ff"/></linearGradient></defs>'
            '<rect x="88" width="232" height="34" fill="#fdf6fb"/><rect x="88" y="30" width="232" height="3" fill="url(#hb)"/>'
            + _star4(112, 30, 3.5, "#ffd76e") + _star4(280, 30, 3, "#ff9ec8"))
    return "", side, head


# ---- Xeno Abyss: deep-space alien bio-tech --------------------------------------------------

XENO_GREEN, XENO_CYAN, XENO_MAGENTA, XENO_VIOLET = "#3dffb8", "#36e1ff", "#ff3df2", "#7a4bff"


def _glyph(x, y, s, kind, colour, width=1.3):
    """Original alien glyphs: arcs, bars and nodes. Not a real script."""
    g = {
        0: f"M{x} {y + 6 * s} L{x} {y - 6 * s} M{x - 4 * s} {y - 2 * s} A4 4 0 0 0 {x + 4 * s} {y - 2 * s}",
        1: f"M{x - 5 * s} {y + 5 * s} L{x} {y - 6 * s} L{x + 5 * s} {y + 5 * s} M{x - 3 * s} {y + 1 * s} L{x + 3 * s} {y + 1 * s}",
        2: f"M{x - 5 * s} {y} A5 5 0 1 1 {x + 5 * s} {y} M{x} {y} L{x} {y + 6 * s}",
        3: f"M{x - 5 * s} {y - 5 * s} L{x + 5 * s} {y - 5 * s} M{x} {y - 5 * s} L{x} {y + 5 * s} M{x - 5 * s} {y + 5 * s} L{x - 1 * s} {y + 1 * s}",
        4: f"M{x - 5 * s} {y + 4 * s} Q{x} {y - 8 * s} {x + 5 * s} {y + 4 * s} M{x - 2 * s} {y + 4 * s} L{x + 2 * s} {y + 4 * s}",
    }[kind % 5]
    dot = f'<circle cx="{x + 5 * s:.1f}" cy="{y - 5 * s:.1f}" r="{1.1 * s:.1f}" fill="{colour}"/>' if kind % 2 else ""
    return f'<path d="{g}" fill="none" stroke="{colour}" stroke-width="{width}" stroke-linecap="round"/>{dot}'


def _hexes(cols, rows, r, colour, opacity):
    out = []
    for row in range(rows):
        for col in range(cols):
            cx = col * r * 1.75 + (r * 0.875 if row % 2 else 0) + r
            cy = row * r * 1.52 + r
            pts = " ".join(f"{cx + r * math.cos(math.radians(60 * k + 30)):.1f},{cy + r * math.sin(math.radians(60 * k + 30)):.1f}" for k in range(6))
            out.append(f'<polygon points="{pts}"/>')
    return f'<g fill="none" stroke="{colour}" stroke-opacity="{opacity}" stroke-width="1">{"".join(out)}</g>'


def _tendrils(glow=True):
    curves = (("M20 900 C30 840 6 800 26 760 C40 732 28 712 40 690", XENO_GREEN, 2.6), ("M70 900 C64 860 92 830 80 790 C72 764 90 748 84 726", XENO_CYAN, 2),
              ("M150 900 C160 870 132 846 146 816", XENO_GREEN, 2.2), ("M206 900 C196 860 226 836 210 800 C200 778 214 760 208 742", XENO_CYAN, 2.4),
              ("M112 900 C118 882 104 866 116 850", XENO_MAGENTA, 1.6))
    tips = ((40, 690, XENO_GREEN), (84, 726, XENO_CYAN), (146, 816, XENO_GREEN), (208, 742, XENO_CYAN), (116, 850, XENO_MAGENTA))
    nodes = ((26, 760), (80, 790), (210, 800), (30, 812), (90, 848))
    out = "".join(f'<path d="{d}" fill="none" stroke="{c}" stroke-width="{w * 3.2:.1f}" stroke-opacity=".22" filter="url(#xblur)"/>' for d, c, w in curves) if glow else ""
    out += "".join(f'<path d="{d}" fill="none" stroke="{c}" stroke-width="{w}" stroke-linecap="round"/>' for d, c, w in curves)
    out += "".join(f'<circle cx="{x}" cy="{y}" r="7" fill="{c}" fill-opacity=".25" filter="url(#xblur)"/><circle cx="{x}" cy="{y}" r="3.2" fill="{c}"/>'
                   f'<circle cx="{x - 1}" cy="{y - 1}" r="1.1" fill="#ffffff"/>' for x, y, c in tips)
    out += "".join(f'<circle cx="{x}" cy="{y}" r="2" fill="{XENO_GREEN}" fill-opacity=".9"/>' for x, y in nodes)
    return out


_XENO_DEFS = ('<filter id="xblur" x="-80%" y="-80%" width="260%" height="260%"><feGaussianBlur stdDeviation="6"/></filter>'
              '<filter id="xsoft" x="-80%" y="-80%" width="260%" height="260%"><feGaussianBlur stdDeviation="22"/></filter>')


def _xeno_backdrop(p):
    seq = _Seq(3)
    defs = ('<defs>' + _XENO_DEFS + '<radialGradient id="xplanet" cx=".35" cy=".32" r=".75"><stop offset="0" stop-color="#2fd6c3"/>'
            '<stop offset=".45" stop-color="#0d5c66"/><stop offset="1" stop-color="#03141c"/></radialGradient>'
            '<linearGradient id="xring" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#ff3df2" stop-opacity=".1"/>'
            '<stop offset=".5" stop-color="#ffb3f6" stop-opacity=".85"/><stop offset="1" stop-color="#36e1ff" stop-opacity=".2"/></linearGradient></defs>')
    nebula = ('<circle cx="40" cy="250" r="120" fill="#b4249c" fill-opacity=".55" filter="url(#xsoft)"/>'
              '<circle cx="210" cy="420" r="130" fill="#0fa3a3" fill-opacity=".45" filter="url(#xsoft)"/>'
              '<circle cx="80" cy="600" r="120" fill="#4b2bb3" fill-opacity=".55" filter="url(#xsoft)"/>'
              '<circle cx="170" cy="120" r="80" fill="#2a2aa0" fill-opacity=".45" filter="url(#xsoft)"/>')
    flares = "".join(_star4(x, y, r, c, .95, thin=.12) for x, y, r, c in ((52, 130, 8, "#ffffff"), (196, 260, 6, XENO_CYAN), (34, 470, 5, "#ffd6fb"), (150, 560, 7, "#ffffff")))
    ring_back = '<path d="M86 790 A112 24 0 0 1 300 760" fill="none" stroke="url(#xring)" stroke-width="5" transform="rotate(-14 190 770)"/>'
    planet = ('<circle cx="190" cy="770" r="84" fill="#36e1ff" fill-opacity=".25" filter="url(#xblur)"/><circle cx="190" cy="770" r="70" fill="url(#xplanet)"/>'
              '<path d="M124 752 Q190 738 256 756 M126 784 Q190 774 252 790" fill="none" stroke="#7ff7ea" stroke-opacity=".25" stroke-width="3"/>')
    ring_front = '<path d="M86 790 A112 24 0 0 0 300 760" fill="none" stroke="url(#xring)" stroke-width="5" transform="rotate(-14 190 770)"/>'
    moon = '<circle cx="54" cy="660" r="11" fill="#5b4f8f"/><path d="M45 662 A11 11 0 0 0 63 664 A13 9 0 0 1 45 662 Z" fill="#2a2148"/>'
    spores = "".join(f'<circle cx="{seq.between(8, 232):.1f}" cy="{seq.between(640, 880):.1f}" r="{seq.between(1, 2.2):.1f}" fill="{XENO_GREEN}" fill-opacity="{seq.between(.4, .9):.2f}"/>' for _ in range(18))
    body = (defs + f'<rect width="{SIDE_W}" height="{SIDE_H}" fill="#04050b"/>' + nebula
            + _stars(seq, 90, 4, 880, ("#ffffff", "#bff6ff", "#ffd6fb", "#d6ffef"), 1.2) + flares + moon
            + ring_back + planet + ring_front + _tendrils() + spores)
    return svg(SIDE_W, SIDE_H, body)


def _xeno_art(p):
    glyph_line = "".join(_glyph(10 + 20 * i, 8, .8, i, XENO_CYAN, 1.1) for i in range(5))
    band = ('<defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#36e1ff" stop-opacity="0"/>'
            '<stop offset=".6" stop-color="#36e1ff" stop-opacity=".35"/><stop offset="1" stop-color="#36e1ff" stop-opacity="0"/></linearGradient></defs>'
            f'<rect y="8" width="100" height="8" fill="url(#g)"/><rect y="14" width="100" height="1.2" fill="{XENO_CYAN}"/>' + glyph_line)
    divider = (f'<defs>{_XENO_DEFS}</defs>'
               f'<path d="M30 14 L210 14" stroke="{XENO_GREEN}" stroke-width="1.4"/><path d="M30 14 L210 14" stroke="{XENO_GREEN}" stroke-width="6" stroke-opacity=".35" filter="url(#xblur)"/>'
               f'<circle cx="16" cy="14" r="9" fill="none" stroke="{XENO_GREEN}" stroke-width="1.6"/><circle cx="16" cy="14" r="3.6" fill="{XENO_GREEN}"/>'
               + "".join(_glyph(60 + 34 * i, 14, .75, i + 1, XENO_CYAN, 1.1) for i in range(4)) + f'<circle cx="214" cy="14" r="2.4" fill="{XENO_MAGENTA}"/>')
    emblem = (f'<defs>{_XENO_DEFS}<radialGradient id="o" cx=".42" cy=".38" r=".7"><stop offset="0" stop-color="#d6fff2"/>'
              f'<stop offset=".35" stop-color="{XENO_GREEN}"/><stop offset=".75" stop-color="#0b6b5a"/><stop offset="1" stop-color="#021512"/></radialGradient></defs>'
              f'<circle cx="60" cy="42" r="34" fill="{XENO_GREEN}" fill-opacity=".3" filter="url(#xblur)"/>'
              f'<ellipse cx="60" cy="42" rx="54" ry="12" fill="none" stroke="{XENO_MAGENTA}" stroke-opacity=".7" stroke-width="2" transform="rotate(-16 60 42)"/>'
              f'<circle cx="60" cy="42" r="22" fill="url(#o)"/><ellipse cx="60" cy="42" rx="4" ry="15" fill="#021512"/>'
              f'<ellipse cx="60" cy="42" rx="1.6" ry="9" fill="{XENO_CYAN}"/>'
              f'<circle cx="60" cy="42" r="27" fill="none" stroke="{XENO_CYAN}" stroke-opacity=".6" stroke-width="1" stroke-dasharray="3 5"/>')
    orbiters = f'<circle cx="6" cy="6" r="3" fill="{XENO_CYAN}"/><circle cx="6" cy="6" r="6" fill="{XENO_CYAN}" fill-opacity=".25"/>'
    corner = _hexes(4, 4, 7, XENO_CYAN, ".28")
    glow = svg(SIDE_W, SIDE_H, f'<defs>{_XENO_DEFS}</defs>' + _tendrils())
    scan = ('<defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#36e1ff" stop-opacity="0"/>'
            '<stop offset=".9" stop-color="#36e1ff" stop-opacity=".16"/><stop offset="1" stop-color="#d6fbff" stop-opacity=".45"/></linearGradient></defs>'
            '<rect width="240" height="60" fill="url(#g)"/>')
    return {"backdrop": _xeno_backdrop(p), "glow": glow, "scan": svg(240, 60, scan), "band": svg(100, 18, band),
            "divider": svg(230, 28, divider), "emblem": svg(120, 84, emblem), "orbiter": svg(12, 12, orbiters),
            "corner": svg(60, 54, corner), "bullet": svg(12, 12, _glyph(6, 6, .7, 2, XENO_GREEN, 1.4)),
            "marker": svg(14, 14, f'<circle cx="7" cy="7" r="6" fill="{XENO_GREEN}" fill-opacity=".25"/><circle cx="7" cy="7" r="3" fill="{XENO_GREEN}"/>')}


def _xeno_tokens(p):
    return {"tb-sky-top": "#04050b", "tb-glass": _rgba(p["panel"], glass_alpha(p, p["panel"])),
            "tb-veil": _rgba(p["panel"], glass_alpha(p, p["panel"]))}


def _xeno_css(skin):
    f = _f()
    out = _backdrop() + _plates(0, "box-shadow:inset 0 0 0 1px rgba(54,225,255,.16)")
    out += f._rule((f.HEADER,), "position:relative;background:linear-gradient(var(--tb-veil),var(--tb-veil)),"
                   "radial-gradient(ellipse 40% 140% at 15% 0%,#3a1450,transparent),radial-gradient(ellipse 40% 140% at 85% 0%,#063a44,transparent),#04050b;"
                   "padding-bottom:22px")
    out += _layer(f.HEADER + "::after", "left:0;right:0;bottom:0;height:18px;background:var(--tb-art-band) 0 0/100px 18px repeat-x")
    out += f._rule(f.PANEL_HEADS, "background:linear-gradient(180deg,rgba(54,225,255,.08),transparent),var(--panel-solid);box-shadow:inset 0 -1px 0 #36e1ff")
    out += f._rule(f.BRACKETED, "background:var(--tb-art-corner) right 2px top 2px/60px 54px no-repeat,"
                   "linear-gradient(180deg,rgba(54,225,255,.06),transparent 35%),var(--panel)")
    out += f._rule(("#log",), "background:radial-gradient(ellipse 60% 50% at 0% 100%,rgba(61,255,184,.07),transparent 70%),"
                   "radial-gradient(ellipse 50% 40% at 100% 0%,rgba(180,36,156,.08),transparent 70%),var(--chat)")
    out += f._rule(("h1", ".empty-greet", ".tb-title"), "letter-spacing:.12em;text-transform:uppercase;text-shadow:0 0 14px rgba(61,255,184,.35)")
    out += _emblem(120, 84) + _divider(230, 28) + _marks(12, 14) + _scrollbars("#24425a")
    return out


def _xeno_effects(skin):
    f = _f()
    out = "@keyframes tb-bio{0%,100%{opacity:.35}50%{opacity:1}}\n@keyframes tb-scan{0%{transform:translateY(-70px)}100%{transform:translateY(105vh)}}\n"
    for side in f.SIDEBARS:
        out += _stage(side)
        out += _layer(side + "::before", "inset:0;z-index:-1;background:var(--tb-art-glow) center bottom/100% auto no-repeat;animation:tb-bio 4.5s ease-in-out infinite")
        out += _layer(side + "::after", "left:0;right:0;top:0;height:60px;z-index:-1;background:var(--tb-art-scan) 0 0/100% 60px no-repeat;"
                      "animation:tb-scan 9s linear infinite")
    out += "@keyframes tb-orbit{from{transform:rotate(0deg) translateX(40px) rotate(0deg)}to{transform:rotate(360deg) translateX(40px) rotate(-360deg)}}\n"
    out += _stage(".empty")
    out += _layer(".empty::after", "left:50%;top:42px;width:12px;height:12px;margin:-6px 0 0 -6px;z-index:1;"
                  "background:var(--tb-art-orbiter) center/12px 12px no-repeat;animation:tb-orbit 5s linear infinite")
    out += "@keyframes tb-pulse{0%,100%{filter:drop-shadow(0 0 0 rgba(61,255,184,0))}50%{filter:drop-shadow(0 0 10px rgba(61,255,184,.55))}}\n"
    out += f._rule(f.PRIMARY, "animation:tb-pulse 3s ease-in-out infinite")
    out += f._rule((".empty-greet::before",), "animation:tb-pulse 3s ease-in-out infinite")
    return out


def _xeno_under_text(p, tokens):
    return [worst_under_glass(p, p["panel"]), mix(p["background"], XENO_GREEN, 0.07), mix(p["background"], "#b4249c", 0.08)]


def _xeno_thumb(p, edge):
    art = _xeno_art(p)
    side = (f'<defs><clipPath id="sc"><rect width="88" height="180"/></clipPath></defs>'
            f'<g clip-path="url(#sc)"><g transform="translate(0 -150) scale(.3667)">{_inner(art["backdrop"])}</g></g>'
            + "".join(f'<rect x="6" y="{y}" width="76" height="12" fill="{_rgba(p["panel"], .9)}" stroke="{XENO_CYAN}" stroke-opacity=".3"/>' for y in (26, 42, 58)))
    head = (f'<rect x="88" width="232" height="34" fill="#070a16"/><rect x="88" y="31" width="232" height="1.2" fill="{XENO_CYAN}"/>'
            + "".join(f'<g transform="translate({96 + 20 * i} 20) scale(.6)">{_glyph(0, 0, 1, i, XENO_CYAN, 1.6)}</g>' for i in range(11)))
    return "", side, head


ORNAMENTS = {
    "neon": {"art": _neon_art, "tokens": _neon_tokens, "css": _neon_css, "effects": _neon_effects, "thumb": _neon_thumb,
             "under_text": _neon_under_text},
    "sakura": {"art": _sakura_art, "tokens": _sakura_tokens, "css": _sakura_css, "effects": _sakura_effects, "thumb": _sakura_thumb,
               "under_text": _sakura_under_text},
    "xeno": {"art": _xeno_art, "tokens": _xeno_tokens, "css": _xeno_css, "effects": _xeno_effects, "thumb": _xeno_thumb,
             "under_text": _xeno_under_text},
}


def readable_everywhere(p, colours, floor=4.5):
    return all(contrast(c, ink) >= floor for c in colours for ink in (p["ink"], p["muted"]))
