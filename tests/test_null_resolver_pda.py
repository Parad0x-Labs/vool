"""Regression tests for the .null resolver PDA + scan fixes.

The hand-rolled find_program_address used to derive a WRONG address for real names
(it pointed at an empty account while the canonical PDA held the record), so live
resolution silently returned MISS for names that exist on-chain. These pins keep
VOOL's derivation byte-for-byte with the reference (solders / web3.js) and keep the
getProgramAccounts filter free of the over-strict dataSize that dropped v2 accounts.
All offline - no RPC.
"""

from __future__ import annotations

import hashlib

import pytest

from core.null_resolver import (
    domain_filters,
    find_program_address,
    pad_name64,
)
from core.x402.client import NULL_REGISTRAR_MAINNET

solders = pytest.importorskip("solders")


def _reference_pda(name: str) -> str:
    from solders.pubkey import Pubkey

    seed = hashlib.sha256(pad_name64(name)).digest()
    pda, _bump = Pubkey.find_program_address([b"null-domain", seed], Pubkey.from_string(NULL_REGISTRAR_MAINNET))
    return str(pda)


@pytest.mark.parametrize("name", ["parad0x", "web0", "vool", "a", "some-longer-name"])
def test_find_program_address_matches_reference(name: str) -> None:
    seed = hashlib.sha256(pad_name64(name)).digest()
    found = find_program_address([b"null-domain", seed], NULL_REGISTRAR_MAINNET)
    assert found is not None
    assert found[0] == _reference_pda(name)


def test_parad0x_derives_to_the_known_canonical_pda() -> None:
    # The canonical on-chain PDA for parad0x (what the deployed program actually uses).
    seed = hashlib.sha256(pad_name64("parad0x")).digest()
    found = find_program_address([b"null-domain", seed], NULL_REGISTRAR_MAINNET)
    assert found is not None
    assert found[0] == "HTPbRoV9ERectjC8soyukEsr2JNUG595FLE4a6SPnmS3"


def test_domain_filters_omit_datasize_and_keep_disc_and_name() -> None:
    filters = domain_filters("web0")
    assert filters is not None
    # No dataSize filter (it would drop v2/378-byte accounts).
    assert all("dataSize" not in f for f in filters)
    offsets = [f["memcmp"]["offset"] for f in filters if "memcmp" in f]
    assert 0 in offsets  # discriminator
    assert 1 in offsets  # 64-byte padded name @ offset 1


def test_domain_filters_reject_overlong_name() -> None:
    assert domain_filters("x" * 65) is None
