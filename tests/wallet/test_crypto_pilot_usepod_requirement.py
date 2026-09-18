"""Correction 1 / F1: the provider requirement is durable and binds the quote, the approval, the claim and the send.

SIMULATED CHAINS. A UsePod requirement is persisted at the wallet's own authority, pinned field by field to its
proposal. A still-valid wallet quote never pays an expired provider request; a refreshed quote or a restart cannot
extend the provider's window; a non-finite, missing or non-positive expiry is refused at intake. Preservation: an
unexpired requirement pays exactly once as before, and its operation is bound to the transfer it produced.
"""
from __future__ import annotations

import json
import time
import uuid

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from tests.wallet._rig import DEVNET_GENESIS, ScriptedRpc
from tests.wallet._rig_evm_native import ScriptedEvmNativeChain
from tests.wallet.test_crypto_pilot_usepod import _count, _pilot, _requirement
from tests.wallet.test_crypto_pilot_usepod import home as usepod_home

from core.wallet.errors import WalletFault

home = usepod_home  # the usepod corpus's environment fixture, under its own name here

pytestmark = [pytest.mark.safety]

BASE_SEPOLIA = "eip155:84532"
SOLANA_DEVNET = "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1"
PIN = "482913"


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


def _approve(proposal_id: str, quote: dict):
    from core.wallet import approval, lifecycle

    return lifecycle.default_lifecycle().approve_pilot_transfer(proposal_id, quote_id=quote["quote_id"], quote_digest=quote["digest"], approver=approval.PinApprover(PIN))


def _frozen_clock(monkeypatch, moment: float) -> None:
    monkeypatch.setattr(time, "time", lambda: moment)


def test_a_still_valid_wallet_quote_never_pays_an_expired_provider_requirement(nodes, monkeypatch):
    """The reviewer's original workflow: a ten-second provider window, a wallet quote minted inside it, the clock past
    the window while the quote is still valid, the correct PIN. Nothing is signed or sent; the request is over."""
    from core.wallet import proposals, quotes, usepod

    base = nodes["base"]
    wallet = _pilot(BASE_SEPOLIA)
    base.fund(wallet["address"], 10**18)
    now = time.time()
    body = _requirement(expires_at=now + 10)
    first = usepod.validate_topup(usepod.parse_requirement(body))
    quote = quotes.mint_quote(first["proposal_id"])
    _frozen_clock(monkeypatch, now + 20)
    with pytest.raises(WalletFault) as refused:
        _approve(first["proposal_id"], quote)
    assert (refused.value.code, refused.value.context.get("reason")) == ("wallet_quote_expired", "provider_requirement_expired"), refused.value.context
    assert len(base.sent) == 0 and len(base.pending) == 0
    assert proposals.get_proposal(first["proposal_id"]).state == proposals.STATE_EXPIRED, "the request is over, not merely refused once"
    record = usepod.status_for(body["correlation_id"])
    assert (record["proposal_state"], record["operation"]["state"], record["transfer"]) == ("expired", "expired", None)
    # a fresh quote cannot bring it back
    with pytest.raises(WalletFault) as gone:
        quotes.mint_quote(first["proposal_id"])
    assert gone.value.code == "wallet_quote_unavailable"


def test_a_refreshed_quote_after_a_restart_cannot_extend_a_provider_requirement(nodes, monkeypatch):
    """Different row, coin, amount and window: Solana Devnet, 0.0007 SOL, five seconds. The engine is rebuilt (a
    restart), the quote is re-minted after the window: refused; the earlier quote is refused too; nothing is sent."""
    from core.wallet import chains, lifecycle, quotes, usepod

    sol = nodes["sol"]
    _pilot(SOLANA_DEVNET, "sol pocket")
    now = time.time()
    body = _requirement(network=SOLANA_DEVNET, asset="SOL", pay_to=_sol_key(), amount="0.0007", expires_at=now + 5, resource="https://usepod.example/v1/credits/sol")
    first = usepod.validate_topup(usepod.parse_requirement(body))
    assert first["network"] == SOLANA_DEVNET and first["amount_minor"] == "700000"
    early = quotes.mint_quote(first["proposal_id"])
    _frozen_clock(monkeypatch, now + 6)
    chains.invalidate_chain_identity()
    engine = lifecycle.default_lifecycle()  # a fresh engine after the "restart"; the operation record is on disk
    with pytest.raises(WalletFault) as refreshed:
        quotes.mint_quote(first["proposal_id"])
    assert (refreshed.value.code, refreshed.value.context.get("reason")) == ("wallet_quote_unavailable", "provider_requirement_expired"), refreshed.value.context
    from core.wallet import approval

    with pytest.raises(WalletFault) as stale:
        engine.approve_pilot_transfer(first["proposal_id"], quote_id=early["quote_id"], quote_digest=early["digest"], approver=approval.PinApprover(PIN))
    assert stale.value.code in ("wallet_quote_expired", "wallet_quote_unavailable", "wallet_duplicate_payment"), stale.value.context
    assert sol.send_count() == 0
    assert usepod.status_for(body["correlation_id"])["operation"]["state"] == "expired"


