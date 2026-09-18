"""The durable security-event plane: observation, never accusation.

A security event records WHAT WAS OBSERVED -- a denial, a verification that failed, a
secret that could not be read -- in language that names the mechanism, not a suspected
mind. "A workspace write was denied by permission policy" is a fact; "the user attacked
the system" is an accusation the runtime is not qualified to make. This pack holds:

* the SEC vocabulary is typed and closed, and every code carries its observation
  contract (observation severity, state, source, affected resource class, summary);
* the wording law: no code's summary or exported form may accuse -- the banned
  vocabulary (attack, malicious, hostile, intrusion, breach...) must never appear;
* the state machine: observed -> acknowledged -> resolved, with typed refusals for
  impossible transitions and terminal states that hold;
* the durable store is joinable (turn/session/evidence refs) and its EXPORT is
  privacy-safe by construction -- typed fields only, self-scanned through the
  bug-report pipeline's outbound scanner;
* the linkage law: every security-relevant fault mints its matching security event at
  the same boundary, with the fault id carried as evidence -- one mapping, one event.
"""
from __future__ import annotations

import json

import pytest

from core.faults.catalog import (
    FAULT_CONFINEMENT_REFUSAL,
    FAULT_CREDENTIAL_FAILURE,
    FAULT_EVIDENCE_CORRUPTION,
    FAULT_INTEGRITY_VERIFICATION_FAILURE,
    FAULT_PERMISSION_DENIED,
    FAULT_TIMEOUT,
)
from core.faults.recorder import record_fault
from core.faults.records import FaultRecord
from core.security_events.catalog import (
    SEC_CONFINEMENT_REFUSAL,
    SEC_CREDENTIAL_FAILURE,
    SEC_EVIDENCE_CORRUPTION,
    SEC_INTEGRITY_VERIFICATION_FAILURE,
    SEC_PERMISSION_DENIED,
    SEC_SCHEMA,
    SecurityVocabularyError,
    UnknownSecurityCodeError,
    all_sec_codes,
    get_sec_spec,
    sec_spec_for_fault_code,
)
from core.security_events.store import (
    InvalidSecurityStateError,
    acknowledge_security_event,
    events_for_turn,
    export_security_events,
    list_security_events,
    record_security_event,
    resolve_security_event,
    security_event_by_id,
)

#: What a runtime may never say about a user it observes. Observation names mechanisms.
_BANNED_ACCUSATIONS = (
    "attack",
    "attacker",
    "malicious",
    "hostile",
    "intrusion",
    "intruder",
    "breach",
    "unauthorized user",
    "bad actor",
    "threat actor",
    "suspicious user",
)


# --------------------------------------------------------------------------- vocabulary


def test_the_security_vocabulary_covers_the_security_relevant_faults():
    codes = set(all_sec_codes())
    for fault_code in (
        FAULT_PERMISSION_DENIED,
        FAULT_CONFINEMENT_REFUSAL,
        FAULT_CREDENTIAL_FAILURE,
        FAULT_INTEGRITY_VERIFICATION_FAILURE,
        FAULT_EVIDENCE_CORRUPTION,
    ):
        spec = sec_spec_for_fault_code(fault_code)
        assert spec.sec_code in codes
        assert spec.fault_code == fault_code


def test_every_sec_entry_carries_its_observation_contract():
    for code in all_sec_codes():
        spec = get_sec_spec(code)
        assert code.startswith("SEC_"), code
        assert spec.severity in {"info", "low", "medium", "high", "critical"}
        assert spec.resource_class.strip(), f"{code}: no affected resource class"
        assert spec.source.strip(), f"{code}: no observing source"
        assert spec.summary.strip(), f"{code}: no observation summary"


def test_the_wording_law_no_code_accuses():
    for code in all_sec_codes():
        summary = get_sec_spec(code).summary.lower()
        for banned in _BANNED_ACCUSATIONS:
            assert banned not in summary, f"{code} accuses ({banned}) -- observation, not accusation"


def test_an_unknown_sec_code_does_not_exist():
    with pytest.raises(UnknownSecurityCodeError):
        get_sec_spec("SEC_MADE_UP")
    # A fault the catalog does not declare security-relevant must never mint a SEC event.
    with pytest.raises(SecurityVocabularyError):
        sec_spec_for_fault_code(FAULT_TIMEOUT)


# --------------------------------------------------------------------------- records + store


def _event(**overrides):
    from core.security_events.records import SecurityEvent

    fields = dict(
        sec_code=SEC_PERMISSION_DENIED,
        source="runtime_execution_tools",
        turn_key="turn-sec-1",
        session_id="s-sec",
        evidence_refs=("fault-abc",),
    )
    fields.update(overrides)
    record = FaultRecord.for_code(
        FAULT_PERMISSION_DENIED,
        authority="runtime_execution_tools",
        turn_key=fields.get("turn_key", ""),
        session_id=fields.get("session_id", ""),
        dedupe="sec-test",
    )
    # The event derives its identity (turn, session, fault id) from the record it observes.
    return SecurityEvent.for_fault(
        record,
        sec_code=fields.get("sec_code", ""),
        source=fields.get("source", ""),
        evidence_refs=fields.get("evidence_refs", ()),
    )


