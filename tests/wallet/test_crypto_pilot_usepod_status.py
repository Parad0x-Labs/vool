"""Correction 2 / R1: an operation's status describes payment evidence, never a local step.

SIMULATED CHAINS. The operation's state is a projection of the transfer record's own state: a claim or a signature is
`signing`; revoked bytes are `revoked`; a transmitted send with no proof is `unknown`; a receipt below the row's tag is
`pending`; a confirmed principal is `paid` (a bounded fee does not negate it); a failed execution is `failed`; a
released, discarded or cancelled record is `released`. `paid` and `proof_eligible` are true only for `paid`.
"""
from __future__ import annotations

import time

import pytest

from core.wallet.errors import WalletFault
from tests.wallet.test_crypto_pilot_usepod_operation import (
    BASE_SEPOLIA,
    SOLANA_DEVNET,
    _pay,
    _pilot,
    _requirement,
    _sol_key,
)
from tests.wallet.test_crypto_pilot_usepod_operation import home as operation_home
from tests.wallet.test_crypto_pilot_usepod_operation import nodes as operation_nodes

pytestmark = [pytest.mark.safety]

PIN = "482913"
home = operation_home
nodes = operation_nodes


def _op(correlation_id: str) -> dict:
    from core.wallet import usepod

    record = usepod.status_for(correlation_id)
    return record["operation"]


def test_a_signed_operation_before_the_send_is_signing_not_paid(nodes, monkeypatch):
    """The reviewer's original observation: paused immediately before the real send, the transfer is signed and
    nothing has left; the operation must say so. After the send completes it is paid."""
    from core.wallet import lifecycle, usepod

    base = nodes["base"]
    wallet = _pilot(BASE_SEPOLIA)
    base.fund(wallet["address"], 10**18)
    body = _requirement(expires_at=time.time() + 600)
    first = usepod.validate_topup(usepod.parse_requirement(body))
    assert (first["operation"]["state"], first["operation"]["paid"], first["operation"]["proof_eligible"]) == ("proposed", False, False)
    original = lifecycle.PaymentLifecycle.send_signed
    captured: list = []

    def observe_before_the_send(self, proposal_id, **kwargs):
        record = usepod.status_for(body["correlation_id"])
        captured.append((record["transfer"]["state"], record["operation"]["state"], record["operation"]["paid"], record["operation"]["proof_eligible"], len(base.sent)))
        return original(self, proposal_id, **kwargs)

    monkeypatch.setattr(lifecycle.PaymentLifecycle, "send_signed", observe_before_the_send)
    paid = _pay(first["proposal_id"])
    assert captured == [("signed", "signing", False, False, 0)], captured
    after = _op(body["correlation_id"])
    assert (paid["state"], after["state"], after["paid"], after["proof_eligible"], after["transfer_state"]) == ("confirmed", "paid", True, True, "confirmed")


def test_a_lost_reply_leaves_the_operation_unknown_until_the_chain_answers(nodes):
    """Different row and data: Solana Devnet, 0.0009 SOL; the node accepts the bytes but the reply is lost and the
    status stays null. The operation is `unknown` — transmitted, unproven — until the chain answers, then paid."""
    from core.wallet import settlement, usepod

    sol = nodes["sol"]
    sol.send_mode = "accept_then_500"
    sol.status_mode = "none"
    _pilot(SOLANA_DEVNET, "sol pocket")
    body = _requirement(network=SOLANA_DEVNET, asset="SOL", pay_to=_sol_key(), amount="0.0009", expires_at=time.time() + 600, resource="https://usepod.example/v1/credits/sol")
    first = usepod.validate_topup(usepod.parse_requirement(body))
    view = _pay(first["proposal_id"])
    assert view["state"] == "unknown" and sol.send_count() == 1
    during = _op(body["correlation_id"])
    assert (during["state"], during["paid"], during["proof_eligible"]) == ("unknown", False, False)
    sol.status_mode = "confirmed"
    settlement.observe_open_transfers()
    after = _op(body["correlation_id"])
    assert (after["state"], after["paid"], after["transfer_state"]) == ("paid", True, "confirmed")


