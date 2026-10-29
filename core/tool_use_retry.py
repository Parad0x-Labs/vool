"""One retry for a needs-a-tool turn whose model reply called nothing.

The 2026-10-29 re-audit (qwen3:8b) logged replies such as "I'll nudge you in 20 minutes" and
"Next Tuesday afternoon is completely free" on turns that needed the owner's reminders and
calendar: the model answered in prose and nothing ran. When the tool loop sees that on its first
decision, it asks once more with a short directive and an offer narrowed to the tools the words
need (the same recognizers that released the lane and seated the offer). One retry per turn,
inside the loop's round budget; approval gates for writes and sends are untouched, because the
retry only changes what is OFFERED, never what may execute.
"""
from __future__ import annotations

from typing import Any

#: loop source-context key carrying {"intents": [...], "directive": str} for exactly one round.
TOOL_USE_RETRY_KEY = "tool_use_retry"


def needed_tool_intents(text: str, source_context: dict[str, Any] | None) -> tuple[str, ...]:
    """The tool intents the owner's words need from this machine, or () for any other turn."""
    if not isinstance(source_context, dict) or not source_context:
        return ()
    try:
        from core.request_trust import request_is_owner_local

        if not request_is_owner_local(source_context):
            return ()
        from core.tool_demand_signals import owner_live_source_intents, owner_local_operator_intents

        intents = [*owner_local_operator_intents(text), *owner_live_source_intents(text)]
    except Exception:
        return ()
    return tuple(i for i in dict.fromkeys(intents) if not i.startswith("email."))


def tool_use_retry_directive(intents: tuple[str, ...] | list[str]) -> str:
    names = ", ".join(str(i) for i in intents)
    return (
        "You answered without calling a tool, but this request needs live data from the user's own "
        "computer, notes, calendar or reminders, which only the tools can read or change. Call the "
        f"right tool now ({names}). Do not answer from memory, and never say something was checked, "
        "scheduled, saved or changed unless a tool did it."
    )


def retry_offer_intents(source_context: dict[str, Any] | None) -> frozenset[str] | None:
    """The intents a retry round's offer is narrowed to (plus respond.direct), or None."""
    if not isinstance(source_context, dict):
        return None
    retry = source_context.get(TOOL_USE_RETRY_KEY)
    if not isinstance(retry, dict):
        return None
    intents = frozenset(str(i) for i in retry.get("intents") or () if str(i).strip())
    return (intents | {"respond.direct"}) if intents else None


def retry_directive(source_context: dict[str, Any] | None) -> str:
    if not isinstance(source_context, dict):
        return ""
    retry = source_context.get(TOOL_USE_RETRY_KEY)
    return str(retry.get("directive") or "") if isinstance(retry, dict) else ""
