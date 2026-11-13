"""Two Mac findings on a Crypto Pilot send (Nov 13).

1. A send of exactly the per-payment cap previewed fine and was refused only at Approve: Approve reserves the amount
   AND the network fee ceiling against the caps. The preview now runs the same check with its own fee ceiling: a send
   over the cap previews as not approvable with the reason and what would fit, and Approve still refuses it (nothing
   slips past the cap). A send of the cap minus the fee ceiling previews clean and is sent.
2. "Approve and send" with nothing typed posted an empty PIN, which was counted as a refused attempt (five lock the
   payment). An empty PIN or password is now refused as "type it first" before any credential check: no attempt is
   counted, the wallet's unlock throttle is untouched, the payment stays pending, and the right PIN then sends once.
"""
from __future__ import annotations

import json
import uuid

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.wallet.errors import WalletFault
from tests.wallet._rig import DEVNET_GENESIS, ScriptedRpc

pytestmark = [pytest.mark.safety]

SOLANA_DEVNET = "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1"
PIN = "482913"
CAP = 100_000_000  # the default per-payment cap: 0.1 SOL
HEADERS = {"host": "127.0.0.1:8731", "origin": "http://127.0.0.1:8731", "content-type": "application/json"}


@pytest.fixture
def node():
    with ScriptedRpc(genesis_hash=DEVNET_GENESIS) as rig:
        rig.real_signature = True
        rig.status_keyed = True
        yield rig


@pytest.fixture
def pilot(monkeypatch, tmp_path, node):
    monkeypatch.setenv("VOOL_WALLET_ENABLED", "1")
    monkeypatch.setenv("VOOL_WALLET_NETWORK_ENVIRONMENT", "testnet")
    monkeypatch.setenv("VOOL_WALLET_TESTNET_RPC_URL", node.url)
    monkeypatch.delenv("VOOL_WALLET_RPC_URLS", raising=False)
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(tmp_path / "blackbox"))
    from core.blackbox import store as store_module
    from core.wallet import capabilities, chains, lifecycle

    monkeypatch.setattr(capabilities, "PILOT_TRANSFER_READY_ROWS", frozenset({SOLANA_DEVNET}))
    monkeypatch.setattr(lifecycle, "_CONFIRM_BUDGET_SECONDS", 1.0)
    store_module.reset_default_store()
    chains.invalidate_chain_identity()
    yield node
    chains.invalidate_chain_identity()
    store_module.reset_default_store()


def _sol_key() -> str:
    from core.vool_wallet import b58encode

    return b58encode(Ed25519PrivateKey.generate().public_key().public_bytes_raw())


def _wallet() -> dict:
    from core.wallet import pilot_custody

    created = pilot_custody.create_pilot_wallet(network=SOLANA_DEVNET, method="pin", credential=PIN, credential_confirmation=PIN, creation_key=f"c-{uuid.uuid4().hex}")
    revealed = pilot_custody.reveal_pilot_backup(created["wallet_id"], credential=PIN)
    return pilot_custody.acknowledge_pilot_backup(created["wallet_id"], ack_token=revealed["ack_token"])


def _pending(wallet: dict, amount: int):
    from core.wallet import lifecycle, proposals

    proposal = proposals.propose_transaction(wallet_id=wallet["wallet_id"], destination=_sol_key(), amount_minor=amount, asset="SOL", origin=proposals.ORIGIN_USER)
    engine = lifecycle.default_lifecycle()
    assert engine.prepare(proposal.proposal_id).state == proposals.STATE_PENDING_APPROVAL
    return engine, proposal


def _approve(engine, proposal_id: str, quote: dict, pin: str = PIN) -> dict:
    from core.wallet import approval

    return engine.approve_pilot_transfer(proposal_id, quote_id=quote["quote_id"], quote_digest=quote["digest"], approver=approval.PinApprover(pin))


def _throttle_rows() -> list[tuple]:
    from core.wallet.store import connection

    with connection() as conn:
        return [tuple(r) for r in conn.execute("SELECT scope, failures, locked_until FROM wallet_unlock_attempts ORDER BY scope").fetchall()]


# --- 1. the cap and the fee ----------------------------------------------------------------------------------------

