"""AUD-20260829-003, C5/C7/C8 at the served door — a slot whose bytes were WITHHELD is not answered.

THE MEASURED DEFECT, on a real served /api/chat turn (capture:
`validation-logs/fabrication-signoff-p0-20260905/f_final/red_b_api_chat_f4c500fa.json`, run
"canonical"). The four-slot prompt was certified:

    {"covered": true, "demand_minted": 4, "demand_satisfied": 4, "demand_unanswered": 0}

while the served bytes contained NOTHING about the weather in Rome -- neither the word "Rome"
nor "weather" appears -- and carried the line "Withheld from this answer: 1 statement that the
sources retrieved for this turn do not support."

This is the false-coverage certificate the whole audit exists about, and it is not the text
ladder's fault: run on the SERVED bytes the ladder correctly returns `u3: indeterminate`. It is
overridden, because the producing lane wrote a lane-attested receipt for that slot BEFORE the
grounding publication gate removed the statement, and nothing retracts a receipt whose content
never reached the reader.

A receipt attests EXECUTION. `covered` is a claim about the ANSWER. When the gate withholds a
statement, the work behind it did not reach the user, and the certificate may not go on saying
it did.
"""
from __future__ import annotations

import pytest

import storage.db as sdb
from core.conductor import obligation_ledger as ol

TWO_SLOT = "What is the weather in Rome? What is the water temperature in the Baltic Sea?"


@pytest.fixture()
def fresh_store(tmp_path):
    from core.runtime_continuity import configure_runtime_continuity_db_path
    from storage.db import active_default_db_path
    from storage.migrations import run_migrations

    sdb.configure_default_db_path(tmp_path / "withheld.db")
    run_migrations()
    configure_runtime_continuity_db_path(active_default_db_path())
    yield
    configure_runtime_continuity_db_path(None)
    sdb.configure_default_db_path(None)
    ol.clear_active_set()


def _serve(content: str, withheld: tuple[str, ...], monkeypatch) -> dict:
    """One turn through the real finalize_answer, with the gate withholding `withheld`."""
    from core.agent_runtime.answer_coverage import demand_units
    from core.finalization import finalize_answer
    from core.semantic.semantic_result_seam import admit_semantic_result, reset_admission

    units = demand_units(TWO_SLOT)
    opened = ol.open_obligation_set(
        request_text=TWO_SLOT,
        obligations=[
            {"obligation_id": "ob:w:answer", "text": TWO_SLOT[:240], "kind": "prose"},
            *(
                {
                    "obligation_id": f"ob:w:demand:{u.unit_id}",
                    "text": u.text,
                    "kind": "demand",
                    "unit_id": u.unit_id,
                    "slice_id": u.slice_id,
                }
                for u in units
            ),
        ],
    )
    # THE PRODUCING LANE ATTESTS BOTH SLOTS -- it ran, and at the time it ran it had content
    # for both. That receipt is honest about execution and is exactly what the sweep's top
    # tier consumes.
    for unit in units:
        ol.record_slice_consumption(
            opened["set_id"], opened["version"], unit_id=unit.unit_id,
            family="live_info", evidence="slice_answer_record",
        )
        ol.record_slice_dispatch(
            opened["set_id"], opened["version"], unit_id=unit.unit_id,
            subtask_id=f"w:{unit.unit_id}", operation="live_info", state="SUCCEEDED",
        )
    ol.record_disposition(
        opened["set_id"], opened["version"], "ob:w:answer", "satisfied",
        evidence_source="served_bytes",
    )

    published = content
    for claim in withheld:
        published = published.replace(claim, "")

    def _gate(text, *, turn_id=""):
        return published, {
            "publication": {
                "schema": "vool.grounding_publication.v1",
                "state": "published",
                "withheld_claim_count": len(withheld),
                "withheld_claims": list(withheld),
            }
        }

    monkeypatch.setattr("core.grounding_publication.gate_publishable_content", _gate)
    reset_admission()
    admit_semantic_result({"response": published, "route_reason": "probe"})
    return finalize_answer(
        turn_id="w",
        canonical_content=content,
        closure={**ol.closure_verdict(opened["set_id"], opened["version"]),
                 "set_id": opened["set_id"]},
    )


ROME = "Rome: Sunny, 31 °C (source: wttr.in)."
WATER = "Water temperature at Jurmala, Latvia (Baltic Sea): 17.8°C (source: open-meteo.com (marine))."


def test_the_control_nothing_withheld_certifies_both_slots(fresh_store, monkeypatch):
    """CONTROL. With both statements published the certificate says so, and must keep saying
    so -- a fix that simply stopped trusting receipts would break this."""
    verdict = _serve(f"{ROME}\n{WATER}", (), monkeypatch)["closure_verdict"]
    assert verdict["demand_satisfied"] == 2, verdict
    assert verdict["demand_unanswered"] == 0, verdict
    assert verdict["covered"] is True, verdict


def test_a_withheld_statement_unsatisfies_the_slot_it_was_about(fresh_store, monkeypatch):
    """THE DEFECT. The gate removes the Rome statement; the receipt for that slot stands; the
    certificate goes on reporting it satisfied and the turn covered."""
    verdict = _serve(f"{ROME}\n{WATER}", (ROME,), monkeypatch)["closure_verdict"]
    assert verdict["demand_satisfied"] == 1, verdict
    assert verdict["covered"] is False, verdict


def test_the_served_bytes_never_carry_the_withheld_statement(fresh_store, monkeypatch):
    """And the bytes stay gated: nothing here may put the withheld claim back."""
    commit = _serve(f"{ROME}\n{WATER}", (ROME,), monkeypatch)
    assert "Sunny" not in commit["canonical_content"], commit["canonical_content"]
    assert "Jurmala" in commit["canonical_content"]


def test_the_reader_is_told_which_slot_went_missing(fresh_store, monkeypatch):
    """THE OTHER HALF, and the one the user actually experiences.

    Demoting the slot in the census stops the CERTIFICATE lying. The certificate is not what
    anyone reads. Measured on the served turn: the gate removed the Rome weather line, the
    answer shipped with no Rome in it, and the only trace was "Withheld from this answer: 1
    statement that the sources retrieved for this turn do not support" -- which names a source
    URL, not the slot. Nobody was told which of the four things they asked for went missing.
    That is C7's silent drop arriving through the gate.
    """
    from core.finalization import RSS_UNAVAILABLE_HEADER

    commit = _serve(f"{ROME}\n{WATER}", (ROME,), monkeypatch)
    served = commit["canonical_content"]
    assert RSS_UNAVAILABLE_HEADER in served, served
    assert "weather in Rome" in served, served
    assert "withheld" in served.lower(), served
    # The slot that DID survive the gate is not accused: it appears once, as its answer.
    assert served.lower().count("water temperature") == 1, served
    assert commit["closure_verdict"]["demand_rendered"] >= 1, commit["closure_verdict"]


def test_a_turn_that_withheld_nothing_renders_exactly_as_before(fresh_store, monkeypatch):
    """CONTROL for the disclosure. A normal turn must not gain rows."""
    from core.finalization import RSS_UNAVAILABLE_HEADER

    commit = _serve(f"{ROME}\n{WATER}", (), monkeypatch)
    assert RSS_UNAVAILABLE_HEADER not in commit["canonical_content"]
    assert commit["closure_verdict"]["demand_rendered"] == 0
