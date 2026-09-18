"""Repair #2A, Lane C: authenticated decode must prove domain provenance, not self-consistency.

D6: an authenticated identity decode used to prove only that *some* key authority signed a
record whose fields agreed with each other and with that authority's own key reference.  It
never asked whether the authenticating authority was entitled to the authority domain the
record names.  An unrelated ``DormantKeyAuthority`` could therefore hand-build an
``EnrollmentClaimV3`` naming another domain's exact stored identity text, sign it with its own
CLAIM_ENROLLMENT key -- so ``authority_key_id`` and ``key_generation`` matched its own
capability perfectly -- and receive genuinely owner-issued ``AuthorityDomainId`` /
``WorkspaceId`` objects that then passed every downstream owned/context check as if they had
come from the real domain's authority.

The repair anchors every logical key ring to exactly one authority domain, inside the closed
key-authority boundary, and makes authenticated decode compare the record's
``authority_domain_id`` against that anchor.  The anchor is text so that it is durable: a
restart reconstructs it from storage rather than re-minting the very ``AuthorityDomainId``
whose decode it is there to authorize.

This module also covers the key-state analogue one layer earlier: ``KeyRingMetadata`` decided
its structural ACTIVE-generation and deferred-family invariants by reading ``KeyState.ACTIVE``
and ``KeyFamily.JOURNAL_EFFECT_RECEIPT`` live on every construction, and the deferred-family
gate inside the closed boundary read a module-level status constant.
"""

from __future__ import annotations

import contextlib

import pytest

from core.workspace_authority_v3 import contracts as contracts_module
from core.workspace_authority_v3.base import (
    ContractValidationError,
    dormant_envelope_record,
)
from core.workspace_authority_v3.canonical import canonical_bytes, typed_sha256
from core.workspace_authority_v3.contracts import (
    DormantKeyAuthority,
    EnrollmentClaimV3,
    KeyFamily,
    KeyFamilyPhase0Status,
    KeyGenerationMetadata,
    KeyReference,
    KeyRingMetadata,
    KeyState,
    key_family_phase0_status,
    require_verification_domain_authority,
    sign_with_key_capability,
)
from core.workspace_authority_v3.cursor import (
    AuthenticatedCursorPayload,
    CursorCodec,
    CursorDirection,
    CursorExpectation,
)
from core.workspace_authority_v3.identity import (
    AuthorityClaimProvenance,
    AuthorityDomainId,
    AuthorityEpoch,
    EnrollmentOperationId,
    IncarnationId,
    RuntimeHomeId,
    WorkspaceEnrollmentProvenance,
    WorkspaceId,
    WorkspaceSequence,
    _issue_authenticated_identity_decode,
    canonical_identity_text,
    require_identity_context,
)
from tests.workspace_authority_v3_fixtures import ring_workspace_id

KEY = b"k" * 32
FOREIGN_KEY = b"f" * 32


@contextlib.contextmanager
def class_attribute(owner: type, name: str, value: object):
    """Redirect a class attribute the way ``type.__setattr__`` can, bypassing EnumMeta's guard."""

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
def module_attribute(module: object, name: str, value: object):
    original = getattr(module, name)
    setattr(module, name, value)
    try:
        yield
    finally:
        setattr(module, name, original)


# ---------------------------------------------------------------------------
# Shared construction.
# ---------------------------------------------------------------------------


def _key_metadata(family: KeyFamily, key_id: str, generation: int, state: KeyState):
    return KeyGenerationMetadata(family, key_id, generation, state, "HMAC-SHA256", 1)


def _claim_ring(
    authority_domain: AuthorityDomainId | str | None,
    *,
    material: bytes = KEY,
    key_id: str = "claim-1",
) -> DormantKeyAuthority:
    metadata = KeyRingMetadata(
        (
            _key_metadata(KeyFamily.CLAIM_ENROLLMENT, key_id, 1, KeyState.ACTIVE),
            _key_metadata(KeyFamily.CURSOR, f"cursor-{key_id}", 1, KeyState.ACTIVE),
        )
    )
    return DormantKeyAuthority(
        metadata,
        {
            KeyReference(KeyFamily.CLAIM_ENROLLMENT, key_id, 1): material,
            KeyReference(KeyFamily.CURSOR, f"cursor-{key_id}", 1): material,
        },
        workspace_id=ring_workspace_id(),
        authority_domain=authority_domain,
    )


