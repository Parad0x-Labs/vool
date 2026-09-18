from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from threading import Barrier
from typing import ClassVar

import pytest

import core.workspace_authority_v3.lifecycle as lifecycle_module
from core.workspace_authority_v3.base import (
    AuthorityPhase,
    AuthorityRecord,
    ContractValidationError,
    sealed_authority_record,
)
from core.workspace_authority_v3.canonical import canonical_bytes
from core.workspace_authority_v3.contracts import (
    JOURNAL_EFFECT_RECEIPT_PHASE0_STATUS,
    AuthorityBudgetProfile,
    BackupManifestV3,
    BackupPredecessor,
    DormantKeyAuthority,
    EnrollmentClaimV3,
    KeyFamily,
    KeyFamilyPhase0Status,
    KeyGenerationMetadata,
    KeyReference,
    KeyRingMetadata,
    KeyState,
    RequiredKeyReference,
    SigningKeyCapability,
    VerificationKeyCapability,
    key_family_phase0_status,
    require_signing_key_capability,
    require_verification_key_capability,
    sign_with_key_capability,
    verify_with_key_capability,
)
from core.workspace_authority_v3.cursor import (
    AuthenticatedCursorPayload,
    CursorCodec,
    CursorDirection,
    CursorError,
    CursorExpectation,
)
from core.workspace_authority_v3.database import (
    AuthorityCommitError,
    DormantAuthorityDatabase,
    RecoveryCycleStorageStatus,
    TransactionOutcome,
    TransactionResult,
)
from core.workspace_authority_v3.identity import (
    AuthenticatedIdentityDecode,
    AuthorityClaimProvenance,
    AuthorityDomainId,
    AuthorityEpoch,
    BackupGenerationId,
    EnrollmentOperationId,
    HeadRevision,
    IncarnationId,
    InvocationIdentity,
    MutationId,
    OperationIdentityBinding,
    RecoveryCycleId,
    RecoveryDecisionId,
    RuntimeHomeId,
    WorkspaceEnrollmentProvenance,
    WorkspaceId,
    WorkspaceSequence,
    _issue_authenticated_identity_decode,
    require_identity_context,
    require_owned_identity,
)
from core.workspace_authority_v3.lifecycle import (
    CasConflictError,
    DormantOperation,
    DormantOperationRegistry,
    DormantWorkspaceStateOwner,
    HeadExpectation,
    IdempotencyConflictError,
    OwnedHeadState,
    PersistedRecoveryDecision,
    RecoveryCycleBinding,
    RecoveryDecisionResult,
    WorkspaceLifecycle,
    advance_head,
    apply_persisted_recovery_decision,
    claim_pending,
    enter_recovery,
    issue_recovery_decision,
)
from tests.workspace_authority_v3_fixtures import (
    active_state_owner,
    owned_identity,
    ring_workspace_id,
)

KEY = b"z" * 32


def _workspace_root() -> tuple[AuthorityDomainId, EnrollmentOperationId, WorkspaceId]:
    domain = AuthorityDomainId.from_authority_claim(AuthorityClaimProvenance.establish())
    enrollment_operation = EnrollmentOperationId.new()
    enrollment = WorkspaceEnrollmentProvenance.establish(
        authority_domain_id=domain,
        enrollment_operation_id=enrollment_operation,
    )
    return domain, enrollment_operation, WorkspaceId.from_enrollment(enrollment)


def _binding(*, action: str = "a", workspace_seed: str = "repair2a") -> OperationIdentityBinding:
    return OperationIdentityBinding(
        invocation_identity=InvocationIdentity(f"turn_{'1' * 64}", 2, 3),
        workspace_id=owned_identity(WorkspaceId, workspace_seed),
        authority_epoch=AuthorityEpoch(4),
        canonical_action_digest=action * 64,
    )


def _active_owner(seed: str = "repair2a-head") -> DormantWorkspaceStateOwner:
    return active_state_owner(
        seed,
        incarnation_id=owned_identity(IncarnationId, f"{seed}:incarnation"),
        authority_epoch=AuthorityEpoch(3),
        head_revision=HeadRevision(4),
        workspace_sequence=WorkspaceSequence(5),
        runtime_home_owner=owned_identity(RuntimeHomeId, f"{seed}:runtime"),
    )


def _key_metadata(
    family: KeyFamily,
    key_id: str,
    generation: int,
    state: KeyState,
) -> KeyGenerationMetadata:
    return KeyGenerationMetadata(family, key_id, generation, state, "HMAC-SHA256", 1)


def test_repair2a_authority_records_have_a_nonvirtual_final_dormant_envelope() -> None:
    record = KeyGenerationMetadata(
        KeyFamily.CLAIM_ENROLLMENT,
        "claim-1",
        1,
        KeyState.ACTIVE,
        "HMAC-SHA256",
        1,
    )
    assert not hasattr(record, "__dict__")
    with pytest.raises(AttributeError):
        object.__setattr__(record, "to_record", lambda: {"execution_authority": True})

    raw = AuthorityRecord.to_record(record)
    assert raw["authority_metadata"] == {
        "execution_authority": False,
        "phase": AuthorityPhase.DORMANT_PHASE0.value,
    }
    assert "execution_authority" not in raw["payload"]
    assert "phase" not in raw["payload"]

    with pytest.raises(TypeError, match="final"):

        class HostileSubtype(KeyGenerationMetadata):
            def __post_init__(self) -> None:
                pass

    class SerializerMixin:
        def to_record(self) -> dict[str, object]:
            return {"execution_authority": True}

    with pytest.raises(TypeError, match="multiple inheritance"):

        @dataclass(frozen=True, slots=True)
        class HostileMultipleInheritance(AuthorityRecord, SerializerMixin):
            RECORD_TYPE: ClassVar[str] = "HostileMultipleInheritance"
            value: str

            def __post_init__(self) -> None:
                pass

    original_serializer = AuthorityRecord.to_record
    type.__setattr__(
        AuthorityRecord,
        "to_record",
        lambda _self: {"authority_metadata": {"execution_authority": True}},
    )
    try:
        assert sealed_authority_record(record)["authority_metadata"]["execution_authority"] is False
    finally:
        type.__setattr__(AuthorityRecord, "to_record", original_serializer)

    type.__setattr__(
        KeyGenerationMetadata,
        "to_record",
        lambda _self: {"authority_metadata": {"execution_authority": True}},
    )
    try:
        with pytest.raises(TypeError, match="serializer boundary changed"):
            sealed_authority_record(record)
    finally:
        type.__delattr__(KeyGenerationMetadata, "to_record")

    bypassed = object.__new__(KeyGenerationMetadata)
    object.__setattr__(bypassed, "family", KeyFamily.CLAIM_ENROLLMENT)
    object.__setattr__(bypassed, "key_id", "claim-bypassed")
    object.__setattr__(bypassed, "generation", 1)
    object.__setattr__(bypassed, "state", KeyState.ACTIVE)
    object.__setattr__(bypassed, "algorithm", "HMAC-SHA256")
    object.__setattr__(bypassed, "algorithm_version", 1)
    with pytest.raises(TypeError, match="not issued"):
        sealed_authority_record(bypassed)

    object.__setattr__(record, "key_id", "claim-mutated")
    with pytest.raises(ContractValidationError, match="changed after sealed construction"):
        sealed_authority_record(record)


def test_repair2a_unknown_authority_record_schema_is_not_accepted() -> None:
    @dataclass(frozen=True, slots=True)
    class UnknownAuthorityRecord(AuthorityRecord):
        RECORD_TYPE: ClassVar[str] = "UnknownAuthorityRecord"
        value: str

        def __post_init__(self) -> None:
            if self.value != "valid":
                raise ValueError("invalid")

    with pytest.raises(TypeError, match="not a supported Phase-0 schema"):
        sealed_authority_record(UnknownAuthorityRecord("valid"))


def test_repair2a_root_identity_provenance_is_exact_owned_and_output_bound() -> None:
    with pytest.raises(TypeError, match="final"):

        class FakeClaimProvenance(AuthorityClaimProvenance):
            pass

    forged = object.__new__(AuthorityClaimProvenance)
    with pytest.raises(TypeError, match="owner-issued"):
        AuthorityDomainId.from_authority_claim(forged)

    claim = AuthorityClaimProvenance.establish()
    first_domain = AuthorityDomainId.from_authority_claim(claim)
    second_domain = AuthorityDomainId.from_authority_claim(claim)
    assert first_domain == second_domain

    enrollment_operation = EnrollmentOperationId.new()
    enrollment = WorkspaceEnrollmentProvenance.establish(
        authority_domain_id=first_domain,
        enrollment_operation_id=enrollment_operation,
    )
    first_workspace = WorkspaceId.from_enrollment(enrollment)
    second_workspace = WorkspaceId.from_enrollment(enrollment)
    assert first_workspace == second_workspace
    require_identity_context(
        first_workspace,
        WorkspaceId,
        authority_domain_id=first_domain,
        enrollment_operation_id=enrollment_operation,
    )

    with pytest.raises(TypeError, match="closed identity owner"):
        WorkspaceId(f"workspace_{'1' * 64}")
    forged_workspace = object.__new__(WorkspaceId)
    object.__setattr__(forged_workspace, "value", str(first_workspace))
    with pytest.raises(TypeError, match="not issued"):
        require_owned_identity(forged_workspace, WorkspaceId)

    mutable_identity = IncarnationId.new()
    object.__setattr__(mutable_identity, "value", f"incarnation_{'f' * 64}")
    with pytest.raises(TypeError, match="changed after identity issuance"):
        require_owned_identity(mutable_identity, IncarnationId)

    mutated_claim = AuthorityClaimProvenance.establish()
    object.__setattr__(mutated_claim, "_authority_domain_value", f"authority-domain_{'f' * 64}")
    with pytest.raises(TypeError, match="owner-issued"):
        AuthorityDomainId.from_authority_claim(mutated_claim)


