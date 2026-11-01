"""Four original places, using the closed stage vocabulary."""
from __future__ import annotations

from core.i18n.catalog import load_catalog_data
from core.toybox import _example
from core.toybox_art_b import artwork


def _palette(bg, panel, field, accent, border, light=False):
    return dict(background=bg, panel=panel, field=field,
                ink="#18232d" if light else "#f5f3e9", muted="#46515d" if light else "#bec9cf",
                accent=accent, accentInk="#ffffff" if light else "#101821", border=border)


def _skin(identifier, dark, light, layout, controls, frame, display, scene, profile):
    strings = load_catalog_data("en")["messages"]
    value = _example(identifier, strings[f"toybox.skin_{identifier}_name"],
                     strings[f"toybox.skin_{identifier}_description"], dark,
                     "flat", "none", 6, 2, "system", "2.0", "1.0.0")
    value["palettes"] = {"dark": dark, "light": light}
    value["stage"] = {"layout": layout, "controls": controls, "type": display,
                      "frame": {"set": frame}, "scene": {"id": scene, "density": "calm"},
                      "reactive": {"profile": profile, "accentFrom": "accent"}}
    return value


SKINS = (
    _skin("tide-station",
          _palette("#0c2028", "#15333c", "#102a33", "#a6e5dc", "#88b7bd"),
          _palette("#e8f4f1", "#f7fffc", "#daeee9", "#25615a", "#517c75", True),
          "console", "glass", "porthole", "rounded-display", "sea", "sweep"),
    _skin("lantern-market",
          _palette("#24171b", "#35232a", "#2a1b22", "#ffd09c", "#bd9b98"),
          _palette("#fbefdf", "#fffbef", "#f3e1cd", "#7d3e37", "#936b61", True),
          "standard", "glass", "brass", "rounded-display", "lanterns", "bloom"),
    _skin("orbital-bridge",
          _palette("#12202d", "#1b3143", "#162738", "#b9dcff", "#89a6c3"),
          _palette("#eef4fa", "#ffffff", "#e3edf6", "#8b3d20", "#667e94", True),
          "dock", "glass", "bezel", "rounded-display", "stars", "glow"),
    _skin("glasshouse",
          _palette("#10231d", "#18362c", "#132c24", "#b9e4b6", "#89b69b"),
          _palette("#edf4e8", "#fbfff6", "#dfead7", "#396344", "#617d60", True),
          "spread", "ink", "bezel", "serif-display", "glasshouse", "bloom"),
)

__all__ = ["SKINS", "artwork"]
