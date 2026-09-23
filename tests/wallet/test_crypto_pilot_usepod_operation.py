"""Correction 1 / F2: one provider operation, whatever account is selected now.

SIMULATED CHAINS. The operation record (provider + correlation id) is reserved before any account is resolved. A replay
returns the original result after the operator's default account changed; the same id with other content, or a
different named payer, is refused typed; two contenders for one new operation mint exactly one proposal under a
controlled schedule; a mint that dies leaves no liability and the next request takes over after the lease; a restart
keeps the identity; a different operation id is independent; an owner-rejected operation replays as rejected and
pays nothing; status is deterministic by provider and correlation id.
"""
from __future__ import annotations

import json
import threading
import time
import uuid

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.wallet.errors import WalletFault
from tests.wallet._rig import DEVNET_GENESIS, ScriptedRpc
from tests.wallet._rig_evm_native import ScriptedEvmNativeChain
from tests.wallet.test_crypto_pilot_usepod import _count, _pilot, _requirement
from tests.wallet.test_crypto_pilot_usepod import home as usepod_home

pytestmark = [pytest.mark.safety]

BASE_SEPOLIA = "eip155:84532"
SOLANA_DEVNET = "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1"
PIN = "482913"

home = usepod_home  # the delivered UsePod corpus's environment fixture, under its own name here


@pytest.fixture
def nodes(home):
    with ScriptedEvmNativeChain(chain_id=84532, fee_model="op_stack", l1_fee=40_000_000_000_000) as base, ScriptedRpc(genesis_hash=DEVNET_GENESIS) as sol:
        sol.real_signature = True
        sol.status_keyed = True
        home.setenv("VOOL_WALLET_RPC_URLS", json.dumps({BASE_SEPOLIA: base.url, SOLANA_DEVNET: sol.url}))
        yield {"base": base, "sol": sol}


def _sol_key() -> str:
    from core.vool_wallet import b58encode

    return b58encode(Ed25519PrivateKey.generate().public_key().public_bytes_raw())


def _pay(proposal_id: str) -> dict:
    from core.wallet import approval, lifecycle, quotes

    quote = quotes.mint_quote(proposal_id)
    return lifecycle.default_lifecycle().approve_pilot_transfer(proposal_id, quote_id=quote["quote_id"], quote_digest=quote["digest"], approver=approval.PinApprover(PIN))["transfer"]


def _select_default(wallet_id: str) -> None:
    """The operator's selected account, persisted the way the product persists it (the review's own technique)."""
    from core.wallet.store import connection

    with connection() as conn:
        conn.execute("UPDATE wallet_profiles SET is_default = 0")
        conn.execute("UPDATE wallet_profiles SET is_default = 1 WHERE wallet_id = ?", (wallet_id,))


def test_a_replayed_requirement_after_the_default_account_changes_returns_the_original_operation(nodes):
    """The reviewer's original workflow: paid from the first account, a second account becomes the default, the same
    requirement again. The original operation answers; one signed transaction ever exists for the correlation."""
    from core.wallet import usepod

    base = nodes["base"]
    first_wallet = _pilot(BASE_SEPOLIA, "first")
    base.fund(first_wallet["address"], 10**18)
    body = _requirement(expires_at=time.time() + 600)
    first = usepod.validate_topup(usepod.parse_requirement(body))
    paid = _pay(first["proposal_id"])
    assert paid["state"] == "confirmed" and len(base.sent) == 1
    second_wallet = _pilot(BASE_SEPOLIA, "second")
    base.fund(second_wallet["address"], 10**18)
    _select_default(second_wallet["wallet_id"])
    replay = usepod.validate_topup(usepod.parse_requirement(body))
    assert (replay["proposal_id"], replay["duplicate"], replay["wallet_id"]) == (first["proposal_id"], True, first_wallet["wallet_id"])
    assert replay["operation"]["state"] == "paid" and _count() == 1 and len(base.sent) == 1
    record = usepod.status_for(body["correlation_id"])
    assert (record["proposal_id"], record["operation"]["wallet_id"], record["transfer"]["tx_id"]) == (first["proposal_id"], first_wallet["wallet_id"], paid["tx_id"])


