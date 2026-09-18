"""Check-3 re-review repair: required-key validation consumes the sealed identity.

``RequiredKeyReference`` is a frozen slotted dataclass, but its public class descriptors
stay replaceable after class definition.  ``BackupManifestV3.validate_required_keys()``
read ``family``/``generation``/``key_id`` through those descriptors, so replacing them
let an already-constructed manifest that genuinely required a retired key be revalidated
as though it required the current one.

The required-key identity is now read through descriptors captured at import, so the
tuple sealed into the reference at construction stays authoritative.
"""

from __future__ import annotations

import inspect
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace

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

KEY = b"q" * 32
_SERIALIZATION_FRAMES = frozenset(
    {"payload_fingerprint", "encode", "seal_record", "envelope_record", "require_spec"}
)

# Captured before any test can replace them, so a discriminating substitution can still
# consult the real stored values.
_REAL_FAMILY = RequiredKeyReference.__dict__["family"]
_REAL_GENERATION = RequiredKeyReference.__dict__["generation"]
_REAL_KEY_ID = RequiredKeyReference.__dict__["key_id"]

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


def _claim_only(forged: object, descriptor: object):
    """Present ``forged`` only for the CLAIM_ENROLLMENT reference; keep the rest real.

    A blanket substitution would also move the manifest-signing reference and be rejected
    for an unrelated reason, which would not prove anything about the sealed read.
    """

    def read(self: object) -> object:
        if _REAL_FAMILY.__get__(self, RequiredKeyReference) is KeyFamily.CLAIM_ENROLLMENT:
            return forged
        return descriptor.__get__(self, RequiredKeyReference)

    return property(read)


def _ring_workspace() -> WorkspaceId:
    domain = AuthorityDomainId.from_authority_claim(AuthorityClaimProvenance.establish())
    return WorkspaceId.from_enrollment(
        WorkspaceEnrollmentProvenance.establish(
            authority_domain_id=domain,
            enrollment_operation_id=EnrollmentOperationId.new(),
        )
    )


def _generation(family: KeyFamily, key_id: str, generation: int, state: KeyState):
    return KeyGenerationMetadata(family, key_id, generation, state, "HMAC-SHA256", 1)


def _budget_profile() -> AuthorityBudgetProfile:
    return AuthorityBudgetProfile(
        profile_version=3,
        **dict.fromkeys(_BUDGET_FIELD_NAMES, 10_000),
    )


def _stale_manifest() -> tuple[BackupManifestV3, DormantKeyAuthority]:
    """A manifest genuinely requiring CLAIM_ENROLLMENT/1/claim-1, later retired."""

    claim_one = _generation(KeyFamily.CLAIM_ENROLLMENT, "claim-1", 1, KeyState.ACTIVE)
    backup = _generation(KeyFamily.BACKUP_MANIFEST, "backup-1", 1, KeyState.ACTIVE)
    authority = DormantKeyAuthority(
        KeyRingMetadata((claim_one, backup)),
        {
            KeyReference(item.family, item.key_id, item.generation): KEY
            for item in (claim_one, backup)
        },
        workspace_id=_ring_workspace(),
    )
    workspace_id = _ring_workspace()
    incarnation_id = IncarnationId.new()
    epoch = AuthorityEpoch(5)
    profile = _budget_profile()
    manifest = BackupManifestV3.issue(
        backup_generation_id=BackupGenerationId.new(),
        authority_generation=1,
        db_snapshot_identity_digest="1" * 64,
        workspace_id=workspace_id,
        incarnation_id=incarnation_id,
        authority_epoch=epoch,
        head_revision=HeadRevision(7),
        claim_digest="2" * 64,
        claim_key_generation=1,
        blob_inventory_digest="3" * 64,
        tombstone_inventory_digest="4" * 64,
        required_keys=(
            RequiredKeyReference(KeyFamily.CLAIM_ENROLLMENT, 1, "claim-1"),
            RequiredKeyReference(KeyFamily.BACKUP_MANIFEST, 1, "backup-1"),
        ),
        budget_profile_digest=profile.digest(),
        predecessor=BackupPredecessor.genesis(
            workspace_id=workspace_id,
            incarnation_id=incarnation_id,
            authority_epoch=epoch,
        ),
        signing_key=authority.signing_capability(
            family=KeyFamily.BACKUP_MANIFEST,
            generation=1,
            key_id="backup-1",
        ),
    )
    # Retire generation 1 and rotate a current generation 2 in behind it.
    claim_two = _generation(KeyFamily.CLAIM_ENROLLMENT, "claim-2", 2, KeyState.ACTIVE)
    authority.install_metadata(
        KeyRingMetadata((replace(claim_one, state=KeyState.RETIRED), claim_two, backup)),
        key_materials={KeyReference(KeyFamily.CLAIM_ENROLLMENT, "claim-2", 2): b"n" * 32},
    )
    return manifest, authority


