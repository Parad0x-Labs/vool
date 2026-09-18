"""Per-unit publication support for a MODEL-AUTHORED turn served by the PLAIN lane.

THE CONTRACT. A mixed turn -- "What is 5+5? What is the exact middle name of the current Emperor
of Japan? In what year did the Berlin Wall fall?" -- is current-information-required as a WHOLE
because one clause carries a temporal marker. The conductor answers it per clause: the
calculation node publishes its value through the COMPUTED channel and the knowledge node's line
through the STABLE-KNOWLEDGE channel (F43), so the sibling's grounding gate cannot take them
down. When the conductor declines and the plain lane serves the same turn, neither channel was
fed, every unsupported claim was withheld, and the whole answer was refused -- measured on the
frozen build (F48, t18c: "5 + 5 = 10" and "1989" refused beside the invented Emperor name) and
on the final pack's turn 14, where a comparison the authority itself read as STABLE was refused
whole after an optional enrichment search found nothing.

THE REPAIR, at the publication boundary, with the same three anchors the conductor's exemption
already enforces (`core.grounding_publication._stable_knowledge_exemptions`):

* DEMAND -- the request's own demand units (`core.agent_runtime.answer_coverage.demand_units`,
  the mint every closure certificate accounts in). A unit is STABLE when the requirements
  authority's own reading of THAT unit's text is DIRECT: no current-information requirement, no
  external-evidence requirement (`core.execution_requirements._compute_requirements`, the pure
  classifier). The Emperor clause reads current on its own text and records nothing.
* AUTHORITY -- the served author's policy verdict for this turn
  (`core.final_answer_authorship.authorship_record_for_publication`), the same
  `decide_final_answer_author` decision the conductor joins per call. An ineligible author
  records entries that exempt nothing; the join fails closed.
* SHAPE -- unchanged: a claim with a current-truth marker, a freshness cue, or an introduced
  non-year numeric never gains exemption. That is what keeps the world-facts pin true: the
  comparison prose publishes, an invented "37 million" does not.

A unit whose text the runtime's own arithmetic evaluator can compute (`5+5`) is not exempted:
its value is COMPUTED by the runtime (`core.conductor.operations._evaluate_arithmetic`, the
conductor's evaluator) and recorded as a computed-value row, so the model's "5+5 = 10" is
supported by a runtime computation and a wrong "5+5 = 11" is withheld.

The line that answers a unit is found by the closure ladder's own anchor rule
(`unit_anchors` / `_anchor_present`): every anchor the unit names must be present in the line.
A unit with no anchors binds nothing, exactly as the ladder declines to claim it.
"""

from __future__ import annotations

import logging
from typing import Any

_log = logging.getLogger(__name__)

PLAIN_LANE_UNIT_AUTHORITY = "plain_lane_stable_unit"


def unit_is_stable(unit_text: str) -> bool:
    """The requirements authority's own reading of ONE unit's text: DIRECT, no evidence required."""
    from core.execution_requirements import _compute_requirements

    text = " ".join(str(unit_text or "").split())
    if not text:
        return False
    try:
        requirements = _compute_requirements(text)
    except Exception:
        return False
    return (
        not bool(requirements.current_information_required)
        and not bool(requirements.external_evidence_required)
        and str(requirements.answer_mode or "") == "DIRECT"
    )


def _arithmetic_statement(unit_text: str) -> str:
    """The runtime's own evaluation of the unit's arithmetic ("5 + 5 = 10"), or ""."""
    from core.conductor.operations import _arithmetic_fragment, _evaluate_arithmetic

    try:
        fragment = _arithmetic_fragment(unit_text)
        if not fragment:
            return ""
        answer = _evaluate_arithmetic(str(fragment))
    except Exception:
        return ""
    return " ".join(str(answer or "").split())


def _identifying_anchors(unit_text: str, anchors: tuple[str, ...]) -> tuple[str, ...]:
    """The anchors that IDENTIFY what was asked about: numbers, codes, proper nouns.

    A truthful answer must carry these ("Berlin", "Wall", "1989"); the question's own working
    words ("year", "fall") it need not -- the answer says "fell in 1989". The closure ladder
    demands every anchor and grades that unit indeterminate for exactly this reason; binding an
    answer LINE to a unit needs the identifying ones, plus half of the rest.
    """
    import re

    from core.agent_runtime.answer_coverage import _UNIT_TOKEN_RE, _normalise_digits

    capitalised: set[str] = set()
    for index, match in enumerate(_UNIT_TOKEN_RE.finditer(_normalise_digits(unit_text))):
        token = match.group(0)
        if index > 0 and token[:1].isupper():
            capitalised.add(token.lower())
    out: list[str] = []
    for anchor in anchors:
        if anchor[:1].isdigit() or (anchor.upper() == anchor and re.search(r"[A-Z]", anchor)) or anchor in capitalised:
            out.append(anchor)
    return tuple(out)


def _line_answers_unit(unit_text: str, anchors: tuple[str, ...], served: frozenset[str]) -> bool:
    from core.agent_runtime.answer_coverage import _anchor_present

    if not anchors:
        return False
    identifying = _identifying_anchors(unit_text, anchors)
    if any(not _anchor_present(anchor, served) for anchor in identifying):
        return False
    present = sum(1 for anchor in anchors if _anchor_present(anchor, served))
    if present == 0:
        return False
    needed = (len(anchors) + 1) // 2
    return present >= needed


