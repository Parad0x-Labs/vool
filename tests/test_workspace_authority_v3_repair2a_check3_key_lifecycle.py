"""Check-3 repair: key lifecycle is decided from sealed key-record reads.

``KeyGenerationMetadata`` fields stay replaceable after class definition, so a property
installed later could answer correctly to sealed-record validation and ``ACTIVE`` to a
key-authority permission decision.  The boundary now reads key metadata and key references
through descriptors captured at import, so a replacement cannot redirect a decision.

Every substitution here is withdrawn in a ``finally``; the two-faced property is the
strongest shape, answering correctly to the serialization frames so the sealed-record
fingerprint does not catch it first.
"""

from __future__ import annotations

import inspect
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace

import pytest

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
    SigningKeyCapability,
    VerificationKeyCapability,
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

KEY = b"c" * 32

# Frames that legitimately serialize a record.  The attacker answers correctly to these so the
# sealed-record fingerprint agrees, and lies only to key-authority lifecycle code.
_SERIALIZATION_FRAMES = frozenset(
    {"payload_fingerprint", "encode", "seal_record", "envelope_record", "require_spec"}
)


@contextmanager
def substituted(owner: type, name: str, value: object) -> Iterator[None]:
    original = owner.__dict__.get(name)
    had_own = name in owner.__dict__
    setattr(owner, name, value)
    try:
        yield
    finally:
        if had_own:
            setattr(owner, name, original)
        else:
            delattr(owner, name)


@contextmanager
def two_faced_state(lie: KeyState = KeyState.ACTIVE) -> Iterator[None]:
    """Answers correctly to sealed-record serialization, ``lie`` to everything else."""

    slot = KeyGenerationMetadata.__dict__["state"]

    def state(self: object) -> KeyState:
        truth = slot.__get__(self, KeyGenerationMetadata)
        for frame in inspect.stack():
            if frame.function in _SERIALIZATION_FRAMES:
                return truth
        return lie

    with substituted(KeyGenerationMetadata, "state", property(state)):
        yield


def _ring_workspace() -> WorkspaceId:
    domain = AuthorityDomainId.from_authority_claim(AuthorityClaimProvenance.establish())
    return WorkspaceId.from_enrollment(
        WorkspaceEnrollmentProvenance.establish(
            authority_domain_id=domain,
            enrollment_operation_id=EnrollmentOperationId.new(),
        )
    )


def _generation(
    family: KeyFamily = KeyFamily.CLAIM_ENROLLMENT,
    key_id: str = "claim-1",
    generation: int = 1,
    state: KeyState = KeyState.ACTIVE,
) -> KeyGenerationMetadata:
    return KeyGenerationMetadata(family, key_id, generation, state, "HMAC-SHA256", 1)


def _authority(*generations: KeyGenerationMetadata) -> DormantKeyAuthority:
    items = generations or (_generation(),)
    return DormantKeyAuthority(
        KeyRingMetadata(items),
        {
            KeyReference(item.family, item.key_id, item.generation): KEY
            for item in items
            if item.family is not KeyFamily.JOURNAL_EFFECT_RECEIPT
        },
        workspace_id=_ring_workspace(),
    )


# ---------------------------------------------------------------------------
# Signing issuance on a non-signing generation.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "revoked_state",
    (
        KeyState.VERIFY_ONLY,
        KeyState.RETIRED,
        KeyState.REVOKED_COMPROMISED,
        KeyState.LOST,
    ),
)
def test_check3_non_signing_generation_cannot_issue_signing_under_state_property(
    revoked_state: KeyState,
) -> None:
    active = _generation()
    authority = _authority(active)
    authority.install_metadata(KeyRingMetadata((replace(active, state=revoked_state),)))

    with two_faced_state(KeyState.ACTIVE):
        with pytest.raises(ValueError, match="ACTIVE key authority"):
            authority.signing_capability(
                family=KeyFamily.CLAIM_ENROLLMENT,
                generation=1,
                key_id="claim-1",
            )


