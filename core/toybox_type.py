"""Display roles use local stacks and one embedded OFL pixel face."""
from __future__ import annotations

STACKS = {
    "system": "system-ui,sans-serif",
    "rounded-display": "ui-rounded,system-ui,sans-serif",
    "serif-display": "ui-serif,Georgia,serif",
    "mono-readout": "ui-monospace,Menlo,monospace",
    "pixel-display": "VoolPixel,ui-monospace,Menlo,monospace",
}


def css(kind):
    from core.toybox_fragment import HEADINGS, LABELS, _rule

    stack = STACKS.get(kind, STACKS["system"])
    return _rule(HEADINGS + LABELS, "font-family:" + stack + ";letter-spacing:.04em")