def _enrollment_root(domain: AuthorityDomainId):
    enrollment_operation = EnrollmentOperationId.new()
    workspace = WorkspaceId.from_enrollment(
        WorkspaceEnrollmentProvenance.establish(
            authority_domain_id=domain,
            enrollment_operation_id=enrollment_operation,
        )
    )
    return enrollment_operation, workspace


def _issue_claim(
    authority: DormantKeyAuthority,
    domain: AuthorityDomainId,
    *,
    key_id: str = "claim-1",
) -> EnrollmentClaimV3:
    enrollment_operation, workspace = _enrollment_root(domain)
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
            key_id=key_id,
        ),
    )


def _handcrafted_claim(
    signer_authority: DormantKeyAuthority,
    *,
    authority_domain_text: str,
    workspace_text: str,
    key_id: str = "claim-1",
) -> tuple[bytes, str, bytes]:
    """Build a self-consistent EnrollmentClaimV3 naming arbitrary stored identity text.

    Returns ``(unsigned_record_bytes, claim_mac, full_record_bytes)``.  Every field agrees
    with every other field, the key reference is the signer's own, and the MAC is genuinely
    produced by the signer's own CLAIM_ENROLLMENT key.  Nothing here is tampered with -- this
    is exactly what an unrelated key authority can legitimately produce for itself.
    """

    signing_key = signer_authority.signing_capability(
        family=KeyFamily.CLAIM_ENROLLMENT,
        generation=1,
        key_id=key_id,
    )
    unsigned = dormant_envelope_record(
        record_type="EnrollmentClaimV3",
        schema_version=3,
        payload={
            "algorithm": "HMAC-SHA256",
            "algorithm_version": 1,
            "authority_domain_id": authority_domain_text,
            "authority_key_id": key_id,
            "enrollment_operation_id": str(EnrollmentOperationId.new()),
            "incarnation_id": str(IncarnationId.new()),
            "incarnation_nonce": "1" * 64,
            "key_generation": 1,
            "persistent_volume_identity_digest": "2" * 64,
            "root_object_identity_digest": "3" * 64,
            "runtime_home_id": str(RuntimeHomeId.new()),
            "workspace_id": workspace_text,
        },
    )
    unsigned_bytes = canonical_bytes(unsigned)
    claim_digest = typed_sha256("VOOL_ENROLLMENT_CLAIM_V3", unsigned_bytes)
    claim_mac = sign_with_key_capability(
        signing_key,
        expected_family=KeyFamily.CLAIM_ENROLLMENT,
        domain="VOOL_ENROLLMENT_CLAIM_MAC_V3",
        message=claim_digest.encode("ascii"),
    )
    full = dict(unsigned)
    full["payload"] = {**unsigned["payload"], "claim_digest": claim_digest, "claim_mac": claim_mac}
    return unsigned_bytes, claim_mac, canonical_bytes(full)


# ---------------------------------------------------------------------------
# D6.1 Legitimate authority-domain / key-ring context still decodes.
# ---------------------------------------------------------------------------


def test_lanec_entitled_ring_decodes_its_own_domain_record() -> None:
    domain = AuthorityDomainId.from_authority_claim(AuthorityClaimProvenance.establish())
    authority = _claim_ring(domain)
    claim = _issue_claim(authority, domain)
    verifier = authority.verification_capability(
        family=KeyFamily.CLAIM_ENROLLMENT, generation=1, key_id="claim-1"
    )

    assert authority.authority_domain == canonical_identity_text(domain, AuthorityDomainId)
    decoded = EnrollmentClaimV3.from_json(claim.canonical_bytes(), verification_key=verifier)
    assert decoded == claim
    assert decoded.authority_domain_id == domain
    assert decoded.verify_mac(verifier)

    # The decoded workspace carries the domain as owner-recorded provenance, not as a
    # field the caller happened to pass alongside it.
    require_identity_context(
        decoded.workspace_id,
        WorkspaceId,
        authority_domain_id=decoded.authority_domain_id,
    )


