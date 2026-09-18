"""Repair #2A, Lane B: key-lifecycle permission decisions must not read live KeyState attributes.

D5: ``usable_generation``, ``issue_signing``, ``issue_verification``, ``require_capability_use``,
and ``install_metadata`` inside the closed key-authority boundary used to rebuild their permitted
states by reading ``KeyState.ACTIVE`` / ``KeyState.VERIFY_ONLY`` live, on every call.  Because
``KeyState`` is defined with a plain ``EnumMeta`` (only its *value* is sealed via
``seal_canonical_enum``), a caller who bypasses the metaclass's member-reassignment guard with
``type.__setattr__`` can redirect the ``ACTIVE`` / ``VERIFY_ONLY`` class attribute to name a
different member object.  Every one of those call sites would then treat a revoked/retired/lost
key as current.  The fix captures the permitted-state constants and the lifecycle transition table
once, as closure-private locals, at the moment the closed key-authority boundary is built --
before any caller-reachable code has run -- so later class-attribute replacement cannot reach the
authority's use-time decisions.
"""

from __future__ import annotations

import contextlib

import pytest

from core.workspace_authority_v3 import contracts as contracts_module
from core.workspace_authority_v3.base import ContractValidationError
from core.workspace_authority_v3.contracts import (
    DormantKeyAuthority,
    KeyFamily,
    KeyGenerationMetadata,
    KeyReference,
    KeyRingMetadata,
    KeyState,
)
from tests.workspace_authority_v3_fixtures import ring_workspace_id

KEY = b"k" * 32


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


def _key_metadata(family: KeyFamily, key_id: str, generation: int, state: KeyState) -> KeyGenerationMetadata:
    return KeyGenerationMetadata(family, key_id, generation, state, "HMAC-SHA256", 1)


def _authority_with_state(state: KeyState, *, key_id: str = "claim-1") -> DormantKeyAuthority:
    metadata = KeyRingMetadata(
        (_key_metadata(KeyFamily.CLAIM_ENROLLMENT, key_id, 1, state),)
    )
    return DormantKeyAuthority(
        metadata,
        {KeyReference(KeyFamily.CLAIM_ENROLLMENT, key_id, 1): KEY},
        workspace_id=ring_workspace_id(),
    )


def _sign_and_verify(authority: DormantKeyAuthority, key_id: str = "claim-1") -> None:
    signer = authority.signing_capability(family=KeyFamily.CLAIM_ENROLLMENT, generation=1, key_id=key_id)
    verifier = authority.verification_capability(
        family=KeyFamily.CLAIM_ENROLLMENT, generation=1, key_id=key_id
    )
    mac = signer.sign(expected_family=KeyFamily.CLAIM_ENROLLMENT, domain="VOOL_LANEB_V3", message=b"payload")
    assert verifier.verify(
        expected_family=KeyFamily.CLAIM_ENROLLMENT,
        domain="VOOL_LANEB_V3",
        message=b"payload",
        supplied_mac=mac,
    )


# ---------------------------------------------------------------------------
# 1 & 2. Genuine ACTIVE / VERIFY_ONLY behavior.
# ---------------------------------------------------------------------------


def test_laneb_genuine_active_key_signs_and_verifies() -> None:
    authority = _authority_with_state(KeyState.ACTIVE)
    _sign_and_verify(authority)


def test_laneb_genuine_verify_only_key_verifies_but_cannot_sign_or_be_reissued_for_signing() -> None:
    authority = _authority_with_state(KeyState.VERIFY_ONLY)
    with pytest.raises(ContractValidationError, match="ACTIVE key authority"):
        authority.signing_capability(family=KeyFamily.CLAIM_ENROLLMENT, generation=1, key_id="claim-1")
    verifier = authority.verification_capability(
        family=KeyFamily.CLAIM_ENROLLMENT, generation=1, key_id="claim-1"
    )
    assert not verifier.verify(
        expected_family=KeyFamily.CLAIM_ENROLLMENT,
        domain="VOOL_LANEB_V3",
        message=b"payload",
        supplied_mac="0" * 64,
    )


# ---------------------------------------------------------------------------
# 3. Revoked / retired / lost behavior: neither issuance nor use is permitted.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "terminal_state",
    (KeyState.RETIRED, KeyState.REVOKED_COMPROMISED, KeyState.LOST),
)
def test_laneb_nonpermitted_states_deny_both_signing_and_verification_capability_issuance(
    terminal_state: KeyState,
) -> None:
    authority = _authority_with_state(terminal_state)
    with pytest.raises(ContractValidationError, match="ACTIVE key authority"):
        authority.signing_capability(family=KeyFamily.CLAIM_ENROLLMENT, generation=1, key_id="claim-1")
    with pytest.raises(ContractValidationError, match="cannot issue a verification lease"):
        authority.verification_capability(family=KeyFamily.CLAIM_ENROLLMENT, generation=1, key_id="claim-1")


# ---------------------------------------------------------------------------
# 4. Caller-visible KeyState class-attribute replacement must not change any decision.
# ---------------------------------------------------------------------------


