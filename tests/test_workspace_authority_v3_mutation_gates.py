"""Executable hostile mutation gates for the dormant workspace authority V3 boundary."""

from __future__ import annotations

import random
import sqlite3
from dataclasses import replace
from functools import cache
from pathlib import Path

import pytest

from core.workspace_authority_v3.base import ContractValidationError
from core.workspace_authority_v3.contracts import (
    AuthorityBudgetProfile,
    AuthorityBudgetRequest,
    BackupManifestV3,
    BackupPredecessor,
    CapabilityEvidenceState,
    KeyFamily,
    KeyGenerationMetadata,
    KeyReference,
    KeyRingMetadata,
    KeyState,
    MountCertificate,
    MountPrimitive,
    RequiredKeyReference,
    WorkspaceUserSnapshot,
)
from core.workspace_authority_v3.cursor import (
    AuthenticatedCursorPayload,
    CursorCodec,
    CursorDirection,
    CursorError,
    CursorExpectation,
    CursorTokenTooLargeError,
)
from core.workspace_authority_v3.database import DormantAuthorityDatabase
from core.workspace_authority_v3.identity import (
    AuthorityEpoch,
    BackupGenerationId,
    HeadRevision,
    IncarnationId,
    InvocationIdentity,
    MutationId,
    OperationId,
    OperationIdentityBinding,
    RuntimeHomeId,
    WorkspaceId,
    WorkspaceSequence,
)
from core.workspace_authority_v3.lifecycle import (
    CasConflictError,
    DormantOperationRegistry,
    HeadExpectation,
    HeadState,
    IdempotencyConflictError,
    MutationLifecycle,
    WorkspaceLifecycle,
    advance_head,
    claim_pending,
    validate_mutation_transition,
)
from core.workspace_authority_v3.path_policy import ReservedNamespaceError, validate_normal_plan_path
from tests.workspace_authority_v3_fixtures import active_state_owner, key_authority, owned_identity

KEY = b"m" * 32


def _opaque(identity_type: type, character: str):
    return owned_identity(identity_type, character)


def _binding(**overrides: object) -> OperationIdentityBinding:
    values = {
        "invocation_identity": InvocationIdentity(f"turn_{'1' * 64}", 2, 3),
        "workspace_id": _opaque(WorkspaceId, "4"),
        "authority_epoch": AuthorityEpoch(5),
        "canonical_action_digest": "6" * 64,
    }
    values.update(overrides)
    return OperationIdentityBinding(**values)


def _head(**overrides: object) -> HeadState:
    values = {
        "incarnation_id": _opaque(IncarnationId, "2"),
        "authority_epoch": AuthorityEpoch(3),
        "head_revision": HeadRevision(4),
        "workspace_sequence": WorkspaceSequence(5),
        "runtime_home_owner": _opaque(RuntimeHomeId, "6"),
    }
    pending_operation_id = overrides.pop("pending_operation_id", None)
    pending_mutation_id = overrides.pop("pending_mutation_id", None)
    values.update(overrides)
    owner = active_state_owner("mutation-gates-head", **values)
    state = owner.current
    if pending_operation_id is not None and pending_mutation_id is not None:
        return claim_pending(
            state,
            HeadExpectation(state.authority_epoch, state.head_revision, state.runtime_home_owner),
            pending_operation_id,
            pending_mutation_id,
        )
    return state


