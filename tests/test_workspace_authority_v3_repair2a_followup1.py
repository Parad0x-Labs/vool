"""Repair #2A follow-up #1: authority derivation must not dispatch through virtual methods.

Every trusted canonical value — serialized identity text, counter values, enum values,
invocation digests, and the OperationId/MutationId derived from them — must come from the
closed owner-controlled representation.  Replacing a caller-visible class method must not
move an authority-bearing byte.
"""

from __future__ import annotations

import contextlib
from dataclasses import replace

import pytest

from core.workspace_authority_v3.base import (
    canonical_enum_value,
    sealed_authority_record_bytes,
    sealed_authority_record_digest,
)
from core.workspace_authority_v3.contracts import (
    DormantKeyAuthority,
    EnrollmentClaimV3,
    KeyFamily,
    KeyGenerationMetadata,
    KeyReference,
    KeyRingMetadata,
    KeyState,
)
from core.workspace_authority_v3.cursor import (
    AuthenticatedCursorPayload,
    CursorCodec,
    CursorDirection,
    CursorExpectation,
)
from core.workspace_authority_v3.database import DormantAuthorityDatabase, TransactionOutcome
from core.workspace_authority_v3.identity import (
    AuthorityClaimProvenance,
    AuthorityDomainId,
    AuthorityEpoch,
    EnrollmentOperationId,
    HeadRevision,
    IncarnationId,
    InvocationIdentity,
    MutationId,
    OpaqueIdentity,
    OperationId,
    OperationIdentityBinding,
    RecoveryCycleId,
    RecoveryDecisionId,
    RuntimeHomeId,
    WorkspaceEnrollmentProvenance,
    WorkspaceId,
    WorkspaceSequence,
    _BoundedCounter,
    _seal_canonical_slots,
    canonical_binding_mutation_id,
    canonical_binding_operation_id,
    canonical_counter_value,
    canonical_identity_text,
    canonical_invocation_digest,
    canonical_invocation_fields,
    canonical_operation_binding_digest,
    canonical_operation_binding_payload,
)
from core.workspace_authority_v3.lifecycle import (
    DormantOperation,
    DormantOperationRegistry,
    IdempotencyConflictError,
    RecoveryDecisionResult,
    _dormant_operation_fingerprint,
    _head_state_fingerprint,
    enter_recovery,
    issue_recovery_decision,
)
from tests.workspace_authority_v3_fixtures import active_state_owner, ring_workspace_id

KEY = b"f" * 32
FORGED_WORKSPACE = f"workspace_{'9' * 64}"


@contextlib.contextmanager
def substituted(owner: type, name: str, replacement: object):
    """Replace one class-level member for the duration of the block, then restore exactly."""

    had_own = name in owner.__dict__
    original = owner.__dict__.get(name)
    type.__setattr__(owner, name, replacement)
    try:
        yield
    finally:
        if had_own:
            type.__setattr__(owner, name, original)
        else:
            type.__delattr__(owner, name)


def _enrollment_root() -> tuple[AuthorityDomainId, EnrollmentOperationId, WorkspaceId]:
    domain = AuthorityDomainId.from_authority_claim(AuthorityClaimProvenance.establish())
    enrollment_operation = EnrollmentOperationId.new()
    enrollment = WorkspaceEnrollmentProvenance.establish(
        authority_domain_id=domain,
        enrollment_operation_id=enrollment_operation,
    )
    return domain, enrollment_operation, WorkspaceId.from_enrollment(enrollment)


def _key_metadata(family: KeyFamily, key_id: str, generation: int, state: KeyState):
    return KeyGenerationMetadata(family, key_id, generation, state, "HMAC-SHA256", 1)


def _authority(authority_domain: AuthorityDomainId | None = None) -> DormantKeyAuthority:
    metadata = KeyRingMetadata(
        (
            _key_metadata(KeyFamily.CLAIM_ENROLLMENT, "claim-1", 1, KeyState.ACTIVE),
            _key_metadata(KeyFamily.CURSOR, "cursor-1", 1, KeyState.ACTIVE),
        )
    )
    return DormantKeyAuthority(
        metadata,
        {
            KeyReference(KeyFamily.CLAIM_ENROLLMENT, "claim-1", 1): KEY,
            KeyReference(KeyFamily.CURSOR, "cursor-1", 1): KEY,
        },
        workspace_id=ring_workspace_id(),
        authority_domain=authority_domain,
    )


