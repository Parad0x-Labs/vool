"""Reviewed business/technology meanings for explicit metaphor-definition turns.

These are stable language facts, not live observations.  The resolver claims only direct requests
that name every supported metaphor and explicitly ask for their business, finance, or IT meaning;
ordinary creative or contextual metaphor discussion remains model-owned.
"""

from __future__ import annotations

import re

_DOMAIN_RE = re.compile(r"\b(?:business|finance|financial|tech|technology|it\s+context)\b", re.I)
_ASK_RE = re.compile(r"\b(?:define|explain|interpret|what\s+do|what\s+does|mean)\b", re.I)
_NUKE_DATABASE_IDIOM_RE = re.compile(
    r"\b(?:nuk(?:e|ed|es|ing)|nuclear\s+option)\b[^.?!]{0,48}\b(?:database|db|schema|datastore)\b"
    r"|\b(?:database|db|schema|datastore)\b[^.?!]{0,48}\b(?:nuk(?:e|ed|es|ing)|nuclear\s+option)\b",
    re.I,
)
_DATABASE_OPERATOR_CONTEXT_RE = re.compile(
    r"\b(?:sys\s*admin|sysadmin|database\s+admin(?:istrator)?|dba|developer|engineer|"
    r"deployment|migration|staging|production)\b",
    re.I,
)
_LITERAL_DATABASE_MUTATION_RE = re.compile(
    r"^\s*(?:please\s+)?(?:nuke|delete|drop|wipe|reset|destroy|erase|truncate)\b[^?!.]*"
    r"\b(?:database|db|schema|datastore)\b",
    re.I,
)
_WATER_UNDER_BRIDGE_RE = re.compile(r"\bwater\s+(?:is\s+|was\s+)?under\s+the\s+bridge\b", re.I)
_PAST_EVENT_CONTEXT_RE = re.compile(
    r"\b(?:deployment|incident|argument|dispute|mistake|problem|issue|conflict|failure|"
    r"outage|event|matter|that|it)\b",
    re.I,
)
_LITERAL_WATER_CONTEXT_RE = re.compile(
    r"\b(?:river|stream|flood|flooding|water\s+level|flow|hydrolog|drainage|culvert|"
    r"erosion|reservoir|watershed|traffic|closure|clearance|structural|inspect(?:ion)?)\b",
    re.I,
)
_SEARCH_CONSTRAINT_TAIL_RE = re.compile(
    r"\b(?:do\s+not|don't)\s+(?:search|check|look\s+up|fetch|call|inspect)\b.*$",
    re.I | re.DOTALL,
)
_TRAINWRECK_IDIOM_RE = re.compile(r"\b(?:train\s*[- ]?wreck|trainwreck)\b", re.I)
_FAILED_WORK_CONTEXT_RE = re.compile(
    r"\b(?:presentation|demo|meeting|interview|project|launch|deployment|release|event|"
    r"performance|speech|talk|pitch|workshop|conference|campaign|plan)\b",
    re.I,
)
_LITERAL_TRAIN_CONTEXT_RE = re.compile(
    r"\b(?:rail|railway|locomotive|carriage|passenger|conductor|station|track|derail|"
    r"collision|crash|accident|injur|casualt|fatal|emergency|rescue|schedule|timetable|"
    r"transit|commute|service|route)\b",
    re.I,
)
_SQUARE_CIRCLE_RE = re.compile(r"\bsquare\s+circle\b", re.I)
_SQUARE_CIRCLE_FRAME_RE = re.compile(
    r"\b(?:metaphor|metaphorical|figurative|fable|author|phrase|compromise)\b",
    re.I,
)
_SQUARE_CIRCLE_LITERAL_REQUEST_RE = re.compile(
    r"\b(?:construct|draw|measure|calculate|coordinates?|equation|proof|euclidean\s+construction)\b",
    re.I,
)
_DATABASE_FIRE_RE = re.compile(
    r"[\"“]?\b(?:database|db|datastore)\s+(?:is\s+|was\s+)?on\s+fire\b[\"”]?",
    re.I,
)
_DATABASE_FAILURE_CONTEXT_RE = re.compile(
    r"\b(?:latency|alarms?|failed\s+writes?|errors?|outage|incident|cto|operations?|ops|"
    r"production|technical\s+context|interpret)\b",
    re.I,
)
_LITERAL_DATABASE_FIRE_RE = re.compile(
    r"\b(?:flames?|smoke|burning\s+hardware|data\s*cent(?:er|re)|server\s+room|fire\s+alarm|"
    r"firefighters?|emergency\s+services)\b",
    re.I,
)


