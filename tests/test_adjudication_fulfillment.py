import pytest

from core.conductor import obligation_ledger as ledger
from tests.test_entity_ambiguity import _probe_agent


@pytest.mark.parametrize("mode", ["raised", "replied_without_verdict", "precheck_error"])
@pytest.mark.parametrize("question", [
    "tell me something interesting",
    "Which terminal serves the island ferry?",
])
def test_failed_adjudication_outweighs_echoed_request_evidence(mode, question):
    current = ledger.open_obligation_set(request_text=question, obligations=[
        {"obligation_id": "ob:question", "kind": "demand", "unit_id": "question", "text": question},
    ])
    other = ledger.open_obligation_set(request_text="Calculate 11 times 13.", obligations=[
        {"obligation_id": "ob:other", "kind": "demand", "unit_id": "other", "text": "Calculate 11 times 13."},
    ])
    ledger.bind_active_set(current["set_id"], current["version"])
    try:
        _probe_agent()._record_unresolved_adjudication({}, mode=mode, attempts=2, detail="test fault")
        # The old text ladder treated the request echoed by the ask-back as satisfied.
        rows = ledger.sweep_demand_obligations(current["set_id"], current["version"],
                                               states={"question": "satisfied"})
        assert rows[0]["state"] == "unanswered"
        dispatch = ledger.slice_dispatches(current["set_id"], current["version"])
        assert len(dispatch) == 1
        assert dispatch[0]["failure_reason"] == mode
        assert ledger.slice_dispatches(other["set_id"], other["version"]) == ()
        assert ledger.demand_census(other["set_id"], other["version"])["demand_satisfied"] == 0
    finally:
        ledger.clear_active_set()


def test_without_failed_adjudication_valid_answer_evidence_keeps_its_authority():
    opened = ledger.open_obligation_set(request_text="Calculate 11 times 13.", obligations=[
        {"obligation_id": "ob:math", "kind": "demand", "unit_id": "math", "text": "Calculate 11 times 13."},
    ])
    rows = ledger.sweep_demand_obligations(opened["set_id"], opened["version"], states={"math": "satisfied"})
    assert rows[0]["state"] == "satisfied"


def test_failed_check_preserves_existing_answer_receipts_and_is_idempotent():
    opened = ledger.open_obligation_set(request_text="Compute the total and identify the terminal.", obligations=[
        {"obligation_id": "ob:total", "kind": "demand", "unit_id": "total", "text": "Compute the total."},
        {"obligation_id": "ob:terminal", "kind": "demand", "unit_id": "terminal", "text": "Identify the terminal."},
    ])
    sid, version = opened["set_id"], opened["version"]
    ledger.record_slice_consumption(sid, version, unit_id="total", evidence="slice_answer_record",
                                    family="calculation", excerpt="The total is 143.")
    ledger.bind_active_set(sid, version)
    try:
        for _ in range(2):
            _probe_agent()._record_unresolved_adjudication({}, mode="raised", attempts=2, detail="test fault")
        rows = ledger.sweep_demand_obligations(sid, version, states={"total": "satisfied", "terminal": "satisfied"})
        assert {row["unit_id"]: row["state"] for row in rows} == {"total": "satisfied", "terminal": "unanswered"}
        assert len(ledger.slice_dispatches(sid, version)) == 2
    finally:
        ledger.clear_active_set()
