"""Mesh reward settlement: a verified WorkProof credits the worker's ledger.

accept_bid escrows the poster's side (CreditLedger.spend); submit_result previously
computed the reward and wrote a receipt but never credited the worker, so escrowed
credits left the poster and were never settled to anyone. These tests prove the
counterparty earn now lands on a verified proof, is skipped on an unverified or
replayed submission, and that a full accept_bid -> submit_result round conserves
credits across the two ledgers.
"""
from __future__ import annotations

import hashlib
from unittest import mock

from core.mesh.credit_ledger import CreditLedger
from core.mesh.task_router import (
    LocalNodeRegistry,
    MeshTaskRouter,
    TaskBid,
    _compute_proof_hash,
)
from storage.db import get_connection

POSTER = "settle-poster-node"
WORKER = "settle-worker-node"


def _reset(*nodes: str) -> None:
    """mesh_credit_ledger is not in the conftest reset set; clear these nodes' rows."""
    conn = get_connection()
    try:
        for node in nodes:
            conn.execute("DELETE FROM mesh_credit_ledger WHERE node_id = ?", (node,))
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


def _router() -> MeshTaskRouter:
    return MeshTaskRouter(LocalNodeRegistry(node_id=POSTER))


def _assign(router: MeshTaskRouter, task_id: str = "t1", worker: str = WORKER, credits: float = 5.0) -> None:
    """Set up the assignment + secret challenge accept_bid produces, minus HTTP/escrow."""
    router._assignments[task_id] = TaskBid(
        task_id=task_id, bidder_node_id=worker, bidder_endpoint="http://w",
        model_name="m", estimated_tokens=10, credits_requested=credits, signature="s",
    )
    router._minter.issue_task_challenge(task_id=task_id, issuer_id=POSTER, credits_offered=int(credits))


def _response(nonce: str, result: str) -> str:
    return hashlib.sha256(nonce.encode() + result.encode()).hexdigest()


def _rh(result: str) -> str:
    return hashlib.sha256(result.encode()).hexdigest()


def _submit_verified(router: MeshTaskRouter, task_id: str = "t1", result: str = "answer",
                     worker: str = WORKER) -> dict:
    nonce = router.commit_result(task_id, _rh(result), node_id=worker)["challenge_nonce"]
    return router.submit_result(task_id, result, challenge_response=_response(nonce, result), node_id=worker)


# ── a verified proof settles to the worker's ledger ──────────────────────────

def test_verified_result_settles_credits_to_worker():
    _reset(WORKER)
    r = _router()
    _assign(r, credits=5.0)
    before = CreditLedger(node_id=WORKER).balance()
    out = _submit_verified(r)
    assert out["verified"] is True
    assert out["settled"] is True
    assert out["credits_awarded"] == 5.0
    assert CreditLedger(node_id=WORKER).balance() == before + 5.0


def test_settlement_anchors_the_workproof_hash():
    _reset(WORKER)
    r = _router()
    _assign(r, credits=5.0)
    _submit_verified(r)
    hashes = CreditLedger(node_id=WORKER).proof_hashes
    assert _compute_proof_hash("t1", "answer", WORKER) in hashes


# ── an unverified submission settles nothing ─────────────────────────────────

def test_unverified_result_does_not_settle():
    _reset(WORKER)
    r = _router()
    _assign(r, credits=5.0)
    # Forged challenge response: verification fails, so nothing is credited.
    forged = hashlib.sha256(b"not-the-nonce").hexdigest()
    out = r.submit_result("t1", "answer", challenge_response=forged, node_id=WORKER)
    assert out["verified"] is False
    assert out["settled"] is False
    assert out["credits_awarded"] == 0.0
    assert CreditLedger(node_id=WORKER).balance() == 0.0


# ── replay settles at most once (no double credit) ───────────────────────────

def test_replay_settles_only_once():
    _reset(WORKER)
    r = _router()
    _assign(r, credits=5.0)
    nonce = r.commit_result("t1", _rh("answer"), node_id=WORKER)["challenge_nonce"]
    resp = _response(nonce, "answer")
    first = r.submit_result("t1", "answer", challenge_response=resp, node_id=WORKER)
    second = r.submit_result("t1", "answer", challenge_response=resp, node_id=WORKER)
    assert first["settled"] is True
    assert second["verified"] is False
    assert second["settled"] is False
    # Credited exactly once — the replayed proof adds nothing.
    assert CreditLedger(node_id=WORKER).balance() == 5.0


# ── end to end: accept_bid -> submit_result conserves credits ────────────────

def test_accept_bid_then_settle_conserves_credits():
    _reset(POSTER, WORKER)
    # Fund the poster so the escrow hold can be placed (accept_bid is fail-closed).
    CreditLedger(node_id=POSTER).earn("seed", 100.0, "a" * 64)
    r = _router()
    bid = TaskBid(task_id="t1", bidder_node_id=WORKER, bidder_endpoint="http://w",
                  model_name="m", estimated_tokens=10, credits_requested=5.0, signature="s")
    with mock.patch("requests.post"):  # don't actually notify the peer
        assert r.accept_bid(bid)["assigned"] is True

    poster_after_escrow = CreditLedger(node_id=POSTER).balance()
    assert poster_after_escrow == 95.0  # 100 seeded - 5 escrowed

    out = _submit_verified(r)
    assert out["settled"] is True

    poster_balance = CreditLedger(node_id=POSTER).balance()
    worker_balance = CreditLedger(node_id=WORKER).balance()
    assert poster_balance == 95.0
    assert worker_balance == 5.0
    # Credits are conserved across the two ledgers: nothing was minted or lost.
    assert poster_balance + worker_balance == 100.0
