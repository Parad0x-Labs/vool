"""One withheld statement must not stamp every slot "answered in part".

Measured 2026-11-05 (build 0a9ffad3, operator's live test of the local 0.7.8 app):
"hows going? whats the wetaher like in Rome now?" — the weather lookup SUCCEEDED 1/1, the
answer carried the sourced Rome reading — and the reply arrived stamped "Answered in part" on
BOTH slots. Mechanism: the text ladder graded both units `indeterminate` (the answer "Rome:
Partly cloudy, 18 C" never echoes the asking word "weather"; absence of echo asserts nothing —
the ladder's own law), and the withheld-claims append then read `state != DEMAND_SATISFIED`,
blanketing every non-satisfied slot the moment any statement was withheld anywhere.

The repaired contract, pinned here on the REAL sweep:
- a slot is "answered in part" only when a WITHHELD statement carries that slot's anchors (the
  gate removed THAT slot's words) or the slot is provably unanswered;
- the C7 reader-telling is preserved: a gate-emptied slot is named, because its own words ride
  the withheld claim;
- an echo-blind slot whose answer survived intact is never accused.
"""

from __future__ import annotations

import uuid


def drive(request: str, body: str, withheld_claims: tuple[str, ...]) -> str:
    from core.agent_runtime.answer_coverage import demand_units
    from core.conductor import obligation_ledger
    from core.finalization import _rss_closure_sweep

    attempt_id = f"attempt-{uuid.uuid4().hex}"
    units = demand_units(request)
    obset = obligation_ledger.open_obligation_set(
        request_text=request,
        request_id=f"req:probe:{uuid.uuid4().hex[:16]}",
        obligations=[
            {"obligation_id": f"ob:{attempt_id}:answer", "text": request[:240], "kind": "prose"},
            *(
                {
                    "obligation_id": f"ob:{attempt_id}:demand:{u.unit_id}",
                    "text": u.text,
                    "kind": "demand",
                    "unit_id": u.unit_id,
                    "slice_id": u.slice_id,
                }
                for u in units
            ),
        ],
    )
    closure = {"set_id": obset["set_id"], "set_version": obset["version"]}
    obligation_ledger.bind_active_set(obset["set_id"], obset["version"])
    try:
        served, _census = _rss_closure_sweep(
            body, closure, withheld_claims=withheld_claims, published_in_part=True
        )
    finally:
        obligation_ledger.clear_active_set()
    return served


SERVED_TURN_BODY = (
    "Running clean. What do you need?\n\n"
    "Rome: Partly cloudy, 18 C (today's high 19 C / low 9 C). "
    "Source: [open-meteo.com](https://open-meteo.com/), observed 2026-11-05T16:30."
)
TWO_PART_REQUEST = "hows going? whats the wetaher like in Rome now?"


def test_a_withheld_runtime_status_line_never_blanks_both_slots() -> None:
    """Tonight's served shape: the withheld 'Running clean' names no slot; no partial rows."""
    served = drive(
        TWO_PART_REQUEST,
        SERVED_TURN_BODY,
        withheld_claims=("Running clean. What do you need?",),
    )
    assert "Answered in part:" not in served, served
    assert SERVED_TURN_BODY in served, served


def test_a_gate_emptied_slot_is_still_named_by_its_own_words() -> None:
    """C7 preserved: the gate removed the Rome line; the withheld claim carries 'Rome'."""
    body = "Running clean. What do you need?"
    served = drive(
        TWO_PART_REQUEST,
        body,
        withheld_claims=("Rome: Partly cloudy, 18 C (today's high 19 C / low 9 C).",),
    )
    assert "Answered in part:" in served, served
    assert "whats the wetaher like in Rome now?" in served, served
    assert "hows going? — answered in part" not in served, served


def test_a_rome_claim_never_accuses_the_going_slot() -> None:
    """A Rome-attributed withheld claim must not accuse the unrelated 'hows going?' slot."""
    served = drive(
        TWO_PART_REQUEST,
        "Running clean. What do you need?",
        withheld_claims=("Rome: Partly cloudy, 18 C.",),
    )
    appended = served[len("Running clean. What do you need?"):]
    assert "hows going?" not in appended, appended


def test_the_no_withholding_turn_is_unchanged() -> None:
    """No gate edits, no rows: the pre-existing law for ordinary turns holds."""
    served = drive(TWO_PART_REQUEST, SERVED_TURN_BODY, withheld_claims=())
    assert "Answered in part:" not in served
    assert SERVED_TURN_BODY in served