def test_lanec_ring_reports_exactly_one_immutable_authority_domain() -> None:
    domain = AuthorityDomainId.from_authority_claim(AuthorityClaimProvenance.establish())
    authority = _claim_ring(domain)
    anchor = authority.authority_domain
    assert anchor == canonical_identity_text(domain, AuthorityDomainId)

    # A forward-only lifecycle install moves key state; it must not move the anchor.
    authority.install_metadata(
        KeyRingMetadata(
            (
                _key_metadata(KeyFamily.CLAIM_ENROLLMENT, "claim-1", 1, KeyState.VERIFY_ONLY),
                _key_metadata(KeyFamily.CURSOR, "cursor-claim-1", 1, KeyState.VERIFY_ONLY),
            )
        )
    )
    assert authority.authority_domain == anchor


def test_lanec_unanchored_ring_inherits_the_ring_workspace_enrollment_domain() -> None:
    """Without an explicit anchor the ring still has exactly one domain, from owner state."""

    domain = AuthorityDomainId.from_authority_claim(AuthorityClaimProvenance.establish())
    _enrollment_operation, ring_workspace = _enrollment_root(domain)
    authority = DormantKeyAuthority(
        KeyRingMetadata((_key_metadata(KeyFamily.CLAIM_ENROLLMENT, "claim-1", 1, KeyState.ACTIVE),)),
        {KeyReference(KeyFamily.CLAIM_ENROLLMENT, "claim-1", 1): KEY},
        workspace_id=ring_workspace,
    )
    assert authority.authority_domain == canonical_identity_text(domain, AuthorityDomainId)
    claim = _issue_claim(authority, domain)
    verifier = authority.verification_capability(
        family=KeyFamily.CLAIM_ENROLLMENT, generation=1, key_id="claim-1"
    )
    assert EnrollmentClaimV3.from_json(claim.canonical_bytes(), verification_key=verifier) == claim


# ---------------------------------------------------------------------------
# D6.2-D6.4 An unrelated key authority cannot authenticate another domain.
# ---------------------------------------------------------------------------


def test_lanec_unrelated_authority_cannot_authenticate_another_domain() -> None:
    """The reported defect, end to end, at the production ``from_json`` seam.

    Everything the old check looked at is satisfied: the record is exact canonical JSON, the
    claim digest binds it, ``authority_key_id``/``key_generation`` are the attacker's own, and
    the MAC is genuinely the attacker's.  Only the provenance is wrong.
    """

    victim_domain = AuthorityDomainId.from_authority_claim(AuthorityClaimProvenance.establish())
    _enrollment_operation, victim_workspace = _enrollment_root(victim_domain)

    attacker_domain = AuthorityDomainId.from_authority_claim(AuthorityClaimProvenance.establish())
    attacker = _claim_ring(attacker_domain, material=FOREIGN_KEY)
    attacker_verifier = attacker.verification_capability(
        family=KeyFamily.CLAIM_ENROLLMENT, generation=1, key_id="claim-1"
    )

    _unsigned, _mac, full_record = _handcrafted_claim(
        attacker,
        authority_domain_text=canonical_identity_text(victim_domain, AuthorityDomainId),
        workspace_text=canonical_identity_text(victim_workspace, WorkspaceId),
    )

    with pytest.raises(ValueError, match="not entitled to authenticate"):
        EnrollmentClaimV3.from_json(full_record, verification_key=attacker_verifier)


