"""Check-3 re-review repair: backup-chain continuity is compared on sealed manifests.

``validate_closure()`` sealed the current predecessor side but still read the previously
validated manifest through public ``BackupManifestV3`` descriptors, so replacing one of
those descriptors -- for the previous manifest only -- let a generation-2 manifest with a
wrong sealed predecessor digest pass continuity.

Both sides of the comparison now resolve through descriptors captured at import.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from core.workspace_authority_v3 import contracts as contracts_module
from core.workspace_authority_v3.contracts import (
    AuthorityBudgetProfile,
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

KEY = b"h" * 32
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


def _lies_only_for(target: object, forged: object, field: str):
    """Report ``forged`` for exactly one manifest; every other instance stays real.

    A blanket substitution would move both sides of the comparison together and could
    pass or fail for reasons unrelated to the sealed read.
    """

    real = BackupManifestV3.__dict__[field]

    def read(self: object) -> object:
        if self is target:
            return forged
        return real.__get__(self, BackupManifestV3)

    return property(read)


def _ring_workspace() -> WorkspaceId:
    domain = AuthorityDomainId.from_authority_claim(AuthorityClaimProvenance.establish())
    return WorkspaceId.from_enrollment(
        WorkspaceEnrollmentProvenance.establish(
            authority_domain_id=domain,
            enrollment_operation_id=EnrollmentOperationId.new(),
        )
    )


class _Chain:
    """A genesis closure plus the machinery to issue generation-2 manifests."""

    def __init__(self) -> None:
        claim = KeyGenerationMetadata(
            KeyFamily.CLAIM_ENROLLMENT, "claim-1", 1, KeyState.ACTIVE, "HMAC-SHA256", 1
        )
        backup = KeyGenerationMetadata(
            KeyFamily.BACKUP_MANIFEST, "backup-1", 1, KeyState.ACTIVE, "HMAC-SHA256", 1
        )
        self.authority = DormantKeyAuthority(
            KeyRingMetadata((claim, backup)),
            {
                KeyReference(item.family, item.key_id, item.generation): KEY
                for item in (claim, backup)
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
        self.first = self.issue(
            1,
            BackupPredecessor.genesis(
                workspace_id=self.workspace_id,
                incarnation_id=self.incarnation_id,
                authority_epoch=self.epoch,
            ),
            head=7,
            blob="3" * 64,
        )
        self.first_closure = self.validate(self.first, None)

    def issue(
        self,
        generation: int,
        predecessor: BackupPredecessor,
        *,
        head: int,
        blob: str,
        workspace_id: WorkspaceId | None = None,
        incarnation_id: IncarnationId | None = None,
        epoch: AuthorityEpoch | None = None,
    ) -> BackupManifestV3:
        return BackupManifestV3.issue(
            backup_generation_id=BackupGenerationId.new(),
            authority_generation=generation,
            db_snapshot_identity_digest="1" * 64,
            workspace_id=self.workspace_id if workspace_id is None else workspace_id,
            incarnation_id=self.incarnation_id if incarnation_id is None else incarnation_id,
            authority_epoch=self.epoch if epoch is None else epoch,
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

    def genesis_predecessor(self) -> BackupPredecessor:
        return BackupPredecessor.genesis(
            workspace_id=self.workspace_id,
            incarnation_id=self.incarnation_id,
            authority_epoch=self.epoch,
        )

    def completed_predecessor(
        self,
        digest: str,
        *,
        generation: int = 1,
        workspace_id: WorkspaceId | None = None,
        incarnation_id: IncarnationId | None = None,
        epoch: AuthorityEpoch | None = None,
    ) -> BackupPredecessor:
        return BackupPredecessor.completed(
            workspace_id=self.workspace_id if workspace_id is None else workspace_id,
            incarnation_id=self.incarnation_id if incarnation_id is None else incarnation_id,
            authority_epoch=self.epoch if epoch is None else epoch,
            authority_generation=generation,
            manifest_digest=digest,
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
def chain() -> _Chain:
    return _Chain()


def _wrong_chain(chain: _Chain) -> BackupManifestV3:
    return chain.issue(
        2, chain.completed_predecessor(WRONG_DIGEST), head=8, blob="7" * 64
    )


_REFUSED = r"wrong, stale, future|changed after sealed construction|changed after construction"


def _expect_rejected(chain: _Chain, manifest: BackupManifestV3) -> None:
    """Refused by continuity, by the predecessor's construction state, or by the
    manifest's whole-record seal -- all refusals; acceptance is the failure."""

    with pytest.raises(ValueError, match=_REFUSED):
        chain.validate(manifest, chain.first_closure)