def test_the_same_correlation_with_other_content_or_another_named_payer_is_refused_typed(nodes):
    """Different row and data: Solana Devnet, 0.0003 SOL. Other content under the id, or a different named payer,
    is a typed refusal; the plain replay is the original; still one send, one proposal."""
    from core.wallet import usepod

    sol = nodes["sol"]
    payer = _pilot(SOLANA_DEVNET, "payer")
    other = _pilot(SOLANA_DEVNET, "other")
    body = _requirement(network=SOLANA_DEVNET, asset="SOL", pay_to=_sol_key(), amount="0.0003", expires_at=time.time() + 600, resource="https://usepod.example/v1/credits/sol", payer_wallet=payer["wallet_id"])
    first = usepod.validate_topup(usepod.parse_requirement(body))
    assert first["wallet_id"] == payer["wallet_id"] and _pay(first["proposal_id"])["state"] == "confirmed" and sol.send_count() == 1
    with pytest.raises(WalletFault) as changed:
        usepod.validate_topup(usepod.parse_requirement({**body, "amount": "0.0004"}))
    assert (changed.value.code, changed.value.context.get("reason")) == ("wallet_duplicate_payment", "same_operation_different_content")
    with pytest.raises(WalletFault) as other_payer:
        usepod.validate_topup(usepod.parse_requirement({**body, "payer_wallet": other["wallet_id"]}))
    assert (other_payer.value.code, other_payer.value.context.get("reason")) == ("wallet_duplicate_payment", "operation_bound_to_another_payer")
    assert other_payer.value.context.get("payer_wallet") == payer["wallet_id"]
    replay = usepod.validate_topup(usepod.parse_requirement(body))
    assert (replay["proposal_id"], replay["duplicate"]) == (first["proposal_id"], True)
    assert _count() == 1 and sol.send_count() == 1


@pytest.mark.parametrize("response_payment", [False, True])
def test_payment_purpose_is_pinned_by_the_owning_entry_point(nodes, response_payment):
    from core.wallet import proposals, purpose, quotes, usepod

    wallet = _pilot(BASE_SEPOLIA)
    nodes["base"].fund(wallet["address"], 10**18)
    mint = usepod.validate_x402_payment if response_payment else usepod.validate_topup
    other = usepod.validate_topup if response_payment else usepod.validate_x402_payment
    requirement = usepod.parse_requirement(_requirement(payment_kind="forged", resource="https://usepod.example/request"))
    first = mint(requirement)
    expected = usepod.KIND_RESPONSE if response_payment else usepod.KIND_CREDIT
    assert usepod.operation_for_proposal(first["proposal_id"])["payment_kind"] == expected
    proposal = proposals.get_proposal(first["proposal_id"])
    view = purpose.purpose_for(proposal)
    assert view["kind"] == (purpose.KIND_SERVICE if response_payment else purpose.KIND_CREDIT)
    quote = quotes.mint_quote(proposal.proposal_id)
    assert quote["fields"]["provider_payment_kind"] == expected
    assert quote["fields"]["review"]["headline"] == ("Pay UsePod for this AI response" if response_payment else "Prepay provider credit")
    assert mint(requirement)["proposal_id"] == first["proposal_id"]
    with pytest.raises(WalletFault) as changed:
        other(requirement)
    assert changed.value.context["reason"] == "same_operation_different_payment_kind"
    assert _count() == 1 and nodes["base"].sent == []


