"""v14.6 hardening items 8 and 9 (ASTRA Pro review, 2026-10-07). Item 8: the receipt chain is ASKED -> DISCOVERED ->
ACTIVATED -> DELIVERED -> ASSERTED -> SUPPORTED with no USED stage; 'supported' means supported by an occurrence or a
derivation, never 'true'; the claim envelope's status vocabulary is supported | contradicted | qualified | unsupported
| not_attempted. Item 9: temporal fields keep unknown unknown; a record's statement day comes from its own statement
time or the turn's own session header, never from the capture time; a state's effective window comes from the chain.
Contributor: sls_0x."""
from __future__ import annotations

from core.evidence_compiler import _render_line, verify_answer
from core.memory_receipts import extract_facts
from core.unsourced_current_claim import _claim_envelope_status


def test_the_claim_envelope_status_vocabulary():
    assert _claim_envelope_status({"attempted": False}) == "not_attempted"
    assert _claim_envelope_status({"attempted": True, "all_supported": True}) == "supported"
    assert _claim_envelope_status({"attempted": True, "all_supported": False, "contradicted": True, "qualifiable": False}) == "contradicted"
    assert _claim_envelope_status({"attempted": True, "all_supported": False, "contradicted": False, "qualifiable": True}) == "qualified"
    assert _claim_envelope_status({"attempted": True, "all_supported": False, "contradicted": False, "qualifiable": False}) == "unsupported"


def test_verify_answer_reports_supported_and_the_chain_stage_beside_verified():
    d = verify_answer("How long between the two trips?", "There is no number here.", [])
    assert d["verified"] is False and d["supported"] is False and d["chain_stage"] == "ASSERTED"


def test_a_turn_without_a_statement_time_or_session_header_keeps_its_days_unknown():
    facts = extract_facts("I bought a kettle for $40 and it arrived on 2 March.", None, "user")
    kinds = {f.value_type: f for f in facts}
    assert "amount" in kinds and kinds["amount"].event_at is None       # no statement day: a dated event cannot be resolved from nothing
    assert all(f.event_at is None or f.event_grain for f in facts)


def test_a_session_header_dates_the_statement_but_capture_time_never_does():
    with_header = extract_facts("Session date: 2025/03/03 (Mon) 09:00\nI bought a kettle for $40 yesterday.", None, "user")
    amount = next(f for f in with_header if f.value_type == "amount")
    assert amount.event_at is not None and amount.event_grain   # "yesterday" resolves from the header's day
    without = extract_facts("I bought a kettle for $40 yesterday.", None, "user")
    assert next(f for f in without if f.value_type == "amount").event_at is None


def test_a_state_row_carries_its_effective_window_from_the_chain():
    old = {"receipt_id": "r:old", "role": "user", "statement_at": 1736500000.0}
    new = {"receipt_id": "r:new", "role": "user", "statement_at": 1743600000.0}
    by_id = {"r:old": old, "r:new": new}
    replaced = {"sentence": "I drive a Skoda Octavia these days.", "value_type": "state", "norm": "vehicle=skoda octavia", "value": "Skoda Octavia", "replaced_by": "r:new"}
    current = {"sentence": "I drive a Kia Niro now.", "value_type": "state", "norm": "vehicle=kia niro", "value": "Kia Niro"}
    a = _render_line(old, replaced, by_id)
    b = _render_line(new, current, by_id)
    assert "effective from 2025-01-10 until 2025-04-02" in a, a
    assert "effective from 2025-04-02, current" in b, b


def test_an_unknown_replacement_day_stays_unknown():
    old = {"receipt_id": "r:old", "role": "user", "statement_at": 1736500000.0}
    by_id = {"r:old": old, "r:new": {"receipt_id": "r:new", "role": "user", "statement_at": None}}
    replaced = {"sentence": "I drive a Skoda Octavia these days.", "value_type": "state", "norm": "vehicle=skoda octavia", "value": "Skoda Octavia", "replaced_by": "r:new"}
    assert "until unknown day" in _render_line(old, replaced, by_id)
