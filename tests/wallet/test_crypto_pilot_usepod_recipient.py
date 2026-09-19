"""Correction 2 / R2: chain-correct recipient identity in the operation record.

SIMULATED CHAINS. One canonical recipient per family — EVM: the lowercase hex address; Solana: the exact Base58 string
(case is identity) — is used by the requirement digest, the persisted record, the replay comparison and the claim
binding. The same correlation with a valid Solana address that differs only by case is another request and is refused
as changed content; an EVM address in checksum case replays the operation minted in lowercase; legacy records (digest
version 1) are certified on first replay by their stored recipient and never replayed as a new payment.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid

import pytest

from core.wallet.errors import WalletFault
from tests.wallet.test_crypto_pilot_usepod_operation import (
    BASE_SEPOLIA,
    SOLANA_DEVNET,
    _count,
    _pay,
    _pilot,
    _requirement,
    _sol_key,
)
from tests.wallet.test_crypto_pilot_usepod_operation import home as operation_home
from tests.wallet.test_crypto_pilot_usepod_operation import nodes as operation_nodes

pytestmark = [pytest.mark.safety]

home = operation_home
nodes = operation_nodes


def _case_variant(address: str) -> str:
    """Another VALID 32-byte Base58 key that differs from ``address`` only by the case of one character."""
    from core.vool_wallet import b58decode

    for index, char in enumerate(address):
        alt = char.swapcase()
        if alt == char or alt in "0OIl":
            continue
        candidate = address[:index] + alt + address[index + 1:]
        try:
            if len(b58decode(candidate)) == 32:
                return candidate
        except Exception:
            continue
    raise AssertionError("no case variant decodes to 32 bytes")


def _select_default(wallet_id: str) -> None:
    from core.wallet.store import connection

    with connection() as conn:
        conn.execute("UPDATE wallet_profiles SET is_default = 0")
        conn.execute("UPDATE wallet_profiles SET is_default = 1 WHERE wallet_id = ?", (wallet_id,))


def test_a_valid_solana_address_differing_only_by_case_is_another_request(nodes):
    """The reviewer's original probe: two valid keys, one correlation id — the second is refused as changed content."""
    from core.wallet import usepod

    _pilot(SOLANA_DEVNET)
    address = _sol_key()
    different = _case_variant(address)
    assert different != address and different.lower() == address.lower()
    body = _requirement(network=SOLANA_DEVNET, asset="SOL", pay_to=address, amount="0.0007", expires_at=time.time() + 600)
    first = usepod.validate_topup(usepod.parse_requirement(body))
    assert first["pay_to"] == address
    with pytest.raises(WalletFault) as changed:
        usepod.validate_topup(usepod.parse_requirement({**body, "pay_to": different}))
    assert (changed.value.code, changed.value.context.get("reason")) == ("wallet_duplicate_payment", "same_operation_different_content")
    assert _count() == 1
    same = usepod.validate_topup(usepod.parse_requirement(body))
    assert (same["proposal_id"], same["duplicate"], same["pay_to"]) == (first["proposal_id"], True, address)


def test_another_row_and_amount_a_case_variant_refuses_after_payment_and_the_exact_key_replays(nodes):
    """Different data: 0.0011 SOL, paid; then the case variant is refused (never a second payment) while the exact key
    replays the paid operation after a payer/default change and an engine rebuild."""
    from core.wallet import chains, lifecycle, usepod

    sol = nodes["sol"]
    payer = _pilot(SOLANA_DEVNET, "payer")
    other = _pilot(SOLANA_DEVNET, "other")
    address = _sol_key()
    body = _requirement(network=SOLANA_DEVNET, asset="SOL", pay_to=address, amount="0.0011", expires_at=time.time() + 600, resource="https://usepod.example/v1/credits/sol", payer_wallet=payer["wallet_id"])
    first = usepod.validate_topup(usepod.parse_requirement(body))
    assert _pay(first["proposal_id"])["state"] == "confirmed" and sol.send_count() == 1
    with pytest.raises(WalletFault) as changed:
        usepod.validate_topup(usepod.parse_requirement({**body, "pay_to": _case_variant(address), "payer_wallet": ""}))
    assert changed.value.context.get("reason") == "same_operation_different_content"
    _select_default(other["wallet_id"])
    chains.invalidate_chain_identity()
    lifecycle.default_lifecycle()
    replay = usepod.validate_topup(usepod.parse_requirement({**body, "payer_wallet": ""}))
    assert (replay["proposal_id"], replay["duplicate"], replay["pay_to"], replay["operation"]["state"]) == (first["proposal_id"], True, address, "paid")
    assert _count() == 1 and sol.send_count() == 1


def test_an_evm_recipient_in_checksum_case_is_the_same_recipient(nodes):
    """EVM identity is case-insensitive (EIP-55 is a checksum, not an identity): the checksum-case replay is the
    original operation, and the persisted recipient is the canonical lowercase form."""
    from core.wallet import usepod

    base = nodes["base"]
    wallet = _pilot(BASE_SEPOLIA)
    base.fund(wallet["address"], 10**18)
    lower = "0x" + "ab12" * 10
    checksum = "0x" + "".join(c.upper() if i % 3 == 0 else c for i, c in enumerate("ab12" * 10))
    assert checksum != lower and checksum.lower() == lower
    body = _requirement(pay_to=lower, expires_at=time.time() + 600)
    first = usepod.validate_topup(usepod.parse_requirement(body))
    replay = usepod.validate_topup(usepod.parse_requirement({**body, "pay_to": checksum}))
    assert (replay["proposal_id"], replay["duplicate"]) == (first["proposal_id"], True)
    assert usepod.status_for(body["correlation_id"])["operation"]["pay_to"] == lower
    assert _pay(first["proposal_id"])["to_address"].lower() == lower and _count() == 1