# ---------------------------------------------------------------------------
# The reported defect and its siblings on the previous-manifest side.
# ---------------------------------------------------------------------------


def test_check3_previous_manifest_digest_property_cannot_forge_continuity(chain: _Chain) -> None:
    manifest = _wrong_chain(chain)
    _expect_rejected(chain, manifest)  # control: genuinely wrong chain

    with substituted(
        BackupManifestV3,
        "manifest_digest",
        _lies_only_for(chain.first, WRONG_DIGEST, "manifest_digest"),
    ):
        assert chain.first.manifest_digest == WRONG_DIGEST  # the descriptor really lies
        _expect_rejected(chain, manifest)


def test_check3_previous_manifest_generation_property_cannot_forge_continuity(
    chain: _Chain,
) -> None:
    """A generation-3 manifest must not chain onto the generation-1 closure."""

    manifest = chain.issue(
        3,
        chain.completed_predecessor(chain.first.manifest_digest, generation=2),
        head=9,
        blob="8" * 64,
    )
    _expect_rejected(chain, manifest)

    with substituted(
        BackupManifestV3,
        "authority_generation",
        _lies_only_for(chain.first, 2, "authority_generation"),
    ):
        _expect_rejected(chain, manifest)


@pytest.mark.parametrize("field", ("workspace_id", "incarnation_id", "authority_epoch"))
def test_check3_previous_manifest_scope_property_cannot_forge_continuity(
    chain: _Chain,
    field: str,
) -> None:
    """A manifest issued in another scope must not chain onto this closure."""

    foreign = {
        "workspace_id": {"workspace_id": _ring_workspace()},
        "incarnation_id": {"incarnation_id": IncarnationId.new()},
        "authority_epoch": {"epoch": AuthorityEpoch(6)},
    }[field]
    manifest = chain.issue(
        2,
        chain.completed_predecessor(chain.first.manifest_digest, **foreign),
        head=8,
        blob="7" * 64,
        **foreign,
    )
    _expect_rejected(chain, manifest)

    forged = getattr(manifest, field)
    with substituted(BackupManifestV3, field, _lies_only_for(chain.first, forged, field)):
        _expect_rejected(chain, manifest)


def test_check3_combined_previous_manifest_substitution_cannot_forge_continuity(
    chain: _Chain,
) -> None:
    manifest = _wrong_chain(chain)
    with (
        substituted(
            BackupManifestV3,
            "manifest_digest",
            _lies_only_for(chain.first, WRONG_DIGEST, "manifest_digest"),
        ),
        substituted(
            BackupManifestV3,
            "authority_generation",
            _lies_only_for(chain.first, 1, "authority_generation"),
        ),
    ):
        _expect_rejected(chain, manifest)


