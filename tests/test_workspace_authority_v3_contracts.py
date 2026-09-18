from __future__ import annotations

from dataclasses import fields, replace
from functools import cache

import pytest

from core.workspace_authority_v3 import EXECUTION_AUTHORITY, PHASE
from core.workspace_authority_v3.base import AuthorityPhase, ContractValidationError
from core.workspace_authority_v3.canonical import canonical_bytes
from core.workspace_authority_v3.contracts import (
    AuthorityBudgetProfile,
    AuthorityBudgetRequest,
    AuthorityFailure,
    BackupGenerationContract,
    BackupGenerationState,
    BackupManifestV3,
    BackupPredecessor,
    CapabilityEvidenceState,
    EnrollmentClaimV3,
    FailureCode,
    KeyFamily,
    KeyGenerationMetadata,
    KeyRingMetadata,
    KeyState,
    MountCertificate,
    MountPrimitive,
    RequiredKeyReference,
    SafeFailureContext,
    WorkspaceUserSnapshot,
    validate_backup_transition,
)
from core.workspace_authority_v3.cursor import (
    AuthenticatedCursorPayload,
    CursorCodec,
    CursorDirection,
    CursorError,
    CursorExpectation,
    CursorTokenTooLargeError,
)
from core.workspace_authority_v3.identity import (
    AuthorityEpoch,
    BackupGenerationId,
    HeadRevision,
    IncarnationId,
    OperationId,
    RuntimeHomeId,
    WorkspaceId,
    WorkspaceSequence,
)
from core.workspace_authority_v3.lifecycle import HeadState, WorkspaceLifecycle
from core.workspace_authority_v3.path_policy import (
    AuthorityInternalTransition,
    NormalPlan,
    NormalPlanPath,
    ReservedNamespaceError,
    validate_normal_plan_path,
)
from tests.workspace_authority_v3_fixtures import enrollment_root, key_authority, owned_identity

KEY = b"k" * 32


def _opaque(identity_type: type, character: str):
    return owned_identity(identity_type, character)


@cache
def _shared_authority():
    """One authority per logical test ring; signing and verification share it.

    Anchored to the same authority domain the shared claim names, because a ring may only
    authenticate records for the domain it is entitled to.
    """

    return key_authority(
        _key_ring(),
        KEY,
        authority_domain=enrollment_root("contracts-claim")[0],
    )


def _claim() -> EnrollmentClaimV3:
    authority_domain_id, workspace_id, enrollment_operation_id = enrollment_root("contracts-claim")
    signing_key = _shared_authority().signing_capability(
        family=KeyFamily.CLAIM_ENROLLMENT,
        generation=2,
        key_id="claim_enrollment-2",
    )
    return EnrollmentClaimV3.issue(
        authority_domain_id=authority_domain_id,
        workspace_id=workspace_id,
        incarnation_id=_opaque(IncarnationId, "3"),
        enrollment_operation_id=enrollment_operation_id,
        incarnation_nonce="5" * 64,
        persistent_volume_identity_digest="6" * 64,
        root_object_identity_digest="7" * 64,
        runtime_home_id=_opaque(RuntimeHomeId, "8"),
        signing_key=signing_key,
    )


def _key_metadata(family: KeyFamily, generation: int, state: KeyState) -> KeyGenerationMetadata:
    return KeyGenerationMetadata(
        family=family,
        key_id=f"{family.value.lower()}-{generation}",
        generation=generation,
        state=state,
        algorithm="HMAC-SHA256",
        algorithm_version=1,
    )


def _key_ring(*, old_claim_state: KeyState = KeyState.VERIFY_ONLY) -> KeyRingMetadata:
    generations = [_key_metadata(KeyFamily.CLAIM_ENROLLMENT, 1, old_claim_state)]
    generations.extend(
        _key_metadata(
            family,
            2,
            KeyState.PENDING if family is KeyFamily.JOURNAL_EFFECT_RECEIPT else KeyState.ACTIVE,
        )
        for family in KeyFamily
    )
    return KeyRingMetadata(tuple(generations))