def _issue_claim(authority: DormantKeyAuthority, root) -> EnrollmentClaimV3:
    domain, enrollment_operation, workspace = root
    return EnrollmentClaimV3.issue(
        authority_domain_id=domain,
        workspace_id=workspace,
        incarnation_id=IncarnationId.new(),
        enrollment_operation_id=enrollment_operation,
        incarnation_nonce="1" * 64,
        persistent_volume_identity_digest="2" * 64,
        root_object_identity_digest="3" * 64,
        runtime_home_id=RuntimeHomeId.new(),
        signing_key=authority.signing_capability(
            family=KeyFamily.CLAIM_ENROLLMENT,
            generation=1,
            key_id="claim-1",
        ),
    )


def _invocation() -> InvocationIdentity:
    return InvocationIdentity(f"turn_{'1' * 64}", 2, 3)


def _binding(workspace: WorkspaceId) -> OperationIdentityBinding:
    return OperationIdentityBinding(
        invocation_identity=_invocation(),
        workspace_id=workspace,
        authority_epoch=AuthorityEpoch(4),
        canonical_action_digest="a" * 64,
    )


# ---------------------------------------------------------------------------
# A. Reported defect 1 — workspace identity serialization.
# ---------------------------------------------------------------------------


def test_followup1_workspace_str_substitution_cannot_forge_enrollment_serialization() -> None:
    root = _enrollment_root()
    authority = _authority(root[0])
    _domain, _enrollment_operation, workspace = root
    true_workspace = canonical_identity_text(workspace, WorkspaceId)
    verifier = authority.verification_capability(
        family=KeyFamily.CLAIM_ENROLLMENT,
        generation=1,
        key_id="claim-1",
    )

    with substituted(WorkspaceId, "__str__", lambda self: FORGED_WORKSPACE):
        assert str(workspace) == FORGED_WORKSPACE
        issued_under_substitution = _issue_claim(authority, root)
        serialized = sealed_authority_record_bytes(issued_under_substitution)
        assert issued_under_substitution.to_record()["payload"]["workspace_id"] == true_workspace
        assert FORGED_WORKSPACE.encode("ascii") not in serialized

    decoded = EnrollmentClaimV3.from_json(
        issued_under_substitution.canonical_bytes(),
        verification_key=verifier,
    )
    assert canonical_identity_text(decoded.workspace_id, WorkspaceId) == true_workspace
    assert decoded.workspace_id == workspace
    assert decoded.verify_mac(verifier)


def test_followup1_workspace_str_substitution_cannot_move_an_existing_claim() -> None:
    root = _enrollment_root()
    authority = _authority(root[0])
    claim = _issue_claim(authority, root)
    baseline = sealed_authority_record_bytes(claim)
    baseline_digest = claim.expected_claim_digest()

    with substituted(WorkspaceId, "__str__", lambda self: FORGED_WORKSPACE):
        assert sealed_authority_record_bytes(claim) == baseline
        assert claim.expected_claim_digest() == baseline_digest
        assert claim.verify_mac(
            authority.verification_capability(
                family=KeyFamily.CLAIM_ENROLLMENT,
                generation=1,
                key_id="claim-1",
            )
        )
    assert sealed_authority_record_bytes(claim) == baseline


# ---------------------------------------------------------------------------
# B. Reported defect 2 — invocation derivation.
# ---------------------------------------------------------------------------


def test_followup1_invocation_digest_substitution_cannot_change_derived_operation_id() -> None:
    _domain, _enrollment_operation, workspace = _enrollment_root()
    binding = _binding(workspace)
    true_operation_id = canonical_identity_text(canonical_binding_operation_id(binding), OperationId)
    true_mutation_id = canonical_identity_text(canonical_binding_mutation_id(binding), MutationId)
    true_binding_digest = canonical_operation_binding_digest(binding)
    true_invocation_digest = canonical_invocation_digest(binding.invocation_identity)

    with substituted(InvocationIdentity, "digest", lambda self: "0" * 64):
        assert canonical_invocation_digest(_invocation()) == true_invocation_digest
        registered = DormantOperationRegistry().register(binding, "b" * 64).operation
        assert canonical_identity_text(registered.operation_id, OperationId) == true_operation_id
        assert canonical_identity_text(registered.mutation_id, MutationId) == true_mutation_id
        assert canonical_operation_binding_digest(binding) == true_binding_digest


