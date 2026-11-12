"""Built-in stage modules. A broken entry cannot remove the other choices."""
from __future__ import annotations

import importlib
import logging

BUILTIN_V2_MODULES: tuple[str, ...] = (
    "core.toybox_flagships_a",
    "core.toybox_flagships_b",
    "core.toybox_pixel",
    "core.toybox_inktide",
    "core.toybox_hanabi",
)


def manifests():
    import json

    from core.toybox import validate

    result = []
    for name in BUILTIN_V2_MODULES:
        try:
            candidates = importlib.import_module(name).SKINS
        except (ImportError, AttributeError, ValueError):
            logging.getLogger(__name__).warning("Skin collection unavailable: %s", name)
            continue
        for candidate in candidates:
            try:
                result.append(validate(json.dumps(candidate)))
            except ValueError as exc:
                logging.getLogger(__name__).warning("Skin omitted: %s", exc)
    return result
