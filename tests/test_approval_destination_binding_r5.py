"""Revision 5: an approved replacement is bound to its COMPLETE action identity.

Every approval here is produced by the real code-task control plane through the production door
-- open, read, reproduce (an executed failing command), identify, propose, approve. No journal row
is forged and no `approved=True` is injected. Each decision is the consuming permission decision
(`decide_tool_call`) in auto mode, where a verified replace is allowed and a blind overwrite of an
existing file requires approval.

Data differs from the review's probes: a money module in a subdirectory, a different defect and
fix, other destinations (another checkout, a symlinked checkout, an in-workspace alias and copy,
the machine's Documents and Downloads directories)."""

from __future__ import annotations

import hashlib
import shlex
from pathlib import Path

import pytest

from core.mode_permission_policy import PermissionEffect, decide_tool_call
from tests.repoops._harness import context, door
from tests.repoops.test_forge_actions import world

MONEY = "ledger/money.py"
BUGGY_MONEY = "def cents(amount):\n    return int(amount * 10)\n"
FIXED_MONEY = "def cents(amount):\n    return int(round(amount * 100))\n"
ALLOW, PROMPT = PermissionEffect.ALLOW, PermissionEffect.REQUIRE_APPROVAL


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _approved(ctx: dict, path: str = MONEY) -> tuple[str, dict]:
    root = Path(ctx["workspace_root"])
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(BUGGY_MONEY, encoding="utf-8")
    opened = door("code.task.open", {"objective": f"Find why cents() is wrong in {path} and repair it"}, ctx)
    assert opened.ok, opened.response_text
    task_id = opened.details["task_id"]
    read = door(
        "code.task.step",
        {"task_id": task_id, "step_id": "read", "intent": "workspace.read_file", "arguments": {"path": path}},
        ctx,
    )
    assert read.ok, read.response_text
    check = f"import runpy; assert runpy.run_path({path!r})['cents'](1.25) == 125"
    reproduced = door(
        "code.task.step",
        {"task_id": task_id, "step_id": "repro", "intent": "workspace.run_tests",
         "arguments": {"command": "python -c " + shlex.quote(check)}},
        ctx,
    )
    assert reproduced.details["tool_result"]["success"] is False, reproduced.details
    identified = door(
        "code.task.identify", {"task_id": task_id, "path": path, "line": 2, "reason": "cents multiplies by ten"}, ctx
    )
    assert identified.ok, identified.response_text
    args = {"path": path, "content": FIXED_MONEY, "expected_hash": _sha(target.read_bytes())}
    proposed = door(
        "code.task.propose",
        {"task_id": task_id, "proposal_id": "money", "intent": "workspace.write_file", "arguments": args,
         "rationale": f"{path}: cents must scale by one hundred and round"},
        ctx,
    )
    assert proposed.ok, proposed.response_text
    assert door("code.task.approve", {"task_id": task_id, "proposal_id": "money"}, ctx).ok
    return task_id, args


def _decide(intent: str, args: dict, ctx: dict):
    return decide_tool_call(intent=intent, arguments=args, task_id="turn-r5", source_context=ctx).effect


def _step(task_id: str, args: dict) -> dict:
    return {"task_id": task_id, "step_id": "apply", "intent": "workspace.write_file", "arguments": args}


def test_the_approved_destination_is_a_verified_replace_before_and_after_a_restart(world) -> None:
    from core.code_assistant.task_runtime import code_task_runtime

    root, _bare, _forge = world
    ctx = context(root, session="money-repair")
    task_id, args = _approved(ctx)
    assert _decide("workspace.write_file", args, ctx) is ALLOW
    assert _decide("code.task.step", _step(task_id, args), ctx) is ALLOW
    code_task_runtime().reset()  # a restarted daemon answers from the journal, never a cache
    assert _decide("workspace.write_file", args, ctx) is ALLOW
    assert _decide("code.task.step", _step(task_id, args), ctx) is ALLOW


