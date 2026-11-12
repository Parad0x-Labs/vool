"""Trusted skin renderer. Imported manifests supply data, never selectors, CSS or art.

Contributor: sls_0x. The application owns every selector, rule and material below. A
manifest chooses colours and shipped presets: `frame` sets control geometry (pixel
bevels, etched rims or flat borders) and `ornament` sets chrome materials and motifs
(Blockcraft stone/turf/oak or woodland map/brass/leaf). Each rule skips the trusted
islands, which keep VOOL's standard presentation inside themed chrome.
"""

from __future__ import annotations

import html
import re

from core import toybox_materials as art
from core.toybox_ornaments import ORNAMENTS
from core.ui_palette import DARK_PALETTE_CSS, LIGHT_ONLY_PALETTE_CSS


def _palette_tokens(css):
    """The custom properties of the first block of one of VOOL's own palette stylesheets."""
    block = css[css.index("{") + 1 : css.index("}")]
    block = re.sub(r"/\*.*?\*/", "", block, flags=re.S)
    return {name: value.strip() for name, value in re.findall(r"--([a-z0-9-]+)\s*:\s*([^;]+);", block)}


# Approval, receipt and money surfaces: no skin rule ever reaches them or their contents, and
# they keep VOOL's standard tokens inside any skin. #vgOverlay is the price gate; #vwSec, #vwCx,
# .vw-sheet and #vwHomeOverlay are the wallet surfaces and their review dialogs. The 0.7.2 code
# added consent surfaces of the same standing: the private-chat markers (.msg.is-private with
# its divider, note and composer banner), the X action approval cards, the in-app confirm
# dialog and the Web lookups switch row. The activity rows that carry a paid call's money receipt
# (amounts, wallets, network and service fees) are receipts too.
TRUSTED = (
    '#permBar,#bypassBanner,#bypassModal,.chat-provenance,.proof-chip,.price-safety,[role="alertdialog"],[role="alert"],'
    "#vgOverlay,#vwSec,#vwCx,.vw-sheet,#vwHomeOverlay,"
    '#confirmPromptDialog,.x-action-card,[data-row="web_lookups"],'
    ".msg.is-private,.private-divider,.private-note,.private-banner,.xp-receipt"
)
_TRUSTED_IS = ":is(" + TRUSTED + ")"
# Appended to every themed selector's subject: never a trusted island, never inside one.
GUARD = ":not(" + _TRUSTED_IS + "):not(" + _TRUSTED_IS + " *)"
# The page tokens a skin sets from its palette (palette field -> page custom properties). The
# trusted islands get exactly these back with VOOL's own values. bg-elev and fg are the surface
# and ink the light theme gives the store and a few raised panels; accent-ink is the text on
# accent-filled controls.
ALIASES = {
    "background": ("bg", "chat"),
    "panel": ("panel", "panel-solid", "bg-elev"),
    "field": ("field", "active", "user"),
    "ink": ("ink", "text", "fg"),
    "muted": ("muted",),
    "accent": ("accent", "accent2"),
    "accentInk": ("accent-ink",),
    "border": ("border",),
}
# A skin is a whole palette, so it applies over the theme chosen in Settings > Appearance: the
# light theme's token block is :root[data-theme=...], and this selector matches it at equal
# specificity, later in the page.
PAGE_TOKENS_SELECTOR = ":root,:root[data-theme]"
SKIN_PAGE_TOKENS = (*(name for names in ALIASES.values() for name in names), "bg2", "grad", "tb-accent-ink")


def _island_tokens(palette):
    """VOOL's own value for every token a skin sets. A token VOOL's palette leaves undefined is
    made undefined again (`initial`), so the islands' own fallbacks apply exactly as without a skin."""
    values = {"tb-accent-ink": "var(--accent-ink)"}
    values.update({name: palette[name] for name in SKIN_PAGE_TOKENS if name in palette})
    scheme = "color-scheme:" + palette["color-scheme"] + ";"
    return scheme + ";".join(f"--{name}:{values.get(name, 'initial')}" for name in SKIN_PAGE_TOKENS)


# Light and "Same as this computer" (Settings > Appearance) are VOOL's own looks too: inside a
# skin, an island shows the theme the person chose, as it would with no skin. The light look is
# the dark :root block with the light block over it, as on the pages.
_DARK_TOKENS = {**_palette_tokens(DARK_PALETTE_CSS), "color-scheme": "dark"}
_LIGHT_TOKENS = {**_DARK_TOKENS, **_palette_tokens(LIGHT_ONLY_PALETTE_CSS), "color-scheme": "light"}
ISLAND_RESET = (
    TRUSTED + "{" + _island_tokens(_DARK_TOKENS) + "}\n"
    + ':root[data-theme="light"] ' + _TRUSTED_IS + "{" + _island_tokens(_LIGHT_TOKENS) + "}\n"
    + '@media (prefers-color-scheme: light){:root[data-theme="system"] ' + _TRUSTED_IS + "{"
    + _island_tokens(_LIGHT_TOKENS) + "}}\n"
    # a standard backing only where an island has no colour or background of its own (zero
    # specificity, so an island's own rules always win); nested islands inherit theirs
    + ":where(" + _TRUSTED_IS + ":not(" + _TRUSTED_IS + " *)){background-color:var(--panel);color:var(--ink)}\n"
)
# Model, permission-mode and connection controls take the skin's chrome, but the parts that
# carry their meaning stay VOOL's standard colours in every skin (owner direction 2026-10-02).
# Values that the pages take from the palette are VOOL's own dark palette values here, read from
# the palette the pages ship; status tokens (--ok, --bad, --warn) are never set by a skin.
_VOOL = _palette_tokens(DARK_PALETTE_CSS)
PINNED = (
    ((".model-lane.auto",), "color:#c4b5fd;background:#27213a"),
    ((".model-lane.local",), f"color:{_VOOL['accent']};background:rgba({_VOOL['accent-rgb']},.12)"),
    ((".model-lane.cloud",), "color:#93c5fd;background:#15294d"),
    (("#modeCtrl .ctrl-btn.bypass",), "border-color:#aa8545"),
    ((".btn.danger", "#vccStop"), "color:var(--bad);border-color:var(--bad)"),
    (("#dictateBtn.recording",), "border-color:var(--warn);box-shadow:0 0 0 2px color-mix(in srgb,var(--warn) 35%,transparent)"),
    (("#dictateBtn.error",), "border-color:var(--bad)"),
    (("#updateChip.upd-ready",), "border-color:var(--accent);color:var(--accent);font-weight:600"),
    (("#updateChip.upd-ok",), "color:var(--ok)"),
    (("#updateChip.upd-warn",), "border-color:var(--bad);color:var(--bad)"),
    # status dots (the model button's run status and connections, Settings pills): the neutral
    # grey is VOOL's standard muted, whatever the skin's muted is
    (("#modelBtn .dot", "#modelConnections .dot", ".pill .dot"), f"background:{_VOOL['muted']}"),
    (("#modelBtn .dot.ok", "#modelConnections .dot.ok", ".pill.on .dot"), "background:var(--ok)"),
    (("#modelBtn .dot.bad", "#modelConnections .dot.bad", ".pill.off .dot"), "background:var(--bad)"),
    (("#modelBtn .dot.warn", "#modelConnections .dot.warn"), "background:var(--warn)"),
)

BUTTONS = (
    ".btn", ".tb-btn", "#newChat", "#newProject", ".home-menu > summary", "#pinBtn", "#searchBtn",
    "#exportBtn", "#panelBtn", "#councilBtn", ".ctrl-btn", "#dictateBtn", "#voiceModeBtn", ".msg-copy",
    ".msg-act", ".search-btn", "#cloudPill", "#vfBell", ".vf-btn", ".vf-tabs button", "#vxEmoteBtn", "#menu",
    "#updateChip", "#xpCopy", ".xp-tab", ".set-btn", "#dictationRetry", ".vcc-add button", "#vccStop",
    "#vccNerdToggle", "#vccRuns button", ".mr-btn", ".addon-controls button", ".vp-run", ".vf-foot button",
    ".tc-report", ".tc-view",
)
PRIMARY = (".btn.primary", ".tb-btn.tb-primary", "#send", ".setup-line-open", "#vccConvene", ".mr-primary")
FIELDS = (
    "#input", ".inp", "#search", "#uiLocaleSelect", ".tb-editor", "#searchInput", "#vxFilter", ".xp-search",
    ".xp-scope", ".set-input", "#dictationLocale", ".vcc-seat select", ".vcc-seat input", "#vccRoot textarea",
    ".vcs-input", ".vns-input",
)
PANELS = (
    ".card", ".tb-card", ".setup-line", ".mp-card", ".key-guide-category", ".scam-card", ".xp-history-card",
    ".vcs-card", ".vcs-add", ".vns-box", ".sb-box", ".addon-card",
)
BRACKETED = (".card", ".tb-card", ".mp-card", ".key-guide-category", ".scam-card", ".addon-card")
MENUS = (".popover", ".home-options", ".proj-menu", ".modal", "#vfPop", "#vpPalette", "#vsToolbeltModal", ".vc-modal", "#mrPop", ".n-toast")
# Toggle buttons that are switched on, and the highlighted entry in a list.
TOGGLED = ("#searchBtn.on", "#panelBtn.on", "#pinBtn.has", ".xp-tab.active", '.vf-tabs button[aria-selected="true"]', '#voiceModeBtn[aria-pressed="true"]')
KEYCAPS = ("#vpHint kbd", ".vp-kbd")
PANEL_HEADS = (".modal-head", ".xp-head")
CHECKS = ('input[type="checkbox"]', 'input[type="radio"]', 'input[type="range"]')
# Page rules that colour TEXT with the accent. A skin whose accent is too light or too dark to
# read as text on its own surfaces gets a readable variant here (the pinned lane and update
# colours are restated afterwards, so they are unaffected).
ACCENT_TEXT = (
    "#newChat", "#panelBtn", "#pinBtn .pin-n", "#dictationRetry", "#vccConvene", '#voiceModeBtn[aria-pressed="true"]',
    ".msg-text a", "header a", ".set-help a", ".tb-link", ".tb-badge", ".nav-item .hits", '.nav-item[aria-current="true"] .ico',
    ".msg-act.on", ".msg-pin.on", ".pop-item .pi-check", ".proj-head.active .proj-caret", ".proj-menu-item.new",
    ".proj-menu-item.on", ".rangeval", ".state .retry", ".set-num", ".set-status.ok", ".badge.enforced", ".badge.pilot",
    ".mp-chip.mp-done", ".mp-step-done", ".mr-kind", ".mr-status", ".mr-prices .mr-pct", ".att-chip .att-expand",
    ".att-chip .att-retry", ".xp-tab.active", ".xp-turn > summary:hover", ".xp-row.run .ic", ".xp-row-flat.run .ic",
    ".xp-item.run .ic", ".rc-item.run .ic", ".rc-sum:hover .rc-hd", ".tc-mark", ".tc-view", ".council-flow span.hot",
    ".p-toggle.enable", ".plugin-card .p-skill b", ".save-banner .sb-ico", ".vcx-act[data-act=\"resume\"]",
    "#vlBar button.vl-truth", ".vw-learn summary",
)
SIDEBARS = ("#sidebar", "#side")
HEADER = "#main > header"
SELECTED = ('.nav-item[aria-current="true"]', "#sessions .session.active", ".proj-head.active", ".vp-item.vp-sel")
HOVERED = (
    ".nav-item:hover", "#sessions .session:hover", ".proj-head:hover", ".pop-item:hover",
    ".proj-menu-item:hover", ".home-options .side-foot-btn:hover", "#back:hover",
)
ROWS = ("#nav .nav-item", "#sessions .session", ".proj-head", ".pop-item", ".proj-menu-item", ".home-options .side-foot-btn", ".vp-item", ".vf-item")
HEADINGS = ("h1", ".tb-title", ".empty-greet", HEADER + " .brand", ".modal-head")
LABELS = (".nav-sec", ".side-title")
TAGS = (".badge", ".tb-badge", ".qchip", ".ctx-chip", ".att-chip", ".vs-tag", ".vcs-chip", ".pill")


