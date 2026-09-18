"""Repair #2A follow-up #2: authority derivation must not read replaceable process state.

Trusted derivation reads only the sealed canonical specification captured when a type is
defined, through closure-private primitives.  Replacing an exported helper, a public class
constant (``PREFIX``/``MINIMUM``), an enum member's ``_value_``, or a public property must
never change an accepted authority value.
"""

from __future__ import annotations

import contextlib

import pytest

from core.workspace_authority_v3 import base as base_module
from core.workspace_authority_v3 import identity as identity_module
from core.workspace_authority_v3.base import (
    canonical_enum_value,
    sealed_authority_record_bytes,
    sealed_authority_record_digest,
)
from core.workspace_authority_v3.contracts import (
    AuthorityBudgetProfile,
    BackupManifestV3,
    BackupPredecessor,
    DormantKeyAuthority,
    EnrollmentClaimV3,
    KeyFamily,
    KeyGenerationMetadata,
    KeyReference,
    KeyRingMetadata,
    KeyState,
    RequiredKeyReference,
)
from core.workspace_authority_v3.database import DormantAuthorityDatabase, TransactionOutcome
from core.workspace_authority_v3.identity import (
    AuthorityClaimProvenance,
    AuthorityDomainId,
    AuthorityEpoch,
    BackupGenerationId,
    EnrollmentOperationId,
    HeadRevision,
    IncarnationId,
    InvocationIdentity,
    MutationId,
    OpaqueIdentity,
    OperationId,
    OperationIdentityBinding,
    RuntimeHomeId,
    WorkspaceEnrollmentProvenance,
    WorkspaceId,
    WorkspaceSequence,
    _BoundedCounter,
    canonical_binding_mutation_id,
    canonical_binding_operation_id,
    canonical_counter_value,
    canonical_identity_text,
    canonical_invocation_digest,
    canonical_operation_binding_digest,
    seal_canonical_enum,
)
from core.workspace_authority_v3.lifecycle import (
    DormantOperationRegistry,
    RecoveryDecisionResult,
    _dormant_operation_fingerprint,
    _head_state_fingerprint,
    enter_recovery,
    issue_recovery_decision,
)
from tests.workspace_authority_v3_fixtures import active_state_owner, ring_workspace_id

KEY = b"g" * 32
BUDGET_FIELDS = (
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
)


@contextlib.contextmanager
def module_attribute(module: object, name: str, value: object):
    original = getattr(module, name)
    setattr(module, name, value)
    try:
        yield
    finally:
        setattr(module, name, original)


@contextlib.contextmanager
def class_attribute(owner: type, name: str, value: object):
    had_own = name in owner.__dict__
    original = owner.__dict__.get(name)
    type.__setattr__(owner, name, value)
    try:
        yield
    finally:
        if had_own:
            type.__setattr__(owner, name, original)
        else:
            type.__delattr__(owner, name)


@contextlib.contextmanager
def member_attribute(member: object, name: str, value: object):
    original = member.__dict__[name]
    object.__setattr__(member, name, value)
    try:
        yield
    finally:
        object.__setattr__(member, name, original)


def _hostile(*_args: object, **_kwargs: object):
    raise AssertionError("a replaced public helper must never be reached by trusted derivation")


def _workspace() -> WorkspaceId:
    domain = AuthorityDomainId.from_authority_claim(AuthorityClaimProvenance.establish())
    return WorkspaceId.from_enrollment(
        WorkspaceEnrollmentProvenance.establish(
            authority_domain_id=domain,
            enrollment_operation_id=EnrollmentOperationId.new(),
        )
    )


def _binding(workspace: WorkspaceId | None = None) -> OperationIdentityBinding:
    return OperationIdentityBinding(
        invocation_identity=InvocationIdentity(f"turn_{'1' * 64}", 2, 3),
        workspace_id=_workspace() if workspace is None else workspace,
        authority_epoch=AuthorityEpoch(4),
        canonical_action_digest="a" * 64,
    )


def _authority(authority_domain: AuthorityDomainId | None = None) -> DormantKeyAuthority:
    metadata = KeyRingMetadata(
        (
            KeyGenerationMetadata(
                KeyFamily.CLAIM_ENROLLMENT, "claim-1", 1, KeyState.ACTIVE, "HMAC-SHA256", 1
            ),
            KeyGenerationMetadata(
                KeyFamily.BACKUP_MANIFEST, "backup-1", 1, KeyState.ACTIVE, "HMAC-SHA256", 1
            ),
        )
    )
    return DormantKeyAuthority(
        metadata,
        {
            KeyReference(KeyFamily.CLAIM_ENROLLMENT, "claim-1", 1): KEY,
            KeyReference(KeyFamily.BACKUP_MANIFEST, "backup-1", 1): KEY,
        },
        workspace_id=ring_workspace_id(),
        authority_domain=authority_domain,
    )


