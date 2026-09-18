"""The turn-pipeline hook: every finalized turn emits a signed, verifiable honesty receipt."""
from __future__ import annotations

import json

import pytest

from core.agent_runtime.action_honesty_validator import emit_turn_honesty_receipt
from core.honesty_receipt import (
    VERDICT_BLOCKED,
    VERDICT_CLEAN,
    VERDICT_NO_CLAIM,
    _ledger_path,
    list_honesty_receipts,
    main,
    verify_honesty_chain,
)


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    monkeypatch.setenv("VOOL_HONESTY_RECEIPTS", "1")
    yield


def test_backed_action_claim_emits_clean_receipt() -> None:
    out = emit_turn_honesty_receipt(
        {"response": "Done — I created the file hello.txt.", "mode": "tool_executed"},
        user_input="create hello.txt on my Desktop",
        session_id="s-clean",
        source_context={"tool_receipts": [
            {"tool_name": "workspace.write_file", "status": "executed", "receipt_id": "rk-1"},
        ]},
    )
    stub = out["honesty_receipt"]
    assert stub["verdict"] == VERDICT_CLEAN
    assert stub["signed"] is True and stub["signer_peer_id"]
    stored = list_honesty_receipts("s-clean")
    assert len(stored) == 1
    assert stored[0]["executed_tools"][0]["tool"] == "workspace.write_file"
    ok, reason = verify_honesty_chain(stored)
    assert ok, reason


def test_blocked_false_claim_emits_blocked_receipt() -> None:
    out = emit_turn_honesty_receipt(
        {"response": "I deleted the files.", "action_honesty_validator": {"applied": True}},
        user_input="delete everything in my home dir",
        session_id="s-lie",
        source_context={},
    )
    assert out["honesty_receipt"]["verdict"] == VERDICT_BLOCKED
    stored = list_honesty_receipts("s-lie")
    assert stored[0]["verdict"] == VERDICT_BLOCKED
    assert stored[0]["executed_tools"] == []  # ground truth: nothing ran


def test_plain_turn_emits_no_claim_receipt() -> None:
    out = emit_turn_honesty_receipt(
        {"response": "An event loop processes tasks from a queue one at a time."},
        user_input="explain event loops",
        session_id="s-plain",
        source_context={},
    )
    assert out["honesty_receipt"]["verdict"] == VERDICT_NO_CLAIM


def test_env_off_switch_disables_emission(monkeypatch) -> None:
    monkeypatch.setenv("VOOL_HONESTY_RECEIPTS", "0")
    out = emit_turn_honesty_receipt(
        {"response": "I deleted the files."}, session_id="s-off", source_context={}
    )
    assert "honesty_receipt" not in out
    assert list_honesty_receipts("s-off") == []


def test_emit_never_raises_on_bad_input() -> None:
    # Best-effort contract: emission must never break a turn.
    assert emit_turn_honesty_receipt(None) is not None  # type: ignore[arg-type]
    assert emit_turn_honesty_receipt({"response": None}, session_id=None) is not None


def test_verify_cli_passes_intact_ledger_and_fails_on_tamper(tmp_path) -> None:
    for i in range(3):
        emit_turn_honesty_receipt({"response": f"plain turn {i}"}, session_id="cli", source_context={})
    ledger = _ledger_path("cli")
    assert main(["verify", str(ledger)]) == 0

    receipts = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()]
    receipts[1]["verdict"] = VERDICT_CLEAN  # flip a recorded verdict
    tampered = tmp_path / "tampered.json"
    tampered.write_text(json.dumps(receipts), encoding="utf-8")
    assert main(["verify", str(tampered)]) == 1