def test_a_security_event_carries_every_required_field():
    event = _event()
    payload = event.to_dict()
    for key in (
        "event_id",
        "sec_code",
        "schema_version",
        "severity",
        "state",
        "source",
        "resource_class",
        "summary",
        "fault_id",
        "evidence_refs",
        "turn_key",
        "session_id",
        "observed_at",
        "acknowledged_at",
        "acknowledged_by",
        "resolved_at",
        "resolution_note",
    ):
        assert key in payload, f"the security event projection is missing {key}"
    assert payload["schema_version"] == SEC_SCHEMA
    assert payload["state"] == "observed"


def test_the_summary_names_the_mechanism_never_a_suspect():
    event = _event()
    assert "denied" in event.summary.lower()
    for banned in _BANNED_ACCUSATIONS:
        assert banned not in event.summary.lower()


def test_recording_is_durable_and_joinable_by_turn():
    event = _event()
    event_id = record_security_event(event)
    assert event_id == event.event_id
    rows = events_for_turn("turn-sec-1")
    assert len(rows) == 1
    assert rows[0].sec_code == SEC_PERMISSION_DENIED
    assert rows[0].fault_id == event.fault_id
    assert security_event_by_id(event.event_id) is not None


def test_the_state_machine_observed_acknowledged_resolved():
    event = _event()
    event_id = record_security_event(event)
    acknowledged = acknowledge_security_event(event_id, by="operator")
    assert acknowledged.state == "acknowledged"
    assert acknowledged.acknowledged_by == "operator"
    assert acknowledged.acknowledged_at
    resolved = resolve_security_event(event_id, note="checked: policy working as intended")
    assert resolved.state == "resolved"
    assert resolved.resolution_note == "checked: policy working as intended"
    assert resolved.resolved_at


def test_impossible_transitions_are_typed_refusals_and_terminal_states_hold():
    event = _event()
    event_id = record_security_event(event)
    # observed -> resolved skips acknowledgement: refused, not silently allowed.
    with pytest.raises(InvalidSecurityStateError):
        resolve_security_event(event_id, note="too soon")
    resolved = resolve_security_event(acknowledge_security_event(event_id, by="operator").event_id, note="done")
    assert resolved.state == "resolved"
    with pytest.raises(InvalidSecurityStateError):
        acknowledge_security_event(event_id, by="operator")


def test_export_is_machine_readable_and_privacy_safe():
    event = _event(evidence_refs=("fault-abc",))
    record_security_event(event)
    acknowledge_security_event(event.event_id, by="operator")
    payload = export_security_events()
    assert payload["schema"] == SEC_SCHEMA
    assert payload["events"], "the export must carry the recorded events"
    raw = json.dumps(payload, sort_keys=True)
    for banned in _BANNED_ACCUSATIONS:
        assert banned not in raw.lower()
    row = payload["events"][0]
    assert set(row) == {
        "event_id",
        "sec_code",
        "schema_version",
        "severity",
        "state",
        "source",
        "resource_class",
        "summary",
        "fault_id",
        "evidence_refs",
        "turn_key",
        "session_id",
        "observed_at",
        "acknowledged_at",
        "acknowledged_by",
        "resolved_at",
        "resolution_note",
    }
    # The export must survive a round trip as pure data.
    assert json.loads(raw) == payload


def test_the_export_passes_the_bug_report_outbound_scanner():
    """The same outbound gate bug reports pass must pass for security-event exports."""
    from core.bug_report.scanner import scan_text

    record_security_event(
        _event(evidence_refs=("fault-abc",))
    )
    raw = json.dumps(export_security_events(), sort_keys=True)
    assert not scan_text(raw), "the security-event export carried scanner-flagged material"


def test_every_security_relevant_fault_mints_its_event_at_the_same_boundary():
    """One mapping, one event: recording the fault opens the SEC observation with the fault as evidence."""
    for fault_code, expected_sec in (
        (FAULT_PERMISSION_DENIED, SEC_PERMISSION_DENIED),
        (FAULT_CONFINEMENT_REFUSAL, SEC_CONFINEMENT_REFUSAL),
        (FAULT_CREDENTIAL_FAILURE, SEC_CREDENTIAL_FAILURE),
        (FAULT_INTEGRITY_VERIFICATION_FAILURE, SEC_INTEGRITY_VERIFICATION_FAILURE),
        (FAULT_EVIDENCE_CORRUPTION, SEC_EVIDENCE_CORRUPTION),
    ):
        record = FaultRecord.for_code(
            fault_code, authority="test-boundary", turn_key=f"turn-link-{fault_code}",
            session_id="s-link", dedupe=f"d-{fault_code}",
        )
        record_fault(record)
        rows = events_for_turn(f"turn-link-{fault_code}")
        assert len(rows) == 1, f"{fault_code} did not mint its security event"
        assert rows[0].sec_code == expected_sec
        assert rows[0].fault_id == record.fault_id
    # A non-security fault mints nothing.
    timeout = FaultRecord.for_code(
        FAULT_TIMEOUT, authority="test-boundary", turn_key="turn-link-timeout",
        session_id="s-link", dedupe="d-timeout",
    )
    record_fault(timeout)
    assert events_for_turn("turn-link-timeout") == []


def test_duplicate_recording_of_one_observation_stays_one_row():
    event = _event()
    record_security_event(event)
    record_security_event(event)
    assert len(events_for_turn("turn-sec-1")) == 1


def test_events_can_be_listed_by_state():
    event = _event()
    record_security_event(event)
    acknowledge_security_event(event.event_id, by="operator")
    observed = [e.event_id for e in list_security_events(state="observed")]
    acknowledged = [e.event_id for e in list_security_events(state="acknowledged")]
    assert event.event_id not in observed
    assert event.event_id in acknowledged