def _budget() -> AuthorityBudgetProfile:
    return AuthorityBudgetProfile(profile_version=3, **{name: 10_000 for name in BUDGET_FIELDS})


def _manifest(authority: DormantKeyAuthority) -> BackupManifestV3:
    workspace, incarnation = _workspace(), IncarnationId.new()
    return BackupManifestV3.issue(
        backup_generation_id=BackupGenerationId.new(),
        authority_generation=1,
        db_snapshot_identity_digest="1" * 64,
        workspace_id=workspace,
        incarnation_id=incarnation,
        authority_epoch=AuthorityEpoch(5),
        head_revision=HeadRevision(7),
        claim_digest="2" * 64,
        claim_key_generation=1,
        blob_inventory_digest="3" * 64,
        tombstone_inventory_digest="4" * 64,
        required_keys=(
            RequiredKeyReference(KeyFamily.CLAIM_ENROLLMENT, 1, "claim-1"),
            RequiredKeyReference(KeyFamily.BACKUP_MANIFEST, 1, "backup-1"),
        ),
        budget_profile_digest=_budget().digest(),
        predecessor=BackupPredecessor.genesis(
            workspace_id=workspace, incarnation_id=incarnation, authority_epoch=AuthorityEpoch(5)
        ),
        signing_key=authority.signing_capability(
            family=KeyFamily.BACKUP_MANIFEST, generation=1, key_id="backup-1"
        ),
    )


# ---------------------------------------------------------------------------
# 1-2. Exported canonical helper replacement.
# ---------------------------------------------------------------------------

EXPORTED_HELPERS = (
    "canonical_identity_text",
    "canonical_counter_value",
    "canonical_enum_value",
    "canonical_invocation_fields",
    "canonical_invocation_payload",
    "canonical_invocation_digest",
    "canonical_mutation_digest",
    "canonical_operation_binding_fields",
    "canonical_operation_binding_payload",
    "canonical_operation_binding_digest",
    "canonical_binding_operation_id",
    "canonical_binding_mutation_id",
)


@pytest.mark.parametrize("helper", EXPORTED_HELPERS)
def test_followup2_replacing_an_exported_helper_cannot_change_derived_identity(helper: str) -> None:
    binding = _binding()
    true_operation_id = canonical_binding_operation_id(binding)
    true_mutation_id = canonical_binding_mutation_id(binding)
    with module_attribute(identity_module, helper, _hostile):
        registration = DormantOperationRegistry().register(binding, "b" * 64)
        assert registration.operation.operation_id == true_operation_id
        assert registration.operation.mutation_id == true_mutation_id


def test_followup2_exported_helper_replacement_preserves_sqlite_replay(tmp_path) -> None:
    binding = _binding()
    true_operation_id = canonical_binding_operation_id(binding)
    database_path = tmp_path / "followup2-replay.sqlite3"

    with module_attribute(identity_module, "canonical_invocation_digest", lambda _invocation: "0" * 64):
        operation = DormantOperationRegistry().register(binding, "b" * 64).operation
        assert operation.operation_id == true_operation_id
        first = DormantAuthorityDatabase(database_path).register_operation(operation)
        assert first.outcome is TransactionOutcome.COMMITTED_NEW

    replayed = DormantAuthorityDatabase(database_path).register_operation(
        DormantOperationRegistry().register(binding, "b" * 64).operation
    )
    assert replayed.outcome is TransactionOutcome.COMMITTED_REPLAY
    assert replayed.value is not None
    assert replayed.value.operation_id == true_operation_id


def test_followup2_replacing_serialization_helpers_cannot_forge_canonical_bytes() -> None:
    record = KeyGenerationMetadata(
        KeyFamily.CLAIM_ENROLLMENT, "claim-1", 1, KeyState.ACTIVE, "HMAC-SHA256", 1
    )
    baseline = sealed_authority_record_bytes(record)
    original_encoder = base_module.encode_value

    def forging_encoder(value: object) -> object:
        encoded = original_encoder(value)
        return "FORGED" if encoded == "claim-1" else encoded

    with module_attribute(base_module, "encode_value", forging_encoder):
        constructed_under_substitution = KeyGenerationMetadata(
            KeyFamily.CLAIM_ENROLLMENT, "claim-1", 1, KeyState.ACTIVE, "HMAC-SHA256", 1
        )
        assert sealed_authority_record_bytes(constructed_under_substitution) == baseline
        assert sealed_authority_record_bytes(record) == baseline
    assert sealed_authority_record_bytes(record) == baseline