def test_followup1_invocation_canonical_payload_substitution_cannot_change_derived_ids() -> None:
    _domain, _enrollment_operation, workspace = _enrollment_root()
    binding = _binding(workspace)
    true_operation_id = canonical_identity_text(canonical_binding_operation_id(binding), OperationId)
    forged_payload = {
        "canonical_turn_id": f"turn_{'7' * 64}",
        "controller_generation": 99,
        "persisted_tool_ordinal": 99,
    }

    with substituted(InvocationIdentity, "canonical_payload", lambda self: forged_payload):
        registered = DormantOperationRegistry().register(binding, "b" * 64).operation
        assert canonical_identity_text(registered.operation_id, OperationId) == true_operation_id
        assert canonical_invocation_fields(binding.invocation_identity) == (
            f"turn_{'1' * 64}",
            2,
            3,
        )


def test_followup1_changing_real_invocation_fields_still_changes_the_derived_id() -> None:
    """The closure must bind the true fields, not merely ignore substitution."""

    _domain, _enrollment_operation, workspace = _enrollment_root()
    baseline = canonical_identity_text(
        canonical_binding_operation_id(_binding(workspace)),
        OperationId,
    )
    variants = (
        InvocationIdentity(f"turn_{'2' * 64}", 2, 3),
        InvocationIdentity(f"turn_{'1' * 64}", 3, 3),
        InvocationIdentity(f"turn_{'1' * 64}", 2, 4),
    )
    for variant in variants:
        candidate = OperationIdentityBinding(
            invocation_identity=variant,
            workspace_id=workspace,
            authority_epoch=AuthorityEpoch(4),
            canonical_action_digest="a" * 64,
        )
        assert (
            canonical_identity_text(canonical_binding_operation_id(candidate), OperationId)
            != baseline
        )


def test_followup1_forged_public_derivation_results_cannot_be_accepted_as_authority(tmp_path) -> None:
    _domain, _enrollment_operation, workspace = _enrollment_root()
    binding = _binding(workspace)
    true_operation_id = canonical_identity_text(canonical_binding_operation_id(binding), OperationId)
    true_binding_digest = canonical_operation_binding_digest(binding)
    reached: list[str] = []

    def forged_operation_id(self: object) -> MutationId:
        reached.append("operation_id")
        return MutationId.new()

    def forged_digest(self: object) -> str:
        reached.append("digest")
        return "0" * 64

    with (
        substituted(InvocationIdentity, "operation_id", forged_operation_id),
        substituted(OperationIdentityBinding, "digest", forged_digest),
    ):
        registration = DormantOperationRegistry().register(binding, "b" * 64)
        operation = registration.operation
        assert canonical_identity_text(operation.operation_id, OperationId) == true_operation_id
        database = DormantAuthorityDatabase(tmp_path / "followup1.sqlite3")
        stored = database.register_operation(operation)
        assert stored.outcome is TransactionOutcome.COMMITTED_NEW
        assert stored.value is not None
        assert stored.value.binding_digest == true_binding_digest
        with pytest.raises((TypeError, ValueError)):
            DormantOperation(MutationId.new(), MutationId.new(), binding, "b" * 64)
    assert reached == []


def test_followup1_binding_derivation_substitution_cannot_admit_a_forged_operation(tmp_path) -> None:
    """DormantOperation must re-derive its identities through the closed representation."""

    _domain, _enrollment_operation, workspace = _enrollment_root()
    binding = _binding(workspace)
    true_operation_id = canonical_binding_operation_id(binding)
    true_mutation_id = canonical_binding_mutation_id(binding)

    other = OperationIdentityBinding(
        invocation_identity=InvocationIdentity(f"turn_{'2' * 64}", 2, 3),
        workspace_id=workspace,
        authority_epoch=AuthorityEpoch(4),
        canonical_action_digest="a" * 64,
    )
    forged_operation_id = canonical_binding_operation_id(other)
    forged_mutation_id = canonical_binding_mutation_id(other)
    assert forged_operation_id != true_operation_id

    with (
        substituted(OperationIdentityBinding, "operation_id", lambda self: forged_operation_id),
        substituted(OperationIdentityBinding, "mutation_id", lambda self: forged_mutation_id),
    ):
        with pytest.raises((TypeError, ValueError)):
            DormantOperation(forged_operation_id, forged_mutation_id, binding, "b" * 64)
        accepted = DormantOperation(true_operation_id, true_mutation_id, binding, "b" * 64)
        assert accepted.operation_id == true_operation_id

        registry = DormantOperationRegistry()
        registration = registry.register(binding, "b" * 64)
        assert registration.operation.operation_id == true_operation_id
        assert registration.operation.mutation_id == true_mutation_id
        assert registration.replayed is False

        # The registry must look its entry up under the same closed identity it stores.
        replay = registry.register(binding, "b" * 64)
        assert replay.replayed is True
        assert replay.operation is registration.operation
        with pytest.raises(IdempotencyConflictError):
            registry.register(binding, "c" * 64)

        database = DormantAuthorityDatabase(tmp_path / "forged-binding.sqlite3")
        stored = database.register_operation(registration.operation)
        assert stored.outcome is TransactionOutcome.COMMITTED_NEW
        assert stored.value is not None
        assert stored.value.operation_id == true_operation_id
        assert stored.value.binding_digest == canonical_operation_binding_digest(binding)