@pytest.mark.parametrize(
    "unverifiable_state",
    (KeyState.RETIRED, KeyState.REVOKED_COMPROMISED, KeyState.LOST),
)
def test_check3_non_verifiable_generation_cannot_issue_verification_under_state_property(
    unverifiable_state: KeyState,
) -> None:
    active = _generation()
    authority = _authority(active)
    authority.install_metadata(KeyRingMetadata((replace(active, state=unverifiable_state),)))

    with two_faced_state(KeyState.ACTIVE):
        with pytest.raises(ValueError, match="cannot issue a verification lease"):
            authority.verification_capability(
                family=KeyFamily.CLAIM_ENROLLMENT,
                generation=1,
                key_id="claim-1",
            )


def test_check3_pending_generation_cannot_issue_either_lease_under_state_property() -> None:
    """PENDING is reachable only at construction; ACTIVE is not a legal predecessor."""

    authority = _authority(_generation(state=KeyState.PENDING))

    with two_faced_state(KeyState.ACTIVE):
        with pytest.raises(ValueError, match="ACTIVE key authority"):
            authority.signing_capability(
                family=KeyFamily.CLAIM_ENROLLMENT,
                generation=1,
                key_id="claim-1",
            )
        with pytest.raises(ValueError, match="cannot issue a verification lease"):
            authority.verification_capability(
                family=KeyFamily.CLAIM_ENROLLMENT,
                generation=1,
                key_id="claim-1",
            )


def test_check3_verification_remains_allowed_exactly_where_the_contract_allows_it() -> None:
    active = _generation()
    authority = _authority(active)
    verifier = authority.verification_capability(
        family=KeyFamily.CLAIM_ENROLLMENT,
        generation=1,
        key_id="claim-1",
    )
    signer = authority.signing_capability(
        family=KeyFamily.CLAIM_ENROLLMENT,
        generation=1,
        key_id="claim-1",
    )
    mac = signer.sign(
        expected_family=KeyFamily.CLAIM_ENROLLMENT,
        domain="VOOL_CHECK3_V3",
        message=b"historical",
    )

    # VERIFY_ONLY: historical verification allowed, signing denied.
    authority.install_metadata(KeyRingMetadata((replace(active, state=KeyState.VERIFY_ONLY),)))
    assert verifier.state is KeyState.VERIFY_ONLY
    assert verifier.verify(
        expected_family=KeyFamily.CLAIM_ENROLLMENT,
        domain="VOOL_CHECK3_V3",
        message=b"historical",
        supplied_mac=mac,
    )
    with pytest.raises(ValueError, match="not current"):
        signer.sign(
            expected_family=KeyFamily.CLAIM_ENROLLMENT,
            domain="VOOL_CHECK3_V3",
            message=b"historical",
        )

    # REVOKED_COMPROMISED: no trusted verification either.
    authority.install_metadata(
        KeyRingMetadata((replace(active, state=KeyState.REVOKED_COMPROMISED),))
    )
    with pytest.raises(ValueError, match="not current"):
        verifier.verify(
            expected_family=KeyFamily.CLAIM_ENROLLMENT,
            domain="VOOL_CHECK3_V3",
            message=b"historical",
            supplied_mac=mac,
        )


# ---------------------------------------------------------------------------
# Use-time validation with a stale capability.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "terminal_state",
    (
        KeyState.VERIFY_ONLY,
        KeyState.RETIRED,
        KeyState.REVOKED_COMPROMISED,
        KeyState.LOST,
    ),
)
def test_check3_stale_signer_cannot_sign_under_state_property(terminal_state: KeyState) -> None:
    active = _generation()
    authority = _authority(active)
    stale = authority.signing_capability(
        family=KeyFamily.CLAIM_ENROLLMENT,
        generation=1,
        key_id="claim-1",
    )
    authority.install_metadata(KeyRingMetadata((replace(active, state=terminal_state),)))

    with two_faced_state(KeyState.ACTIVE):
        with pytest.raises(ValueError, match="not current"):
            stale.sign(
                expected_family=KeyFamily.CLAIM_ENROLLMENT,
                domain="VOOL_CHECK3_V3",
                message=b"payload",
            )