@pytest.mark.parametrize(
    "destination",
    ["another-checkout", "symlinked-checkout", "alias-inside-the-workspace", "identical-copy-inside-the-workspace"],
)
def test_the_same_bytes_at_any_other_destination_keep_prompting(world, tmp_path, destination) -> None:
    root, _bare, _forge = world
    ctx = context(root, session="money-repair")
    _task_id, args = _approved(ctx)
    if destination == "another-checkout":
        other = tmp_path / "second-checkout"
        (other / "ledger").mkdir(parents=True)
        (other / MONEY).write_text(BUGGY_MONEY, encoding="utf-8")
        call_ctx, call_args = {**ctx, "workspace": str(other), "workspace_root": str(other)}, args
    elif destination == "symlinked-checkout":
        # Another checkout whose `ledger` directory is a symlink INTO the approved workspace.
        other = tmp_path / "linked-checkout"
        other.mkdir()
        (other / "ledger").symlink_to(root / "ledger", target_is_directory=True)
        call_ctx, call_args = {**ctx, "workspace": str(other), "workspace_root": str(other)}, args
    elif destination == "alias-inside-the-workspace":
        (root / "money_alias.py").symlink_to(root / MONEY)
        call_ctx, call_args = ctx, {**args, "path": "money_alias.py"}
    else:
        (root / "ledger" / "money_copy.py").write_text(BUGGY_MONEY, encoding="utf-8")
        call_ctx, call_args = ctx, {**args, "path": "ledger/money_copy.py"}
    assert _decide("workspace.write_file", call_args, call_ctx) is PROMPT
    # Control: the approved destination itself is still a verified replace.
    assert _decide("workspace.write_file", args, ctx) is ALLOW


@pytest.mark.parametrize("home_directory", ["Documents", "Downloads"])
def test_a_workspace_approval_never_authorizes_the_machine_writer(world, tmp_path, monkeypatch, home_directory) -> None:
    root, _bare, _forge = world
    ctx = context(root, session="money-repair")
    relative = f"{home_directory}/money.py"
    _task_id, args = _approved(ctx, path=relative)
    home = tmp_path / "disposable-home"
    (home / home_directory).mkdir(parents=True)
    (home / relative).write_text(BUGGY_MONEY, encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    assert _decide("machine.write_file", args, ctx) is PROMPT
    # Control: the workspace write the operator approved is still a verified replace.
    assert _decide("workspace.write_file", args, ctx) is ALLOW


def test_the_approval_belongs_to_its_session_and_to_the_task_that_owns_it(world) -> None:
    root, _bare, _forge = world
    ctx = context(root, session="money-repair")
    first_task, args = _approved(ctx)
    stranger = context(root, session="another-operator-chat")
    assert _decide("workspace.write_file", args, stranger) is PROMPT
    assert _decide("code.task.step", _step(first_task, args), stranger) is PROMPT

    second = door("code.task.open", {"objective": "Investigate an unrelated rounding report"}, ctx)
    assert second.ok, second.response_text
    second_task = second.details["task_id"]
    # A genuinely new task now owns the session's unbound writes ...
    assert _decide("workspace.write_file", args, ctx) is PROMPT
    # ... a step naming the approving task stays bound to that task's approval ...
    assert _decide("code.task.step", _step(first_task, args), ctx) is ALLOW
    # ... and a step naming the new task, which approved nothing, is not.
    assert _decide("code.task.step", _step(second_task, args), ctx) is PROMPT
    assert _decide("code.task.step", _step("../code_tasks/" + first_task, args), ctx) is PROMPT


def test_changed_content_changed_bytes_and_consumption_each_unbind_the_approval(world) -> None:
    from core.code_assistant.task_runtime import approved_replacement_matches

    root, _bare, _forge = world
    ctx = context(root, session="money-repair")
    task_id, args = _approved(ctx)
    target = root / MONEY
    assert _decide("workspace.write_file", {**args, "content": FIXED_MONEY + "# unreviewed extra\n"}, ctx) is PROMPT
    target.write_text(BUGGY_MONEY + "# edited after the approval\n", encoding="utf-8")
    assert _decide("workspace.write_file", args, ctx) is PROMPT
    assert _decide("workspace.write_file", {**args, "expected_hash": _sha(target.read_bytes())}, ctx) is PROMPT
    target.write_text(BUGGY_MONEY, encoding="utf-8")
    assert _decide("workspace.write_file", args, ctx) is ALLOW  # the approved prior bytes again

    applied = door("code.task.step", _step(task_id, args), ctx)
    assert applied.ok and applied.details["bytes_match_approved_patch"] is True, applied.details
    assert target.read_text(encoding="utf-8") == FIXED_MONEY
    target.write_text(BUGGY_MONEY, encoding="utf-8")  # identical prior bytes, but the approval is spent
    assert approved_replacement_matches(
        ctx, intent="workspace.write_file", path=MONEY, content=FIXED_MONEY, expected_hash=args["expected_hash"]
    ) is False
    assert _decide("workspace.write_file", args, ctx) is PROMPT


def test_a_cancelled_task_approval_binds_nothing(world) -> None:
    root, _bare, _forge = world
    ctx = context(root, session="money-repair")
    task_id, args = _approved(ctx)
    assert _decide("workspace.write_file", args, ctx) is ALLOW
    assert door("code.task.cancel", {"task_id": task_id, "reason": "the operator withdrew the repair"}, ctx).ok
    assert _decide("workspace.write_file", args, ctx) is PROMPT
    assert _decide("code.task.step", _step(task_id, args), ctx) is PROMPT
