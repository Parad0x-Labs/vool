"""Should this turn take the clean local path? One pure decision, in a fixed order.

1. The flag `VOOL_CLEAN_LOCAL_LOOP` is on (default OFF).
2. The turn is not RED: no pay/send/delete/post/cancel-style verb and no money words. VOOL has no
   text-level risky-action classifier to reuse (its consent gates sit at the effect, which this loop
   never reaches), so this is a small verb floor. RED turns keep the normal ceremony.
3. Nothing else owns the turn: not a planned sub-turn, a resumed checkpoint, a browser workflow, a
   project-bound chat or a chat with an active goal workflow, and it is the owner's own local turn.
4. It is about the owner's own files or computer (or a short follow-up to a clean-loop turn), and
   not a live/online question, which the online lanes own.
5. The brain that would answer is a local model (resolved last: it is the only costly check).
"""
from __future__ import annotations

import os
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

FLAG_ENV = "VOOL_CLEAN_LOCAL_LOOP"
_TRUTHY = {"1", "true", "yes", "on"}

_RED_RE = re.compile(
    r"\b(?:pay|paying|send|sending|transfer|wire|buy|purchase|sell|trade|swap|donate|tip\s+(?:him|her|them|the)|"
    r"order\s+(?:me|a|an|some)|book\s+(?:me|a|an)|reserve|subscribe|unsubscribe|cancel|"
    r"delete|deleting|remove|removing|erase|wipe|trash|shred|purge|clear\s+out|clean\s+out|clean\s+up|"
    r"empty\s+(?:the\s+)?(?:trash|bin)|get\s+rid|uninstall|install|kill|shut\s*down|reboot|restart\s+(?:it|my|the)|"
    r"format\s+(?:the|my|this|that)\s+(?:drive|disk|usb|computer|laptop|mac|pc)|"
    r"post\s+(?:it|this|that|to|on|a|an|my)|publish|tweet|share|upload|email\s+(?:it|this|that|him|her|them|to)|"
    r"message\s+(?:him|her|them)|sign\s+(?:it|this|that|the|up|in|me)|submit|approve|authori[sz]e)\b"
    r"|[$€£]\s*\d|\b(?:wallet|crypto|bitcoin|btc|eth|solana|usdc|credit\s+card|bank\s+account)\b",
    re.I,
)
_ONLINE_RE = re.compile(
    r"\b(?:weather|forecast|news|headlines|stock|share\s+price|exchange\s+rate|price\s+of|search\s+(?:the\s+)?(?:web|internet|online)|"
    r"google|online|website|https?://|www\.)\b",
    re.I,
)
_TOPIC_RE = re.compile(
    r"\b(?:files?|folders?|pdfs?|csv|docs?|documents?|downloads?|desktop|photos?|pictures?|pics?|images?|screenshots?|"
    r"videos?|movies?|music|songs?|notes?|to\s*do|list|invoices?|receipts?|bills?|owe|cv|resume|tax|bank|statement|boarding\s+pass|"
    r"tickets?|recipes?|disk|drive|storage|space|ram|memory|cpu|processor|specs?|laptop|computer|mac|pc|"
    r"screen|monitors?|resolution|process(?:es)?|running|background|services?|logs?|errors?|slow|sluggish|junk|temp)\b",
    re.I,
)
_OWNED_RE = re.compile(r"\b(?:my|mine|i\s+(?:wrote|saved|downloaded|took|put|keep|named|made|have|had|got))\b", re.I)
_SELF_RE = re.compile(r"\b(?:my\s+name|call\s+me|who\s+am\s+i|about\s+me)\b|^\s*(?:please\s+)?(?:remember|forget)\b", re.I)
_APPROVAL_RE = re.compile(r"^\s*(?:yes|yep|yeah|ok|okay|sure|go\s+ahead|do\s+it|approve[d]?|confirm(?:ed)?|no|nope|stop)\b", re.I)

_FOLLOW_UP_WORDS = 14
_FOLLOW_UP_TTL_S = 900.0
_RECENT: OrderedDict[str, float] = OrderedDict()
_RECENT_LOCK = threading.Lock()


@dataclass(frozen=True)
class RouteDecision:
    admit: bool
    reason: str
    brain: Any = None


def flag_on(env: Mapping[str, str] | None = None) -> bool:
    return str((env if env is not None else os.environ).get(FLAG_ENV) or "").strip().lower() in _TRUTHY


def is_red(text: str) -> bool:
    return bool(_RED_RE.search(str(text or "")))


def is_online_ask(text: str) -> bool:
    return bool(_ONLINE_RE.search(str(text or "")))


def asks_about_own_files_or_computer(text: str) -> bool:
    value = str(text or "")
    if _SELF_RE.search(value):
        return False
    return bool(_TOPIC_RE.search(value) or _OWNED_RE.search(value))


def note_clean_turn(session_id: str) -> None:
    """Remember that this session's last turn ran here, so a short follow-up stays on the same path."""
    if not session_id:
        return
    with _RECENT_LOCK:
        _RECENT[session_id] = time.monotonic()
        _RECENT.move_to_end(session_id)
        while len(_RECENT) > 256:
            _RECENT.popitem(last=False)


def _is_follow_up(text: str, session_id: str) -> bool:
    value = str(text or "")
    if not session_id or len(value.split()) > _FOLLOW_UP_WORDS or _APPROVAL_RE.search(value):
        return False
    with _RECENT_LOCK:
        seen = _RECENT.get(session_id)
    return seen is not None and time.monotonic() - seen < _FOLLOW_UP_TTL_S


def _owner_context_blocks(source_context: Mapping[str, Any], session_id: str) -> str:
    if source_context.get("planned_subturn"):
        return "planned_subturn"
    if source_context.get("turn_routing_retry") or source_context.get("runtime_checkpoint_resumed"):
        return "resumed_turn"
    if str(source_context.get("_trusted_project_id") or "").strip():
        return "project_owns_turn"
    try:
        from core.goal_workflow import load_active_goal_for_session

        if session_id and load_active_goal_for_session(session_id):
            return "workflow_owns_turn"
    except Exception:
        pass
    try:
        from core.request_trust import request_is_owner_local

        if not request_is_owner_local(dict(source_context)):
            return "not_owner_local"
    except Exception:
        return "not_owner_local"
    return ""


def decide(
    text: str,
    *,
    source_context: Mapping[str, Any] | None,
    session_id: str = "",
    resolve_brain: Callable[[], Any] | None = None,
    browser_turn: bool = False,
    env: Mapping[str, str] | None = None,
) -> RouteDecision:
    if not flag_on(env):
        return RouteDecision(False, "flag_off")
    if is_red(text):
        return RouteDecision(False, "red_turn")
    if browser_turn:
        return RouteDecision(False, "browser_turn")
    blocked = _owner_context_blocks(dict(source_context or {}), session_id)
    if blocked:
        return RouteDecision(False, blocked)
    if is_online_ask(text):
        return RouteDecision(False, "online_ask")
    if not (asks_about_own_files_or_computer(text) or _is_follow_up(text, session_id)):
        return RouteDecision(False, "not_about_own_files")
    brain = resolve_brain() if resolve_brain is not None else None
    if brain is None:
        return RouteDecision(False, "brain_not_local")
    return RouteDecision(True, "admitted", brain)


__all__ = [
    "FLAG_ENV",
    "RouteDecision",
    "asks_about_own_files_or_computer",
    "decide",
    "flag_on",
    "is_online_ask",
    "is_red",
    "note_clean_turn",
]