def _each(selectors, suffix=""):
    """One guarded selector per rule: `:is(a,b,…)` keeps a single guard and one specificity."""
    bases, pseudos = [], set()
    for selector in selectors:
        base, _, pseudo = selector.partition("::")
        bases.append(base)
        pseudos.add(pseudo)
    if len(pseudos) != 1:
        raise ValueError("one pseudo-element per rule")
    pseudo = pseudos.pop()
    subject = bases[0] if len(bases) == 1 else ":is(" + ",".join(bases) + ")"
    return subject + suffix + GUARD + ("::" + pseudo if pseudo else "")


def _rule(selectors, body, suffix=""):
    return _each(selectors, suffix) + "{" + body + "}\n"


def _notch(n):
    a, b = f"{n}px", f"calc(100% - {n}px)"
    return (
        f"clip-path:polygon(0 {a},{a} {a},{a} 0,{b} 0,{b} {a},100% {a},100% {b},{b} {b},{b} 100%,"
        f"{a} 100%,{a} {b},0 {b})"
    )


def _tokens(skin):
    p = skin["palette"]
    dark = art.is_dark(p)
    inks = (p["ink"], p["muted"])
    # hover moves the accent away from its ink, so button text only gains contrast
    lighter_ink = art.contrast(p["accentInk"], "#000000") > art.contrast(p["accent"], "#000000")
    toward = "#000000" if lighter_ink else "#ffffff"
    accent_hover = art.mix(
        p["accent"], toward, art.safe_weight(p["accent"], toward, 0.12, (p["accentInk"],))
    )
    values = {
        "tb-accent-ink": p["accentInk"],
        "tb-edge": art.mix(p["background"], "#000000", 0.55) if dark else art.mix(p["ink"], p["background"], 0.3),
        "tb-hi": "rgba(255,255,255,.16)" if dark else "rgba(255,255,255,.8)",
        "tb-lo": "rgba(0,0,0,.42)" if dark else "rgba(0,0,0,.2)",
        "tb-hi-soft": "rgba(255,255,255,.07)" if dark else "rgba(255,255,255,.6)",
        "tb-lo-soft": "rgba(0,0,0,.28)" if dark else "rgba(0,0,0,.1)",
        "tb-drop": "rgba(0,0,0,.45)" if dark else "rgba(0,0,0,.16)",
        "tb-hover": art.mix(p["field"], p["accent"], art.safe_weight(p["field"], p["accent"], 0.14, inks)),
        "tb-panel-hover": art.mix(p["panel"], p["accent"], art.safe_weight(p["panel"], p["accent"], 0.1, inks)),
        "tb-accent-hi": art.mix(p["accent"], "#ffffff", 0.32),
        "tb-accent-lo": art.mix(p["accent"], "#000000", 0.38),
        "tb-accent-hover": accent_hover,
        "tb-accent-line": art.mix(p["accent"], p["accentInk"], 0.45),
        "tb-rim-dim": art.mix(p["border"], p["panel"], 0.3),
        "tb-rim-faint": art.mix(p["border"], p["panel"], 0.62),
        "tb-switch-on": art.mix(p["field"], p["accent"], 0.3),
        "tb-text-shadow": art.mix(p["background"], "#000000", 0.7) if dark else art.mix(p["background"], p["ink"], 0.18),
        "tb-glow": _rgba(p["accent"], 0.55),
        "tb-mote": _rgba(art.mix(p["accent"], "#ffffff", 0.3), 0.85),
    }
    if skin["ornament"] == "voxel":
        colours = art.voxel_colours(p)
        values.update({"tb-dirt": colours["dirt"], "tb-wood": colours["wood"]})
    if skin["frame"] in NEW_FRAMES:
        values.update(_new_frame_tokens(skin, dark, inks))
    if skin["ornament"] in ORNAMENTS:
        values.update(ORNAMENTS[skin["ornament"]]["tokens"](p))
    return values


def _readable_variant(p, floor):
    """The accent if it reaches `floor` on every page surface, else the accent pulled toward ink."""
    for step in range(21):
        colour = art.mix(p["accent"], p["ink"], step / 20)
        if all(art.contrast(colour, p[s]) >= floor for s in ("background", "panel", "field")):
            return colour
    return p["ink"]


def _focus_colour(p):
    return _readable_variant(p, 3.05)


NEW_FRAMES = ("toon", "capsule", "chamfer")


def _new_frame_tokens(skin, dark, inks):
    p = skin["palette"]
    line = art.mix(p["background"], "#000000", 0.6) if dark else art.mix(p["ink"], p["background"], 0.08)
    values = {"tb-focus": _focus_colour(p)}
    if skin["frame"] == "toon":
        # cel shading: a hard-edged lit band over the top of each control, never under-contrast
        lit = art.mix(p["field"], "#ffffff", art.safe_weight(p["field"], "#ffffff", 0.14 if dark else 0.7, inks))
        lit_user = art.mix(p["field"], "#ffffff", art.safe_weight(p["field"], "#ffffff", 0.08 if dark else 0.6, inks))
        values.update({
            "tb-ink-line": line,
            "tb-shadow": line,
            "tb-cel": lit,
            "tb-cel-user": lit_user,
            "tb-cel-lo": art.mix(p["field"], "#000000", 0.28),
            "tb-select-bg": art.mix(p["field"], p["accent"], art.safe_weight(p["field"], p["accent"], 0.22, inks)),
        })
    elif skin["frame"] == "capsule":
        gloss = art.safe_weight(p["field"], "#ffffff", 0.1 if dark else 0.85, inks)
        gloss_accent = art.safe_weight(p["accent"], "#ffffff", 0.5, (p["accentInk"],))
        values.update({
            "tb-chrome": art.mix(p["border"], "#ffffff", 0.45),
            "tb-chrome-dim": art.mix(p["border"], p["panel"], 0.25),
            "tb-gloss": f"rgba(255,255,255,{int(gloss * 1000) / 1000})",
            "tb-gloss-accent": f"rgba(255,255,255,{int(gloss_accent * 1000) / 1000})",
            "tb-soft-shadow": _rgba(art.mix(p["border"], p["accent"], 0.3), 0.28),
        })
    else:
        values.update({
            "tb-rim": art.mix(p["border"], p["accent"], 0.35),
            "tb-rim-glow": _rgba(p["accent"], 0.32),
            "tb-sheen": f"rgba(255,255,255,{int(art.safe_weight(p['field'], '#ffffff', 0.06, inks) * 1000) / 1000})",
        })
    return values


