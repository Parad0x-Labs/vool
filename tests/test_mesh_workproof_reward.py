"""Mesh reward is bound to a verified commit/reveal WorkProof.

The old path rewarded a helper for a self-computable `sha256(task+result+worker)`
hash — anyone could forge it. Now `submit_result` grants the contribution credit
only when a genuine commit/reveal WorkProof verifies. These tests prove the
legitimate path is paid and every forgery earns zero.
"""
from __future__ import annotations

import hashlib
from unittest import mock

from core.mesh.credit_ledger import CreditLedger
from core.mesh.task_router import LocalNodeRegistry, MeshTaskRouter, TaskBid
from network import signer
from storage.db import get_connection

POSTER = "poster-node"
WORKER = "worker-A"


def _router() -> MeshTaskRouter:
    return MeshTaskRouter(LocalNodeRegistry(node_id=POSTER))


def _reset_mesh_ledger() -> None:
    """mesh_credit_ledger is not in the conftest reset set; clear it explicitly.

    The real schema is created lazily by CreditLedger on first use; if the table
    does not exist yet the DELETE is a harmless no-op (nothing to clear).
    """
    conn = get_connection()
    try:
        conn.execute("DELETE FROM mesh_credit_ledger")
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


def _fund_poster(amount: float = 100.0) -> None:
    CreditLedger(node_id=POSTER).earn("seed", amount, "a" * 64)


class _FakeResp:
    def __init__(self, data: dict) -> None:
        self._data = data

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._data


def _assign(router: MeshTaskRouter, task_id: str = "t1", worker: str = WORKER, credits: float = 5.0) -> None:
    """Set up an assignment + secret challenge the way accept_bid does, minus HTTP."""
    router._assignments[task_id] = TaskBid(
        task_id=task_id, bidder_node_id=worker, bidder_endpoint="http://w",
        model_name="m", estimated_tokens=10, credits_requested=credits, signature="s",
    )
    router._minter.issue_task_challenge(task_id=task_id, issuer_id=POSTER, credits_offered=int(credits))


def _response(nonce: str, result: str) -> str:
    return hashlib.sha256(nonce.encode() + result.encode()).hexdigest()


def _rh(result: str) -> str:
    return hashlib.sha256(result.encode()).hexdigest()


# ── legitimate path is rewarded ──────────────────────────────────────────────

def test_legitimate_worker_earns_the_reward():
    r = _router()
    _assign(r, credits=5.0)
    commit = r.commit_result("t1", _rh("answer"), node_id=WORKER)
    assert commit["committed"] is True
    nonce = commit["challenge_nonce"]
    out = r.submit_result("t1", "answer", challenge_response=_response(nonce, "answer"), node_id=WORKER)
    assert out["verified"] is True
    assert out["credits_awarded"] == 5.0
    assert r.verify_result("t1") is True


# ── forgeries earn nothing ─────────────────────────────────────────────────────

def test_self_computed_hash_alone_earns_nothing():
    # The OLD forgery: attacker knows task_id+result+worker (all public) and submits
    # sha256 of them as the "proof" — without committing, without the nonce.
    r = _router()
    _assign(r)
    forged = hashlib.sha256(("t1" + "answer" + WORKER).encode()).hexdigest()
    out = r.submit_result("t1", "answer", challenge_response=forged, node_id=WORKER)
    assert out["verified"] is False
    assert out["credits_awarded"] == 0.0


def test_missing_challenge_response_earns_nothing():
    r = _router()
    _assign(r)
    r.commit_result("t1", _rh("answer"), node_id=WORKER)
    out = r.submit_result("t1", "answer", challenge_response=None, node_id=WORKER)
    assert out["verified"] is False
    assert out["credits_awarded"] == 0.0
    assert out["reason"] == "missing_challenge_response"


def test_no_challenge_issued_earns_nothing():
    r = _router()  # no _assign -> no challenge for the task
    out = r.submit_result("t1", "answer", challenge_response="whatever", node_id=WORKER)
    assert out["verified"] is False
    assert out["credits_awarded"] == 0.0
    assert out["reason"] == "no_challenge"


def test_result_swap_after_commit_is_rejected():
    # Commit to result A, get the nonce, then try to be paid for a different result B.
    r = _router()
    _assign(r)
    commit = r.commit_result("t1", _rh("answerA"), node_id=WORKER)
    nonce = commit["challenge_nonce"]
    out = r.submit_result("t1", "answerB", challenge_response=_response(nonce, "answerB"), node_id=WORKER)
    assert out["verified"] is False
    assert out["reason"] == "result_differs_from_commitment"
    assert out["credits_awarded"] == 0.0