def _profile(**overrides: int) -> AuthorityBudgetProfile:
    values = {field_name: 10_000 for field_name in (
        "raw_before_bytes_per_file",
        "staged_after_bytes_per_file",
        "mutation_bytes",
        "entries",
        "directory_manifest_work",
        "path_bytes",
        "retained_undo_bytes",
        "staging_bytes",
        "tombstone_bytes",
        "domain_storage_bytes",
        "backup_generation_bytes",
        "worker_snapshot_bytes",
        "required_free_space_bytes",
        "change_page_size",
        "preview_work_bytes",
        "stored_preview_bytes",
    )}
    values.update(overrides)
    return AuthorityBudgetProfile(profile_version=3, **values)


def _manifest(*, claim_generation: int = 2, tombstone_digest: str = "d" * 64) -> BackupManifestV3:
    workspace_id = _opaque(WorkspaceId, "2")
    incarnation_id = _opaque(IncarnationId, "3")
    authority_epoch = AuthorityEpoch(4)
    authority = key_authority(_key_ring(), KEY)
    signing_key = authority.signing_capability(
        family=KeyFamily.BACKUP_MANIFEST,
        generation=2,
        key_id="backup_manifest-2",
    )
    return BackupManifestV3.issue(
        backup_generation_id=_opaque(BackupGenerationId, "1"),
        authority_generation=3,
        db_snapshot_identity_digest="a" * 64,
        workspace_id=workspace_id,
        incarnation_id=incarnation_id,
        authority_epoch=authority_epoch,
        head_revision=HeadRevision(5),
        claim_digest="b" * 64,
        claim_key_generation=claim_generation,
        blob_inventory_digest="c" * 64,
        tombstone_inventory_digest=tombstone_digest,
        required_keys=(
            RequiredKeyReference(
                KeyFamily.CLAIM_ENROLLMENT,
                claim_generation,
                f"claim_enrollment-{claim_generation}",
            ),
            RequiredKeyReference(KeyFamily.BACKUP_MANIFEST, 2, "backup_manifest-2"),
        ),
        budget_profile_digest=_profile().digest(),
        predecessor=BackupPredecessor.completed(
            workspace_id=workspace_id,
            incarnation_id=incarnation_id,
            authority_epoch=authority_epoch,
            authority_generation=2,
            manifest_digest="e" * 64,
        ),
        signing_key=signing_key,
    )


def test_enrollment_claim_is_canonical_authenticated_and_non_executing() -> None:
    claim = _claim()
    ring = _key_ring()
    verifier = _shared_authority().verification_capability(
        family=KeyFamily.CLAIM_ENROLLMENT,
        generation=2,
        key_id="claim_enrollment-2",
    )
    wrong_verifier = key_authority(ring, b"x" * 32).verification_capability(
        family=KeyFamily.CLAIM_ENROLLMENT,
        generation=2,
        key_id="claim_enrollment-2",
    )
    assert claim.verify_mac(verifier)
    assert not claim.verify_mac(wrong_verifier)
    assert EnrollmentClaimV3.from_json(claim.canonical_bytes(), verification_key=verifier) == claim
    assert claim.to_record()["authority_metadata"]["phase"] == "DORMANT_PHASE0"
    assert claim.to_record()["authority_metadata"]["execution_authority"] is False
    tampered = dict(claim.to_record())
    tampered["payload"] = dict(tampered["payload"])
    tampered["payload"]["workspace_id"] = str(_opaque(WorkspaceId, "9"))
    with pytest.raises(ContractValidationError, match="claim_digest"):
        EnrollmentClaimV3.from_json(canonical_bytes(tampered), verification_key=verifier)


def test_enrollment_claim_refuses_shadow_upgrade_to_execution_authority() -> None:
    raw = dict(_claim().to_record())
    raw["authority_metadata"] = dict(raw["authority_metadata"])
    raw["authority_metadata"]["execution_authority"] = True
    with pytest.raises(ContractValidationError, match="not dormant"):
        EnrollmentClaimV3.from_json(
            canonical_bytes(raw),
            verification_key=_shared_authority().verification_capability(
                family=KeyFamily.CLAIM_ENROLLMENT,
                generation=2,
                key_id="claim_enrollment-2",
            ),
        )