def _budget_profile() -> AuthorityBudgetProfile:
    return AuthorityBudgetProfile(
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


def _cursor() -> AuthenticatedCursorPayload:
    return AuthenticatedCursorPayload(
        principal_id="principal-1",
        workspace_id=_opaque(WorkspaceId, "1"),
        incarnation_id=_opaque(IncarnationId, "2"),
        authority_epoch=AuthorityEpoch(3),
        query_digest="4" * 64,
        direction=CursorDirection.FORWARD,
        page_size=50,
        highwater=WorkspaceSequence(10_000),
        exclusive_boundary="sequence:9000",
        projection_version=3,
        issued_at_unix_ms=100,
        expires_at_unix_ms=200,
        key_generation=2,
    )


def _expectation(payload: AuthenticatedCursorPayload) -> CursorExpectation:
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


def _mount() -> MountCertificate:
    return MountCertificate(
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
        supported_primitive_bitset=0,
        generation=1,
        probe_suite_digest="4" * 64,
        evidence_state=CapabilityEvidenceState.UNPROVEN,
    )


def _key_ring(old_state: KeyState = KeyState.VERIFY_ONLY, *, cursor_generation: int = 2) -> KeyRingMetadata:
    generations = [
        KeyGenerationMetadata(KeyFamily.CLAIM_ENROLLMENT, "claim-old", 1, old_state, "HMAC-SHA256", 1)
    ]
    generations.extend(
        KeyGenerationMetadata(
            family,
            f"{family.value.lower()}-{cursor_generation if family is KeyFamily.CURSOR else 2}",
            cursor_generation if family is KeyFamily.CURSOR else 2,
            KeyState.PENDING if family is KeyFamily.JOURNAL_EFFECT_RECEIPT else KeyState.ACTIVE,
            "HMAC-SHA256",
            1,
        )
        for family in KeyFamily
    )
    return KeyRingMetadata(tuple(generations))


def _backup_manifest() -> BackupManifestV3:
    workspace_id = _opaque(WorkspaceId, "3")
    incarnation_id = _opaque(IncarnationId, "4")
    authority_epoch = AuthorityEpoch(5)
    authority = key_authority(_key_ring(), KEY)
    return BackupManifestV3.issue(
        backup_generation_id=_opaque(BackupGenerationId, "1"),
        authority_generation=2,
        db_snapshot_identity_digest="2" * 64,
        workspace_id=workspace_id,
        incarnation_id=incarnation_id,
        authority_epoch=authority_epoch,
        head_revision=HeadRevision(6),
        claim_digest="7" * 64,
        claim_key_generation=1,
        blob_inventory_digest="8" * 64,
        tombstone_inventory_digest="9" * 64,
        required_keys=(
            RequiredKeyReference(KeyFamily.CLAIM_ENROLLMENT, 1, "claim-old"),
            RequiredKeyReference(KeyFamily.BACKUP_MANIFEST, 2, "backup_manifest-2"),
        ),
        budget_profile_digest=_budget_profile().digest(),
        predecessor=BackupPredecessor.completed(
            workspace_id=workspace_id,
            incarnation_id=incarnation_id,
            authority_epoch=authority_epoch,
            authority_generation=1,
            manifest_digest="a" * 64,
        ),
        signing_key=authority.signing_capability(
            family=KeyFamily.BACKUP_MANIFEST,
            generation=2,
            key_id="backup_manifest-2",
        ),
    )


@cache
def _cursor_authority(generation: int = 2):
    """One authority per cursor test ring so signing and verification share truth."""

    return key_authority(_key_ring(cursor_generation=generation), KEY)


def _cursor_key(*, generation: int = 2, verification: bool = False):
    authority = _cursor_authority(generation)
    method = authority.verification_capability if verification else authority.signing_capability
    return method(
        family=KeyFamily.CURSOR,
        generation=generation,
        key_id=f"cursor-{generation}",
    )


def _snapshot_signing_key():
    return key_authority(_key_ring(), KEY).signing_capability(
        family=KeyFamily.WORKER_SNAPSHOT,
        generation=2,
        key_id="worker_snapshot-2",
    )


def test_mutant_session_or_path_derived_workspace_identity_factory_is_absent_and_raw_forms_fail() -> None:
    assert not hasattr(WorkspaceId, "from_session")
    assert not hasattr(WorkspaceId, "from_path")
    for raw in ("session-123", "/private/workspace", "C:\\repo"):
        with pytest.raises((TypeError, ValueError)):
            WorkspaceId(raw)


def test_mutant_timestamp_or_session_added_to_operation_ordering_is_rejected() -> None:
    with pytest.raises(TypeError, match="unexpected keyword"):
        OperationIdentityBinding(
            invocation_identity=InvocationIdentity(f"turn_{'1' * 64}", 2, 3),
            workspace_id=_opaque(WorkspaceId, "4"),
            authority_epoch=AuthorityEpoch(5),
            canonical_action_digest="6" * 64,
            created_at_unix_ms=100,  # type: ignore[call-arg]
        )


def test_mutant_duplicate_operation_id_for_different_identity_is_killed() -> None:
    registry = DormantOperationRegistry()
    baseline = registry.register(_binding(), "a" * 64).operation
    hostile = _binding(canonical_action_digest="b" * 64)
    assert hostile.operation_id() == baseline.operation_id
    with pytest.raises(IdempotencyConflictError, match="IDEMPOTENCY_CONFLICT"):
        registry.register(hostile, "a" * 64)


def test_mutant_replay_creates_second_mutation_or_accepts_different_plan_is_killed() -> None:
    registry = DormantOperationRegistry()
    first = registry.register(_binding(), "a" * 64)
    replay = registry.register(_binding(), "a" * 64)
    assert replay.operation.mutation_id == first.operation.mutation_id
    with pytest.raises(IdempotencyConflictError):
        registry.register(_binding(), "b" * 64)


def test_mutant_sequence_contiguity_requirement_is_killed_across_random_gaps() -> None:
    generator = random.Random(0xC451)
    operation_id = _opaque(OperationId, "7")
    mutation_id = _opaque(MutationId, "8")
    for _index in range(100):
        state = _head(pending_operation_id=operation_id, pending_mutation_id=mutation_id)
        revision_gap = generator.randrange(1, 10_000)
        sequence_gap = generator.randrange(1, 10_000)
        advanced = advance_head(
            state,
            operation_id=operation_id,
            mutation_id=mutation_id,
            new_head_revision=HeadRevision(int(state.head_revision) + revision_gap),
            new_workspace_sequence=WorkspaceSequence(int(state.workspace_sequence) + sequence_gap),
        )
        assert int(advanced.head_revision) - int(state.head_revision) == revision_gap


def test_mutant_epoch_ignored_or_pending_pair_overwritten_in_cas_is_killed() -> None:
    state = _head()
    operation_id = _opaque(OperationId, "7")
    mutation_id = _opaque(MutationId, "8")
    wrong_epoch = HeadExpectation(AuthorityEpoch(99), state.head_revision, state.runtime_home_owner)
    with pytest.raises(CasConflictError, match="epoch"):
        claim_pending(state, wrong_epoch, operation_id, mutation_id)
    pending = _head(pending_operation_id=operation_id, pending_mutation_id=mutation_id)
    correct = HeadExpectation(state.authority_epoch, state.head_revision, state.runtime_home_owner)
    with pytest.raises(CasConflictError, match="pending"):
        claim_pending(pending, correct, _opaque(OperationId, "9"), _opaque(MutationId, "a"))


def test_mutant_unknown_causality_promoted_to_applied_is_killed() -> None:
    for unknown in (MutationLifecycle.RECOVERED_EFFECT_CAUSALITY_UNKNOWN, MutationLifecycle.RECOVERY_REQUIRED):
        with pytest.raises(ContractValidationError):
            validate_mutation_transition(unknown, MutationLifecycle.APPLIED_NORMAL)


def test_mutant_reserved_namespace_alias_accepted_is_killed() -> None:
    for hostile in (".VOOL-AUTHORITY", ".ｖｏｏｌ－ａｕｔｈｏｒｉｔｙ", ".vool-authority:$DATA", "VOOL-A~1"):
        with pytest.raises(ReservedNamespaceError):
            validate_normal_plan_path(hostile)


def test_mutant_cursor_owner_workspace_or_epoch_omitted_or_ignored_is_killed() -> None:
    payload = _cursor()
    token = CursorCodec(key=_cursor_key()).encode(payload)
    verifier = CursorCodec(key=_cursor_key(verification=True))
    for hostile in (
        replace(_expectation(payload), principal_id="principal-2"),
        replace(_expectation(payload), workspace_id=_opaque(WorkspaceId, "f")),
        replace(_expectation(payload), authority_epoch=AuthorityEpoch(4)),
    ):
        with pytest.raises(CursorError):
            verifier.decode(token, expectation=hostile, now_unix_ms=150)
    raw = payload.to_record()
    del raw["payload"]["workspace_id"]
    with pytest.raises(TypeError, match="verification_key"):
        AuthenticatedCursorPayload.from_record(raw)


def test_mutant_cursor_oversized_check_moved_after_authentication_is_killed() -> None:
    class ProbeCodec(CursorCodec):
        authenticated = False

        def _authenticate(self, payload: bytes) -> str:
            self.authenticated = True
            return super()._authenticate(payload)

    codec = ProbeCodec(key=_cursor_key(), max_token_bytes=64)
    with pytest.raises(CursorTokenTooLargeError):
        codec.decode("A" * 65, expectation=_expectation(_cursor()), now_unix_ms=150)
    assert codec.authenticated is False


def test_mutant_budget_overflow_or_dimension_omission_is_killed_across_random_reservations() -> None:
    profile = _budget_profile()
    generator = random.Random(0xB0D6E7)
    for _index in range(250):
        request = AuthorityBudgetRequest(
            mutation_bytes=generator.randrange(0, profile.mutation_bytes + 1),
            entries=generator.randrange(0, profile.entries + 1),
            path_bytes=generator.randrange(0, profile.path_bytes + 1),
        )
        profile.validate_reservation(profile.reserve(request))
    with pytest.raises(ContractValidationError, match="overflows"):
        AuthorityBudgetRequest(staging_bytes=2**63 - 1, tombstone_bytes=1)
    with pytest.raises(ContractValidationError, match="entries"):
        profile.reserve(AuthorityBudgetRequest(entries=profile.entries + 1))


def test_mutant_cursor_key_generation_ignored_is_killed() -> None:
    payload = _cursor()
    ring = _key_ring(cursor_generation=2)
    authority = key_authority(ring, KEY)
    signer = authority.signing_capability(
        family=KeyFamily.CURSOR,
        generation=2,
        key_id="cursor-2",
    )
    token = CursorCodec(key=signer).encode(payload)
    rotated = KeyRingMetadata(
        (
            *(
                replace(item, state=KeyState.VERIFY_ONLY)
                if item.family is KeyFamily.CURSOR
                else item
                for item in ring.generations
            ),
            KeyGenerationMetadata(KeyFamily.CURSOR, "cursor-3", 3, KeyState.ACTIVE, "HMAC-SHA256", 1),
        )
    )
    authority.install_metadata(
        rotated,
        key_materials={KeyReference(KeyFamily.CURSOR, "cursor-3", 3): b"n" * 32},
    )
    next_generation_verifier = authority.verification_capability(
        family=KeyFamily.CURSOR,
        generation=3,
        key_id="cursor-3",
    )
    with pytest.raises(CursorError, match=r"authentication|stale"):
        CursorCodec(key=next_generation_verifier).decode(
            token,
            expectation=_expectation(payload),
            now_unix_ms=150,
        )


def test_mutant_stale_backup_key_generation_accepted_is_killed() -> None:
    manifest = _backup_manifest()
    manifest.validate_required_keys(key_authority(_key_ring(), KEY))
    with pytest.raises(ContractValidationError, match="STALE_KEY_BACKUP"):
        manifest.validate_required_keys(key_authority(_key_ring(KeyState.RETIRED), KEY))


def test_mutant_mount_certificate_inferred_from_filesystem_name_is_killed() -> None:
    certificate = _mount()
    assert certificate.filesystem_type == "ext4"
    assert not certificate.supports(MountPrimitive.ATOMIC_RENAME)
    with pytest.raises(ContractValidationError, match="UNPROVEN"):
        replace(certificate, supported_primitive_bitset=int(MountPrimitive.ATOMIC_RENAME))


def test_mutant_snapshot_authority_exclusion_or_sealed_source_flag_removed_is_killed() -> None:
    snapshot = WorkspaceUserSnapshot.seal(
        source_workspace_id=_opaque(WorkspaceId, "1"),
        source_incarnation_id=_opaque(IncarnationId, "2"),
        authority_epoch=AuthorityEpoch(3),
        head_revision=HeadRevision(4),
        included_entry_manifest_digest="5" * 64,
        excluded_authority_proof_digest="6" * 64,
        signing_key=_snapshot_signing_key(),
    )
    for field_name in ("authority_namespace_absent", "no_live_object_aliases", "sealed_source_identity"):
        with pytest.raises(ContractValidationError):
            replace(snapshot, **{field_name: False})


def test_mutant_snapshot_source_drift_ignored_is_killed() -> None:
    snapshot = WorkspaceUserSnapshot.seal(
        source_workspace_id=_opaque(WorkspaceId, "1"),
        source_incarnation_id=_opaque(IncarnationId, "2"),
        authority_epoch=AuthorityEpoch(3),
        head_revision=HeadRevision(4),
        included_entry_manifest_digest="5" * 64,
        excluded_authority_proof_digest="6" * 64,
        signing_key=_snapshot_signing_key(),
    )
    drifted = HeadState(
        workspace_id=snapshot.source_workspace_id,
        incarnation_id=snapshot.source_incarnation_id,
        authority_epoch=snapshot.authority_epoch,
        head_revision=HeadRevision(5),
        workspace_sequence=WorkspaceSequence(0),
        runtime_home_owner=RuntimeHomeId.new(),
        lifecycle=WorkspaceLifecycle.ACTIVE,
    )
    with pytest.raises(ContractValidationError, match="source drift"):
        snapshot.validate_source(drifted)


def test_mutant_backup_tombstone_closure_removed_is_killed() -> None:
    with pytest.raises(ContractValidationError, match="tombstone_inventory_digest"):
        replace(_backup_manifest(), tombstone_inventory_digest=None)  # type: ignore[arg-type]


def test_mutant_dormant_database_row_upgraded_to_execution_authority_is_killed(tmp_path: Path) -> None:
    path = tmp_path / "authority.sqlite3"
    database = DormantAuthorityDatabase(path)
    operation = DormantOperationRegistry().register(_binding(), "a" * 64).operation
    assert database.register_operation(operation).committed
    with sqlite3.connect(path) as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "UPDATE authority_phase0_operations SET execution_authority = 1 WHERE operation_id = ?",
            (str(operation.operation_id),),
        )