def test_lanec_unrelated_authority_cannot_mint_identities_at_the_decode_proof_seam() -> None:
    """The same rejection at the seam that actually mints owned identities."""

    victim_domain = AuthorityDomainId.from_authority_claim(AuthorityClaimProvenance.establish())
    _enrollment_operation, victim_workspace = _enrollment_root(victim_domain)
    victim_workspace_text = canonical_identity_text(victim_workspace, WorkspaceId)

    attacker_domain = AuthorityDomainId.from_authority_claim(AuthorityClaimProvenance.establish())
    attacker = _claim_ring(attacker_domain, material=FOREIGN_KEY)
    attacker_verifier = attacker.verification_capability(
        family=KeyFamily.CLAIM_ENROLLMENT, generation=1, key_id="claim-1"
    )
    unsigned, mac, _full = _handcrafted_claim(
        attacker,
        authority_domain_text=canonical_identity_text(victim_domain, AuthorityDomainId),
        workspace_text=victim_workspace_text,
    )

    for source_field, identity_type in (
        ("workspace_id", WorkspaceId),
        ("authority_domain_id", AuthorityDomainId),
        ("incarnation_id", IncarnationId),
    ):
        with pytest.raises(ValueError, match="not entitled to authenticate"):
            _issue_authenticated_identity_decode(
                verification_key=attacker_verifier,
                source_record=unsigned,
                supplied_mac=mac,
                record_type="EnrollmentClaimV3",
                source_field=source_field,
                identity_type=identity_type,
            )


def test_lanec_matching_key_reference_and_genuine_mac_are_not_sufficient() -> None:
    """Self-consistency is not provenance: the attacker's own record verifies, and is refused.

    The control matters as much as the kill.  The same bytes and the same MAC are accepted by
    the raw MAC check, so the rejection is the domain-entitlement decision and nothing else.
    """

    victim_domain = AuthorityDomainId.from_authority_claim(AuthorityClaimProvenance.establish())
    attacker_domain = AuthorityDomainId.from_authority_claim(AuthorityClaimProvenance.establish())
    attacker = _claim_ring(attacker_domain, material=FOREIGN_KEY)
    attacker_verifier = attacker.verification_capability(
        family=KeyFamily.CLAIM_ENROLLMENT, generation=1, key_id="claim-1"
    )
    _enrollment_operation, some_workspace = _enrollment_root(victim_domain)

    unsigned, mac, _full = _handcrafted_claim(
        attacker,
        authority_domain_text=canonical_identity_text(victim_domain, AuthorityDomainId),
        workspace_text=canonical_identity_text(some_workspace, WorkspaceId),
    )
    claim_digest = typed_sha256("VOOL_ENROLLMENT_CLAIM_V3", unsigned)

    # Cryptographically the attacker's own MAC over their own record: valid.
    assert attacker_verifier.verify(
        expected_family=KeyFamily.CLAIM_ENROLLMENT,
        domain="VOOL_ENROLLMENT_CLAIM_MAC_V3",
        message=claim_digest.encode("ascii"),
        supplied_mac=mac,
    )
    # The key reference the record names is the attacker's own capability.
    assert require_verification_domain_authority(
        attacker_verifier, expected_family=KeyFamily.CLAIM_ENROLLMENT
    ) == canonical_identity_text(attacker_domain, AuthorityDomainId)

    with pytest.raises(ValueError, match="not entitled to authenticate"):
        _issue_authenticated_identity_decode(
            verification_key=attacker_verifier,
            source_record=unsigned,
            supplied_mac=mac,
            record_type="EnrollmentClaimV3",
            source_field="workspace_id",
            identity_type=WorkspaceId,
        )


def test_lanec_entitled_ring_still_refuses_a_record_naming_a_different_domain() -> None:
    """Holding a genuine domain does not license authenticating a neighbouring one."""

    domain = AuthorityDomainId.from_authority_claim(AuthorityClaimProvenance.establish())
    other_domain = AuthorityDomainId.from_authority_claim(AuthorityClaimProvenance.establish())
    authority = _claim_ring(domain)
    verifier = authority.verification_capability(
        family=KeyFamily.CLAIM_ENROLLMENT, generation=1, key_id="claim-1"
    )
    _enrollment_operation, other_workspace = _enrollment_root(other_domain)
    unsigned, mac, _full = _handcrafted_claim(
        authority,
        authority_domain_text=canonical_identity_text(other_domain, AuthorityDomainId),
        workspace_text=canonical_identity_text(other_workspace, WorkspaceId),
    )
    with pytest.raises(ValueError, match="not entitled to authenticate"):
        _issue_authenticated_identity_decode(
            verification_key=verifier,
            source_record=unsigned,
            supplied_mac=mac,
            record_type="EnrollmentClaimV3",
            source_field="workspace_id",
            identity_type=WorkspaceId,
        )


