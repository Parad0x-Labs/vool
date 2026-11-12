"""Ink Tide: the first whole-skin — original woodblock-print style art for the
chrome itself, not a recolour of the app's controls.

Everything is drawn here as code: buttons, selects, checkboxes, sliders,
scrollbars and text fields carry their own paper-and-ink art; the main column
hangs in a scroll frame with wooden rods, and the header and footer take a
torn-paper tide edge instead of a rectangle. Names and drawings are original;
no existing artwork, artist or brand is referenced or imitated.
"""

from __future__ import annotations

from core.i18n.catalog import load_catalog_data
from core.toybox import _example
from core.toybox_pixel import mascot_svg

IDENTIFIER = "ink-tide"


def _palette(light):
    if light:
        return dict(background="#f1e9d6", panel="#faf4e4", field="#f7efdc", ink="#2b3242",
                    muted="#5c6172", accent="#b4451f", accentInk="#fff3df", border="#7d8496")
    return dict(background="#1d2430", panel="#262f3d", field="#202836", ink="#f2ead8",
                muted="#b8ae97", accent="#d9822b", accentInk="#2a1608", border="#6f7d8f")


def _uri(svg: str) -> str:
    return "url(\"data:image/svg+xml," + svg.replace("<", "%3C").replace(">", "%3E").replace("#", "%23").replace('"', "'") + "\")"


def _chip(p: dict) -> str:
    """A pressed-paper button chip: torn deckle edge, ink outline, grain lines."""
    grain = "".join(f"<path d='M4 {y}h44' stroke='{p['border']}' stroke-opacity='.25' stroke-width='1' stroke-dasharray='7 5'/>"
                    for y in (14, 20, 26))
    body = (f"<rect x='2' y='2' width='52' height='30' fill='{p['field']}' stroke='{p['border']}' stroke-width='2'/>"
            f"<path d='M2 6l3-4 3 3 4-4 3 3 5-3 3 3 4-3 3 3 5-3 3 3 4-3 3 3 4-2' fill='none' stroke='{p['field']}' stroke-width='3'/>"
            f"<path d='M2 29l4 3 3-3 4 3 3-3 5 3 3-3 4 3 3-3 5 3 3-3 4 3 3-2 4-1' fill='none' stroke='{p['field']}' stroke-width='3'/>"
            f"<path d='M4 4h48v26H4Z' fill='none' stroke='{p['border']}' stroke-opacity='.4'/>{grain}")
    return _uri(f"<svg xmlns='http://www.w3.org/2000/svg' width='56' height='34' shape-rendering='crispEdges'>{body}</svg>")


def _stamp(p: dict, on: bool) -> str:
    """A square seal: empty groove off, vermillion wave mark on."""
    mark = (f"<path d='M6 15c2-3 5-3 7 0s5 3 7 0 5-3 7 0' fill='none' stroke='{p['accentInk']}' stroke-width='2'/>"
            f"<path d='M6 20c2-3 5-3 7 0s5 3 7 0 5-3 7 0' fill='none' stroke='{p['accentInk']}' stroke-width='2'/>") if on else ""
    body = (f"<rect x='2' y='2' width='26' height='26' fill='{p['field']}' stroke='{p['border']}' stroke-width='2'/>"
            f"<rect x='5' y='5' width='20' height='20' fill='none' stroke='{p['border']}' stroke-opacity='.5'/>{mark}")
    if on:
        body = (f"<rect x='2' y='2' width='26' height='26' fill='{p['accent']}' stroke='{p['border']}' stroke-width='2'/>"
                f"<rect x='5' y='5' width='20' height='20' fill='none' stroke='{p['accentInk']}' stroke-opacity='.6'/>{mark}")
    return _uri(f"<svg xmlns='http://www.w3.org/2000/svg' width='30' height='30' shape-rendering='crispEdges'>{body}</svg>")


def _track(p: dict) -> str:
    """A brush stroke slider track: one imperfect ink line with dry-brush gaps."""
    body = (f"<path d='M2 8c8-4 18 3 28 1s20-5 28-1' fill='none' stroke='{p['border']}' stroke-width='4' stroke-linecap='round'/>"
            f"<path d='M10 8h2M24 8h3M40 8h2' stroke='{p['panel']}' stroke-width='4'/>")
    return _uri(f"<svg xmlns='http://www.w3.org/2000/svg' width='60' height='16'>{body}</svg>")