def test_legacy_payment_purpose_stays_unknown_and_a_changed_kind_invalidates_the_quote(nodes):
    from core.wallet import proposals, purpose, quotes, usepod
    from core.wallet.store import connection

    wallet = _pilot(BASE_SEPOLIA)
    nodes["base"].fund(wallet["address"], 10**18)
    requirement = usepod.parse_requirement(_requirement())
    first = usepod.validate_topup(requirement)
    proposal = proposals.get_proposal(first["proposal_id"])
    quote = quotes.mint_quote(proposal.proposal_id)
    with connection() as conn:
        conn.execute("UPDATE wallet_usepod_operations SET payment_kind = 'unknown' WHERE proposal_id = ?", (proposal.proposal_id,))
    with pytest.raises(WalletFault) as changed:
        usepod.require_binding(proposal, moment=time.time(), quote_fields=quote["fields"])
    assert changed.value.context["reason"] == "provider_requirement_changed"
    assert purpose.purpose_for(proposal)["kind"] == purpose.KIND_UNKNOWN
    assert usepod.validate_topup(requirement)["proposal_id"] == proposal.proposal_id
    assert usepod.operation_for_proposal(proposal.proposal_id)["payment_kind"] == usepod.KIND_UNKNOWN
    assert _count() == 1 and nodes["base"].sent == []


def test_two_contenders_for_one_new_operation_mint_exactly_one_proposal_under_a_controlled_schedule(nodes, monkeypatch):
    """A holds the reservation and is paused inside the mint; B arrives: a typed refusal to retry, never a second
    mint; A completes; B's retry is the original. Events, not timing, order the schedule."""
    from core.wallet import transfers, usepod

    base = nodes["base"]
    wallet = _pilot(BASE_SEPOLIA)
    base.fund(wallet["address"], 10**18)
    body = _requirement(expires_at=time.time() + 600)
    entered, release = threading.Event(), threading.Event()
    original = transfers.resolve_request

    def paused_inside_the_mint(**kwargs):
        entered.set()
        assert release.wait(timeout=30), "the schedule never released contender A"
        return original(**kwargs)

    monkeypatch.setattr(transfers, "resolve_request", paused_inside_the_mint)
    outcome: dict = {}

    def contender_a() -> None:
        try:
            outcome["a"] = usepod.validate_topup(usepod.parse_requirement(body))
        except BaseException as exc:  # recorded, asserted below
            outcome["error"] = exc

    thread = threading.Thread(target=contender_a, name="contender-a")
    thread.start()
    assert entered.wait(timeout=30), "contender A never reached the mint"
    monkeypatch.setattr(transfers, "resolve_request", original)
    with pytest.raises(WalletFault) as busy:
        usepod.validate_topup(usepod.parse_requirement(body))
    assert (busy.value.code, busy.value.context.get("reason")) == ("wallet_duplicate_payment", "operation_in_progress")
    release.set()
    thread.join(timeout=60)
    assert "error" not in outcome, outcome.get("error")
    assert outcome["a"]["duplicate"] is False
    again = usepod.validate_topup(usepod.parse_requirement(body))
    assert (again["proposal_id"], again["duplicate"]) == (outcome["a"]["proposal_id"], True)
    assert _count() == 1 and len(base.sent) == 0


def test_a_mint_that_dies_leaves_no_liability_and_the_next_request_takes_over_after_the_lease(nodes, monkeypatch):
    from core.wallet import transfers, usepod
    from core.wallet.store import connection

    base = nodes["base"]
    wallet = _pilot(BASE_SEPOLIA)
    base.fund(wallet["address"], 10**18)
    body = _requirement(expires_at=time.time() + 600)
    original = transfers.resolve_request

    def dies(**kwargs):
        raise RuntimeError("the minter died before any proposal")

    monkeypatch.setattr(transfers, "resolve_request", dies)
    with pytest.raises(RuntimeError):
        usepod.validate_topup(usepod.parse_requirement(body))
    monkeypatch.setattr(transfers, "resolve_request", original)
    assert _count() == 0 and usepod.status_for(body["correlation_id"]) is None, "a failed mint leaves no record and no proposal"
    # a minter that died holding its lease: the record stays 'minting' until the lease is over, then the next request takes over
    key = usepod.operation_key(usepod.provider_of(body["resource"]), body["correlation_id"])
    requirement = usepod.parse_requirement(body)
    with connection() as conn:
        conn.execute(
            "INSERT INTO wallet_usepod_operations (operation_key, provider, correlation_id, authority, network, asset, pay_to, amount_minor, expires_at, resource, "
            "requirement_digest, proposal_id, wallet_id, state, mint_token, mint_lease_until, detail, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', '', 'minting', 'dead', ?, '', 'x', 'x')",
            (key, requirement.provider, requirement.correlation_id, usepod.AUTHORITY, BASE_SEPOLIA, "ETH", requirement.pay_to, 10**15, requirement.expires_at, requirement.resource,
             usepod.requirement_digest(requirement, 10**15, BASE_SEPOLIA), time.time() + 5),
        )
    with pytest.raises(WalletFault) as still_leased:
        usepod.validate_topup(requirement)
    assert still_leased.value.context.get("reason") == "operation_in_progress"
    with connection() as conn:
        conn.execute("UPDATE wallet_usepod_operations SET mint_lease_until = ? WHERE operation_key = ?", (time.time() - 1, key))
    taken = usepod.validate_topup(requirement)
    assert taken["duplicate"] is False and taken["state"] == "pending_approval" and _count() == 1
    assert usepod.status_for(body["correlation_id"])["proposal_id"] == taken["proposal_id"]


