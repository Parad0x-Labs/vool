from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace
from typing import Any

from core.agent_runtime import orchestrator, response_policy_tool_history
from core.agent_runtime.builder import support as builder_support
from core.runtime_continuity import load_tool_receipt, store_tool_receipt
from core.runtime_task_events import (
    configure_runtime_event_store,
    emit_runtime_event,
    list_runtime_session_events,
    reset_runtime_event_state,
)
from storage.migrations import run_migrations


class _RuntimeAgent:
    @staticmethod
    def _runtime_checkpoint_id(source_context: dict[str, Any] | None) -> str:
        return str((source_context or {}).get("checkpoint_id") or "")


class _HistoryAgent:
    @staticmethod
    def _tool_history_observation_prompt(observation: dict[str, Any]) -> str:
        return orchestrator.tool_history_observation_prompt(observation)


def test_terminal_tool_event_persists_complete_redacted_action_record(tmp_path) -> None:
    db_path = tmp_path / "runtime-events.db"
    run_migrations(db_path=db_path)
    configure_runtime_event_store(str(db_path))
    reset_runtime_event_state()
    secret = "[redacted-openrouter-prefix]" + ("a" * 64)
    context = {
        "runtime_session_id": "runtime-session-1",
        "chat_id": "chat-1",
        "_trusted_project_id": "project-1",
        "checkpoint_id": "checkpoint-1",
        "turn_id": "turn-1",
        "actor_id": "kas-local",
    }
    try:
        record = orchestrator.emit_runtime_event(
            _RuntimeAgent(),
            context,
            event_type="tool_executed",
            message=f"Wrote the file with api_key={secret}",
            emit_runtime_event_fn=emit_runtime_event,
            tool_name="workspace.write_file",
            status="executed",
            mode="tool_executed",
            ok=True,
            summary=f"Saved settings with api_key={secret}",
            tool_call_id="call-1",
            arguments={
                "path": "C:/work/settings.json",
                "api_key": secret,
                "callback_url": "https://example.test/callback?code=secret-code&state=secret-state",
                "nested": {"authorization": "Bearer secret-token"},
            },
            approval={
                "approval_id": "approval-1",
                "approval_state": "approved",
                "approved_by": "owner_local",
            },
        )

        assert record is not None
        events = list_runtime_session_events("runtime-session-1", after_seq=0, limit=10)
        assert len(events) == 1
        persisted = events[0]["action_record"]
        serialized = json.dumps(events[0], sort_keys=True)
        assert secret not in serialized
        assert "secret-code" not in serialized
        assert "secret-state" not in serialized
        assert "secret-token" not in serialized
        assert persisted["schema"] == "tool_action_receipt_v1"
        assert persisted["action_type"] == "tool_executed"
        assert persisted["actor"] == "kas-local"
        assert persisted["target"] == "C:/work/settings.json"
        assert persisted["origin"] == {"chat_id": "chat-1", "project_id": "project-1"}
        assert persisted["parameters"]["api_key"] == "[redacted]"
        assert persisted["parameters"]["nested"]["authorization"] == "[redacted]"
        assert persisted["parameters"]["callback_url"].endswith("code=%5Bredacted%5D&state=%5Bredacted%5D")
        assert persisted["result"]["outcome"] == "succeeded"
        assert persisted["result"]["ok"] is True
        assert persisted["failure"] == {"failed": False}
        assert persisted["identifiers"] == {
            "checkpoint_id": "checkpoint-1",
            "turn_id": "turn-1",
            "tool_call_id": "call-1",
        }
        assert persisted["approval"] == {
            "approval_id": "approval-1",
            "approval_state": "approved",
            "approved_by": "owner_local",
        }
        assert persisted["receipt_id"].startswith("tool-receipt-")
        assert persisted["action_id"].startswith("tool-action-")
        assert len(persisted["parameters_hash"]) == 64
        assert len(persisted["result_hash"]) == 64
        assert len(persisted["record_hash"]) == 64
        datetime.fromisoformat(persisted["occurred_at"])
        assert events[0]["receipt_id"] == persisted["receipt_id"]
        ledger_receipt = load_tool_receipt(persisted["receipt_id"])
        assert ledger_receipt is not None
        assert ledger_receipt["execution"]["action_record"] == persisted
        assert ledger_receipt["arguments"] == persisted["parameters"]
        assert events[0]["created_at"]
    finally:
        reset_runtime_event_state()
        configure_runtime_event_store(None)


def test_action_identifiers_and_hashes_are_stable_for_the_same_tool_call() -> None:
    context = {
        "chat_id": "chat-stable",
        "project_id": "project-stable",
        "checkpoint_id": "checkpoint-stable",
        "turn_id": "turn-stable",
    }
    details = {
        "tool_name": "workspace.search_text",
        "status": "executed",
        "mode": "tool_executed",
        "ok": True,
        "summary": "Found 3 matches.",
        "tool_call_id": "call-stable",
        "arguments": {"query": "needle", "path": "C:/work"},
    }

    first = orchestrator.build_tool_action_record(
        context,
        event_type="tool_executed",
        message="Found 3 matches.",
        details=details,
    )
    second = orchestrator.build_tool_action_record(
        context,
        event_type="tool_executed",
        message="Found 3 matches.",
        details=details,
    )

    assert first is not None and second is not None
    assert first["receipt_id"] == second["receipt_id"]
    assert first["action_id"] == second["action_id"]
    assert first["parameters_hash"] == second["parameters_hash"]
    assert first["result_hash"] == second["result_hash"]
    assert first["record_hash"] == second["record_hash"]