def test_lanec_verify_mac_refuses_a_claim_outside_the_rings_domain() -> None:
    """The other authentication entry point on the same record answers the same way.

    Isolated deliberately: one ring signs both claims, so the MAC is valid in both cases and
    the only difference is which authority domain the record names.  A cross-ring pair would
    already fail on the MAC and would never reach the entitlement decision.
    """

    domain = AuthorityDomainId.from_authority_claim(AuthorityClaimProvenance.establish())
    other_domain = AuthorityDomainId.from_authority_claim(AuthorityClaimProvenance.establish())
    authority = _claim_ring(domain)
    verifier = authority.verification_capability(
        family=KeyFamily.CLAIM_ENROLLMENT, generation=1, key_id="claim-1"
    )

    own_domain_claim = _issue_claim(authority, domain)
    foreign_domain_claim = _issue_claim(authority, other_domain)

    # Same signer, same key reference, both MACs genuinely valid.
    assert verifier.verify(
        expected_family=KeyFamily.CLAIM_ENROLLMENT,
        domain="VOOL_ENROLLMENT_CLAIM_MAC_V3",
        message=foreign_domain_claim.claim_digest.encode("ascii"),
        supplied_mac=foreign_domain_claim.claim_mac,
    )
    assert own_domain_claim.verify_mac(verifier) is True
    assert foreign_domain_claim.verify_mac(verifier) is False


# ---------------------------------------------------------------------------
# D6.5 Restart / storage reconstruction stays possible.
# ---------------------------------------------------------------------------


def test_lanec_ring_anchored_from_stored_text_reconstructs_owned_identities() -> None:
    """Reconstruction must not require the identity whose decode it authorizes.

    The ring here is anchored by canonical authority-domain *text*, the way a restart reads
    it back from storage.  No owned ``AuthorityDomainId`` object exists for that domain when
    the ring is built -- the object comes back out of the authenticated decode afterwards.

    Scope note: this proves the anchor is reconstructible from stored data.  It does not
    claim cross-process MAC continuity, which the pre-existing ``ring_context`` binding to the
    ring's workspace anchor already prevented before this repair.
    """

    stored_domain_text = canonical_identity_text(
        AuthorityDomainId.from_authority_claim(AuthorityClaimProvenance.establish()),
        AuthorityDomainId,
    )
    authority = _claim_ring(stored_domain_text)
    assert authority.authority_domain == stored_domain_text

    verifier = authority.verification_capability(
        family=KeyFamily.CLAIM_ENROLLMENT, generation=1, key_id="claim-1"
    )
    _enrollment_operation, workspace = _enrollment_root(
        _issue_authenticated_identity_decode_domain(authority, stored_domain_text)
    )
    unsigned, mac, full_record = _handcrafted_claim(
        authority,
        authority_domain_text=stored_domain_text,
        workspace_text=canonical_identity_text(workspace, WorkspaceId),
    )
    del unsigned, mac

    decoded = EnrollmentClaimV3.from_json(full_record, verification_key=verifier)
    assert canonical_identity_text(decoded.authority_domain_id, AuthorityDomainId) == (
        stored_domain_text
    )
    assert decoded.workspace_id == workspace


def _issue_authenticated_identity_decode_domain(
    authority: DormantKeyAuthority,
    stored_domain_text: str,
) -> AuthorityDomainId:
    """Recover the owned AuthorityDomainId for a text-anchored ring, from an authenticated record."""

    verifier = authority.verification_capability(
        family=KeyFamily.CLAIM_ENROLLMENT, generation=1, key_id="claim-1"
    )
    unsigned, mac, _full = _handcrafted_claim(
        authority,
        authority_domain_text=stored_domain_text,
        workspace_text=f"workspace_{'0' * 64}",
    )
    proof = _issue_authenticated_identity_decode(
        verification_key=verifier,
        source_record=unsigned,
        supplied_mac=mac,
        record_type="EnrollmentClaimV3",
        source_field="authority_domain_id",
        identity_type=AuthorityDomainId,
    )
    return AuthorityDomainId._from_trusted_storage(proof)


