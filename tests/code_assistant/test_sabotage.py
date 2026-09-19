"""Sabotage proofs for the coding assistant, driven through the ONE production door: permission,
confinement, receipt honesty, direct-execution refusal, tamper detection, rollback. Each sabotage
must FAIL CLOSED with bytes on disk unchanged."""
from __future__ import annotations

from pathlib import Path

from core.code_assistant.fixture import (
    DEFECT_OLD_TEXT,
    DEFECT_STATS_PY,
    FIX_NEW_TEXT,
    NARROW_TEST_COMMAND,
    OWNER_PATH,
)
from core.code_assistant.review import summarize_effects

from .conftest import SESSION, door, drive_to_approved_proposal

# ---------------------------------------------------------------- permission


def test_sabotage_manual_mode_blocks_the_fix_and_bytes_survive(auto_context, workspace):
    """Manual mode: the mode matrix gates the step itself at the executor funnel -- the same
    funnel a served model crosses -- so an approved proposal still cannot write without the
    mode's authority."""
    from core.mode_permission_policy import set_active_mode
    from core.tool_intent_executor import execute_tool_intent
    from tests._toolchain_fixtures import executor_kwargs

    set_active_mode(SESSION, "manual")
    task_id, args = drive_to_approved_proposal(auto_context)
    mutate = execute_tool_intent(
        {
            "intent": "code.task.step",
            "arguments": {
                "task_id": task_id,
                "step_id": "mutate",
                "intent": "workspace.replace_in_file",
                "arguments": args,
            },
        },
        **executor_kwargs(
            SESSION,
            workspace=str(workspace),
            workspace_root=str(workspace),
            operating_mode="manual",
            surface="api",
        ),
    )
    assert mutate.ok is False and mutate.details["executed"] is False, (mutate.status, mutate.response_text)
    assert (workspace / OWNER_PATH).read_text() == DEFECT_STATS_PY


def test_sabotage_arguments_can_never_carry_authority(auto_context, workspace):
    """An `authority_token` in ARGUMENTS is data, never authority: manual mode still stops the
    write, because authority comes from the caller's own scope, not from anything a proposal
    carries."""
    from core.mode_permission_policy import set_active_mode

    set_active_mode(SESSION, "manual")
    task_id, args = drive_to_approved_proposal(auto_context)
    forged = door(
        "code.task.step",
        {
            "task_id": task_id,
            "step_id": "mutate",
            "intent": "workspace.replace_in_file",
            "arguments": {**args, "authority_token": "model-minted-token"},
        },
        auto_context,
    )
    assert forged.details["executed"] is False, forged.status
    assert (workspace / OWNER_PATH).read_text() == DEFECT_STATS_PY


# ---------------------------------------------------------------- confinement


def test_sabotage_escape_paths_are_never_offered_for_approval(auto_context):
    """The contract's confinement pre-check at proposal time: an absolute or `..` path is refused
    before a preview exists, so nothing unconfined is ever put in front of an approval."""
    from core.code_assistant.fixture import DEFECT_OLD_TEXT, FIX_NEW_TEXT

    opened = door("code.task.open", {"objective": "x"}, auto_context)
    task_id = opened.details["task_id"]
    door(
        "code.task.step",
        {
            "task_id": task_id,
            "step_id": "repro",
            "intent": "workspace.run_tests",
            "arguments": {"command": NARROW_TEST_COMMAND},
        },
        auto_context,
    )
    door(
        "code.task.identify",
        {"task_id": task_id, "path": OWNER_PATH, "line": 6, "reason": "unsorted"},
        auto_context,
    )
    door(
        "code.task.step",
        {"task_id": task_id, "step_id": "read", "intent": "workspace.read_file", "arguments": {"path": OWNER_PATH}},
        auto_context,
    )
    for bad in ("../outside.py", "/etc/stats.py"):
        offered = door(
            "code.task.propose",
            {
                "task_id": task_id,
                "proposal_id": f"p-{abs(hash(bad)) % 9999}",
                "intent": "workspace.replace_in_file",
                "arguments": {"path": bad, "old_text": DEFECT_OLD_TEXT, "new_text": FIX_NEW_TEXT},
                "rationale": "escape attempt",
            },
            auto_context,
        )
        assert offered.status == "unconfined_path", offered.status
        assert offered.details["executed"] is False
    from core.code_assistant.task_runtime import code_task_runtime

    assert code_task_runtime()._load(task_id).proposals == {}


def test_sabotage_shell_cwd_escape_is_refused(auto_context):
    opened = door("code.task.open", {"objective": "x"}, auto_context)
    task_id = opened.details["task_id"]
    refused = door(
        "code.task.step",
        {
            "task_id": task_id,
            "step_id": "cwd",
            "intent": "sandbox.run_command",
            "arguments": {"command": "pwd", "cwd": ".."},
        },
        auto_context,
    )
    assert refused.details["executed"] is False and refused.ok is False, refused.status


# ---------------------------------------------------------------- direct execution