def _assert_still_stale(manifest: BackupManifestV3, authority: DormantKeyAuthority) -> None:
    """Refused either by the sealed required-key read or by the whole-record seal.

    validate_required_keys now enforces the manifest's construction-time fingerprint
    first, and a lying class descriptor makes the record serialize differently, so a
    descriptor substitution is refused there rather than reaching the key lookup.
    Both are refusals; what must never happen is acceptance.
    """

    with pytest.raises(ValueError, match=r"STALE_KEY_BACKUP|changed after sealed construction"):
        manifest.validate_required_keys(authority)


@contextmanager
def _recorded_required_key_lookups(monkeypatch) -> Iterator[list[tuple[object, int, str]]]:
    """Record the triples validate_required_keys actually hands to the key authority.

    A single forged field always lands on some nonexistent reference, so an
    "it still raised" assertion would pass whether or not the read was sealed.
    Observing the consumed triple is what makes each field independently decisive.
    """

    seen: list[tuple[object, int, str]] = []
    real = contracts_module._require_usable_key_generation

    def recording(authority, *, family, generation, key_id):
        seen.append((family, generation, key_id))
        return real(authority, family=family, generation=generation, key_id=key_id)

    monkeypatch.setattr(contracts_module, "_require_usable_key_generation", recording)
    yield seen


# ---------------------------------------------------------------------------
# 1-4. Descriptor replacement cannot reinterpret a sealed required key.
# ---------------------------------------------------------------------------


SEALED_CLAIM = (KeyFamily.CLAIM_ENROLLMENT, 1, "claim-1")
SEALED_BACKUP = (KeyFamily.BACKUP_MANIFEST, 1, "backup-1")


def _assert_never_consumed_forged(
    seen: list[tuple[object, int, str]],
    forged: tuple[object, int, str],
    manifest: BackupManifestV3,
) -> None:
    """Two refusal orderings are correct; consuming the forged identity is not.

    The whole-record seal on the manifest fires before the key lookup when a class
    descriptor lies, so ``seen`` may be empty.  When the lookup *is* reached it must
    consume the sealed triple.  Either way the sealed read itself must still report the
    construction-time identity, which is asserted directly so this test keeps proving
    the sealed read even when the record guard short-circuits the path.
    """

    assert forged not in seen, "validation consumed an identity the manifest never sealed"
    assert seen == [] or SEALED_CLAIM in seen
    claim_reference = next(
        item
        for item in manifest.required_keys
        if _REAL_FAMILY.__get__(item, RequiredKeyReference) is KeyFamily.CLAIM_ENROLLMENT
    )
    assert contracts_module._sealed_required_key(claim_reference) == SEALED_CLAIM


def test_check3_required_key_generation_descriptor_replacement_is_ignored(monkeypatch) -> None:
    manifest, authority = _stale_manifest()
    _assert_still_stale(manifest, authority)  # control: genuinely stale

    with substituted(RequiredKeyReference, "generation", _claim_only(2, _REAL_GENERATION)):
        assert manifest.required_keys[1].generation == 2  # the descriptor really does lie
        with _recorded_required_key_lookups(monkeypatch) as seen:
            _assert_still_stale(manifest, authority)
    _assert_never_consumed_forged(seen, (KeyFamily.CLAIM_ENROLLMENT, 2, "claim-1"), manifest)