def test_repair2a_authenticated_decode_is_one_use_exact_type_and_context_bound() -> None:
    domain, enrollment_operation, workspace = _workspace_root()
    incarnation = IncarnationId.new()
    runtime_home = RuntimeHomeId.new()
    metadata = KeyRingMetadata(
        (_key_metadata(KeyFamily.CLAIM_ENROLLMENT, "claim-decode", 1, KeyState.ACTIVE),)
    )
    authority = DormantKeyAuthority(
        metadata,
        {KeyReference(KeyFamily.CLAIM_ENROLLMENT, "claim-decode", 1): KEY},
        workspace_id=ring_workspace_id(),
        authority_domain=domain,
    )
    signer = authority.signing_capability(
        family=KeyFamily.CLAIM_ENROLLMENT,
        generation=1,
        key_id="claim-decode",
    )
    verifier = authority.verification_capability(
        family=KeyFamily.CLAIM_ENROLLMENT,
        generation=1,
        key_id="claim-decode",
    )
    claim = EnrollmentClaimV3.issue(
        authority_domain_id=domain,
        workspace_id=workspace,
        incarnation_id=incarnation,
        enrollment_operation_id=enrollment_operation,
        incarnation_nonce="1" * 64,
        persistent_volume_identity_digest="2" * 64,
        root_object_identity_digest="3" * 64,
        runtime_home_id=runtime_home,
        signing_key=signer,
    )
    source_record = canonical_bytes(claim.unsigned_record())
    proof = _issue_authenticated_identity_decode(
        verification_key=verifier,
        source_record=source_record,
        supplied_mac=claim.claim_mac,
        record_type="EnrollmentClaimV3",
        source_field="workspace_id",
        identity_type=WorkspaceId,
    )
    assert proof.workspace_id == str(workspace)
    assert proof.authority_domain_id == str(domain)

    with pytest.raises(TypeError, match="another identity type"):
        IncarnationId._from_trusted_storage(proof)
    with pytest.raises(TypeError, match="already consumed"):
        WorkspaceId._from_trusted_storage(proof)

    with pytest.raises(TypeError, match="minted"):
        AuthenticatedIdentityDecode()
    with pytest.raises(TypeError, match="final"):

        class FakeDecode(AuthenticatedIdentityDecode):
            pass

    forged = object.__new__(AuthenticatedIdentityDecode)
    with pytest.raises(TypeError, match="unissued"):
        WorkspaceId._from_trusted_storage(forged)

    mutated = _issue_authenticated_identity_decode(
        verification_key=verifier,
        source_record=source_record,
        supplied_mac=claim.claim_mac,
        record_type="EnrollmentClaimV3",
        source_field="workspace_id",
        identity_type=WorkspaceId,
    )
    object.__setattr__(mutated, "record_digest", "e" * 64)
    with pytest.raises(TypeError, match="changed after issuance"):
        WorkspaceId._from_trusted_storage(mutated)


def test_repair2a_identity_types_are_mechanically_disjoint() -> None:
    with pytest.raises(TypeError, match="final"):

        class WorkspaceSubtype(WorkspaceId):
            pass

    with pytest.raises(TypeError, match="multiple inheritance"):

        class HybridIdentity(WorkspaceId, IncarnationId):
            pass


def test_repair2a_mutation_identity_is_stable_across_registry_and_database_restart(tmp_path) -> None:
    binding = _binding()
    first = DormantOperationRegistry().register(binding, "b" * 64).operation
    restarted = DormantOperationRegistry().register(binding, "b" * 64).operation
    assert restarted.operation_id == first.operation_id
    assert restarted.mutation_id == first.mutation_id == binding.mutation_id()

    database_path = tmp_path / "authority.sqlite3"
    first_database = DormantAuthorityDatabase(database_path)
    assert first_database.register_operation(first).outcome is TransactionOutcome.COMMITTED_NEW
    reopened_database = DormantAuthorityDatabase(database_path)
    replay = reopened_database.register_operation(restarted)
    assert replay.outcome is TransactionOutcome.COMMITTED_REPLAY
    assert replay.value is not None and replay.value.mutation_id == first.mutation_id

    with pytest.raises(ValueError, match="stable invocation owner"):
        DormantOperation(
            binding.operation_id(),
            MutationId.new(),
            binding,
            "b" * 64,
        )


def test_repair2a_in_memory_registry_is_linearizable_for_replay_and_conflict() -> None:
    registry = DormantOperationRegistry()
    binding = _binding(workspace_seed="linearizable-identical")
    barrier = Barrier(16)

    def register_identical() -> object:
        barrier.wait()
        return registry.register(binding, "c" * 64)

    with ThreadPoolExecutor(max_workers=16) as executor:
        registrations = list(executor.map(lambda _index: register_identical(), range(16)))
    assert sum(not item.replayed for item in registrations) == 1
    assert sum(item.replayed for item in registrations) == 15
    assert len({item.operation.mutation_id for item in registrations}) == 1
    assert len({id(item.operation) for item in registrations}) == 1

    conflict_registry = DormantOperationRegistry()
    first_binding = _binding(action="d", workspace_seed="linearizable-conflict")
    second_binding = replace(first_binding, canonical_action_digest="e" * 64)
    conflict_barrier = Barrier(2)

    def register_conflicting(candidate: OperationIdentityBinding) -> str:
        conflict_barrier.wait()
        try:
            conflict_registry.register(candidate, "f" * 64)
        except IdempotencyConflictError:
            return "CONFLICT"
        return "NEW"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(register_conflicting, (first_binding, second_binding)))
    assert sorted(outcomes) == ["CONFLICT", "NEW"]


def test_repair2a_sqlite_concurrency_and_retry_use_the_same_stable_identity(tmp_path, monkeypatch) -> None:
    binding = _binding(workspace_seed="sqlite-linearizable")
    operation = DormantOperationRegistry().register(binding, "9" * 64).operation
    database_path = tmp_path / "authority.sqlite3"
    first_database = DormantAuthorityDatabase(database_path)
    second_database = DormantAuthorityDatabase(database_path)
    barrier = Barrier(2)

    def persist(database: DormantAuthorityDatabase) -> TransactionOutcome:
        barrier.wait()
        return database.register_operation(operation).outcome

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(persist, (first_database, second_database)))
    assert sorted(outcome.value for outcome in outcomes) == [
        TransactionOutcome.COMMITTED_NEW.value,
        TransactionOutcome.COMMITTED_REPLAY.value,
    ]

    retry_database = DormantAuthorityDatabase(tmp_path / "retry.sqlite3")
    monkeypatch.setattr(
        retry_database,
        "run_transaction",
        lambda _callback: TransactionResult(TransactionOutcome.NOT_COMMITTED_RETRYABLE),
    )
    assert retry_database.register_operation(operation).outcome is TransactionOutcome.NOT_COMMITTED_RETRYABLE
    after_retry = DormantOperationRegistry().register(binding, "9" * 64).operation
    assert after_retry.mutation_id == operation.mutation_id


def test_repair2a_public_head_snapshot_cannot_substitute_for_transition_authority() -> None:
    owner = _active_owner("public-head")
    snapshot = owner.current.snapshot
    expectation = HeadExpectation(snapshot.authority_epoch, snapshot.head_revision, snapshot.runtime_home_owner)
    operation_id = _binding(workspace_seed="public-head-operation").operation_id()
    mutation_id = _binding(workspace_seed="public-head-operation").mutation_id()

    with pytest.raises(TypeError, match="owner-issued OwnedHeadState"):
        claim_pending(snapshot, expectation, operation_id, mutation_id)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="issued from enrollment provenance"):
        DormantWorkspaceStateOwner(snapshot)

    claimed = claim_pending(owner.current, expectation, operation_id, mutation_id)
    assert claimed.pending_operation_id == operation_id
    assert claimed.pending_mutation_id == mutation_id

    tamper_owner = _active_owner("public-head-tamper")
    tampered = tamper_owner.current
    object.__setattr__(
        tampered,
        "_snapshot",
        replace(tampered.snapshot, head_revision=HeadRevision(99)),
    )
    with pytest.raises(CasConflictError, match="not the current value"):
        claim_pending(tampered, expectation, operation_id, mutation_id)