def _pixel_frame(w):
    lo2 = 2 * w
    out = _rule(BUTTONS, f"border:{w}px solid var(--tb-edge);border-radius:0;background:var(--field);color:var(--ink);"
                f"box-shadow:inset {w}px {w}px 0 var(--tb-hi),inset -{w}px -{w}px 0 var(--tb-lo)")
    out += _rule(BUTTONS, "background:var(--tb-hover);border-color:var(--tb-edge)", ":hover:not(:disabled)")
    out += _rule(BUTTONS, f"box-shadow:inset {w}px {w}px 0 var(--tb-lo),inset -{w}px -{w}px 0 var(--tb-hi)", ":active:not(:disabled)")
    out += _rule(BUTTONS, "box-shadow:none;background:var(--field)", ":disabled")
    out += _rule(PRIMARY, f"border:{w}px solid var(--tb-edge);border-radius:0;background:var(--accent);color:var(--tb-accent-ink);"
                 f"font-weight:700;box-shadow:inset {w}px {w}px 0 var(--tb-accent-hi),inset -{w}px -{w + 1}px 0 var(--tb-accent-lo)")
    out += _rule(PRIMARY, "background:var(--tb-accent-hover)", ":hover:not(:disabled)")
    out += _rule(PRIMARY, f"box-shadow:inset {w}px {w}px 0 var(--tb-accent-lo),inset -{w}px -{w}px 0 var(--tb-accent-hi)", ":active:not(:disabled)")
    out += _rule(FIELDS, f"border:{w}px solid var(--tb-edge);border-radius:0;background:var(--field);"
                 f"box-shadow:inset {w}px {w}px 0 var(--tb-lo),inset -{w}px -{w}px 0 var(--tb-hi-soft)")
    out += _rule(FIELDS, "border-color:var(--accent)", ":focus")
    out += _rule(PANELS, f"border:{w}px solid var(--tb-edge);border-radius:0;background-color:var(--panel);"
                 f"box-shadow:inset {w}px {w}px 0 var(--tb-hi-soft),inset -{w}px -{w}px 0 var(--tb-lo-soft);{_notch(w)}")
    out += _rule((".msg.user",), f"border:{w}px solid var(--tb-edge);border-radius:0;background-color:var(--user);"
                 f"box-shadow:inset {w}px {w}px 0 var(--tb-hi-soft),inset -{w}px -{w}px 0 var(--tb-lo-soft);{_notch(w)}")
    out += _rule(MENUS, f"border:{w}px solid var(--tb-edge);border-radius:0;"
                 f"box-shadow:inset {w}px {w}px 0 var(--tb-hi-soft),inset -{w}px -{w}px 0 var(--tb-lo-soft),6px 6px 0 var(--tb-drop)")
    out += _rule(SIDEBARS, f"border-right:0;border-inline-end:{w + 1}px solid var(--tb-edge)")
    out += _rule((HEADER,), f"border-bottom:{w}px solid var(--tb-edge)")
    out += _rule(ROWS, "border-radius:0")
    out += _rule(HOVERED, "background:var(--tb-panel-hover)")
    out += _rule(SELECTED, f"background:var(--field);border-radius:0;box-shadow:inset 0 0 0 {w}px var(--accent),inset 0 0 0 {lo2}px var(--tb-lo-soft)")
    out += _rule((".sw",), f"border-radius:0;border:{w}px solid var(--tb-edge);background:var(--field);box-shadow:inset {w}px {w}px 0 var(--tb-lo)")
    out += _rule((".sw::after",), "border-radius:0;background:var(--muted);box-shadow:inset 2px 2px 0 rgba(255,255,255,.35),inset -2px -2px 0 rgba(0,0,0,.35)")
    out += _rule(('.sw[aria-checked="true"]',), "background:var(--tb-switch-on)")
    out += _rule(('.sw[aria-checked="true"]::after',), "background:var(--accent)")
    out += _rule((".card .row", ".tb-preview-box"), "border-color:var(--tb-edge)")
    out += _rule(TAGS, "border-radius:0")
    out += _rule((".tb-thumb", ".tb-preview"), f"border:{w}px solid var(--tb-edge);border-radius:0")
    out += _rule(("h1", ".empty-greet", ".tb-title", HEADER + " .brand"), "text-shadow:2px 2px 0 var(--tb-text-shadow)")
    out += _rule((HEADER + " .brand img",), "border-radius:0;box-shadow:0 0 0 2px var(--tb-edge)")
    out += _rule(TOGGLED, f"border-color:var(--accent);background:var(--tb-hover);box-shadow:inset {w}px {w}px 0 var(--tb-lo),inset -{w}px -{w}px 0 var(--tb-hi-soft)")
    out += _rule(KEYCAPS, f"border:{w}px solid var(--tb-edge);border-radius:0;background:var(--field);box-shadow:inset 1px 1px 0 var(--tb-hi),inset -1px -2px 0 var(--tb-lo)")
    out += _rule(("#xpanel",), f"border-left:{w + 1}px solid var(--tb-edge)")
    out += _rule((".search-bar",), f"border-bottom:{w}px solid var(--tb-edge)")
    return out


def _etched_frame(w, r):
    out = _rule(BUTTONS, f"border:{w}px solid var(--tb-rim-dim);border-radius:{r}px;background:var(--field);color:var(--ink);"
                "box-shadow:inset 0 1px 0 var(--tb-hi-soft),0 1px 0 var(--tb-lo-soft)")
    out += _rule(BUTTONS, "border-color:var(--accent);background:var(--tb-hover)", ":hover:not(:disabled)")
    out += _rule(BUTTONS, "box-shadow:inset 0 2px 4px var(--tb-lo)", ":active:not(:disabled)")
    out += _rule(BUTTONS, "box-shadow:none;background:var(--field);border-color:var(--tb-rim-dim)", ":disabled")
    out += _rule(PRIMARY, f"border:{w}px solid var(--tb-accent-lo);border-radius:{r}px;background:var(--accent);color:var(--tb-accent-ink);"
                 "font-weight:700;box-shadow:inset 0 0 0 2px var(--accent),inset 0 0 0 3px var(--tb-accent-line),0 1px 0 var(--tb-lo-soft)")
    out += _rule(PRIMARY, "background:var(--tb-accent-hover);box-shadow:inset 0 0 0 2px var(--tb-accent-hover),inset 0 0 0 3px var(--tb-accent-line)", ":hover:not(:disabled)")
    out += _rule(FIELDS, f"border:{w}px solid var(--tb-rim-dim);border-radius:{r}px;background:var(--field);"
                 "box-shadow:inset 0 0 0 2px var(--field),inset 0 0 0 3px var(--tb-rim-faint)")
    out += _rule(FIELDS, "border-color:var(--accent)", ":focus")
    out += _rule(PANELS, f"border:{w}px solid var(--tb-rim-dim);border-radius:{r}px;box-shadow:inset 0 1px 0 var(--tb-hi-soft)")
    out += _rule((".msg.user",), f"border:{w}px solid var(--tb-rim-dim);border-radius:{r}px;background-color:var(--user);"
                 "box-shadow:inset 0 0 0 3px var(--user),inset 0 0 0 4px var(--tb-rim-faint)")
    out += _rule(MENUS, f"border:{w}px solid var(--tb-rim-dim);border-radius:{r}px;"
                 "box-shadow:inset 0 0 0 3px var(--panel),inset 0 0 0 4px var(--tb-rim-faint),0 12px 32px var(--tb-drop)")
    out += _rule(SIDEBARS, f"border-right:0;border-inline-end:{w}px solid var(--tb-rim-dim)")
    out += _rule((HEADER,), "border-bottom:0")
    out += _rule(ROWS, f"border-radius:{r}px")
    out += _rule(("#nav .nav-item", "#sessions .session", ".proj-head"), "border-inline-start:2px solid transparent")
    out += _rule(HOVERED, "background:var(--tb-panel-hover)")
    out += _rule(SELECTED, "background:var(--field);border-inline-start-color:var(--accent);box-shadow:inset 0 0 0 1px var(--tb-rim-faint)")
    out += _rule((".sw",), f"border:{w}px solid var(--tb-rim-dim);background:var(--field);box-shadow:inset 0 1px 3px var(--tb-lo)")
    out += _rule((".sw::after",), "background:var(--muted);box-shadow:inset 0 0 0 2px var(--tb-rim-faint)")
    out += _rule(('.sw[aria-checked="true"]',), "background:var(--tb-switch-on);border-color:var(--accent)")
    out += _rule(('.sw[aria-checked="true"]::after',),
                 "background:radial-gradient(circle at 45% 35%,var(--tb-accent-hi),var(--accent) 55%,var(--tb-accent-lo));box-shadow:none")
    out += _rule((".card .row", ".tb-preview-box"), "border-color:var(--tb-rim-faint)")
    out += _rule((".tb-thumb", ".tb-preview"), f"border:{w}px solid var(--tb-rim-dim);border-radius:{r}px")
    out += _rule((HEADER + " .brand img",), f"border-radius:{r}px;box-shadow:0 0 0 1px var(--accent)")
    out += _rule(TOGGLED, "border-color:var(--accent);background:var(--field);box-shadow:inset 0 0 0 1px var(--tb-rim-faint)")
    out += _rule(KEYCAPS, f"border:1px solid var(--tb-rim-dim);border-radius:{min(r, 4)}px;background:var(--field);box-shadow:inset 0 -1px 0 var(--tb-lo-soft)")
    out += _rule(("#xpanel",), f"border-left:{w}px solid var(--tb-rim-dim)")
    out += _rule((".search-bar",), "border-bottom-color:var(--tb-rim-dim)")
    return out


_FOCUSABLE = BUTTONS + PRIMARY + FIELDS + ROWS


def _focus_ring(width=2):
    # clipped or heavily styled controls show focus as an inner ring that nothing can hide;
    # the council button keeps browser padding by default, so it gets its neighbours' spacing
    return _rule(_FOCUSABLE, f"outline:none;box-shadow:inset 0 0 0 {width}px var(--tb-focus),0 0 0 1px var(--tb-focus)",
                 ":focus-visible:focus-visible:focus-visible") + _rule(("#councilBtn",), "padding:4px 10px;font-size:13px")


def _toon_frame(w, r):
    ink, shadow = "var(--tb-ink-line)", "var(--tb-shadow)"
    cel = "linear-gradient(180deg,var(--tb-cel) 0 44%,var(--field) 44%)"
    cel_accent = "linear-gradient(180deg,var(--tb-accent-hi) 0 44%,var(--accent) 44%)"
    out = _rule(BUTTONS, f"border:{w}px solid {ink};border-radius:{r}px;background:{cel};color:var(--ink);"
                f"box-shadow:0 {w + 1}px 0 {shadow},inset 0 -3px 0 var(--tb-cel-lo)")
    out += _rule(BUTTONS, f"background:linear-gradient(180deg,var(--tb-cel) 0 44%,var(--tb-hover) 44%);box-shadow:0 {w + 1}px 0 {shadow},0 0 14px -3px var(--tb-glow)", ":hover:not(:disabled)")
    out += _rule(BUTTONS, f"box-shadow:0 1px 0 {shadow},inset 0 2px 0 var(--tb-cel-lo)", ":active:not(:disabled)")
    out += _rule(BUTTONS, "box-shadow:none", ":disabled")
    out += _rule(PRIMARY, f"border:{w}px solid {ink};border-radius:{r}px;background:{cel_accent};color:var(--tb-accent-ink);font-weight:800;"
                 f"box-shadow:0 {w + 1}px 0 {shadow},0 0 18px -4px var(--tb-glow),inset 0 -3px 0 var(--tb-accent-lo)")
    out += _rule(PRIMARY, "background:linear-gradient(180deg,var(--tb-accent-hi) 0 44%,var(--tb-accent-hover) 44%)", ":hover:not(:disabled)")
    out += _rule(PRIMARY, f"box-shadow:0 1px 0 {shadow},inset 0 2px 0 var(--tb-accent-lo)", ":active:not(:disabled)")
    out += _rule(FIELDS, f"border:{w}px solid {ink};border-radius:{r}px;background:var(--field);box-shadow:inset 0 3px 0 var(--tb-cel-lo)")
    out += _rule(FIELDS, "border-color:var(--tb-focus)", ":focus")
    out += _rule(PANELS, f"border:{w}px solid {ink};border-radius:{r}px;background-color:var(--panel);box-shadow:{w + 2}px {w + 3}px 0 {shadow}")
    out += _rule((".msg.user",), f"border:{w}px solid {ink};border-radius:{r + 8}px;background:linear-gradient(180deg,var(--tb-cel-user) 0 40%,var(--user) 40%);"
                 f"box-shadow:{w + 1}px {w + 2}px 0 {shadow}")
    out += _rule((".msg.user::after",), f'content:"";position:absolute;inset-inline-end:22px;bottom:-{5 + w}px;width:10px;height:10px;'
                 f"background:var(--user);border-right:{w}px solid {ink};border-bottom:{w}px solid {ink};transform:rotate(45deg);pointer-events:none")
    out += _rule(MENUS, f"border:{w}px solid {ink};border-radius:{r}px;box-shadow:{w + 3}px {w + 4}px 0 {shadow}")
    out += _rule(SIDEBARS, f"border-right:0;border-inline-end:{w + 1}px solid {ink}")
    out += _rule((HEADER,), f"border-bottom:{w + 1}px solid {ink}")
    out += _rule(ROWS, f"border-radius:{r}px")
    out += _rule(HOVERED, "background:var(--tb-panel-hover);box-shadow:inset 0 0 0 1px var(--tb-glow)")
    out += _rule(SELECTED, f"background:var(--tb-select-bg);border-radius:{r}px;box-shadow:inset 0 0 0 {w}px var(--accent),0 0 14px -3px var(--tb-glow)")
    out += _rule((".sw",), f"border:{w}px solid {ink};background:var(--field)")
    out += _rule((".sw::after",), f"background:var(--tb-cel);box-shadow:0 0 0 {w}px {ink}")
    out += _rule(('.sw[aria-checked="true"]',), "background:var(--accent)")
    out += _rule((".card .row", ".tb-preview-box"), "border-color:var(--tb-rim-faint)")
    out += _rule(TAGS, "border-radius:999px")
    out += _rule((".tb-thumb", ".tb-preview"), f"border:{w}px solid {ink};border-radius:{r}px")
    out += _rule((HEADER + " .brand img",), f"border-radius:{r}px;box-shadow:0 0 0 {w}px {ink},0 0 12px -2px var(--tb-glow)")
    out += _rule(TOGGLED, f"border-color:var(--accent);background:var(--tb-select-bg);box-shadow:0 1px 0 {shadow}")
    out += _rule(KEYCAPS, f"border:1px solid {ink};border-radius:4px;background:{cel};box-shadow:0 2px 0 {shadow}")
    out += _rule(("#xpanel",), f"border-left:{w}px solid {ink}")
    out += _rule((".search-bar",), f"border-bottom:{w}px solid {ink}")
    return out + _focus_ring()