def test_key_ring_allows_unavailable_families_but_never_multiple_active_generations() -> None:
    ring = _key_ring()
    assert not any(
        item.family is KeyFamily.JOURNAL_EFFECT_RECEIPT and item.state is KeyState.ACTIVE
        for item in ring.generations
    )
    duplicate_active = (*ring.generations, _key_metadata(KeyFamily.CURSOR, 3, KeyState.ACTIVE))
    with pytest.raises(ContractValidationError, match="at most one ACTIVE"):
        KeyRingMetadata(duplicate_active)
    missing_family = tuple(item for item in ring.generations if item.family is not KeyFamily.CURSOR)
    assert KeyRingMetadata(missing_family).find(KeyFamily.CURSOR, 2) is None
    with pytest.raises(TypeError, match="immutable tuple"):
        KeyRingMetadata(list(ring.generations))  # type: ignore[arg-type]


def test_key_metadata_has_no_plaintext_export_or_secret_material_field() -> None:
    field_names = {field.name for field in fields(KeyGenerationMetadata)}
    assert field_names.isdisjoint({"key", "secret", "private_key", "plaintext", "material"})


def test_budget_reservation_checks_every_dimension_and_combined_overflow() -> None:
    profile = _profile(domain_storage_bytes=40_000)
    request = AuthorityBudgetRequest(
        mutation_bytes=100,
        retained_undo_bytes=1_000,
        staging_bytes=2_000,
        tombstone_bytes=3_000,
    )
    reservation = profile.reserve(request)
    assert reservation.profile_digest == profile.digest()
    profile.validate_reservation(reservation)
    with pytest.raises(ContractValidationError, match="mutation_bytes"):
        profile.reserve(replace(request, mutation_bytes=10_001))
    with pytest.raises(ContractValidationError, match="overflows"):
        AuthorityBudgetRequest(retained_undo_bytes=2**63 - 1, staging_bytes=1)


def test_mount_certificate_never_infers_support_from_filesystem_name() -> None:
    common = dict(
        certificate_id="mount-cert-1",
        adapter_id="posix-probe",
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
        generation=1,
        probe_suite_digest="4" * 64,
    )
    unproven = MountCertificate(
        **common,
        supported_primitive_bitset=0,
        evidence_state=CapabilityEvidenceState.UNPROVEN,
    )
    assert not unproven.supports(MountPrimitive.ATOMIC_RENAME)
    with pytest.raises(ContractValidationError, match="UNPROVEN"):
        MountCertificate(
            **common,
            supported_primitive_bitset=int(MountPrimitive.ATOMIC_RENAME),
            evidence_state=CapabilityEvidenceState.UNPROVEN,
        )
    proven = replace(
        unproven,
        supported_primitive_bitset=int(MountPrimitive.ATOMIC_RENAME | MountPrimitive.DIRECTORY_FSYNC),
        evidence_state=CapabilityEvidenceState.PROVEN,
    )
    assert proven.supports(MountPrimitive.ATOMIC_RENAME)


@pytest.mark.parametrize(
    "hostile_path",
    (
        ".vool-authority",
        ".VOOL-AUTHORITY",
        ".ｖｏｏｌ－ａｕｔｈｏｒｉｔｙ",
        ".vool-authority.",
        ".vool-authority ",
        ".vool-authority:$DATA",
        "VOOL-A~1",
        "nested/.vool-authority/claim",
        "\\\\?\\C:\\repo\\.vool-authority",
        "..\\.vool-authority",
        ".",
    ),
)
def test_normal_plans_reject_all_reserved_namespace_alias_classes(hostile_path: str) -> None:
    with pytest.raises(ReservedNamespaceError, match="REJECTED_RESERVED_NAMESPACE"):
        validate_normal_plan_path(hostile_path, destructive=True)