def test_repair2a_authoritative_head_state_is_not_stored_on_caller_writable_owner_slots() -> None:
    owner = _active_owner("closed-head-owner")
    initial = owner.current
    recovery = enter_recovery(initial)
    forged_snapshot = replace(
        recovery.snapshot,
        lifecycle=WorkspaceLifecycle.ACTIVE,
        recovery_cycle=None,
    )
    forged = object.__new__(OwnedHeadState)
    object.__setattr__(forged, "_owner", owner)
    object.__setattr__(forged, "_snapshot", forged_snapshot)
    object.__setattr__(
        forged,
        "_fingerprint",
        lifecycle_module._head_state_fingerprint(forged_snapshot),
    )

    replacement_attempts = (
        ("_current", forged),
        ("_current_snapshot", forged_snapshot),
        ("_current_fingerprint", forged._fingerprint),
    )
    for attribute, value in replacement_attempts:
        with pytest.raises(AttributeError):
            setattr(owner, attribute, value)
        with pytest.raises(AttributeError):
            object.__setattr__(owner, attribute, value)

    expectation = HeadExpectation(
        forged.authority_epoch,
        forged.head_revision,
        forged.runtime_home_owner,
    )
    binding = _binding(workspace_seed="closed-head-owner-operation")
    with pytest.raises(CasConflictError, match="not the current value"):
        claim_pending(forged, expectation, binding.operation_id(), binding.mutation_id())
    assert owner.current is recovery
    assert owner.current.lifecycle is WorkspaceLifecycle.RECOVERY_REQUIRED


def test_repair2a_owned_head_state_cannot_transfer_between_owners_or_restore_stale_authority() -> None:
    first = _active_owner("closed-head-first")
    second = _active_owner("closed-head-second")
    first_initial = first.current
    second_initial = second.current
    first_binding = _binding(workspace_seed="closed-head-first-operation")
    first_expectation = HeadExpectation(
        first_initial.authority_epoch,
        first_initial.head_revision,
        first_initial.runtime_home_owner,
    )
    claimed = claim_pending(
        first_initial,
        first_expectation,
        first_binding.operation_id(),
        first_binding.mutation_id(),
    )

    second_binding = _binding(workspace_seed="closed-head-second-operation")
    second_expectation = HeadExpectation(
        second_initial.authority_epoch,
        second_initial.head_revision,
        second_initial.runtime_home_owner,
    )
    with pytest.raises(CasConflictError, match="not the current value"):
        second.claim_pending(
            first_initial,
            second_expectation,
            second_binding.operation_id(),
            second_binding.mutation_id(),
        )

    transferred = object.__new__(OwnedHeadState)
    object.__setattr__(transferred, "_owner", second)
    object.__setattr__(transferred, "_snapshot", first_initial.snapshot)
    object.__setattr__(transferred, "_fingerprint", first_initial._fingerprint)
    with pytest.raises(CasConflictError, match="not the current value"):
        second.claim_pending(
            transferred,
            second_expectation,
            second_binding.operation_id(),
            second_binding.mutation_id(),
        )

    rollback = object.__new__(OwnedHeadState)
    object.__setattr__(rollback, "_owner", first)
    object.__setattr__(rollback, "_snapshot", first_initial.snapshot)
    object.__setattr__(rollback, "_fingerprint", first_initial._fingerprint)
    with pytest.raises(CasConflictError, match="not the current value"):
        first.claim_pending(
            rollback,
            first_expectation,
            first_binding.operation_id(),
            first_binding.mutation_id(),
        )
    assert first.current is claimed
    assert second.current is second_initial


def test_repair2a_closed_head_owner_preserves_normal_transitions_and_exact_persistence(tmp_path) -> None:
    owner = _active_owner("closed-head-normal")
    initial = owner.current
    binding = _binding(workspace_seed="closed-head-normal-operation")
    expectation = HeadExpectation(
        initial.authority_epoch,
        initial.head_revision,
        initial.runtime_home_owner,
    )
    claimed = claim_pending(
        initial,
        expectation,
        binding.operation_id(),
        binding.mutation_id(),
    )
    advanced = advance_head(
        claimed,
        operation_id=binding.operation_id(),
        mutation_id=binding.mutation_id(),
        new_head_revision=HeadRevision(int(claimed.head_revision) + 1),
        new_workspace_sequence=WorkspaceSequence(int(claimed.workspace_sequence) + 1),
    )
    recovery = enter_recovery(advanced)
    decision = issue_recovery_decision(
        recovery,
        result=RecoveryDecisionResult.REACTIVATE,
        evidence_digest="b" * 64,
    )
    database = DormantAuthorityDatabase(tmp_path / "closed-head.sqlite3")
    assert database.register_recovery_cycle(recovery).outcome is TransactionOutcome.COMMITTED_NEW

    with pytest.raises(CasConflictError, match="atomically consumed"):
        apply_persisted_recovery_decision(recovery, database, decision)
    assert owner.current is recovery

    assert database.persist_recovery_decision(recovery, decision).outcome is TransactionOutcome.COMMITTED_NEW
    active = apply_persisted_recovery_decision(recovery, database, decision)
    assert owner.current is active
    assert active.lifecycle is WorkspaceLifecycle.ACTIVE
    assert active.head_revision == HeadRevision(int(initial.head_revision) + 1)
    assert active.workspace_sequence == WorkspaceSequence(int(initial.workspace_sequence) + 1)


def test_repair2a_workspace_state_owner_is_unique_per_enrollment_provenance() -> None:
    domain = AuthorityDomainId.from_authority_claim(AuthorityClaimProvenance.establish())
    enrollment = WorkspaceEnrollmentProvenance.establish(
        authority_domain_id=domain,
        enrollment_operation_id=EnrollmentOperationId.new(),
    )
    kwargs = {
        "enrollment": enrollment,
        "incarnation_id": IncarnationId.new(),
        "authority_epoch": AuthorityEpoch(1),
        "head_revision": HeadRevision(0),
        "workspace_sequence": WorkspaceSequence(0),
        "runtime_home_owner": RuntimeHomeId.new(),
    }
    first = DormantWorkspaceStateOwner.from_enrollment(**kwargs)
    assert first.current.lifecycle is WorkspaceLifecycle.ACTIVE
    with pytest.raises(CasConflictError, match="already has"):
        DormantWorkspaceStateOwner.from_enrollment(**kwargs)

    with pytest.raises(ContractValidationError, match="cannot transition to recovery"):
        RecoveryCycleBinding(
            recovery_cycle_id=RecoveryCycleId.new(),
            generation=42,
            expected_recovery_decision_id=RecoveryDecisionId.new(),
            workspace_id=first.current.workspace_id,
            incarnation_id=first.current.incarnation_id,
            authority_epoch=first.current.authority_epoch,
            expected_head_revision=first.current.head_revision,
            entered_from=WorkspaceLifecycle.UNENROLLED,
        )


def test_repair2a_recovery_decision_is_exact_cycle_bound_durable_and_one_use(tmp_path) -> None:
    owner = _active_owner("recovery-cycle")
    recovery = enter_recovery(owner.current)
    assert recovery.recovery_cycle is not None
    first_cycle = recovery.recovery_cycle
    database = DormantAuthorityDatabase(tmp_path / "authority.sqlite3")
    assert database.register_recovery_cycle(recovery).outcome is TransactionOutcome.COMMITTED_NEW

    decision = issue_recovery_decision(
        recovery,
        result=RecoveryDecisionResult.REACTIVATE,
        evidence_digest="d" * 64,
    )
    wrong_decisions = (
        replace(decision),
        replace(decision, recovery_decision_id=RecoveryDecisionId.new()),
        replace(decision, recovery_cycle_id=RecoveryCycleId.new()),
        replace(decision, recovery_generation=decision.recovery_generation + 1),
        replace(decision, authority_epoch=AuthorityEpoch(99)),
        replace(decision, expected_head_revision=HeadRevision(99)),
        replace(decision, prior_state=WorkspaceLifecycle.REBIND_REQUIRED),
    )
    for wrong in wrong_decisions:
        with pytest.raises(CasConflictError):
            database.persist_recovery_decision(recovery, wrong)

    persisted = database.persist_recovery_decision(recovery, decision)
    assert persisted.outcome is TransactionOutcome.COMMITTED_NEW and persisted.value is not None
    active = apply_persisted_recovery_decision(recovery, database, decision)
    assert active.lifecycle is WorkspaceLifecycle.ACTIVE
    with pytest.raises(CasConflictError):
        apply_persisted_recovery_decision(active, database, decision)

    reopened = DormantAuthorityDatabase(database.path)
    assert (
        reopened.recovery_cycle_status(first_cycle.recovery_cycle_id)
        is RecoveryCycleStorageStatus.CONSUMED
    )
    with pytest.raises(CasConflictError):
        reopened.persist_recovery_decision(recovery, decision)

    later_recovery = enter_recovery(active)
    assert later_recovery.recovery_cycle is not None
    assert later_recovery.recovery_cycle.generation == first_cycle.generation + 1
    assert later_recovery.recovery_cycle.recovery_cycle_id != first_cycle.recovery_cycle_id
    assert (
        later_recovery.recovery_cycle.expected_recovery_decision_id
        != first_cycle.expected_recovery_decision_id
    )
    assert reopened.register_recovery_cycle(later_recovery).outcome is TransactionOutcome.COMMITTED_NEW
    with pytest.raises(CasConflictError, match="exact open recovery cycle"):
        apply_persisted_recovery_decision(later_recovery, reopened, decision)


