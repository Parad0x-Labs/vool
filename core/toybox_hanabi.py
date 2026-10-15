"""Hanabi Sky: a real anime skin — the whole app floats as a cel-shaded card on
an illustrated festival-night wallpaper.

The wallpaper is code-drawn (sky gradient, stars, firework bursts, a lit
skyline, a river) fixed behind the app; the main column and sidebar become
outlined cards with hard cel shadows; every button is cel-shaded with a cut
corner; the live header draws a firework that bursts on verified completion.
All names and drawings are original; no existing artwork or brand is imitated.
"""

from __future__ import annotations

from core.i18n.catalog import load_catalog_data
from core.toybox import _example
from core.toybox_pixel import mascot_svg

IDENTIFIER = "hanabi-sky"


def _palette(light):
    if light:
        return dict(background="#ffeede", panel="#fff8ef", field="#ffefdf", ink="#33244d",
                    muted="#5d4a78", accent="#ff4f7d", accentInk="#4d0f26", border="#8a5f9e")
    return dict(background="#151a38", panel="#20265a", field="#1a2044", ink="#f6f2ff",
                muted="#d5d3f5", accent="#ff5f8f", accentInk="#2b0a18", border="#8f8ac2")


def _rgba(hex_color: str, alpha: float) -> str:
    value = hex_color.lstrip("#")
    r, g, b = int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


def _uri(svg: str) -> str:
    return "url(\"data:image/svg+xml," + svg.replace("<", "%3C").replace(">", "%3E").replace("#", "%23").replace('"', "'") + "\")"


def _burst(cx: float, cy: float, radius: int, color: str, rays: int = 12) -> str:
    """One firework: radiating rays with spark tips and a glowing core."""
    import math

    parts = [f"<circle cx='{cx}' cy='{cy}' r='3' fill='{color}'/>",
             f"<circle cx='{cx}' cy='{cy}' r='{radius}' fill='{color}' fill-opacity='.12'/>"]
    for index in range(rays):
        angle = math.pi * 2 * index / rays
        x2, y2 = cx + math.cos(angle) * radius, cy + math.sin(angle) * radius
        parts.append(f"<path d='M{cx} {cy}L{x2:.0f} {y2:.0f}' stroke='{color}' stroke-width='2'/>")
        for fraction in (0.7,):
            mx, my = cx + math.cos(angle) * radius * fraction, cy + math.sin(angle) * radius * fraction
            parts.append(f"<circle cx='{mx:.0f}' cy='{my:.0f}' r='1.6' fill='{color}'/>")
    return "".join(parts)


def _wallpaper(light: bool) -> str:
    """The full-viewport festival night (or dawn) behind the whole app."""
    if light:
        sky_top, sky_mid, glow, dark = "#ffc98e", "#ff9ebc", "#ff7a52", "#5d3555"
        water = "#e893b6"
    else:
        sky_top, sky_mid, glow, dark = "#0e1030", "#2a1e52", "#ff7b54", "#0a0b1e"
        water = "#1c2150"
    stars = "".join(f"<circle cx='{(index * 137 + 41) % 2000}' cy='{(index * 89 + 17) % 500}' r='{1 + index % 2}' fill='#ffffff' fill-opacity='{0.25 + (index % 5) * 0.12}'/>"
                    for index in range(40))
    skyline = "".join(f"<rect x='{x}' y='{640 - height}' width='{width}' height='{height}' fill='{dark}'/>"
                      f"<rect x='{x + 6}' y='{644 - height}' width='4' height='5' fill='{glow}' fill-opacity='.9'/>"
                      for x, width, height in ((0, 90, 120), (110, 70, 190), (195, 110, 150), (320, 60, 230),
                                               (395, 120, 170), (530, 80, 210), (625, 130, 140), (770, 70, 250),
                                               (855, 110, 180), (1085, 140, 150), (1240, 80, 260), (1470, 100, 230),
                                               (1585, 150, 160), (0, 2000, 60)))
    bursts = (_burst(420, 210, 95, "#ff5f8f", 14) + _burst(1150, 150, 70, "#56e0ff", 10)
              + _burst(1700, 260, 85, "#ffd76b", 12) + _burst(880, 90, 45, "#b78aff", 8))
    if light:
        bursts = (_burst(420, 200, 95, "#e83a68", 14) + _burst(1500, 160, 75, "#ff8c1a", 12)
                  + _burst(900, 120, 55, "#d94f9e", 10))
    river = (f"<rect x='0' y='660' width='2000' height='440' fill='{water}'/>"
             f"<path d='M0 700h2000M0 760h2000M0 830h2000' stroke='{glow}' stroke-opacity='.25' stroke-width='6'/>"
             f"<path d='M420 250v420' stroke='#ff5f8f' stroke-opacity='.18' stroke-width='26'/>"
             f"<path d='M1150 180v480' stroke='#56e0ff' stroke-opacity='.16' stroke-width='20'/>")
    moon = f"<circle cx='1790' cy='130' r='52' fill='{glow}' fill-opacity='.9'/><circle cx='1772' cy='118' r='44' fill='{sky_mid}'/>"
    svg = (f"<svg xmlns='http://www.w3.org/2000/svg' width='2000' height='1100' preserveAspectRatio='xMidYMid slice'>"
           f"<defs><linearGradient id='s' x1='0' y1='0' x2='0' y2='1'>"
           f"<stop offset='0' stop-color='{sky_top}'/><stop offset='.55' stop-color='{sky_mid}'/>"
           f"<stop offset='.78' stop-color='{glow}' stop-opacity='.55'/><stop offset='1' stop-color='{sky_mid}'/></linearGradient></defs>"
           f"<rect width='2000' height='1100' fill='url(%23s)'/>{stars}{moon}{bursts}{skyline}{river}</svg>")
    return _uri(svg)