@pytest.mark.parametrize(
    "terminal_state",
    (KeyState.RETIRED, KeyState.REVOKED_COMPROMISED, KeyState.LOST),
)
def test_check3_stale_verifier_cannot_verify_under_state_property(
    terminal_state: KeyState,
) -> None:
    active = _generation()
    authority = _authority(active)
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
    mac = signer.sign(
        expected_family=KeyFamily.CLAIM_ENROLLMENT,
        domain="VOOL_CHECK3_V3",
        message=b"payload",
    )
    authority.install_metadata(KeyRingMetadata((replace(active, state=terminal_state),)))

    with two_faced_state(KeyState.ACTIVE):
        with pytest.raises(ValueError, match="not current"):
            verifier.verify(
                expected_family=KeyFamily.CLAIM_ENROLLMENT,
                domain="VOOL_CHECK3_V3",
                message=b"payload",
                supplied_mac=mac,
            )


def test_check3_capability_state_reports_the_sealed_lifecycle_state() -> None:
    active = _generation()
    authority = _authority(active)
    verifier = authority.verification_capability(
        family=KeyFamily.CLAIM_ENROLLMENT,
        generation=1,
        key_id="claim-1",
    )
    authority.install_metadata(KeyRingMetadata((replace(active, state=KeyState.VERIFY_ONLY),)))
    with two_faced_state(KeyState.ACTIVE):
        assert verifier.state is KeyState.VERIFY_ONLY


# ---------------------------------------------------------------------------
# Same-root analogues: the other key-record fields.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "forged"),
    (
        ("key_id", "claim-forged"),
        ("algorithm", "NOT-HMAC"),
        ("algorithm_version", 99),
        ("generation", 99),
    ),
)
def test_check3_key_record_field_property_cannot_redirect_issuance(
    field: str,
    forged: object,
) -> None:
    """A replaced descriptor must not be able to move the reference an issuance resolves."""

    authority = _authority(_generation())
    slot = KeyGenerationMetadata.__dict__[field]

    def two_faced(self: object) -> object:
        for frame in inspect.stack():
            if frame.function in _SERIALIZATION_FRAMES:
                return slot.__get__(self, KeyGenerationMetadata)
        return forged

    with substituted(KeyGenerationMetadata, field, property(two_faced)):
        # The real reference still resolves; the forged view never becomes authority.
        capability = authority.signing_capability(
            family=KeyFamily.CLAIM_ENROLLMENT,
            generation=1,
            key_id="claim-1",
        )
        assert capability.key_id == "claim-1"
        assert capability.generation == 1
        assert capability.family is KeyFamily.CLAIM_ENROLLMENT