def test_repair2a_recovery_persistence_rejects_lookalikes_and_consumes_once_concurrently(tmp_path) -> None:
    owner = _active_owner("recovery-concurrency")
    recovery = enter_recovery(owner.current)
    decision = issue_recovery_decision(
        recovery,
        result=RecoveryDecisionResult.REACTIVATE,
        evidence_digest="e" * 64,
    )
    database = DormantAuthorityDatabase(tmp_path / "authority.sqlite3")
    assert database.register_recovery_cycle(recovery).committed
    persisted = database.persist_recovery_decision(recovery, decision)
    assert persisted.value is not None

    passive_receipt = PersistedRecoveryDecision(decision, decision.digest())
    with pytest.raises(TypeError, match="exact authority database owner"):
        apply_persisted_recovery_decision(recovery, passive_receipt, decision)

    mutated_receipt_result = database.persist_recovery_decision(recovery, decision)
    assert mutated_receipt_result.value is not None
    mutated_receipt = mutated_receipt_result.value
    object.__setattr__(mutated_receipt, "record_digest", "f" * 64)
    assert mutated_receipt.record_digest == "f" * 64

    barrier = Barrier(2)

    def apply_once() -> str:
        barrier.wait()
        try:
            apply_persisted_recovery_decision(recovery, database, decision)
        except CasConflictError:
            return "CONFLICT"
        return "APPLIED"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _index: apply_once(), range(2)))
    assert sorted(outcomes) == ["APPLIED", "CONFLICT"]


def test_repair2a_recovery_commit_unknown_is_idempotently_reconciled(
    tmp_path,
    monkeypatch,
) -> None:
    owner = _active_owner("recovery-unknown")
    recovery = enter_recovery(owner.current)
    decision = issue_recovery_decision(
        recovery,
        result=RecoveryDecisionResult.REACTIVATE,
        evidence_digest="a" * 64,
    )
    database = DormantAuthorityDatabase(tmp_path / "authority.sqlite3")
    assert database.register_recovery_cycle(recovery).committed
    assert database.persist_recovery_decision(recovery, decision).committed
    original_commit = database._commit_or_raise_unknown

    def commit_then_report_unknown(connection) -> None:
        connection.commit()
        raise AuthorityCommitError(
            "injected durable commit with unknown acknowledgement",
            TransactionResult(TransactionOutcome.OUTCOME_UNKNOWN),
        )

    monkeypatch.setattr(database, "_commit_or_raise_unknown", commit_then_report_unknown)
    with pytest.raises(AuthorityCommitError):
        apply_persisted_recovery_decision(recovery, database, decision)
    assert owner.current is recovery
    assert recovery.recovery_cycle is not None
    assert (
        database.recovery_cycle_status(recovery.recovery_cycle.recovery_cycle_id)
        is RecoveryCycleStorageStatus.CONSUMED
    )

    monkeypatch.setattr(database, "_commit_or_raise_unknown", original_commit)
    active = apply_persisted_recovery_decision(recovery, database, decision)
    assert active.lifecycle is WorkspaceLifecycle.ACTIVE


def test_repair2a_cached_key_leases_follow_current_rotation_and_revocation_state() -> None:
    generation_one = _key_metadata(KeyFamily.CLAIM_ENROLLMENT, "claim-1", 1, KeyState.ACTIVE)
    authority = DormantKeyAuthority(
        KeyRingMetadata((generation_one,)),
        {KeyReference(KeyFamily.CLAIM_ENROLLMENT, "claim-1", 1): KEY},
        workspace_id=ring_workspace_id(),
    )
    signer = authority.signing_capability(
        family=KeyFamily.CLAIM_ENROLLMENT,
        generation=1,
        key_id="claim-1",
    )
    verifier = authority.verification_capability(
        family=KeyFamily.CLAIM_ENROLLMENT,
        generation=1,
        key_id="claim-1",
    )
    message = b"historical enrollment claim"
    mac = signer.sign(
        expected_family=KeyFamily.CLAIM_ENROLLMENT,
        domain="VOOL_TEST_CLAIM_V3",
        message=message,
    )
    assert verifier.verify(
        expected_family=KeyFamily.CLAIM_ENROLLMENT,
        domain="VOOL_TEST_CLAIM_V3",
        message=message,
        supplied_mac=mac,
    )

    generation_two = _key_metadata(KeyFamily.CLAIM_ENROLLMENT, "claim-2", 2, KeyState.ACTIVE)
    authority.install_metadata(
        KeyRingMetadata((replace(generation_one, state=KeyState.VERIFY_ONLY), generation_two)),
        key_materials={KeyReference(KeyFamily.CLAIM_ENROLLMENT, "claim-2", 2): b"y" * 32},
    )
    with pytest.raises(ContractValidationError, match="not current"):
        signer.sign(
            expected_family=KeyFamily.CLAIM_ENROLLMENT,
            domain="VOOL_TEST_CLAIM_V3",
            message=message,
        )
    assert verifier.state is KeyState.VERIFY_ONLY
    assert verifier.verify(
        expected_family=KeyFamily.CLAIM_ENROLLMENT,
        domain="VOOL_TEST_CLAIM_V3",
        message=message,
        supplied_mac=mac,
    )

    authority.install_metadata(
        KeyRingMetadata(
            (
                replace(generation_one, state=KeyState.REVOKED_COMPROMISED),
                generation_two,
            )
        )
    )
    with pytest.raises(ContractValidationError, match="not current"):
        verifier.verify(
            expected_family=KeyFamily.CLAIM_ENROLLMENT,
            domain="VOOL_TEST_CLAIM_V3",
            message=message,
            supplied_mac=mac,
        )


@pytest.mark.parametrize(
    "terminal_state",
    (KeyState.RETIRED, KeyState.REVOKED_COMPROMISED, KeyState.LOST),
)
def test_repair2a_nonactive_key_states_invalidate_cached_signing_and_verification(
    terminal_state: KeyState,
) -> None:
    active = _key_metadata(KeyFamily.BACKUP_MANIFEST, "backup-1", 1, KeyState.ACTIVE)
    authority = DormantKeyAuthority(
        KeyRingMetadata((active,)),
        {KeyReference(KeyFamily.BACKUP_MANIFEST, "backup-1", 1): KEY},
        workspace_id=ring_workspace_id(),
    )
    signer = authority.signing_capability(
        family=KeyFamily.BACKUP_MANIFEST,
        generation=1,
        key_id="backup-1",
    )
    verifier = authority.verification_capability(
        family=KeyFamily.BACKUP_MANIFEST,
        generation=1,
        key_id="backup-1",
    )
    authority.install_metadata(KeyRingMetadata((replace(active, state=terminal_state),)))
    with pytest.raises(ContractValidationError, match="not current"):
        signer.sign(
            expected_family=KeyFamily.BACKUP_MANIFEST,
            domain="VOOL_TEST_BACKUP_V3",
            message=b"record",
        )
    with pytest.raises(ContractValidationError, match="not current"):
        verifier.verify(
            expected_family=KeyFamily.BACKUP_MANIFEST,
            domain="VOOL_TEST_BACKUP_V3",
            message=b"record",
            supplied_mac="0" * 64,
        )


def test_repair2a_key_capability_issuance_is_closed_and_use_binds_exact_reference() -> None:
    claim = _key_metadata(KeyFamily.CLAIM_ENROLLMENT, "claim-1", 1, KeyState.ACTIVE)
    cursor = _key_metadata(KeyFamily.CURSOR, "cursor-1", 1, KeyState.ACTIVE)
    authority = DormantKeyAuthority(
        KeyRingMetadata((claim, cursor)),
        {
            KeyReference(KeyFamily.CLAIM_ENROLLMENT, "claim-1", 1): KEY,
            KeyReference(KeyFamily.CURSOR, "cursor-1", 1): KEY,
        },
        workspace_id=ring_workspace_id(),
    )
    claim_signer = authority.signing_capability(
        family=KeyFamily.CLAIM_ENROLLMENT,
        generation=1,
        key_id="claim-1",
    )
    cursor_signer = authority.signing_capability(
        family=KeyFamily.CURSOR,
        generation=1,
        key_id="cursor-1",
    )
    claim_mac = claim_signer.sign(
        expected_family=KeyFamily.CLAIM_ENROLLMENT,
        domain="VOOL_SHARED_TEST_V3",
        message=b"same bytes",
    )
    cursor_mac = cursor_signer.sign(
        expected_family=KeyFamily.CURSOR,
        domain="VOOL_SHARED_TEST_V3",
        message=b"same bytes",
    )
    assert claim_mac != cursor_mac
    with pytest.raises(ContractValidationError, match="family"):
        claim_signer.sign(
            expected_family=KeyFamily.CURSOR,
            domain="VOOL_SHARED_TEST_V3",
            message=b"same bytes",
        )
    with pytest.raises(ContractValidationError, match="not valid"):
        authority.signing_capability(
            family=KeyFamily.CLAIM_ENROLLMENT,
            generation=1,
            key_id="cursor-1",
        )

    object.__setattr__(cursor_signer, "key_id", "cursor-mutated")
    with pytest.raises(TypeError, match="changed after owner issuance"):
        cursor_signer.sign(
            expected_family=KeyFamily.CURSOR,
            domain="VOOL_SHARED_TEST_V3",
            message=b"same bytes",
        )

    with pytest.raises(TypeError, match="minted only"):
        SigningKeyCapability()
    with pytest.raises(TypeError, match="final"):

        class FakeVerifier(VerificationKeyCapability):
            def verify(self, **_kwargs: object) -> bool:
                return True

    forged = object.__new__(SigningKeyCapability)
    object.__setattr__(forged, "family", KeyFamily.CLAIM_ENROLLMENT)
    object.__setattr__(forged, "generation", 1)
    object.__setattr__(forged, "key_id", "claim-1")
    with pytest.raises(AttributeError):
        object.__setattr__(forged, "_owner", authority)
    with pytest.raises(TypeError, match="not issued"):
        forged.sign(
            expected_family=KeyFamily.CLAIM_ENROLLMENT,
            domain="VOOL_SHARED_TEST_V3",
            message=b"same bytes",
        )
    assert not hasattr(claim_signer, "verify")
    verifier = authority.verification_capability(
        family=KeyFamily.CLAIM_ENROLLMENT,
        generation=1,
        key_id="claim-1",
    )
    assert not hasattr(verifier, "sign")