def _star(size: int, color: str, ink: str) -> str:
    half = size // 2
    return _uri(f"<svg xmlns='http://www.w3.org/2000/svg' width='{size}' height='{size}' shape-rendering='crispEdges'>"
                f"<path d='M{half} 1L{size - 1} {half}L{half} {size - 1}L1 {half}Z' fill='{color}' stroke='{ink}' stroke-width='2'/>"
                f"<path d='M{half} {half - 4}v8M{half - 4} {half}h8' stroke='{ink}' stroke-width='2'/></svg>")


def _log_art() -> str:
    art = (_burst(160, 120, 60, "#ff5f8f", 12) + _burst(480, 200, 46, "#56e0ff", 10)
          + _burst(320, 60, 34, "#ffd76b", 8)
          + "".join(f"<circle cx='{(index * 173 + 60) % 640}' cy='{(index * 97 + 30) % 260}' r='1.5' fill='#ffffff' fill-opacity='.5'/>"
                    for index in range(24)))
    return _uri(f"<svg xmlns='http://www.w3.org/2000/svg' width='640' height='260'>{art}</svg>")


def chrome(identifier, p: dict) -> str:
    from core.toybox_fragment import GUARD

    if identifier != IDENTIFIER:
        return ""
    # chrome() is called once per palette (dark, light); everything ships on the
    # dark pass and the light pass adds nothing, so base rules are never re-emitted
    # with the other palette's colours.
    if p["background"] != "#151a38":
        return ""
    dark = _palette(False)
    light = _palette(True)
    line = "var(--tb-line)"
    shadow_line = "var(--tb-shadow)"
    out = (":root{--tb-wall:" + _wallpaper(False) + ";--tb-wall-light:" + _wallpaper(True)
           + ";--tb-line:#f6f2ff;--tb-shadow:#0a0b1e;--tb-glass:" + _rgba(dark["panel"], 0.85)
           + ";--tb-log-art:" + _log_art() + ";--tb-empty:" + _rgba(dark["panel"], 0.8) + "}\n")
    out += (':root[data-theme="light"]{--tb-line:#3a2440;--tb-shadow:#3a2440;--tb-glass:' + _rgba(light["panel"], 0.88)
            + ";--tb-empty:" + _rgba(light["panel"], 0.85) + "}\n")
    out += ('@media(prefers-color-scheme:light){:root[data-theme="system"]{--tb-line:#3a2440;--tb-shadow:#3a2440;--tb-glass:'
            + _rgba(light["panel"], 0.88) + ";--tb-empty:" + _rgba(light["panel"], 0.85) + "}}\n")
    out += "body{background:transparent!important}\n"
    out += ("#log" + GUARD + "{position:relative}\n")
    out += ("#log::before{content:'';position:absolute;inset:0;background-image:var(--tb-log-art);"
            "background-repeat:repeat-y;background-size:640px 260px;opacity:.5;pointer-events:none}\n")
    out += ".empty" + GUARD + "::before{height:250px!important}\n"
    glass = "var(--tb-glass)"
    out += (":is(#main,#log,#sidebar,#side,#pane,#xpanel,.set-section,.tb-root,#main > header,#main > footer)" + GUARD
            + "{background-color:" + glass + "!important}\n")
    out += ("body::before{content:'';position:fixed;inset:0;z-index:-1;background-image:var(--tb-wall);"
            "background-size:cover;background-position:center}\n")
    out += ':root[data-theme="light"] body::before{background-image:var(--tb-wall-light)}\n'
    out += '@media(prefers-color-scheme:light){:root[data-theme="system"] body::before{background-image:var(--tb-wall-light)}}' + "\n"
    subject = ":is(button,select,.tb-btn,.ctrl-btn,.set-btn,#send,#newChat,#searchBtn,#panelBtn,#modelBtn,#modeBtn,#cloudBtn,.side-foot-btn,.home-options button,.tb-options select)" + GUARD
    out += (subject + "{appearance:none;-webkit-appearance:none;background-color:var(--panel);border:3px solid " + line
            + ";border-radius:10px;box-shadow:0 4px 0 " + line + ",inset 0 2px 0 rgba(255,255,255,.35);"
            "min-height:36px;color:var(--ink);font-weight:700;"
            "clip-path:polygon(0 0,calc(100% - 16px) 0,100% 16px,100% 100%,16px 100%,0 calc(100% - 16px));border-width:3px;"
            "transition:transform .12s ease,box-shadow .12s ease}\n")
    out += subject + ":hover{transform:translateY(-2px);box-shadow:0 6px 0 " + shadow_line + "}\n"
    out += subject + ":active:not(:disabled){transform:translateY(2px);box-shadow:0 1px 0 " + shadow_line + "}\n"
    out += subject + ":focus-visible{outline:3px solid var(--accent);outline-offset:3px}\n"
    out += (":is(#send,.tb-primary)" + GUARD + "{background-color:var(--accent);color:var(--accent-ink);"
            "border-color:" + line + ";box-shadow:0 5px 0 " + shadow_line + ",inset 0 2px 0 rgba(255,255,255,.4)}\n")
    out += ('@keyframes tb-pop{0%{transform:scale(.96)}100%{transform:scale(1)}}\n')
    out += ("textarea" + GUARD + ",input:not([type=checkbox]):not([type=range]):not([type=radio])" + GUARD +
            "{background-color:var(--field);border:3px solid " + line + ";border-radius:12px;"
            "box-shadow:inset 0 3px 0 rgba(0,0,0,.25)}\n")
    out += ("input[type=checkbox]" + GUARD + "{appearance:none;-webkit-appearance:none;width:24px;height:24px;"
            "background-color:var(--panel);border:3px solid " + line + ";border-radius:6px;box-shadow:0 3px 0 " + line + "}\n")
    out += ("input[type=checkbox]:checked" + GUARD + "{background-color:var(--accent);"
            "background-image:" + _star(24, "none", "x") + "}\n")
    out += ("input[type=checkbox]:checked" + GUARD + "{background-image:none;"
            "box-shadow:0 3px 0 " + shadow_line + ",inset 0 0 0 3px var(--accent),inset 0 0 0 6px var(--accent-ink)}\n")
    out += ("input[type=range]" + GUARD + "{appearance:none;-webkit-appearance:none;height:22px;background-color:transparent;"
            "border:0;background-image:linear-gradient(" + line + "," + line + ");"
            "background-size:100% 4px;background-position:center;background-repeat:no-repeat}\n")
    for pseudo in ("::-webkit-slider-thumb", "::-moz-range-thumb"):
        out += ("input[type=range]" + GUARD + pseudo + "{appearance:none;-webkit-appearance:none;width:22px;height:22px;"
                "background-color:var(--accent);border:3px solid " + line + ";border-radius:50%;"
                "box-shadow:0 3px 0 " + shadow_line + ",inset 0 2px 0 rgba(255,255,255,.45);cursor:pointer}\n")
    for scoped in ("#main", "#sidebar", "#pane"):
        out += (scoped + GUARD + "::-webkit-scrollbar{width:12px}"
                + scoped + GUARD + "::-webkit-scrollbar-thumb{background-color:var(--accent);border:3px solid " + line + ";border-radius:8px}"
                + scoped + GUARD + "::-webkit-scrollbar-track{background:var(--panel)}\n")
    # The whole app floats as a cel card over the wallpaper.
    out += ("#main" + GUARD + "{margin:16px 18px;border:3px solid " + line + ";border-radius:20px;"
            "box-shadow:0 10px 0 " + shadow_line + ",0 24px 60px rgba(6,6,20,.55);overflow:hidden}\n")
    out += (":is(#sidebar,#side,#xpanel,#pane)" + GUARD +
            "{border:3px solid " + line + ";border-radius:18px;box-shadow:0 8px 0 " + shadow_line + ";margin-block:12px}\n")
    out += (":is(#sidebar,#xpanel)" + GUARD + "{margin-inline:12px}\n")
    out += ("#main > header" + GUARD + "{background-image:var(--tb-stage-header);background-repeat:repeat-x;"
            "background-size:auto 44px;background-position:center;border-block-end:3px solid " + line + ";padding-block-start:48px}\n")
    out += ("#main > footer" + GUARD + "{border-block-start:3px solid " + line + ";background-color:var(--panel)}\n")
    out += ("#sessions .session.active" + GUARD + "{border:2px solid var(--accent);border-radius:10px;"
            "box-shadow:0 3px 0 " + shadow_line + ";background-color:var(--panel)}\n")
    out += ("#newChat" + GUARD + "{background-color:var(--accent);color:var(--accent-ink)}\n")
    out += ".empty" + GUARD + "{border:3px solid " + line + ";border-radius:18px;box-shadow:0 8px 0 " + shadow_line + ";background-color:var(--tb-empty)}\n"
    out += ":root[data-vool-motion='off'] " + subject + "{transition:none}\n"
    out += "@media(prefers-reduced-motion:reduce){" + subject + "{transition:none!important}}\n"
    return out