def _paper(p: dict) -> str:
    """Laid paper for text fields: faint chain lines across the sheet."""
    lines = "".join(f"<path d='M0 {y}h56' stroke='{p['border']}' stroke-opacity='.16'/>" for y in range(4, 30, 6))
    body = f"<rect width='56' height='30' fill='{p['field']}'/>{lines}<path d='M0 2h56' stroke='{p['border']}' stroke-opacity='.5'/>"
    return _uri(f"<svg xmlns='http://www.w3.org/2000/svg' width='56' height='30'>{body}</svg>")


def _rod(p: dict) -> str:
    """The wooden hanging rod of a scroll: slats, cord loops, end caps."""
    body = (f"<rect x='0' y='2' width='64' height='8' fill='{p['border']}'/>"
            f"<path d='M0 2h64M0 10h64' stroke='{p['ink']}' stroke-opacity='.5'/>"
            f"<path d='M8 2v8M20 2v8M32 2v8M44 2v8M56 2v8' stroke='{p['ink']}' stroke-opacity='.25'/>"
            f"<rect x='0' y='0' width='4' height='12' fill='{p['muted']}'/><rect x='60' y='0' width='4' height='12' fill='{p['muted']}'/>")
    return _uri(f"<svg xmlns='http://www.w3.org/2000/svg' width='64' height='12' shape-rendering='crispEdges'>{body}</svg>")


def _frame(p: dict) -> str:
    """Scroll frame edges: rods top and bottom, deckle strip on the sides."""
    top = (f"<rect x='0' y='0' width='48' height='12' fill='{p['border']}'/>"
           f"<path d='M0 0h48M0 12h48' stroke='{p['ink']}' stroke-opacity='.5'/>"
           f"<path d='M6 0v12M18 0v12M30 0v12M42 0v12' stroke='{p['ink']}' stroke-opacity='.25'/>")
    side = f"<rect x='0' y='12' width='4' height='4' fill='{p['muted']}'/>"
    body = top + side + f"<g transform='translate(0 16)'>{top}</g>"
    return _uri(f"<svg xmlns='http://www.w3.org/2000/svg' width='48' height='28' shape-rendering='crispEdges'>{body}</svg>")


def _seal(p: dict, size: int) -> str:
    mark = (f"<path d='M5 {size-6}c2-3 5-3 7 0s5 3 7 0' fill='none' stroke='{p['accentInk']}' stroke-width='2'/>"
            f"<path d='M7 7h6v6H7Z' fill='{p['accentInk']}'/>")
    body = f"<rect x='1' y='1' width='{size-2}' height='{size-2}' fill='{p['accent']}'/>{mark}"
    return _uri(f"<svg xmlns='http://www.w3.org/2000/svg' width='{size}' height='{size}' shape-rendering='crispEdges'>{body}</svg>")


