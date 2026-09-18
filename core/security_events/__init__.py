"""The durable security-event plane: observations, never accusations.

Public surface:

* ``core.security_events.catalog`` -- the SEC vocabulary, derived from the fault
  catalog's ``security_relevant`` declarations (no second list to drift);
* ``core.security_events.records`` -- the typed event and its state machine;
* ``core.security_events.store``   -- the durable ledger: record, acknowledge,
  resolve, read, and the privacy-safe export.
"""
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
from core.security_events.records import (
    STATE_ACKNOWLEDGED,
    STATE_OBSERVED,
    STATE_RESOLVED,
    InvalidSecurityStateError,
    SecurityEvent,
)
from core.security_events.store import (
    acknowledge_security_event,
    events_for_turn,
    export_security_events,
    list_security_events,
    record_security_event,
    resolve_security_event,
    security_event_by_id,
)

__all__ = [
    "SEC_CONFINEMENT_REFUSAL",
    "SEC_CREDENTIAL_FAILURE",
    "SEC_EVIDENCE_CORRUPTION",
    "SEC_INTEGRITY_VERIFICATION_FAILURE",
    "SEC_PERMISSION_DENIED",
    "SEC_SCHEMA",
    "STATE_ACKNOWLEDGED",
    "STATE_OBSERVED",
    "STATE_RESOLVED",
    "InvalidSecurityStateError",
    "SecurityEvent",
    "SecurityVocabularyError",
    "UnknownSecurityCodeError",
    "acknowledge_security_event",
    "all_sec_codes",
    "events_for_turn",
    "export_security_events",
    "get_sec_spec",
    "list_security_events",
    "record_security_event",
    "resolve_security_event",
    "sec_spec_for_fault_code",
    "security_event_by_id",
]
