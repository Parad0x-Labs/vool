"""M-P5 — evidence-checked task completion (LP2/C5): the server, not the client, decides."""
from __future__ import annotations

import hashlib
import json

import pytest

from tests.first_run_pact_rig import pact_rig  # noqa: F401 — fixture

TASK_PROMPT = "Create welcome-notes.txt containing Welcome to VOOL."


def _claim(pact_rig, request_id: str):
    snap = pact_rig.pact()
    return pact_rig.post("/api/onboarding/pact/task/claim", {
        "session_id": pact_rig.canonical_session(), "request_id": request_id,
        "expect_revision": snap["revision"],
    })


def test_a_real_deterministic_task_turn_completes_from_its_signed_receipt(pact_rig):
    pact_rig.walk_to("local_task")
    request_id, _frames, commit = pact_rig.run_task_turn(TASK_PROMPT)
    assert commit and commit.get("status") == "answer_present"
    assert (pact_rig.workspace / "welcome-notes.txt").exists(), "the real file must exist"

    status, payload = _claim(pact_rig, request_id)
    assert status == 200, payload
    receipt = payload["receipt"]
    assert receipt["signed"] is True
    assert receipt["signature_verified"] is True
    assert receipt["chain_verified"] is True
    assert payload["proof_state"] in {"VERIFIED", "RECORDED"}
    snap = pact_rig.pact()
    assert snap["state"] == "task_receipt"
    assert snap["steps"]["task"] == "done"
    # The file's bytes are the literal content: turn_model_calls == 0 by construction.
    content = (pact_rig.workspace / "welcome-notes.txt").read_text()
    assert "Welcome to VOOL" in content


def test_a_fabricated_request_id_is_refused_with_the_typed_fault(pact_rig):
    pact_rig.walk_to("local_task")
    status, payload = _claim(pact_rig, "req:http:auto-fabricated0000000000000000")
    assert status == 409
    assert payload["error"] in {"evidence_missing", "evidence_session_unknown"}


def test_another_sessions_request_id_is_refused(pact_rig):
    pact_rig.walk_to("local_task")
    request_id, _frames, _commit = pact_rig.run_task_turn(TASK_PROMPT)
    snap = pact_rig.pact()
    status, payload = pact_rig.post("/api/onboarding/pact/task/claim", {
        "session_id": "openclaw:" + "ffffffffffffffffffff",  # a different session
        "request_id": request_id,
        "expect_revision": snap["revision"],
    })
    assert status == 409
    assert payload["error"] in {"evidence_missing", "evidence_session_unknown", "evidence_unverifiable"}


def test_a_tampered_receipt_is_refused_even_though_it_exists(pact_rig):
    """S-P18: locate succeeds, but cryptographic verification must fail."""
    pact_rig.walk_to("local_task")
    request_id, _frames, _commit = pact_rig.run_task_turn(TASK_PROMPT)
    from core.honesty_receipt import _ledger_dir, list_honesty_receipts

    ledger = _ledger_dir() / (_ledger_dir().name and "")
    receipts = list_honesty_receipts(pact_rig.canonical_session())
    assert receipts, "the turn must have produced a receipt"
    # Tamper the LAST receipt in the ledger file: flip a byte inside its signed content.
    from core.runtime_paths import active_data_dir
    import hashlib

    ledger_dir = active_data_dir() / "honesty_receipts"
    ledger_file = ledger_dir / (hashlib.sha256(pact_rig.canonical_session().encode()).hexdigest()[:24] + ".jsonl")
    lines = ledger_file.read_text().splitlines()
    target = json.loads(lines[-1])
    target["response_hash"] = "hmac:deadbeef"
    lines[-1] = json.dumps(target, sort_keys=True)
    ledger_file.write_text("\n".join(lines) + "\n")

    status, payload = _claim(pact_rig, request_id)
    assert status == 409
    assert payload["error"] in {"evidence_unverifiable", "evidence_missing"}


def test_claim_is_reachable_after_a_restart_because_evidence_is_durable(pact_rig, capsys):
    pact_rig.walk_to("local_task")
    request_id, _frames, _commit = pact_rig.run_task_turn(TASK_PROMPT)
    # Simulate a restart: rebuild runtime services over the SAME home (fresh caches).
    import storage.db as sdb
    from apps.vool_api_server import _bootstrap
    from core.runtime_paths import configure_runtime_home

    configure_runtime_home(pact_rig.home)
    sdb.configure_default_db_path(pact_rig.home / "data" / "vool_web0_v2.db")
    pact_rig.runtime = _bootstrap(run_prewarm=False)

    status, payload = _claim(pact_rig, request_id)
    assert status == 200, payload
    assert payload["receipt"]["chain_verified"] is True


def test_a_broken_chain_is_refused_even_when_the_last_receipt_verifies_alone(pact_rig):
    """S-P18 pin: the CHAIN gate is independent. Flip the LAST receipt's prev_hash — the
    single-receipt verify passes (content and signature intact), the chain must not."""
    pact_rig.walk_to("local_task")
    request_id, _frames, _commit = pact_rig.run_task_turn("Create welcome-notes.txt containing Welcome to VOOL.")
    from core.runtime_paths import active_data_dir

    ledger_file = active_data_dir() / "honesty_receipts" / (hashlib.sha256(pact_rig.canonical_session().encode()).hexdigest()[:24] + ".jsonl")
    lines = ledger_file.read_text().splitlines()
    target = json.loads(lines[-1])
    target["prev_hash"] = "sha256:" + "f" * 64
    lines[-1] = json.dumps(target, sort_keys=True)
    ledger_file.write_text("\n".join(lines) + "\n")

    status, payload = _claim(pact_rig, request_id)
    assert status == 409
    assert payload["error"] == "evidence_unverifiable"