def _capsule_frame(w, r):
    dim, chrome = "var(--tb-chrome-dim)", "var(--tb-chrome)"
    gloss = "linear-gradient(180deg,var(--tb-gloss) 0%,transparent 52%)"
    gloss_accent = "linear-gradient(180deg,var(--tb-gloss-accent) 0%,transparent 55%)"
    out = _rule(BUTTONS, f"border:{w}px solid {dim};border-radius:999px;background:{gloss},var(--field);color:var(--ink);"
                "box-shadow:inset 0 1px 0 rgba(255,255,255,.7),0 2px 8px var(--tb-soft-shadow)")
    out += _rule(BUTTONS, f"border-color:var(--accent);background:{gloss},var(--tb-hover);box-shadow:inset 0 1px 0 rgba(255,255,255,.8),0 0 0 3px var(--tb-glow),0 4px 12px var(--tb-soft-shadow)", ":hover:not(:disabled)")
    out += _rule(BUTTONS, "box-shadow:inset 0 2px 6px var(--tb-soft-shadow)", ":active:not(:disabled)")
    out += _rule(BUTTONS, "box-shadow:none", ":disabled")
    out += _rule(PRIMARY, f"border:{w}px solid var(--tb-accent-hi);border-radius:999px;background:{gloss_accent},linear-gradient(180deg,var(--tb-accent-hi),var(--accent) 60%);"
                 "color:var(--tb-accent-ink);font-weight:800;box-shadow:inset 0 1px 0 rgba(255,255,255,.8),0 4px 14px var(--tb-glow)")
    out += _rule(PRIMARY, f"background:{gloss_accent},linear-gradient(180deg,var(--tb-accent-hi),var(--tb-accent-hover) 60%)", ":hover:not(:disabled)")
    out += _rule(PRIMARY, "box-shadow:inset 0 2px 6px var(--tb-accent-lo)", ":active:not(:disabled)")
    out += _rule(FIELDS, f"border:{w}px solid {dim};border-radius:{min(r + 4, 18)}px;background:var(--field);box-shadow:inset 0 2px 6px var(--tb-soft-shadow)")
    out += _rule(FIELDS, "border-color:var(--accent);box-shadow:inset 0 2px 6px var(--tb-soft-shadow),0 0 0 3px var(--tb-glow)", ":focus")
    out += _rule(PANELS, f"border:{w}px solid {dim};border-radius:{r + 4}px;background-color:var(--panel);box-shadow:0 8px 26px var(--tb-soft-shadow)")
    out += _rule((".msg.user",), f"border:{w}px solid {dim};border-radius:{r + 8}px;background-color:var(--user);box-shadow:0 3px 10px var(--tb-soft-shadow)")
    out += _rule((".msg.user::after",), f'content:"";position:absolute;inset-inline-end:24px;bottom:-6px;width:10px;height:10px;'
                 f"background:var(--user);border-right:{w}px solid {dim};border-bottom:{w}px solid {dim};transform:rotate(45deg);pointer-events:none")
    out += _rule(MENUS, f"border:{w}px solid {dim};border-radius:{r + 2}px;box-shadow:0 14px 36px var(--tb-soft-shadow)")
    out += _rule(SIDEBARS, f"border-right:0;border-inline-end:{w}px solid {dim}")
    out += _rule((HEADER,), f"border-bottom:{w}px solid {dim}")
    out += _rule(("#nav .nav-item", "#sessions .session", ".proj-head"), "border-radius:999px")
    out += _rule((".pop-item", ".proj-menu-item", ".home-options .side-foot-btn", ".vp-item", ".vf-item"), f"border-radius:{min(r, 12)}px")
    out += _rule(HOVERED, "background:var(--tb-panel-hover)")
    out += _rule(SELECTED, f"background:{gloss},var(--tb-select-bg,var(--field));border-radius:999px;box-shadow:inset 0 0 0 1px var(--accent),0 0 14px -3px var(--tb-glow)")
    out += _rule((".sw",), f"border:{w}px solid {dim};background:var(--field);box-shadow:inset 0 2px 4px var(--tb-soft-shadow)")
    out += _rule((".sw::after",), f"background:radial-gradient(circle at 40% 35%,#ffffff,{chrome} 70%)")
    out += _rule(('.sw[aria-checked="true"]',), "background:linear-gradient(180deg,var(--tb-accent-hi),var(--accent));border-color:var(--accent)")
    out += _rule((".card .row", ".tb-preview-box"), "border-color:var(--tb-rim-faint)")
    out += _rule(TAGS, "border-radius:999px")
    out += _rule((".tb-thumb", ".tb-preview"), f"border:{w}px solid {dim};border-radius:{r}px")
    out += _rule((HEADER + " .brand img",), "border-radius:12px;box-shadow:0 0 0 2px #ffffff,0 3px 10px var(--tb-soft-shadow)")
    out += _rule(TOGGLED, "border-color:var(--accent);background:var(--field);box-shadow:0 0 0 3px var(--tb-glow)")
    out += _rule(KEYCAPS, f"border:1px solid {dim};border-radius:6px;background:{gloss},var(--field);box-shadow:0 1px 3px var(--tb-soft-shadow)")
    out += _rule(("#xpanel",), f"border-left:{w}px solid {dim}")
    out += _rule((".search-bar",), f"border-bottom-color:{dim}")
    return out + _focus_ring()


def _chamfer(cut):
    a, b = f"{cut}px", f"calc(100% - {cut}px)"
    return f"clip-path:polygon({a} 0,{b} 0,100% {a},100% {b},{b} 100%,{a} 100%,0 {b},0 {a})"


def _cut_lines(cut, colour):
    """Thin diagonal strokes on the four cut corners, so the clipped shape keeps an outline."""
    size, edge = cut + 1, f"{cut - 0.8:.1f}px"
    tl = f"linear-gradient(135deg,transparent {edge},{colour} {edge} {cut + 0.6:.1f}px,transparent {cut + 0.6:.1f}px)"
    tr = f"linear-gradient(225deg,transparent {edge},{colour} {edge} {cut + 0.6:.1f}px,transparent {cut + 0.6:.1f}px)"
    bl = f"linear-gradient(45deg,transparent {edge},{colour} {edge} {cut + 0.6:.1f}px,transparent {cut + 0.6:.1f}px)"
    br = f"linear-gradient(315deg,transparent {edge},{colour} {edge} {cut + 0.6:.1f}px,transparent {cut + 0.6:.1f}px)"
    return (f"{tl} left top/{size}px {size}px no-repeat,{tr} right top/{size}px {size}px no-repeat,"
            f"{bl} left bottom/{size}px {size}px no-repeat,{br} right bottom/{size}px {size}px no-repeat")