def test_lanec_anchor_text_must_be_canonical_authority_domain_text() -> None:
    for bad_anchor in ("", "workspace_" + "a" * 64, "authority-domain_zz", 7, object()):
        with pytest.raises(TypeError, match="authority-domain anchor"):
            _claim_ring(bad_anchor)


# ---------------------------------------------------------------------------
# D6.6 Ring semantics stay distinct; nothing is redefined around workspace equality.
# ---------------------------------------------------------------------------


def test_lanec_two_rings_in_one_domain_stay_separate_logical_rings() -> None:
    """One domain may hold many rings; the domain anchor is not a ring identity.

    A control, not a kill: the separation here is the pre-existing ``ring_context`` binding.
    It is asserted so that adding a domain anchor cannot quietly collapse ring identity into
    domain -- or into workspace -- equality.
    """

    domain = AuthorityDomainId.from_authority_claim(AuthorityClaimProvenance.establish())
    first = _claim_ring(domain, key_id="claim-a")
    second = _claim_ring(domain, key_id="claim-b")

    assert first.authority_domain == second.authority_domain
    assert first.workspace_id != second.workspace_id

    claim = _issue_claim(first, domain, key_id="claim-a")
    first_verifier = first.verification_capability(
        family=KeyFamily.CLAIM_ENROLLMENT, generation=1, key_id="claim-a"
    )
    second_verifier = second.verification_capability(
        family=KeyFamily.CLAIM_ENROLLMENT, generation=1, key_id="claim-b"
    )
    assert claim.verify_mac(first_verifier) is True
    # Same domain, same key material, different logical ring: still not this ring's record.
    assert claim.verify_mac(second_verifier) is False


# ---------------------------------------------------------------------------
# D6.7 The cursor decode path carries the same domain authority.
# ---------------------------------------------------------------------------


def test_lanec_cursor_decode_stamps_the_authenticating_rings_domain() -> None:
    """A cursor payload names no domain, so the decoded identity records who vouched for it."""

    domain = AuthorityDomainId.from_authority_claim(AuthorityClaimProvenance.establish())
    other_domain = AuthorityDomainId.from_authority_claim(AuthorityClaimProvenance.establish())
    authority = _claim_ring(domain, key_id="claim-cursor")
    _enrollment_operation, workspace = _enrollment_root(domain)

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
        key=authority.signing_capability(
            family=KeyFamily.CURSOR, generation=1, key_id="cursor-claim-cursor"
        )
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
            family=KeyFamily.CURSOR, generation=1, key_id="cursor-claim-cursor"
        )
    ).decode(token, expectation=expectation, now_unix_ms=150)
    assert decoded == payload

    # The cursor-decoded workspace is attributable to the ring that authenticated it, and
    # only to that ring's domain.
    require_identity_context(
        decoded.workspace_id,
        WorkspaceId,
        authority_domain_id=domain,
    )
    with pytest.raises(ValueError, match="provenance does not match authority_domain_id"):
        require_identity_context(
            decoded.workspace_id,
            WorkspaceId,
            authority_domain_id=other_domain,
        )


# ---------------------------------------------------------------------------
# Key-state analogue: KeyRingMetadata structural invariants.
# ---------------------------------------------------------------------------


def _ring_with_two_active(state_alias: KeyState) -> KeyRingMetadata:
    return KeyRingMetadata(
        (
            _key_metadata(KeyFamily.CLAIM_ENROLLMENT, "claim-x", 1, state_alias),
            _key_metadata(KeyFamily.CLAIM_ENROLLMENT, "claim-y", 2, state_alias),
        )
    )


def test_lanec_double_active_generation_is_refused_without_tamper() -> None:
    with pytest.raises(ContractValidationError, match="at most one ACTIVE"):
        _ring_with_two_active(KeyState.ACTIVE)


