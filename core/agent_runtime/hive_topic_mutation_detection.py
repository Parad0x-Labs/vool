from __future__ import annotations

import re
from typing import Any

from core.agent_runtime.intent_claims import ActionPolicy, action_policy_from_context
from core.inline_payload import turn_supplies_its_own_content

_CONVERSATIONAL_TOPIC_SHIFT_RE = re.compile(
    r"\b(?:let'?s\s+)?(?:change|switch)\s+(?:the\s+)?topic\b"
    r"(?:\s+(?:completely|entirely|now|please))?\s*(?:[:;,.!?]|$)",
    re.IGNORECASE,
)


def maybe_handle_hive_topic_mutation_request(
    agent: Any,
    user_input: str,
    *,
    task: Any,
    session_id: str,
    source_context: dict[str, object] | None,
) -> dict[str, Any] | None:
    # An explicit no-action instruction is a turn-wide execution boundary. Keep it
    # here as well as at ingress because this handler is a direct fast-path seam.
    if action_policy_from_context(source_context) is ActionPolicy.FORBIDDEN:
        return None
    clean = " ".join(str(user_input or "").split()).strip()
    lowered = clean.lower()
    if agent._looks_like_hive_topic_update_request(lowered):
        if not _names_hive_product(lowered) and not _bound_topic_resolves(agent, session_id, clean):
            # The match came only from unqualified task wording ("update the task") or a
            # continuation phrase with nothing behind it: not a product operation, and no
            # current binding carries it. The selected model answers instead.
            return None
        return agent._handle_hive_topic_update_request(
            clean,
            task=task,
            session_id=session_id,
            source_context=source_context,
        )
    if agent._looks_like_hive_topic_delete_request(lowered):
        if not _names_hive_product(lowered) and not _bound_topic_resolves(agent, session_id, clean):
            return None
        return agent._handle_hive_topic_delete_request(
            clean,
            task=task,
            session_id=session_id,
            source_context=source_context,
        )
    return None


#: The product named as the operation's target. Words like "task" alone authorize nothing
#: (product decision of 2026-09-17): "delete the done tasks" inside a to-do app's spec must
#: never route to Hive, and an update about "the task" in the user's own project must never
#: mutate a Hive topic.
_HIVE_PRODUCT_MARKERS = (
    "hive topic",
    "hive task",
    "hive mind",
    "brain hive",
    "public hive",
    "the hive",
    "to hive",
    "in hive",
    "on hive",
)


def _names_hive_product(lowered: str) -> bool:
    compact = str(lowered or "").strip().lower()
    return any(marker in compact for marker in _HIVE_PRODUCT_MARKERS)


def _bound_topic_resolves(agent: Any, session_id: str, clean: str) -> bool:
    """Whether a session Hive topic actually exists behind a continuation phrase.

    "Update the one you created" is a product operation only as a CURRENT BOUND CONTINUATION:
    this session must still hold the topic it created. With nothing to resolve, the phrase is
    ordinary prose and the model answers it.
    """
    try:
        return (
            agent._resolve_hive_topic_for_mutation(
                session_id=session_id,
                topic_hint=agent._extract_hive_topic_hint(clean),
            )
            is not None
        )
    except Exception:
        return False


def looks_like_hive_topic_update_request(agent: Any, lowered: str) -> bool:
    compact = " ".join(str(lowered or "").split()).strip().lower()
    if not compact or turn_supplies_its_own_content(compact) or agent._looks_like_hive_topic_create_request(compact):
        return False
    if _CONVERSATIONAL_TOPIC_SHIFT_RE.search(compact):
        return False
    if "update my twitter handle" in compact:
        return False
    if not any(marker in compact for marker in ("update", "edit", "change")):
        return False
    # The Hive product named as the target, or an explicit continuation of a create ("the one
    # you created") -- the caller binds the continuation to a topic that actually exists. Bare
    # task wording is NOT a Hive operation.
    return (
        any(marker in compact for marker in ("hive topic", "hive task", "hive mind", "brain hive"))
        or "the one you created" in compact
        or "the one you just created" in compact
    )


def looks_like_hive_topic_delete_request(agent: Any, lowered: str) -> bool:
    compact = " ".join(str(lowered or "").split()).strip().lower()
    if not compact or turn_supplies_its_own_content(compact) or agent._looks_like_hive_topic_create_request(compact):
        return False
    if not any(marker in compact for marker in ("delete", "remove", "cancel", "close")):
        return False
    return (
        any(marker in compact for marker in ("hive topic", "hive task", "hive mind", "brain hive"))
        or "the one you created" in compact
        or "the one you just created" in compact
    )


def extract_hive_topic_update_draft(agent: Any, text: str) -> dict[str, Any] | None:
    structured = agent._extract_hive_topic_create_draft(text)
    if structured is not None:
        return structured
    raw = agent._strip_context_subject_suffix(text)
    tail = re.sub(
        r"^.*?\b(?:update|edit|change)\b\s+(?:the\s+|my\s+)?(?:(?:current|last|latest|existing)\s+)?"
        r"(?:(?:hive|hive mind|brain hive)\s+)?(?:task|topic|thread|one\s+you\s+created(?:\s+already)?)\b"
        r"(?:\s+(?:#?[a-z0-9-]{6,64}))?"
        r"(?:\s+(?:with|to))?(?:\s+the)?(?:\s+following)?\s*[:\-]?\s*",
        "",
        raw,
        flags=re.IGNORECASE | re.DOTALL,
    ).strip()
    tail = agent._strip_wrapping_quotes(" ".join(tail.split()).strip())
    if not tail or tail == "already":
        return None
    return {
        "title": "",
        "summary": tail[:4000],
        "topic_tags": [],
        "auto_start_research": False,
    }