def test_a_send_of_exactly_the_cap_previews_as_over_the_cap_and_approve_still_refuses_it(pilot):
    from core.wallet import quotes

    node = pilot
    engine, proposal = _pending(_wallet(), CAP)
    quote = quotes.mint_quote(proposal.proposal_id)
    fields = quote["fields"]
    fee_max = int(fields["fee_max_minor"])
    assert fee_max > 0
    blocked = fields.get("approval_unavailable") or {}
    assert blocked.get("code") == "wallet_limit_exceeded" and blocked.get("limit") == "per_transaction", fields.get("approval_unavailable")
    room = (CAP - fee_max) / 1e9
    assert "0.1 SOL for one payment" in blocked["message"] and f"Send at most {room:.9f}".rstrip("0") in blocked["message"], blocked["message"]
    assert any(w["code"] == "over_spending_limit" for w in fields["review"]["warnings"]), fields["review"]["warnings"]
    # the binding check at Approve is unchanged: the preview only says it first
    with pytest.raises(WalletFault) as refused:
        _approve(engine, proposal.proposal_id, quote)
    assert refused.value.code == "wallet_limit_exceeded"
    assert node.send_count() == 0


def test_a_send_of_the_cap_minus_the_fee_ceiling_previews_clean_and_is_sent(pilot):
    from core.wallet import quotes

    node = pilot
    wallet = _wallet()
    engine, probe = _pending(wallet, CAP)
    fee_max = int(quotes.mint_quote(probe.proposal_id)["fields"]["fee_max_minor"])
    engine, proposal = _pending(wallet, CAP - fee_max)
    quote = quotes.mint_quote(proposal.proposal_id)
    assert "approval_unavailable" not in quote["fields"], quote["fields"].get("approval_unavailable")
    assert not any(w["code"] == "over_spending_limit" for w in quote["fields"]["review"]["warnings"])
    assert _approve(engine, proposal.proposal_id, quote)["transfer"]["state"] == "confirmed" and node.send_count() == 1


# --- 2. an empty PIN is not an attempt -----------------------------------------------------------------------------

def test_an_empty_pin_is_refused_as_type_it_first_and_never_counts_or_touches_the_throttle(pilot):
    from core.wallet import proposals, quotes

    node = pilot
    engine, proposal = _pending(_wallet(), 500_000)
    quote = quotes.mint_quote(proposal.proposal_id)
    before = _throttle_rows()
    for _ in range(7):  # more than the 5 refusals that would lock the payment
        with pytest.raises(WalletFault) as empty:
            _approve(engine, proposal.proposal_id, quote, pin="")
        assert empty.value.code == "wallet_pin_invalid" and empty.value.context.get("reason") == "credential_required"
    assert proposals.approval_refusals(proposal.proposal_id) == 0
    assert _throttle_rows() == before, "an empty PIN touched the unlock throttle"
    assert proposals.get_proposal(proposal.proposal_id).state == proposals.STATE_PENDING_APPROVAL
    assert quotes.get_quote(quote["quote_id"])["state"] == "open" and node.send_count() == 0
    assert _approve(engine, proposal.proposal_id, quote)["transfer"]["state"] == "confirmed" and node.send_count() == 1


def test_the_approve_door_answers_an_empty_pin_with_credential_required_and_counts_nothing(pilot):
    from core.wallet import proposals, quotes
    from core.web.api.wallet_api import handle_wallet_post

    engine, proposal = _pending(_wallet(), 500_000)
    quote = quotes.mint_quote(proposal.proposal_id)
    before = _throttle_rows()
    response = handle_wallet_post("/api/wallet/approve", {"proposal_id": proposal.proposal_id, "quote_id": quote["quote_id"], "quote_digest": quote["digest"], "pin": ""},
                                  client_host="127.0.0.1", headers=HEADERS)
    answer = json.loads(response.body)
    assert response.status == 400 and answer["error"] == "wallet_pin_invalid" and answer["fault"]["context"]["reason"] == "credential_required", answer
    assert proposals.approval_refusals(proposal.proposal_id) == 0 and _throttle_rows() == before
    assert proposals.get_proposal(proposal.proposal_id).state == proposals.STATE_PENDING_APPROVAL