def test_normal_and_internal_plan_types_are_distinct_without_internal_boolean() -> None:
    normal = NormalPlan("a" * 64, (NormalPlanPath("src/file.py"),))
    internal = AuthorityInternalTransition("b" * 64)
    assert type(normal) is not type(internal)
    assert "internal" not in {field.name for field in fields(NormalPlan)}
    with pytest.raises(TypeError, match="NormalPlanPath"):
        NormalPlan("c" * 64, [NormalPlanPath("src/file.py")])  # type: ignore[arg-type]


def _cursor_payload(**overrides: object) -> AuthenticatedCursorPayload:
    values = {
        "principal_id": "principal-1",
        "workspace_id": _opaque(WorkspaceId, "1"),
        "incarnation_id": _opaque(IncarnationId, "2"),
        "authority_epoch": AuthorityEpoch(3),
        "query_digest": "4" * 64,
        "direction": CursorDirection.FORWARD,
        "page_size": 50,
        "highwater": WorkspaceSequence(10_000),
        "exclusive_boundary": "sequence:9000",
        "projection_version": 3,
        "issued_at_unix_ms": 100,
        "expires_at_unix_ms": 200,
        "key_generation": 7,
    }
    values.update(overrides)
    return AuthenticatedCursorPayload(**values)


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


def _cursor_signing_key():
    return _shared_authority().signing_capability(
        family=KeyFamily.CURSOR,
        generation=2,
        key_id="cursor-2",
    )


def _cursor_verification_key():
    return _shared_authority().verification_capability(
        family=KeyFamily.CURSOR,
        generation=2,
        key_id="cursor-2",
    )


def test_cursor_round_trip_binds_owner_workspace_incarnation_epoch_and_query() -> None:
    payload = _cursor_payload()
    payload = replace(payload, key_generation=2)
    signing_codec = CursorCodec(key=_cursor_signing_key())
    verification_codec = CursorCodec(key=_cursor_verification_key())
    token = signing_codec.encode(payload)
    assert verification_codec.decode(token, expectation=_cursor_expectation(payload), now_unix_ms=150) == payload
    hostile_expectations = (
        replace(_cursor_expectation(payload), principal_id="principal-2"),
        replace(_cursor_expectation(payload), workspace_id=_opaque(WorkspaceId, "9")),
        replace(_cursor_expectation(payload), incarnation_id=_opaque(IncarnationId, "9")),
        replace(_cursor_expectation(payload), authority_epoch=AuthorityEpoch(4)),
        replace(_cursor_expectation(payload), query_digest="9" * 64),
    )
    for expectation in hostile_expectations:
        with pytest.raises(CursorError):
            verification_codec.decode(token, expectation=expectation, now_unix_ms=150)


def test_cursor_schema_rejects_omitted_owner_and_tampered_generation() -> None:
    payload = _cursor_payload()
    raw = payload.to_record()
    del raw["payload"]["principal_id"]
    with pytest.raises(TypeError, match="verification_key"):
        AuthenticatedCursorPayload.from_record(raw)
    with pytest.raises(CursorError, match="generation"):
        CursorCodec(key=_cursor_signing_key()).encode(payload)


def test_oversized_cursor_is_rejected_before_decode_or_mac_work() -> None:
    class CountingCodec(CursorCodec):
        authentication_calls = 0

        def _authenticate(self, payload: bytes) -> str:
            self.authentication_calls += 1
            return super()._authenticate(payload)

    codec = CountingCodec(key=_cursor_signing_key(), max_token_bytes=100)
    with pytest.raises(CursorTokenTooLargeError):
        codec.decode("A" * 101, expectation=_cursor_expectation(_cursor_payload()), now_unix_ms=150)
    assert codec.authentication_calls == 0


