"""Check-3 re-review repair: backup-generation sealing binds the validated manifest.

``BackupGenerationContract.seal()`` late-read ``closure.manifest.backup_generation_id``
and ``closure.manifest.manifest_digest`` through public ``BackupManifestV3`` descriptors
*after* a valid ``ValidatedBackupClosure`` already existed, so replacing either descriptor
on the class could redirect the sealing transition -- writing an attacker-chosen digest
into the sealed contract, or satisfying the generation-identity check that should fail.

Sealing now reaches the manifest through the closure's own sealed field and reads the
manifest's identity and digest through descriptors captured at import.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace as dataclass_replace

import pytest

from core.workspace_authority_v3.contracts import (
    AuthorityBudgetProfile,
    BackupGenerationContract,
    BackupGenerationState,
    BackupManifestV3,
    BackupPredecessor,
    DormantKeyAuthority,
    KeyFamily,
    KeyGenerationMetadata,
    KeyReference,
    KeyRingMetadata,
    KeyState,
    RequiredKeyReference,
    ValidatedBackupClosure,
)
from core.workspace_authority_v3.identity import (
    AuthorityClaimProvenance,
    AuthorityDomainId,
    AuthorityEpoch,
    BackupGenerationId,
    EnrollmentOperationId,
    HeadRevision,
    IncarnationId,
    WorkspaceEnrollmentProvenance,
    WorkspaceId,
)

KEY = b"s" * 32
FORGED_DIGEST = "a" * 64
WRONG_DIGEST = "9" * 64

_BUDGET_FIELD_NAMES = (
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


@contextmanager
def substituted(owner: type, name: str, value: object) -> Iterator[None]:
    had_own = name in owner.__dict__
    original = owner.__dict__.get(name)
    setattr(owner, name, value)
    try:
        yield
    finally:
        if had_own:
            setattr(owner, name, original)
        else:
            delattr(owner, name)


def _ring_workspace() -> WorkspaceId:
    domain = AuthorityDomainId.from_authority_claim(AuthorityClaimProvenance.establish())
    return WorkspaceId.from_enrollment(
        WorkspaceEnrollmentProvenance.establish(
            authority_domain_id=domain,
            enrollment_operation_id=EnrollmentOperationId.new(),
        )
    )


class _Backup:
    """A validated genesis closure plus its ARTIFACTS_COPIED contract."""

    def __init__(self) -> None:
        self.claim = KeyGenerationMetadata(
            KeyFamily.CLAIM_ENROLLMENT, "claim-1", 1, KeyState.ACTIVE, "HMAC-SHA256", 1
        )
        self.backup = KeyGenerationMetadata(
            KeyFamily.BACKUP_MANIFEST, "backup-1", 1, KeyState.ACTIVE, "HMAC-SHA256", 1
        )
        self.authority = DormantKeyAuthority(
            KeyRingMetadata((self.claim, self.backup)),
            {
                KeyReference(item.family, item.key_id, item.generation): KEY
                for item in (self.claim, self.backup)
            },
            workspace_id=_ring_workspace(),
        )
        self.workspace_id = _ring_workspace()
        self.incarnation_id = IncarnationId.new()
        self.epoch = AuthorityEpoch(5)
        self.profile = AuthorityBudgetProfile(
            profile_version=3, **dict.fromkeys(_BUDGET_FIELD_NAMES, 10_000)
        )
        self.required = (
            RequiredKeyReference(KeyFamily.CLAIM_ENROLLMENT, 1, "claim-1"),
            RequiredKeyReference(KeyFamily.BACKUP_MANIFEST, 1, "backup-1"),
        )
        self.signing_key = self.authority.signing_capability(
            family=KeyFamily.BACKUP_MANIFEST, generation=1, key_id="backup-1"
        )
        self.verification_key = self.authority.verification_capability(
            family=KeyFamily.BACKUP_MANIFEST, generation=1, key_id="backup-1"
        )
        self.generation_id = BackupGenerationId.new()
        self.manifest = self.issue(self.generation_id, 1, self.genesis(), head=7, blob="3" * 64)
        self.closure = self.validate(self.manifest, None)
        self.contract = BackupGenerationContract(
            self.generation_id, BackupGenerationState.ARTIFACTS_COPIED, None
        )

    def genesis(self) -> BackupPredecessor:
        return BackupPredecessor.genesis(
            workspace_id=self.workspace_id,
            incarnation_id=self.incarnation_id,
            authority_epoch=self.epoch,
        )

    def completed(self, digest: str, generation: int = 1) -> BackupPredecessor:
        return BackupPredecessor.completed(
            workspace_id=self.workspace_id,
            incarnation_id=self.incarnation_id,
            authority_epoch=self.epoch,
            authority_generation=generation,
            manifest_digest=digest,
        )

    def issue(
        self,
        generation_id: BackupGenerationId,
        generation: int,
        predecessor: BackupPredecessor,
        *,
        head: int,
        blob: str,
    ) -> BackupManifestV3:
        return BackupManifestV3.issue(
            backup_generation_id=generation_id,
            authority_generation=generation,
            db_snapshot_identity_digest="1" * 64,
            workspace_id=self.workspace_id,
            incarnation_id=self.incarnation_id,
            authority_epoch=self.epoch,
            head_revision=HeadRevision(head),
            claim_digest="2" * 64,
            claim_key_generation=1,
            blob_inventory_digest=blob,
            tombstone_inventory_digest="4" * 64,
            required_keys=self.required,
            budget_profile_digest=self.profile.digest(),
            predecessor=predecessor,
            signing_key=self.signing_key,
        )

    def issue_generation_two(self, predecessor_digest: str) -> BackupManifestV3:
        return BackupManifestV3.issue(
            backup_generation_id=BackupGenerationId.new(),
            authority_generation=2,
            db_snapshot_identity_digest="1" * 64,
            workspace_id=self.workspace_id,
            incarnation_id=self.incarnation_id,
            authority_epoch=self.epoch,
            head_revision=HeadRevision(8),
            claim_digest="2" * 64,
            claim_key_generation=1,
            blob_inventory_digest="7" * 64,
            tombstone_inventory_digest="4" * 64,
            required_keys=self.required,
            budget_profile_digest=self.profile.digest(),
            predecessor=self.completed(predecessor_digest),
            signing_key=self.signing_key,
        )

    def validate(
        self,
        manifest: BackupManifestV3,
        previous: ValidatedBackupClosure | None,
    ) -> ValidatedBackupClosure:
        return manifest.validate_closure(
            key_authority=self.authority,
            verification_key=self.verification_key,
            budget_profile=self.profile,
            previous_closure=previous,
        )


@pytest.fixture()
def backup() -> _Backup:
    return _Backup()


# ---------------------------------------------------------------------------
# The reported defect.
# ---------------------------------------------------------------------------


def test_check3_manifest_digest_property_cannot_redirect_the_seal(backup: _Backup) -> None:
    real_digest = backup.manifest.manifest_digest
    assert (
        BackupGenerationContract.seal(backup.contract, backup.closure).manifest_digest
        == real_digest
    )

    with substituted(BackupManifestV3, "manifest_digest", property(lambda self: FORGED_DIGEST)):
        assert backup.manifest.manifest_digest == FORGED_DIGEST  # the descriptor really lies
        sealed = BackupGenerationContract.seal(backup.contract, backup.closure)
        assert sealed.manifest_digest == real_digest
        assert sealed.manifest_digest != FORGED_DIGEST

    assert (
        BackupGenerationContract.seal(backup.contract, backup.closure).manifest_digest
        == real_digest
    )


def test_check3_backup_generation_id_property_cannot_bypass_identity_mismatch(
    backup: _Backup,
) -> None:
    foreign = BackupGenerationContract(
        BackupGenerationId.new(), BackupGenerationState.ARTIFACTS_COPIED, None
    )
    with pytest.raises(ValueError, match="generation identity mismatch"):
        BackupGenerationContract.seal(foreign, backup.closure)

    with substituted(
        BackupManifestV3,
        "backup_generation_id",
        property(lambda self: foreign.backup_generation_id),
    ):
        with pytest.raises(ValueError, match="generation identity mismatch"):
            BackupGenerationContract.seal(foreign, backup.closure)


def test_check3_closure_manifest_handle_cannot_be_swapped(backup: _Backup) -> None:
    """The closure must yield the manifest it validated, not a later substitute."""

    other_id = BackupGenerationId.new()
    other = backup.issue(other_id, 1, backup.genesis(), head=9, blob="8" * 64)
    assert other.manifest_digest != backup.manifest.manifest_digest

    real_digest = backup.manifest.manifest_digest  # captured before any substitution
    with substituted(ValidatedBackupClosure, "manifest", property(lambda self: other)):
        sealed = BackupGenerationContract.seal(backup.contract, backup.closure)
        assert sealed.manifest_digest == real_digest
        assert sealed.manifest_digest != other.manifest_digest
        assert sealed.backup_generation_id == backup.generation_id


def test_check3_combined_post_closure_substitution_cannot_redirect_the_seal(
    backup: _Backup,
) -> None:
    foreign = BackupGenerationContract(
        BackupGenerationId.new(), BackupGenerationState.ARTIFACTS_COPIED, None
    )
    real_digest = backup.manifest.manifest_digest  # captured before any substitution
    with (
        substituted(BackupManifestV3, "manifest_digest", property(lambda self: FORGED_DIGEST)),
        substituted(
            BackupManifestV3,
            "backup_generation_id",
            property(lambda self: foreign.backup_generation_id),
        ),
    ):
        with pytest.raises(ValueError, match="generation identity mismatch"):
            BackupGenerationContract.seal(foreign, backup.closure)
        sealed = BackupGenerationContract.seal(backup.contract, backup.closure)
        assert sealed.manifest_digest == real_digest
        assert sealed.manifest_digest != FORGED_DIGEST


# ---------------------------------------------------------------------------
# The legitimate transition is preserved.
# ---------------------------------------------------------------------------


def test_check3_valid_closure_still_seals_normally(backup: _Backup) -> None:
    sealed = BackupGenerationContract.seal(backup.contract, backup.closure)
    assert sealed.state is BackupGenerationState.SEALED_COMPLETE
    assert sealed.backup_generation_id == backup.generation_id
    assert sealed.manifest_digest == backup.manifest.manifest_digest

    # The state-machine preconditions still hold.
    for early in (
        BackupGenerationState.REQUESTED,
        BackupGenerationState.FROZEN_AND_PINNED,
        BackupGenerationState.DB_SNAPSHOTTED,
    ):
        with pytest.raises(ValueError, match="ARTIFACTS_COPIED before sealing"):
            BackupGenerationContract.seal(
                BackupGenerationContract(backup.generation_id, early, None),
                backup.closure,
            )
    with pytest.raises(TypeError, match="ValidatedBackupClosure"):
        BackupGenerationContract.seal(backup.contract, object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="manifest validation"):
        ValidatedBackupClosure(backup.manifest)


# ---------------------------------------------------------------------------
# Previously closed Check-3 repairs must keep holding.
# ---------------------------------------------------------------------------


def test_check3_backup_chain_continuity_regression_still_holds(backup: _Backup) -> None:
    wrong = backup.issue(
        BackupGenerationId.new(), 2, backup.completed(WRONG_DIGEST), head=8, blob="7" * 64
    )
    with pytest.raises(ValueError, match=r"wrong, stale, future|changed after sealed construction"):
        backup.validate(wrong, backup.closure)

    real_slot = BackupManifestV3.__dict__["manifest_digest"]

    def lies_for_previous(self: object) -> object:
        if self is backup.manifest:
            return WRONG_DIGEST
        return real_slot.__get__(self, BackupManifestV3)

    with substituted(BackupManifestV3, "manifest_digest", property(lies_for_previous)):
        with pytest.raises(ValueError, match=r"wrong, stale, future|changed after sealed construction"):
            backup.validate(wrong, backup.closure)

    with substituted(
        BackupPredecessor,
        "manifest_digest",
        property(lambda self: backup.manifest.manifest_digest),
    ):
        with pytest.raises(ValueError, match=r"wrong, stale, future|changed after sealed construction"):
            backup.validate(wrong, backup.closure)

    # The legitimate generation-2 chain still validates and seals.
    second_id = BackupGenerationId.new()
    second = backup.issue(
        second_id, 2, backup.completed(backup.manifest.manifest_digest), head=8, blob="7" * 64
    )
    second_closure = backup.validate(second, backup.closure)
    sealed = BackupGenerationContract.seal(
        BackupGenerationContract(second_id, BackupGenerationState.ARTIFACTS_COPIED, None),
        second_closure,
    )
    assert sealed.manifest_digest == second.manifest_digest


def test_check3_required_key_regression_still_holds(backup: _Backup) -> None:
    claim_two = KeyGenerationMetadata(
        KeyFamily.CLAIM_ENROLLMENT, "claim-2", 2, KeyState.ACTIVE, "HMAC-SHA256", 1
    )
    backup.authority.install_metadata(
        KeyRingMetadata(
            (
                dataclass_replace(backup.claim, state=KeyState.RETIRED),
                claim_two,
                backup.backup,
            )
        ),
        key_materials={KeyReference(KeyFamily.CLAIM_ENROLLMENT, "claim-2", 2): b"n" * 32},
    )
    with pytest.raises(ValueError, match=r"STALE_KEY_BACKUP|changed after sealed construction"):
        backup.manifest.validate_required_keys(backup.authority)

    real_family = RequiredKeyReference.__dict__["family"]
    real_generation = RequiredKeyReference.__dict__["generation"]
    real_key_id = RequiredKeyReference.__dict__["key_id"]

    def claim_only(forged: object, descriptor: object):
        def read(self: object) -> object:
            if real_family.__get__(self, RequiredKeyReference) is KeyFamily.CLAIM_ENROLLMENT:
                return forged
            return descriptor.__get__(self, RequiredKeyReference)

        return property(read)

    with (
        substituted(RequiredKeyReference, "generation", claim_only(2, real_generation)),
        substituted(RequiredKeyReference, "key_id", claim_only("claim-2", real_key_id)),
    ):
        with pytest.raises(ValueError, match=r"STALE_KEY_BACKUP|changed after sealed construction"):
            backup.manifest.validate_required_keys(backup.authority)


def test_check3_phase0_stays_dormant() -> None:
    from core.workspace_authority_v3 import EXECUTION_AUTHORITY

    assert EXECUTION_AUTHORITY is False
    assert DormantKeyAuthority.execution_authority is False


# ---------------------------------------------------------------------------
# Check-3 re-review: the closure must bind the manifest that passed validation.
#
# Sealing the ``manifest`` descriptor stops a class-level property from redirecting
# the read; it does nothing about ``object.__setattr__`` replacing the stored value
# afterwards. Post-closure authority now comes from the validation-time binding.
# ---------------------------------------------------------------------------


def test_check3_closure_manifest_slot_swap_cannot_redirect_the_seal(backup: _Backup) -> None:
    """seal() must not seal a manifest swapped into the closure after validation."""

    validated_digest = backup.manifest.manifest_digest
    second = backup.issue(backup.generation_id, 1, backup.genesis(), head=9, blob="8" * 64)
    assert second.manifest_digest != validated_digest

    object.__setattr__(backup.closure, "manifest", second)
    assert backup.closure.manifest is second  # the slot really was replaced

    sealed = BackupGenerationContract.seal(backup.contract, backup.closure)
    assert sealed.manifest_digest == validated_digest
    assert sealed.manifest_digest != second.manifest_digest
    assert sealed.backup_generation_id == backup.generation_id


def test_check3_closure_manifest_slot_swap_cannot_change_generation_identity(
    backup: _Backup,
) -> None:
    """A swapped manifest must not satisfy the generation-identity check either."""

    foreign_id = BackupGenerationId.new()
    foreign_contract = BackupGenerationContract(
        foreign_id, BackupGenerationState.ARTIFACTS_COPIED, None
    )
    with pytest.raises(ValueError, match="generation identity mismatch"):
        BackupGenerationContract.seal(foreign_contract, backup.closure)

    swapped = backup.issue(foreign_id, 1, backup.genesis(), head=9, blob="8" * 64)
    object.__setattr__(backup.closure, "manifest", swapped)
    with pytest.raises(ValueError, match="generation identity mismatch"):
        BackupGenerationContract.seal(foreign_contract, backup.closure)


def test_check3_closure_manifest_slot_swap_cannot_supply_a_previous_closure(
    backup: _Backup,
) -> None:
    """validate_closure() must chain onto the validated manifest, not a swapped one."""

    wrong_digest_manifest = backup.issue(
        BackupGenerationId.new(), 1, backup.genesis(), head=9, blob="8" * 64
    )
    # A generation-2 manifest that chains onto the *swapped* manifest, not the real one.
    second = backup.issue_generation_two(wrong_digest_manifest.manifest_digest)
    with pytest.raises(ValueError, match=r"wrong, stale, future|changed after sealed construction"):
        backup.validate(second, backup.closure)

    object.__setattr__(backup.closure, "manifest", wrong_digest_manifest)
    with pytest.raises(ValueError, match=r"wrong, stale, future|changed after sealed construction"):
        backup.validate(second, backup.closure)

    # The chain that matches the genuinely validated manifest still passes.
    object.__setattr__(backup.closure, "manifest", backup.manifest)
    valid_second = backup.issue_generation_two(backup.manifest.manifest_digest)
    assert backup.validate(valid_second, backup.closure).manifest is valid_second


def test_check3_unissued_closure_object_is_refused(backup: _Backup) -> None:
    """A closure that never came from manifest validation carries no authority."""

    forged = object.__new__(ValidatedBackupClosure)
    object.__setattr__(forged, "manifest", backup.manifest)
    with pytest.raises(TypeError, match="not issued by manifest validation"):
        BackupGenerationContract.seal(backup.contract, forged)


def test_check3_valid_closure_still_works_after_the_binding_change(backup: _Backup) -> None:
    sealed = BackupGenerationContract.seal(backup.contract, backup.closure)
    assert sealed.state is BackupGenerationState.SEALED_COMPLETE
    assert sealed.manifest_digest == backup.manifest.manifest_digest
    assert backup.closure.manifest is backup.manifest

    second = backup.issue_generation_two(backup.manifest.manifest_digest)
    second_closure = backup.validate(second, backup.closure)
    assert second_closure.manifest is second
    second_sealed = BackupGenerationContract.seal(
        BackupGenerationContract(
            second.backup_generation_id, BackupGenerationState.ARTIFACTS_COPIED, None
        ),
        second_closure,
    )
    assert second_sealed.manifest_digest == second.manifest_digest


# ---------------------------------------------------------------------------
# Check-3 re-review: the closure binds validated manifest STATE, not just the object.
#
# Binding the manifest object stops a slot swap on the closure; it does nothing about
# ``object.__setattr__`` rewriting slots on the bound manifest itself. Authority now
# comes from a primitive snapshot taken at validation time, and a manifest that has
# since diverged from its snapshot fails closed.
# ---------------------------------------------------------------------------


def test_check3_mutating_validated_manifest_digest_cannot_validate_a_wrong_chain(
    backup: _Backup,
) -> None:
    forged = "9" * 64
    wrong_chain = backup.issue_generation_two(forged)
    with pytest.raises(ValueError, match=r"wrong, stale, future|changed after sealed construction"):
        backup.validate(wrong_chain, backup.closure)

    object.__setattr__(backup.closure.manifest, "manifest_digest", forged)
    with pytest.raises(ValueError, match=r"wrong, stale, future|changed after (validation|construction)"):
        backup.validate(wrong_chain, backup.closure)


def test_check3_mutating_validated_generation_id_cannot_seal_a_foreign_generation(
    backup: _Backup,
) -> None:
    foreign_id = BackupGenerationId.new()
    foreign_contract = BackupGenerationContract(
        foreign_id, BackupGenerationState.ARTIFACTS_COPIED, None
    )
    with pytest.raises(ValueError, match="generation identity mismatch"):
        BackupGenerationContract.seal(foreign_contract, backup.closure)

    object.__setattr__(backup.closure.manifest, "backup_generation_id", foreign_id)
    with pytest.raises(
        ValueError, match=r"generation identity mismatch|changed after (validation|construction)"
    ):
        BackupGenerationContract.seal(foreign_contract, backup.closure)


@pytest.mark.parametrize(
    ("field", "forged"),
    (
        ("authority_generation", 7),
        ("manifest_digest", "b" * 64),
    ),
)
def test_check3_any_validated_manifest_state_change_fails_closed(
    backup: _Backup,
    field: str,
    forged: object,
) -> None:
    """A validated manifest must not change; divergence is refused, not absorbed."""

    object.__setattr__(backup.closure.manifest, field, forged)
    with pytest.raises(ValueError, match=r"changed after (validation|construction)"):
        BackupGenerationContract.seal(backup.contract, backup.closure)


def test_check3_valid_closure_still_seals_and_chains_after_the_snapshot_change(
    backup: _Backup,
) -> None:
    sealed = BackupGenerationContract.seal(backup.contract, backup.closure)
    assert sealed.state is BackupGenerationState.SEALED_COMPLETE
    assert sealed.manifest_digest == backup.manifest.manifest_digest
    assert sealed.backup_generation_id == backup.generation_id

    second = backup.issue_generation_two(backup.manifest.manifest_digest)
    second_closure = backup.validate(second, backup.closure)
    assert second_closure.manifest is second
    assert (
        BackupGenerationContract.seal(
            BackupGenerationContract(
                second.backup_generation_id, BackupGenerationState.ARTIFACTS_COPIED, None
            ),
            second_closure,
        ).manifest_digest
        == second.manifest_digest
    )


def test_check3_unissued_closure_still_fails_closed_after_the_snapshot_change(
    backup: _Backup,
) -> None:
    forged = object.__new__(ValidatedBackupClosure)
    object.__setattr__(forged, "manifest", backup.manifest)
    with pytest.raises(TypeError, match="not issued by manifest validation"):
        BackupGenerationContract.seal(backup.contract, forged)
    with pytest.raises(TypeError, match="not issued by manifest validation"):
        backup.validate(backup.issue_generation_two(backup.manifest.manifest_digest), forged)


def test_check3_closure_object_swap_regression_still_holds(backup: _Backup) -> None:
    validated_digest = backup.manifest.manifest_digest
    second = backup.issue(backup.generation_id, 1, backup.genesis(), head=9, blob="8" * 64)
    assert second.manifest_digest != validated_digest

    object.__setattr__(backup.closure, "manifest", second)
    sealed = BackupGenerationContract.seal(backup.contract, backup.closure)
    assert sealed.manifest_digest == validated_digest
    assert sealed.manifest_digest != second.manifest_digest