def _chamfer_frame(w, r):
    rim, sheen = "var(--tb-rim)", "linear-gradient(180deg,var(--tb-sheen),transparent 60%)"
    out = _rule(BUTTONS, f"border:{w}px solid {rim};border-radius:0;{_chamfer(7)};background:{_cut_lines(7, 'var(--tb-rim)')},{sheen},var(--field);"
                "color:var(--ink);box-shadow:inset 0 0 0 1px var(--tb-rim-glow),inset 0 -10px 16px -12px var(--tb-glow)")
    out += _rule(BUTTONS, f"border-color:var(--accent);background:{_cut_lines(7, 'var(--accent)')},{sheen},var(--tb-hover);"
                 "box-shadow:inset 0 0 0 1px var(--accent),inset 0 0 16px -4px var(--tb-glow)", ":hover:not(:disabled)")
    out += _rule(BUTTONS, "box-shadow:inset 0 0 22px -2px var(--tb-glow)", ":active:not(:disabled)")
    out += _rule(BUTTONS, "box-shadow:none", ":disabled")
    out += _rule(PRIMARY, f"border:{w}px solid var(--tb-accent-hi);border-radius:0;{_chamfer(8)};"
                 "background:linear-gradient(180deg,var(--tb-accent-hi),var(--accent) 55%,var(--tb-accent-lo));color:var(--tb-accent-ink);font-weight:800;"
                 "letter-spacing:.06em;text-transform:uppercase;box-shadow:inset 0 0 0 1px rgba(255,255,255,.35),inset 0 -8px 14px -8px rgba(0,0,0,.4)")
    out += _rule(PRIMARY, "background:linear-gradient(180deg,var(--tb-accent-hi),var(--tb-accent-hover) 55%,var(--tb-accent-lo))", ":hover:not(:disabled)")
    out += _rule(FIELDS, f"border:{w}px solid var(--tb-rim-dim);border-radius:0;{_chamfer(9)};background:{_cut_lines(9, 'var(--tb-rim-dim)')},var(--field);"
                 "box-shadow:inset 0 0 0 1px var(--tb-rim-glow),inset 0 8px 18px -12px rgba(0,0,0,.7)")
    out += _rule(FIELDS, f"border-color:var(--accent);background:{_cut_lines(9, 'var(--accent)')},var(--field)", ":focus")
    out += _rule(PANELS, f"border:{w}px solid var(--tb-rim-dim);border-radius:0;{_chamfer(14)};box-shadow:inset 0 0 0 1px var(--tb-rim-glow),inset 0 0 30px -18px var(--tb-glow)")
    out += _rule((".msg.user",), f"border:{w}px solid var(--tb-rim);border-radius:0;{_chamfer(10)};background:{_cut_lines(10, 'var(--tb-rim)')},{sheen},var(--user);"
                 "box-shadow:inset 0 0 0 1px var(--tb-rim-glow)")
    out += _rule(MENUS, f"border:{w}px solid var(--tb-rim);border-radius:0;{_chamfer(12)};box-shadow:inset 0 0 0 1px var(--tb-rim-glow),inset 0 0 40px -20px var(--tb-glow)")
    out += _rule(SIDEBARS, f"border-right:0;border-inline-end:{w}px solid var(--tb-rim-dim)")
    out += _rule((HEADER,), "border-bottom:0")
    out += _rule(ROWS, f"border-radius:0;{_chamfer(6)}")
    out += _rule(("#nav .nav-item", "#sessions .session", ".proj-head"), "border-inline-start:3px solid transparent")
    out += _rule(HOVERED, "background:var(--tb-panel-hover);box-shadow:inset 0 0 0 1px var(--tb-rim-glow)")
    out += _rule(SELECTED, "background:linear-gradient(90deg,var(--tb-glow),transparent 70%),var(--field);border-inline-start-color:var(--accent);"
                 "box-shadow:inset 0 0 0 1px var(--tb-rim-glow)")
    out += _rule((".sw",), f"border:{w}px solid var(--tb-rim-dim);border-radius:0;{_chamfer(5)};background:var(--field)")
    out += _rule((".sw::after",), "border-radius:0;background:var(--muted)")
    out += _rule(('.sw[aria-checked="true"]',), "background:var(--tb-switch-on);border-color:var(--accent)")
    out += _rule(('.sw[aria-checked="true"]::after',), "background:var(--accent);box-shadow:0 0 8px var(--accent)")
    out += _rule((".card .row", ".tb-preview-box"), "border-color:var(--tb-rim-faint)")
    out += _rule(TAGS, f"border-radius:0;{_chamfer(4)}")
    out += _rule((".tb-thumb", ".tb-preview"), f"border:{w}px solid var(--tb-rim);border-radius:0;{_chamfer(10)}")
    out += _rule((HEADER + " .brand img",), f"border-radius:0;{_chamfer(6)}")
    out += _rule(TOGGLED, "border-color:var(--accent);box-shadow:inset 0 0 0 1px var(--accent),inset 0 0 14px -4px var(--tb-glow)")
    out += _rule(KEYCAPS, f"border:1px solid var(--tb-rim-dim);border-radius:0;{_chamfer(3)};background:var(--field)")
    out += _rule(("#xpanel",), f"border-left:{w}px solid var(--tb-rim-dim)")
    out += _rule((".search-bar",), "border-bottom-color:var(--tb-rim-dim)")
    return out + _focus_ring(2)


def _flat_frame(w, r):
    out = _rule(BUTTONS + FIELDS + PANELS + MENUS, f"border-color:var(--border);border-width:{w}px;border-radius:{r}px")
    out += _rule((".msg.user",), f"border-color:var(--border);border-width:{w}px;border-radius:{r}px")
    out += _rule(PRIMARY, f"border-radius:{r}px;background:var(--accent);color:var(--tb-accent-ink)")
    out += _rule((*SIDEBARS, HEADER), "border-color:var(--border)")
    out += _rule((".tb-thumb", ".tb-preview"), f"border-width:{w}px;border-radius:{r}px")
    return out


def _voxel_art(p):
    return {
        "stone": art.stone_tile(p),
        "turf": art.grass_cap(p),
        "planks": art.plank_tile(p, art.voxel_colours(p)["wood"]),
        "blocks": art.block_row(p),
        "bullet": art.label_block(p),
    }


def _voxel_ornament(w):
    cap_h = 2 * len(art.GRASS_CAP)
    turf = f"var(--tb-art-turf) 0 0/32px {cap_h}px repeat-x"
    out = _rule(SIDEBARS, f"background:{turf},var(--tb-art-stone) 0 0/48px 48px repeat,var(--panel)")
    out += _rule((HEADER,), f"background:{turf},var(--tb-dirt);padding-top:{cap_h + 3}px")
    out += "@media (max-height: 360px){" + _rule((HEADER,), f"padding-top:{cap_h}px") + "}\n"
    out += _rule((".side-foot",), f"background:var(--tb-art-planks) 0 0/32px 24px repeat,var(--tb-wood);border-top:{w}px solid var(--tb-edge)")
    out += _rule(PANEL_HEADS, f"background:var(--tb-art-planks) 0 0/32px 24px repeat,var(--tb-wood);border-bottom:{w}px solid var(--tb-edge)")
    out += _rule(("#paneTitle::after",), 'content:"";display:block;width:56px;height:16px;margin-top:12px;background:var(--tb-art-blocks) 0 0/56px 16px no-repeat')
    out += _rule((".empty-greet::before",), 'content:"";display:block;width:56px;height:16px;margin:0 auto 16px;background:var(--tb-art-blocks) 0 0/56px 16px no-repeat')
    out += _rule(tuple(s + "::before" for s in LABELS),
                 'content:"";display:inline-block;width:10px;height:10px;margin-inline-end:7px;vertical-align:-1px;background:var(--tb-art-bullet) 0 0/10px 10px no-repeat')
    out += "@media (max-width: 720px){" + _rule(("#side",), f"border-inline-end:0;border-bottom:{w}px solid var(--tb-edge)") + "}\n"
    return out


def _leaf_art(p):
    art_map = {
        "contour": art.contour_tile(p),
        "crest": art.crest(p),
        "leaf": art.leaf_marker(p),
        "fleuron": art.fleuron(p),
        "gem": art.diamond(p),
    }
    art_map.update({"corner-" + c: art.bracket(p, c) for c in ("tl", "tr", "bl", "br")})
    return art_map


def _leaf_ornament(w):
    brackets = ",".join(
        f"var(--tb-art-corner-{corner}) {pos}/18px 18px no-repeat"
        for corner, pos in (("tl", "left 5px top 5px"), ("tr", "right 5px top 5px"), ("bl", "left 5px bottom 5px"), ("br", "right 5px bottom 5px"))
    )
    out = _rule(SIDEBARS, "background:var(--tb-art-contour) 0 0/320px 320px repeat,var(--panel)")
    out += _rule((HEADER,), "background:var(--tb-art-crest) center bottom 4px/96px 15px no-repeat,"
                 "linear-gradient(var(--accent),var(--accent)) left bottom/100% 1px no-repeat,"
                 "linear-gradient(var(--tb-rim-dim),var(--tb-rim-dim)) left bottom 3px/100% 1px no-repeat,var(--panel);"
                 "padding-bottom:16px")
    out += "@media (max-height: 360px){" + _rule((HEADER,), "padding-bottom:6px;background-size:0 0,100% 1px,100% 1px") + "}\n"
    out += _rule(BRACKETED, f"background:{brackets},var(--panel)")
    out += _rule((".modal",), f"background:{brackets},var(--panel-solid)")
    out += _rule(PANEL_HEADS, "border-bottom:1px solid var(--accent);box-shadow:0 3px 0 -2px var(--tb-rim-dim)")
    out += _rule(("#paneTitle::after",), 'content:"";display:block;width:160px;height:14px;margin-top:10px;background:var(--tb-art-fleuron) 0 0/160px 14px no-repeat')
    out += _rule((".empty-sub::after",), 'content:"";display:block;width:160px;height:14px;margin:14px auto 0;background:var(--tb-art-fleuron) 0 0/160px 14px no-repeat')
    out += _rule(tuple(s + "::before" for s in LABELS),
                 'content:"";display:inline-block;width:8px;height:8px;margin-inline-end:8px;vertical-align:0;background:var(--tb-art-gem) 0 0/8px 8px no-repeat')
    out += _rule(('.nav-item[aria-current="true"]::after',),
                 'content:"";flex:0 0 12px;width:12px;height:12px;margin-inline-start:auto;background:var(--tb-art-leaf) center/12px 12px no-repeat')
    out += "@media (max-width: 720px){" + _rule(("#side",), f"border-inline-end:0;border-bottom:{w}px solid var(--tb-rim-dim)") + "}\n"
    return out


def _rgba(colour, alpha):
    r, g, b = (int(colour[i : i + 2], 16) for i in (1, 3, 5))
    return f"rgba({r},{g},{b},{alpha})"


