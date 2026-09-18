from __future__ import annotations

import sqlite3

from core.runtime_continuity import (
    create_runtime_checkpoint,
    finalize_runtime_checkpoint,
    latest_failed_checkpoint,
)
from core.runtime_task_outcome import (
    FulfillmentStatus,
    fulfillment_outcome_from_source_context,
    normalize_runtime_task_outcome,
    output_validation_outcome,
)
from storage.migrations import run_migrations


def test_completed_transport_can_persist_failed_fulfillment_and_remain_retryable() -> None:
    checkpoint = create_runtime_checkpoint(
        session_id="outcome:validation",
        request_text="Answer A, B, and C.",
        source_context={"runtime_session_id": "outcome:validation"},
        task_id="task-original",
    )

    finalized = finalize_runtime_checkpoint(
        checkpoint["checkpoint_id"],
        status="completed",
        final_response="I couldn't produce a normal response.",
        outcome={
            "fulfillment_status": "failed",
            "failure_stage": "output_validation",
            "failure_codes": ["missing_requested_parts"],
            "retryable": True,
        },
    )

    assert finalized is not None
    assert finalized["status"] == "completed"
    assert finalized["outcome"]["fulfillment_status"] == "failed"
    assert finalized["outcome"]["failure_stage"] == "output_validation"
    assert finalized["outcome"]["origin_task_id"] == "task-original"
    assert finalized["failure_text"] == "output_validation: missing_requested_parts"
    retry_target = latest_failed_checkpoint("outcome:validation")
    assert retry_target is not None
    assert retry_target["checkpoint_id"] == checkpoint["checkpoint_id"]


def test_outcome_contract_accepts_every_declared_fulfillment_state() -> None:
    for status in FulfillmentStatus:
        outcome = normalize_runtime_task_outcome(
            {
                "fulfillment_status": status.value,
                "failure_stage": "execution" if status != FulfillmentStatus.FULFILLED else "",
                "failure_codes": ["fixture"] if status != FulfillmentStatus.FULFILLED else [],
                "retryable": status in {
                    FulfillmentStatus.PARTIALLY_FULFILLED,
                    FulfillmentStatus.FAILED,
                },
            }
        )
        assert outcome.fulfillment_status == status


def test_retry_lookup_never_skips_a_newer_fulfilled_turn_for_an_older_failure() -> None:
    old = create_runtime_checkpoint(
        session_id="outcome:latest-only",
        request_text="old failed task",
        source_context={},
    )
    finalize_runtime_checkpoint(
        old["checkpoint_id"],
        status="failed",
        failure_text="provider failed",
    )
    newest = create_runtime_checkpoint(
        session_id="outcome:latest-only",
        request_text="new successful task",
        source_context={},
    )
    finalize_runtime_checkpoint(
        newest["checkpoint_id"],
        status="completed",
        final_response="done",
    )

    assert latest_failed_checkpoint("outcome:latest-only") is None


def test_output_validation_fallback_has_typed_failure_evidence() -> None:
    outcome = output_validation_outcome(
        {
            "fallback_applied": True,
            "ordinary_chat_output": {
                "allowed": False,
                "reasons": ["missing_requested_parts"],
            },
        }
    )

    assert outcome == {
        "fulfillment_status": "failed",
        "failure_stage": "output_validation",
        "failure_codes": ["missing_requested_parts"],
        "retryable": True,
    }


def test_incomplete_but_usable_answer_is_partially_fulfilled() -> None:
    outcome = output_validation_outcome(
        {
            "final_ui": {
                "fallback_applied": True,
                "answer_completeness": {
                    "incomplete": True,
                    "has_content": True,
                    "degenerate": False,
                    "reasons": ["dangling_tail"],
                },
            }
        }
    )

    assert outcome["fulfillment_status"] == "partially_fulfilled"
    assert outcome["failure_codes"] == ["answer_completeness:dangling_tail"]
    assert outcome["retryable"] is True


def test_terminal_constraint_fallback_is_failed_even_without_router_control() -> None:
    outcome = fulfillment_outcome_from_source_context(
        {
            "response_constraint_final": {
                "compliant": False,
                "fallback_applied": True,
                "violations": ["incomplete_fragment"],
            }
        }
    )

    assert outcome["fulfillment_status"] == "failed"
    assert outcome["failure_codes"] == ["response_constraint:incomplete_fragment"]


def test_migration_adds_outcome_column_to_legacy_checkpoint_table(tmp_path) -> None:
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE runtime_checkpoints (
            checkpoint_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            task_id TEXT NOT NULL DEFAULT '',
            task_class TEXT NOT NULL DEFAULT '',
            request_text TEXT NOT NULL,
            source_context_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'running',
            step_count INTEGER NOT NULL DEFAULT 0,
            last_tool_name TEXT NOT NULL DEFAULT '',
            pending_intent_json TEXT NOT NULL DEFAULT '{}',
            state_json TEXT NOT NULL DEFAULT '{}',
            final_response TEXT NOT NULL DEFAULT '',
            failure_text TEXT NOT NULL DEFAULT '',
            resume_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT,
            resumed_from_checkpoint_id TEXT
        )
        """
    )
    conn.commit()
    conn.close()

    run_migrations(db_path=db_path)

    conn = sqlite3.connect(db_path)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(runtime_checkpoints)")}
    finally:
        conn.close()
    assert "outcome_json" in columns