def test_repair2a_journal_effect_receipt_signing_is_explicitly_deferred() -> None:
    assert JOURNAL_EFFECT_RECEIPT_PHASE0_STATUS is KeyFamilyPhase0Status.DEFERRED_PHASE0
    assert (
        key_family_phase0_status(KeyFamily.JOURNAL_EFFECT_RECEIPT)
        is KeyFamilyPhase0Status.DEFERRED_PHASE0
    )
    with pytest.raises(ContractValidationError, match="explicitly deferred"):
        KeyRingMetadata(
            (
                _key_metadata(
                    KeyFamily.JOURNAL_EFFECT_RECEIPT,
                    "journal-1",
                    1,
                    KeyState.ACTIVE,
                ),
            )
        )

    pending = _key_metadata(
        KeyFamily.JOURNAL_EFFECT_RECEIPT,
        "journal-1",
        1,
        KeyState.PENDING,
    )
    authority = DormantKeyAuthority(
        KeyRingMetadata((pending,)),
        {KeyReference(KeyFamily.JOURNAL_EFFECT_RECEIPT, "journal-1", 1): KEY},
        workspace_id=ring_workspace_id(),
    )
    with pytest.raises(ContractValidationError, match=r"deferred|ACTIVE"):
        authority.signing_capability(
            family=KeyFamily.JOURNAL_EFFECT_RECEIPT,
            generation=1,
            key_id="journal-1",
        )


# ---------------------------------------------------------------------------
# Key-authority closure hostile regressions (Repair #2A items 1-22).
# ---------------------------------------------------------------------------


def _claim_ring_authority(
    material: bytes = KEY,
    *,
    authority_domain: AuthorityDomainId | None = None,
) -> DormantKeyAuthority:
    metadata = KeyRingMetadata(
        (_key_metadata(KeyFamily.CLAIM_ENROLLMENT, "claim-1", 1, KeyState.ACTIVE),)
    )
    return DormantKeyAuthority(
        metadata,
        {KeyReference(KeyFamily.CLAIM_ENROLLMENT, "claim-1", 1): material},
        workspace_id=ring_workspace_id(),
        authority_domain=authority_domain,
    )


def _forged_capability(
    capability_type: type,
    *,
    family: KeyFamily = KeyFamily.CLAIM_ENROLLMENT,
    key_id: str = "claim-1",
    generation: int = 1,
) -> object:
    forged = object.__new__(capability_type)
    object.__setattr__(forged, "family", family)
    object.__setattr__(forged, "generation", generation)
    object.__setattr__(forged, "key_id", key_id)
    return forged