def test_a_pending_then_failed_execution_is_pending_then_failed_never_paid(nodes):
    from core.wallet import settlement, usepod

    base = nodes["base"]
    base.mine_mode = "revert"
    wallet = _pilot(BASE_SEPOLIA)
    base.fund(wallet["address"], 10**18)
    body = _requirement(expires_at=time.time() + 600)
    first = usepod.validate_topup(usepod.parse_requirement(body))
    base.finalized_block = 50
    view = _pay(first["proposal_id"])
    assert view["state"] == "pending"
    pending = _op(body["correlation_id"])
    assert (pending["state"], pending["paid"]) == ("pending", False)
    base.finalized_block = base.block_number
    settlement.observe_open_transfers()
    failed = _op(body["correlation_id"])
    assert (failed["state"], failed["paid"], failed["proof_eligible"], failed["transfer_state"]) == ("failed", False, False, "failed_on_chain")


def test_revoked_bytes_are_revoked_then_released_never_paid(nodes, monkeypatch):
    from core.wallet import lifecycle, settlement, usepod

    base = nodes["base"]
    wallet = _pilot(BASE_SEPOLIA)
    base.fund(wallet["address"], 10**18)
    now = time.time()
    body = _requirement(expires_at=now + 30)
    first = usepod.validate_topup(usepod.parse_requirement(body))
    original = lifecycle.PaymentLifecycle.send_signed

    def window_closes_before_the_send(self, proposal_id, **kwargs):
        monkeypatch.setattr(time, "time", lambda: now + 31)
        return original(self, proposal_id, **kwargs)

    monkeypatch.setattr(lifecycle.PaymentLifecycle, "send_signed", window_closes_before_the_send)
    with pytest.raises(WalletFault):
        _pay(first["proposal_id"])
    revoked = _op(body["correlation_id"])
    assert (revoked["state"], revoked["paid"], revoked["transfer_state"]) == ("revoked", False, "signed_revoked") and len(base.sent) == 0
    settlement.owner_discard(first["proposal_id"])
    released = _op(body["correlation_id"])
    assert (released["state"], released["paid"], released["transfer_state"]) == ("released", False, "discarded")


def test_a_bounded_fee_does_not_negate_a_confirmed_principal(nodes, monkeypatch):
    from core.wallet import usepod

    base = nodes["base"]
    wallet = _pilot(BASE_SEPOLIA)
    base.fund(wallet["address"], 10**18)
    original = base._answer

    def without_l1_fee(body):
        kind, answer = original(body)
        if body.get("method") == "eth_getTransactionReceipt" and isinstance(answer, dict) and isinstance(answer.get("result"), dict):
            answer = dict(answer)
            answer["result"] = {k: v for k, v in answer["result"].items() if k != "l1Fee"}
        return kind, answer

    monkeypatch.setattr(base, "_answer", without_l1_fee)
    body = _requirement(expires_at=time.time() + 600)
    first = usepod.validate_topup(usepod.parse_requirement(body))
    view = _pay(first["proposal_id"])
    assert (view["state"], view["fee_state"]) == ("confirmed", "bounded")
    op = _op(body["correlation_id"])
    assert (op["state"], op["paid"], op["proof_eligible"]) == ("paid", True, True)


def test_restart_and_replay_report_the_same_truthful_state(nodes):
    from core.wallet import chains, lifecycle, usepod

    base = nodes["base"]
    wallet = _pilot(BASE_SEPOLIA)
    base.fund(wallet["address"], 10**18)
    body = _requirement(expires_at=time.time() + 600)
    first = usepod.validate_topup(usepod.parse_requirement(body))
    replay_before = usepod.validate_topup(usepod.parse_requirement(body))
    assert (replay_before["duplicate"], replay_before["operation"]["state"], replay_before["operation"]["paid"]) == (True, "proposed", False)
    assert _pay(first["proposal_id"])["state"] == "confirmed"
    chains.invalidate_chain_identity()
    lifecycle.default_lifecycle()
    replay_after = usepod.validate_topup(usepod.parse_requirement(body))
    assert (replay_after["duplicate"], replay_after["operation"]["state"], replay_after["operation"]["paid"]) == (True, "paid", True)