def _effects(skin):
    """Motion and light effects. Application-owned, off under reduced motion, never over a control."""
    p, w, frame, ornament = skin["palette"], skin["borderWidth"], skin["frame"], skin["ornament"]
    out = ""
    if frame == "pixel":
        out += _rule(BUTTONS + PRIMARY, "transform:translateY(1px)", ":active:not(:disabled)")
        out += f"@keyframes tb-pop{{0%{{box-shadow:inset 0 0 0 {2 * w}px var(--tb-accent-hi)}}100%{{box-shadow:inset 0 0 0 {w}px var(--accent),inset 0 0 0 {2 * w}px var(--tb-lo-soft)}}}}\n"
        out += _rule(SELECTED, "animation:tb-pop .24s steps(3,end) 1")
    if frame == "etched":
        out += _rule(BUTTONS + PRIMARY + ROWS + FIELDS, "transition:background-color .2s,border-color .2s,box-shadow .2s")
        out += _rule(BUTTONS, "box-shadow:0 0 0 1px var(--tb-glow),0 0 14px -3px var(--tb-glow)", ":hover:not(:disabled)")
        if art.contrast(p["accentInk"], "#000000") < art.contrast(p["accent"], "#000000"):
            # a light sweep across brass only ever raises contrast with dark button text
            out += "@keyframes tb-sheen{from{background-position:130% 0,0 0}to{background-position:-60% 0,0 0}}\n"
            out += _rule(PRIMARY, "background:linear-gradient(110deg,transparent 35%,rgba(255,255,255,.34) 50%,transparent 65%) 130% 0/260% 100% no-repeat,"
                         "var(--tb-accent-hover);animation:tb-sheen .9s ease-out 1", ":hover:not(:disabled)")
    if ornament == "voxel":
        cap_h = 2 * len(art.GRASS_CAP)

        def sway(layers):
            return ("{" + "0%,49.9%{background-position:" + ",".join(["0 0"] * layers) + "}50%,100%{background-position:2px 0,"
                    + ",".join(["0 0"] * (layers - 1)) + "}}")

        out += "@keyframes tb-turf2" + sway(2) + "\n@keyframes tb-turf3" + sway(3) + "\n"
        out += _rule((HEADER,), "animation:tb-turf2 5s steps(1,end) infinite")
        out += _rule(SIDEBARS, "animation:tb-turf3 5s steps(1,end) infinite")
        out += "@keyframes tb-bob{0%,100%{transform:translateY(0)}50%{transform:translateY(-4px)}}\n"
        out += _rule((".empty-greet::before",), "animation:tb-bob 2.8s steps(4,end) infinite")
        motes = ",".join(f"{x}px {y}px 0 var(--tb-mote)" for x, y in ((-170, 30), (-120, 90), (-60, 10), (70, 60), (140, 20), (190, 100), (-210, 120), (20, 130)))
        out += "@keyframes tb-motes{0%{transform:translateY(24px);opacity:0}20%{opacity:.9}100%{transform:translateY(-36px);opacity:0}}\n"
        out += _rule((".empty",), "position:relative")
        out += _rule((".empty::after",), f'content:"";position:absolute;left:50%;top:0;width:{cap_h // 4}px;height:{cap_h // 4}px;'
                     f"background:var(--tb-mote);box-shadow:{motes};pointer-events:none;animation:tb-motes 7s steps(14,end) infinite")
    if ornament == "leaf":
        out += "@keyframes tb-leaf{0%,100%{transform:rotate(-8deg)}50%{transform:rotate(7deg)}}\n"
        out += _rule(('.nav-item[aria-current="true"]::after',), "transform-origin:15% 85%;animation:tb-leaf 4.5s ease-in-out infinite")
        flies = ",".join(f"{x}px {y}px 3px 1px var(--tb-glow)" for x, y in ((-180, 40), (-90, 110), (-30, 0), (80, 70), (150, 20), (210, 120)))
        out += "@keyframes tb-flies{0%{transform:translate(0,10px);opacity:.15}50%{opacity:.85}100%{transform:translate(14px,-22px);opacity:.2}}\n"
        out += _rule((".empty",), "position:relative")
        out += _rule((".empty::after",), 'content:"";position:absolute;left:50%;top:10px;width:3px;height:3px;border-radius:50%;'
                     f"background:var(--tb-glow);box-shadow:{flies};pointer-events:none;animation:tb-flies 6s ease-in-out infinite alternate")
    if frame == "toon":
        out += _rule(BUTTONS + PRIMARY, f"transform:translateY({w}px)", ":active:not(:disabled)")
        out += "@keyframes tb-hop{0%{transform:translateY(-3px)}60%{transform:translateY(1px)}100%{transform:none}}\n"
        out += _rule(SELECTED, "animation:tb-hop .32s ease-out 1")
    if frame == "capsule":
        out += "@keyframes tb-beam{from{box-shadow:inset 0 0 0 1px var(--accent),0 0 22px 2px var(--tb-glow)}to{box-shadow:inset 0 0 0 1px var(--accent),0 0 12px -3px var(--tb-glow)}}\n"
        out += _rule(SELECTED, "animation:tb-beam .6s ease-out 1")
        if art.contrast(p["accentInk"], "#000000") < art.contrast(p["accent"], "#000000"):
            out += "@keyframes tb-glint{from{background-position:130% 0,0 0,0 0}to{background-position:-60% 0,0 0,0 0}}\n"
            out += _rule(PRIMARY, "background:linear-gradient(110deg,transparent 35%,rgba(255,255,255,.3) 50%,transparent 65%) 130% 0/260% 100% no-repeat,"
                         "linear-gradient(180deg,var(--tb-gloss-accent) 0 46%,transparent 54%),var(--tb-accent-hover);animation:tb-glint .9s ease-out 1",
                         ":hover:not(:disabled)")
    if ornament in ORNAMENTS:
        out += ORNAMENTS[ornament]["effects"](skin)
    return "@media (prefers-reduced-motion: no-preference){" + out + "}\n" if out else ""


def _pinned():
    # stated twice, plain and hovered, so a frame's hover rule never outranks a meaning colour
    return "".join(_rule(selectors, body) + _rule(selectors, body, ":hover") for selectors, body in PINNED)


_MONO = 'ui-monospace,"SF Mono",Menlo,Consolas,monospace'
_SERIF = '"Iowan Old Style","Palatino Linotype",Palatino,"Book Antiqua",Georgia,serif'
_ROUNDED = 'ui-rounded,"SF Pro Rounded","Arial Rounded MT Bold","Nunito","Varela Round",system-ui,sans-serif'
_SLAB = '"Rockwell","Roboto Slab","Zilla Slab","American Typewriter",Georgia,serif'


def _typography(kind):
    if kind == "mono-heading":
        out = _rule(HEADINGS + LABELS, f"font-family:{_MONO}")
        return out + _rule(("h1", ".empty-greet", ".tb-title"), "letter-spacing:.04em;font-weight:800")
    if kind == "serif-heading":
        out = _rule(HEADINGS, f"font-family:{_SERIF};font-weight:600")
        out += _rule(("h1", ".empty-greet"), "letter-spacing:.01em")
        return out + _rule(LABELS, f"font-family:{_SERIF};letter-spacing:.16em;font-weight:600")
    if kind == "rounded-heading":
        out = _rule(HEADINGS + LABELS, f"font-family:{_ROUNDED}")
        return out + _rule(("h1", ".empty-greet", ".tb-title"), "font-weight:800;letter-spacing:.005em")
    if kind == "slab-heading":
        out = _rule(HEADINGS, f"font-family:{_SLAB};font-weight:700;letter-spacing:.015em")
        return out + _rule(LABELS, f"font-family:{_SLAB};letter-spacing:.1em")
    return ""


def skin_css(manifest):
    if manifest is None:
        return ""
    if manifest.get("renderer_contract") == "2.0":
        from core.toybox_stage import render

        return render(manifest)
    skin = manifest["skin"]
    palette = skin["palette"]
    tokens = [f"--{name}:{palette[field]}" for field, names in ALIASES.items() for name in names]
    tokens += [f"--{name}:{value}" for name, value in _tokens(skin).items()]
    builders = {"voxel": _voxel_art, "leaf": _leaf_art, **{name: o["art"] for name, o in ORNAMENTS.items()}}
    artwork = builders.get(skin["ornament"], lambda _: {})(palette)
    tokens += [f"--tb-art-{name}:{art.css_url(markup)}" for name, markup in artwork.items()]
    # --bg2 is the toast surface; the pages never define it, so a skin gives it the panel colour
    tokens.append("--bg2:" + palette["panel"])
    accent_text = _readable_variant(palette, 4.55)
    focus = _readable_variant(palette, 3.05)
    if accent_text != palette["accent"]:
        tokens.append("--tb-accent-text:" + accent_text)
    if focus != palette["accent"] and skin["frame"] not in NEW_FRAMES:
        tokens.append("--tb-focus:" + focus)
    scheme = "color-scheme:" + ("dark" if art.is_dark(palette) else "light") + ";"
    css = (PAGE_TOKENS_SELECTOR + "{" + scheme + ";".join(tokens)
           + ";--grad:linear-gradient(135deg," + palette["accent"] + "," + palette["accent"] + ")}\n")
    w, r = skin["borderWidth"], skin["corners"]
    css += _rule((".tb-grid", HEADER), f"gap:{skin['gap']}px")
    pad = 6 if skin["density"] == "compact" else 10
    css += _rule((".nav-item", ".side-foot-btn", ".chat-item"), f"padding-top:{pad}px;padding-bottom:{pad}px")
    css += _rule(BUTTONS, "font-family:inherit")
    css += _rule(CHECKS, "accent-color:var(--accent)")
    css += _typography(skin["typography"])
    frames = {"pixel": lambda: _pixel_frame(w), "etched": lambda: _etched_frame(w, r), "flat": lambda: _flat_frame(w, r),
              "toon": lambda: _toon_frame(w, r), "capsule": lambda: _capsule_frame(w, r), "chamfer": lambda: _chamfer_frame(w, r)}
    css += frames[skin["frame"]]()
    if skin["ornament"] == "voxel":
        css += _voxel_ornament(w)
    elif skin["ornament"] == "leaf":
        css += _leaf_ornament(w)
    elif skin["ornament"] in ORNAMENTS:
        css += ORNAMENTS[skin["ornament"]]["css"](skin)
    if accent_text != palette["accent"]:
        css += _rule(ACCENT_TEXT, "color:var(--tb-accent-text)")
    if focus != palette["accent"]:
        css += _rule(("*",), "outline-color:var(--tb-focus)", ":focus-visible")
    css += _effects(skin) + _pinned()
    # Only cosmetic owners are styled. These immutable islands retain VOOL's
    # provider identity, permission wording, costs, status colours and receipts: they get
    # VOOL's own tokens back, for the theme the person chose.
    return css + ISLAND_RESET


def current_skin_style():
    from core.toybox import state

    return '<style id="vool-skin-style">' + state()["css"] + "</style>"


PREVIEW_CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:"