def test_unassigned_worker_cannot_commit():
    r = _router()
    _assign(r, worker=WORKER)
    commit = r.commit_result("t1", _rh("x"), node_id="attacker-node")
    assert commit["committed"] is False
    assert commit["reason"] == "not_the_assigned_worker"


def test_commitment_is_one_shot_no_regrind():
    r = _router()
    _assign(r)
    assert r.commit_result("t1", _rh("answerA"), node_id=WORKER)["committed"] is True
    # Having seen the nonce, the worker cannot re-commit a different hash.
    again = r.commit_result("t1", _rh("answerB"), node_id=WORKER)
    assert again["committed"] is False
    assert again["reason"] == "already_committed"


def test_replay_earns_reward_only_once():
    r = _router()
    _assign(r)
    nonce = r.commit_result("t1", _rh("answer"), node_id=WORKER)["challenge_nonce"]
    resp = _response(nonce, "answer")
    first = r.submit_result("t1", "answer", challenge_response=resp, node_id=WORKER)
    second = r.submit_result("t1", "answer", challenge_response=resp, node_id=WORKER)
    assert first["verified"] is True and first["credits_awarded"] == 5.0
    assert second["verified"] is False           # replay of the same proof
    assert second["credits_awarded"] == 0.0


# ── integration: accept_bid issues the challenge ──────────────────────────────

def test_accept_bid_issues_a_challenge():
    _reset_mesh_ledger()
    _fund_poster(100.0)  # the escrow hold needs funds — accept_bid is now fail-closed
    r = _router()
    bid = TaskBid(task_id="t1", bidder_node_id=WORKER, bidder_endpoint="http://w",
                  model_name="m", estimated_tokens=10, credits_requested=3.0, signature="s")
    with mock.patch("requests.post"):  # don't actually notify the peer
        out = r.accept_bid(bid)
    assert out["assigned"] is True
    assert out["credits_escrowed"] == 3.0  # the hold was actually placed
    assert out["challenge_hash"]  # a secret challenge was issued for the task
    # and the worker can now commit + get a nonce
    assert r.commit_result("t1", _rh("z"), node_id=WORKER)["committed"] is True


def test_accept_bid_fails_closed_when_escrow_cannot_be_funded():
    # The escrow-import bug used to swallow the failure and assign anyway (fail-open).
    _reset_mesh_ledger()  # poster has zero mesh credits
    r = _router()
    bid = TaskBid(task_id="t1", bidder_node_id=WORKER, bidder_endpoint="http://w",
                  model_name="m", estimated_tokens=10, credits_requested=3.0, signature="s")
    with mock.patch("requests.post") as post:
        out = r.accept_bid(bid)
    assert out["assigned"] is False
    assert out["reason"] == "escrow_failed"
    assert "t1" not in r._assignments          # no assignment recorded
    assert out.get("challenge_hash") is None   # no challenge issued
    post.assert_not_called()                    # winner never notified


def test_solicit_bid_rejects_an_unsigned_or_forged_bid():
    # Bids enter through _solicit_bid; an unsigned/forged/MITM-rewritten bid must
    # be dropped before it can be selected or escrowed.
    r = _router()
    me = signer.get_local_peer_id()  # sign as the bidder so the local key matches bidder_node_id

    def _payload(sig: str, **override) -> dict:
        d = {
            "task_id": "t1", "bidder_node_id": me, "bidder_endpoint": "http://w",
            "model_name": "m", "estimated_tokens": 10, "credits_requested": 2.0, "signature": sig,
        }
        d.update(override)
        return d

    canonical = TaskBid(
        task_id="t1", bidder_node_id=me, bidder_endpoint="http://w",
        model_name="m", estimated_tokens=10, credits_requested=2.0, signature="",
    ).canonical_payload()
    good_sig = signer.sign(canonical.encode("utf-8"))
    offer = {"task_id": "t1"}
    peer = {"endpoint": "http://peer"}

    # Correctly signed bid -> accepted.
    with mock.patch("requests.post", return_value=_FakeResp(_payload(good_sig))):
        assert r._solicit_bid(peer, offer) is not None
    # Tampered credits (signature no longer matches the payload) -> dropped.
    with mock.patch("requests.post", return_value=_FakeResp(_payload(good_sig, credits_requested=0.0))):
        assert r._solicit_bid(peer, offer) is None
    # Junk signature -> dropped.
    with mock.patch("requests.post", return_value=_FakeResp(_payload("garbage"))):
        assert r._solicit_bid(peer, offer) is None
