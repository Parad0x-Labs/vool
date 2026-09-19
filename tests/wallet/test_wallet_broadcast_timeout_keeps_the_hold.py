"""A Solana broadcast releases its hold only on POSITIVE evidence of non-execution; everything else is UNKNOWN.

Operator correction (2026-09-07): an HTTP response is not proof of non-execution. A node can accept and record
`sendTransaction` and then a proxy answers 500, or the body comes back malformed, or a JSON-RPC error arrives with
the wrong id or an unlisted code. Releasing the budget there re-opens spending for a transaction that may land.

The law, reproduced through the REAL loopback RPC boundary (`ScriptedRpc` records the transaction, then answers):
* release on proven PRE-DISPATCH failure (connection refused, unresolved host, no route: the bytes never left);
* release on a NARROWLY VALIDATED definitive rejection (a well-formed JSON-RPC error to OUR request id with a
  code that means the node did not forward: preflight/simulation failure, signature verification failure,
  invalid request/params, parse error, method not found);
* everything else -- HTTP status without such a body, malformed response, wrong id, unlisted code, timeouts,
  resets, any other exception -- is UNKNOWN: state `broadcast`, `settlement=submitted_unknown`, hold RESERVED,
  the payment effect left unresolved (no `applied=False` claim), no automatic retry.
"""
from __future__ import annotations

import http.client
import socket
import urllib.error

import pytest

PIN = "482913"
DESTINATION = "9xQeWvG816bUx9EPjHmaT23yvVM2ZWbrrpZb9PusVFin"


def _pocket(custody):
    return custody.create_pocket_wallet(acknowledged_warning=True, confirmation_phrase=custody.POCKET_CONFIRMATION_PHRASE, pin=PIN).profile


def _drive(lifecycle, proposal, *, pin=PIN):
    from core.wallet import approval

    lifecycle.prepare(proposal.proposal_id)
    return lifecycle.approve_and_execute(proposal.proposal_id, approver=approval.PinApprover(pin))


def _propose(custody, proposals):
    profile = _pocket(custody)
    return proposals.propose_transaction(wallet_id=profile.wallet_id, destination=DESTINATION, amount_minor=1, asset="SOL", origin="user")


def _resolutions(proposal_id: str) -> list[dict]:
    """Resolution events recorded for the payment effect; empty while its fate is unknown."""
    from core.runtime_continuity import list_unresolved_effect_resolutions
    from core.wallet import reconciliation

    return list_unresolved_effect_resolutions(reconciliation.logical_effect_id(proposal_id))


def _assert_unknown(wallet_env, exc, proposal_id: str, kind_prefix: str) -> None:
    from core.wallet import limits, proposals, receipts

    assert exc.value.code == "wallet_broadcast_failed"
    reason = str(exc.value.context.get("reason") or "")
    assert reason.startswith("broadcast_unknown:" + kind_prefix), reason
    proposal = proposals.get_proposal(proposal_id)
    assert proposal.state == proposals.STATE_BROADCAST, "an answered-but-unproven send is UNKNOWN, not failed"
    assert limits.reservation_state(proposal_id) == limits.RESERVATION_RESERVED, "the hold stays until positive no-dispatch or chain reconciliation"
    assert _resolutions(proposal_id) == [], "no applied=False claim may be written for an UNKNOWN send"
    assert wallet_env["rpc"].send_count() == 1, "no automatic retry"
    rows = [r for r in receipts.list_receipts() if r.get("proposal_id") == proposal_id]
    assert rows and rows[-1]["state"] == proposals.STATE_BROADCAST and not any(r["state"] == proposals.STATE_FAILED for r in rows), rows


@pytest.mark.parametrize(
    ("mode", "kind_prefix"),
    [
        ("accept_then_500", "http_500"),
        ("accept_then_malformed", "malformed_response"),
        ("accept_then_wrong_id", "malformed_response:id_mismatch"),  # an answer to a different request id is not ours
        ("accept_then_unlisted_error", "rpc_error:-32005"),
    ],
)
def test_a_recorded_send_with_an_unproven_answer_is_unknown_and_keeps_the_hold(wallet_env, mode, kind_prefix):
    """The loopback node RECORDS the transaction, then answers in a way that proves nothing about execution."""
    from core.wallet import custody, lifecycle, proposals
    from core.wallet.errors import WalletFault

    wallet_env["rpc"].send_mode = mode
    p = _propose(custody, proposals)
    with pytest.raises(WalletFault) as exc:
        _drive(lifecycle.default_lifecycle(), p)
    assert wallet_env["rpc"].send_count() == 1 and wallet_env["rpc"].sent, "the node did receive and record the transaction"
    _assert_unknown(wallet_env, exc, p.proposal_id, kind_prefix)