def test_check3_required_key_key_id_descriptor_replacement_is_ignored(monkeypatch) -> None:
    manifest, authority = _stale_manifest()

    with substituted(RequiredKeyReference, "key_id", _claim_only("claim-2", _REAL_KEY_ID)):
        with _recorded_required_key_lookups(monkeypatch) as seen:
            _assert_still_stale(manifest, authority)
    _assert_never_consumed_forged(seen, (KeyFamily.CLAIM_ENROLLMENT, 1, "claim-2"), manifest)


def test_check3_required_key_family_descriptor_replacement_is_ignored(monkeypatch) -> None:
    """Presenting the stale claim reference as the current BACKUP_MANIFEST family."""

    manifest, authority = _stale_manifest()

    with substituted(
        RequiredKeyReference,
        "family",
        _claim_only(KeyFamily.BACKUP_MANIFEST, _REAL_FAMILY),
    ):
        with _recorded_required_key_lookups(monkeypatch) as seen:
            _assert_still_stale(manifest, authority)
    _assert_never_consumed_forged(seen, (KeyFamily.BACKUP_MANIFEST, 1, "claim-1"), manifest)
    assert seen == [] or SEALED_BACKUP in seen


def test_check3_required_key_combined_descriptor_replacement_is_ignored() -> None:
    """family + generation + key_id together, describing a currently valid key."""

    manifest, authority = _stale_manifest()

    with (
        substituted(
            RequiredKeyReference,
            "generation",
            _claim_only(2, _REAL_GENERATION),
        ),
        substituted(
            RequiredKeyReference,
            "key_id",
            _claim_only("claim-2", _REAL_KEY_ID),
        ),
    ):
        _assert_still_stale(manifest, authority)

    # And with the family moved onto the still-current backup key as well.
    manifest, authority = _stale_manifest()
    with (
        substituted(RequiredKeyReference, "generation", property(lambda self: 1)),
        substituted(RequiredKeyReference, "key_id", property(lambda self: "backup-1")),
        substituted(
            RequiredKeyReference,
            "family",
            property(lambda self: KeyFamily.BACKUP_MANIFEST),
        ),
    ):
        _assert_still_stale(manifest, authority)


def test_check3_required_key_two_faced_descriptor_is_ignored() -> None:
    """Answering correctly to serialization frames does not buy the bypass either."""

    manifest, authority = _stale_manifest()

    def two_faced(descriptor: object, forged: object):
        def read(self: object) -> object:
            for frame in inspect.stack():
                if frame.function in _SERIALIZATION_FRAMES:
                    return descriptor.__get__(self, RequiredKeyReference)
            if _REAL_FAMILY.__get__(self, RequiredKeyReference) is KeyFamily.CLAIM_ENROLLMENT:
                return forged
            return descriptor.__get__(self, RequiredKeyReference)

        return property(read)

    with (
        substituted(RequiredKeyReference, "generation", two_faced(_REAL_GENERATION, 2)),
        substituted(RequiredKeyReference, "key_id", two_faced(_REAL_KEY_ID, "claim-2")),
    ):
        _assert_still_stale(manifest, authority)