def test_lanec_active_alias_replacement_cannot_admit_two_active_generations() -> None:
    """The structural invariant must not move when ``KeyState.ACTIVE`` is redirected.

    The genuine members are captured before the tamper, so the ring built inside really does
    hold two ACTIVE generations while the caller-visible ``KeyState.ACTIVE`` name points at
    ``RETIRED``.  A live re-read would classify both as non-ACTIVE and accept the ring.
    """

    genuine_active = KeyState.ACTIVE
    genuine_retired = KeyState.RETIRED
    with class_attribute(KeyState, "ACTIVE", genuine_retired):
        assert KeyState.ACTIVE is genuine_retired
        with pytest.raises(ContractValidationError, match="at most one ACTIVE"):
            _ring_with_two_active(genuine_active)
        # ...and the member the alias now names must not become ACTIVE for ring structure.
        KeyRingMetadata(
            (
                _key_metadata(KeyFamily.CLAIM_ENROLLMENT, "claim-x", 1, genuine_retired),
                _key_metadata(KeyFamily.CLAIM_ENROLLMENT, "claim-y", 2, genuine_retired),
            )
        )


def test_lanec_active_alias_replacement_does_not_reject_a_well_formed_ring() -> None:
    """Deriving from the sealed spec, not denying everything on tamper."""

    genuine_active = KeyState.ACTIVE
    with class_attribute(KeyState, "ACTIVE", KeyState.LOST):
        ring = KeyRingMetadata(
            (_key_metadata(KeyFamily.CLAIM_ENROLLMENT, "claim-ok", 1, genuine_active),)
        )
        assert ring.find(KeyFamily.CLAIM_ENROLLMENT, 1) is not None


def test_lanec_deferred_family_alias_replacement_cannot_admit_an_active_journal_key() -> None:
    deferred = (_key_metadata(KeyFamily.JOURNAL_EFFECT_RECEIPT, "journal-1", 1, KeyState.ACTIVE),)
    with pytest.raises(ContractValidationError, match="explicitly deferred"):
        KeyRingMetadata(deferred)
    with class_attribute(KeyFamily, "JOURNAL_EFFECT_RECEIPT", KeyFamily.CURSOR):
        with pytest.raises(ContractValidationError, match="explicitly deferred"):
            KeyRingMetadata(deferred)


def test_lanec_deferred_status_constant_rebinding_cannot_open_the_deferred_family() -> None:
    """The closed boundary's deferred gate reads sealed state, not a module attribute.

    The journal generation is VERIFY_ONLY on purpose.  That state is structurally legal for
    this family and is inside the permitted verification set, so the deferred gate is the only
    thing standing between the caller and a genuine verification lease -- remove it and the
    lease is issued rather than merely denied with a different message.
    """

    assert key_family_phase0_status(KeyFamily.JOURNAL_EFFECT_RECEIPT) is (
        KeyFamilyPhase0Status.DEFERRED_PHASE0
    )
    ring = KeyRingMetadata(
        (
            _key_metadata(KeyFamily.CLAIM_ENROLLMENT, "claim-1", 1, KeyState.ACTIVE),
            _key_metadata(KeyFamily.JOURNAL_EFFECT_RECEIPT, "journal-1", 1, KeyState.VERIFY_ONLY),
        )
    )
    authority = DormantKeyAuthority(
        ring,
        {
            KeyReference(KeyFamily.CLAIM_ENROLLMENT, "claim-1", 1): KEY,
            KeyReference(KeyFamily.JOURNAL_EFFECT_RECEIPT, "journal-1", 1): KEY,
        },
        workspace_id=ring_workspace_id(),
    )
    with module_attribute(
        contracts_module,
        "JOURNAL_EFFECT_RECEIPT_PHASE0_STATUS",
        KeyFamilyPhase0Status.DORMANT_TYPED_RECORD,
    ):
        with pytest.raises(ContractValidationError, match="deferred in Phase 0"):
            authority.verification_capability(
                family=KeyFamily.JOURNAL_EFFECT_RECEIPT, generation=1, key_id="journal-1"
            )
    with module_attribute(
        contracts_module,
        "key_family_phase0_status",
        lambda _family: KeyFamilyPhase0Status.DORMANT_TYPED_RECORD,
    ):
        with pytest.raises(ContractValidationError, match="deferred in Phase 0"):
            authority.verification_capability(
                family=KeyFamily.JOURNAL_EFFECT_RECEIPT, generation=1, key_id="journal-1"
            )