def test_a_changed_evm_recipient_is_refused_not_normalised_away(nodes):
    from core.wallet import usepod

    base = nodes["base"]
    wallet = _pilot(BASE_SEPOLIA)
    base.fund(wallet["address"], 10**18)
    body = _requirement(pay_to="0x" + "1" * 40, expires_at=time.time() + 600)
    usepod.validate_topup(usepod.parse_requirement(body))
    with pytest.raises(WalletFault) as changed:
        usepod.validate_topup(usepod.parse_requirement({**body, "pay_to": "0x" + "2" * 40}))
    assert changed.value.context.get("reason") == "same_operation_different_content" and _count() == 1


def test_a_legacy_record_is_certified_by_its_stored_recipient_and_never_replayed_as_a_new_payment(nodes):
    """A record written before this correction carries the version-1 digest (the recipient lowercased). Its exact
    recipient certifies it on first replay (the record is upgraded, the original proposal returned); a case variant is
    refused as changed content; a record whose stored recipient cannot be canonicalised is refused with a recovery
    reason and mints nothing."""
    from core.wallet import usepod
    from core.wallet.store import connection

    sol = nodes["sol"]
    _pilot(SOLANA_DEVNET)
    address = _sol_key()
    body = _requirement(network=SOLANA_DEVNET, asset="SOL", pay_to=address, amount="0.0005", expires_at=time.time() + 600)
    first = usepod.validate_topup(usepod.parse_requirement(body))
    requirement = usepod.parse_requirement(body)
    legacy_digest = hashlib.sha256(json.dumps({
        "provider": requirement.provider, "correlation_id": requirement.correlation_id, "network": SOLANA_DEVNET, "asset": "SOL",
        "pay_to": address.lower(), "amount_minor": 500_000, "expires_at": repr(float(requirement.expires_at)), "resource": requirement.resource,
    }, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    key = usepod.operation_key(requirement.provider, requirement.correlation_id)
    with connection() as conn:
        conn.execute("UPDATE wallet_usepod_operations SET requirement_digest = ?, digest_version = 1 WHERE operation_key = ?", (legacy_digest, key))
    with pytest.raises(WalletFault) as changed:
        usepod.validate_topup(usepod.parse_requirement({**body, "pay_to": _case_variant(address)}))
    assert changed.value.context.get("reason") == "same_operation_different_content"
    certified = usepod.validate_topup(usepod.parse_requirement(body))
    assert (certified["proposal_id"], certified["duplicate"]) == (first["proposal_id"], True)
    with connection() as conn:
        row = conn.execute("SELECT digest_version, requirement_digest FROM wallet_usepod_operations WHERE operation_key = ?", (key,)).fetchone()
    assert row[0] == 2 and row[1] != legacy_digest
    assert _count() == 1 and sol.send_count() == 0
    # an uncertifiable legacy record: its stored recipient is not a key of this row's family
    broken = _requirement(network=SOLANA_DEVNET, asset="SOL", pay_to=_sol_key(), amount="0.0006", expires_at=time.time() + 600, correlation_id=f"legacy-{uuid.uuid4().hex[:8]}")
    second = usepod.validate_topup(usepod.parse_requirement(broken))
    with connection() as conn:
        conn.execute("UPDATE wallet_usepod_operations SET pay_to = 'not-a-key', digest_version = 1 WHERE proposal_id = ?", (second["proposal_id"],))
    with pytest.raises(WalletFault) as uncertified:
        usepod.validate_topup(usepod.parse_requirement(broken))
    assert (uncertified.value.code, uncertified.value.context.get("reason")) == ("wallet_duplicate_payment", "legacy_record_uncertified")
    assert _count() == 2


def test_the_claim_binding_compares_a_solana_recipient_by_exact_case(nodes):
    from core.wallet import quotes, usepod
    from core.wallet.store import connection

    _pilot(SOLANA_DEVNET)
    address = _sol_key()
    body = _requirement(network=SOLANA_DEVNET, asset="SOL", pay_to=address, amount="0.0004", expires_at=time.time() + 600)
    first = usepod.validate_topup(usepod.parse_requirement(body))
    quote = quotes.mint_quote(first["proposal_id"])
    with connection() as conn:
        conn.execute("UPDATE wallet_usepod_operations SET pay_to = ? WHERE proposal_id = ?", (_case_variant(address), first["proposal_id"]))
    from core.wallet import approval, lifecycle

    with pytest.raises(WalletFault) as refused:
        lifecycle.default_lifecycle().approve_pilot_transfer(first["proposal_id"], quote_id=quote["quote_id"], quote_digest=quote["digest"], approver=approval.PinApprover("482913"))
    assert (refused.value.code, refused.value.context.get("reason")) == ("wallet_quote_mismatch", "provider_requirement_changed")
    assert nodes["sol"].send_count() == 0