def test_check3_no_reference_can_be_born_with_divergent_stored_and_shown_values() -> None:
    """A replaced field descriptor makes construction impossible, not merely mis-validated.

    The frozen dataclass assigns its fields through the very descriptor being replaced, so
    a read-only property blocks ``__init__`` outright.  No RequiredKeyReference can exist
    whose stored identity differs from the one its descriptors present at birth, which is
    why the sealed reads in ``__post_init__`` are defence in depth rather than the guard
    that carries this defect -- the load-bearing reads are the validation-time ones above.
    """

    for field, forged in (
        ("generation", property(lambda self: 1)),
        ("key_id", property(lambda self: "claim-1")),
        ("family", property(lambda self: KeyFamily.CLAIM_ENROLLMENT)),
    ):
        with substituted(RequiredKeyReference, field, forged):
            with pytest.raises(AttributeError, match="no setter"):
                RequiredKeyReference(KeyFamily.CLAIM_ENROLLMENT, 1, "claim-1")

    # And normal construction still validates its arguments.
    with pytest.raises(ValueError, match="generation"):
        RequiredKeyReference(KeyFamily.CLAIM_ENROLLMENT, 0, "claim-1")
    with pytest.raises(ValueError, match="key_id"):
        RequiredKeyReference(KeyFamily.CLAIM_ENROLLMENT, 1, "not a safe identifier!!")
    with pytest.raises(TypeError, match="family must be KeyFamily"):
        RequiredKeyReference("CLAIM_ENROLLMENT", 1, "claim-1")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 5. Normal valid control.
# ---------------------------------------------------------------------------


def test_check3_current_required_keys_still_validate_normally() -> None:
    claim = _generation(KeyFamily.CLAIM_ENROLLMENT, "claim-1", 1, KeyState.ACTIVE)
    backup = _generation(KeyFamily.BACKUP_MANIFEST, "backup-1", 1, KeyState.ACTIVE)
    authority = DormantKeyAuthority(
        KeyRingMetadata((claim, backup)),
        {
            KeyReference(item.family, item.key_id, item.generation): KEY
            for item in (claim, backup)
        },
        workspace_id=_ring_workspace(),
    )
    workspace_id = _ring_workspace()
    incarnation_id = IncarnationId.new()
    epoch = AuthorityEpoch(5)
    profile = _budget_profile()
    manifest = BackupManifestV3.issue(
        backup_generation_id=BackupGenerationId.new(),
        authority_generation=1,
        db_snapshot_identity_digest="1" * 64,
        workspace_id=workspace_id,
        incarnation_id=incarnation_id,
        authority_epoch=epoch,
        head_revision=HeadRevision(7),
        claim_digest="2" * 64,
        claim_key_generation=1,
        blob_inventory_digest="3" * 64,
        tombstone_inventory_digest="4" * 64,
        required_keys=(
            RequiredKeyReference(KeyFamily.CLAIM_ENROLLMENT, 1, "claim-1"),
            RequiredKeyReference(KeyFamily.BACKUP_MANIFEST, 1, "backup-1"),
        ),
        budget_profile_digest=profile.digest(),
        predecessor=BackupPredecessor.genesis(
            workspace_id=workspace_id,
            incarnation_id=incarnation_id,
            authority_epoch=epoch,
        ),
        signing_key=authority.signing_capability(
            family=KeyFamily.BACKUP_MANIFEST,
            generation=1,
            key_id="backup-1",
        ),
    )
    manifest.validate_required_keys(authority)

    closure = manifest.validate_closure(
        key_authority=authority,
        verification_key=authority.verification_capability(
            family=KeyFamily.BACKUP_MANIFEST,
            generation=1,
            key_id="backup-1",
        ),
        budget_profile=profile,
        previous_closure=None,
    )
    assert closure.manifest is manifest

    # VERIFY_ONLY still satisfies required-key validation; RETIRED does not.
    authority.install_metadata(
        KeyRingMetadata((replace(claim, state=KeyState.VERIFY_ONLY), backup))
    )
    manifest.validate_required_keys(authority)
    authority.install_metadata(
        KeyRingMetadata((replace(claim, state=KeyState.RETIRED), backup))
    )
    with pytest.raises(ValueError, match="STALE_KEY_BACKUP"):
        manifest.validate_required_keys(authority)


# ---------------------------------------------------------------------------
# 6. Serialization / reconstruction control.
# ---------------------------------------------------------------------------