def test_backup_manifest_binds_tombstone_closure_and_exact_key_generations() -> None:
    manifest = _manifest()
    manifest.validate_required_keys(key_authority(_key_ring(), KEY))
    stale = _manifest(claim_generation=1)
    stale.validate_required_keys(key_authority(_key_ring(), KEY))
    with pytest.raises(ContractValidationError, match="STALE_KEY_BACKUP"):
        stale.validate_required_keys(key_authority(_key_ring(old_claim_state=KeyState.RETIRED), KEY))
    with pytest.raises(TypeError, match="tombstone_inventory_digest"):
        BackupManifestV3(
            backup_generation_id=_opaque(BackupGenerationId, "1"),
            authority_generation=3,
            db_snapshot_identity_digest="a" * 64,
            workspace_id=_opaque(WorkspaceId, "2"),
            incarnation_id=_opaque(IncarnationId, "3"),
            authority_epoch=AuthorityEpoch(4),
            head_revision=HeadRevision(5),
            claim_digest="b" * 64,
            claim_key_generation=2,
            blob_inventory_digest="c" * 64,
            required_keys=(),
            budget_profile_digest="e" * 64,
        )


def test_backup_state_machine_is_strict_and_only_sealed_state_has_manifest() -> None:
    validate_backup_transition(BackupGenerationState.REQUESTED, BackupGenerationState.FROZEN_AND_PINNED)
    with pytest.raises(ContractValidationError):
        validate_backup_transition(BackupGenerationState.REQUESTED, BackupGenerationState.SEALED_COMPLETE)
    with pytest.raises(ContractValidationError, match="requires"):
        BackupGenerationContract(_opaque(BackupGenerationId, "1"), BackupGenerationState.SEALED_COMPLETE, None)


def test_worker_snapshot_requires_exclusion_proof_and_source_stability() -> None:
    authority = key_authority(_key_ring(), KEY)
    signer = authority.signing_capability(
        family=KeyFamily.WORKER_SNAPSHOT,
        generation=2,
        key_id="worker_snapshot-2",
    )
    snapshot = WorkspaceUserSnapshot.seal(
        source_workspace_id=_opaque(WorkspaceId, "1"),
        source_incarnation_id=_opaque(IncarnationId, "2"),
        authority_epoch=AuthorityEpoch(3),
        head_revision=HeadRevision(4),
        included_entry_manifest_digest="5" * 64,
        excluded_authority_proof_digest="6" * 64,
        signing_key=signer,
    )
    assert snapshot.verify_mac(
        authority.verification_capability(
            family=KeyFamily.WORKER_SNAPSHOT,
            generation=2,
            key_id="worker_snapshot-2",
        )
    )
    source = HeadState(
        snapshot.source_workspace_id,
        snapshot.source_incarnation_id,
        snapshot.authority_epoch,
        snapshot.head_revision,
        WorkspaceSequence(100),
        _opaque(RuntimeHomeId, "7"),
        WorkspaceLifecycle.ACTIVE,
    )
    snapshot.validate_source(source)
    with pytest.raises(ContractValidationError, match="source drift"):
        snapshot.validate_source(replace(source, head_revision=HeadRevision(5)))
    with pytest.raises(ContractValidationError, match="exclude authority"):
        replace(snapshot, authority_namespace_absent=False)


def test_failures_have_stable_codes_and_cannot_serialize_raw_paths_or_secrets() -> None:
    failure = AuthorityFailure(
        FailureCode.IDEMPOTENCY_CONFLICT,
        False,
        SafeFailureContext(operation_id=_opaque(OperationId, "1")),
    )
    assert failure.to_safe_record()["code"] == "IDEMPOTENCY_CONFLICT"
    with pytest.raises(TypeError, match="WorkspaceId"):
        SafeFailureContext(workspace_id="/private/secret")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="SafeFailureContext"):
        AuthorityFailure(FailureCode.OWNERSHIP_KEY_UNAVAILABLE, False, (("secret", "hunter2"),))  # type: ignore[arg-type]


def test_package_level_dormant_gate_is_fixed() -> None:
    assert PHASE is AuthorityPhase.DORMANT_PHASE0
    assert EXECUTION_AUTHORITY is False