# The preview uses the real pages' element ids, classes and base rules for the chat
# component families, so the very same skin rules style it. Synthetic text only.
_PREVIEW_BASE = """:root{--bg:#101216;--panel:#16191f;--panel-solid:#16191f;--chat:#101216;--field:#1d2129;--ink:#e8eaf0;--text:#e8eaf0;--muted:#9aa1af;--accent:#5eead4;--accent2:#34d399;--grad:linear-gradient(135deg,#5eead4,#34d399);--user:#233043;--border:#262b35;--active:#1d2129;--warn:#f0b429}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:13px/1.5 -apple-system,'Inter',system-ui,Segoe UI,Roboto,sans-serif;display:flex;height:100vh;overflow:hidden}
#sidebar{flex:0 0 184px;width:184px;background:var(--panel);border-right:1px solid var(--border);display:flex;flex-direction:column;min-height:0}
#sidebar .side-head{display:flex;align-items:center;gap:6px;padding:14px 10px 8px}#sidebar .side-title{font-size:11px;font-weight:700;letter-spacing:.6px;color:var(--muted);text-transform:uppercase;flex:1}
#newChat{background:transparent;color:var(--accent);border:1px solid var(--border);border-radius:8px;padding:4px 9px;font:inherit;font-size:12px;font-weight:600}
#sessions{flex:1;padding:4px 8px;display:flex;flex-direction:column;gap:4px}#sessions .session{display:flex;color:var(--ink);border:1px solid transparent;border-radius:8px;padding:6px 8px;font-size:12.5px}
#sessions .session.active{background:var(--active);border-color:var(--border)}.side-foot{padding:8px 10px 10px;border-top:1px solid var(--border)}
.home-menu>summary{display:flex;gap:8px;list-style:none;padding:8px 10px;border:1px solid var(--border);border-radius:10px;font-weight:600}.home-menu>summary::-webkit-details-marker{display:none}
#main{flex:1;display:flex;flex-direction:column;min-width:0}#main>header{display:flex;align-items:center;gap:10px;min-height:48px;padding:7px 14px;border-bottom:1px solid var(--border);background:var(--panel)}
#main>header .brand{display:inline-flex;align-items:center;gap:8px;font-weight:800;letter-spacing:.14em}#main>header .brand i{display:block;width:26px;height:26px;background:#000;border-radius:8px}
#main>header .spacer{flex:1}.ctrl{position:relative;display:inline-flex}.ctrl-btn{display:inline-flex;align-items:center;gap:6px;background:var(--bg);color:var(--ink);border:1px solid var(--border);border-radius:8px;padding:4px 9px;font:inherit;font-size:12px}
.model-lane{border-radius:999px;padding:1px 6px;font-size:9px;line-height:16px;font-weight:800;letter-spacing:.08em;color:#c4b5fd;background:#27213a}
#searchBtn,#panelBtn{background:transparent;color:var(--muted);border:1px solid var(--border);border-radius:8px;padding:4px 9px;font:inherit;font-size:12px}#panelBtn{color:var(--accent)}
#log{flex:1;overflow:hidden;padding:18px 22px;display:flex;flex-direction:column;gap:10px;background:var(--chat)}.msg{max-width:78%;padding:9px 30px 9px 12px;border-radius:12px}
.msg.user{align-self:flex-end;background:var(--user);border:1px solid var(--border)}.msg.assistant{align-self:stretch;max-width:100%;padding:4px 0;border:0;background:transparent;font-size:13.5px;line-height:1.6}
footer{padding:8px 18px 14px;display:flex;flex-direction:column;gap:8px}.perm-bar{display:flex;align-items:center;gap:8px;padding:7px 10px;border:1px solid var(--warn);border-radius:12px;font-size:12px}
.perm-btn{border:1px solid var(--border);background:var(--field);color:var(--ink);border-radius:8px;padding:3px 9px}.composer-row{display:flex;gap:8px}
#input{flex:1;min-height:42px;background:var(--field);color:var(--muted);border:1px solid var(--border);border-radius:10px;padding:10px 12px}
#send{background:var(--grad);color:#fff;border:none;border-radius:10px;padding:0 16px;font:inherit;font-weight:700}"""

_PREVIEW_BODY = (
    '<aside id="sidebar"><div class="side-head"><span class="side-title">Chats</span><button id="newChat" type="button">+ New</button></div>'
    '<div id="sessions"><div class="session active">Trail plan</div><div class="session">Garden notes</div><div class="session">Recipe swap</div></div>'
    '<div class="side-foot"><details class="home-menu"><summary>&#8962; Home</summary></details></div></aside>'
    '<div id="main"><header><span class="brand"><i></i>VOOL</span>'
    '<div class="ctrl" id="modelCtrl"><span class="ctrl-btn"><span class="model-lane">AUTO</span>VOOL Auto</span></div>'
    '<span class="spacer"></span><button id="searchBtn" type="button">Search</button><button id="panelBtn" type="button">Activity</button></header>'
    '<div id="log"><div class="msg user">Plan a weekend hike near the river.</div>'
    '<div class="msg assistant">Start at the north trailhead, follow the river path for six kilometres, then loop back over the ridge. Pack water and a light jacket.</div></div>'
    '<footer><div id="permBar" class="perm-bar" role="alertdialog"><b>Permission needed</b><span class="perm-btn">Allow once</span><span class="perm-btn">Deny</span></div>'
    '<div class="composer-row"><div id="input">Message VOOL&hellip;</div><button id="send" type="button">Send</button></div></footer></div>'
)


def preview_html(manifest):
    """Scriptless bridge-free preview of the chat component families; never embeds owner data."""
    from core.toybox_font import css as font_css
    title = html.escape(manifest["name"])
    pixel = manifest.get("stage", {}).get("type") == "pixel-display"
    policy = PREVIEW_CSP + ("; font-src data:" if pixel else "")
    font = font_css() if pixel else ""
    body = _PREVIEW_BODY
    if manifest.get("stage", {}).get("layout") == "dock":
        icons = "".join("<i style='display:block;width:26px;height:26px;border:2px solid currentColor;border-radius:6px'></i>" for _ in range(4))
        body = "<nav id='tb-dock' class='tb-root' aria-hidden='true'>"+icons+"</nav>"+body
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        f'<meta http-equiv="Content-Security-Policy" content="{policy}">'
        f"<title>{title}</title><style>{font}{_PREVIEW_BASE}</style><style>{skin_css(manifest)}</style></head>"
        f"<body data-vool-stage=\"chat\">{body}</body></html>"
    )


def render_toybox_fragment():
    import json

    from core.i18n.catalog import load_catalog_data
    from core.toybox_chrome import CHROME_JS
    from core.toybox_stage import STAGE_JS, governor_css

    english = {key.removeprefix("toybox."): value for key, value in load_catalog_data("en")["messages"].items() if key.startswith("toybox.")}
    fragment = _TOYBOX_FRAGMENT.replace("__TOYBOX_ENGLISH__", json.dumps(english, ensure_ascii=True).replace("<", "\\u003c"))
    from core.pet_cosmetics_fragment import render_pet_cosmetics_fragment
    from core.toybox_font import css as font_css

    return '<style>'+font_css()+'</style>' + render_pet_cosmetics_fragment() + '<style id="vool-stage-governor">' + governor_css() + '</style><script>' + CHROME_JS.replace('"__TB_PROTECTED__"', json.dumps(TRUSTED)) + STAGE_JS + '</script>' + fragment