def artwork(identifier, p):
    if identifier != IDENTIFIER:
        return {}
    edge, accent = p["border"], p["accent"]

    skyline = "".join(f"<rect x='{x}' y='{58 - height}' width='{width}' height='{height}' fill='{edge}'/>"
                      f"<rect x='{x + 5}' y='{60 - height}' width='3' height='4' fill='{accent}'/>"
                      for x, width, height in ((0, 46, 16), (60, 30, 24), (100, 40, 20), (150, 26, 28), (190, 44, 18),
                                               (250, 30, 26), (300, 50, 20), (360, 28, 24), (410, 46, 18),
                                               (470, 32, 26), (520, 50, 20), (580, 40, 24)))
    header = (f"<svg xmlns='http://www.w3.org/2000/svg' width='640' height='64' shape-rendering='crispEdges'>"
              f"<rect width='640' height='64' fill='{p['background']}'/>"
              f"<path d='M0 0h640v14H0Z' fill='{p['panel']}'/>"
              + _burst(110, 18, 20, accent, 12) + _burst(330, 14, 14, p["muted"], 8) + _burst(530, 20, 22, accent, 12)
              + skyline + "</svg>")
    bridge = (f"<path d='M60 196q260 -70 520 0v10q-260 -60 -520 0Z' fill='{edge}'/>"
              f"<path d='M80 206v22M180 206v22M460 206v22M560 206v22' stroke='{edge}' stroke-width='6'/>")
    lanterns = "".join(f"<path d='M{40 + index * 90} 26q6 10 0 20' stroke='{edge}' stroke-width='2'/>"
                       f"<rect x='{34 + index * 90}' y='44' width='14' height='18' rx='5' fill='{accent}'/>"
                       for index in range(7))
    water = (f"<path d='M0 232h640v8H0Z' fill='{edge}' fill-opacity='.5'/>"
             f"<path d='M120 244q60 8 120 0M420 244q60 8 120 0' stroke='{accent}' stroke-width='3' fill='none'/>")
    mascots = ("<g transform='translate(150 118) scale(1.9)'>" + mascot_svg("lumen-fox", "happy", {"border": edge, "accent": accent}) + "</g>"
               "<g transform='translate(370 116) scale(1.9)'>" + mascot_svg("dusk-lynx", "surprise", {"border": edge, "accent": accent}) + "</g>")
    empty = (f"<svg xmlns='http://www.w3.org/2000/svg' width='640' height='240' shape-rendering='crispEdges'>"
             f"<rect width='640' height='240' fill='{p['panel']}'/>"
             f"<rect x='6' y='6' width='628' height='228' fill='none' stroke='{edge}' stroke-width='3' stroke-dasharray='14 8'/>"
             + _burst(110, 70, 42, accent, 12) + _burst(530, 60, 36, p["muted"], 10)
             + lanterns + bridge + water + mascots + "</svg>")
    return {"header": header, "empty": empty}


_strings = load_catalog_data("en")["messages"]
_skin = _example(IDENTIFIER, _strings[f"toybox.skin_{IDENTIFIER}_name"],
                 _strings[f"toybox.skin_{IDENTIFIER}_description"], _palette(False),
                 "capsule", "none", 12, 3, "rounded-heading", "2.0", "1.0.0")
_skin["palettes"] = {"dark": _palette(False), "light": _palette(True)}
_skin["stage"] = {"layout": "device", "controls": "brush", "type": "rounded-display",
                  "frame": {"set": "bezel"}, "scene": {"id": "stars", "density": "lively"},
                  "reactive": {"profile": "glow", "accentFrom": "accent"}}
SKINS = (_skin,)