def test_check3_current_manifest_side_is_sealed_too(chain: _Chain) -> None:
    """The comparison seals both sides, not only the previously validated manifest.

    Uses fields the continuity clause actually reads on the current manifest: its
    generation, and the predecessor object it binds.
    """

    # Generation 3 cannot chain onto the generation-1 closure...
    manifest = chain.issue(
        3,
        chain.completed_predecessor(chain.first.manifest_digest, generation=2),
        head=9,
        blob="8" * 64,
    )
    _expect_rejected(chain, manifest)
    # ...and presenting it as generation 2 must not change that.
    with substituted(
        BackupManifestV3,
        "authority_generation",
        _lies_only_for(manifest, 2, "authority_generation"),
    ):
        _expect_rejected(chain, manifest)

    # Swapping the predecessor object the current manifest appears to bind is refused too.
    wrong = _wrong_chain(chain)
    real_predecessor = chain.completed_predecessor(chain.first.manifest_digest)
    with substituted(
        BackupManifestV3,
        "predecessor",
        _lies_only_for(wrong, real_predecessor, "predecessor"),
    ):
        _expect_rejected(chain, wrong)


# ---------------------------------------------------------------------------
# The legitimate chain still validates.
# ---------------------------------------------------------------------------


def test_check3_valid_predecessor_chain_still_passes(chain: _Chain) -> None:
    second = chain.issue(
        2,
        chain.completed_predecessor(chain.first.manifest_digest),
        head=8,
        blob="7" * 64,
    )
    second_closure = chain.validate(second, chain.first_closure)
    assert second_closure.manifest is second

    third = chain.issue(
        3,
        chain.completed_predecessor(second.manifest_digest, generation=2),
        head=9,
        blob="8" * 64,
    )
    assert chain.validate(third, second_closure).manifest is third

    # Genesis still refuses a completed predecessor closure.
    with pytest.raises(ValueError, match="genesis backup cannot bind"):
        chain.validate(chain.first, second_closure)
    # And a later generation still requires one.
    with pytest.raises(ValueError, match="requires the previous validated"):
        chain.validate(second, None)


def test_check3_required_key_and_predecessor_regressions_still_hold(chain: _Chain) -> None:
    """The previously closed Check-3 repairs must keep holding in this same flow."""

    second = chain.issue(
        2,
        chain.completed_predecessor(chain.first.manifest_digest),
        head=8,
        blob="7" * 64,
    )

    # BackupPredecessor sealed read.
    wrong = _wrong_chain(chain)
    with substituted(
        BackupPredecessor,
        "manifest_digest",
        property(lambda self: chain.first.manifest_digest),
    ):
        _expect_rejected(chain, wrong)

    # RequiredKeyReference sealed read: retire the claim key, then lie about it.
    from dataclasses import replace as dataclass_replace

    claim = KeyGenerationMetadata(
        KeyFamily.CLAIM_ENROLLMENT, "claim-1", 1, KeyState.ACTIVE, "HMAC-SHA256", 1
    )
    backup = KeyGenerationMetadata(
        KeyFamily.BACKUP_MANIFEST, "backup-1", 1, KeyState.ACTIVE, "HMAC-SHA256", 1
    )
    claim_two = KeyGenerationMetadata(
        KeyFamily.CLAIM_ENROLLMENT, "claim-2", 2, KeyState.ACTIVE, "HMAC-SHA256", 1
    )
    chain.authority.install_metadata(
        KeyRingMetadata(
            (dataclass_replace(claim, state=KeyState.RETIRED), claim_two, backup)
        ),
        key_materials={KeyReference(KeyFamily.CLAIM_ENROLLMENT, "claim-2", 2): b"n" * 32},
    )
    real_generation = RequiredKeyReference.__dict__["generation"]
    real_key_id = RequiredKeyReference.__dict__["key_id"]
    real_family = RequiredKeyReference.__dict__["family"]

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
            chain.validate(second, chain.first_closure)


def test_check3_phase0_stays_dormant() -> None:
    from core.workspace_authority_v3 import EXECUTION_AUTHORITY

    assert EXECUTION_AUTHORITY is False
    assert DormantKeyAuthority.execution_authority is False


# ---------------------------------------------------------------------------
# Check-3 re-review: BackupPredecessor binds construction-time state.
#
# Sealing the predecessor's descriptors stopped a class-level property; the instance
# slots stayed writable, so ``object.__setattr__`` on an issued manifest's predecessor
# could point a wrong chain at the correct previous digest. The identity is now
# recorded when the constructor validates it, and later divergence is refused.
# ---------------------------------------------------------------------------