_TOYBOX_FRAGMENT = r"""<style>
.tb-root{min-width:0}.tb-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,280px),1fr));gap:18px;margin-block:20px}.tb-card{min-width:0;border:1px solid var(--border);background:var(--panel);padding:14px;border-radius:12px}.tb-title{font-weight:750;margin:0}.tb-muted{color:var(--muted);font-size:13px;line-height:1.6}.tb-toolbar{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.tb-btn{font:inherit;font-size:13px;min-height:36px;padding:7px 12px;border:1px solid var(--border);border-radius:8px;color:var(--ink);background:var(--field);cursor:pointer}.tb-btn:focus-visible,.tb-link:focus-visible,.tb-options select:focus-visible{outline:3px solid var(--border);outline-offset:3px}.tb-primary{background:var(--accent);color:var(--tb-accent-ink,#0b0f14);font-weight:700}.tb-root[aria-busy=true] .tb-btn{opacity:.65;pointer-events:none}.tb-thumb{display:block;width:100%;height:auto;aspect-ratio:8/5;border:1px solid var(--border);border-radius:8px;background:var(--bg)}.tb-mini{position:relative;height:200px;overflow:hidden;contain:layout paint;border-radius:8px;border:1px solid var(--border);background:var(--bg)}.tb-mini iframe{position:absolute;left:0;top:0;display:block;width:200%;height:400px;border:0;transform:scale(.5);transform-origin:top left;pointer-events:none}.tb-name{font-size:16px;margin:12px 0 0;overflow-wrap:anywhere}.tb-meta{margin:4px 0;font-size:12px}.tb-card .tb-desc{margin:8px 0 14px}.tb-badge{font-size:12px;font-weight:700;color:var(--tb-accent-text,var(--accent))}.tb-link{color:var(--tb-accent-text,var(--accent));font-size:13px}.tb-preview-box:empty{display:none}.tb-preview-box{padding:18px 0;border-block:1px solid var(--border)}.tb-preview{display:block;width:100%;height:470px;border:1px solid var(--border);border-radius:10px;background:var(--bg)}.tb-import{padding:16px 0}.tb-import summary{cursor:pointer;font-weight:700}.tb-editor{width:100%;min-height:180px;resize:vertical;box-sizing:border-box;background:var(--field);color:var(--ink);border:1px solid var(--border);border-radius:8px;padding:12px;font:12px/1.5 ui-monospace,monospace}.tb-import input{display:block;margin:10px 0;max-width:100%}.tb-message{font-size:13px;line-height:1.6;white-space:pre-wrap;overflow-wrap:anywhere}.tb-error{color:var(--ink);background:var(--field);border:2px solid var(--border);border-radius:8px;padding:10px}.tb-options{display:flex;gap:14px;flex-wrap:wrap;padding:14px 0;border-block:1px solid var(--border);margin-block:16px}.tb-options label{display:flex;align-items:center;gap:8px;font-size:13px}.tb-options select{font:inherit;min-height:36px;border:1px solid var(--border);border-radius:6px;background:var(--field);color:var(--ink);padding:5px}.tb-footnote{font-size:12px}.tb-applied{animation:tb-apply .28s ease-out}@keyframes tb-apply{from{opacity:.5}to{opacity:1}}@media(prefers-reduced-motion:reduce){.tb-applied{animation:none}}@media(max-width:600px){.tb-card{padding:12px}.tb-options{gap:10px}.tb-preview{height:420px}}
</style><script>(function(){
'use strict';
const english=__TOYBOX_ENGLISH__;
const tr=(key,values={})=>{const full='toybox.'+key;let text=window.VOOLT?window.VOOLT(full):full;if(text===full)text=english[key]||key;return text.replace(/\{(\w+)\}/g,(_,k)=>String(values[k]??''));};
let canonical=null,preview=null,epoch=0,busy=false,host=null,cards=null,status=null,previewBox=null,message=null,motion=null,layout=null,demo=null,demoTimers=[];
const node=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;};
const button=(key,handler,cls='')=>{const b=node('button','tb-btn '+cls,tr(key));b.type='button';b.onclick=handler;return b;};
const prefs=()=>({motion:motion?motion.value:'full',layout_override:layout?layout.value:'skin'});
function paint(css,id){let s=document.getElementById('vool-skin-style');if(!s){s=node('style');s.id='vool-skin-style';document.head.appendChild(s);}if(s.textContent!==(css||''))s.textContent=css||'';const selected=id||'default';if(document.documentElement.dataset.skin!==selected)document.documentElement.dataset.skin=selected;}
function say(key,bad=false){if(message&&message.isConnected){message.textContent=tr(key);message.classList.toggle('tb-error',bad);}}
async function request(action,body){const r=await fetch('/api/toybox'+(action?'/'+action:''),action?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{cache:'no-store'});const j=await r.json();if(!r.ok||!j.ok){const e=new Error();e.code=j.error;throw e;}return j;}
function accept(s){if(canonical&&s.revision<canonical.revision)return;const changed=!canonical||s.revision!==canonical.revision;canonical=(host||document.getElementById('paneBody'))?s:{revision:s.revision,digest:s.digest,selected:s.selected,css:s.css,motion:s.motion,layout_override:s.layout_override,warning:s.warning,choices:[]};if(!preview){paint(s.css,s.selected&&s.selected.id);if(window.VoolStage)window.VoolStage.selection(s);if(motion)motion.value=s.motion;if(layout)layout.value=s.layout_override;}if(host&&host.isConnected){status.textContent=s.warning?tr('error'):tr('active',{name:s.selected?skinName(s.selected):tr('default')});if(changed||!cards.childElementCount)drawCards();}}
async function refresh(){try{accept(await request(''));}catch(_){if(host&&host.isConnected)say('read_failed',true);}}
function skinName(m){return english['skin_'+m.id+'_name']?tr('skin_'+m.id+'_name'):m.name;}
function skinDescription(m){return english['skin_'+m.id+'_description']?tr('skin_'+m.id+'_description'):m.description;}
function source(markup,state='idle',movement='off'){const theme=['light','dark','system'].includes(document.documentElement.dataset.theme)?document.documentElement.dataset.theme:'dark';return markup.replace('<html>','<html dir="'+(document.documentElement.dir==='rtl'?'rtl':'ltr')+'" data-theme="'+theme+'" data-vool-activity="'+state+'" data-vool-motion="'+movement+'">');}
function stopDemo(){for(const timer of demoTimers)clearTimeout(timer);demoTimers=[];if(demo&&demo.frame.isConnected)demo.frame.srcdoc=source(demo.markup);demo=null;}
function cancel(){stopDemo();epoch++;preview=null;if(previewBox)previewBox.replaceChildren();if(canonical)accept(canonical);say('preview_closed');}
async function showPreview(raw){if(busy)return;const target=host,ticket=++epoch;say('checking');try{if(!canonical)accept(await request(''));const revision=canonical.revision,result=await request('preview',{manifest:raw});if(ticket!==epoch||host!==target||!host||!host.isConnected)return;preview={raw,revision,digest:result.digest};paint(result.css,result.manifest.id);if(window.VoolStage)window.VoolStage.selection(prefs());previewBox.replaceChildren();const bar=node('div','tb-toolbar');bar.append(node('strong','tb-title',skinName(result.manifest)+' · '+tr('preview')),button('apply',apply,'tb-primary'),button('cancel',cancel));previewBox.append(bar,node('p','tb-muted',tr('preview_help')));const frame=node('iframe','tb-preview');frame.title=tr('preview');frame.setAttribute('sandbox','');frame.srcdoc=source(result.preview);previewBox.append(frame);const demo=button('demo',()=>demoFrame(frame,result.preview));previewBox.append(demo);say('preview_only');previewBox.scrollIntoView({block:'nearest',behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth'});}catch(e){if(ticket===epoch)say(e.code==='skin_revision_conflict'?'conflict':'error',true);}}
function demoFrame(frame,markup){stopDemo();if(matchMedia('(prefers-reduced-motion: reduce)').matches||prefs().motion==='off')return;const ticket=epoch;demo={frame,markup};frame.srcdoc=source(markup,'thinking',prefs().motion);demoTimers.push(setTimeout(()=>{if(ticket===epoch&&frame.isConnected)frame.srcdoc=source(markup,'streaming',prefs().motion);},500),setTimeout(()=>{if(ticket===epoch&&frame.isConnected)frame.srcdoc=source(markup,'success',prefs().motion);},1000),setTimeout(stopDemo,1500));}
for(const event of ['blur','visibilitychange'])window.addEventListener(event,()=>{if(event==='blur'||document.hidden)stopDemo();});
matchMedia('(prefers-reduced-motion: reduce)').addEventListener('change',stopDemo);
async function mutate(action,body){if(busy)return;stopDemo();busy=true;if(host)host.setAttribute('aria-busy','true');try{const result=await request(action,body);preview=null;epoch++;if(previewBox)previewBox.replaceChildren();accept(result);say(action==='restore'?'restored':action==='remove'?'removed':action==='configure'?'options_saved':'saved');if(host&&prefs().motion!=='off'&&!matchMedia('(prefers-reduced-motion: reduce)').matches){host.classList.remove('tb-applied');requestAnimationFrame(()=>host.classList.add('tb-applied'));}}catch(e){preview=null;epoch++;if(previewBox)previewBox.replaceChildren();await refresh();say(e.code==='skin_revision_conflict'?'conflict':'error',true);}finally{busy=false;if(host)host.removeAttribute('aria-busy');}}
function apply(){if(preview&&!busy)mutate('apply',{manifest:preview.raw,revision:preview.revision,confirmed:true,...prefs()});}
function download(manifest){const url=URL.createObjectURL(new Blob([JSON.stringify(manifest,null,2)+'\n'],{type:'application/json'}));const a=node('a');a.href=url;a.download=manifest.id+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
function drawCards(){stopDemo();cards.replaceChildren();for(const c of canonical.choices){const m=c.manifest,card=node('article','tb-card');card.dataset.skinId=m.id;if(c.preview){const wrap=node('div','tb-mini'),frame=node('iframe');frame.setAttribute('sandbox','');frame.setAttribute('aria-hidden','true');frame.loading='lazy';frame.tabIndex=-1;frame.srcdoc=source(c.preview);wrap.append(frame);card.append(wrap);card.addEventListener('pointerenter',()=>demoFrame(frame,c.preview));card.addEventListener('focusin',()=>demoFrame(frame,c.preview));}else{const thumb=node('img','tb-thumb');thumb.alt='';thumb.setAttribute('aria-hidden','true');thumb.width=320;thumb.height=200;if(typeof c.thumbnail==='string'&&c.thumbnail.startsWith('data:image/svg+xml,'))thumb.src=c.thumbnail;card.append(thumb);}card.append(node('h3','tb-title tb-name',skinName(m)),node('p','tb-muted tb-meta',tr(c.builtin?'builtin':'copy')+' · '+m.version),node('p','tb-muted tb-desc',skinDescription(m)));const controls=node('div','tb-toolbar');controls.append(button('preview',()=>showPreview(JSON.stringify(m)),'tb-primary'),button('download',()=>download(m)));if(c.digest===canonical.digest)controls.append(node('span','tb-badge',tr('active_badge')));if(!c.builtin)controls.append(button('remove',()=>mutate('remove',{digest:c.digest,revision:canonical.revision,confirmed:true})));card.append(controls);cards.append(card);}}
function select(key,options){const label=node('label',null,tr(key)),input=node('select');input.id='tb-'+key;for(const [value,name] of options){const option=node('option',null,tr(name));option.value=value;input.append(option);}label.append(input);return [label,input];}
function mount(target){if(host&&host!==target)cancel();epoch++;host=target;host.classList.add('tb-root');host.replaceChildren();const top=node('div','tb-toolbar');top.append(node('h2','tb-title',tr('title')),button('restore',()=>{if(canonical)mutate('restore',{revision:canonical.revision,confirmed:true});}),button('reload',refresh));const pet=node('a','tb-link',tr('pets'));pet.href='/pet-yard';pet.textContent=window.VoolPetCosmetics.t('open');top.append(pet);const options=node('div','tb-options');const [motionLabel,motionSelect]=select('motion',[['full','full'],['calm','calm'],['off','off']]),[layoutLabel,layoutSelect]=select('layout',[['skin','skin_layout'],['standard','standard']]);motion=motionSelect;layout=layoutSelect;options.append(motionLabel,layoutLabel,button('save_options',()=>{if(canonical)mutate('configure',{revision:canonical.revision,confirmed:true,...prefs()});}));for(const input of [motion,layout])input.onchange=()=>{stopDemo();if(preview&&window.VoolStage)window.VoolStage.selection(prefs());};status=node('p','tb-muted',tr('reading'));status.id='tb-active';message=node('p','tb-message');message.setAttribute('aria-live','polite');message.id='tb-message';cards=node('div','tb-grid');previewBox=node('section','tb-preview-box');previewBox.id='tb-preview-box';host.append(top,node('p','tb-muted',tr('intro')),options,status,previewBox,cards);const details=node('details','tb-import');details.append(node('summary',null,tr('import')),node('p','tb-muted',tr('import_help')));const editor=node('textarea','tb-editor');editor.id='tb-json';editor.maxLength=16384;editor.placeholder=tr('paste');editor.setAttribute('aria-label',tr('paste'));editor.spellcheck=false;const file=node('input');file.type='file';file.accept='.json,application/json';file.setAttribute('aria-label',tr('file'));file.onchange=async()=>{const f=file.files[0];if(!f)return;if(f.size>16384){say('size_error',true);return;}try{editor.value=await f.text();say('file_loaded');}catch(_){say('file_error',true);}};details.append(editor,file,button('validate',()=>showPreview(editor.value),'tb-primary'));host.append(details,message,node('p','tb-muted tb-footnote',tr('footnote')));if(window.VoolPetCosmetics)window.VoolPetCosmetics.mount(host);if(canonical)accept(canonical);refresh();}
window.VoolToybox={mountInto(target){mount(target);return true;}};window.addEventListener('focus',refresh);window.addEventListener('hashchange',()=>{if(preview)cancel();});document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});refresh();
})();</script>"""