# ---------------------------------------------------------------------------
# 3-4. Mutable counter bounds.
# ---------------------------------------------------------------------------


def test_followup2_lowering_authority_epoch_minimum_cannot_admit_epoch_zero(tmp_path) -> None:
    with pytest.raises(ValueError):
        AuthorityEpoch(0)
    with class_attribute(AuthorityEpoch, "MINIMUM", 0):
        with pytest.raises(ValueError):
            AuthorityEpoch(0)
        assert AuthorityEpoch(1) == AuthorityEpoch(1)
    with pytest.raises(ValueError):
        AuthorityEpoch(0)


def test_followup2_raising_a_counter_maximum_cannot_enlarge_the_accepted_domain() -> None:
    beyond = 2**63
    with pytest.raises(ValueError):
        HeadRevision(beyond)
    for owner, name, value in (
        (HeadRevision, "MAXIMUM", beyond),
        (_BoundedCounter, "MAXIMUM", beyond),
        (HeadRevision, "MINIMUM", 0),
    ):
        with class_attribute(owner, name, value), pytest.raises(ValueError):
            HeadRevision(beyond)
    with class_attribute(_BoundedCounter, "MINIMUM", 99):
        assert canonical_counter_value(HeadRevision(4)) == 4
        assert HeadRevision(4) == HeadRevision(4)


def test_followup2_counter_bound_substitution_cannot_move_a_head_fingerprint() -> None:
    owner = active_state_owner(
        "followup2-head",
        incarnation_id=IncarnationId.new(),
        authority_epoch=AuthorityEpoch(3),
        head_revision=HeadRevision(4),
        workspace_sequence=WorkspaceSequence(5),
        runtime_home_owner=RuntimeHomeId.new(),
    )
    snapshot = owner.current.snapshot
    baseline = _head_state_fingerprint(snapshot)
    for target in (AuthorityEpoch, HeadRevision, WorkspaceSequence, _BoundedCounter):
        with class_attribute(target, "MINIMUM", 0):
            assert _head_state_fingerprint(snapshot) == baseline


# ---------------------------------------------------------------------------
# 5-6. Identity prefix specification.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("identity_type", "expected_prefix"),
    (
        (WorkspaceId, "workspace"),
        (IncarnationId, "incarnation"),
        (OperationId, "operation"),
        (MutationId, "mutation"),
        (RuntimeHomeId, "runtime-home"),
        (BackupGenerationId, "backup-generation"),
    ),
)
def test_followup2_prefix_substitution_cannot_change_issued_identity_semantics(
    identity_type: type,
    expected_prefix: str,
) -> None:
    with class_attribute(identity_type, "PREFIX", "forged-prefix"):
        if identity_type is WorkspaceId:
            issued = _workspace()
        else:
            issued = identity_type.new()
        text = canonical_identity_text(issued, identity_type)
        assert text.startswith(f"{expected_prefix}_")
        assert not text.startswith("forged-prefix")


def test_followup2_base_prefix_substitution_cannot_change_operation_derivation() -> None:
    binding = _binding()
    true_operation_id = canonical_binding_operation_id(binding)
    for owner in (OpaqueIdentity, OperationId, MutationId):
        with class_attribute(owner, "PREFIX", "forged-prefix"):
            assert DormantOperationRegistry().register(
                binding, "b" * 64
            ).operation.operation_id == true_operation_id


def test_followup2_enrollment_claim_survives_prefix_substitution() -> None:
    domain = AuthorityDomainId.from_authority_claim(AuthorityClaimProvenance.establish())
    authority = _authority(domain)
    enrollment_operation = EnrollmentOperationId.new()
    workspace = WorkspaceId.from_enrollment(
        WorkspaceEnrollmentProvenance.establish(
            authority_domain_id=domain, enrollment_operation_id=enrollment_operation
        )
    )
    verifier = authority.verification_capability(
        family=KeyFamily.CLAIM_ENROLLMENT, generation=1, key_id="claim-1"
    )
    with class_attribute(WorkspaceId, "PREFIX", "forged-workspace"):
        claim = EnrollmentClaimV3.issue(
            authority_domain_id=domain,
            workspace_id=workspace,
            incarnation_id=IncarnationId.new(),
            enrollment_operation_id=enrollment_operation,
            incarnation_nonce="1" * 64,
            persistent_volume_identity_digest="2" * 64,
            root_object_identity_digest="3" * 64,
            runtime_home_id=RuntimeHomeId.new(),
            signing_key=authority.signing_capability(
                family=KeyFamily.CLAIM_ENROLLMENT, generation=1, key_id="claim-1"
            ),
        )
        assert claim.to_record()["payload"]["workspace_id"].startswith("workspace_")
    decoded = EnrollmentClaimV3.from_json(claim.canonical_bytes(), verification_key=verifier)
    assert decoded.workspace_id == workspace


