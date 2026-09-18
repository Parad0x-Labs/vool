from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from typing import ClassVar

import pytest

from core.workspace_authority_v3.base import AuthorityRecord, AuthorityRecordPayload, ContractValidationError
from core.workspace_authority_v3.contracts import (
    AuthorityBudgetProfile,
    AuthorityBudgetRequest,
    BackupGenerationContract,
    BackupGenerationState,
    BackupManifestV3,
    BackupPredecessor,
    CapabilityEvidenceState,
    DormantKeyAuthority,
    EnrollmentClaimV3,
    KeyFamily,
    KeyGenerationMetadata,
    KeyRingMetadata,
    KeyState,
    MountCertificate,
    MountPrimitive,
    RequiredKeyReference,
    SafeFailureContext,
    ValidatedBackupClosure,
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
    TransactionOutcome,
    TransactionResult,
)
from core.workspace_authority_v3.identity import (
    AuthorityClaimProvenance,
    AuthorityDomainId,
    AuthorityEpoch,
    BackupGenerationId,
    EnrollmentOperationId,
    HeadRevision,
    IncarnationId,
    InvocationIdentity,
    OperationIdentityBinding,
    RuntimeHomeId,
    WorkspaceEnrollmentProvenance,
    WorkspaceId,
    WorkspaceSequence,
)
from core.workspace_authority_v3.lifecycle import (
    CasConflictError,
    DormantOperation,
    DormantOperationRegistry,
    HeadState,
    IdempotencyConflictError,
    PersistedRecoveryDecision,
    RecoveryDecision,
    RecoveryDecisionResult,
    WorkspaceLifecycle,
    apply_persisted_recovery_decision,
    enter_recovery,
    issue_recovery_decision,
    validate_workspace_transition,
)
from tests.workspace_authority_v3_fixtures import (
    active_state_owner,
    enrollment_root,
    key_authority,
    owned_identity,
)


def _trusted(identity_type: type, character: str):
    return owned_identity(identity_type, character)


def _invocation() -> InvocationIdentity:
    return InvocationIdentity(f"turn_{'1' * 64}", 2, 3)


def _binding(**overrides: object) -> OperationIdentityBinding:
    values = {
        "invocation_identity": _invocation(),
        "workspace_id": _trusted(WorkspaceId, "2"),
        "authority_epoch": AuthorityEpoch(3),
        "canonical_action_digest": "4" * 64,
    }
    values.update(overrides)
    return OperationIdentityBinding(**values)


def _recovery_head(**overrides: object) -> HeadState:
    values = {
        "incarnation_id": _trusted(IncarnationId, "2"),
        "authority_epoch": AuthorityEpoch(3),
        "head_revision": HeadRevision(4),
        "workspace_sequence": WorkspaceSequence(5),
        "runtime_home_owner": _trusted(RuntimeHomeId, "6"),
    }
    values.update(overrides)
    return enter_recovery(active_state_owner("repair1-recovery", **values).current)


def _recovery_decision(head: HeadState, **overrides: object) -> RecoveryDecision:
    decision = issue_recovery_decision(
        head,
        result=RecoveryDecisionResult.REACTIVATE,
        evidence_digest="8" * 64,
    )
    return decision if not overrides else replace(decision, **overrides)


KEY = b"r" * 32


def _key_ring(*, retired_claim: bool = False) -> KeyRingMetadata:
    generations = [
        KeyGenerationMetadata(
            KeyFamily.CLAIM_ENROLLMENT,
            "claim-retired-1",
            1,
            KeyState.RETIRED if retired_claim else KeyState.VERIFY_ONLY,
            "HMAC-SHA256",
            1,
        )
    ]
    generations.extend(
        KeyGenerationMetadata(
            family,
            f"{family.value.lower()}-2",
            2,
            KeyState.PENDING if family is KeyFamily.JOURNAL_EFFECT_RECEIPT else KeyState.ACTIVE,
            "HMAC-SHA256",
            1,
        )
        for family in KeyFamily
    )
    return KeyRingMetadata(tuple(generations))