def chrome(identifier, p: dict) -> str:
    """The whole-control redrawn chrome, guarded like every other skin rule."""
    from core.toybox_fragment import GUARD

    if identifier != IDENTIFIER:
        return ""
    # one emit only: the light-palette pass would re-issue every base rule with
    # the light art for both modes; both mode palettes ship as variables instead.
    if p["background"] != "#1d2430":
        return ""

    surface = ":is(#main,#sidebar,#side,#pane,#xpanel,.tb-root)" + GUARD
    light = _palette(True)
    out = (surface + "{--tb-chip:" + _chip(p) + ";--tb-stamp:" + _stamp(p, False) + ";--tb-stamp-on:" + _stamp(p, True)
           + ";--tb-seal:" + _seal(p, 30) + ";--tb-track:" + _track(p) + ";--tb-paper:" + _paper(p)
           + ";--tb-rod:" + _rod(p) + ";--tb-frame:" + _frame(p) + ";}\n")
    light_vars = "--tb-chip:" + _chip(light) + ";--tb-stamp-on:" + _stamp(light, True) + ";--tb-seal:" + _seal(light, 30) + ";"
    out += ':root[data-theme="light"] ' + surface + "{" + light_vars + "}\n"
    out += '@media(prefers-color-scheme:light){:root[data-theme="system"] ' + surface + "{" + light_vars + "}}\n"
    subject = ":is(button,select,.tb-btn,.ctrl-btn,.set-btn,#send,#newChat,#searchBtn,#panelBtn,#modelBtn,#modeBtn,#cloudBtn,.side-foot-btn,.home-options button,.tb-options select)" + GUARD
    out += (subject + "{appearance:none;-webkit-appearance:none;background-color:var(--field);"
            "border:2px solid var(--border);border-image:var(--tb-chip) 12 stretch;"
            "border-radius:4px 8px 4px 9px;box-shadow:2px 3px 0 var(--tb-rim-dim,transparent);"
            "min-height:34px;color:var(--ink);padding-inline-end:30px}\n")
    out += subject + ":hover{border-color:var(--accent)}\n"
    out += subject + ":active:not(:disabled){transform:translateY(2px);box-shadow:none}\n"
    out += subject + ":focus-visible{outline:3px solid var(--tb-focus,var(--border));outline-offset:3px}\n"
    out += (":is(#send,.tb-primary)" + GUARD + "{background-color:var(--accent);color:var(--accent-ink);font-weight:700}\n")
    out += ("textarea" + GUARD + ",input:not([type=checkbox]):not([type=range]):not([type=radio])" + GUARD +
            "{background-color:var(--field);background-image:var(--tb-paper);background-repeat:repeat;"
            "background-size:56px 30px;border:2px solid var(--border);border-image:var(--tb-paper) 8 stretch;color:var(--ink)}\n")
    out += ("input[type=checkbox]" + GUARD + "{appearance:none;-webkit-appearance:none;width:22px;height:22px;"
            "background-color:var(--field);background-image:var(--tb-stamp);background-size:100% 100%;border:0;box-shadow:none}\n")
    out += ("input[type=checkbox]:checked" + GUARD + "{background-image:var(--tb-stamp-on)}\n")
    out += ("input[type=range]" + GUARD + "{appearance:none;-webkit-appearance:none;height:20px;"
            "background-color:transparent;background-image:var(--tb-track);background-repeat:repeat-x;"
            "background-size:60px 16px;background-position:center;border:0}\n")
    for pseudo in ("::-webkit-slider-thumb", "::-moz-range-thumb"):
        out += ("input[type=range]" + GUARD + pseudo + "{appearance:none;-webkit-appearance:none;width:22px;height:22px;"
                "background-image:var(--tb-seal);background-size:100% 100%;border:0;cursor:pointer}\n")
    for scoped in ("#main", "#sidebar", "#pane"):
        out += (scoped + GUARD + "::-webkit-scrollbar{width:12px}"
                + scoped + GUARD + "::-webkit-scrollbar-track{background:var(--panel);background-image:var(--tb-paper);background-size:56px 30px}\n"
                + scoped + GUARD + "::-webkit-scrollbar-thumb{background-color:var(--border);background-image:var(--tb-rod);"
                "background-size:64px 12px;border:2px solid var(--panel);border-radius:2px}\n")
    # The scroll silhouette: rods carry the header and footer; the tide edge tears
    # the rectangle; the main column hangs inside a scroll frame. Only the outer edge
    # tears: each clip runs a full screen past the inner edge, so the menus that open
    # out of the header (model picker) and the footer (mode menu) are never cut off.
    out += ("#main > header" + GUARD + "{clip-path:polygon(0 12px,3% 4px,7% 10px,12% 2px,18% 9px,26% 3px,34% 11px,"
            "43% 5px,52% 12px,61% 4px,70% 10px,79% 3px,87% 9px,94% 4px,100% 12px,100% calc(100% + 100vh),0 calc(100% + 100vh));"
            "background-image:var(--tb-stage-header);background-repeat:repeat-x;"
            "background-size:auto 44px;background-position:center top;"
            "border-block-start:12px solid transparent;border-image:var(--tb-frame) 12 4 round;padding-block-start:56px}\n")
    # The clip makes the header its own layer, so an open header menu would sit under the
    # composer on a short window; while one is open the header takes the menu's own rank.
    out += ("#main > header" + GUARD + ":has(.popover.open){z-index:30}\n")
    out += ("#main > header" + GUARD + "::after{content:'';position:absolute;inset-block-start:44px;left:0;right:0;height:12px;"
            "background-image:var(--tb-rod);background-repeat:repeat-x;background-size:auto 12px;pointer-events:none}\n")
    out += ("#main > footer" + GUARD + "{clip-path:polygon(0 -100vh,100% -100vh,100% calc(100% - 10px),95% 100%,88% calc(100% - 7px),"
            "80% 100%,72% calc(100% - 9px),63% 100%,55% calc(100% - 6px),46% 100%,38% calc(100% - 8px),29% 100%,"
            "21% calc(100% - 6px),12% 100%,5% calc(100% - 8px),0 100%);"
            "border-block-end:12px solid transparent;border-image:var(--tb-frame) 12 4 round}\n")
    out += ("#main > footer" + GUARD + "::after{content:'';position:absolute;inset-block-end:0;left:0;right:0;height:12px;"
            "background-image:var(--tb-rod);background-repeat:repeat-x;background-size:auto 12px;pointer-events:none}\n")
    out += ("#main" + GUARD + "{border-inline:4px solid transparent;border-image:var(--tb-frame) 12 4 round}\n")
    out += "@media(prefers-reduced-motion:reduce){" + subject + "{transition:none!important}}\n"
    return out