def test_check3_sealed_required_keys_survive_serialization_and_ordering() -> None:
    manifest, _authority = _stale_manifest()
    baseline_bytes = manifest.canonical_bytes()
    baseline_digest = manifest.manifest_digest
    sealed = [contracts_module._sealed_required_key(item) for item in manifest.required_keys]

    with (
        substituted(RequiredKeyReference, "generation", property(lambda self: 99)),
        substituted(RequiredKeyReference, "key_id", property(lambda self: "claim-99")),
    ):
        # The sealed identity is unmoved...
        assert [
            contracts_module._sealed_required_key(item) for item in manifest.required_keys
        ] == sealed
        # ...and serializing the constructed record fails closed rather than emitting
        # the substituted values as authority.
        with pytest.raises(ValueError, match="changed after sealed construction"):
            manifest.canonical_bytes()

    assert manifest.canonical_bytes() == baseline_bytes
    assert manifest.manifest_digest == baseline_digest


def test_check3_required_key_ordering_and_duplicates_use_the_sealed_identity() -> None:
    """Canonical ordering and duplicate detection must not be steerable either."""

    unsorted = (
        RequiredKeyReference(KeyFamily.CLAIM_ENROLLMENT, 2, "claim-2"),
        RequiredKeyReference(KeyFamily.BACKUP_MANIFEST, 1, "backup-1"),
        RequiredKeyReference(KeyFamily.CLAIM_ENROLLMENT, 1, "claim-1"),
    )
    expected_keys = [contracts_module._required_key_sort_key(item) for item in unsorted]
    expected_order = [
        contracts_module._sealed_required_key(item)
        for item in sorted(unsorted, key=contracts_module._required_key_sort_key)
    ]
    with substituted(RequiredKeyReference, "generation", property(lambda self: 7)):
        assert [
            contracts_module._required_key_sort_key(item) for item in unsorted
        ] == expected_keys, "canonical ordering key followed a replaced descriptor"
        ordered = tuple(sorted(unsorted, key=contracts_module._required_key_sort_key))
    assert [contracts_module._sealed_required_key(item) for item in ordered] == expected_order

    with pytest.raises(TypeError, match="exact RequiredKeyReference"):
        contracts_module._sealed_required_key(object())


