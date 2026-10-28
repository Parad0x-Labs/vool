"""What VOOL says when a chat asks for X while X is not connected.

GLM audit R9 (X01, X02, X03): "whats trending on X right now?", "anything blowing up on twitter
tonight?" and "how much of my X api usage have i burned" ended on a grounding or records refusal
and never said what to connect or where. With X not connected no X tool is offered, so the turn
fell through to the research lane; and an X tool named anyway answered with its contract reason,
which for trending reads "set x.trending_enabled and configure a bearer token".

Both paths now give one plain reply in the app language: X isn't connected yet, the real Settings
place to connect it (the same catalog words the Settings page shows: Settings > API Keys > X
(Twitter) > Connect X), and that drafting posts still works. Nothing is sent to X.
"""
from __future__ import annotations

import re
from typing import Any

X_NOT_CONNECTED_KEY = "x.not_connected.reply"
X_NOT_CONNECTED_EN = (
    "X is not connected yet. To connect it, open {settings} > {keys} > {x_row}, choose {connect} and "
    "follow the steps. Until then, I can still help you draft posts."
)
#: The Settings words the reply names, read from the catalog the Settings page itself uses.
_PLACE_KEYS = (
    ("settings", "sidebar.settings", "Settings"),
    ("keys", "settings.group.keys.title", "API Keys"),
    ("x_row", "xsetup.title", "X (Twitter)"),
    ("connect", "xsetup.open", "Connect X"),
)

_X = r"(?:x|twitter)"
_ON_X = r"\b(?:on|in|across|from)\s+" + _X + r"(?![\w.'’-])"
#: Asks to READ X: what is trending or being talked about on X, or the owner's X usage.
_X_READ = re.compile(
    r"\b(?:trend(?:s|ing)?|blowing\s+up|viral|buzz(?:ing)?|hot|popular|talking\s+about|saying|"
    r"going\s+on|happening|news|latest)\b[^.?!]{0,40}?" + _ON_X
    + r"|" + _ON_X + r"[^.?!]{0,30}?\b(?:trend(?:s|ing)?|blowing\s+up|viral|right\s+now|today|tonight)\b"
    r"|\b(?:my\s+)?" + _X + r"\s+(?:api\s+)?(?:usage|quota|credits?|limits?|cap|spend|bill)\b"
    r"|\b" + _X + r"\s+trends\b|\btwitter\b[^.?!]{0,40}?\b(?:trend(?:s|ing)?|blowing\s+up|viral)\b",
    re.I,
)
#: A writing ask is served without X (the drafting the reply promises), so it is never claimed.
_COMPOSING = re.compile(r"\b(?:draft|write|compose|rewrite|edit|brainstorm|ideas?\s+for)\b", re.I)


def not_connected_text(locale: str = "") -> str:
    """The plain reply, in ``locale`` or the turn's app language (English when none is bound)."""
    from core.i18n.catalog import catalog_for
    from core.i18n.turn_locale import turn_locale, turn_text

    resolved = str(locale or "").strip() or turn_locale()
    catalog = catalog_for(resolved)
    fields = {}
    for name, key, english in _PLACE_KEYS:
        text = catalog.text(key)
        fields[name] = english if text == key else text
    return turn_text(X_NOT_CONNECTED_KEY, X_NOT_CONNECTED_EN, resolved, **fields)


def x_connected() -> bool:
    try:
        from core.x_api import connected

        return bool(connected())
    except Exception:
        return False


def asks_for_x_read(text: str) -> bool:
    """Whether the turn asks VOOL to read X (trends, what people are saying, usage), not to write."""
    value = str(text or "")
    if not value.strip() or _COMPOSING.search(value):
        return False
    if _X_READ.search(value):
        return True
    try:
        from core.tool_demand_signals import resolve_demand_signals

        signals = resolve_demand_signals(value)
    except Exception:
        return False
    explicit = set(signals.explicit_intents)
    return "x" in signals.required_families and not (explicit and explicit <= {"x.action.propose"})


def reply_if_not_connected(agent: Any, text: str, *, session_id: str, source_context: dict[str, Any] | None):
    """A finished chat result for an X read asked while X is not connected; None otherwise."""
    if not asks_for_x_read(text) or x_connected():
        return None
    locale = str((source_context or {}).get("ui_locale") or "")
    response = not_connected_text(locale)
    result = agent._fast_path_result(session_id=session_id, user_input=text, response=response,
                                     confidence=1.0, source_context=source_context, reason="x_not_connected")
    result.update(deterministic=True, success=False, status="not_configured", task_outcome="failed")
    return result


__all__ = ["X_NOT_CONNECTED_EN", "X_NOT_CONNECTED_KEY", "asks_for_x_read", "not_connected_text",
           "reply_if_not_connected", "x_connected"]