def test_followup1_database_replay_derivation_survives_invocation_substitution(tmp_path) -> None:
    """Replay reconstructs stored identities through the closed derivation, not a class method."""

    _domain, _enrollment_operation, workspace = _enrollment_root()
    binding = _binding(workspace)
    operation = DormantOperationRegistry().register(binding, "b" * 64).operation
    true_operation_id = canonical_binding_operation_id(binding)
    true_mutation_id = canonical_binding_mutation_id(binding)

    database_path = tmp_path / "replay.sqlite3"
    first = DormantAuthorityDatabase(database_path)
    assert first.register_operation(operation).outcome is TransactionOutcome.COMMITTED_NEW

    other_invocation = InvocationIdentity(f"turn_{'3' * 64}", 2, 3)
    forged_operation_id = other_invocation.operation_id()
    forged_mutation_id = other_invocation.mutation_id()
    assert forged_operation_id != true_operation_id

    with (
        substituted(InvocationIdentity, "operation_id", lambda self: forged_operation_id),
        substituted(InvocationIdentity, "mutation_id", lambda self: forged_mutation_id),
    ):
        reopened = DormantAuthorityDatabase(database_path)
        replay = reopened.register_operation(operation)
        assert replay.outcome is TransactionOutcome.COMMITTED_REPLAY
        assert replay.value is not None
        assert replay.value.operation_id == true_operation_id
        assert replay.value.mutation_id == true_mutation_id


def test_followup1_persisted_binding_payload_is_the_closed_canonical_payload(tmp_path) -> None:
    """A record written under substitution must still replay against an unsubstituted reader."""

    _domain, _enrollment_operation, workspace = _enrollment_root()
    binding = _binding(workspace)
    operation = DormantOperationRegistry().register(binding, "b" * 64).operation
    database_path = tmp_path / "payload.sqlite3"

    with substituted(OperationIdentityBinding, "canonical_payload", lambda self: {"forged": True}):
        database = DormantAuthorityDatabase(database_path)
        assert database.register_operation(operation).outcome is TransactionOutcome.COMMITTED_NEW

    reopened = DormantAuthorityDatabase(database_path)
    replay = reopened.register_operation(operation)
    assert replay.outcome is TransactionOutcome.COMMITTED_REPLAY
    assert replay.value is not None
    assert replay.value.operation_id == canonical_binding_operation_id(binding)
    assert replay.value.binding_digest == canonical_operation_binding_digest(binding)


# ---------------------------------------------------------------------------
# C/D. Substitution pressure across the audited authority surface.
# ---------------------------------------------------------------------------