def test_nonterminal_tool_events_are_redacted_at_the_shared_boundary() -> None:
    captured: dict[str, Any] = {}
    secret = "[redacted-openrouter-prefix]" + ("c" * 64)

    def _capture(
        source_context: dict[str, Any] | None,
        *,
        event_type: str,
        message: str,
        details: dict[str, Any],
    ) -> None:
        captured.update(
            {
                "source_context": source_context,
                "event_type": event_type,
                "message": message,
                "details": details,
            }
        )

    record = orchestrator.emit_runtime_event(
        _RuntimeAgent(),
        {"chat_id": "chat-selected"},
        event_type="tool_selected",
        message=f"Selected a tool with api_key={secret}",
        emit_runtime_event_fn=_capture,
        tool_name="web.fetch",
        arguments={"api_key": secret},
    )

    assert record is None
    assert secret not in json.dumps(captured, sort_keys=True)
    assert captured["details"]["arguments"] == {"api_key": "[redacted]"}
    assert "action_record" not in captured["details"]


def test_failure_and_pending_approval_records_are_truthful() -> None:
    failed = orchestrator.build_tool_action_record(
        {"chat_id": "chat-failed"},
        event_type="tool_failed",
        message="Unknown argument.",
        details={
            "tool_name": "machine.list_directory",
            "status": "invalid_arguments",
            "mode": "tool_failed",
            "ok": False,
            "arguments": {"directory": "Downloads"},
        },
    )
    pending = orchestrator.build_tool_action_record(
        {"chat_id": "chat-preview"},
        event_type="tool_preview",
        message="Approval required.",
        details={
            "tool_name": "workspace.write_file",
            "status": "user_action_required",
            "mode": "tool_preview",
            "ok": False,
            "arguments": {"path": "C:/work/report.md"},
        },
    )

    assert failed is not None
    assert failed["result"]["outcome"] == "failed"
    assert failed["failure"] == {"failed": True, "reason": "invalid_arguments"}
    assert pending is not None
    assert pending["result"]["outcome"] == "pending_approval"
    assert pending["approval"] == {"approval_required": True, "approval_state": "pending"}


def test_conversation_history_receives_only_receipt_reference_and_safe_summary() -> None:
    secret = "[redacted-openrouter-prefix]" + ("b" * 64)
    execution = SimpleNamespace(
        details={
            "observation": {
                "schema": "tool_observation_v1",
                "intent": "web.fetch",
                "raw_arguments": {"api_key": secret},
                "response_body": "private response body",
            }
        },
        response_text=f"Fetched the endpoint with api_key={secret}",
        ok=True,
        status="executed",
        mode="tool_executed",
        tool_name="web.fetch",
    )

    updated = response_policy_tool_history.append_tool_result_to_source_context(
        _HistoryAgent(),
        {"conversation_history": []},
        execution=execution,
        tool_name="web.fetch",
        receipt={"receipt_id": "tool-receipt-safe-1", "parameters": {"api_key": secret}},
    )

    history = list(updated["conversation_history"])
    assert len(history) == 1
    content = history[0]["content"]
    payload = json.loads(content.split("\n", 1)[1])
    assert payload == {
        "receipt_id": "tool-receipt-safe-1",
        "safe_summary": "web.fetch: Fetched the endpoint with api_key: [redacted]",
    }
    assert secret not in content
    assert "raw_arguments" not in content
    assert "private response body" not in content
    assert "parameters" not in content


def test_builder_step_record_never_retains_raw_secret_arguments_or_output() -> None:
    secret = "[redacted-openrouter-prefix]" + ("d" * 64)
    execution = SimpleNamespace(
        details={
            "observation": {"authorization": f"Bearer {secret}"},
            "artifacts": [{"path": "C:/work/output.txt", "api_key": secret}],
        },
        response_text=f"Completed with api_key={secret}",
        ok=True,
        status="executed",
        mode="tool_executed",
        tool_name="workspace.write_file",
    )
    agent = SimpleNamespace(
        _tool_step_summary=lambda text, fallback: str(text or fallback),
    )

    step = builder_support.controller_step_record(
        agent,
        execution=execution,
        tool_payload={
            "intent": "workspace.write_file",
            "arguments": {"path": "C:/work/output.txt", "api_key": secret},
        },
    )

    serialized = json.dumps(step, sort_keys=True)
    assert secret not in serialized
    assert step["arguments"]["api_key"] == "[redacted]"
    assert step["observation"]["authorization"] == "[redacted]"
    assert step["artifacts"][0]["api_key"] == "[redacted]"


def test_existing_tool_receipt_ledger_redacts_all_persisted_fields(
    tmp_path,
) -> None:
    db_path = tmp_path / "tool-receipts.db"
    run_migrations(db_path=db_path)
    configure_runtime_event_store(str(db_path))
    secret = "[redacted-openrouter-prefix]" + ("e" * 64)
    try:
        store_tool_receipt(
            receipt_key="receipt-redaction-test",
            session_id="chat-redaction-test",
            checkpoint_id="checkpoint-redaction-test",
            tool_name="web.fetch",
            idempotency_key="idempotency-redaction-test",
            arguments={
                "api_key": secret,
                "url": (
                    "https://example.test/callback"
                    "?code=private-code&state=private-state"
                ),
            },
            execution={
                "details": {
                    "authorization": f"Bearer {secret}",
                },
                "response_text": f"api_key={secret}",
            },
        )

        loaded = load_tool_receipt("receipt-redaction-test")
        assert loaded is not None
        serialized = json.dumps(loaded, sort_keys=True)
        assert secret not in serialized
        assert "private-code" not in serialized
        assert "private-state" not in serialized
        assert loaded["arguments"]["api_key"] == "[redacted]"
        assert (
            loaded["execution"]["details"]["authorization"]
            == "[redacted]"
        )
    finally:
        reset_runtime_event_state()
        configure_runtime_event_store(None)