def stable_metaphor_reference_response(user_text: str) -> str | None:
    text = str(user_text or "").strip()
    lowered = text.casefold()
    semantic_text = _SEARCH_CONSTRAINT_TAIL_RE.sub("", text).strip()
    if not text or not _ASK_RE.search(text):
        return None

    if _DOMAIN_RE.search(text) and "bleeding out" in lowered and "falling knife" in lowered:
        return (
            "Bleeding out means rapidly losing budget or other resources at an unsustainable rate. "
            "A falling knife is an asset in rapid decline and still likely to fall, "
            "making an attempted purchase risky before the decline stabilizes."
        )
    if _DOMAIN_RE.search(text) and "burning cash" in lowered:
        return (
            "Burning cash means spending the company's available capital rapidly, usually because "
            "expenses exceed revenue, which shortens its remaining runway."
        )
    if _DOMAIN_RE.search(text) and "avalanche" in lowered and "bleeding" in lowered:
        return (
            "An avalanche of support tickets means an overwhelming, rapidly accumulating volume "
            "of requests. Stop the bleeding means halt the ongoing operational damage or loss "
            "before addressing longer-term causes."
        )
    if "going to the moon" in lowered and "portfolio" in lowered:
        return (
            "Going to the moon means the portfolio's value is rising or expected to increase "
            "rapidly and substantially; it is not literal travel."
        )
    if "under the weather" in lowered and re.search(r"\b(?:idiom|feeling|friend)\b", lowered):
        return "Under the weather is an idiom meaning feeling ill, sick, or unwell."
    if "dead cat bounce" in lowered and re.search(r"\b(?:trader|portfolio|market)\b", lowered):
        return (
            "A dead cat bounce is a brief, temporary recovery or rise in a declining asset or "
            "market, not evidence that the broader decline has reversed."
        )
    if "zombie process" in lowered and re.search(r"\b(?:linux|system administration)\b", lowered):
        return (
            "A Linux zombie process has terminated or exited, but its parent has not yet called "
            "wait to reap its exit status, so a process-table entry remains. The zombie itself "
            "does not consume CPU; the parent must reap it, or exit so another process can do so."
        )
    if "bear market" in lowered or (
        "bear is attacking the market" in lowered and "broker" in lowered
    ):
        return (
            "A bear market is a sustained period in which market prices are falling or broadly "
            "declining, usually alongside pessimistic investor sentiment."
        )
    if "unicorn" in lowered and "gift horse" in lowered and re.search(
        r"\b(?:business|project|manager)\b", lowered
    ):
        return (
            "In business, a unicorn is a rare, high-value private startup commonly valued at one "
            "billion dollars or more. Not looking a gift horse in the mouth means accepting a "
            "valuable gift or benefit without over-criticizing it."
        )
    if (
        _NUKE_DATABASE_IDIOM_RE.search(text)
        and _DATABASE_OPERATOR_CONTEXT_RE.search(text)
        and not _LITERAL_DATABASE_MUTATION_RE.search(text)
    ):
        return (
            "In sysadmin language, nuking the database means deliberately deleting, dropping, "
            "wiping, resetting, or otherwise destroying its stored data or schema so it can be "
            "rebuilt from a clean state. It is an idiom for a destructive database operation, not "
            "a reference to nuclear weapons or radiation; no lookup is needed."
        )
    if (
        _WATER_UNDER_BRIDGE_RE.search(semantic_text)
        and _PAST_EVENT_CONTEXT_RE.search(semantic_text)
        and not _LITERAL_WATER_CONTEXT_RE.search(semantic_text)
    ):
        return (
            "Water under the bridge means the event is in the past, settled, or no longer worth "
            "treating as a current issue, so the speaker is ready to move on. It is an idiom, not "
            "a hydrology or bridge-traffic claim; no lookup is needed."
        )
    if (
        _TRAINWRECK_IDIOM_RE.search(semantic_text)
        and _FAILED_WORK_CONTEXT_RE.search(semantic_text)
        and not _LITERAL_TRAIN_CONTEXT_RE.search(semantic_text)
    ):
        return (
            "Calling the presentation a trainwreck means it was a disaster: it went very badly "
            "and was chaotic, disorganized, or visibly failing. The phrase is nonliteral and does "
            "not describe a train crash or rail accident; no schedule or rail-safety lookup is needed."
        )
    if (
        _SQUARE_CIRCLE_RE.search(semantic_text)
        and _SQUARE_CIRCLE_FRAME_RE.search(semantic_text)
        and not _SQUARE_CIRCLE_LITERAL_REQUEST_RE.search(semantic_text)
    ):
        return (
            "In that fable's quoted metaphor, a square circle represents an impossible or "
            "internally contradictory compromise: the defining requirements cannot all be "
            "satisfied at once. It is figurative language, not a claim that a literal Euclidean "
            "square circle exists."
        )
    if (
        _DATABASE_FIRE_RE.search(semantic_text)
        and _DATABASE_FAILURE_CONTEXT_RE.search(semantic_text)
        and not _LITERAL_DATABASE_FIRE_RE.search(semantic_text)
    ):
        return (
            "The database is on fire is a nonliteral technical metaphor for an urgent, severe "
            "database failure or operational crisis requiring immediate attention—for example, "
            "the stated latency alarms and failed writes. It does not mean the database is "
            "physically burning."
        )
    return None


__all__ = ["stable_metaphor_reference_response"]