def test_transport_silence_after_the_send_is_unknown(wallet_env, monkeypatch):
    from core.wallet import custody, lifecycle, limits, proposals
    from core.wallet.errors import WalletFault

    def _timeout(self, raw_transaction):
        raise TimeoutError("read timed out")

    monkeypatch.setattr(lifecycle.RpcClient, "broadcast", _timeout)
    p = _propose(custody, proposals)
    with pytest.raises(WalletFault) as exc:
        _drive(lifecycle.default_lifecycle(), p)
    assert str(exc.value.context.get("reason") or "").startswith("broadcast_unknown:TimeoutError")
    assert proposals.get_proposal(p.proposal_id).state == proposals.STATE_BROADCAST
    assert limits.reservation_state(p.proposal_id) == limits.RESERVATION_RESERVED
    assert _resolutions(p.proposal_id) == []


def test_a_validated_preflight_rejection_releases_the_hold(wallet_env):
    """Positive control: a well-formed JSON-RPC -32002 to OUR id means the node did not forward the transaction."""
    from core.wallet import custody, lifecycle, limits, proposals
    from core.wallet.errors import WalletFault

    wallet_env["rpc"].send_ok = False
    p = _propose(custody, proposals)
    with pytest.raises(WalletFault) as exc:
        _drive(lifecycle.default_lifecycle(), p)
    assert exc.value.code == "wallet_broadcast_failed"
    assert str(exc.value.context.get("reason") or "") == "broadcast_rejected:-32002"
    assert proposals.get_proposal(p.proposal_id).state == proposals.STATE_FAILED
    assert limits.reservation_state(p.proposal_id) == limits.RESERVATION_RELEASED
    assert any("FAILED_SAFE" in str(row) for row in _resolutions(p.proposal_id)), "a proven rejection resolves the effect as failed-safe"
    assert wallet_env["rpc"].send_count() == 1


def test_a_connection_that_was_never_made_releases_the_hold(wallet_env, monkeypatch):
    """Positive control: the bytes never left this machine."""
    from core.wallet import custody, lifecycle, limits, proposals
    from core.wallet.errors import WalletFault

    def _refused(self, raw_transaction):
        raise urllib.error.URLError(ConnectionRefusedError(61, "Connection refused"))

    monkeypatch.setattr(lifecycle.RpcClient, "broadcast", _refused)
    p = _propose(custody, proposals)
    with pytest.raises(WalletFault) as exc:
        _drive(lifecycle.default_lifecycle(), p)
    assert str(exc.value.context.get("reason") or "") == "broadcast_pre_dispatch:ConnectionRefusedError"
    assert proposals.get_proposal(p.proposal_id).state == proposals.STATE_FAILED
    assert limits.reservation_state(p.proposal_id) == limits.RESERVATION_RELEASED


def test_a_successful_submission_settles_the_hold(wallet_env):
    """Positive control: the node answers a signature to our id; the hold settles and the receipt confirms."""
    from core.wallet import custody, lifecycle, limits, proposals

    p = _propose(custody, proposals)
    receipt = _drive(lifecycle.default_lifecycle(), p)
    assert receipt.state == proposals.STATE_CONFIRMED
    assert limits.reservation_state(p.proposal_id) == limits.RESERVATION_SETTLED
    assert wallet_env["rpc"].send_count() == 1


def _rejected(code: int, *, validated: bool = True):
    from core.wallet.lifecycle import RpcRejected

    return RpcRejected("sendTransaction", code, "message", validated=validated)


@pytest.mark.parametrize(
    ("factory", "release", "kind"),
    [
        (lambda: urllib.error.URLError(ConnectionRefusedError(61, "refused")), True, "pre_dispatch:ConnectionRefusedError"),
        (lambda: urllib.error.URLError(socket.gaierror(8, "nodename nor servname provided")), True, "pre_dispatch:gaierror"),
        (lambda: _rejected(-32002), True, "rejected:-32002"),
        (lambda: _rejected(-32003), True, "rejected:-32003"),
        (lambda: _rejected(-32602), True, "rejected:-32602"),
        (lambda: _rejected(-32005), False, "rpc_error:-32005"),
        (lambda: _rejected(-32002, validated=False), False, "rpc_error:-32002:unvalidated"),
        (lambda: urllib.error.HTTPError("http://rpc", 500, "boom", {}, None), False, "http_500"),
        (lambda: urllib.error.HTTPError("http://rpc", 400, "bad", {}, None), False, "http_400"),
        (lambda: TimeoutError("read timed out"), False, "TimeoutError"),
        (lambda: urllib.error.URLError(TimeoutError("timed out")), False, "TimeoutError"),  # socket.timeout is TimeoutError on 3.10+
        (lambda: http.client.RemoteDisconnected("closed"), False, "RemoteDisconnected"),
        (lambda: ConnectionResetError(), False, "ConnectionResetError"),
        (lambda: RuntimeError("rpc_error:sendTransaction:-32002"), False, "RuntimeError"),
        (lambda: ValueError("anything unexpected"), False, "ValueError"),
    ],
)
def test_the_classifier_releases_only_on_positive_evidence(factory, release, kind):
    from core.wallet.lifecycle import classify_broadcast_failure

    fate = classify_broadcast_failure(factory())
    assert fate.release is release, fate
    assert fate.kind == kind, fate
