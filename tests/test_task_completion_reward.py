"""task_completion self-credit is gated + bounded.

It now mints only against an internally-consistent work receipt, always to the
LOCAL peer, and is capped per window so a request flood can't inflate the balance.
"""
from __future__ import annotations

import dataclasses
from unittest import mock

import core.web.api.service as svc
from core.web0_work_receipt import issue_work_receipt


def _receipt():
    return issue_work_receipt(task_id="t1", result="some work output", worker_id="vool")


def test_invalid_proof_earns_nothing():
    r = _receipt()
    tampered = dataclasses.replace(r, proof=dataclasses.replace(r.proof, proof_hash="ff" * 32))
    out = svc._award_task_completion_credit(tampered)
    assert out["awarded"] is False
    assert out["reason"] == "invalid_proof"


def test_result_binding_mismatch_earns_nothing():
    r = _receipt()
    tampered = dataclasses.replace(r, result_hash="ab" * 32)  # != proof.result_hash
    out = svc._award_task_completion_credit(tampered)
    assert out["awarded"] is False
    assert out["reason"] == "result_binding_mismatch"


def test_valid_receipt_credits_the_local_peer_only():
    svc._task_award_window.clear()
    calls = {}

    def fake_award(peer_id, amount, reason="", *, receipt_id=None):
        calls.update(peer_id=peer_id, amount=amount, reason=reason)
        return True

    # Build the receipt with the real signer so its signature and signer_peer_id are
    # consistent; only the award step's recipient lookup is mocked to the local peer.
    receipt = _receipt()
    with mock.patch("core.credit_ledger.award_credits", fake_award), \
         mock.patch("network.signer.get_local_peer_id", lambda: "LOCAL_PEER"):
        out = svc._award_task_completion_credit(receipt)
    assert out["awarded"] is True
    assert calls["peer_id"] == "LOCAL_PEER"  # recipient is always the local node
    assert calls["amount"] == 1.0 and calls["reason"] == "task_completion"


def test_request_flood_is_rate_limited():
    svc._task_award_window.clear()
    r = _receipt()
    try:
        with mock.patch("core.credit_ledger.award_credits", lambda *a, **k: True), \
             mock.patch("network.signer.get_local_peer_id", lambda: "LOCAL"):
            awarded = sum(
                1 for _ in range(svc._TASK_AWARD_MAX_PER_WINDOW + 10)
                if svc._award_task_completion_credit(r)["awarded"]
            )
    finally:
        # The award window is process-global: a full window left behind rate-limits the next
        # suite's real award (seen as test_launch_readiness's credit test reading 0.0).
        svc._task_award_window.clear()
    assert awarded == svc._TASK_AWARD_MAX_PER_WINDOW  # the flood is capped at the window max