def test_sabotage_direct_mutation_during_an_open_task_is_refused(auto_context, workspace):
    """The coding lane's execution law: with a task open, a mutation proposed OUTSIDE the
    control plane -- direct workspace write, or a rollback slipped past approval -- is refused
    before any authority is spent. Only contracted code.task.* calls execute."""
    task_id, args = drive_to_approved_proposal(auto_context)
    from core.tool_intent_executor import execute_tool_intent
    from tests._toolchain_fixtures import executor_kwargs

    kwargs = executor_kwargs(
        SESSION,
        runtime_session_id=SESSION,
        workspace=str(workspace),
        workspace_root=str(workspace),
        operating_mode="auto",
        surface="api",
    )
    sneaky = execute_tool_intent(
        {"intent": "workspace.write_file", "arguments": {"path": OWNER_PATH, "content": "# direct, no proposal\n"}},
        **kwargs,
    )
    assert sneaky.ok is False and sneaky.status == "code_task_control_required", (sneaky.status, sneaky.response_text)
    assert sneaky.details["executed"] is False
    assert (workspace / OWNER_PATH).read_text() == DEFECT_STATS_PY
    rollback_sneak = execute_tool_intent(
        {"intent": "workspace.rollback_last_change", "arguments": {}}, **kwargs
    )
    assert rollback_sneak.ok is False, rollback_sneak.status
    assert (workspace / OWNER_PATH).read_text() == DEFECT_STATS_PY
    # The task itself is unharmed and still lawful: its approved mutation runs through the same funnel.
    mutate = execute_tool_intent(
        {
            "intent": "code.task.step",
            "arguments": {
                "task_id": task_id,
                "step_id": "mutate",
                "intent": "workspace.replace_in_file",
                "arguments": args,
            },
        },
        **kwargs,
    )
    assert mutate.ok is True, (mutate.status, mutate.response_text)
    assert (workspace / OWNER_PATH).read_text() != DEFECT_STATS_PY


# ---------------------------------------------------------------- receipt honesty


def test_sabotage_forged_result_argument_never_enters_the_journal(auto_context):
    task_id, _ = drive_to_approved_proposal(auto_context)
    before = set()
    from core.code_assistant.task_runtime import code_task_runtime

    before = set(code_task_runtime()._load(task_id).steps)
    forged = door(
        "code.task.step",
        {
            "task_id": task_id,
            "step_id": "f1",
            "intent": "workspace.run_tests",
            "arguments": {"command": NARROW_TEST_COMMAND},
            "result": {"success": True},
        },
        auto_context,
    )
    assert forged.status == "invalid_arguments", forged.status
    assert set(code_task_runtime()._load(task_id).steps) == before


def test_sabotage_post_fix_tampering_is_detected_by_the_review(auto_context, workspace):
    from .conftest import drive_to_approved_proposal

    task_id, args = drive_to_approved_proposal(auto_context)
    mutate = door(
        "code.task.step",
        {"task_id": task_id, "step_id": "mutate", "intent": "workspace.replace_in_file", "arguments": args},
        auto_context,
    )
    assert mutate.ok, mutate.response_text
    turn_id = str(mutate.details["tool_result"]["blackbox"]["turn_id"])
    (workspace / OWNER_PATH).write_text("# quietly rewritten after the fact\n", encoding="utf-8")
    receipt = door("code.review_evidence", {"turn_id": turn_id}, auto_context)
    assert receipt.ok
    assert receipt.details["drifted_count"] >= 1
    row = next(item for item in receipt.details["evidence"] if item["path"] == OWNER_PATH)
    assert row["state"] == "DRIFTED"


# ---------------------------------------------------------------- rollback


def test_sabotage_rollback_restores_exact_defect_bytes_and_red_test(auto_context, workspace):
    task_id, args = drive_to_approved_proposal(auto_context)
    mutate = door(
        "code.task.step",
        {"task_id": task_id, "step_id": "mutate", "intent": "workspace.replace_in_file", "arguments": args},
        auto_context,
    )
    assert mutate.ok, mutate.response_text
    turn_id = str(mutate.details["tool_result"]["blackbox"]["turn_id"])
    assert (workspace / OWNER_PATH).read_text() != DEFECT_STATS_PY

    # The rollback crosses Blackbox with the task's own bounded internal authority for the inner
    # delete-class effect (task/session/workspace/intent-bound, expiring, revoked on return); the
    # SERVED approval gate for the outer call is proven in tests/test_code_assistant_served_gaps.py.
    rolled = door("code.task.rollback", {"task_id": task_id}, auto_context)
    assert rolled.ok, rolled.response_text
    assert (workspace / OWNER_PATH).read_text() == DEFECT_STATS_PY, "rollback must restore byte-exact pre-fix content"
    # Against the FIX turn's journal the restored defect bytes are a drift -- the honest reading --
    # and the store marks the turn rolled back, so no later review can claim the fix still stands.
    summary = summarize_effects(turn_id, workspace_root=workspace)
    row = next(item for item in summary["effects"] if item["path"] == OWNER_PATH)
    assert row["state"] == "DRIFTED"
    from core.blackbox.store import default_store

    assert default_store().turn_index()[turn_id].rolled_back is True
    # A rolled-back task is terminal: no further step executes.
    late = door(
        "code.task.step",
        {
            "task_id": task_id,
            "step_id": "late",
            "intent": "workspace.run_tests",
            "arguments": {"command": NARROW_TEST_COMMAND},
        },
        auto_context,
    )
    assert late.details["executed"] is False, late.status