# ---------------------------------------------------------------------------
# 7. Enum canonical value specification.
# ---------------------------------------------------------------------------


def test_followup2_enum_member_value_substitution_cannot_change_key_authentication() -> None:
    authority = _authority()
    signer = authority.signing_capability(
        family=KeyFamily.CLAIM_ENROLLMENT, generation=1, key_id="claim-1"
    )
    verifier = authority.verification_capability(
        family=KeyFamily.CLAIM_ENROLLMENT, generation=1, key_id="claim-1"
    )
    baseline = signer.sign(
        expected_family=KeyFamily.CLAIM_ENROLLMENT, domain="VOOL_FOLLOWUP2_V3", message=b"payload"
    )
    for member, name in (
        (KeyFamily.CLAIM_ENROLLMENT, "_value_"),
        (KeyState.ACTIVE, "_value_"),
    ):
        with member_attribute(member, name, "FORGED"):
            assert (
                signer.sign(
                    expected_family=KeyFamily.CLAIM_ENROLLMENT,
                    domain="VOOL_FOLLOWUP2_V3",
                    message=b"payload",
                )
                == baseline
            )
            assert verifier.verify(
                expected_family=KeyFamily.CLAIM_ENROLLMENT,
                domain="VOOL_FOLLOWUP2_V3",
                message=b"payload",
                supplied_mac=baseline,
            )
    assert canonical_enum_value(KeyFamily.CLAIM_ENROLLMENT) == "CLAIM_ENROLLMENT"


def test_followup2_enum_value_substitution_cannot_move_serialized_records() -> None:
    record = KeyGenerationMetadata(
        KeyFamily.CLAIM_ENROLLMENT, "claim-1", 1, KeyState.ACTIVE, "HMAC-SHA256", 1
    )
    baseline = sealed_authority_record_bytes(record)
    with member_attribute(KeyFamily.CLAIM_ENROLLMENT, "_value_", "FORGED_FAMILY"):
        assert sealed_authority_record_bytes(record) == baseline
    with member_attribute(KeyState.ACTIVE, "_value_", "FORGED_STATE"):
        assert sealed_authority_record_bytes(record) == baseline


def test_followup2_enum_canonical_values_are_sealed_once() -> None:
    with pytest.raises(TypeError, match="already sealed"):
        seal_canonical_enum(KeyFamily)


# ---------------------------------------------------------------------------
# 8. Closed key metadata access.
# ---------------------------------------------------------------------------


def test_followup2_metadata_property_substitution_cannot_revive_a_retired_required_key() -> None:
    authority = _authority()
    manifest = _manifest(authority)
    manifest.validate_required_keys(authority)
    active_snapshot = authority.metadata

    authority.install_metadata(
        KeyRingMetadata(
            (
                KeyGenerationMetadata(
                    KeyFamily.CLAIM_ENROLLMENT, "claim-1", 1, KeyState.ACTIVE, "HMAC-SHA256", 1
                ),
                KeyGenerationMetadata(
                    KeyFamily.BACKUP_MANIFEST, "backup-1", 1, KeyState.RETIRED, "HMAC-SHA256", 1
                ),
            )
        )
    )
    with pytest.raises(ValueError, match="STALE_KEY_BACKUP"):
        manifest.validate_required_keys(authority)

    with class_attribute(DormantKeyAuthority, "metadata", property(lambda self: active_snapshot)):
        with pytest.raises(ValueError, match="STALE_KEY_BACKUP"):
            manifest.validate_required_keys(authority)


def test_followup2_metadata_property_substitution_cannot_validate_a_backup_closure() -> None:
    authority = _authority()
    manifest = _manifest(authority)
    profile = _budget()
    verifier = authority.verification_capability(
        family=KeyFamily.BACKUP_MANIFEST, generation=1, key_id="backup-1"
    )
    closure = manifest.validate_closure(
        key_authority=authority,
        verification_key=verifier,
        budget_profile=profile,
        previous_closure=None,
    )
    assert closure.manifest is manifest
    active_snapshot = authority.metadata
    authority.install_metadata(
        KeyRingMetadata(
            (
                KeyGenerationMetadata(
                    KeyFamily.CLAIM_ENROLLMENT, "claim-1", 1, KeyState.ACTIVE, "HMAC-SHA256", 1
                ),
                KeyGenerationMetadata(
                    KeyFamily.BACKUP_MANIFEST, "backup-1", 1, KeyState.REVOKED_COMPROMISED, "HMAC-SHA256", 1
                ),
            )
        )
    )
    with class_attribute(DormantKeyAuthority, "metadata", property(lambda self: active_snapshot)):
        with pytest.raises(ValueError):
            manifest.validate_closure(
                key_authority=authority,
                verification_key=verifier,
                budget_profile=profile,
                previous_closure=None,
            )


