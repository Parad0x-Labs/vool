"""This chat's own memory records as grounding support for the turn's publication gate.

A question about what someone in this chat said or did -- "When will Tim leave for Ireland?",
"How much does James pay per dance class?", "According to John, who is his favorite character
from Lord of the Rings?" -- carries the surface words of a schedule, price or public-entity
lookup, so the current-information signals open a grounding lifecycle for it. The web retrieves
nothing for it, and the gate refused the turn as unsupported, although the reader answered from
the records this chat holds (measured 2026-10-07: the same three questions refused in all three
of the memory port's official-150 parity runs).

The records the reader was given for this turn -- the admitted capsule evidence, read through the
one reader every guard uses (`core.bootstrap_context.admitted_capsule_evidence_text`) -- are
recorded on the lifecycle as support rows. The gate still matches claim by claim: a claim the
records state publishes; a claim they do not state (a live train time, a product's price today,
today's news) is withheld exactly as before. Lines the assistant itself said are not offered:
the model's own earlier output may not certify a claim.
"""

from __future__ import annotations

import re
from typing import Any

_ASSISTANT_LINE_RE = re.compile(r"^\s*-?\s*(?:\[[^\]]*\]\s*)?assistant\s+said\b", re.IGNORECASE)
_STRUCTURAL_LINE_RE = re.compile(r"^\s*</?[a-z_]+>\s*$|^\s*(?:distilled local facts|answer from these records)\b", re.IGNORECASE)


def memory_record_rows(evidence_text: Any) -> list[dict[str, str]]:
    """The admitted evidence as support rows: one row per record, assistant records excluded.

    A capsule record is a "- " bullet with the lines under it: "- user said (stated 2024-01-07): Session date:
    7 January, 2024" then "Tim: ... Next month, I'm off to Ireland". The record's date lives on the bullet and its
    words on the line below, so the row is the bullet with its continuation lines. A line under an assistant bullet
    is the assistant's words and is never a row; a line with no bullet above it is a record of its own.
    """

    rows: list[str] = []
    open_bullet = False
    in_assistant = False
    for raw in str(evidence_text or "").splitlines():
        line = raw.strip()
        if not line or _STRUCTURAL_LINE_RE.search(line):
            open_bullet = in_assistant = False
            continue
        if line.startswith("- ") or _ASSISTANT_LINE_RE.search(line):
            in_assistant = bool(_ASSISTANT_LINE_RE.search(line))
            open_bullet = not in_assistant
            if open_bullet:
                rows.append(line)
            continue
        if in_assistant:
            continue
        if open_bullet:
            rows[-1] = rows[-1] + " " + line
        else:
            rows.append(line)
    return [{"summary": row, "source": "memory_record"} for row in rows]


_ASKED_NAME_RE = re.compile(r"(?<![.!?]\s)(?<!^)\b([A-Z][a-z][\w'-]*)\b")


def question_names_someone_in_the_records(question: Any, evidence_text: Any) -> bool:
    """Whether the question asks about a named person or thing that this chat's own records name.

    "How much does James pay per dance class?" over records where James speaks is a question about the records; "When
    does the next train to Vilnius leave?" over records that never name Vilnius is not. Only names inside the question
    (not its first word) count, and only against record rows, never assistant lines.
    """

    lines = [line for line in str(question or "").splitlines() if line.strip()]
    asked = {re.sub(r"['\u2019]s$", "", name).lower() for name in _ASKED_NAME_RE.findall(lines[-1] if lines else "")}
    if not asked:
        return False
    rows = evidence_text if isinstance(evidence_text, (list, tuple)) else memory_record_rows(evidence_text)
    text = " ".join(
        str(row.get("withheld_speaker") or row.get("summary") or "") for row in rows if isinstance(row, dict)
    ).lower()
    return any(re.search(rf"(?<![\w'-]){re.escape(name)}(?![\w-])", text) for name in asked)


def publish_memory_records_for_turn(source_context: dict[str, Any] | None) -> int:
    """Record this turn's admitted memory evidence on its grounding lifecycle. Returns rows added.

    A no-op for a turn with no lifecycle (every turn the requirements authority did not mark
    current-information) and for a turn whose capsule admitted no evidence for this chat.
    """

    if not isinstance(source_context, dict):
        return 0
    try:
        from core.bootstrap_context import admitted_capsule_evidence_text
        from core.grounding_lifecycle import record_memory_records

        rows = memory_record_rows(admitted_capsule_evidence_text(source_context))
        # Withheld markers: the chat holds records about these speakers that the absence gate refused for this
        # facet. The marker carries the NAME only (summary is the name), so the records refusal can still say
        # "not mentioned in the records"; it is never a support row (grounding_publication skips withheld rows).
        from core.context_retrieval import get_last_retrieval_telemetry

        for name in get_last_retrieval_telemetry().get("absence_gate_withheld_speakers") or ():
            if str(name or "").strip():
                rows.append({"summary": str(name).strip(), "withheld_speaker": str(name).strip(), "withheld": True})
        return record_memory_records(source_context, entries=rows) if rows else 0
    except Exception:
        return 0


__all__ = ["memory_record_rows", "publish_memory_records_for_turn", "question_names_someone_in_the_records"]
