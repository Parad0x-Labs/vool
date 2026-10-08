"""Telling a turn's answer requests from its auxiliary model calls on a served rig. Contributor: sls_0x.

Since the memory recall line landed, a chat turn in a chat that already holds stored turns makes one small
auxiliary call before the answer: the search-expansion call that writes recall search phrases
(``core.agent_runtime.turn_planner_hook.SEARCH_EXPANSION_SYSTEM_PROMPT``). It is not an answer attempt, so a check
that counts answer attempts ("the queued turn ran exactly once", "draft plus one repair") counts without it, and
checks the auxiliary calls on their own.
"""
from __future__ import annotations

from typing import Any

from core.agent_runtime.turn_planner_hook import SEARCH_EXPANSION_SYSTEM_PROMPT


def is_search_expansion(body: dict[str, Any]) -> bool:
    """Whether one captured provider request is the search-expansion call (its system prompt is exactly that one)."""
    return any(
        message.get("role") == "system" and str(message.get("content") or "") == SEARCH_EXPANSION_SYSTEM_PROMPT
        for message in (body.get("messages") or [])
    )