def test_an_unexpired_requirement_pays_once_and_binds_its_operation_to_the_transfer(nodes):
    """Preservation: the delivered flow is unchanged inside the window, and the operation now names its transfer."""
    from core.wallet import quotes, usepod

    base = nodes["base"]
    wallet = _pilot(BASE_SEPOLIA)
    base.fund(wallet["address"], 10**18)
    body = _requirement(expires_at=time.time() + 600)
    first = usepod.validate_topup(usepod.parse_requirement(body))
    result = _approve(first["proposal_id"], quotes.mint_quote(first["proposal_id"]))
    assert result["transfer"]["state"] == "confirmed" and len(base.sent) == 1
    record = usepod.status_for(body["correlation_id"])
    assert (record["operation"]["state"], record["operation"]["proposal_id"], record["transfer"]["tx_id"]) == ("paid", first["proposal_id"], result["transfer"]["tx_id"])
    pinned = record["operation"]
    assert (pinned["network"], pinned["asset"], pinned["pay_to"], pinned["amount_minor"], pinned["wallet_id"]) == (BASE_SEPOLIA, "ETH", body["pay_to"], str(10**15), wallet["wallet_id"])


@pytest.mark.parametrize(("expiry", "reason"), [
    (float("nan"), "expires_at_not_finite"),
    ("NaN", "expires_at_not_finite"),
    (float("inf"), "expires_at_not_finite"),
    ("-inf", "expires_at_not_finite"),
    (None, "expires_at_required"),
    ("", "expires_at_required"),
    (0, "expires_at_not_positive"),
    (-5, "expires_at_not_positive"),
    ("soon", "expires_at_not_a_number"),
])
def test_a_non_finite_missing_or_non_positive_expiry_is_refused_at_intake(home, expiry, reason):
    from core.wallet import usepod

    body = _requirement()
    if expiry is None:
        body.pop("expires_at")
    else:
        body["expires_at"] = expiry
    with pytest.raises(usepod.RequirementError) as bad:
        usepod.parse_requirement(body)
    assert bad.value.reason == reason


def test_a_pinned_field_that_no_longer_matches_the_proposal_is_refused_before_any_credential(nodes):
    """The durable record is the provider's truth: a rewound or corrupted recipient on it never pays."""
    from core.wallet import proposals, quotes, usepod
    from core.wallet.store import connection

    base = nodes["base"]
    wallet = _pilot(BASE_SEPOLIA)
    base.fund(wallet["address"], 10**18)
    body = _requirement(expires_at=time.time() + 600)
    first = usepod.validate_topup(usepod.parse_requirement(body))
    quote = quotes.mint_quote(first["proposal_id"])
    with connection() as conn:
        conn.execute("UPDATE wallet_usepod_operations SET pay_to = ? WHERE proposal_id = ?", ("0x" + "9" * 40, first["proposal_id"]))
    with pytest.raises(WalletFault) as refused:
        _approve(first["proposal_id"], quote)
    assert (refused.value.code, refused.value.context.get("reason")) == ("wallet_quote_mismatch", "provider_requirement_changed"), refused.value.context
    assert len(base.sent) == 0 and proposals.approval_refusals(first["proposal_id"]) == 0, "no credential attempt was consumed"


def test_a_requirement_expiring_between_signing_and_sending_revokes_the_bytes_and_sends_nothing(nodes, monkeypatch):
    """The send's own gate: the window closes after the signature and before the transmit. The bytes are revoked,
    nothing leaves, the never-sent transfer can be discarded and its hold released."""
    from core.wallet import lifecycle, quotes, settlement, transfers, usepod

    base = nodes["base"]
    wallet = _pilot(BASE_SEPOLIA)
    base.fund(wallet["address"], 10**18)
    now = time.time()
    body = _requirement(expires_at=now + 30)
    first = usepod.validate_topup(usepod.parse_requirement(body))
    quote = quotes.mint_quote(first["proposal_id"])
    original = lifecycle.PaymentLifecycle.send_signed

    def window_closes_before_the_send(self, proposal_id, **kwargs):
        assert transfers.latest_receipt(proposal_id)["state"] == "signed"
        _frozen_clock(monkeypatch, now + 31)
        return original(self, proposal_id, **kwargs)

    monkeypatch.setattr(lifecycle.PaymentLifecycle, "send_signed", window_closes_before_the_send)
    with pytest.raises(WalletFault) as refused:
        _approve(first["proposal_id"], quote)
    assert (refused.value.code, refused.value.context.get("reason")) == ("wallet_broadcast_failed", "signed_not_sent:provider_requirement_expired"), refused.value.context
    assert transfers.latest_receipt(first["proposal_id"])["state"] == "signed_revoked" and len(base.sent) == 0 and len(base.pending) == 0
    dropped = settlement.owner_discard(first["proposal_id"])
    assert dropped["state"] == "discarded"
    operation = usepod.status_for(body["correlation_id"])["operation"]
    assert (operation["state"], operation["record_state"], operation["paid"]) == ("released", "expired", False), "released bytes, a closed window: both stay visible"


def test_operations_are_scoped_to_their_provider(nodes):
    """The same correlation id from two different providers is two operations, never a collision."""
    from core.wallet import usepod

    base = nodes["base"]
    wallet = _pilot(BASE_SEPOLIA)
    base.fund(wallet["address"], 10**18)
    shared = f"op-{uuid.uuid4().hex[:8]}"
    one = usepod.validate_topup(usepod.parse_requirement(_requirement(correlation_id=shared, resource="https://one.example/v1/credits")))
    two = usepod.validate_topup(usepod.parse_requirement(_requirement(correlation_id=shared, resource="https://two.example/v1/credits", amount="0.002")))
    assert one["proposal_id"] != two["proposal_id"] and _count() == 2
    assert usepod.status_for(shared, provider="https://one.example")["proposal_id"] == one["proposal_id"]
    assert usepod.status_for(shared, provider="https://two.example")["proposal_id"] == two["proposal_id"]