def test_check3_key_reference_property_cannot_forge_a_cross_family_mac() -> None:
    """The MAC context and the key material both come from the sealed reference.

    Substituting only ``family`` fails closed on the material lookup and proves nothing;
    substituting ``family`` and ``key_id`` together keeps the lookup consistent and
    reaches the MAC, which is the shape that matters.  On a ring whose references share
    material bytes -- what ``workspace_authority_v3_fixtures.key_authority`` builds -- a
    successful redirect would mint a MAC byte-identical to the other family's.
    """

    shared = b"s" * 32
    generations = (
        _generation(KeyFamily.CLAIM_ENROLLMENT, "claim-1", 1),
        _generation(KeyFamily.CURSOR, "cursor-1", 1),
    )
    authority = DormantKeyAuthority(
        KeyRingMetadata(generations),
        {
            KeyReference(item.family, item.key_id, item.generation): shared
            for item in generations
        },
        workspace_id=_ring_workspace(),
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
    genuine_cursor = cursor_signer.sign(
        expected_family=KeyFamily.CURSOR,
        domain="VOOL_CHECK3_V3",
        message=b"payload",
    )
    baseline_claim = claim_signer.sign(
        expected_family=KeyFamily.CLAIM_ENROLLMENT,
        domain="VOOL_CHECK3_V3",
        message=b"payload",
    )
    assert genuine_cursor != baseline_claim

    # The boundary refuses the direct route outright.
    with pytest.raises(ValueError, match="key family does not match"):
        claim_signer.sign(
            expected_family=KeyFamily.CURSOR,
            domain="VOOL_CHECK3_V3",
            message=b"payload",
        )

    produced = None
    with (
        substituted(KeyReference, "family", property(lambda self: KeyFamily.CURSOR)),
        substituted(KeyReference, "key_id", property(lambda self: "cursor-1")),
    ):
        try:
            produced = claim_signer.sign(
                expected_family=KeyFamily.CLAIM_ENROLLMENT,
                domain="VOOL_CHECK3_V3",
                message=b"payload",
            )
        except (TypeError, ValueError, KeyError):
            produced = None

    # Whatever happened, it must not be the CURSOR family's MAC.
    assert produced != genuine_cursor
    assert produced in (baseline_claim, None)

    # A separate, never-substituted CURSOR verifier must reject it.
    cursor_verifier = authority.verification_capability(
        family=KeyFamily.CURSOR,
        generation=1,
        key_id="cursor-1",
    )
    if produced is not None:
        assert not cursor_verifier.verify(
            expected_family=KeyFamily.CURSOR,
            domain="VOOL_CHECK3_V3",
            message=b"payload",
            supplied_mac=produced,
        )
    assert cursor_verifier.verify(
        expected_family=KeyFamily.CURSOR,
        domain="VOOL_CHECK3_V3",
        message=b"payload",
        supplied_mac=genuine_cursor,
    )


def test_check3_key_reference_property_cannot_move_a_single_family_context() -> None:
    """Even on a one-reference ring the bound context must not follow the descriptor."""

    authority = _authority(_generation())
    signer = authority.signing_capability(
        family=KeyFamily.CLAIM_ENROLLMENT,
        generation=1,
        key_id="claim-1",
    )
    baseline = signer.sign(
        expected_family=KeyFamily.CLAIM_ENROLLMENT,
        domain="VOOL_CHECK3_V3",
        message=b"payload",
    )
    for field, forged in (("key_id", "claim-forged"), ("generation", 99)):
        produced = None
        with substituted(KeyReference, field, property(lambda self, v=forged: v)):
            try:
                produced = signer.sign(
                    expected_family=KeyFamily.CLAIM_ENROLLMENT,
                    domain="VOOL_CHECK3_V3",
                    message=b"payload",
                )
            except (TypeError, ValueError, KeyError):
                produced = None
        assert produced in (baseline, None), f"{field} moved the bound MAC context"


def test_check3_backup_required_key_validation_uses_the_closed_lifecycle() -> None:
    active_claim = _generation(KeyFamily.CLAIM_ENROLLMENT, "claim-1", 1)
    active_backup = _generation(KeyFamily.BACKUP_MANIFEST, "backup-1", 1)
    authority = _authority(active_claim, active_backup)
    workspace_id = _ring_workspace()
    incarnation_id = IncarnationId.new()
    epoch = AuthorityEpoch(5)
    profile = AuthorityBudgetProfile(
        profile_version=3,
        **{
            name: 10_000
            for name in (
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
        },
    )
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

    authority.install_metadata(
        KeyRingMetadata(
            (replace(active_claim, state=KeyState.RETIRED), active_backup),
        )
    )
    with two_faced_state(KeyState.ACTIVE):
        with pytest.raises(ValueError, match="STALE_KEY_BACKUP"):
            manifest.validate_required_keys(authority)


# ---------------------------------------------------------------------------
# Exact-type discipline is unaffected.
# ---------------------------------------------------------------------------


def test_check3_lookalike_capability_objects_still_fail_exact_type_checks() -> None:
    authority = _authority(_generation())

    for capability_type in (SigningKeyCapability, VerificationKeyCapability):
        forged = object.__new__(capability_type)
        object.__setattr__(forged, "family", KeyFamily.CLAIM_ENROLLMENT)
        object.__setattr__(forged, "generation", 1)
        object.__setattr__(forged, "key_id", "claim-1")
        assert authority.owns_capability(forged) is False

    forged_signer = object.__new__(SigningKeyCapability)
    object.__setattr__(forged_signer, "family", KeyFamily.CLAIM_ENROLLMENT)
    object.__setattr__(forged_signer, "generation", 1)
    object.__setattr__(forged_signer, "key_id", "claim-1")
    with pytest.raises(TypeError, match="not issued"):
        forged_signer.sign(
            expected_family=KeyFamily.CLAIM_ENROLLMENT,
            domain="VOOL_CHECK3_V3",
            message=b"payload",
        )

    with pytest.raises(TypeError, match="final"):

        class HostileSigner(SigningKeyCapability):
            pass

    with pytest.raises(TypeError, match="final"):

        class HostileMetadata(KeyGenerationMetadata):
            pass


def test_check3_sealed_key_record_read_requires_the_exact_record_type() -> None:
    from core.workspace_authority_v3.contracts import _read_key_record_slot

    with pytest.raises(TypeError, match="exact KeyGenerationMetadata"):
        _read_key_record_slot(KeyGenerationMetadata, "state", object())
    with pytest.raises(TypeError, match="not a sealed key-record field"):
        _read_key_record_slot(KeyGenerationMetadata, "not_a_field", _generation())


# ---------------------------------------------------------------------------
# The legitimate path is preserved.
# ---------------------------------------------------------------------------


def test_check3_normal_rotation_and_use_are_unchanged() -> None:
    first = _generation()
    authority = _authority(first)
    signer = authority.signing_capability(
        family=KeyFamily.CLAIM_ENROLLMENT,
        generation=1,
        key_id="claim-1",
    )
    mac = signer.sign(
        expected_family=KeyFamily.CLAIM_ENROLLMENT,
        domain="VOOL_CHECK3_V3",
        message=b"payload",
    )
    verifier = authority.verification_capability(
        family=KeyFamily.CLAIM_ENROLLMENT,
        generation=1,
        key_id="claim-1",
    )
    assert verifier.verify(
        expected_family=KeyFamily.CLAIM_ENROLLMENT,
        domain="VOOL_CHECK3_V3",
        message=b"payload",
        supplied_mac=mac,
    )

    second = _generation(key_id="claim-2", generation=2)
    authority.install_metadata(
        KeyRingMetadata((replace(first, state=KeyState.VERIFY_ONLY), second)),
        key_materials={KeyReference(KeyFamily.CLAIM_ENROLLMENT, "claim-2", 2): b"n" * 32},
    )
    rotated = authority.signing_capability(
        family=KeyFamily.CLAIM_ENROLLMENT,
        generation=2,
        key_id="claim-2",
    )
    assert rotated.sign(
        expected_family=KeyFamily.CLAIM_ENROLLMENT,
        domain="VOOL_CHECK3_V3",
        message=b"payload",
    ) != mac
    assert authority.metadata.find(KeyFamily.CLAIM_ENROLLMENT, 2) is not None
    assert authority.metadata.find(KeyFamily.CLAIM_ENROLLMENT, 9) is None


def test_check3_phase0_stays_dormant() -> None:
    from core.workspace_authority_v3 import EXECUTION_AUTHORITY

    assert EXECUTION_AUTHORITY is False
    assert DormantKeyAuthority.execution_authority is False