def _profile(*, mutation_bytes: int = 10_000) -> AuthorityBudgetProfile:
    return AuthorityBudgetProfile(
        profile_version=3,
        raw_before_bytes_per_file=10_000,
        staged_after_bytes_per_file=10_000,
        mutation_bytes=mutation_bytes,
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


def _cursor_payload() -> AuthenticatedCursorPayload:
    return AuthenticatedCursorPayload(
        principal_id="principal-1",
        workspace_id=_trusted(WorkspaceId, "1"),
        incarnation_id=_trusted(IncarnationId, "2"),
        authority_epoch=AuthorityEpoch(3),
        query_digest="4" * 64,
        direction=CursorDirection.FORWARD,
        page_size=50,
        highwater=WorkspaceSequence(100),
        exclusive_boundary="sequence:50",
        projection_version=3,
        issued_at_unix_ms=100,
        expires_at_unix_ms=200,
        key_generation=2,
    )


def _cursor_expectation(payload: AuthenticatedCursorPayload) -> CursorExpectation:
    return CursorExpectation(
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


def _backup_manifest() -> tuple[BackupManifestV3, DormantKeyAuthority, AuthorityBudgetProfile]:
    ring = _key_ring()
    authority = key_authority(ring, KEY)
    profile = _profile()
    workspace_id = _trusted(WorkspaceId, "3")
    incarnation_id = _trusted(IncarnationId, "4")
    authority_epoch = AuthorityEpoch(5)
    manifest = BackupManifestV3.issue(
        backup_generation_id=_trusted(BackupGenerationId, "6"),
        authority_generation=1,
        db_snapshot_identity_digest="1" * 64,
        workspace_id=workspace_id,
        incarnation_id=incarnation_id,
        authority_epoch=authority_epoch,
        head_revision=HeadRevision(7),
        claim_digest="2" * 64,
        claim_key_generation=2,
        blob_inventory_digest="3" * 64,
        tombstone_inventory_digest="4" * 64,
        required_keys=(
            RequiredKeyReference(KeyFamily.CLAIM_ENROLLMENT, 2, "claim_enrollment-2"),
            RequiredKeyReference(KeyFamily.BACKUP_MANIFEST, 2, "backup_manifest-2"),
        ),
        budget_profile_digest=profile.digest(),
        predecessor=BackupPredecessor.genesis(
            workspace_id=workspace_id,
            incarnation_id=incarnation_id,
            authority_epoch=authority_epoch,
        ),
        signing_key=authority.signing_capability(
            family=KeyFamily.BACKUP_MANIFEST,
            generation=2,
            key_id="backup_manifest-2",
        ),
    )
    return manifest, authority, profile


def test_repair1_hard_gate_rejects_field_collision_before_serialization() -> None:
    with pytest.raises(TypeError, match="sealed envelope"):

        @dataclass(frozen=True, slots=True)
        class HostileAuthorityRecord(AuthorityRecord):
            RECORD_TYPE: ClassVar[str] = "HostileAuthorityRecord"
            execution_authority: bool = True

    with pytest.raises(ContractValidationError, match="reserved envelope"):
        AuthorityRecordPayload((("execution_authority", True),))
    with pytest.raises(TypeError, match="sealed"):
        RecoveryDecision.execution_authority = True


def test_repair1_workspace_identity_requires_typed_enrollment_provenance() -> None:
    for source in ("session:alpha", "/private/workspace", "2026-08-10T12:00:00Z"):
        digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
        with pytest.raises(TypeError, match="closed identity owner"):
            WorkspaceId(f"workspace_{digest}")
        with pytest.raises(TypeError, match="closed identity owner"):
            AuthorityDomainId(f"authority-domain_{digest}")
        with pytest.raises(TypeError, match="AuthenticatedIdentityDecode"):
            WorkspaceId._from_trusted_storage(None)  # type: ignore[arg-type]

    domain = AuthorityDomainId.from_authority_claim(AuthorityClaimProvenance.establish())
    enrollment = WorkspaceEnrollmentProvenance.establish(
        authority_domain_id=domain,
        enrollment_operation_id=EnrollmentOperationId.new(),
    )
    workspace = WorkspaceId.from_enrollment(enrollment)
    assert isinstance(workspace, WorkspaceId)


def test_repair1_same_invocation_conflicts_across_scope_epoch_action_and_plan() -> None:
    baseline = _binding()
    variants = (
        replace(baseline, workspace_id=_trusted(WorkspaceId, "9")),
        replace(baseline, authority_epoch=AuthorityEpoch(4)),
        replace(baseline, canonical_action_digest="9" * 64),
    )
    for hostile in variants:
        registry = DormantOperationRegistry()
        registered = registry.register(baseline, "a" * 64)
        assert hostile.operation_id() == registered.operation.operation_id
        with pytest.raises(IdempotencyConflictError, match="IDEMPOTENCY_CONFLICT"):
            registry.register(hostile, "a" * 64)

    registry = DormantOperationRegistry()
    registry.register(baseline, "a" * 64)
    with pytest.raises(IdempotencyConflictError, match="IDEMPOTENCY_CONFLICT"):
        registry.register(baseline, "b" * 64)


def test_repair1_persisted_idempotency_conflicts_across_scope(tmp_path) -> None:
    baseline_binding = _binding()
    baseline = DormantOperationRegistry().register(baseline_binding, "a" * 64).operation
    database = DormantAuthorityDatabase(tmp_path / "authority.sqlite3")
    assert database.register_operation(baseline).outcome is TransactionOutcome.COMMITTED_NEW
    for hostile_binding in (
        replace(baseline_binding, workspace_id=_trusted(WorkspaceId, "9")),
        replace(baseline_binding, authority_epoch=AuthorityEpoch(9)),
        replace(baseline_binding, canonical_action_digest="9" * 64),
    ):
        hostile = DormantOperation(
            hostile_binding.operation_id(),
            hostile_binding.mutation_id(),
            hostile_binding,
            "a" * 64,
        )
        result = database.register_operation(hostile)
        assert result.outcome is TransactionOutcome.CAS_CONFLICT
        assert result.failure is not None and result.failure.code.value == "IDEMPOTENCY_CONFLICT"


def test_repair1_recovery_exit_requires_exact_persisted_decision(tmp_path) -> None:
    head = _recovery_head()
    with pytest.raises(ContractValidationError, match="illegal workspace"):
        validate_workspace_transition(WorkspaceLifecycle.RECOVERY_REQUIRED, WorkspaceLifecycle.ACTIVE)
    with pytest.raises(ContractValidationError, match="digest mismatch"):
        PersistedRecoveryDecision(_recovery_decision(head), "a" * 64)

    database = DormantAuthorityDatabase(tmp_path / "authority.sqlite3")
    assert database.register_recovery_cycle(head).outcome is TransactionOutcome.COMMITTED_NEW
    decision = _recovery_decision(head)
    wrong = replace(decision, authority_epoch=AuthorityEpoch(9))
    with pytest.raises(CasConflictError):
        database.persist_recovery_decision(head, wrong)

    persisted_result = database.persist_recovery_decision(head, decision)
    assert persisted_result.outcome is TransactionOutcome.COMMITTED_NEW
    assert persisted_result.value is not None
    active = apply_persisted_recovery_decision(head, database, decision)
    assert active.lifecycle is WorkspaceLifecycle.ACTIVE
    with pytest.raises(CasConflictError, match="replayed"):
        apply_persisted_recovery_decision(active, database, decision)


def test_repair1_signing_requires_active_exact_family_generation_and_key_id() -> None:
    retired_authority = key_authority(_key_ring(retired_claim=True), KEY)
    with pytest.raises(ContractValidationError, match="ACTIVE"):
        retired_authority.signing_capability(
            family=KeyFamily.CLAIM_ENROLLMENT,
            generation=1,
            key_id="claim-retired-1",
        )

    ring = _key_ring()
    authority = key_authority(ring, KEY)
    claim_signer = authority.signing_capability(
        family=KeyFamily.CLAIM_ENROLLMENT,
        generation=2,
        key_id="claim_enrollment-2",
    )
    cursor_signer = authority.signing_capability(
        family=KeyFamily.CURSOR,
        generation=2,
        key_id="cursor-2",
    )
    with pytest.raises(ContractValidationError, match="family"):
        CursorCodec(key=claim_signer)
    with pytest.raises(ContractValidationError, match="family"):
        authority_domain_id, workspace_id, enrollment_operation_id = enrollment_root(
            "repair1-wrong-key-family"
        )
        EnrollmentClaimV3.issue(
            authority_domain_id=authority_domain_id,
            workspace_id=workspace_id,
            incarnation_id=_trusted(IncarnationId, "3"),
            enrollment_operation_id=enrollment_operation_id,
            incarnation_nonce="5" * 64,
            persistent_volume_identity_digest="6" * 64,
            root_object_identity_digest="7" * 64,
            runtime_home_id=_trusted(RuntimeHomeId, "8"),
            signing_key=cursor_signer,
        )


def test_repair1_cursor_expectation_rejects_direction_and_page_size_replay() -> None:
    payload = _cursor_payload()
    authority = key_authority(_key_ring(), KEY)
    signer = authority.signing_capability(
        family=KeyFamily.CURSOR,
        generation=2,
        key_id="cursor-2",
    )
    token = CursorCodec(key=signer).encode(payload)
    codec = CursorCodec(
        key=authority.verification_capability(
            family=KeyFamily.CURSOR,
            generation=2,
            key_id="cursor-2",
        )
    )
    with pytest.raises(CursorError, match="direction"):
        codec.decode(
            token,
            expectation=replace(_cursor_expectation(payload), direction=CursorDirection.BACKWARD),
            now_unix_ms=150,
        )
    with pytest.raises(CursorError, match="page-size"):
        codec.decode(
            token,
            expectation=replace(_cursor_expectation(payload), page_size=999),
            now_unix_ms=150,
        )


def test_repair1_backup_sealing_requires_authenticated_complete_closure() -> None:
    manifest, authority, profile = _backup_manifest()
    verifier = authority.verification_capability(
        family=KeyFamily.BACKUP_MANIFEST,
        generation=2,
        key_id="backup_manifest-2",
    )
    closure = manifest.validate_closure(
        key_authority=authority,
        verification_key=verifier,
        budget_profile=profile,
        previous_closure=None,
    )
    current = BackupGenerationContract(
        manifest.backup_generation_id,
        BackupGenerationState.ARTIFACTS_COPIED,
        None,
    )
    sealed = BackupGenerationContract.seal(current, closure)
    assert sealed.state is BackupGenerationState.SEALED_COMPLETE
    assert sealed.manifest_digest == manifest.manifest_digest

    with pytest.raises(ContractValidationError, match="validated backup closure"):
        BackupGenerationContract(
            manifest.backup_generation_id,
            BackupGenerationState.SEALED_COMPLETE,
            "a" * 64,
        )
    with pytest.raises(TypeError, match="manifest validation"):
        ValidatedBackupClosure(manifest)
    with pytest.raises((TypeError, ContractValidationError), match="db_snapshot_identity_digest"):
        replace(manifest, db_snapshot_identity_digest=None)  # type: ignore[arg-type]
    with pytest.raises((TypeError, ContractValidationError), match="tombstone_inventory_digest"):
        replace(manifest, tombstone_inventory_digest=None)  # type: ignore[arg-type]
    with pytest.raises(ContractValidationError, match="manifest-signing key"):
        replace(
            manifest,
            required_keys=(RequiredKeyReference(KeyFamily.CLAIM_ENROLLMENT, 2, "claim_enrollment-2"),),
        )


def test_repair1_backup_predecessor_is_mandatory_exact_and_scope_bound() -> None:
    manifest, authority, profile = _backup_manifest()
    signer = authority.signing_capability(
        family=KeyFamily.BACKUP_MANIFEST,
        generation=2,
        key_id="backup_manifest-2",
    )
    verifier = authority.verification_capability(
        family=KeyFamily.BACKUP_MANIFEST,
        generation=2,
        key_id="backup_manifest-2",
    )
    genesis_closure = manifest.validate_closure(
        key_authority=authority,
        verification_key=verifier,
        budget_profile=profile,
        previous_closure=None,
    )

    def issue_generation_two(previous_digest: str) -> BackupManifestV3:
        return BackupManifestV3.issue(
            backup_generation_id=_trusted(BackupGenerationId, "8"),
            authority_generation=2,
            db_snapshot_identity_digest="6" * 64,
            workspace_id=manifest.workspace_id,
            incarnation_id=manifest.incarnation_id,
            authority_epoch=manifest.authority_epoch,
            head_revision=HeadRevision(8),
            claim_digest=manifest.claim_digest,
            claim_key_generation=manifest.claim_key_generation,
            blob_inventory_digest="7" * 64,
            tombstone_inventory_digest="8" * 64,
            required_keys=manifest.required_keys,
            budget_profile_digest=profile.digest(),
            predecessor=BackupPredecessor.completed(
                workspace_id=manifest.workspace_id,
                incarnation_id=manifest.incarnation_id,
                authority_epoch=manifest.authority_epoch,
                authority_generation=1,
                manifest_digest=previous_digest,
            ),
            signing_key=signer,
        )

    generation_two = issue_generation_two(manifest.manifest_digest)
    generation_two.validate_closure(
        key_authority=authority,
        verification_key=verifier,
        budget_profile=profile,
        previous_closure=genesis_closure,
    )
    with pytest.raises(ContractValidationError, match="previous validated"):
        generation_two.validate_closure(
            key_authority=authority,
            verification_key=verifier,
            budget_profile=profile,
            previous_closure=None,
        )
    with pytest.raises(ContractValidationError, match="wrong, stale, future"):
        issue_generation_two("9" * 64).validate_closure(
            key_authority=authority,
            verification_key=verifier,
            budget_profile=profile,
            previous_closure=genesis_closure,
        )

    with pytest.raises(TypeError, match="predecessor"):
        replace(manifest, predecessor=None)  # type: ignore[arg-type]

    for predecessor in (
        BackupPredecessor.completed(
            workspace_id=manifest.workspace_id,
            incarnation_id=manifest.incarnation_id,
            authority_epoch=manifest.authority_epoch,
            authority_generation=1,
            manifest_digest="a" * 64,
        ),
        BackupPredecessor.completed(
            workspace_id=manifest.workspace_id,
            incarnation_id=manifest.incarnation_id,
            authority_epoch=manifest.authority_epoch,
            authority_generation=3,
            manifest_digest="a" * 64,
        ),
        BackupPredecessor.completed(
            workspace_id=_trusted(WorkspaceId, "9"),
            incarnation_id=manifest.incarnation_id,
            authority_epoch=manifest.authority_epoch,
            authority_generation=2,
            manifest_digest="a" * 64,
        ),
    ):
        with pytest.raises(ContractValidationError, match=r"previous|scope"):
            BackupManifestV3.issue(
                backup_generation_id=manifest.backup_generation_id,
                authority_generation=3,
                db_snapshot_identity_digest=manifest.db_snapshot_identity_digest,
                workspace_id=manifest.workspace_id,
                incarnation_id=manifest.incarnation_id,
                authority_epoch=manifest.authority_epoch,
                head_revision=manifest.head_revision,
                claim_digest=manifest.claim_digest,
                claim_key_generation=manifest.claim_key_generation,
                blob_inventory_digest=manifest.blob_inventory_digest,
                tombstone_inventory_digest=manifest.tombstone_inventory_digest,
                required_keys=manifest.required_keys,
                budget_profile_digest=profile.digest(),
                predecessor=predecessor,
                signing_key=signer,
            )


def test_repair1_budget_reservation_rejects_profile_substitution() -> None:
    profile_a = _profile(mutation_bytes=10_000)
    profile_b = _profile(mutation_bytes=20_000)
    reservation = profile_a.reserve(AuthorityBudgetRequest(mutation_bytes=100))
    profile_a.validate_reservation(reservation)
    with pytest.raises(ContractValidationError, match="profile identity"):
        profile_b.validate_reservation(reservation)


def test_repair1_mount_support_requires_complete_nonzero_known_mask() -> None:
    certificate = MountCertificate(
        certificate_id="mount-cert-1",
        adapter_id="probe-adapter",
        adapter_version="v3",
        os_name="Linux",
        kernel_version="6.8.0",
        mount_instance_id="mount-instance-1",
        persistent_volume_identity_digest="1" * 64,
        filesystem_type="ext4",
        filesystem_subtype="default",
        mount_flags=("rw",),
        root_identity_digest="2" * 64,
        authority_namespace_identity_digest="3" * 64,
        supported_primitive_bitset=int(MountPrimitive.ATOMIC_RENAME),
        generation=1,
        probe_suite_digest="4" * 64,
        evidence_state=CapabilityEvidenceState.PROVEN,
    )
    requested = MountPrimitive.ATOMIC_RENAME | MountPrimitive.DIRECTORY_FSYNC
    assert not certificate.supports(requested)
    assert replace(certificate, supported_primitive_bitset=int(requested)).supports(requested)
    assert not certificate.supports(MountPrimitive.NONE)
    assert not certificate.supports(MountPrimitive(1 << 20))


def test_repair1_safe_failure_context_rejects_all_raw_secret_like_values() -> None:
    hostile_values = (
        "sk-live-secret-123",
        "Bearer abc.def.ghi",
        "-----BEGIN PRIVATE KEY-----",
        "/Users/alice/private/repository",
        "raw user content with personal data",
    )
    for hostile in hostile_values:
        with pytest.raises(TypeError, match="OperationId"):
            SafeFailureContext(operation_id=hostile)  # type: ignore[arg-type]
    safe = SafeFailureContext(
        operation_id=_trusted(type(_invocation().operation_id()), "a"),
        redacted_metadata_digest=hashlib.sha256(hostile_values[-1].encode("utf-8")).hexdigest(),
    )
    serialized = safe.to_record()
    assert all(hostile not in repr(serialized) for hostile in hostile_values)


def test_repair1_non_sqlite_commit_exception_has_typed_unknown_outcome(tmp_path) -> None:
    class CommitFailureProxy:
        def __init__(self, connection) -> None:
            self._connection = connection

        def __getattr__(self, name: str):
            return getattr(self._connection, name)

        def commit(self) -> None:
            raise RuntimeError("wrapped storage layer lost commit acknowledgement")

    class CommitFailingDatabase(DormantAuthorityDatabase):
        fail_commits = False

        def _connect(self):
            connection = super()._connect()
            return CommitFailureProxy(connection) if self.fail_commits else connection

    database = CommitFailingDatabase(tmp_path / "authority.sqlite3")
    database.fail_commits = True
    with pytest.raises(AuthorityCommitError) as captured:
        database.run_transaction(lambda _connection: TransactionResult(TransactionOutcome.COMMITTED_NEW))
    assert captured.value.result.outcome is TransactionOutcome.OUTCOME_UNKNOWN
    assert isinstance(captured.value.__cause__, RuntimeError)
