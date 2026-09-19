"""M-P5/C4 — the denial step completes only from a REAL, durable pre-dispatch refusal.

The genuine served refusal at this SHA (census §7): the deterministic live-data lane
ATTEMPTS the retrieval without any model, and under the Local Only composite every
outbound door refuses pre-dispatch — the attempt is durably recorded as an
attempted-and-contained action, bound to the turn, with zero workspace effect.
"""
from __future__ import annotations

import pytest

from core import policy_engine
from tests.first_run_pact_rig import pact_rig

DENIAL_PROMPT = "What is the weather in Kaunas?"


def _claim_denial(pact_rig, request_id: str):
    snap = pact_rig.pact()
    return pact_rig.post("/api/onboarding/pact/denial/claim", {
        "session_id": pact_rig.canonical_session(), "request_id": request_id,
        "expect_revision": snap["revision"],
    })


def _walk_into_denial_demo(pact_rig) -> None:
    pact_rig.walk_to("local_task")
    request_id, _frames, _commit = pact_rig.run_task_turn(
        "Create welcome-notes.txt containing Welcome to VOOL."
    )
    snap = pact_rig.pact()
    status, payload = pact_rig.post("/api/onboarding/pact/task/claim", {
        "session_id": pact_rig.canonical_session(), "request_id": request_id,
        "expect_revision": snap["revision"],
    })
    assert status == 200, payload
    snap = pact_rig.pact()
    status, payload = pact_rig.post("/api/onboarding/pact/advance", {"to": "denial_demo", "expect_revision": snap["revision"]})
    assert status == 200, payload


def test_a_genuine_contained_attempt_completes_the_denial_step_with_zero_effect(pact_rig):
    # The composite ON is what makes the demo's refusal deterministic.
    snap = pact_rig.pact()
    status, payload = pact_rig.post("/api/onboarding/pact/boundary", {
        "key": "local_only_composite", "value": True, "expect_revision": snap["revision"],
    })
    assert status == 200 and payload["live"]["local_only_mode"] is True

    _walk_into_denial_demo(pact_rig)

    from core.memory.files import conversation_log_path, load_jsonl

    before_tree = sorted((p.relative_to(pact_rig.workspace), p.read_bytes()) for p in pact_rig.workspace.rglob("*") if p.is_file())
    status, frames = pact_rig.post("/api/chat", {
        "model": "vool-local-only", "stream": True, "stream_task_events": True,
        "session_id": pact_rig.canonical_session(), "turn_id": "pact-denial-turn",
        "messages": [{"role": "user", "content": DENIAL_PROMPT}],
    })
    commit = next((f["vool_response_commit"] for f in frames if isinstance(f, dict) and f.get("vool_response_commit")), None)
    assert commit and commit.get("status") == "answer_present"
    answer = "".join(f.get("message", {}).get("content", "") for f in frames if isinstance(f, dict))
    assert "disabled" in answer.lower() or "can't" in answer.lower()  # the honest refusal answer

    after_tree = sorted((p.relative_to(pact_rig.workspace), p.read_bytes()) for p in pact_rig.workspace.rglob("*") if p.is_file())
    assert before_tree == after_tree, "the denied demo must not touch the workspace"

    status, payload = _claim_denial(pact_rig, commit["request_id"])
    assert status == 200, payload
    assert payload["denial"]["decision"] == "denied"
    assert payload["denial"]["verified"] is True
    assert payload["proof_state"] in {"VERIFIED", "RECORDED"}
    snap = pact_rig.pact()
    assert snap["state"] == "memory_done"
    assert snap["steps"]["denial"] == "done"


def test_a_turn_where_nothing_was_attempted_never_completes_the_denial(pact_rig):
    """The honesty law: no attempt ⇒ no denial, retry or skip (spec §10.3)."""
    snap = pact_rig.pact()
    pact_rig.post("/api/onboarding/pact/boundary", {"key": "local_only_composite", "value": True, "expect_revision": snap["revision"]})
    _walk_into_denial_demo(pact_rig)

    status, frames = pact_rig.post("/api/chat", {
        "model": "vool-local-only", "stream": True, "stream_task_events": True,
        "session_id": pact_rig.canonical_session(), "turn_id": "pact-math-turn",
        "messages": [{"role": "user", "content": "what is 2 plus 2?"}],
    })
    commit = next((f["vool_response_commit"] for f in frames if isinstance(f, dict) and f.get("vool_response_commit")), None)
    assert commit

    status, payload = _claim_denial(pact_rig, commit["request_id"])
    assert status == 409
    assert payload["error"] == "evidence_missing"
    snap = pact_rig.pact()
    assert snap["state"] == "denial_demo"  # the step stays honestly pending


def test_denial_claim_requires_the_denial_demo_state(pact_rig):
    status, payload = _claim_denial(pact_rig, "req:http:auto-whatever")
    assert status == 409
    assert payload["error"] == "invalid_transition"


def test_the_denied_turns_receipt_is_signed_and_the_chain_intact(pact_rig):
    snap = pact_rig.pact()
    pact_rig.post("/api/onboarding/pact/boundary", {"key": "local_only_composite", "value": True, "expect_revision": snap["revision"]})
    _walk_into_denial_demo(pact_rig)
    status, frames = pact_rig.post("/api/chat", {
        "model": "vool-local-only", "stream": True, "stream_task_events": True,
        "session_id": pact_rig.canonical_session(), "turn_id": "pact-denial-turn-2",
        "messages": [{"role": "user", "content": DENIAL_PROMPT}],
    })
    commit = next((f["vool_response_commit"] for f in frames if isinstance(f, dict) and f.get("vool_response_commit")), None)
    status, payload = _claim_denial(pact_rig, commit["request_id"])
    assert status == 200
    # Proof Chip linkage: the SAME (session, request) serves the chip the page renders.
    status, proof = pact_rig.get(f"/api/chat/proof?session={pact_rig.canonical_session()}&request_id={commit['request_id']}")
    assert status == 200
    assert proof["bound"] is True
    assert proof["state"] in {"VERIFIED", "RECORDED"}
    # ... and the receipts endpoint returns the signed receipt with a server-verified chain.
    status, receipts = pact_rig.get(f"/api/runtime/receipts?session={pact_rig.canonical_session()}")
    assert status == 200 and receipts["receipts"]
    assert receipts["chain_verified"] is True