def test_check3_backup_predecessor_descriptor_cannot_forge_chain_continuity() -> None:
    """Sibling of the same root: the predecessor chain is compared on sealed values."""

    claim = _generation(KeyFamily.CLAIM_ENROLLMENT, "claim-1", 1, KeyState.ACTIVE)
    backup = _generation(KeyFamily.BACKUP_MANIFEST, "backup-1", 1, KeyState.ACTIVE)
    authority = DormantKeyAuthority(
        KeyRingMetadata((claim, backup)),
        {
            KeyReference(item.family, item.key_id, item.generation): KEY
            for item in (claim, backup)
        },
        workspace_id=_ring_workspace(),
    )
    workspace_id = _ring_workspace()
    incarnation_id = IncarnationId.new()
    epoch = AuthorityEpoch(5)
    profile = _budget_profile()
    required = (
        RequiredKeyReference(KeyFamily.CLAIM_ENROLLMENT, 1, "claim-1"),
        RequiredKeyReference(KeyFamily.BACKUP_MANIFEST, 1, "backup-1"),
    )
    signing_key = authority.signing_capability(
        family=KeyFamily.BACKUP_MANIFEST, generation=1, key_id="backup-1"
    )
    verification_key = authority.verification_capability(
        family=KeyFamily.BACKUP_MANIFEST, generation=1, key_id="backup-1"
    )

    def issue(generation: int, predecessor, head: int, blob: str):
        return BackupManifestV3.issue(
            backup_generation_id=BackupGenerationId.new(),
            authority_generation=generation,
            db_snapshot_identity_digest="1" * 64,
            workspace_id=workspace_id,
            incarnation_id=incarnation_id,
            authority_epoch=epoch,
            head_revision=HeadRevision(head),
            claim_digest="2" * 64,
            claim_key_generation=1,
            blob_inventory_digest=blob,
            tombstone_inventory_digest="4" * 64,
            required_keys=required,
            budget_profile_digest=profile.digest(),
            predecessor=predecessor,
            signing_key=signing_key,
        )

    first = issue(
        1,
        BackupPredecessor.genesis(
            workspace_id=workspace_id,
            incarnation_id=incarnation_id,
            authority_epoch=epoch,
        ),
        7,
        "3" * 64,
    )
    first_closure = first.validate_closure(
        key_authority=authority,
        verification_key=verification_key,
        budget_profile=profile,
        previous_closure=None,
    )
    wrong_chain = issue(
        2,
        BackupPredecessor.completed(
            workspace_id=workspace_id,
            incarnation_id=incarnation_id,
            authority_epoch=epoch,
            authority_generation=1,
            manifest_digest="9" * 64,
        ),
        8,
        "7" * 64,
    )

    def validate_wrong() -> None:
        wrong_chain.validate_closure(
            key_authority=authority,
            verification_key=verification_key,
            budget_profile=profile,
            previous_closure=first_closure,
        )

    with pytest.raises(ValueError, match="wrong, stale, future"):
        validate_wrong()

    with substituted(
        BackupPredecessor,
        "manifest_digest",
        property(lambda self: first.manifest_digest),
    ):
        with pytest.raises(ValueError, match=r"wrong, stale, future|changed after sealed construction"):
            validate_wrong()

    # The legitimate chain still validates.
    issue(
        2,
        BackupPredecessor.completed(
            workspace_id=workspace_id,
            incarnation_id=incarnation_id,
            authority_epoch=epoch,
            authority_generation=1,
            manifest_digest=first.manifest_digest,
        ),
        8,
        "7" * 64,
    ).validate_closure(
        key_authority=authority,
        verification_key=verification_key,
        budget_profile=profile,
        previous_closure=first_closure,
    )


# ---------------------------------------------------------------------------
# 7. Previously repaired descriptor hardening must not regress.
# ---------------------------------------------------------------------------


def test_check3_key_generation_state_hardening_is_preserved() -> None:
    """The reviewer's KeyGenerationMetadata.state reprobe, kept as a standing control."""

    claim = _generation(KeyFamily.CLAIM_ENROLLMENT, "claim-1", 1, KeyState.ACTIVE)
    authority = DormantKeyAuthority(
        KeyRingMetadata((claim,)),
        {KeyReference(KeyFamily.CLAIM_ENROLLMENT, "claim-1", 1): KEY},
        workspace_id=_ring_workspace(),
    )
    stale_signer = authority.signing_capability(
        family=KeyFamily.CLAIM_ENROLLMENT,
        generation=1,
        key_id="claim-1",
    )
    authority.install_metadata(
        KeyRingMetadata((replace(claim, state=KeyState.REVOKED_COMPROMISED),))
    )
    real_state = KeyGenerationMetadata.__dict__["state"]

    def two_faced_state(self: object) -> KeyState:
        for frame in inspect.stack():
            if frame.function in _SERIALIZATION_FRAMES:
                return real_state.__get__(self, KeyGenerationMetadata)
        return KeyState.ACTIVE

    with substituted(KeyGenerationMetadata, "state", property(two_faced_state)):
        with pytest.raises(ValueError, match="only current ACTIVE key authority may issue"):
            authority.signing_capability(
                family=KeyFamily.CLAIM_ENROLLMENT,
                generation=1,
                key_id="claim-1",
            )
        with pytest.raises(ValueError, match="key authority is not current for requested use"):
            stale_signer.sign(
                expected_family=KeyFamily.CLAIM_ENROLLMENT,
                domain="VOOL_CHECK3_RK_V3",
                message=b"payload",
            )


def test_check3_phase0_stays_dormant() -> None:
    from core.workspace_authority_v3 import EXECUTION_AUTHORITY

    assert EXECUTION_AUTHORITY is False
    assert DormantKeyAuthority.execution_authority is False
