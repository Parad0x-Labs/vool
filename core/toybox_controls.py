"""Control drawings applied to the existing accessible elements."""
from __future__ import annotations


def css(kind):
    from core.toybox_fragment import _rule

    sets = {
        "keys": "border:2px solid var(--border);border-radius:7px;box-shadow:0 4px 0 var(--tb-edge),inset 0 2px 0 var(--tb-hi)",
        "dials": "border:3px double var(--border);border-radius:999px;box-shadow:inset 0 0 0 3px var(--field)",
        "glass": "border:1px solid var(--border);border-radius:18px;box-shadow:inset 0 2px 0 var(--tb-hi)",
        "ink": "border:2px solid var(--border);border-radius:3px 8px 3px 8px;box-shadow:2px 3px 0 var(--tb-rim-dim)",
    }
    if kind not in sets:
        return ""
    controls = ("#send", ".ctrl-btn", ".tb-btn", ".set-btn", ".switch", ".toggle", ".sw", "input[type=checkbox]", "#modelBtn", "#modeBtn", "#cloudBtn")
    out = _rule(controls, sets[kind] + ";min-height:32px;min-width:32px;background-color:var(--field);color:var(--ink)")
    out += _rule((".sw",), "min-width:44px;min-height:32px")
    out += _rule(("input[type=checkbox]",), "accent-color:var(--accent)")
    out += _rule(("#send", ".tb-primary"), "background:var(--accent);color:var(--accent-ink)")
    if kind == "ink":
        out += _rule(("#send",), "border-radius:50%;min-width:64px")
    out += _rule(controls, "transform:translateY(2px);box-shadow:none", ":active:not(:disabled)")
    out += _rule(controls, "outline:3px solid var(--tb-focus,var(--border));outline-offset:3px", ":focus-visible")
    out += _rule(("#sessions", "#log", "#pane"), "scrollbar-color:var(--border) var(--panel);scrollbar-width:thin")
    return out
