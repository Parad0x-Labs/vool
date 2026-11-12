"""Four original object skins using the bounded stage vocabulary."""
from __future__ import annotations

from core.i18n.catalog import load_catalog_data
from core.toybox import _example
from core.toybox_art_a import artwork


def _palette(bg, panel, field, accent, border, *, light=False):
    return dict(background=bg, panel=panel, field=field,
                ink="#18232d" if light else "#f5f3e9",
                muted="#46515d" if light else "#bec9cf", accent=accent,
                accentInk="#ffffff" if light else "#101821", border=border)


def _skin(identifier, dark, light, layout, controls, frame, type_role, scene, reactive):
    strings = load_catalog_data("en")["messages"]
    value = _example(identifier, strings[f"toybox.skin_{identifier}_name"],
                     strings[f"toybox.skin_{identifier}_description"], dark,
                     "flat", "none", 6, 2, "system", "2.0", "1.0.0")
    value["palettes"] = {"dark": dark, "light": light}
    value["stage"] = {"layout": layout, "controls": controls, "type": type_role,
                      "frame": {"set": frame}, "scene": {"id": scene, "density": "calm"},
                      "reactive": {"profile": reactive, "accentFrom": "accent"}}
    return value


SKINS = (
    _skin("pocket-transceiver",
          _palette("#111c18", "#1b2a22", "#15231d", "#b9d780", "#879e7c"),
          _palette("#eff2e4", "#fafbf1", "#e5ebd8", "#416039", "#61745a", light=True),
          "device", "keys", "bezel", "mono-readout", "none", "meter"),
    _skin("field-journal",
          _palette("#211b17", "#2c241e", "#241c18", "#e5bd7a", "#a4947b"),
          _palette("#f5eddb", "#fffaf0", "#eee3cc", "#774127", "#7c6a53", light=True),
          "spread", "ink", "book", "serif-display", "none", "bloom"),
    _skin("night-observatory",
          _palette("#101522", "#1c2335", "#151c2b", "#efd08a", "#8998b2"),
          _palette("#eef0f9", "#fafbff", "#e5e8f4", "#514676", "#6a6c88", light=True),
          "dock", "dials", "brass", "serif-display", "stars", "sweep"),
    _skin("midnight-cabinet",
          _palette("#1a1026", "#271b36", "#1d142b", "#f4bcf6", "#a28bb8"),
          _palette("#f5effb", "#ffffff", "#ece2f5", "#713b92", "#7d648e", light=True),
          "console", "keys", "cabinet", "pixel-display", "grid", "meter"),
)

__all__ = ["SKINS", "artwork"]