def test_check3_mutating_current_predecessor_digest_cannot_validate_a_wrong_chain(
    chain: _Chain,
) -> None:
    current_wrong = chain.issue(
        2, chain.completed_predecessor(WRONG_DIGEST), head=8, blob="7" * 64
    )
    _expect_rejected(chain, current_wrong)  # control: genuinely wrong chain

    object.__setattr__(
        current_wrong.predecessor, "manifest_digest", chain.first.manifest_digest
    )
    with pytest.raises(ValueError, match=_REFUSED):
        chain.validate(current_wrong, chain.first_closure)


@pytest.mark.parametrize(
    ("field", "forged"),
    (
        ("authority_generation", 2),
        ("manifest_digest", "b" * 64),
    ),
)
def test_check3_any_predecessor_state_change_fails_closed(
    chain: _Chain,
    field: str,
    forged: object,
) -> None:
    manifest = chain.issue(
        2, chain.completed_predecessor(chain.first.manifest_digest), head=8, blob="7" * 64
    )
    chain.validate(manifest, chain.first_closure)  # valid before mutation

    object.__setattr__(manifest.predecessor, field, forged)
    with pytest.raises(ValueError, match=_REFUSED):
        chain.validate(manifest, chain.first_closure)


@pytest.mark.parametrize("field", ("workspace_id", "incarnation_id", "authority_epoch"))
def test_check3_predecessor_scope_change_fails_closed(chain: _Chain, field: str) -> None:
    manifest = chain.issue(
        2, chain.completed_predecessor(chain.first.manifest_digest), head=8, blob="7" * 64
    )
    forged = {
        "workspace_id": _ring_workspace(),
        "incarnation_id": IncarnationId.new(),
        "authority_epoch": AuthorityEpoch(6),
    }[field]

    object.__setattr__(manifest.predecessor, field, forged)
    with pytest.raises(ValueError, match=_REFUSED):
        chain.validate(manifest, chain.first_closure)


def test_check3_unissued_predecessor_fails_closed(chain: _Chain) -> None:
    """A predecessor that never ran its constructor carries no identity."""

    forged = object.__new__(BackupPredecessor)
    real = chain.completed_predecessor(chain.first.manifest_digest)
    for name in (
        "kind",
        "workspace_id",
        "incarnation_id",
        "authority_epoch",
        "authority_generation",
        "manifest_digest",
    ):
        object.__setattr__(forged, name, getattr(real, name))
    with pytest.raises(TypeError, match="not issued by its constructor"):
        BackupPredecessor._sealed(forged)


def test_check3_valid_genesis_and_generation_two_chains_still_validate(chain: _Chain) -> None:
    # Genesis validated in the fixture; re-assert it explicitly.
    genesis_again = chain.issue(1, chain.genesis_predecessor(), head=7, blob="3" * 64)
    assert chain.validate(genesis_again, None).manifest is genesis_again

    second = chain.issue(
        2, chain.completed_predecessor(chain.first.manifest_digest), head=8, blob="7" * 64
    )
    second_closure = chain.validate(second, chain.first_closure)
    assert second_closure.manifest is second

    third = chain.issue(
        3,
        chain.completed_predecessor(second.manifest_digest, generation=2),
        head=9,
        blob="8" * 64,
    )
    assert chain.validate(third, second_closure).manifest is third


def test_check3_validated_closure_snapshot_regressions_still_hold(chain: _Chain) -> None:
    """The prior ValidatedBackupClosure state-binding repairs must keep holding."""

    wrong = chain.issue(2, chain.completed_predecessor(WRONG_DIGEST), head=8, blob="7" * 64)
    _expect_rejected(chain, wrong)

    # Mutating the previously validated manifest is refused.
    object.__setattr__(chain.first, "manifest_digest", WRONG_DIGEST)
    with pytest.raises(ValueError, match=_REFUSED + r"|changed after validation"):
        chain.validate(wrong, chain.first_closure)


