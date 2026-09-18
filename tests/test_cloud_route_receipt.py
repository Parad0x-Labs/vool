from __future__ import annotations

from core.cloud_route_receipt import (
    issue_cloud_route_receipt,
    list_cloud_route_receipts,
    record_cloud_route_receipt,
    verify_cloud_route_receipt,
)


def _receipt(**overrides):
    payload = {
        "attempt_id": "attempt",
        "phase": "started",
        "session_id": "session",
        "task_id": "task",
        "turn_id": "turn",
        "subtask_id": "sub",
        "model_call_id": "call",
        "provider_id": "provider",
        "model_id": "model",
        "pricing_state": "free",
        "estimated_max_usd": 0.0,
        "privacy_class": "public",
        "policy_decision": "local+free",
        "route_reason": "verified_zero",
    }
    payload.update(overrides)
    return issue_cloud_route_receipt(**payload)


def test_cloud_route_receipt_is_signed_and_tamper_evident(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("core.cloud_route_receipt._receipt_root", lambda: tmp_path)
    receipt = _receipt()
    assert verify_cloud_route_receipt(receipt) == (True, "ok")
    record_cloud_route_receipt(receipt)
    stored = list_cloud_route_receipts("session")
    assert len(stored) == 1
    stored[0]["model_id"] = "tampered"
    assert verify_cloud_route_receipt(stored[0])[0] is False


def test_receipt_never_stores_prompt_and_redacts_safe_error(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("core.cloud_route_receipt._receipt_root", lambda: tmp_path)
    secret = "test-only-redaction-value"
    receipt = _receipt(phase="completed", safe_error=f"api_key={secret}", success=False)
    record_cloud_route_receipt(receipt)
    payload = list_cloud_route_receipts("session")[0]
    assert secret not in str(payload)
    assert "prompt" not in payload


def test_receipt_chain_rejects_wrong_previous_hash(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("core.cloud_route_receipt._receipt_root", lambda: tmp_path)
    first = _receipt()
    record_cloud_route_receipt(first)
    second = _receipt(attempt_id="two", prev_hash="wrong")
    try:
        record_cloud_route_receipt(second)
    except ValueError as exc:
        assert "chain" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("broken receipt chain was accepted")
