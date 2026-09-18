"""The root-cause workflow, enforced by the ONE runtime's stage machine. Each invariant the
retired in-process workflow gated is proven here at the production door: reproduced failure
before any repair, owner read before propose, rationale on every proposal, commands lawful only
at evidence stages, a refusal never counts as reproduction, and no stage skipped."""
from __future__ import annotations

from core.code_assistant.fixture import (
    DEFECT_OLD_TEXT,
    FIX_NEW_TEXT,
    NARROW_TEST_COMMAND,
    OWNER_PATH,
    REGRESSION_COMMAND,
)

from .conftest import door, drive_to_approved_proposal


def test_fix_is_refused_before_reproduction(auto_context):
    opened = door("code.task.open", {"objective": "x"}, auto_context)
    task_id = opened.details["task_id"]
    early = door(
        "code.task.step",
        {
            "task_id": task_id,
            "step_id": "early",
            "intent": "workspace.replace_in_file",
            "arguments": {"path": OWNER_PATH, "old_text": DEFECT_OLD_TEXT, "new_text": FIX_NEW_TEXT},
        },
        auto_context,
    )
    assert early.status == "stage_violation" and early.details["executed"] is False
    assert "reproduce" in early.response_text


def test_propose_requires_a_rationale(auto_context):
    """Inherited invariant (fix_without_owner_rationale): a repair proposal must name the owner
    and root cause, so the approval the operator gives is one they can judge."""
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
    silent = door(
        "code.task.propose",
        {
            "task_id": task_id,
            "proposal_id": "p",
            "intent": "workspace.replace_in_file",
            "arguments": {"path": OWNER_PATH, "old_text": DEFECT_OLD_TEXT, "new_text": FIX_NEW_TEXT},
        },
        auto_context,
    )
    assert silent.status == "invalid_arguments" and "rationale" in silent.response_text
    assert auto_context["workspace_root"]


def test_propose_is_refused_before_the_owner_is_read(auto_context):
    """Inherited invariant (fix_without_owner): the owning file must be READ through the boundary
    before its repair is proposed -- the journal's read set, never the model's word."""
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
    unread = door(
        "code.task.propose",
        {
            "task_id": task_id,
            "proposal_id": "p",
            "intent": "workspace.replace_in_file",
            "arguments": {"path": OWNER_PATH, "old_text": DEFECT_OLD_TEXT, "new_text": FIX_NEW_TEXT},
            "rationale": "Owner stats.py: median never sorts.",
        },
        auto_context,
    )
    assert unread.status == "owner_not_read", unread.status
    assert "read" in unread.response_text
    # Reading the owner through the boundary makes the same proposal lawful.
    door(
        "code.task.step",
        {"task_id": task_id, "step_id": "read", "intent": "workspace.read_file", "arguments": {"path": OWNER_PATH}},
        auto_context,
    )
    read = door(
        "code.task.propose",
        {
            "task_id": task_id,
            "proposal_id": "p",
            "intent": "workspace.replace_in_file",
            "arguments": {"path": OWNER_PATH, "old_text": DEFECT_OLD_TEXT, "new_text": FIX_NEW_TEXT},
            "rationale": "Owner stats.py: median never sorts.",
        },
        auto_context,
    )
    assert read.ok, read.response_text


def test_command_cannot_act_outside_evidence_stages(auto_context, workspace):
    """Inherited invariant (family/stage table): commands are evidence acts -- reproduce, diagnose,
    narrow, cumulative. A command between an approval and its mutation is order disobedience."""
    task_id, args = drive_to_approved_proposal(auto_context)
    smuggled = door(
        "code.task.step",
        {
            "task_id": task_id,
            "step_id": "smuggled",
            "intent": "sandbox.run_command",
            "arguments": {"command": "pwd"},
        },
        auto_context,
    )
    assert smuggled.status == "stage_violation", smuggled.status
    assert smuggled.details["executed"] is False


def test_a_refusal_is_never_reproduction_evidence(auto_context):
    opened = door("code.task.open", {"objective": "x"}, auto_context)
    task_id = opened.details["task_id"]
    refused = door(
        "code.task.step",
        {
            "task_id": task_id,
            "step_id": "r",
            "intent": "workspace.run_tests",
            "arguments": {"command": "python3 -m pytest --version >/dev/null"},
        },
        auto_context,
    )
    # A blocked/refused command physically ran nothing, so it reproduces nothing.
    if not refused.details["executed"]:
        assert refused.details["tool_result"] == {}
    from core.code_assistant.task_runtime import code_task_runtime

    task = code_task_runtime()._load(task_id)
    assert task.reproduced_failure is None or refused.details["executed"]
    assert task.stage in {"reproduce", "identify"}


def test_a_passing_test_before_a_fix_is_not_a_narrow_test(auto_context):
    """Inherited invariant (narrow_test_before_fix): the narrow test proves A FIX; a green test
    with no journaled mutation advances nothing."""
    opened = door("code.task.open", {"objective": "x"}, auto_context)
    task_id = opened.details["task_id"]
    green = door(
        "code.task.step",
        {
            "task_id": task_id,
            "step_id": "g",
            "intent": "workspace.run_tests",
            "arguments": {"command": "python3 -m pytest -q tests/test_textutil.py"},
        },
        auto_context,
    )
    assert green.details["tool_result"]["success"] is True
    from core.code_assistant.task_runtime import code_task_runtime

    task = code_task_runtime()._load(task_id)
    assert task.stage == "reproduce" and task.narrow is None


def test_happy_path_records_every_stage_in_order(auto_context):
    from core.code_assistant.task_runtime import code_task_runtime

    task_id, args = drive_to_approved_proposal(auto_context)
    seen = []
    record = lambda: seen.append(code_task_runtime()._load(task_id).stage)
    mutate = door(
        "code.task.step",
        {"task_id": task_id, "step_id": "mutate", "intent": "workspace.replace_in_file", "arguments": args},
        auto_context,
    )
    assert mutate.ok and mutate.details["stage"] == "narrow_test", mutate.response_text
    record()
    narrow = door(
        "code.task.step",
        {
            "task_id": task_id,
            "step_id": "narrow",
            "intent": "workspace.run_tests",
            "arguments": {"command": NARROW_TEST_COMMAND},
        },
        auto_context,
    )
    assert narrow.details["tool_result"]["success"] is True and narrow.details["stage"] == "cumulative"
    record()
    cumulative = door(
        "code.task.step",
        {
            "task_id": task_id,
            "step_id": "cumulative",
            "intent": "workspace.run_tests",
            "arguments": {"command": REGRESSION_COMMAND},
        },
        auto_context,
    )
    assert cumulative.details["tool_result"]["success"] is True and cumulative.details["stage"] == "inspect_diff"
    record()
    diff = door(
        "code.task.step", {"task_id": task_id, "step_id": "diff", "intent": "workspace.git_diff", "arguments": {}}, auto_context
    )
    assert diff.ok and diff.details["stage"] == "report"
    record()
    report = door("code.task.report", {"task_id": task_id}, auto_context)
    assert report.details["verdict"] == "completed"
    # Stages observed after the approved proposal: each proof advances exactly one stage.
    stages = list(dict.fromkeys(seen))
    assert stages == ["narrow_test", "cumulative", "inspect_diff", "report"]