# ---------------------------------------------------------------------------
# Check-3 re-review: the whole-record seal gates the current-manifest boundary.
#
# BackupManifestV3 already carries a construction-time fingerprint. validate_closure
# and validate_required_keys now require the manifest to still be its exact sealed
# record before any continuity or key decision reads it, so a manifest mutated after
# issuance is refused as a record rather than examined field by field.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "forged"),
    (
        ("authority_generation", 7),
        ("manifest_digest", "c" * 64),
        ("db_snapshot_identity_digest", "d" * 64),
    ),
)
def test_check3_mutating_the_current_manifest_is_refused_as_a_record(
    chain: _Chain,
    field: str,
    forged: object,
) -> None:
    manifest = chain.issue(
        2, chain.completed_predecessor(chain.first.manifest_digest), head=8, blob="7" * 64
    )
    chain.validate(manifest, chain.first_closure)  # valid before mutation

    object.__setattr__(manifest, field, forged)
    with pytest.raises(ValueError, match="changed after sealed construction"):
        chain.validate(manifest, chain.first_closure)
    with pytest.raises(ValueError, match="changed after sealed construction"):
        manifest.validate_required_keys(chain.authority)


@pytest.mark.parametrize("field", ("workspace_id", "incarnation_id", "authority_epoch"))
def test_check3_mutating_current_manifest_scope_is_refused_as_a_record(
    chain: _Chain,
    field: str,
) -> None:
    manifest = chain.issue(
        2, chain.completed_predecessor(chain.first.manifest_digest), head=8, blob="7" * 64
    )
    forged = {
        "workspace_id": _ring_workspace(),
        "incarnation_id": IncarnationId.new(),
        "authority_epoch": AuthorityEpoch(6),
    }[field]

    object.__setattr__(manifest, field, forged)
    with pytest.raises(ValueError, match="changed after sealed construction"):
        chain.validate(manifest, chain.first_closure)


def test_check3_swapping_the_current_predecessor_object_is_refused_as_a_record(
    chain: _Chain,
) -> None:
    """Swapping the whole predecessor field, and mutating the bound object's slots."""

    manifest = chain.issue(
        2, chain.completed_predecessor(chain.first.manifest_digest), head=8, blob="7" * 64
    )
    chain.validate(manifest, chain.first_closure)

    object.__setattr__(manifest, "predecessor", chain.completed_predecessor(WRONG_DIGEST))
    with pytest.raises(ValueError, match="changed after sealed construction"):
        chain.validate(manifest, chain.first_closure)

    fresh = chain.issue(
        2, chain.completed_predecessor(chain.first.manifest_digest), head=8, blob="7" * 64
    )
    object.__setattr__(fresh.predecessor, "manifest_digest", WRONG_DIGEST)
    with pytest.raises(ValueError, match=_REFUSED):
        chain.validate(fresh, chain.first_closure)


def test_check3_valid_generation_one_two_and_three_chains_still_pass(chain: _Chain) -> None:
    genesis_again = chain.issue(1, chain.genesis_predecessor(), head=7, blob="3" * 64)
    assert chain.validate(genesis_again, None).manifest is genesis_again

    second = chain.issue(
        2, chain.completed_predecessor(chain.first.manifest_digest), head=8, blob="7" * 64
    )
    second_closure = chain.validate(second, chain.first_closure)
    third = chain.issue(
        3,
        chain.completed_predecessor(second.manifest_digest, generation=2),
        head=9,
        blob="8" * 64,
    )
    assert chain.validate(third, second_closure).manifest is third
    second.validate_required_keys(chain.authority)


# ---------------------------------------------------------------------------
# Check-3 re-review: one authority source for integrity and continuity.
#
# require_closed_authority_record recomputes its fingerprint through ordinary attribute
# access, while continuity reads go through the sealed field descriptors. That split was
# exploitable: rewrite a slot, then install a class descriptor reporting the original
# value, and the integrity guard saw an intact record while continuity consumed the
# rewrite. The manifest's construction-time snapshot and its divergence check now both
# read through the sealed descriptors.
# ---------------------------------------------------------------------------