def _issued_claim(
    authority: DormantKeyAuthority,
    domain: AuthorityDomainId | None = None,
) -> EnrollmentClaimV3:
    if domain is None:
        domain = AuthorityDomainId.from_authority_claim(AuthorityClaimProvenance.establish())
    enrollment_operation = EnrollmentOperationId.new()
    enrollment = WorkspaceEnrollmentProvenance.establish(
        authority_domain_id=domain,
        enrollment_operation_id=enrollment_operation,
    )
    return EnrollmentClaimV3.issue(
        authority_domain_id=domain,
        workspace_id=WorkspaceId.from_enrollment(enrollment),
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


def test_repair2a_key_lookalike_owner_capabilities_hold_no_authority() -> None:
    """Items 1-2: exact-typed capabilities minted outside the closed boundary are inert."""

    authority = _claim_ring_authority()

    class LookalikeOwner:
        execution_authority = False

        def _mac(self, *_args: object, **_kwargs: object) -> str:
            raise AssertionError("lookalike owner MAC must never be reachable")

    lookalike = LookalikeOwner()
    forged_signer = _forged_capability(SigningKeyCapability)
    forged_verifier = _forged_capability(VerificationKeyCapability)
    with pytest.raises(AttributeError):
        object.__setattr__(forged_signer, "_owner", lookalike)
    with pytest.raises(TypeError, match="not issued"):
        sign_with_key_capability(
            forged_signer,
            expected_family=KeyFamily.CLAIM_ENROLLMENT,
            domain="VOOL_HOSTILE_TEST_V3",
            message=b"payload",
        )
    with pytest.raises(TypeError, match="not issued"):
        verify_with_key_capability(
            forged_verifier,
            expected_family=KeyFamily.CLAIM_ENROLLMENT,
            domain="VOOL_HOSTILE_TEST_V3",
            message=b"payload",
            supplied_mac="0" * 64,
        )
    with pytest.raises(TypeError, match="not issued"):
        _issued_claim_with_signer(authority, forged_signer)
    claim = _issued_claim(authority)
    with pytest.raises(TypeError, match="not issued"):
        claim.verify_mac(forged_verifier)


def _issued_claim_with_signer(authority: DormantKeyAuthority, signer: object) -> EnrollmentClaimV3:
    domain = AuthorityDomainId.from_authority_claim(AuthorityClaimProvenance.establish())
    enrollment_operation = EnrollmentOperationId.new()
    enrollment = WorkspaceEnrollmentProvenance.establish(
        authority_domain_id=domain,
        enrollment_operation_id=enrollment_operation,
    )
    return EnrollmentClaimV3.issue(
        authority_domain_id=domain,
        workspace_id=WorkspaceId.from_enrollment(enrollment),
        incarnation_id=IncarnationId.new(),
        enrollment_operation_id=enrollment_operation,
        incarnation_nonce="1" * 64,
        persistent_volume_identity_digest="2" * 64,
        root_object_identity_digest="3" * 64,
        runtime_home_id=RuntimeHomeId.new(),
        signing_key=signer,
    )


def test_repair2a_key_capability_owner_substitution_does_not_transfer_authority() -> None:
    """Item 3: no owner slot exists on capabilities, and use stays bound to the issuer."""

    authority = _claim_ring_authority()
    signer = authority.signing_capability(
        family=KeyFamily.CLAIM_ENROLLMENT,
        generation=1,
        key_id="claim-1",
    )

    class HostileOwner:
        def _mac(self, *_args: object, **_kwargs: object) -> str:
            raise AssertionError("substituted owner must never be consulted")

    with pytest.raises(AttributeError):
        signer._owner = HostileOwner()
    with pytest.raises(AttributeError):
        object.__setattr__(signer, "_owner", HostileOwner())
    mac = sign_with_key_capability(
        signer,
        expected_family=KeyFamily.CLAIM_ENROLLMENT,
        domain="VOOL_HOSTILE_TEST_V3",
        message=b"payload",
    )
    verifier = authority.verification_capability(
        family=KeyFamily.CLAIM_ENROLLMENT,
        generation=1,
        key_id="claim-1",
    )
    assert verify_with_key_capability(
        verifier,
        expected_family=KeyFamily.CLAIM_ENROLLMENT,
        domain="VOOL_HOSTILE_TEST_V3",
        message=b"payload",
        supplied_mac=mac,
    )
    object.__delattr__(signer, "family")
    with pytest.raises(TypeError, match="changed after owner issuance"):
        sign_with_key_capability(
            signer,
            expected_family=KeyFamily.CLAIM_ENROLLMENT,
            domain="VOOL_HOSTILE_TEST_V3",
            message=b"payload",
        )


def test_repair2a_key_metadata_rollback_cannot_resurrect_authority() -> None:
    """Items 4 and 7: no rollback path exists, and a cached stale signer stays dead."""

    generation_one = _key_metadata(KeyFamily.CLAIM_ENROLLMENT, "claim-1", 1, KeyState.ACTIVE)
    old_metadata = KeyRingMetadata((generation_one,))
    authority = DormantKeyAuthority(
        old_metadata,
        {KeyReference(KeyFamily.CLAIM_ENROLLMENT, "claim-1", 1): KEY},
        workspace_id=ring_workspace_id(),
    )
    cached_signer = authority.signing_capability(
        family=KeyFamily.CLAIM_ENROLLMENT,
        generation=1,
        key_id="claim-1",
    )
    generation_two = _key_metadata(KeyFamily.CLAIM_ENROLLMENT, "claim-2", 2, KeyState.ACTIVE)
    rotated = KeyRingMetadata(
        (replace(generation_one, state=KeyState.VERIFY_ONLY), generation_two)
    )
    authority.install_metadata(
        rotated,
        key_materials={KeyReference(KeyFamily.CLAIM_ENROLLMENT, "claim-2", 2): b"y" * 32},
    )

    for attribute in ("_metadata", "metadata", "_materials", "_capabilities", "_lock"):
        with pytest.raises(AttributeError):
            object.__setattr__(authority, attribute, old_metadata)
        with pytest.raises(AttributeError):
            setattr(authority, attribute, old_metadata)
    with pytest.raises(ContractValidationError, match="cannot remove generations"):
        authority.install_metadata(old_metadata)
    resurrected = KeyRingMetadata(
        (generation_one, replace(generation_two, state=KeyState.VERIFY_ONLY))
    )
    with pytest.raises(ContractValidationError, match="illegal key lifecycle transition"):
        authority.install_metadata(resurrected)
    assert authority.metadata == rotated
    with pytest.raises(ContractValidationError, match="not current"):
        sign_with_key_capability(
            cached_signer,
            expected_family=KeyFamily.CLAIM_ENROLLMENT,
            domain="VOOL_HOSTILE_TEST_V3",
            message=b"payload",
        )
    with pytest.raises(ContractValidationError, match="not current"):
        cached_signer.sign(
            expected_family=KeyFamily.CLAIM_ENROLLMENT,
            domain="VOOL_HOSTILE_TEST_V3",
            message=b"payload",
        )


def test_repair2a_key_material_ownership_cannot_be_replaced_or_mutated() -> None:
    """Item 5: authoritative material cannot change, and tampered snapshots fail closed."""

    authority = _claim_ring_authority()
    signer = authority.signing_capability(
        family=KeyFamily.CLAIM_ENROLLMENT,
        generation=1,
        key_id="claim-1",
    )
    before = sign_with_key_capability(
        signer,
        expected_family=KeyFamily.CLAIM_ENROLLMENT,
        domain="VOOL_HOSTILE_TEST_V3",
        message=b"payload",
    )
    with pytest.raises(AttributeError):
        object.__setattr__(
            authority,
            "_materials",
            {KeyReference(KeyFamily.CLAIM_ENROLLMENT, "claim-1", 1): b"h" * 32},
        )
    with pytest.raises(ContractValidationError, match="cannot change"):
        authority.install_metadata(
            authority.metadata,
            key_materials={KeyReference(KeyFamily.CLAIM_ENROLLMENT, "claim-1", 1): b"h" * 32},
        )
    after = sign_with_key_capability(
        signer,
        expected_family=KeyFamily.CLAIM_ENROLLMENT,
        domain="VOOL_HOSTILE_TEST_V3",
        message=b"payload",
    )
    assert before == after

    snapshot = authority.metadata
    hostile_generations = (
        _key_metadata(KeyFamily.CLAIM_ENROLLMENT, "claim-hostile", 1, KeyState.ACTIVE),
    )
    object.__setattr__(snapshot, "generations", hostile_generations)
    with pytest.raises(ContractValidationError, match="changed after sealed construction"):
        sign_with_key_capability(
            signer,
            expected_family=KeyFamily.CLAIM_ENROLLMENT,
            domain="VOOL_HOSTILE_TEST_V3",
            message=b"payload",
        )


def test_repair2a_key_capability_issuance_registry_cannot_be_injected() -> None:
    """Item 6: nothing reachable from the owner or module admits a foreign capability."""

    authority = _claim_ring_authority()
    forged = _forged_capability(SigningKeyCapability)
    assert authority.owns_capability(forged) is False
    with pytest.raises(AttributeError):
        object.__setattr__(authority, "_capabilities", {id(forged): forged})
    assert not hasattr(authority, "_capabilities")
    assert not hasattr(authority, "_materials")
    assert not hasattr(authority, "_metadata")
    with pytest.raises(TypeError, match="not issued"):
        sign_with_key_capability(
            forged,
            expected_family=KeyFamily.CLAIM_ENROLLMENT,
            domain="VOOL_HOSTILE_TEST_V3",
            message=b"payload",
        )
    with pytest.raises(TypeError, match="not issued"):
        require_signing_key_capability(forged, expected_family=KeyFamily.CLAIM_ENROLLMENT)


@pytest.mark.parametrize(
    "denied_state",
    (KeyState.PENDING, KeyState.RETIRED, KeyState.REVOKED_COMPROMISED, KeyState.LOST),
)
def test_repair2a_key_lifecycle_states_gate_fresh_issuance(denied_state: KeyState) -> None:
    """Item 8: PENDING/RETIRED/REVOKED/LOST issue neither signing nor verification."""

    metadata = KeyRingMetadata(
        (_key_metadata(KeyFamily.CLAIM_ENROLLMENT, "claim-1", 1, denied_state),)
    )
    authority = DormantKeyAuthority(
        metadata,
        {KeyReference(KeyFamily.CLAIM_ENROLLMENT, "claim-1", 1): KEY},
        workspace_id=ring_workspace_id(),
    )
    with pytest.raises(ContractValidationError, match="ACTIVE"):
        authority.signing_capability(
            family=KeyFamily.CLAIM_ENROLLMENT,
            generation=1,
            key_id="claim-1",
        )
    with pytest.raises(ContractValidationError, match="cannot issue a verification lease"):
        authority.verification_capability(
            family=KeyFamily.CLAIM_ENROLLMENT,
            generation=1,
            key_id="claim-1",
        )


def test_repair2a_second_key_authority_for_same_logical_ring_is_rejected() -> None:
    """Item 9: one logical ring admits exactly one authority owner."""

    ring_workspace = ring_workspace_id()
    metadata = KeyRingMetadata(
        (_key_metadata(KeyFamily.CLAIM_ENROLLMENT, "claim-1", 1, KeyState.ACTIVE),)
    )
    materials = {KeyReference(KeyFamily.CLAIM_ENROLLMENT, "claim-1", 1): KEY}
    first = DormantKeyAuthority(metadata, materials, workspace_id=ring_workspace)
    assert first.workspace_id == ring_workspace
    with pytest.raises(ContractValidationError, match="already has an authority owner"):
        DormantKeyAuthority(metadata, materials, workspace_id=ring_workspace)
    different_metadata = KeyRingMetadata(
        (_key_metadata(KeyFamily.CURSOR, "cursor-1", 1, KeyState.ACTIVE),)
    )
    with pytest.raises(ContractValidationError, match="already has an authority owner"):
        DormantKeyAuthority(
            different_metadata,
            {KeyReference(KeyFamily.CURSOR, "cursor-1", 1): KEY},
            workspace_id=ring_workspace,
        )
    with pytest.raises(ContractValidationError, match="already issued"):
        first.__init__(metadata, materials, workspace_id=ring_workspace_id())


def test_repair2a_identical_key_bytes_cannot_cross_sign_or_verify_between_rings() -> None:
    """Items 10-11: ring context binds every authenticated use; bytes alone carry nothing."""

    first = _claim_ring_authority()
    second = _claim_ring_authority()
    message = b"identical bytes, different logical rings"
    first_mac = sign_with_key_capability(
        first.signing_capability(
            family=KeyFamily.CLAIM_ENROLLMENT,
            generation=1,
            key_id="claim-1",
        ),
        expected_family=KeyFamily.CLAIM_ENROLLMENT,
        domain="VOOL_HOSTILE_TEST_V3",
        message=message,
    )
    second_mac = sign_with_key_capability(
        second.signing_capability(
            family=KeyFamily.CLAIM_ENROLLMENT,
            generation=1,
            key_id="claim-1",
        ),
        expected_family=KeyFamily.CLAIM_ENROLLMENT,
        domain="VOOL_HOSTILE_TEST_V3",
        message=message,
    )
    assert first_mac != second_mac
    assert not verify_with_key_capability(
        second.verification_capability(
            family=KeyFamily.CLAIM_ENROLLMENT,
            generation=1,
            key_id="claim-1",
        ),
        expected_family=KeyFamily.CLAIM_ENROLLMENT,
        domain="VOOL_HOSTILE_TEST_V3",
        message=message,
        supplied_mac=first_mac,
    )
    claim = _issued_claim(first)
    assert not claim.verify_mac(
        second.verification_capability(
            family=KeyFamily.CLAIM_ENROLLMENT,
            generation=1,
            key_id="claim-1",
        )
    )


def test_repair2a_cross_ring_cursor_tokens_do_not_authenticate() -> None:
    """Items 10-11 at the cursor consumer: a foreign ring cannot decode a token."""

    cursor_metadata = KeyRingMetadata(
        (_key_metadata(KeyFamily.CURSOR, "cursor-1", 1, KeyState.ACTIVE),)
    )
    materials = {KeyReference(KeyFamily.CURSOR, "cursor-1", 1): KEY}
    first = DormantKeyAuthority(
        cursor_metadata,
        materials,
        workspace_id=ring_workspace_id(),
    )
    second = DormantKeyAuthority(
        cursor_metadata,
        materials,
        workspace_id=ring_workspace_id(),
    )
    payload = AuthenticatedCursorPayload(
        principal_id="principal-1",
        workspace_id=owned_identity(WorkspaceId, "cross-ring-cursor"),
        incarnation_id=owned_identity(IncarnationId, "cross-ring-cursor"),
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
        key=first.signing_capability(family=KeyFamily.CURSOR, generation=1, key_id="cursor-1")
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
    same_ring = CursorCodec(
        key=first.verification_capability(family=KeyFamily.CURSOR, generation=1, key_id="cursor-1")
    ).decode(token, expectation=expectation, now_unix_ms=150)
    assert same_ring == payload
    with pytest.raises(CursorError, match="authentication"):
        CursorCodec(
            key=second.verification_capability(
                family=KeyFamily.CURSOR,
                generation=1,
                key_id="cursor-1",
            )
        ).decode(token, expectation=expectation, now_unix_ms=150)


def test_repair2a_key_use_binds_family_key_id_generation_and_material() -> None:
    """Items 12-15: every authenticated use binds the exact reference and material."""

    ring = KeyRingMetadata(
        (
            _key_metadata(KeyFamily.CLAIM_ENROLLMENT, "claim-old", 1, KeyState.VERIFY_ONLY),
            _key_metadata(KeyFamily.CLAIM_ENROLLMENT, "claim-2", 2, KeyState.ACTIVE),
            _key_metadata(KeyFamily.CURSOR, "cursor-2", 2, KeyState.ACTIVE),
        )
    )
    authority = DormantKeyAuthority(
        ring,
        {
            KeyReference(KeyFamily.CLAIM_ENROLLMENT, "claim-old", 1): b"o" * 32,
            KeyReference(KeyFamily.CLAIM_ENROLLMENT, "claim-2", 2): KEY,
            KeyReference(KeyFamily.CURSOR, "cursor-2", 2): KEY,
        },
        workspace_id=ring_workspace_id(),
    )
    claim_signer = authority.signing_capability(
        family=KeyFamily.CLAIM_ENROLLMENT,
        generation=2,
        key_id="claim-2",
    )
    with pytest.raises(ContractValidationError, match="family"):
        sign_with_key_capability(
            claim_signer,
            expected_family=KeyFamily.CURSOR,
            domain="VOOL_HOSTILE_TEST_V3",
            message=b"payload",
        )
    claim = _issued_claim_for_key(authority, key_id="claim-2", generation=2)
    stale_verifier = authority.verification_capability(
        family=KeyFamily.CLAIM_ENROLLMENT,
        generation=1,
        key_id="claim-old",
    )
    assert not claim.verify_mac(stale_verifier)
    wrong_material_ring = _claim_ring_authority(material=b"w" * 32)
    wrong_material_verifier = wrong_material_ring.verification_capability(
        family=KeyFamily.CLAIM_ENROLLMENT,
        generation=1,
        key_id="claim-1",
    )
    assert not verify_with_key_capability(
        wrong_material_verifier,
        expected_family=KeyFamily.CLAIM_ENROLLMENT,
        domain="VOOL_ENROLLMENT_CLAIM_MAC_V3",
        message=claim.claim_digest.encode("ascii"),
        supplied_mac=claim.claim_mac,
    )
    with pytest.raises(ContractValidationError, match="not valid"):
        authority.signing_capability(
            family=KeyFamily.CLAIM_ENROLLMENT,
            generation=2,
            key_id="cursor-2",
        )
    with pytest.raises(ContractValidationError, match="not valid"):
        authority.signing_capability(
            family=KeyFamily.CLAIM_ENROLLMENT,
            generation=3,
            key_id="claim-2",
        )


def _issued_claim_for_key(
    authority: DormantKeyAuthority,
    *,
    key_id: str,
    generation: int,
) -> EnrollmentClaimV3:
    domain = AuthorityDomainId.from_authority_claim(AuthorityClaimProvenance.establish())
    enrollment_operation = EnrollmentOperationId.new()
    enrollment = WorkspaceEnrollmentProvenance.establish(
        authority_domain_id=domain,
        enrollment_operation_id=enrollment_operation,
    )
    return EnrollmentClaimV3.issue(
        authority_domain_id=domain,
        workspace_id=WorkspaceId.from_enrollment(enrollment),
        incarnation_id=IncarnationId.new(),
        enrollment_operation_id=enrollment_operation,
        incarnation_nonce="1" * 64,
        persistent_volume_identity_digest="2" * 64,
        root_object_identity_digest="3" * 64,
        runtime_home_id=RuntimeHomeId.new(),
        signing_key=authority.signing_capability(
            family=KeyFamily.CLAIM_ENROLLMENT,
            generation=generation,
            key_id=key_id,
        ),
    )


def test_repair2a_class_level_capability_substitution_cannot_affect_consumers() -> None:
    """Items 16-17: consumers authenticate through the closed boundary, not class methods."""

    domain = AuthorityDomainId.from_authority_claim(AuthorityClaimProvenance.establish())
    authority = _claim_ring_authority(authority_domain=domain)
    verifier = authority.verification_capability(
        family=KeyFamily.CLAIM_ENROLLMENT,
        generation=1,
        key_id="claim-1",
    )
    foreign_authority = _claim_ring_authority()
    hostile_calls: list[str] = []

    def hostile_sign(self: object, **_kwargs: object) -> str:
        hostile_calls.append("sign")
        return "0" * 64

    def hostile_verify(self: object, **_kwargs: object) -> bool:
        hostile_calls.append("verify")
        return True

    def hostile_require_family(self: object, _expected: object) -> None:
        hostile_calls.append("require_family")

    def hostile_owned_by(self: object, _authority: object) -> bool:
        hostile_calls.append("owned_by")
        return True

    original_sign = SigningKeyCapability.__dict__["sign"]
    original_verify = VerificationKeyCapability.__dict__["verify"]
    type.__setattr__(SigningKeyCapability, "sign", hostile_sign)
    type.__setattr__(VerificationKeyCapability, "verify", hostile_verify)
    type.__setattr__(SigningKeyCapability, "require_family", hostile_require_family)
    type.__setattr__(VerificationKeyCapability, "require_family", hostile_require_family)
    type.__setattr__(SigningKeyCapability, "owned_by", hostile_owned_by)
    type.__setattr__(VerificationKeyCapability, "owned_by", hostile_owned_by)
    try:
        claim = _issued_claim(authority, domain)
        assert verify_with_key_capability(
            verifier,
            expected_family=KeyFamily.CLAIM_ENROLLMENT,
            domain="VOOL_ENROLLMENT_CLAIM_MAC_V3",
            message=claim.claim_digest.encode("ascii"),
            supplied_mac=claim.claim_mac,
        )
        assert claim.verify_mac(verifier)
        tampered = replace(claim, claim_mac="0" * 64)
        assert tampered.verify_mac(verifier) is False
        foreign_verifier = foreign_authority.verification_capability(
            family=KeyFamily.CLAIM_ENROLLMENT,
            generation=1,
            key_id="claim-1",
        )
        assert claim.verify_mac(foreign_verifier) is False
        assert authority.owns_capability(foreign_verifier) is False
        cursor_signer_rejection = pytest.raises(
            ContractValidationError,
            match="family",
        )
        with cursor_signer_rejection:
            CursorCodec(
                key=authority.signing_capability(
                    family=KeyFamily.CLAIM_ENROLLMENT,
                    generation=1,
                    key_id="claim-1",
                )
            )
        assert hostile_calls == []
    finally:
        type.__setattr__(SigningKeyCapability, "sign", original_sign)
        type.__setattr__(VerificationKeyCapability, "verify", original_verify)
        type.__delattr__(SigningKeyCapability, "require_family")
        type.__delattr__(VerificationKeyCapability, "require_family")
        type.__delattr__(SigningKeyCapability, "owned_by")
        type.__delattr__(VerificationKeyCapability, "owned_by")


def test_repair2a_class_substitution_cannot_leak_backup_closure_ownership() -> None:
    """Item 17 at the backup consumer: hostile owned_by cannot bridge two owners."""

    ring = KeyRingMetadata(
        (
            _key_metadata(KeyFamily.CLAIM_ENROLLMENT, "claim-2", 2, KeyState.ACTIVE),
            _key_metadata(KeyFamily.BACKUP_MANIFEST, "backup-2", 2, KeyState.ACTIVE),
        )
    )
    materials = {
        KeyReference(KeyFamily.CLAIM_ENROLLMENT, "claim-2", 2): KEY,
        KeyReference(KeyFamily.BACKUP_MANIFEST, "backup-2", 2): KEY,
    }
    authority = DormantKeyAuthority(ring, materials, workspace_id=ring_workspace_id())
    foreign = DormantKeyAuthority(ring, materials, workspace_id=ring_workspace_id())
    profile = AuthorityBudgetProfile(
        profile_version=3,
        raw_before_bytes_per_file=10_000,
        staged_after_bytes_per_file=10_000,
        mutation_bytes=10_000,
        entries=1_000,
        directory_manifest_work=10_000,
        path_bytes=10_000,
        retained_undo_bytes=10_000,
        staging_bytes=10_000,
        tombstone_bytes=10_000,
        domain_storage_bytes=60_000,
        backup_generation_bytes=10_000,
        worker_snapshot_bytes=10_000,
        required_free_space_bytes=10_000,
        change_page_size=100,
        preview_work_bytes=10_000,
        stored_preview_bytes=10_000,
    )
    workspace = owned_identity(WorkspaceId, "backup-closure-ownership")
    incarnation = owned_identity(IncarnationId, "backup-closure-ownership")
    epoch = AuthorityEpoch(5)
    manifest = BackupManifestV3.issue(
        backup_generation_id=BackupGenerationId.new(),
        authority_generation=1,
        db_snapshot_identity_digest="1" * 64,
        workspace_id=workspace,
        incarnation_id=incarnation,
        authority_epoch=epoch,
        head_revision=HeadRevision(7),
        claim_digest="2" * 64,
        claim_key_generation=2,
        blob_inventory_digest="3" * 64,
        tombstone_inventory_digest="4" * 64,
        required_keys=(
            RequiredKeyReference(KeyFamily.CLAIM_ENROLLMENT, 2, "claim-2"),
            RequiredKeyReference(KeyFamily.BACKUP_MANIFEST, 2, "backup-2"),
        ),
        budget_profile_digest=profile.digest(),
        predecessor=BackupPredecessor.genesis(
            workspace_id=workspace,
            incarnation_id=incarnation,
            authority_epoch=epoch,
        ),
        signing_key=authority.signing_capability(
            family=KeyFamily.BACKUP_MANIFEST,
            generation=2,
            key_id="backup-2",
        ),
    )
    foreign_verifier = foreign.verification_capability(
        family=KeyFamily.BACKUP_MANIFEST,
        generation=2,
        key_id="backup-2",
    )

    def hostile_owned_by(self: object, _authority: object) -> bool:
        return True

    type.__setattr__(VerificationKeyCapability, "owned_by", hostile_owned_by)
    try:
        with pytest.raises(ContractValidationError, match="another key authority owner"):
            manifest.validate_closure(
                key_authority=authority,
                verification_key=foreign_verifier,
                budget_profile=profile,
                previous_closure=None,
            )
    finally:
        type.__delattr__(VerificationKeyCapability, "owned_by")
    own_verifier = authority.verification_capability(
        family=KeyFamily.BACKUP_MANIFEST,
        generation=2,
        key_id="backup-2",
    )
    closure = manifest.validate_closure(
        key_authority=authority,
        verification_key=own_verifier,
        budget_profile=profile,
        previous_closure=None,
    )
    assert closure.manifest is manifest


def test_repair2a_nonvirtual_key_use_supports_active_signing_verification_and_rotation() -> None:
    """Items 18-21: the closed path carries the whole normal lifecycle."""

    generation_one = _key_metadata(KeyFamily.CLAIM_ENROLLMENT, "claim-1", 1, KeyState.ACTIVE)
    authority = DormantKeyAuthority(
        KeyRingMetadata((generation_one,)),
        {KeyReference(KeyFamily.CLAIM_ENROLLMENT, "claim-1", 1): KEY},
        workspace_id=ring_workspace_id(),
    )
    signer = authority.signing_capability(
        family=KeyFamily.CLAIM_ENROLLMENT,
        generation=1,
        key_id="claim-1",
    )
    verifier = authority.verification_capability(
        family=KeyFamily.CLAIM_ENROLLMENT,
        generation=1,
        key_id="claim-1",
    )
    signing_reference = require_signing_key_capability(
        signer,
        expected_family=KeyFamily.CLAIM_ENROLLMENT,
    )
    verification_reference = require_verification_key_capability(
        verifier,
        expected_family=KeyFamily.CLAIM_ENROLLMENT,
    )
    assert (signing_reference.family, signing_reference.key_id, signing_reference.generation) == (
        KeyFamily.CLAIM_ENROLLMENT,
        "claim-1",
        1,
    )
    assert signing_reference == verification_reference
    message = b"historical record"
    mac = sign_with_key_capability(
        signer,
        expected_family=KeyFamily.CLAIM_ENROLLMENT,
        domain="VOOL_HOSTILE_TEST_V3",
        message=message,
    )
    assert verify_with_key_capability(
        verifier,
        expected_family=KeyFamily.CLAIM_ENROLLMENT,
        domain="VOOL_HOSTILE_TEST_V3",
        message=message,
        supplied_mac=mac,
    )

    generation_two = _key_metadata(KeyFamily.CLAIM_ENROLLMENT, "claim-2", 2, KeyState.ACTIVE)
    authority.install_metadata(
        KeyRingMetadata((replace(generation_one, state=KeyState.VERIFY_ONLY), generation_two)),
        key_materials={KeyReference(KeyFamily.CLAIM_ENROLLMENT, "claim-2", 2): b"y" * 32},
    )
    next_signer = authority.signing_capability(
        family=KeyFamily.CLAIM_ENROLLMENT,
        generation=2,
        key_id="claim-2",
    )
    next_mac = sign_with_key_capability(
        next_signer,
        expected_family=KeyFamily.CLAIM_ENROLLMENT,
        domain="VOOL_HOSTILE_TEST_V3",
        message=message,
    )
    assert next_mac != mac
    assert verifier.state is KeyState.VERIFY_ONLY
    assert verify_with_key_capability(
        verifier,
        expected_family=KeyFamily.CLAIM_ENROLLMENT,
        domain="VOOL_HOSTILE_TEST_V3",
        message=message,
        supplied_mac=mac,
    )
    with pytest.raises(ContractValidationError, match="not current"):
        require_signing_key_capability(signer, expected_family=KeyFamily.CLAIM_ENROLLMENT)


def test_repair2a_forged_verifier_cannot_authorize_trusted_identity_reconstruction() -> None:
    """Item 22: identity decode proofs require a boundary-issued, ring-correct verifier."""

    domain = AuthorityDomainId.from_authority_claim(AuthorityClaimProvenance.establish())
    authority = _claim_ring_authority(authority_domain=domain)
    claim = _issued_claim(authority, domain)
    source_record = canonical_bytes(claim.unsigned_record())
    forged = _forged_capability(VerificationKeyCapability)
    with pytest.raises(TypeError, match="not issued"):
        _issue_authenticated_identity_decode(
            verification_key=forged,
            source_record=source_record,
            supplied_mac=claim.claim_mac,
            record_type="EnrollmentClaimV3",
            source_field="workspace_id",
            identity_type=WorkspaceId,
        )
    lookalike_ring = _claim_ring_authority()
    lookalike_verifier = lookalike_ring.verification_capability(
        family=KeyFamily.CLAIM_ENROLLMENT,
        generation=1,
        key_id="claim-1",
    )
    with pytest.raises(ValueError, match="MAC is invalid"):
        _issue_authenticated_identity_decode(
            verification_key=lookalike_verifier,
            source_record=source_record,
            supplied_mac=claim.claim_mac,
            record_type="EnrollmentClaimV3",
            source_field="workspace_id",
            identity_type=WorkspaceId,
        )
    genuine_verifier = authority.verification_capability(
        family=KeyFamily.CLAIM_ENROLLMENT,
        generation=1,
        key_id="claim-1",
    )
    proof = _issue_authenticated_identity_decode(
        verification_key=genuine_verifier,
        source_record=source_record,
        supplied_mac=claim.claim_mac,
        record_type="EnrollmentClaimV3",
        source_field="workspace_id",
        identity_type=WorkspaceId,
    )
    decoded = WorkspaceId._from_trusted_storage(proof)
    assert decoded == claim.workspace_id


def test_repair2a_key_rotation_and_signing_race_is_linearized() -> None:
    """Concurrent signing against a rotation never yields a stale-generation MAC."""

    generation_one = _key_metadata(KeyFamily.CLAIM_ENROLLMENT, "claim-1", 1, KeyState.ACTIVE)
    authority = DormantKeyAuthority(
        KeyRingMetadata((generation_one,)),
        {KeyReference(KeyFamily.CLAIM_ENROLLMENT, "claim-1", 1): KEY},
        workspace_id=ring_workspace_id(),
    )
    signer = authority.signing_capability(
        family=KeyFamily.CLAIM_ENROLLMENT,
        generation=1,
        key_id="claim-1",
    )
    barrier = Barrier(9)

    def sign_once() -> str:
        barrier.wait()
        try:
            sign_with_key_capability(
                signer,
                expected_family=KeyFamily.CLAIM_ENROLLMENT,
                domain="VOOL_HOSTILE_TEST_V3",
                message=b"raced payload",
            )
        except ContractValidationError:
            return "DENIED"
        return "SIGNED"

    def rotate_once() -> str:
        barrier.wait()
        authority.install_metadata(
            KeyRingMetadata(
                (
                    replace(generation_one, state=KeyState.VERIFY_ONLY),
                    _key_metadata(KeyFamily.CLAIM_ENROLLMENT, "claim-2", 2, KeyState.ACTIVE),
                )
            ),
            key_materials={KeyReference(KeyFamily.CLAIM_ENROLLMENT, "claim-2", 2): b"y" * 32},
        )
        return "ROTATED"

    with ThreadPoolExecutor(max_workers=9) as executor:
        futures = [executor.submit(sign_once) for _index in range(8)]
        futures.append(executor.submit(rotate_once))
        outcomes = [future.result() for future in futures]
    assert outcomes.count("ROTATED") == 1
    assert set(outcomes) <= {"SIGNED", "DENIED", "ROTATED"}
    with pytest.raises(ContractValidationError, match="not current"):
        sign_with_key_capability(
            signer,
            expected_family=KeyFamily.CLAIM_ENROLLMENT,
            domain="VOOL_HOSTILE_TEST_V3",
            message=b"raced payload",
        )
