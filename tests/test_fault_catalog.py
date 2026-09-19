"""ONE versioned fault catalog: the tests that hold its shape.

Before this plane, a failure's meaning lived in whatever prose the failing seam
interpolated: ``"I couldn't get a live model response"``, ``"permission denied"``,
``"network fetch denied by the permission gateway: ..."``. Every surface re-guessed
severity, retryability and blame from those strings, and the same failure wore a
different sentence at every boundary that touched it. This pack holds the replacement
to five laws:

* the catalog is CLOSED and TYPED -- a code that is not declared does not exist, two
  declared entries may never collide, and every entry carries its operator contract
  (severity, retry, safe message, operator action, owning authority) as data;
* ONE failure maps ONCE -- the owning boundary mints the record, and every wrapper
  that re-raises through :func:`map_exception` preserves the original mapping instead
  of re-classifying the wrapper;
* identity is not optional -- a fault nobody can join to a turn, attempt, effect,
  session or stable dedupe key is refused, because an unjoinable fault re-creates the
  fragmentation the plane exists to remove;
* nothing a model or an exception carried survives unredacted -- context keys are
  allowlisted, values are secret-masked and path-stripped, cause chains carry class
  names and redacted messages only;
* the unknown is typed -- an exception nobody classified becomes ``FAULT_UNKNOWN``
  with an honest cause chain, never a guessed code and never a leaked secret.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from core.faults.catalog import (
    CATEGORY_AVAILABILITY,
    CATEGORY_CANCELLATION,
    CATEGORY_INTEGRITY,
    CATEGORY_POLICY,
    CATEGORY_SECURITY,
    FAULT_CANCELLED,
    FAULT_CONFINEMENT_REFUSAL,
    FAULT_CREDENTIAL_FAILURE,
    FAULT_EVIDENCE_CORRUPTION,
    FAULT_INTEGRITY_VERIFICATION_FAILURE,
    FAULT_PERMISSION_DENIED,
    FAULT_PROVIDER_EXHAUSTED,
    FAULT_PROVIDER_UNAVAILABLE,
    FAULT_SCHEMA,
    FAULT_TIMEOUT,
    FAULT_TOOL_UNAVAILABLE,
    FAULT_UNKNOWN,
    FAULT_UNSUPPORTED_CLAIM,
    DuplicateFaultCodeError,
    FaultSpec,
    UnknownFaultCodeError,
    all_codes,
    all_specs,
    build_catalog,
    catalog_digest,
    export_catalog,
    get_spec,
)
from core.faults.mapping import (
    FaultError,
    fault_code_for_provider_error_class,
    map_exception,
    retry_classification_from_faults,
)
from core.faults.recorder import (
    advance_lifecycle,
    faults_for_turn,
    record_fault,
    record_for_exception,
)
from core.faults.records import (
    FaultCause,
    FaultRecord,
    InvalidFaultTransitionError,
    MissingFaultIdentityError,
)
from core.faults.redaction import CONTEXT_KEY_ALLOWLIST, redact_context, redact_text

# --------------------------------------------------------------------------- catalog


def test_the_required_failure_families_are_declared():
    codes = set(all_codes())
    for required in (
        FAULT_PERMISSION_DENIED,
        FAULT_PROVIDER_UNAVAILABLE,
        FAULT_PROVIDER_EXHAUSTED,
        FAULT_TOOL_UNAVAILABLE,
        FAULT_TIMEOUT,
        FAULT_CANCELLED,
        FAULT_CONFINEMENT_REFUSAL,
        FAULT_EVIDENCE_CORRUPTION,
        FAULT_UNSUPPORTED_CLAIM,
        FAULT_CREDENTIAL_FAILURE,
        FAULT_INTEGRITY_VERIFICATION_FAILURE,
        FAULT_UNKNOWN,
    ):
        assert required in codes, f"required fault family missing from the catalog: {required}"


def test_every_declared_fault_carries_its_full_operator_contract():
    for spec in all_specs():
        assert spec.code, "a fault entry without a code is not an entry"
        assert spec.category in {
            CATEGORY_POLICY,
            CATEGORY_AVAILABILITY,
            CATEGORY_INTEGRITY,
            CATEGORY_SECURITY,
            CATEGORY_CANCELLATION,
            "internal",
        }
        assert spec.severity in {"info", "low", "medium", "high", "critical"}
        assert spec.retry in {"never", "retry_now", "retry_later", "retry_after_change"}
        assert spec.user_message.strip(), f"{spec.code}: a fault without safe user text"
        assert spec.operator_action.strip(), f"{spec.code}: a fault without operator action"
        assert spec.authority.strip(), f"{spec.code}: a fault without an owning authority"


def test_a_duplicate_code_cannot_enter_the_catalog():
    twin = FaultSpec(
        code=FAULT_TIMEOUT,
        category=CATEGORY_AVAILABILITY,
        severity="low",
        retry="never",
        user_message="twin",
        operator_action="twin",
        authority="test",
    )
    with pytest.raises(DuplicateFaultCodeError):
        build_catalog([*all_specs(), twin])


def test_an_undeclared_code_does_not_exist():
    with pytest.raises(UnknownFaultCodeError):
        get_spec("totally_made_up_fault")


def test_the_catalog_exports_machine_readably_and_stably():
    payload = export_catalog()
    raw = json.dumps(payload, sort_keys=True)
    assert payload["schema"] == FAULT_SCHEMA
    assert isinstance(payload["catalog_version"], int)
    assert len(payload["faults"]) == len(all_specs())
    for entry in payload["faults"]:
        assert set(entry) == {
            "code",
            "category",
            "severity",
            "retry",
            "retryable",
            "user_message",
            "operator_action",
            "authority",
            "security_relevant",
        }
        # The export must be re-readable as data: no nested objects, no Nones.
        assert all(isinstance(value, (str, bool, int)) for value in entry.values())
    assert catalog_digest() == catalog_digest(), "the catalog digest must be stable in-process"
    assert len(catalog_digest()) == 64
    # Round trip: the export is the machine-readable contract.
    assert json.loads(raw) == payload


def test_the_schema_version_travels_with_every_code():
    payload = export_catalog()
    assert payload["schema"] == FAULT_SCHEMA
    assert FAULT_SCHEMA.startswith("vool.fault.v")


# --------------------------------------------------------------------------- mapping


def test_the_same_exception_always_maps_to_the_same_record():
    def _boom() -> None:
        raise PermissionError("secrets.txt is not yours")

    for _ in range(3):
        try:
            _boom()
        except PermissionError as exc:
            first = map_exception(exc, authority="test-boundary", dedupe="d1")
    try:
        _boom()
    except PermissionError as exc:
        second = map_exception(exc, authority="test-boundary", dedupe="d1")
    assert first.code == second.code == FAULT_PERMISSION_DENIED
    assert first.fault_id == second.fault_id, "mapping must be a pure, stable function"


def test_the_exception_type_owns_the_mapping_not_its_message():
    """The mapping reads types and typed attributes, never prose."""
    for message in (
        "connection timed out while waiting",
        "waiting for the provider",
        "oops",
    ):
        record = map_exception(TimeoutError(message), authority="t", dedupe="m1")
        assert record.code == FAULT_TIMEOUT, message
        record = map_exception(PermissionError(message), authority="t", dedupe="m2")
        assert record.code == FAULT_PERMISSION_DENIED, message


def test_an_unknown_exception_maps_to_typed_unknown_without_leaking_secrets():
    secret = "sk-live-abcdef0123456789abcdef"
    try:
        raise RuntimeError(f"boom at /Users/example-user/desktop/secret_loader.py key={secret}")
    except RuntimeError as exc:
        record = map_exception(exc, authority="test-boundary", dedupe="u1")
    assert record.code == FAULT_UNKNOWN
    assert record.category == "internal"
    rendered = json.dumps(record.to_dict())
    assert secret not in rendered, "the raw exception text leaked into the fault record"
    assert "/Users/example-user" not in rendered, "a personal path leaked into the fault record"
    assert "RuntimeError" in rendered, "the honest class name is kept"


def test_a_wrapper_preserves_the_one_mapping_from_the_owning_boundary():
    """The owning boundary mapped the failure; a generic wrapper must not re-map it."""
    try:
        try:
            raise TimeoutError("provider did not answer in 30s")
        except TimeoutError as inner:
            raise FaultError(map_exception(inner, authority="provider-boundary", dedupe="w1")) from inner
    except FaultError as mapped:
        # Re-raised through three layers of generic wrappers that catch Exception and
        # wrap in RuntimeError -- the wrapper-drift defect shape.
        try:
            raise RuntimeError("turn failed") from mapped
        except RuntimeError as wrapped:
            record = map_exception(wrapped, authority="wrapper-seam")

    assert record.code == FAULT_TIMEOUT, (
        "a wrapper re-classified a mapped failure -- the one-mapping law broke"
    )
    assert record.authority == "provider-boundary"


def test_cancellation_maps_to_the_cancellation_family_and_is_never_retry_bait():
    try:
        raise asyncio.CancelledError()
    except asyncio.CancelledError as exc:
        record = map_exception(exc, authority="test-boundary", dedupe="c1")
    assert record.code == FAULT_CANCELLED
    assert record.retry == "never"


def test_provider_error_classes_map_onto_stable_codes():
    assert fault_code_for_provider_error_class("TimeoutError") == FAULT_TIMEOUT
    assert fault_code_for_provider_error_class("socket.timeout") == FAULT_TIMEOUT
    assert fault_code_for_provider_error_class("RateLimitError") == FAULT_PROVIDER_EXHAUSTED
    assert fault_code_for_provider_error_class("quota_exhausted") == FAULT_PROVIDER_EXHAUSTED
    assert fault_code_for_provider_error_class("ConnectionRefusedError") == FAULT_PROVIDER_UNAVAILABLE
    assert fault_code_for_provider_error_class("") == FAULT_PROVIDER_UNAVAILABLE
    assert fault_code_for_provider_error_class("MysteryError") == FAULT_PROVIDER_UNAVAILABLE


def test_the_retry_law_is_consistent_per_family():
    """Policy refusals and cancellations never read as 'just retry it'."""
    assert get_spec(FAULT_PERMISSION_DENIED).retry == "never"
    assert get_spec(FAULT_CONFINEMENT_REFUSAL).retry == "never"
    assert get_spec(FAULT_CANCELLED).retry == "never"
    assert get_spec(FAULT_UNKNOWN).retry == "never"
    assert get_spec(FAULT_TIMEOUT).retry == "retry_now"
    assert get_spec(FAULT_PROVIDER_EXHAUSTED).retry == "retry_later"
    assert get_spec(FAULT_PROVIDER_UNAVAILABLE).retry == "retry_later"
    # A retry hint without a safe message would send the user into the same wall.
    for spec in all_specs():
        if spec.retry != "never":
            assert "retry" in spec.user_message.lower() or "try" in spec.user_message.lower(), (
                f"{spec.code} claims retryability its user message never explains"
            )


def test_retry_classification_derives_from_the_turns_faults():
    from core.faults.records import fault_record_for_code

    cancelled = fault_record_for_code(FAULT_CANCELLED, authority="t", dedupe="a")
    timeout = fault_record_for_code(FAULT_TIMEOUT, authority="t", dedupe="b")
    denied = fault_record_for_code(FAULT_PERMISSION_DENIED, authority="t", dedupe="c")
    exhausted = fault_record_for_code(FAULT_PROVIDER_EXHAUSTED, authority="t", dedupe="d")

    assert retry_classification_from_faults([]) == ""
    assert retry_classification_from_faults([timeout]) == "retry_now"
    assert retry_classification_from_faults([exhausted]) == "retry_later"
    assert retry_classification_from_faults([denied]) == "do_not_retry"
    # A user cancellation outranks every other hint: retrying it overrides the user.
    assert retry_classification_from_faults([timeout, cancelled]) == "do_not_retry"


# --------------------------------------------------------------------------- identity


def test_a_fault_without_any_identity_is_refused():
    with pytest.raises(MissingFaultIdentityError):
        FaultRecord.for_code(FAULT_TIMEOUT, authority="test-boundary")
    # Authority alone is not identity -- it names who mapped it, not what it happened to.
    with pytest.raises(MissingFaultIdentityError):
        record_for_exception(TimeoutError("t"), authority="test-boundary")


def test_identity_travels_on_the_record():
    record = FaultRecord.for_code(
        FAULT_TOOL_UNAVAILABLE,
        authority="test-boundary",
        turn_key="turn-77",
        attempt_id="attempt-9",
        effect_id="effect-3",
        session_id="sess-1",
        dedupe="create_files",
    )
    assert record.turn_key == "turn-77"
    assert record.attempt_id == "attempt-9"
    assert record.effect_id == "effect-3"
    assert record.session_id == "sess-1"
    assert record.schema_version == FAULT_SCHEMA
    payload = record.to_dict()
    for key in ("fault_id", "code", "schema_version", "category", "severity", "retry", "lifecycle",
                "user_message", "operator_action", "authority", "turn_key", "attempt_id",
                "effect_id", "session_id", "evidence_refs", "context", "cause_chain", "created_at"):
        assert key in payload, f"the record projection is missing {key}"


# --------------------------------------------------------------------------- redaction


def test_context_is_allowlisted_redacted_and_truncated():
    leaked = redact_context(
        {
            "tool_name": "web.fetch",
            "provider_id": "openrouter",
            "api_key": "sk-live-abcdef0123456789abcdef",
            "authorization": "Bearer abc.def.ghi",
            "home_path": "/Users/example-user/secret-folder/creds.txt",
            "password": "hunter2",
            "target": "/home/alice/whatever",
        }
    )
    rendered = json.dumps(redact_text(json.dumps(leaked)))
    assert "sk-live" not in rendered
    assert "hunter2" not in rendered
    assert "Bearer abc" not in rendered
    assert "example-user" not in rendered  # the sanitized fixture home prefix must not leak
    assert "alice" not in rendered
    assert leaked["tool_name"] == "web.fetch", "safe structure that reproduction needs is kept"
    assert leaked["provider_id"] == "openrouter"


def test_unknown_context_keys_are_dropped_not_guessed():
    kept = redact_context({"tool_name": "t", "some_random_future_key": "value"})
    assert "some_random_future_key" not in kept
    assert set(CONTEXT_KEY_ALLOWLIST) >= {"tool_name", "provider_id"}


def test_redaction_is_idempotent():
    once = redact_text("Bearer abc123def456 at /Users/example-user/x/y.py")
    twice = redact_text(once)
    assert once == twice


def test_the_cause_chain_carries_types_and_redacted_messages_only():
    try:
        try:
            raise ValueError("token=ghp_abcdef0123456789abcdef0123456789abcdef12")
        except ValueError as inner:
            raise RuntimeError("outer failure") from inner
    except RuntimeError as exc:
        record = map_exception(exc, authority="test-boundary", dedupe="cc1")
    chain = record.to_dict()["cause_chain"]
    assert len(chain) >= 2, "the cause chain must survive, redacted -- not be dropped"
    rendered = json.dumps(chain)
    assert "ghp_" not in rendered, "a credential leaked through the cause chain"
    assert "ValueError" in rendered and "RuntimeError" in rendered


def test_a_fault_cause_hides_the_exception_payload():
    cause = FaultCause.from_exception(ValueError("api_key=sk-live-abcdef0123456789abcdef"))
    assert cause.exception_type == "ValueError"
    assert "sk-live" not in cause.message


def test_record_construction_itself_redacts_the_context():
    """The masking happens AT CONSTRUCTION, so a producer cannot forget it."""
    record = FaultRecord.for_code(
        FAULT_TIMEOUT,
        authority="test-boundary",
        turn_key="turn-redact-1",
        dedupe="r1",
        context={
            "tool_name": "web.fetch",
            "api_key": "sk-live-abcdef0123456789abcdef",
            "home": "/Users/example-user/secret-folder/creds.txt",
            "future_unknown_key": "value",
        },
    )
    rendered = json.dumps(record.to_dict())
    assert "sk-live" not in rendered, "the record stored an unmasked credential"
    assert "/Users/example-user" not in rendered, "the record stored an unmasked home path"
    assert "future_unknown_key" not in record.context, "an unallowlisted key rode the record"
    assert record.context.get("tool_name") == "web.fetch"


# --------------------------------------------------------------------------- recorder


def test_recording_is_durable_and_deduplicating():
    record = FaultRecord.for_code(
        FAULT_TIMEOUT, authority="test-boundary", turn_key="turn-rec-1", session_id="s-rec", dedupe="x1"
    )
    fault_id = record_fault(record)
    assert fault_id == record.fault_id
    assert record_fault(record) == fault_id, "the same fault recorded twice is still one fault"
    rows = faults_for_turn("turn-rec-1")
    assert len(rows) == 1
    assert rows[0].code == FAULT_TIMEOUT
    assert rows[0].lifecycle == "raised"


def test_recording_survives_a_broken_store_without_raising():
    record = FaultRecord.for_code(
        FAULT_TIMEOUT, authority="test-boundary", turn_key="turn-broken", dedupe="x2"
    )
    # A store that cannot persist must never be able to fail the caller.
    import core.faults.recorder as recorder_module

    original = recorder_module._persist_row
    try:
        def _explode(conn, row):
            raise RuntimeError("db gone")

        recorder_module._persist_row = _explode
        assert record_fault(record) == ""
    finally:
        recorder_module._persist_row = original


def test_lifecycle_transitions_are_typed_and_terminal_states_hold():
    record = FaultRecord.for_code(
        FAULT_PROVIDER_UNAVAILABLE, authority="test-boundary", turn_key="turn-lc-1", dedupe="x3"
    )
    fault_id = record_fault(record)
    served = advance_lifecycle(fault_id, "served", actor="surface")
    assert served is not None and served.lifecycle == "served"
    resolved = advance_lifecycle(fault_id, "resolved", actor="operator")
    assert resolved is not None and resolved.lifecycle == "resolved"
    # A resolved fault does not silently reopen.
    from core.faults.catalog import LIFECYCLE_RAISED

    with pytest.raises(InvalidFaultTransitionError):
        advance_lifecycle(fault_id, LIFECYCLE_RAISED, actor="operator")


def test_record_for_exception_maps_and_records_once():
    try:
        raise TimeoutError("slow provider")
    except TimeoutError as exc:
        fault_id = record_for_exception(
            exc, authority="test-boundary", turn_key="turn-rfe-1", session_id="s-rfe", dedupe="z1"
        )
    rows = faults_for_turn("turn-rfe-1")
    assert [row.code for row in rows] == [FAULT_TIMEOUT]
    assert rows[0].fault_id == fault_id


def test_security_relevant_faults_are_declared_so():
    """The security plane derives its events from these declarations -- no second list."""
    for code in (
        FAULT_PERMISSION_DENIED,
        FAULT_CONFINEMENT_REFUSAL,
        FAULT_CREDENTIAL_FAILURE,
        FAULT_INTEGRITY_VERIFICATION_FAILURE,
        FAULT_EVIDENCE_CORRUPTION,
    ):
        assert get_spec(code).security_relevant, f"{code} must be security-relevant"
    for code in (FAULT_TIMEOUT, FAULT_CANCELLED, FAULT_TOOL_UNAVAILABLE, FAULT_UNKNOWN):
        assert not get_spec(code).security_relevant, f"{code} must not page the security plane"
