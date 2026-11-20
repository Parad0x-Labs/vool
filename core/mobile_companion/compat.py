"""Which phone apps this computer works with, and which computers a phone app works with.

Each side carries one small number for the language it speaks. An app or computer from before this
check sends nothing and counts as 1. The numbers only decide which plain "update" advice to show
and which old apps to stop serving; they never grant or widen any right.
"""

from __future__ import annotations

#: What this computer speaks to phone apps.
COMPANION_API = 2
#: The oldest phone app this computer still serves. Raise it only when an old app would go wrong.
MIN_APP_API = 1
#: The newest phone app known when this computer was built; an older one is told an update exists.
NEWEST_APP_API = 2


def describe() -> dict[str, int]:
    return {"companion_api": COMPANION_API, "min_app_api": MIN_APP_API, "newest_app_api": NEWEST_APP_API}


def app_api(value: object) -> int:
    """The level a phone says it speaks; anything missing or odd counts as an app from before the check."""
    if type(value) is int and 1 <= value <= 10_000:
        return value
    return 1


def app_too_old(level: int) -> bool:
    return level < MIN_APP_API


def qr_params() -> str:
    """Added to the pairing code. Older apps ignore extra fields, newer ones check them before pairing."""
    return f"&api={COMPANION_API}&minapp={MIN_APP_API}"