# ---------------------------------------------------------------------------
# 9. Public wrappers cannot move any authority-bearing output.
# ---------------------------------------------------------------------------


def test_followup2_public_wrapper_replacement_cannot_move_authority_bearing_output(tmp_path) -> None:
    authority = _authority()
    signer = authority.signing_capability(
        family=KeyFamily.CLAIM_ENROLLMENT, generation=1, key_id="claim-1"
    )
    manifest = _manifest(authority)
    binding = _binding()
    operation = DormantOperationRegistry().register(binding, "b" * 64).operation
    owner = active_state_owner(
        "followup2-wrappers",
        incarnation_id=IncarnationId.new(),
        authority_epoch=AuthorityEpoch(3),
        head_revision=HeadRevision(4),
        workspace_sequence=WorkspaceSequence(5),
        runtime_home_owner=RuntimeHomeId.new(),
    )
    recovery = enter_recovery(owner.current)
    decision = issue_recovery_decision(
        recovery, result=RecoveryDecisionResult.REACTIVATE, evidence_digest="d" * 64
    )
    probes = {
        "manifest_bytes": lambda: sealed_authority_record_bytes(manifest),
        "ring_bytes": lambda: sealed_authority_record_bytes(authority.metadata),
        "decision_digest": lambda: sealed_authority_record_digest(decision),
        "operation_fingerprint": lambda: _dormant_operation_fingerprint(operation),
        "head_fingerprint": lambda: _head_state_fingerprint(recovery.snapshot),
        "binding_digest": lambda: canonical_operation_binding_digest(binding),
        "invocation_digest": lambda: canonical_invocation_digest(binding.invocation_identity),
        "key_mac": lambda: signer.sign(
            expected_family=KeyFamily.CLAIM_ENROLLMENT,
            domain="VOOL_FOLLOWUP2_V3",
            message=b"payload",
        ),
        "registry_operation_id": lambda: DormantOperationRegistry()
        .register(binding, "b" * 64)
        .operation.operation_id,
    }
    baseline = {name: probe() for name, probe in probes.items()}

    for helper in EXPORTED_HELPERS:
        if not hasattr(identity_module, helper):
            continue
        with module_attribute(identity_module, helper, _hostile):
            for name, probe in probes.items():
                assert probe() == baseline[name], f"identity.{helper} moved {name}"
    with module_attribute(base_module, "canonical_enum_value", _hostile):
        for name, probe in probes.items():
            assert probe() == baseline[name], f"base.canonical_enum_value moved {name}"


# ---------------------------------------------------------------------------
# 10. Normal behaviour is unchanged.
# ---------------------------------------------------------------------------


def test_followup2_public_helpers_and_display_behave_normally_without_substitution() -> None:
    workspace = _workspace()
    epoch = AuthorityEpoch(7)
    binding = _binding(workspace)
    assert canonical_identity_text(workspace, WorkspaceId) == str(workspace)
    assert canonical_counter_value(epoch) == int(epoch) == 7
    assert canonical_enum_value(KeyFamily.CURSOR) == KeyFamily.CURSOR.value
    assert canonical_invocation_digest(binding.invocation_identity) == (
        binding.invocation_identity.digest()
    )
    assert canonical_operation_binding_digest(binding) == binding.digest()
    assert canonical_binding_operation_id(binding) == binding.operation_id()
    assert WorkspaceId.PREFIX == "workspace"
    assert AuthorityEpoch.MINIMUM == 1


def test_followup2_sealed_specification_rejects_unknown_and_duplicate_types() -> None:
    with pytest.raises(TypeError, match="requires an authority counter"):
        canonical_counter_value(object())
    with pytest.raises(TypeError):
        canonical_identity_text(object(), WorkspaceId)
    with pytest.raises(TypeError, match="already sealed"):
        seal_canonical_enum(KeyState)

    with pytest.raises(TypeError, match="fixed prefix"):

        class MissingPrefix(OpaqueIdentity):
            pass

    with pytest.raises(TypeError, match="already claimed"):

        class DuplicatePrefix(OpaqueIdentity):
            PREFIX = "workspace"
