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

#: R9 recheck (X13): "show me whats trending but absolutely do not post anything" names no X token, yet
#: the post word places it on X. A bare trending ask counts as an X read when a social word appears
#: anywhere in the turn, negated or not: "do not post" still says the turn is about X.
_BARE_TREND = re.compile(
    r"\b(?:what(?:'|’)?s|what\s+is|whats|show\s+me(?:\s+what(?:'|’)?s|\s+whats)?|anything)\s+"
    r"(?:\w+\s+){0,2}?(?:trending|blowing\s+up|viral)\b",
    re.I,
)
_SOCIAL_CUE = re.compile(r"\b(?:post(?:s|ing)?|tweet(?:s|ing)?|hashtags?|timeline|retweet)\b", re.I)

#: "do not post", "don't tweet anything", "without posting": a negated publish word is no ask to post.
_NEGATED_PUBLISH = re.compile(
    r"\b(?:do\s+not|don(?:'|’)?t|never|not|without|no)\s+(?:\w+\s+){0,2}?"
    r"(?:post(?:ing)?|tweet(?:ing)?|publish(?:ing)?|shar(?:e|ing)|send(?:ing)?|put(?:ting)?)\b"
    r"(?:\s+(?:anything|it|this|that))?",
    re.I,
)
#: R9 recheck (X05, X06): asks to PUBLISH on X ("post a tweet for me saying ...", "put this on X for me: ...").
_PUBLISH = re.compile(
    r"\b(?:post|tweet|publish|share|put|send)\b[^.?!:]{0,30}?\b(?:on|to)\s+" + _X + r"(?![\w.'’-])"
    r"|\bpost\s+(?:a|an|this|that|it|my|the)?\s*(?:tweet|thread)\b"
    r"|\btweet\s+(?:this|that|it|out|for\s+me)\b"
    r"|\b(?:can|could|would|will|pls|please)\s+(?:you\s+)?tweet\b",
    re.I,
)
#: Where the owner's own words for the post start.
_SAYING = re.compile(r"\b(?:saying|that\s+says|which\s+says|with\s+the\s+text|reading)\s+(.+)$", re.I | re.S)
_QUOTED = re.compile(r"[\"“”]([^\"“”]{2,})[\"“”]")
_COLON = re.compile(r":\s*(.+)$", re.S)

X_POST_DRAFT_KEY = "x.not_connected.post_draft"
X_POST_DRAFT_EN = (
    "X is not connected, so nothing was posted. To connect it, open {settings} > {keys} > {x_row}, choose "
    "{connect} and follow the steps. Here is your post as a draft you can copy and post yourself:"
)
X_POST_ASK_KEY = "x.not_connected.post_ask"
X_POST_ASK_EN = (
    "X is not connected, so nothing was posted. To connect it, open {settings} > {keys} > {x_row}, choose "
    "{connect} and follow the steps. Tell me what the post should say and I will write you a draft to copy."
)


def _place_fields(locale: str) -> tuple[str, dict[str, str]]:
    from core.i18n.catalog import catalog_for
    from core.i18n.turn_locale import turn_locale

    resolved = str(locale or "").strip() or turn_locale()
    catalog = catalog_for(resolved)
    fields = {}
    for name, key, english in _PLACE_KEYS:
        text = catalog.text(key)
        fields[name] = english if text == key else text
    return resolved, fields


def not_connected_text(locale: str = "") -> str:
    """The plain reply, in ``locale`` or the turn's app language (English when none is bound)."""
    from core.i18n.turn_locale import turn_text

    resolved, fields = _place_fields(locale)
    return turn_text(X_NOT_CONNECTED_KEY, X_NOT_CONNECTED_EN, resolved, **fields)


def _without_negated_publish(text: str) -> str:
    return _NEGATED_PUBLISH.sub(" ", str(text or ""))


def asks_to_post_on_x(text: str) -> bool:
    """Whether the turn asks VOOL to publish on X now; drafting asks and "do not post" are not."""
    value = _without_negated_publish(text)
    if not value.strip() or _COMPOSING.search(value):
        return False
    return bool(_PUBLISH.search(value))