def artwork(identifier, p):
    if identifier != IDENTIFIER:
        return {}
    edge, accent, field = p["border"], p["accent"], p["field"]

    def waves(width, base, count):
        """Banded tide lines: one smooth stroke per band, foam as crisp ticks."""
        parts = []
        for step in range(count):
            y = base + step * 7
            amp = max(3, 10 - step * 2)
            runs = f"M0 {y}"
            x = 0
            direction = 1
            while x < width:
                runs += f"q40 {-amp * direction} 80 0"
                x += 80
                direction = -direction
            color = edge if step % 2 else accent
            opacity = 0.85 - step * 0.15
            parts.append(f"<path d='{runs}' fill='none' stroke='{color}' stroke-opacity='{opacity}' stroke-width='{6 - step}'/>")
            foam = "".join(f"<path d='M{x} {y - 4}l4 4l-4 4l-4 -4Z' fill='{field}' fill-opacity='.8'/>" for x in range(60, width, 120))
            parts.append(foam)
        return "".join(parts)

    header_sun = f"<circle cx='568' cy='18' r='13' fill='{accent}'/><circle cx='568' cy='18' r='9' fill='{p['field']}' fill-opacity='.35'/>"
    header = (f"<svg xmlns='http://www.w3.org/2000/svg' width='640' height='64' shape-rendering='crispEdges'>"
              f"<rect width='640' height='64' fill='{p['panel']}'/>{header_sun}"
              + waves(640, 40, 3) + "</svg>")
    crane = (f"<g transform='translate(470 52)'><path d='M0 6q8-8 16 0q8-8 16 0' fill='none' stroke='{edge}' stroke-width='2'/>"
             f"<path d='M14 6l4 5-8 0Z' fill='{edge}'/></g>")
    mist = "".join(f"<path d='M20 {y}h130' stroke='{edge}' stroke-opacity='.3' stroke-width='3'/>" for y in (96, 104))
    seal = f"<g transform='translate(596 200)'><rect width='30' height='30' fill='{accent}'/><path d='M6 20c2-3 5-3 7 0s5 3 7 0' fill='none' stroke='{p['accentInk']}' stroke-width='2'/></g>"
    mascots = ("<g transform='translate(96 128) scale(2.4)'>" + mascot_svg("lumen-fox", "happy", {"border": edge, "accent": accent}) + "</g>"
               "<g transform='translate(300 128) scale(2.4)'>" + mascot_svg("dusk-lynx", "surprise", {"border": edge, "accent": accent}) + "</g>")
    tide = waves(640, 176, 4)
    shore = f"<path d='M0 210h640v30H0Z' fill='{edge}' fill-opacity='.8'/><path d='M0 210h640' stroke='{p['field']}' stroke-width='3' stroke-dasharray='18 10'/>"
    empty = (f"<svg xmlns='http://www.w3.org/2000/svg' width='640' height='240' shape-rendering='crispEdges'>"
             f"<rect width='640' height='240' fill='{p['panel']}'/>"
             f"<rect x='8' y='8' width='624' height='224' fill='none' stroke='{edge}' stroke-opacity='.6'/>{mist}{tide}{shore}{crane}{mascots}{seal}</svg>")
    return {"header": header, "empty": empty}


_strings = load_catalog_data("en")["messages"]
_skin = _example(IDENTIFIER, _strings[f"toybox.skin_{IDENTIFIER}_name"],
                 _strings[f"toybox.skin_{IDENTIFIER}_description"], _palette(False),
                 "flat", "none", 4, 2, "serif-heading", "2.0", "1.0.0")
_skin["palettes"] = {"dark": _palette(False), "light": _palette(True)}
_skin["stage"] = {"layout": "spread", "controls": "brush", "type": "serif-display",
                  "frame": {"set": "scroll"}, "scene": {"id": "sea", "density": "calm"},
                  "reactive": {"profile": "glow", "accentFrom": "accent"}}
SKINS = (_skin,)