def test_a_restart_keeps_the_operation_identity_and_another_operation_id_is_independent(nodes):
    from core.wallet import chains, lifecycle, usepod

    base = nodes["base"]
    wallet = _pilot(BASE_SEPOLIA)
    base.fund(wallet["address"], 10**18)
    body = _requirement(expires_at=time.time() + 600)
    first = usepod.validate_topup(usepod.parse_requirement(body))
    assert _pay(first["proposal_id"])["state"] == "confirmed"
    chains.invalidate_chain_identity()
    lifecycle.default_lifecycle()  # a fresh engine after the "restart"; the record is on disk
    replay = usepod.validate_topup(usepod.parse_requirement(body))
    assert (replay["proposal_id"], replay["duplicate"], replay["operation"]["state"]) == (first["proposal_id"], True, "paid")
    other = usepod.validate_topup(usepod.parse_requirement({**body, "correlation_id": f"usepod-{uuid.uuid4().hex[:12]}"}))
    assert other["proposal_id"] != first["proposal_id"] and other["duplicate"] is False
    assert _count() == 2 and len(base.sent) == 1


def test_an_owner_rejected_operation_replays_as_rejected_and_pays_nothing(nodes):
    from core.wallet import settlement, usepod

    base = nodes["base"]
    wallet = _pilot(BASE_SEPOLIA)
    base.fund(wallet["address"], 10**18)
    body = _requirement(expires_at=time.time() + 600)
    first = usepod.validate_topup(usepod.parse_requirement(body))
    answer = settlement.request_cancel(first["proposal_id"])
    assert answer["cancelled"] is True
    replay = usepod.validate_topup(usepod.parse_requirement(body))
    assert (replay["proposal_id"], replay["duplicate"]) == (first["proposal_id"], True)
    assert replay["state"] in ("rejected", "cancelled") and _count() == 1 and len(base.sent) == 0


def test_status_is_deterministic_by_provider_and_correlation_id(nodes):
    from core.wallet import usepod

    base = nodes["base"]
    wallet = _pilot(BASE_SEPOLIA)
    base.fund(wallet["address"], 10**18)
    assert usepod.status_for("never-seen") is None
    shared = f"op-{uuid.uuid4().hex[:8]}"
    one = usepod.validate_topup(usepod.parse_requirement(_requirement(correlation_id=shared, resource="https://one.example/v1/credits", expires_at=time.time() + 600)))
    two = usepod.validate_topup(usepod.parse_requirement(_requirement(correlation_id=shared, resource="https://two.example/v1/credits", amount="0.002", expires_at=time.time() + 600)))
    ambiguous = usepod.status_for(shared)
    assert ambiguous["ambiguous"] is True and sorted(ambiguous["providers"]) == ["https://one.example", "https://two.example"]
    assert usepod.status_for(shared, provider="https://one.example")["proposal_id"] == one["proposal_id"]
    assert usepod.status_for(shared, provider="https://two.example")["proposal_id"] == two["proposal_id"]
    paid = _pay(one["proposal_id"])
    assert usepod.status_for(shared, provider="https://one.example")["transfer"]["tx_id"] == paid["tx_id"]
    assert usepod.status_for(shared, provider="https://two.example")["transfer"] is None