def _authority_probes():
    """One fixed object per authority-bearing surface, re-serialized on every call."""

    authority = _authority()
    root = _enrollment_root()
    _domain, _enrollment_operation, workspace = root
    claim = _issue_claim(authority, root)
    signer = authority.signing_capability(
        family=KeyFamily.CLAIM_ENROLLMENT,
        generation=1,
        key_id="claim-1",
    )
    ring_metadata = authority.metadata
    binding = _binding(workspace)
    operation = DormantOperationRegistry().register(binding, "b" * 64).operation
    owner = active_state_owner(
        "followup1",
        incarnation_id=IncarnationId.new(),
        authority_epoch=AuthorityEpoch(3),
        head_revision=HeadRevision(4),
        workspace_sequence=WorkspaceSequence(5),
        runtime_home_owner=RuntimeHomeId.new(),
    )
    recovery = enter_recovery(owner.current)
    decision = issue_recovery_decision(
        recovery,
        result=RecoveryDecisionResult.REACTIVATE,
        evidence_digest="d" * 64,
    )
    cursor_payload = AuthenticatedCursorPayload(
        principal_id="principal-1",
        workspace_id=workspace,
        incarnation_id=IncarnationId.new(),
        authority_epoch=AuthorityEpoch(3),
        query_digest="4" * 64,
        direction=CursorDirection.FORWARD,
        page_size=50,
        highwater=WorkspaceSequence(100),
        exclusive_boundary="sequence:50",
        projection_version=3,
        issued_at_unix_ms=100,
        expires_at_unix_ms=200,
        key_generation=1,
    )
    return {
        "claim_bytes": lambda: sealed_authority_record_bytes(claim),
        "claim_digest": lambda: claim.expected_claim_digest(),
        "cursor_payload_bytes": lambda: sealed_authority_record_bytes(cursor_payload),
        "ring_metadata_bytes": lambda: sealed_authority_record_bytes(ring_metadata),
        "operation_id": lambda: canonical_identity_text(
            canonical_binding_operation_id(binding), OperationId
        ),
        "mutation_id": lambda: canonical_identity_text(
            canonical_binding_mutation_id(binding), MutationId
        ),
        "binding_digest": lambda: canonical_operation_binding_digest(binding),
        "binding_payload": lambda: canonical_operation_binding_payload(binding),
        "operation_fingerprint": lambda: _dormant_operation_fingerprint(operation),
        "head_fingerprint": lambda: _head_state_fingerprint(recovery.snapshot),
        "recovery_decision_digest": lambda: sealed_authority_record_digest(decision),
        "key_mac": lambda: signer.sign(
            expected_family=KeyFamily.CLAIM_ENROLLMENT,
            domain="VOOL_FOLLOWUP1_V3",
            message=b"payload",
        ),
    }


DISPLAY_SUBSTITUTIONS = (
    pytest.param(OpaqueIdentity, "__str__", lambda self: FORGED_WORKSPACE, id="OpaqueIdentity.__str__"),
    pytest.param(WorkspaceId, "__str__", lambda self: FORGED_WORKSPACE, id="WorkspaceId.__str__"),
    pytest.param(
        IncarnationId, "__str__", lambda self: f"incarnation_{'9' * 64}", id="IncarnationId.__str__"
    ),
    pytest.param(
        AuthorityDomainId,
        "__str__",
        lambda self: f"authority-domain_{'9' * 64}",
        id="AuthorityDomainId.__str__",
    ),
    pytest.param(
        RecoveryCycleId, "__str__", lambda self: f"recovery-cycle_{'9' * 64}", id="RecoveryCycleId.__str__"
    ),
    pytest.param(
        RecoveryDecisionId,
        "__str__",
        lambda self: f"recovery-decision_{'9' * 64}",
        id="RecoveryDecisionId.__str__",
    ),
    pytest.param(
        RuntimeHomeId, "__str__", lambda self: f"runtime-home_{'9' * 64}", id="RuntimeHomeId.__str__"
    ),
    pytest.param(OperationId, "__str__", lambda self: f"operation_{'9' * 64}", id="OperationId.__str__"),
    pytest.param(MutationId, "__str__", lambda self: f"mutation_{'9' * 64}", id="MutationId.__str__"),
    pytest.param(OpaqueIdentity, "__repr__", lambda self: "<forged>", id="OpaqueIdentity.__repr__"),
    pytest.param(_BoundedCounter, "__int__", lambda self: 4242, id="_BoundedCounter.__int__"),
    pytest.param(AuthorityEpoch, "__int__", lambda self: 4242, id="AuthorityEpoch.__int__"),
    pytest.param(HeadRevision, "__int__", lambda self: 4242, id="HeadRevision.__int__"),
    pytest.param(WorkspaceSequence, "__int__", lambda self: 4242, id="WorkspaceSequence.__int__"),
    pytest.param(
        InvocationIdentity, "digest", lambda self: "0" * 64, id="InvocationIdentity.digest"
    ),
    pytest.param(
        InvocationIdentity,
        "canonical_payload",
        lambda self: {"forged": True},
        id="InvocationIdentity.canonical_payload",
    ),
    pytest.param(
        OperationIdentityBinding,
        "canonical_payload",
        lambda self: {"forged": True},
        id="OperationIdentityBinding.canonical_payload",
    ),
    pytest.param(
        OperationIdentityBinding, "digest", lambda self: "0" * 64, id="OperationIdentityBinding.digest"
    ),
    pytest.param(
        KeyFamily, "value", property(lambda self: "FORGED_FAMILY"), id="KeyFamily.value"
    ),
    pytest.param(
        WorkspaceId, "value", property(lambda self: FORGED_WORKSPACE), id="WorkspaceId.value"
    ),
    pytest.param(AuthorityEpoch, "value", property(lambda self: 4242), id="AuthorityEpoch.value"),
    pytest.param(HeadRevision, "value", property(lambda self: 4242), id="HeadRevision.value"),
)


