"""The review/evidence projection: journal-backed rows, byte verification against current disk,
and the ``code.review_evidence`` intent served through the production door like any read."""
from __future__ import annotations

from pathlib import Path

from core.code_assistant.fixture import DEFECT_OLD_TEXT, FIX_NEW_TEXT, OWNER_PATH
from core.code_assistant.review import STATE_DRIFTED, STATE_VERIFIED, summarize_effects

from .conftest import door


def _land_fix(auto_context) -> tuple[str, str]:
    """Drive the fixture journey through the mutation; returns (task_id, blackbox turn id)."""
    from .conftest import drive_to_approved_proposal

    task_id, args = drive_to_approved_proposal(auto_context)
    mutate = door(
        "code.task.step",
        {"task_id": task_id, "step_id": "mutate", "intent": "workspace.replace_in_file", "arguments": args},
        auto_context,
    )
    assert mutate.ok, mutate.response_text
    turn_id = str(mutate.details["tool_result"]["blackbox"]["turn_id"])
    return task_id, turn_id


def test_review_intent_is_dispatched_by_the_runtime(auto_context):
    receipt = door("code.review_evidence", {}, auto_context)
    # No journal yet for this workspace: an honest not_found, not a fabricated summary.
    assert receipt.status == "not_found"
    assert receipt.details["executed"] is False


def test_review_summarizes_the_journaled_fix_with_verified_bytes(auto_context):
    _task_id, turn_id = _land_fix(auto_context)
    receipt = door("code.review_evidence", {"turn_id": turn_id}, auto_context)
    assert receipt.ok, receipt.response_text
    rows = receipt.details["evidence"]
    assert rows, receipt.details
    row = next(item for item in rows if item["path"] == OWNER_PATH)
    assert row["state"] == STATE_VERIFIED
    assert row["operation"], row
    assert row["after_sha256"] and row["after_sha256"] == row["current_sha256"]
    assert receipt.details["verified_count"] >= 1


def test_review_flags_drifted_bytes(auto_context):
    _task_id, turn_id = _land_fix(auto_context)
    Path(auto_context["workspace"], OWNER_PATH).write_text("tampered\n", encoding="utf-8")
    summary = summarize_effects(turn_id, workspace_root=Path(auto_context["workspace"]))
    row = next(item for item in summary["effects"] if item["path"] == OWNER_PATH)
    assert row["state"] == STATE_DRIFTED
    assert summary["drifted_count"] >= 1


def test_review_missing_file_is_reported_missing(auto_context):
    _task_id, turn_id = _land_fix(auto_context)
    Path(auto_context["workspace"], OWNER_PATH).unlink()
    summary = summarize_effects(turn_id, workspace_root=Path(auto_context["workspace"]))
    row = next(item for item in summary["effects"] if item["path"] == OWNER_PATH)
    assert row["state"] == "MISSING"