def test_laneb_active_attribute_replacement_cannot_revive_signing_or_verification_for_a_revoked_key() -> None:
    authority = _authority_with_state(KeyState.REVOKED_COMPROMISED)

    # Redirect the caller-visible ``KeyState.ACTIVE`` name to alias the revoked member itself --
    # the exact shape of D5: a live re-read of ``KeyState.ACTIVE`` would then treat the revoked
    # generation as ACTIVE.
    with class_attribute(KeyState, "ACTIVE", KeyState.REVOKED_COMPROMISED):
        with pytest.raises(ContractValidationError, match="ACTIVE key authority"):
            authority.signing_capability(family=KeyFamily.CLAIM_ENROLLMENT, generation=1, key_id="claim-1")
        with class_attribute(KeyState, "VERIFY_ONLY", KeyState.REVOKED_COMPROMISED):
            with pytest.raises(ContractValidationError, match="cannot issue a verification lease"):
                authority.verification_capability(
                    family=KeyFamily.CLAIM_ENROLLMENT, generation=1, key_id="claim-1"
                )

    # Restoring the attributes must not itself grant anything either.
    with pytest.raises(ContractValidationError, match="ACTIVE key authority"):
        authority.signing_capability(family=KeyFamily.CLAIM_ENROLLMENT, generation=1, key_id="claim-1")


def test_laneb_active_attribute_replacement_cannot_revive_a_cached_lease_after_revocation() -> None:
    authority = _authority_with_state(KeyState.ACTIVE, key_id="claim-cached")
    signer = authority.signing_capability(
        family=KeyFamily.CLAIM_ENROLLMENT, generation=1, key_id="claim-cached"
    )
    verifier = authority.verification_capability(
        family=KeyFamily.CLAIM_ENROLLMENT, generation=1, key_id="claim-cached"
    )
    authority.install_metadata(
        KeyRingMetadata(
            (_key_metadata(KeyFamily.CLAIM_ENROLLMENT, "claim-cached", 1, KeyState.REVOKED_COMPROMISED),)
        )
    )

    with class_attribute(KeyState, "ACTIVE", KeyState.REVOKED_COMPROMISED):
        with pytest.raises(ContractValidationError, match="not current"):
            signer.sign(
                expected_family=KeyFamily.CLAIM_ENROLLMENT,
                domain="VOOL_LANEB_V3",
                message=b"payload",
            )
        with class_attribute(KeyState, "VERIFY_ONLY", KeyState.REVOKED_COMPROMISED):
            with pytest.raises(ContractValidationError, match="not current"):
                verifier.verify(
                    expected_family=KeyFamily.CLAIM_ENROLLMENT,
                    domain="VOOL_LANEB_V3",
                    message=b"payload",
                    supplied_mac="0" * 64,
                )


def test_laneb_active_attribute_replacement_does_not_deny_a_genuinely_active_key() -> None:
    """The fix must derive truth from the sealed spec, not simply deny everything on tamper."""

    authority = _authority_with_state(KeyState.ACTIVE, key_id="claim-genuine")
    with class_attribute(KeyState, "ACTIVE", KeyState.LOST):
        # A real ACTIVE generation must still sign and verify even though the live
        # ``KeyState.ACTIVE`` name now points at an unrelated member.
        signer = authority.signing_capability(
            family=KeyFamily.CLAIM_ENROLLMENT, generation=1, key_id="claim-genuine"
        )
        verifier = authority.verification_capability(
            family=KeyFamily.CLAIM_ENROLLMENT, generation=1, key_id="claim-genuine"
        )
        mac = signer.sign(
            expected_family=KeyFamily.CLAIM_ENROLLMENT, domain="VOOL_LANEB_V3", message=b"payload"
        )
        assert verifier.verify(
            expected_family=KeyFamily.CLAIM_ENROLLMENT,
            domain="VOOL_LANEB_V3",
            message=b"payload",
            supplied_mac=mac,
        )


def test_laneb_transition_table_replacement_cannot_reactivate_a_revoked_generation() -> None:
    """Direct analogue: the lifecycle transition table must not be a live module attribute."""

    assert not hasattr(contracts_module, "_KEY_STATE_TRANSITIONS")
    authority = _authority_with_state(KeyState.REVOKED_COMPROMISED, key_id="claim-transition")
    with pytest.raises(ContractValidationError, match="illegal key lifecycle transition"):
        authority.install_metadata(
            KeyRingMetadata(
                (
                    _key_metadata(
                        KeyFamily.CLAIM_ENROLLMENT, "claim-transition", 1, KeyState.ACTIVE
                    ),
                )
            )
        )


# ---------------------------------------------------------------------------
# 5. Restoring class attributes afterward does not affect persisted lifecycle truth.
# ---------------------------------------------------------------------------


def test_laneb_restoring_class_attributes_leaves_normal_behavior_unchanged() -> None:
    authority = _authority_with_state(KeyState.ACTIVE, key_id="claim-restore")
    with class_attribute(KeyState, "ACTIVE", KeyState.LOST):
        with class_attribute(KeyState, "VERIFY_ONLY", KeyState.LOST):
            pass
    assert KeyState.ACTIVE.name == "ACTIVE"
    assert KeyState.VERIFY_ONLY.name == "VERIFY_ONLY"
    _sign_and_verify(authority, key_id="claim-restore")

    authority.install_metadata(
        KeyRingMetadata(
            (_key_metadata(KeyFamily.CLAIM_ENROLLMENT, "claim-restore", 1, KeyState.REVOKED_COMPROMISED),)
        )
    )
    with pytest.raises(ContractValidationError, match="ACTIVE key authority"):
        authority.signing_capability(family=KeyFamily.CLAIM_ENROLLMENT, generation=1, key_id="claim-restore")
