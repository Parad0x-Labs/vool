"""The memory kernel binds only evidence that is still the chat's: a forgotten value never binds a claim.

A packet is compiled while the store is open; the binders run later in the turn. A forget that lands in between
(the forget law's revocation ledger, ``core.vool_memory.record_revocation``) must not let the packet's copy of the
forgotten value support an answer. Every binder input is filtered against the chat's active revocations at bind
time: evidence lines and packet facts that carry a revoked token are dropped.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def without_revoked(
    evidence_text: Any,
    packet_facts: Sequence[Mapping[str, Any]] | None,
    chat_id: str,
) -> tuple[str, list[Mapping[str, Any]]]:
    """``(evidence_text, packet_facts)`` with every line and fact carrying a revoked token removed."""

    facts = list(packet_facts or [])
    text = str(evidence_text or "")
    try:
        from core.context_retrieval import revoked_tokens_for_chat, text_carries_revoked_token

        revoked = revoked_tokens_for_chat(str(chat_id or ""))
    except Exception:
        return text, facts
    if not revoked:
        return text, facts
    kept_lines = [line for line in text.splitlines() if not text_carries_revoked_token(line, revoked)]
    kept_facts = [
        fact for fact in facts
        if not text_carries_revoked_token(" ".join(str(value) for value in dict(fact).values()), revoked)
    ]
    return "\n".join(kept_lines), kept_facts


__all__ = ["without_revoked"]