@pytest.mark.parametrize(("owner", "name", "replacement"), DISPLAY_SUBSTITUTIONS)
def test_followup1_class_substitution_cannot_move_authority_bearing_bytes(
    owner: type,
    name: str,
    replacement: object,
) -> None:
    probes = _authority_probes()
    baseline = {label: probe() for label, probe in probes.items()}
    with substituted(owner, name, replacement):
        for label, probe in probes.items():
            assert probe() == baseline[label], f"{owner.__name__}.{name} moved {label}"
    for label, probe in probes.items():
        assert probe() == baseline[label], f"{owner.__name__}.{name} left {label} changed"


def test_followup1_enum_value_substitution_cannot_move_the_key_mac() -> None:
    authority = _authority()
    signer = authority.signing_capability(
        family=KeyFamily.CLAIM_ENROLLMENT,
        generation=1,
        key_id="claim-1",
    )
    cursor_signer = authority.signing_capability(
        family=KeyFamily.CURSOR,
        generation=1,
        key_id="cursor-1",
    )
    claim_mac = signer.sign(
        expected_family=KeyFamily.CLAIM_ENROLLMENT,
        domain="VOOL_FOLLOWUP1_V3",
        message=b"payload",
    )
    cursor_mac = cursor_signer.sign(
        expected_family=KeyFamily.CURSOR,
        domain="VOOL_FOLLOWUP1_V3",
        message=b"payload",
    )
    assert claim_mac != cursor_mac

    with substituted(KeyFamily, "value", property(lambda self: "FORGED_FAMILY")):
        assert (
            signer.sign(
                expected_family=KeyFamily.CLAIM_ENROLLMENT,
                domain="VOOL_FOLLOWUP1_V3",
                message=b"payload",
            )
            == claim_mac
        )
        assert (
            cursor_signer.sign(
                expected_family=KeyFamily.CURSOR,
                domain="VOOL_FOLLOWUP1_V3",
                message=b"payload",
            )
            == cursor_mac
        )
        assert canonical_enum_value(KeyFamily.CURSOR) == "CURSOR"


# ---------------------------------------------------------------------------
# E. Normal behaviour is unchanged.
# ---------------------------------------------------------------------------