def post_draft(text: str) -> str:
    """The post the owner gave: their quoted or after-colon words, or a short draft of "saying ..."."""
    value = str(text or "").strip()
    quoted = _QUOTED.search(value)
    if quoted:
        return quoted.group(1).strip()
    saying = _SAYING.search(value)
    if saying:
        words = saying.group(1).strip().strip("\"“”").rstrip(" .")
        if not words:
            return ""
        draft = words[0].upper() + words[1:]
        return draft if draft[-1] in "!?…" else draft + "!"
    colon = _COLON.search(value)
    if colon:
        return colon.group(1).strip().strip("\"“”")
    return ""


def post_not_connected_text(text: str, locale: str = "") -> str:
    """X is not connected so nothing was posted, where to connect it, and the draft to copy."""
    from core.i18n.turn_locale import turn_text

    resolved, fields = _place_fields(locale)
    draft = post_draft(text)
    if draft:
        # The owner's words go after the line verbatim, outside the placeholder law (which escapes).
        return turn_text(X_POST_DRAFT_KEY, X_POST_DRAFT_EN, resolved, **fields) + "\n\n" + draft
    return turn_text(X_POST_ASK_KEY, X_POST_ASK_EN, resolved, **fields)


def x_connected() -> bool:
    try:
        from core.x_api import connected

        return bool(connected())
    except Exception:
        return False


def asks_for_x_read(text: str, *, web_available: bool = False) -> bool:
    """Whether the turn asks VOOL to read X (trends, what people are saying, usage), not to write.

    Trends, buzz and the owner's X usage can only come from X. Any other ask that only names X (an
    account or a person "on X") is claimed only while the web cannot answer it: with Web lookups on,
    the web finds the person or the profile, so it stays the ordinary lookup it was before X existed."""
    value = str(text or "")
    readable = _without_negated_publish(value)
    if not value.strip() or _COMPOSING.search(readable):
        return False
    if _X_READ.search(readable):
        return True
    if _BARE_TREND.search(readable) and _SOCIAL_CUE.search(value):
        return True
    if web_available:
        return False
    try:
        from core.tool_demand_signals import resolve_demand_signals

        signals = resolve_demand_signals(value)
    except Exception:
        return False
    explicit = set(signals.explicit_intents)
    return "x" in signals.required_families and not (explicit and explicit <= {"x.action.propose"})


def _social_content_turn(text: str) -> bool:
    """Campaign or content-calendar work: the social content manager owns the turn.

    That skill answers a publish ask with a typed unavailable outcome and copy-ready text, and an X
    trend ask inside a plan as a separately sourced claim, so the plan, the other platforms and the
    owner's exact lines are kept. Answering such a turn with the X line alone dropped all of it.
    """
    try:
        from core.tool_demand_signals import resolve_demand_signals

        return "social_content" in set(resolve_demand_signals(str(text or "")).required_families)
    except Exception:
        return False


def _web_available(source_context: dict[str, Any] | None) -> bool:
    try:
        from core.remote_fetch_policy import effective_web_available

        return bool(effective_web_available(source_context))
    except Exception:
        return False


def reply_if_not_connected(agent: Any, text: str, *, session_id: str, source_context: dict[str, Any] | None):
    """A finished chat result for an X read asked while X is not connected; None otherwise."""
    if _social_content_turn(text):
        return None
    reading = asks_for_x_read(text, web_available=_web_available(source_context))
    posting = not reading and asks_to_post_on_x(text)
    if not (reading or posting) or x_connected():
        return None
    locale = str((source_context or {}).get("ui_locale") or "")
    response = not_connected_text(locale) if reading else post_not_connected_text(text, locale)
    result = agent._fast_path_result(session_id=session_id, user_input=text, response=response,
                                     confidence=1.0, source_context=source_context, reason="x_not_connected")
    result.update(deterministic=True, success=False, status="not_configured", task_outcome="failed")
    return result


__all__ = ["X_NOT_CONNECTED_EN", "X_NOT_CONNECTED_KEY", "X_POST_ASK_EN", "X_POST_ASK_KEY", "X_POST_DRAFT_EN",
           "X_POST_DRAFT_KEY", "asks_for_x_read", "asks_to_post_on_x", "not_connected_text", "post_draft",
           "post_not_connected_text", "reply_if_not_connected", "x_connected"]
