from __future__ import annotations

import re
from typing import Any


def wants_hive_create_auto_start(text: str) -> bool:
    compact = " ".join(str(text or "").split()).strip().lower()
    if not compact:
        return False
    return any(
        phrase in compact
        for phrase in (
            "start working on it",
            "start working on this",
            "start on it",
            "start on this",
            "start researching",
            "start research",
            "work on it",
            "work on this",
            "research it",
            "research this",
            "go ahead and start",
            "create it and start",
            "post it and start",
            "start there",
        )
    )


def hive_create_requested(text: str) -> bool:
    """PURE FORM of the Hive-create ownership decision -- no agent seam.

    The same rule `looks_like_hive_topic_create_request` enforces, callable from seams that
    hold no agent (the execution planner): a Hive create is intercepted only when the request
    names the Hive product as the action's target. Verb + "task" anywhere in a message
    authorizes nothing (product decision of 2026-09-17), and a turn that pastes content it
    asks ABOUT commands nothing its paste says -- a quoted "create a hive task" inside a
    review ask is data.
    """
    from core.inline_payload import turn_supplies_its_own_content

    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    if turn_supplies_its_own_content(lowered):
        return False
    if looks_like_hive_topic_drafting_request(None, lowered):
        return False
    return bool(
        _EXPLICIT_HIVE_PUBLISH_MARKERS_RE.search(lowered)
        or _HIVE_QUALIFIED_CREATE_RE.search(lowered)
    ) and not any(marker in lowered for marker in _CREATE_EXCLUSION_MARKERS)


# An explicit publish phrase naming the Hive product as the destination.
_EXPLICIT_HIVE_PUBLISH_MARKERS_RE = re.compile(
    r"\b(?:add|post|send|push|put)\s+(?:this|it|that|them)?\s*(?:to|on)\s+(?:the\s+)?"
    r"(?:hive(?:\s+mind)?|brain hive|public hive)\b",
    re.IGNORECASE,
)
# A create command with the PRODUCT NAMED as what is created or where: "create a hive task",
# "start a hive mind topic", "open a topic in the hive", "create these tasks on Hive".
_HIVE_QUALIFIED_CREATE_RE = re.compile(
    r"\b(?:create|make|start|open|add)\s+"
    r"(?:(?:a|an|the|new|this|these|those)\s+){0,2}"
    r"(?:hive(?:\s+mind)?|brain hive|public hive)\s+"
    r"(?:task|topic|thread)s?\b"
    r"|\b(?:create|make|start|open|add)\s+"
    r"(?:(?:a|an|the|new|this|these|those)\s+){0,2}"
    r"(?:task|topic|thread)s?\s+"
    r"(?:in|on|to|for|at)\s+(?:the\s+)?(?:hive(?:\s+mind)?|brain hive|public hive)\b",
    re.IGNORECASE,
)
_CREATE_EXCLUSION_MARKERS = (
    "claim task",
    "pull hive tasks",
    "open hive tasks",
    "open tasks",
    "show me",
    "what do we have",
    "any tasks",
    "list tasks",
    "ignore hive",
    "research complete",
    "status",
)


def looks_like_hive_topic_create_request(agent: Any, lowered: str) -> bool:
    """Whether this text asks for the HIVE PRODUCT ACTION of creating a topic.

    OWNERSHIP RULE (the product decision of 2026-09-17): a Hive operation is intercepted only
    when the request names the Hive product as the action's target -- an explicit publish
    phrase ("add this to the hive") or a create command qualified by the product ("create a
    hive task", "open a topic in the hive mind"). A verb plus "task(s)" ANYWHERE in the message
    is not a product operation: "Add new tasks" inside a task-board spec used to seize the
    whole turn and answer with Hive boilerplate before any model ran. Such requests reach the
    selected model, which can author the artifact or ask what the ambiguous wording means.
    """
    return hive_create_requested(lowered)


def looks_like_hive_topic_drafting_request(_: Any, lowered: str) -> bool:
    text = " ".join(str(lowered or "").split()).strip().lower()
    if not text:
        return False
    strong_drafting_markers = (
        "give me the perfect script",
        "create extensive script first",
        "write the script first",
        "draft it first",
        "before i push",
        "before i post",
        "before i send",
        "then i decide if i want to push",
        "then i check and decide",
        "if i want to push that to the hive",
        "if i want to send that to the hive",
        "improve the task first",
        "improve this task first",
    )
    if any(marker in text for marker in strong_drafting_markers):
        return True
    if any(token in text for token in ("script", "prompt", "outline", "template")):
        explicit_send_markers = (
            "create hive mind task",
            "create hive task",
            "create new hive task",
            "create task in hive",
            "add this to the hive",
            "post this to the hive",
            "send this to the hive",
            "push this to the hive",
            "put this on the hive",
        )
        if not any(marker in text for marker in explicit_send_markers):
            if any(
                marker in text
                for marker in (
                    "give me",
                    "write me",
                    "draft",
                    "improve",
                    "polish",
                    "rewrite",
                    "fix typos",
                    "help me",
                )
            ):
                return True
    return False