def test_followup1_normal_serialization_derivation_and_round_trips_are_unchanged(tmp_path) -> None:
    root = _enrollment_root()
    authority = _authority(root[0])
    _domain, _enrollment_operation, workspace = root
    claim = _issue_claim(authority, root)
    verifier = authority.verification_capability(
        family=KeyFamily.CLAIM_ENROLLMENT,
        generation=1,
        key_id="claim-1",
    )
    assert claim.verify_mac(verifier)
    assert EnrollmentClaimV3.from_json(claim.canonical_bytes(), verification_key=verifier) == claim
    assert claim.to_record()["payload"]["workspace_id"] == canonical_identity_text(
        workspace, WorkspaceId
    )

    binding = _binding(workspace)
    first = DormantOperationRegistry().register(binding, "b" * 64)
    restarted = DormantOperationRegistry().register(binding, "b" * 64)
    assert first.operation.operation_id == restarted.operation.operation_id
    assert first.operation.mutation_id == restarted.operation.mutation_id
    assert first.operation.operation_id == canonical_binding_operation_id(binding)

    database = DormantAuthorityDatabase(tmp_path / "normal.sqlite3")
    assert database.register_operation(first.operation).outcome is TransactionOutcome.COMMITTED_NEW
    reopened = DormantAuthorityDatabase(tmp_path / "normal.sqlite3")
    assert (
        reopened.register_operation(restarted.operation).outcome
        is TransactionOutcome.COMMITTED_REPLAY
    )

    payload = AuthenticatedCursorPayload(
        principal_id="principal-1",
        workspace_id=workspace,
        incarnation_id=IncarnationId.new(),
        authority_epoch=AuthorityEpoch(3),
        query_digest="4" * 64,
        direction=CursorDirection.FORWARD,
        page_size=50,
        highwater=WorkspaceSequence(100),
        exclusive_boundary="sequence:50",
        projection_version=3,
        issued_at_unix_ms=100,
        expires_at_unix_ms=200,
        key_generation=1,
    )
    token = CursorCodec(
        key=authority.signing_capability(family=KeyFamily.CURSOR, generation=1, key_id="cursor-1")
    ).encode(payload)
    expectation = CursorExpectation(
        principal_id=payload.principal_id,
        workspace_id=payload.workspace_id,
        incarnation_id=payload.incarnation_id,
        authority_epoch=payload.authority_epoch,
        query_digest=payload.query_digest,
        direction=payload.direction,
        page_size=payload.page_size,
        highwater=payload.highwater,
        projection_version=payload.projection_version,
    )
    decoded = CursorCodec(
        key=authority.verification_capability(
            family=KeyFamily.CURSOR,
            generation=1,
            key_id="cursor-1",
        )
    ).decode(token, expectation=expectation, now_unix_ms=150)
    assert decoded == payload


def test_followup1_canonical_helpers_agree_with_display_when_nothing_is_substituted() -> None:
    _domain, _enrollment_operation, workspace = _enrollment_root()
    epoch = AuthorityEpoch(7)
    invocation = _invocation()
    assert canonical_identity_text(workspace, WorkspaceId) == str(workspace)
    assert canonical_counter_value(epoch) == int(epoch)
    assert canonical_invocation_digest(invocation) == invocation.digest()
    assert canonical_operation_binding_payload(_binding(workspace)) == _binding(
        workspace
    ).canonical_payload()


# ---------------------------------------------------------------------------
# Hostile inputs to the canonical boundary itself.
# ---------------------------------------------------------------------------


def test_followup1_canonical_helpers_reject_unowned_foreign_and_mutated_inputs() -> None:
    _domain, _enrollment_operation, workspace = _enrollment_root()

    forged = object.__new__(WorkspaceId)
    object.__setattr__(forged, "value", canonical_identity_text(workspace, WorkspaceId))
    with pytest.raises(TypeError, match="not issued"):
        canonical_identity_text(forged, WorkspaceId)
    with pytest.raises(TypeError, match="exact owned"):
        canonical_identity_text(workspace, IncarnationId)

    mutated = IncarnationId.new()
    object.__setattr__(mutated, "value", f"incarnation_{'e' * 64}")
    with pytest.raises(TypeError, match="changed after identity issuance"):
        canonical_identity_text(mutated, IncarnationId)

    for hostile in ("7", 7, None, workspace):
        with pytest.raises(TypeError):
            canonical_counter_value(hostile)
    with pytest.raises(TypeError):
        canonical_invocation_fields(object())
    with pytest.raises(TypeError):
        canonical_operation_binding_digest(_invocation())
    with pytest.raises(TypeError, match="already sealed"):
        _seal_canonical_slots(((OpaqueIdentity, "value"),))


def test_followup1_counter_value_substitution_cannot_forge_a_head_fingerprint() -> None:
    owner = active_state_owner(
        "followup1-head",
        incarnation_id=IncarnationId.new(),
        authority_epoch=AuthorityEpoch(3),
        head_revision=HeadRevision(4),
        workspace_sequence=WorkspaceSequence(5),
        runtime_home_owner=RuntimeHomeId.new(),
    )
    snapshot = owner.current.snapshot
    baseline = _head_state_fingerprint(snapshot)
    drifted = _head_state_fingerprint(replace(snapshot, head_revision=HeadRevision(9)))
    assert drifted != baseline

    with substituted(HeadRevision, "__int__", lambda self: 9):
        assert _head_state_fingerprint(snapshot) == baseline
    with substituted(_BoundedCounter, "__int__", lambda self: 9):
        assert _head_state_fingerprint(snapshot) == baseline