def _line_tokens(line: str) -> frozenset[str]:
    from core.agent_runtime.answer_coverage import _unit_tokens

    served = frozenset(_unit_tokens(line))
    return served | {token.replace(",", "").replace(".", "") for token in served}


def derive_plain_lane_unit_entries(
    *,
    request_text: str,
    answer_text: str,
    author_eligible: bool | None,
    serving_model: str = "",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(stable-knowledge entries, computed-value entries) for the plain lane's answer.

    Pure: reads the request and the answer, records nothing. Stable-knowledge entries carry the
    author verdict as `author_eligible`; computed entries carry the runtime's own arithmetic.
    """
    from core.agent_runtime.answer_coverage import answering_body, demand_units, unit_anchors

    request = str(request_text or "")
    answer = str(answer_text or "")
    if not request.strip() or not answer.strip():
        return [], []
    try:
        units = tuple(demand_units(request))
    except Exception:
        return [], []
    if not units:
        return [], []
    lines = [line.strip() for line in answering_body(answer).splitlines() if line.strip()]
    stable_entries: list[dict[str, Any]] = []
    computed_entries: list[dict[str, Any]] = []
    for unit in units:
        text = " ".join(str(getattr(unit, "text", "") or "").split())
        unit_id = str(getattr(unit, "unit_id", "") or "")
        if not text or not unit_id or not unit_is_stable(text):
            continue
        statement = _arithmetic_statement(text)
        if statement:
            computed_entries.append(
                {
                    "schema": "computed_value_v1",
                    "ok": True,
                    "node_id": f"unit:{unit_id}",
                    "demand_id": unit_id,
                    "demand_text": text[:200],
                    "summary": statement[:400],
                    "authority": PLAIN_LANE_UNIT_AUTHORITY,
                }
            )
            continue
        anchors = tuple(unit_anchors(text))
        if not anchors:
            continue
        render = ""
        if len(units) == 1:
            # ONE unit, answered across its lines (a comparison, an explanation): the unit's render
            # is the whole answering body, when the body answers it. The SHAPE guards still judge
            # each claim inside it on its own.
            body = " ".join(" ".join(lines).split())
            if body and _line_answers_unit(text, anchors, _line_tokens(body)):
                render = body
        else:
            for line in lines:
                if _line_answers_unit(text, anchors, _line_tokens(line)):
                    render = line
                    break
        if not render:
            continue
        stable_entries.append(
            {
                "node_id": f"unit:{unit_id}",
                "demand_id": unit_id,
                "demand_text": text[:200],
                "summary": render[:4000],
                "author_eligible": author_eligible is True,
                "authority": PLAIN_LANE_UNIT_AUTHORITY,
                "serving_model": str(serving_model or ""),
            }
        )
    return stable_entries, computed_entries


def publish_plain_lane_units_for_turn(
    source_context: dict[str, Any] | None, *, turn_id: str, answer_text: str
) -> dict[str, Any] | None:
    """Feed the turn's publication channels from the plain lane's answer, once, before the gate.

    Returns None when the question does not arise: no grounding lifecycle (a DIRECT turn), a
    lifecycle another lane already fed (the conductor's publishers), or bytes no model authored.
    """
    from core.final_answer_authorship import authorship_record_for_publication
    from core.grounding_lifecycle import (
        LIFECYCLE_ID_KEY,
        lifecycle_for_publication,
        record_computed_values,
        record_stable_knowledge,
    )

    lifecycle = lifecycle_for_publication(turn_id=turn_id)
    if lifecycle is None or not bool(getattr(lifecycle, "model_authored", False)):
        return None
    if tuple(getattr(lifecycle, "stable_knowledge", ()) or ()) or tuple(
        getattr(lifecycle, "computed_values", ()) or ()
    ):
        return None
    record = authorship_record_for_publication(turn_id=turn_id)
    decision = getattr(record, "decision", None) if record is not None else None
    author_eligible = getattr(decision, "eligible", None) if decision is not None else None
    serving_model = ""
    if decision is not None:
        serving_model = str(
            getattr(decision, "selected_model", "") or getattr(decision, "requested_model", "") or ""
        )
    request_text = str(getattr(lifecycle, "request_text", "") or "") or str(
        getattr(record, "request_text", "") or ""
    )
    stable, computed = derive_plain_lane_unit_entries(
        request_text=request_text,
        answer_text=answer_text,
        author_eligible=author_eligible,
        serving_model=serving_model,
    )
    if not stable and not computed:
        return {"units_recorded": 0, "computed_recorded": 0}
    context = source_context if isinstance(source_context, dict) else {}
    if not str(context.get(LIFECYCLE_ID_KEY) or "").strip():
        context[LIFECYCLE_ID_KEY] = str(getattr(lifecycle, "lifecycle_id", "") or "")
    added_stable = record_stable_knowledge(context, entries=stable) if stable else 0
    added_computed = record_computed_values(context, entries=computed) if computed else 0
    return {
        "units_recorded": int(added_stable),
        "computed_recorded": int(added_computed),
        "author_eligible": author_eligible is True,
        "serving_model": serving_model,
    }


__all__ = [
    "PLAIN_LANE_UNIT_AUTHORITY",
    "derive_plain_lane_unit_entries",
    "publish_plain_lane_units_for_turn",
    "unit_is_stable",
]