def test_check3_combined_descriptor_and_slot_bypass_is_refused(chain: _Chain) -> None:
    """The reviewer's shape: slot rewritten, descriptor reporting the original value."""

    wrong_predecessor = chain.completed_predecessor(WRONG_DIGEST)
    current = chain.issue(2, wrong_predecessor, head=8, blob="7" * 64)
    _expect_rejected(chain, current)  # control: genuinely wrong chain

    right_predecessor = chain.completed_predecessor(chain.first.manifest_digest)
    object.__setattr__(current, "predecessor", right_predecessor)

    real_slot = BackupManifestV3.__dict__["predecessor"]

    def reports_the_original(self: object) -> object:
        if self is current:
            return wrong_predecessor
        return real_slot.__get__(self, BackupManifestV3)

    with substituted(BackupManifestV3, "predecessor", property(reports_the_original)):
        with pytest.raises(ValueError, match=_REFUSED):
            chain.validate(current, chain.first_closure)
        with pytest.raises(ValueError, match=_REFUSED):
            current.validate_required_keys(chain.authority)


def test_check3_slot_only_and_descriptor_only_variants_are_refused(chain: _Chain) -> None:
    right_predecessor = chain.completed_predecessor(chain.first.manifest_digest)

    slot_only = chain.issue(2, chain.completed_predecessor(WRONG_DIGEST), head=8, blob="7" * 64)
    object.__setattr__(slot_only, "predecessor", right_predecessor)
    with pytest.raises(ValueError, match=_REFUSED):
        chain.validate(slot_only, chain.first_closure)

    descriptor_only = chain.issue(
        2, chain.completed_predecessor(WRONG_DIGEST), head=8, blob="7" * 64
    )
    with substituted(
        BackupManifestV3, "predecessor", property(lambda self: right_predecessor)
    ):
        with pytest.raises(ValueError, match=_REFUSED):
            chain.validate(descriptor_only, chain.first_closure)


def test_check3_unissued_manifest_has_no_authority_state(chain: _Chain) -> None:
    forged = object.__new__(BackupManifestV3)
    for name in (
        "backup_generation_id",
        "authority_generation",
        "workspace_id",
        "incarnation_id",
        "authority_epoch",
        "manifest_digest",
        "manifest_mac",
        "manifest_key_id",
        "manifest_key_generation",
        "budget_profile_digest",
        "predecessor",
        "required_keys",
    ):
        object.__setattr__(forged, name, getattr(chain.first, name))
    with pytest.raises(TypeError, match="not issued by its constructor"):
        contracts_module._sealed_manifest_state(forged)


def test_check3_unified_reader_preserves_valid_chains_and_prior_regressions(
    chain: _Chain,
) -> None:
    genesis_again = chain.issue(1, chain.genesis_predecessor(), head=7, blob="3" * 64)
    assert chain.validate(genesis_again, None).manifest is genesis_again

    second = chain.issue(
        2, chain.completed_predecessor(chain.first.manifest_digest), head=8, blob="7" * 64
    )
    second_closure = chain.validate(second, chain.first_closure)
    third = chain.issue(
        3,
        chain.completed_predecessor(second.manifest_digest, generation=2),
        head=9,
        blob="8" * 64,
    )
    assert chain.validate(third, second_closure).manifest is third
    second.validate_required_keys(chain.authority)

    # BackupPredecessor construction-state regression.
    wrong = chain.issue(2, chain.completed_predecessor(WRONG_DIGEST), head=8, blob="7" * 64)
    object.__setattr__(wrong.predecessor, "manifest_digest", chain.first.manifest_digest)
    with pytest.raises(ValueError, match=_REFUSED):
        chain.validate(wrong, chain.first_closure)
