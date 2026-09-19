"""Revision 6: an approval is bound to the physical destination the operator reviewed.

A proposal records its destination when it is previewed -- the canonical target the writer's own
resolver produces, and the bytes reviewed there -- and every later decision compares against that
record: approval, the permission gate, the step's admission and the direct write door (where the
path resolves), and the writer itself (where it resolves and the reviewed bytes, inside the flight
recorder and again inside the pinned directory before the rename). None of them re-resolves the
stored path string and trusts the answer, because a link retargeted after review resolves the same
string to a different file.

Every approval comes from the real control plane (open, read, reproduce, identify, propose,
approve); no approval row is forged. The one journal edit in this file reproduces the pre-revision-6
journal SHAPE (a proposal without a recorded destination) from a real proposal, to prove such a
journal is re-reviewed rather than trusted. Data differs from the review's probes (`money.py`, one
file link and one directory link at the workspace top level): a tax module, a nested directory
link, an escape outside the workspace, reviewed-byte drift, restart, consumption, cancellation,
rollback, expiry, the exact tool and task, races at admission, at the direct write door and at the
writer, and an atomic same-bytes replacement that must stay approved."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
from pathlib import Path

import pytest

from core.code_assistant import task_runtime as task_runtime_module
from core.code_assistant.task_runtime import code_task_runtime, task_dir
from core.mode_permission_policy import PermissionEffect, decide_tool_call
from tests.repoops._harness import context, door
from tests.repoops.test_forge_actions import world

ALLOW, PROMPT = PermissionEffect.ALLOW, PermissionEffect.REQUIRE_APPROVAL
TAX_BUGGY = "def vat(net):\n    return net * 2\n"
TAX_FIXED = "def vat(net):\n    return round(net * 0.2, 2)\n"
RATE_BUGGY = "def rate():\n    return 20\n"
RATE_FIXED = "def rate():\n    return 0.2\n"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _decide(intent: str, args: dict, ctx: dict):
    return decide_tool_call(intent=intent, arguments=args, task_id="turn-r6-destination", source_context=ctx).effect


def _open_and_identify(ctx: dict, path: str) -> str:
    """open -> read -> reproduce (an executed, failing check) -> identify, through the door."""
    opened = door("code.task.open", {"objective": f"Find why vat() in {path} overcharges and repair it"}, ctx)
    assert opened.ok, opened.response_text
    task_id = opened.details["task_id"]
    read = door("code.task.step", {"task_id": task_id, "step_id": f"read-{path}", "intent": "workspace.read_file",
                                   "arguments": {"path": path}}, ctx)
    assert read.ok, read.response_text
    probe = f"import runpy; assert runpy.run_path({path!r})['vat'](10) == 2"
    reproduced = door("code.task.step", {"task_id": task_id, "step_id": f"repro-{path}", "intent": "workspace.run_tests",
                                         "arguments": {"command": "python -c " + shlex.quote(probe)}}, ctx)
    assert reproduced.details["tool_result"]["success"] is False, reproduced.details
    identified = door("code.task.identify", {"task_id": task_id, "path": path, "line": 2,
                                             "reason": "vat multiplies by two instead of taking a fifth"}, ctx)
    assert identified.ok, identified.response_text
    return task_id


def _propose(ctx: dict, task_id: str, path: str, content: str, proposal_id: str):
    root = Path(ctx["workspace_root"])
    args = {"path": path, "content": content, "expected_hash": _sha((root / path).read_bytes())}
    proposed = door("code.task.propose", {"task_id": task_id, "proposal_id": proposal_id, "intent": "workspace.write_file",
                                          "arguments": args, "rationale": f"{path}: correct the magnitude"}, ctx)
    return args, proposed


def _approved(ctx: dict, path: str, *, content: str = TAX_FIXED, proposal_id: str = "vat") -> tuple[str, dict]:
    task_id = _open_and_identify(ctx, path)
    args, proposed = _propose(ctx, task_id, path, content, proposal_id)
    assert proposed.ok, proposed.response_text
    approved = door("code.task.approve", {"task_id": task_id, "proposal_id": proposal_id}, ctx)
    assert approved.ok, approved.response_text
    return task_id, args


def _step(task_id: str, args: dict, step_id: str = "apply") -> dict:
    return {"task_id": task_id, "step_id": step_id, "intent": "workspace.write_file", "arguments": args}


def _journal(task_id: str) -> dict:
    return json.loads((task_dir() / f"{task_id}.json").read_text(encoding="utf-8"))


def _restart() -> None:
    code_task_runtime().reset()  # a restarted daemon answers from the journal, never a cache


def _retarget(link: Path, target: Path) -> None:
    link.unlink()
    link.symlink_to(target, target_is_directory=target.is_dir())


def _turn_ids() -> set[str]:
    from core.blackbox.store import default_store

    return set(default_store().turn_index())


def _new_effects(turns_before: set[str]) -> list[tuple[str, str, str]]:
    """(path, outcome, writer status) of every flight-recorder effect journaled since ``turns_before``."""
    from core.blackbox.store import default_store

    store = default_store()
    return sorted(
        (effect.path, effect.outcome, str((effect.terminal or {}).get("status") or ""))
        for turn_id in set(store.turn_index()) - turns_before
        for effect in store.effects_for_turn(turn_id)
    )


# ---------------------------------------------------------------------------
# The reviewed destination survives nothing that changes it
# ---------------------------------------------------------------------------


def test_a_file_link_retargeted_after_approval_moves_neither_the_approval_nor_the_write(world) -> None:
    root, _bare, _forge = world
    (root / "rules").mkdir()
    (root / "rules" / "a.py").write_text(TAX_BUGGY, encoding="utf-8")
    (root / "rules" / "b.py").write_text(TAX_BUGGY, encoding="utf-8")
    link = root / "active_rules.py"
    link.symlink_to(root / "rules" / "a.py")
    ctx = context(root, session="destination-file-link")
    task_id, args = _approved(ctx, "active_rules.py")
    assert _decide("code.task.step", _step(task_id, args), ctx) is ALLOW

    _retarget(link, root / "rules" / "b.py")
    _restart()
    assert _decide("code.task.step", _step(task_id, args), ctx) is PROMPT
    assert _decide("workspace.write_file", args, ctx) is PROMPT
    refused = door("code.task.step", _step(task_id, args), ctx)
    assert refused.ok is False and refused.status == "destination_changed", (refused.status, refused.response_text)
    assert (root / "rules" / "a.py").read_text(encoding="utf-8") == TAX_BUGGY
    assert (root / "rules" / "b.py").read_text(encoding="utf-8") == TAX_BUGGY
    assert os.readlink(link) == str(root / "rules" / "b.py")


def test_a_nested_directory_link_retargeted_after_approval_moves_neither_the_approval_nor_the_write(world) -> None:
    root, _bare, _forge = world
    for release in ("v1", "v2"):
        (root / "releases" / release / "pricing").mkdir(parents=True)
        (root / "releases" / release / "pricing" / "tax.py").write_text(TAX_BUGGY, encoding="utf-8")
    link = root / "current"
    link.symlink_to(root / "releases" / "v1", target_is_directory=True)
    ctx = context(root, session="destination-directory-link")
    task_id, args = _approved(ctx, "current/pricing/tax.py")
    assert _decide("code.task.step", _step(task_id, args), ctx) is ALLOW

    _retarget(link, root / "releases" / "v2")
    _restart()
    assert _decide("code.task.step", _step(task_id, args), ctx) is PROMPT
    refused = door("code.task.step", _step(task_id, args), ctx)
    assert refused.ok is False and refused.status == "destination_changed", (refused.status, refused.response_text)
    for release in ("v1", "v2"):
        assert (root / "releases" / release / "pricing" / "tax.py").read_text(encoding="utf-8") == TAX_BUGGY


def test_a_link_retargeted_outside_the_workspace_after_approval_is_refused_and_writes_nothing(world, tmp_path) -> None:
    root, _bare, _forge = world
    (root / "rules").mkdir()
    (root / "rules" / "a.py").write_text(TAX_BUGGY, encoding="utf-8")
    outside = tmp_path / "outside-workspace" / "tax.py"
    outside.parent.mkdir()
    outside.write_text(TAX_BUGGY, encoding="utf-8")
    link = root / "active_rules.py"
    link.symlink_to(root / "rules" / "a.py")
    ctx = context(root, session="destination-escape")
    task_id, args = _approved(ctx, "active_rules.py")

    _retarget(link, outside)
    assert _decide("code.task.step", _step(task_id, args), ctx) is PROMPT
    refused = door("code.task.step", _step(task_id, args), ctx)
    assert refused.ok is False and refused.status == "destination_changed", (refused.status, refused.response_text)
    assert outside.read_text(encoding="utf-8") == TAX_BUGGY
    assert (root / "rules" / "a.py").read_text(encoding="utf-8") == TAX_BUGGY


def test_reviewed_bytes_that_change_after_approval_unbind_it_and_nothing_is_written(world) -> None:
    root, _bare, _forge = world
    (root / "invoice").mkdir()
    (root / "invoice" / "tax.py").write_text(TAX_BUGGY, encoding="utf-8")
    ctx = context(root, session="destination-reviewed-bytes")
    task_id, args = _approved(ctx, "invoice/tax.py")
    drifted = TAX_BUGGY + "# edited by someone else after review\n"
    (root / "invoice" / "tax.py").write_text(drifted, encoding="utf-8")
    # A direct write over those bytes is an unapproved overwrite and prompts. The task's own step is admitted, and its
    # writer refuses the stale base (journaled) instead of an overwrite prompt for a write that could never land.
    assert _decide("workspace.write_file", args, ctx) is PROMPT
    assert _decide("code.task.step", _step(task_id, args), ctx) is ALLOW
    turns_before = _turn_ids()
    refused = door("code.task.step", _step(task_id, args), ctx)
    assert refused.ok is False and refused.status == "stale_base", (refused.status, refused.response_text)
    assert (root / "invoice" / "tax.py").read_text(encoding="utf-8") == drifted
    # The writer refused inside the flight recorder: the attempt is journaled as a refused effect.
    assert _new_effects(turns_before) == [("invoice/tax.py", "refused", "stale_base")]


def test_a_link_retargeted_between_proposal_and_approval_cannot_be_approved(world) -> None:
    root, _bare, _forge = world
    (root / "rules").mkdir()
    (root / "rules" / "a.py").write_text(TAX_BUGGY, encoding="utf-8")
    (root / "rules" / "b.py").write_text(TAX_BUGGY, encoding="utf-8")
    link = root / "active_rules.py"
    link.symlink_to(root / "rules" / "a.py")
    ctx = context(root, session="destination-before-approval")
    task_id = _open_and_identify(ctx, "active_rules.py")
    args, proposed = _propose(ctx, task_id, "active_rules.py", TAX_FIXED, "vat")
    assert proposed.ok, proposed.response_text

    _retarget(link, root / "rules" / "b.py")
    refused = door("code.task.approve", {"task_id": task_id, "proposal_id": "vat"}, ctx)
    assert refused.ok is False and refused.status == "destination_changed", (refused.status, refused.response_text)
    assert _journal(task_id)["proposals"]["vat"]["approved"] is False
    assert _decide("code.task.step", _step(task_id, args), ctx) is PROMPT


# ---------------------------------------------------------------------------
# Positive controls: what the operator reviewed still executes
# ---------------------------------------------------------------------------


def test_an_unchanged_link_stays_approved_and_the_write_lands_on_its_reviewed_target(world) -> None:
    root, _bare, _forge = world
    (root / "rules").mkdir()
    (root / "rules" / "a.py").write_text(TAX_BUGGY, encoding="utf-8")
    link = root / "active_rules.py"
    link.symlink_to(root / "rules" / "a.py")
    ctx = context(root, session="destination-stable-link")
    task_id, args = _approved(ctx, "active_rules.py")
    assert _decide("code.task.step", _step(task_id, args), ctx) is ALLOW
    _restart()
    assert _decide("code.task.step", _step(task_id, args), ctx) is ALLOW
    applied = door("code.task.step", _step(task_id, args), ctx)
    assert applied.ok, (applied.status, applied.response_text)
    assert (root / "rules" / "a.py").read_text(encoding="utf-8") == TAX_FIXED
    assert link.is_symlink() and os.readlink(link) == str(root / "rules" / "a.py")
    assert _journal(task_id)["steps"]["apply"]["bytes_match_approved_patch"] is True


def test_an_ordinary_file_stays_approved_across_a_restart_and_applies_once(world) -> None:
    root, _bare, _forge = world
    (root / "invoice").mkdir()
    (root / "invoice" / "tax.py").write_text(TAX_BUGGY, encoding="utf-8")
    ctx = context(root, session="destination-ordinary")
    task_id, args = _approved(ctx, "invoice/tax.py")
    assert _decide("workspace.write_file", args, ctx) is ALLOW
    _restart()
    assert _decide("code.task.step", _step(task_id, args), ctx) is ALLOW
    applied = door("code.task.step", _step(task_id, args), ctx)
    assert applied.ok, (applied.status, applied.response_text)
    assert (root / "invoice" / "tax.py").read_text(encoding="utf-8") == TAX_FIXED
    # Consumed: the executed step spent the approval, so it binds nothing further, and the same
    # step replays its journaled result instead of writing again.
    assert _decide("code.task.step", _step(task_id, args, step_id="apply-again"), ctx) is PROMPT
    replay = door("code.task.step", _step(task_id, args), ctx)
    assert replay.details.get("replayed") is True and replay.details.get("executed") is False, replay.details


def test_an_atomic_replacement_with_the_reviewed_bytes_stays_approved(world) -> None:
    """The identity is the canonical destination and the reviewed bytes, not an inode: an editor's
    atomic save that leaves the same bytes at the same path is still the reviewed file."""
    root, _bare, _forge = world
    (root / "invoice").mkdir()
    target = root / "invoice" / "tax.py"
    target.write_text(TAX_BUGGY, encoding="utf-8")
    ctx = context(root, session="destination-atomic-save")
    task_id, args = _approved(ctx, "invoice/tax.py")
    before_inode = target.stat().st_ino
    staged = root / "invoice" / ".tax.py.editor-save"
    staged.write_text(TAX_BUGGY, encoding="utf-8")
    os.replace(staged, target)
    assert target.stat().st_ino != before_inode
    assert _decide("code.task.step", _step(task_id, args), ctx) is ALLOW
    applied = door("code.task.step", _step(task_id, args), ctx)
    assert applied.ok, (applied.status, applied.response_text)
    assert target.read_text(encoding="utf-8") == TAX_FIXED


# ---------------------------------------------------------------------------
# Owner, scope and tool
# ---------------------------------------------------------------------------


def test_a_cancelled_task_approval_binds_nothing_and_executes_nothing(world) -> None:
    root, _bare, _forge = world
    (root / "invoice").mkdir()
    (root / "invoice" / "tax.py").write_text(TAX_BUGGY, encoding="utf-8")
    ctx = context(root, session="destination-cancelled")
    task_id, args = _approved(ctx, "invoice/tax.py")
    assert door("code.task.cancel", {"task_id": task_id, "reason": "operator withdrew the repair"}, ctx).ok
    assert _decide("code.task.step", _step(task_id, args), ctx) is PROMPT
    refused = door("code.task.step", _step(task_id, args), ctx)
    assert refused.ok is False and refused.status == "cancelled", (refused.status, refused.response_text)
    assert (root / "invoice" / "tax.py").read_text(encoding="utf-8") == TAX_BUGGY


def test_an_expired_task_approval_binds_nothing(world, monkeypatch) -> None:
    root, _bare, _forge = world
    (root / "invoice").mkdir()
    (root / "invoice" / "tax.py").write_text(TAX_BUGGY, encoding="utf-8")
    ctx = context(root, session="destination-expired")
    task_id, args = _approved(ctx, "invoice/tax.py")
    assert _decide("code.task.step", _step(task_id, args), ctx) is ALLOW
    monkeypatch.setattr(task_runtime_module, "_ACTIVE_TASK_TTL_SECONDS", -1)
    assert _decide("code.task.step", _step(task_id, args), ctx) is PROMPT
    assert (root / "invoice" / "tax.py").read_text(encoding="utf-8") == TAX_BUGGY


def test_a_rolled_back_task_approval_binds_nothing(world) -> None:
    root, _bare, _forge = world
    (root / "invoice").mkdir()
    (root / "invoice" / "tax.py").write_text(TAX_BUGGY, encoding="utf-8")
    (root / "invoice" / "rate.py").write_text(RATE_BUGGY, encoding="utf-8")
    ctx = context(root, session="destination-rolled-back")
    task_id = _open_and_identify(ctx, "invoice/tax.py")
    read_rate = door("code.task.step", {"task_id": task_id, "step_id": "read-rate", "intent": "workspace.read_file",
                                        "arguments": {"path": "invoice/rate.py"}}, ctx)
    assert read_rate.ok, read_rate.response_text
    tax_args, tax_proposed = _propose(ctx, task_id, "invoice/tax.py", TAX_FIXED, "vat")
    rate_args, rate_proposed = _propose(ctx, task_id, "invoice/rate.py", RATE_FIXED, "rate")
    assert tax_proposed.ok and rate_proposed.ok, (tax_proposed.response_text, rate_proposed.response_text)
    for proposal_id in ("vat", "rate"):
        assert door("code.task.approve", {"task_id": task_id, "proposal_id": proposal_id}, ctx).ok
    applied = door("code.task.step", _step(task_id, tax_args), ctx)
    assert applied.ok, (applied.status, applied.response_text)
    assert _decide("code.task.step", _step(task_id, rate_args, step_id="apply-rate"), ctx) is ALLOW
    rolled = door("code.task.rollback", {"task_id": task_id}, ctx)
    assert rolled.ok, (rolled.status, rolled.response_text)
    assert (root / "invoice" / "tax.py").read_text(encoding="utf-8") == TAX_BUGGY
    assert _decide("code.task.step", _step(task_id, rate_args, step_id="apply-rate"), ctx) is PROMPT
    assert (root / "invoice" / "rate.py").read_text(encoding="utf-8") == RATE_BUGGY


def test_the_approval_binds_its_own_tool_and_its_own_task(world, tmp_path, monkeypatch) -> None:
    root, _bare, _forge = world
    (root / "Documents").mkdir()
    (root / "Documents" / "tax.py").write_text(TAX_BUGGY, encoding="utf-8")
    ctx = context(root, session="destination-tool-and-task")
    task_id, args = _approved(ctx, "Documents/tax.py")
    home = tmp_path / "disposable-home"
    (home / "Documents").mkdir(parents=True)
    (home / "Documents" / "tax.py").write_text(TAX_BUGGY, encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    assert _decide("machine.write_file", args, ctx) is PROMPT
    assert _decide("workspace.write_file", args, ctx) is ALLOW
    other = door("code.task.open", {"objective": "Investigate an unrelated rounding report"}, ctx)
    assert other.ok, other.response_text
    assert _decide("code.task.step", _step(other.details["task_id"], args), ctx) is PROMPT
    assert _decide("code.task.step", _step(task_id, args), ctx) is ALLOW
    assert (home / "Documents" / "tax.py").read_text(encoding="utf-8") == TAX_BUGGY


# ---------------------------------------------------------------------------
# Journals written before the destination was recorded
# ---------------------------------------------------------------------------


def _strip_recorded_destination(task_id: str, proposal_id: str) -> None:
    """Rewrite the journal into the pre-revision-6 SHAPE: the real proposal, minus the destination
    record older runtimes never wrote. Consent is not added -- only identity evidence is removed."""
    path = task_dir() / f"{task_id}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    preview = payload["proposals"][proposal_id]["preview"]
    assert preview.pop("destinations", None) is not None, preview
    path.write_text(json.dumps(payload, sort_keys=True, indent=1), encoding="utf-8")
    _restart()


def test_an_approved_proposal_from_an_older_journal_must_be_reviewed_again(world) -> None:
    root, _bare, _forge = world
    (root / "invoice").mkdir()
    (root / "invoice" / "tax.py").write_text(TAX_BUGGY, encoding="utf-8")
    ctx = context(root, session="destination-legacy-approved")
    task_id, args = _approved(ctx, "invoice/tax.py")
    _strip_recorded_destination(task_id, "vat")

    assert _decide("code.task.step", _step(task_id, args), ctx) is PROMPT
    refused = door("code.task.step", _step(task_id, args), ctx)
    assert refused.ok is False and refused.status == "destination_unrecorded", (refused.status, refused.response_text)
    assert (root / "invoice" / "tax.py").read_text(encoding="utf-8") == TAX_BUGGY

    reviewed = door("code.task.propose", {"task_id": task_id, "proposal_id": "vat", "intent": "workspace.write_file",
                                          "arguments": args, "rationale": "invoice/tax.py: correct the magnitude"}, ctx)
    assert reviewed.ok and reviewed.details.get("replayed") is not True, reviewed.details
    row = _journal(task_id)["proposals"]["vat"]
    assert row["approved"] is False and row["preview"]["destinations"], row
    assert door("code.task.approve", {"task_id": task_id, "proposal_id": "vat"}, ctx).ok
    applied = door("code.task.step", _step(task_id, args, step_id="apply-reviewed"), ctx)
    assert applied.ok, (applied.status, applied.response_text)
    assert (root / "invoice" / "tax.py").read_text(encoding="utf-8") == TAX_FIXED


def test_an_unapproved_proposal_from_an_older_journal_cannot_be_approved_until_reviewed(world) -> None:
    root, _bare, _forge = world
    (root / "invoice").mkdir()
    (root / "invoice" / "tax.py").write_text(TAX_BUGGY, encoding="utf-8")
    ctx = context(root, session="destination-legacy-unapproved")
    task_id = _open_and_identify(ctx, "invoice/tax.py")
    _args, proposed = _propose(ctx, task_id, "invoice/tax.py", TAX_FIXED, "vat")
    assert proposed.ok, proposed.response_text
    _strip_recorded_destination(task_id, "vat")
    refused = door("code.task.approve", {"task_id": task_id, "proposal_id": "vat"}, ctx)
    assert refused.ok is False and refused.status == "destination_unrecorded", (refused.status, refused.response_text)
    assert _journal(task_id)["proposals"]["vat"]["approved"] is False


# ---------------------------------------------------------------------------
# Races after the permission decision: admission and the writer re-check the record
# ---------------------------------------------------------------------------


def test_a_retarget_after_the_permission_decision_is_refused_at_admission(world) -> None:
    root, _bare, _forge = world
    (root / "rules").mkdir()
    (root / "rules" / "a.py").write_text(TAX_BUGGY, encoding="utf-8")
    (root / "rules" / "b.py").write_text(TAX_BUGGY, encoding="utf-8")
    link = root / "active_rules.py"
    link.symlink_to(root / "rules" / "a.py")
    ctx = context(root, session="destination-race-admission")
    task_id, args = _approved(ctx, "active_rules.py")
    assert _decide("code.task.step", _step(task_id, args), ctx) is ALLOW
    _retarget(link, root / "rules" / "b.py")  # after the gate allowed it, before the step runs
    refused = door("code.task.step", _step(task_id, args), ctx)
    assert refused.ok is False and refused.status == "destination_changed", (refused.status, refused.response_text)
    assert (root / "rules" / "a.py").read_text(encoding="utf-8") == TAX_BUGGY
    assert (root / "rules" / "b.py").read_text(encoding="utf-8") == TAX_BUGGY


def test_a_retarget_after_admission_is_refused_by_the_writer(world, monkeypatch) -> None:
    import core.runtime_execution_tools as runtime_tools

    root, _bare, _forge = world
    (root / "rules").mkdir()
    (root / "rules" / "a.py").write_text(TAX_BUGGY, encoding="utf-8")
    (root / "rules" / "b.py").write_text(TAX_BUGGY, encoding="utf-8")
    link = root / "active_rules.py"
    link.symlink_to(root / "rules" / "a.py")
    ctx = context(root, session="destination-race-writer")
    task_id, args = _approved(ctx, "active_rules.py")

    original = runtime_tools.execute_runtime_tool

    def retarget_then_dispatch(intent, arguments, **kwargs):
        if intent == "workspace.write_file":
            _retarget(link, root / "rules" / "b.py")  # admitted by the task runtime; the writer runs next
        return original(intent, arguments, **kwargs)

    monkeypatch.setattr(runtime_tools, "execute_runtime_tool", retarget_then_dispatch)
    refused = door("code.task.step", _step(task_id, args), ctx)
    assert refused.ok is False and refused.status == "destination_changed", (refused.status, refused.response_text)
    assert (root / "rules" / "a.py").read_text(encoding="utf-8") == TAX_BUGGY
    assert (root / "rules" / "b.py").read_text(encoding="utf-8") == TAX_BUGGY


def test_a_directory_swapped_for_a_link_while_the_writer_opens_it_is_refused(world, monkeypatch) -> None:
    from core.execution import artifacts

    root, _bare, _forge = world
    (root / "pricing").mkdir()
    (root / "pricing" / "tax.py").write_text(TAX_BUGGY, encoding="utf-8")
    (root / "decoy").mkdir()
    (root / "decoy" / "tax.py").write_text(TAX_BUGGY, encoding="utf-8")
    ctx = context(root, session="destination-race-directory")
    task_id, args = _approved(ctx, "pricing/tax.py")

    original = artifacts._open_pinned_directory

    def swap_then_open(parent):
        os.rename(root / "pricing", root / "pricing-moved")
        (root / "pricing").symlink_to(root / "decoy", target_is_directory=True)
        return original(parent)

    monkeypatch.setattr(artifacts, "_open_pinned_directory", swap_then_open)
    refused = door("code.task.step", _step(task_id, args), ctx)
    assert refused.ok is False and refused.status == "destination_changed", (refused.status, refused.response_text)
    assert (root / "decoy" / "tax.py").read_text(encoding="utf-8") == TAX_BUGGY
    assert (root / "pricing-moved" / "tax.py").read_text(encoding="utf-8") == TAX_BUGGY
    assert not any(name.endswith(".tmp") for name in os.listdir(root / "decoy"))


# ---------------------------------------------------------------------------
# The reviewed prior bytes are re-checked inside the pinned directory, just before the rename
# ---------------------------------------------------------------------------


def test_bytes_changed_just_before_the_rename_are_kept_and_the_write_is_refused(world, monkeypatch) -> None:
    from core.execution import artifacts

    root, _bare, _forge = world
    (root / "invoice").mkdir()
    target = root / "invoice" / "tax.py"
    target.write_text(TAX_BUGGY, encoding="utf-8")
    ctx = context(root, session="destination-prior-bytes-at-rename")
    task_id, args = _approved(ctx, "invoice/tax.py")
    concurrent = TAX_BUGGY + "# saved by an editor while the step was running\n"
    original = artifacts._open_pinned_directory

    def edit_then_open(parent):
        target.write_text(concurrent, encoding="utf-8")  # after the writer's own check, before the rename
        return original(parent)

    monkeypatch.setattr(artifacts, "_open_pinned_directory", edit_then_open)
    refused = door("code.task.step", _step(task_id, args), ctx)
    assert refused.ok is False and refused.status == "stale_base", (refused.status, refused.response_text)
    assert target.read_text(encoding="utf-8") == concurrent
    assert not any(name.endswith(".tmp") for name in os.listdir(root / "invoice"))


# ---------------------------------------------------------------------------
# The proposal door carries only the coding contract's tools
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("intent", "arguments"),
    [
        ("machine.write_file", {"path": "Documents/tax.py", "content": TAX_FIXED}),
        ("machine.move_path", {"source": "Documents/tax.py", "destination": "Downloads/tax.py"}),
        ("email.send", {"to": "billing@example.invalid", "subject": "vat", "body": "fixed"}),
    ],
)
def test_the_proposal_door_refuses_tools_outside_the_coding_contract(world, tmp_path, monkeypatch, intent, arguments) -> None:
    root, _bare, _forge = world
    home = tmp_path / "disposable-home"
    for name in ("Documents", "Downloads", "Desktop"):
        (home / name).mkdir(parents=True)
    (home / "Documents" / "tax.py").write_text(TAX_BUGGY, encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    (root / "invoice").mkdir()
    (root / "invoice" / "tax.py").write_text(TAX_BUGGY, encoding="utf-8")
    ctx = context(root, session="destination-door-scope")
    task_id = _open_and_identify(ctx, "invoice/tax.py")
    refused = door("code.task.propose", {"task_id": task_id, "proposal_id": "outside", "intent": intent,
                                         "arguments": arguments, "rationale": "invoice/tax.py: out-of-contract tool"}, ctx)
    assert refused.ok is False and refused.status == "intent_not_in_code_assistant_scope", (refused.status, refused.response_text)
    assert "outside" not in _journal(task_id)["proposals"]
    # With no approval able to exist, a direct machine overwrite keeps prompting.
    direct = {"path": "Documents/tax.py", "content": TAX_FIXED, "expected_hash": _sha((home / "Documents" / "tax.py").read_bytes())}
    assert _decide("machine.write_file", direct, ctx) is PROMPT
    assert (home / "Documents" / "tax.py").read_text(encoding="utf-8") == TAX_BUGGY


# ---------------------------------------------------------------------------
# Approved unified diffs: every touched destination is recorded, re-checked and written pinned
# ---------------------------------------------------------------------------

VAT_RATE_PATCH = (
    "diff --git a/billing/vat.py b/billing/vat.py\n"
    "--- a/billing/vat.py\n"
    "+++ b/billing/vat.py\n"
    "@@ -1,2 +1,2 @@\n"
    " def vat(net):\n"
    "-    return net * 2\n"
    "+    return round(net * 0.2, 2)\n"
    "diff --git a/pricing/rate.py b/pricing/rate.py\n"
    "--- a/pricing/rate.py\n"
    "+++ b/pricing/rate.py\n"
    "@@ -1,2 +1,2 @@\n"
    " def rate():\n"
    "-    return 20\n"
    "+    return 0.2\n"
)


def _approved_patch(ctx: dict, patch: str, *, defect_path: str = "billing/vat.py", proposal_id: str = "patch") -> tuple[str, dict]:
    task_id = _open_and_identify(ctx, defect_path)
    args = {"patch": patch}
    proposed = door("code.task.propose", {"task_id": task_id, "proposal_id": proposal_id,
                                          "intent": "workspace.apply_unified_diff", "arguments": args,
                                          "rationale": f"{defect_path}: correct the magnitudes"}, ctx)
    assert proposed.ok, (proposed.status, proposed.response_text)
    approved = door("code.task.approve", {"task_id": task_id, "proposal_id": proposal_id}, ctx)
    assert approved.ok, (approved.status, approved.response_text)
    return task_id, args


def _patch_step(task_id: str, args: dict, step_id: str = "apply-patch") -> dict:
    return {"task_id": task_id, "step_id": step_id, "intent": "workspace.apply_unified_diff", "arguments": args}


def _billing_world(root: Path) -> None:
    (root / "billing").mkdir(exist_ok=True)
    (root / "pricing").mkdir(exist_ok=True)
    (root / "billing" / "vat.py").write_text(TAX_BUGGY, encoding="utf-8")
    (root / "pricing" / "rate.py").write_text(RATE_BUGGY, encoding="utf-8")
    (root / "billing" / "NOTES.md").write_text("untouched\n", encoding="utf-8")


def test_an_approved_multi_file_patch_applies_to_exactly_its_reviewed_files(world) -> None:
    root, _bare, _forge = world
    _billing_world(root)
    ctx = context(root, session="destination-patch-positive")
    task_id, args = _approved_patch(ctx, VAT_RATE_PATCH)
    recorded = _journal(task_id)["proposals"]["patch"]["preview"]["destinations"]
    assert sorted(row["path"] for row in recorded) == ["billing/vat.py", "pricing/rate.py"], recorded
    _restart()
    applied = door("code.task.step", _patch_step(task_id, args), ctx)
    assert applied.ok, (applied.status, applied.response_text)
    assert (root / "billing" / "vat.py").read_text(encoding="utf-8") == TAX_FIXED
    assert (root / "pricing" / "rate.py").read_text(encoding="utf-8") == RATE_FIXED
    assert (root / "billing" / "NOTES.md").read_text(encoding="utf-8") == "untouched\n"


def test_a_patch_directory_link_retargeted_after_approval_is_refused_before_any_byte(world) -> None:
    root, _bare, _forge = world
    for release in ("v1", "v2"):
        (root / "releases" / release / "billing").mkdir(parents=True)
        (root / "releases" / release / "billing" / "vat.py").write_text(TAX_BUGGY, encoding="utf-8")
    (root / "billing").symlink_to(root / "releases" / "v1" / "billing", target_is_directory=True)
    (root / "pricing").mkdir()
    (root / "pricing" / "rate.py").write_text(RATE_BUGGY, encoding="utf-8")
    ctx = context(root, session="destination-patch-retarget")
    task_id, args = _approved_patch(ctx, VAT_RATE_PATCH)
    _retarget(root / "billing", root / "releases" / "v2" / "billing")
    refused = door("code.task.step", _patch_step(task_id, args), ctx)
    assert refused.ok is False and refused.status == "destination_changed", (refused.status, refused.response_text)
    for release in ("v1", "v2"):
        assert (root / "releases" / release / "billing" / "vat.py").read_text(encoding="utf-8") == TAX_BUGGY
    assert (root / "pricing" / "rate.py").read_text(encoding="utf-8") == RATE_BUGGY


def test_a_patch_link_retargeted_after_admission_is_refused_by_the_writer_before_any_byte(world, monkeypatch) -> None:
    import core.runtime_execution_tools as runtime_tools

    root, _bare, _forge = world
    for release in ("v1", "v2"):
        (root / "releases" / release / "billing").mkdir(parents=True)
        (root / "releases" / release / "billing" / "vat.py").write_text(TAX_BUGGY, encoding="utf-8")
    (root / "billing").symlink_to(root / "releases" / "v1" / "billing", target_is_directory=True)
    (root / "pricing").mkdir()
    (root / "pricing" / "rate.py").write_text(RATE_BUGGY, encoding="utf-8")
    ctx = context(root, session="destination-patch-writer-race")
    task_id, args = _approved_patch(ctx, VAT_RATE_PATCH)
    original = runtime_tools.execute_runtime_tool

    def retarget_then_dispatch(intent, arguments, **kwargs):
        if intent == "workspace.apply_unified_diff":
            _retarget(root / "billing", root / "releases" / "v2" / "billing")
        return original(intent, arguments, **kwargs)

    monkeypatch.setattr(runtime_tools, "execute_runtime_tool", retarget_then_dispatch)
    refused = door("code.task.step", _patch_step(task_id, args), ctx)
    assert refused.ok is False and refused.status == "destination_changed", (refused.status, refused.response_text)
    for release in ("v1", "v2"):
        assert (root / "releases" / release / "billing" / "vat.py").read_text(encoding="utf-8") == TAX_BUGGY
    assert (root / "pricing" / "rate.py").read_text(encoding="utf-8") == RATE_BUGGY


def test_a_patch_refused_part_way_restores_the_files_it_already_wrote(world, monkeypatch) -> None:
    from core.execution import artifacts

    root, _bare, _forge = world
    _billing_world(root)
    (root / "decoy").mkdir()
    (root / "decoy" / "rate.py").write_text(RATE_BUGGY, encoding="utf-8")
    ctx = context(root, session="destination-patch-partway")
    task_id, args = _approved_patch(ctx, VAT_RATE_PATCH)
    original = artifacts._open_pinned_directory

    def swap_pricing_then_open(parent):
        if Path(parent).name == "pricing" and not (root / "pricing").is_symlink():
            os.rename(root / "pricing", root / "pricing-moved")
            (root / "pricing").symlink_to(root / "decoy", target_is_directory=True)
        return original(parent)

    monkeypatch.setattr(artifacts, "_open_pinned_directory", swap_pricing_then_open)
    refused = door("code.task.step", _patch_step(task_id, args), ctx)
    assert refused.ok is False and refused.status == "destination_changed", (refused.status, refused.response_text)
    assert (root / "billing" / "vat.py").read_text(encoding="utf-8") == TAX_BUGGY  # written, then restored
    assert (root / "pricing-moved" / "rate.py").read_text(encoding="utf-8") == RATE_BUGGY
    assert (root / "decoy" / "rate.py").read_text(encoding="utf-8") == RATE_BUGGY
    assert "billing/vat.py" in list(refused.details.get("tool_result", {}).get("restored_paths") or []), refused.details


def test_a_patch_the_protected_writer_cannot_render_is_refused_and_can_be_reproposed_as_a_write(world) -> None:
    """A hunk positioned at the wrong lines: an ordinary `git apply` relocates it by context, but the
    protected writer renders exactly what was reviewed and refuses before any byte. The same change,
    re-proposed as a full-file write, applies through the protected writer."""
    root, _bare, _forge = world
    _billing_world(root)
    misplaced = (
        "--- a/billing/vat.py\n"
        "+++ b/billing/vat.py\n"
        "@@ -7,2 +7,2 @@\n"
        " def vat(net):\n"
        "-    return net * 2\n"
        "+    return round(net * 0.2, 2)\n"
    )
    ctx = context(root, session="destination-patch-unsupported")
    task_id, args = _approved_patch(ctx, misplaced)
    refused = door("code.task.step", _patch_step(task_id, args), ctx)
    assert refused.ok is False and refused.status == "protected_patch_unsupported", (refused.status, refused.response_text)
    assert "workspace.write_file" in refused.response_text, refused.response_text
    assert (root / "billing" / "vat.py").read_text(encoding="utf-8") == TAX_BUGGY

    write_args, proposed = _propose(ctx, task_id, "billing/vat.py", TAX_FIXED, "as-write")
    assert proposed.ok, (proposed.status, proposed.response_text)
    assert door("code.task.approve", {"task_id": task_id, "proposal_id": "as-write"}, ctx).ok
    applied = door("code.task.step", _step(task_id, write_args, step_id="apply-as-write"), ctx)
    assert applied.ok, (applied.status, applied.response_text)
    assert (root / "billing" / "vat.py").read_text(encoding="utf-8") == TAX_FIXED


# ---------------------------------------------------------------------------
# Approved directory creation: the reviewed location, not wherever a link points later
# ---------------------------------------------------------------------------


def _approved_directory(ctx: dict, path: str) -> tuple[str, dict]:
    root = Path(ctx["workspace_root"])
    (root / "invoice").mkdir(exist_ok=True)
    (root / "invoice" / "tax.py").write_text(TAX_BUGGY, encoding="utf-8")
    task_id = _open_and_identify(ctx, "invoice/tax.py")
    args = {"path": path}
    proposed = door("code.task.propose", {"task_id": task_id, "proposal_id": "dir", "intent": "workspace.ensure_directory",
                                          "arguments": args, "rationale": "invoice/tax.py: its exports need a folder"}, ctx)
    assert proposed.ok, (proposed.status, proposed.response_text)
    assert door("code.task.approve", {"task_id": task_id, "proposal_id": "dir"}, ctx).ok
    return task_id, args


def test_an_approved_directory_is_created_where_it_was_reviewed(world) -> None:
    root, _bare, _forge = world
    (root / "archive" / "a").mkdir(parents=True)
    (root / "exports").symlink_to(root / "archive" / "a", target_is_directory=True)
    ctx = context(root, session="destination-directory-positive")
    task_id, args = _approved_directory(ctx, "exports/2027")
    created = door("code.task.step", {"task_id": task_id, "step_id": "mkdir", "intent": "workspace.ensure_directory",
                                      "arguments": args}, ctx)
    assert created.ok, (created.status, created.response_text)
    assert (root / "archive" / "a" / "2027").is_dir()


def test_a_directory_link_retargeted_after_approval_creates_nothing_elsewhere(world) -> None:
    root, _bare, _forge = world
    (root / "archive" / "a").mkdir(parents=True)
    (root / "archive" / "b").mkdir(parents=True)
    (root / "exports").symlink_to(root / "archive" / "a", target_is_directory=True)
    ctx = context(root, session="destination-directory-retarget")
    task_id, args = _approved_directory(ctx, "exports/2027")
    _retarget(root / "exports", root / "archive" / "b")
    refused = door("code.task.step", {"task_id": task_id, "step_id": "mkdir", "intent": "workspace.ensure_directory",
                                      "arguments": args}, ctx)
    assert refused.ok is False and refused.status == "destination_changed", (refused.status, refused.response_text)
    assert not (root / "archive" / "a" / "2027").exists()
    assert not (root / "archive" / "b" / "2027").exists()


# ---------------------------------------------------------------------------
# git-format patches, unsupported headers, and the mechanism actually in use
# ---------------------------------------------------------------------------

GIT_FORMAT_PATCH = (
    "diff --git a/billing/vat.py b/billing/vat.py\n"
    "index 1111111..2222222 100644\n"
    "--- a/billing/vat.py\n"
    "+++ b/billing/vat.py\n"
    "@@ -1,2 +1,2 @@\n"
    " def vat(net):\n"
    "-    return net * 2\n"
    "+    return round(net * 0.2, 2)\n"
    "diff --git a/billing/CHANGES.md b/billing/CHANGES.md\n"
    "new file mode 100644\n"
    "index 0000000..3333333\n"
    "--- /dev/null\n"
    "+++ b/billing/CHANGES.md\n"
    "@@ -0,0 +1,1 @@\n"
    "+vat now takes a fifth and rounds\n"
)


def test_a_git_format_patch_that_creates_a_file_applies_to_exactly_its_reviewed_destinations(world) -> None:
    root, _bare, _forge = world
    _billing_world(root)
    ctx = context(root, session="destination-patch-git-format")
    task_id, args = _approved_patch(ctx, GIT_FORMAT_PATCH)
    recorded = {row["path"]: row for row in _journal(task_id)["proposals"]["patch"]["preview"]["destinations"]}
    assert recorded["billing/CHANGES.md"]["kind"] == "absent" and recorded["billing/vat.py"]["kind"] == "file", recorded
    applied = door("code.task.step", _patch_step(task_id, args), ctx)
    assert applied.ok, (applied.status, applied.response_text)
    assert (root / "billing" / "vat.py").read_text(encoding="utf-8") == TAX_FIXED
    assert (root / "billing" / "CHANGES.md").read_text(encoding="utf-8") == "vat now takes a fifth and rounds\n"
    assert (root / "billing" / "NOTES.md").read_text(encoding="utf-8") == "untouched\n"
    assert (root / "pricing" / "rate.py").read_text(encoding="utf-8") == RATE_BUGGY


def test_a_patch_that_changes_a_file_mode_is_refused_before_any_byte(world) -> None:
    root, _bare, _forge = world
    _billing_world(root)
    mode_patch = (
        "diff --git a/billing/vat.py b/billing/vat.py\n"
        "old mode 100644\n"
        "new mode 100755\n"
        "--- a/billing/vat.py\n"
        "+++ b/billing/vat.py\n"
        "@@ -1,2 +1,2 @@\n"
        " def vat(net):\n"
        "-    return net * 2\n"
        "+    return round(net * 0.2, 2)\n"
    )
    before_mode = (root / "billing" / "vat.py").stat().st_mode
    ctx = context(root, session="destination-patch-mode")
    task_id, args = _approved_patch(ctx, mode_patch)
    refused = door("code.task.step", _patch_step(task_id, args), ctx)
    assert refused.ok is False and refused.status == "protected_patch_unsupported", (refused.status, refused.response_text)
    assert "old mode 100644" in refused.response_text, refused.response_text
    assert (root / "billing" / "vat.py").read_text(encoding="utf-8") == TAX_BUGGY
    assert (root / "billing" / "vat.py").stat().st_mode == before_mode


@pytest.mark.skipif(os.name != "posix", reason="directory descriptors are a POSIX facility; elsewhere the path-checked fallback runs")
def test_the_pinned_directory_mechanism_is_the_one_that_runs_on_posix(tmp_path, monkeypatch) -> None:
    """The protection is a mechanism, not a name: on POSIX a reviewed write must go through the pinned
    directory descriptor. A build that lists `os.rename` but not `os.replace` in `os.supports_dir_fd`
    (CPython 3.11 on macOS) once made it fall back to a path-based write without a sound."""
    from core.execution import artifacts

    target = (tmp_path / "reviewed").resolve() / "tax.py"
    target.parent.mkdir()
    target.write_text(TAX_BUGGY, encoding="utf-8")
    opened: list[Path] = []
    original = artifacts._open_pinned_directory

    def counting(parent):
        opened.append(Path(parent))
        return original(parent)

    monkeypatch.setattr(artifacts, "_open_pinned_directory", counting)
    artifacts.pinned_atomic_write_text(target, TAX_FIXED, expected_prior_sha256=_sha(TAX_BUGGY.encode("utf-8")))
    assert opened == [target.parent], opened
    assert target.read_text(encoding="utf-8") == TAX_FIXED
    with pytest.raises(artifacts.ReviewedBaseChangedError):
        artifacts.pinned_atomic_write_text(target, TAX_BUGGY, expected_prior_sha256=_sha(TAX_BUGGY.encode("utf-8")))
    assert target.read_text(encoding="utf-8") == TAX_FIXED
    assert len(opened) == 2


# ---------------------------------------------------------------------------
# The direct write door: an approval that allows a replace also pins the write it allows
# ---------------------------------------------------------------------------


def _authorized_write(args: dict, ctx: dict):
    from core.authorized_tool_execution import execute_authorized_runtime_tool

    return execute_authorized_runtime_tool("workspace.write_file", args, task_id="turn-r6-direct", source_context=ctx)


def test_a_direct_write_allowed_by_an_approval_is_written_pinned_to_the_reviewed_destination(world, monkeypatch) -> None:
    from core.execution import artifacts

    root, _bare, _forge = world
    (root / "rules").mkdir()
    (root / "rules" / "a.py").write_text(TAX_BUGGY, encoding="utf-8")
    link = root / "active_rules.py"
    link.symlink_to(root / "rules" / "a.py")
    ctx = context(root, session="destination-direct-positive")
    _task_id, args = _approved(ctx, "active_rules.py")
    opened: list[Path] = []
    original = artifacts._open_pinned_directory

    def counting(parent):
        opened.append(Path(parent))
        return original(parent)

    monkeypatch.setattr(artifacts, "_open_pinned_directory", counting)
    written = _authorized_write(args, ctx)
    assert written.ok, (written.status, written.response_text)
    assert (root / "rules" / "a.py").read_text(encoding="utf-8") == TAX_FIXED
    assert opened == [root / "rules"], opened
    assert link.is_symlink() and os.readlink(link) == str(root / "rules" / "a.py")


def test_a_retarget_between_the_gate_and_a_direct_write_is_refused_before_any_byte(world, monkeypatch) -> None:
    import core.authorized_tool_execution as authorized

    root, _bare, _forge = world
    (root / "rules").mkdir()
    (root / "rules" / "a.py").write_text(TAX_BUGGY, encoding="utf-8")
    (root / "rules" / "b.py").write_text(TAX_BUGGY, encoding="utf-8")
    link = root / "active_rules.py"
    link.symlink_to(root / "rules" / "a.py")
    ctx = context(root, session="destination-direct-gate-race")
    _task_id, args = _approved(ctx, "active_rules.py")
    original = authorized.authorize_runtime_tool

    def decide_then_retarget(*call_args, **call_kwargs):
        decision = original(*call_args, **call_kwargs)
        _retarget(link, root / "rules" / "b.py")  # the gate allowed the reviewed file; the link moves now
        return decision

    monkeypatch.setattr(authorized, "authorize_runtime_tool", decide_then_retarget)
    refused = _authorized_write(args, ctx)
    assert refused.ok is False and refused.status == "destination_changed", (refused.status, refused.response_text)
    assert refused.details["executed"] is False
    assert (root / "rules" / "a.py").read_text(encoding="utf-8") == TAX_BUGGY
    assert (root / "rules" / "b.py").read_text(encoding="utf-8") == TAX_BUGGY


def test_a_retarget_after_the_door_attached_the_destination_is_refused_by_the_writer(world, monkeypatch) -> None:
    import core.runtime_execution_tools as runtime_tools

    root, _bare, _forge = world
    (root / "rules").mkdir()
    (root / "rules" / "a.py").write_text(TAX_BUGGY, encoding="utf-8")
    (root / "rules" / "b.py").write_text(TAX_BUGGY, encoding="utf-8")
    link = root / "active_rules.py"
    link.symlink_to(root / "rules" / "a.py")
    ctx = context(root, session="destination-direct-writer-race")
    _task_id, args = _approved(ctx, "active_rules.py")
    original = runtime_tools.execute_runtime_tool

    def retarget_then_dispatch(intent, arguments, **kwargs):
        if intent == "workspace.write_file":
            _retarget(link, root / "rules" / "b.py")
        return original(intent, arguments, **kwargs)

    monkeypatch.setattr(runtime_tools, "execute_runtime_tool", retarget_then_dispatch)
    refused = _authorized_write(args, ctx)
    assert refused.ok is False and refused.status == "destination_changed", (refused.status, refused.response_text)
    assert (root / "rules" / "a.py").read_text(encoding="utf-8") == TAX_BUGGY
    assert (root / "rules" / "b.py").read_text(encoding="utf-8") == TAX_BUGGY


def test_bytes_changed_between_the_gate_and_a_direct_write_are_refused_by_the_writer_and_journaled(world, monkeypatch) -> None:
    import core.authorized_tool_execution as authorized

    root, _bare, _forge = world
    (root / "invoice").mkdir()
    (root / "invoice" / "tax.py").write_text(TAX_BUGGY, encoding="utf-8")
    ctx = context(root, session="destination-direct-bytes-race")
    _task_id, args = _approved(ctx, "invoice/tax.py")
    drifted = TAX_BUGGY + "# edited by someone else after the decision\n"
    original = authorized.authorize_runtime_tool

    def decide_then_edit(*call_args, **call_kwargs):
        decision = original(*call_args, **call_kwargs)
        (root / "invoice" / "tax.py").write_text(drifted, encoding="utf-8")  # the same file, new bytes
        return decision

    monkeypatch.setattr(authorized, "authorize_runtime_tool", decide_then_edit)
    turns_before = _turn_ids()
    refused = _authorized_write(args, ctx)
    assert refused.ok is False and refused.status == "stale_base", (refused.status, refused.response_text)
    assert (root / "invoice" / "tax.py").read_text(encoding="utf-8") == drifted
    assert _new_effects(turns_before) == [("invoice/tax.py", "refused", "stale_base")]


def test_an_approval_cancelled_between_the_gate_and_a_direct_write_is_refused_before_any_byte(world, monkeypatch) -> None:
    import core.authorized_tool_execution as authorized

    root, _bare, _forge = world
    (root / "invoice").mkdir()
    (root / "invoice" / "tax.py").write_text(TAX_BUGGY, encoding="utf-8")
    ctx = context(root, session="destination-direct-cancel-race")
    task_id, args = _approved(ctx, "invoice/tax.py")
    original = authorized.authorize_runtime_tool

    def decide_then_cancel(*call_args, **call_kwargs):
        decision = original(*call_args, **call_kwargs)
        cancelled = door("code.task.cancel", {"task_id": task_id, "reason": "operator withdrew the repair"}, ctx)
        assert cancelled.ok, cancelled.response_text
        return decision

    monkeypatch.setattr(authorized, "authorize_runtime_tool", decide_then_cancel)
    refused = _authorized_write(args, ctx)
    assert refused.ok is False and refused.status == "approval_mismatch", (refused.status, refused.response_text)
    assert refused.details["executed"] is False
    assert (root / "invoice" / "tax.py").read_text(encoding="utf-8") == TAX_BUGGY


def test_a_direct_create_and_an_unapproved_overwrite_keep_their_existing_decisions(world) -> None:
    root, _bare, _forge = world
    (root / "invoice").mkdir()
    (root / "invoice" / "tax.py").write_text(TAX_BUGGY, encoding="utf-8")
    ctx = context(root, session="destination-direct-controls")
    created = _authorized_write({"path": "invoice/rates_2027.py", "content": RATE_FIXED}, ctx)
    assert created.ok, (created.status, created.response_text)
    assert (root / "invoice" / "rates_2027.py").read_text(encoding="utf-8") == RATE_FIXED
    blind = _authorized_write({"path": "invoice/tax.py", "content": TAX_FIXED,
                               "expected_hash": _sha(TAX_BUGGY.encode("utf-8"))}, ctx)
    assert blind.ok is False and blind.status == "pending_approval", (blind.status, blind.response_text)
    assert (root / "invoice" / "tax.py").read_text(encoding="utf-8") == TAX_BUGGY


# ---------------------------------------------------------------------------
# Approved unified diffs the protected writer cannot reproduce byte for byte
# ---------------------------------------------------------------------------


def _notes_patch(notes_hunk: str) -> str:
    return (
        "--- a/billing/vat.py\n"
        "+++ b/billing/vat.py\n"
        "@@ -1,2 +1,2 @@\n"
        " def vat(net):\n"
        "-    return net * 2\n"
        "+    return round(net * 0.2, 2)\n"
        "--- a/billing/NOTES.md\n"
        "+++ b/billing/NOTES.md\n"
    ) + notes_hunk


# Each case: the bytes of billing/NOTES.md, and the patch's hunk for it. The module's Python engine -- the
# only engine the protected writer uses -- reads a file as UTF-8 with replacement characters and universal
# newlines, splits lines with `str.splitlines`, and skips `\ No newline at end of file`. Applied, each case
# would write bytes the reviewed patch does not: line endings rewritten, an invalid byte replaced, a final
# newline added, or the hunk moved one line early past a form feed.
NOT_BYTE_FOR_BYTE = {
    "crlf-file": (b"untouched\r\nsecond\r\n", "@@ -2,1 +2,1 @@\n-second\n+changed\n"),
    "crlf-patch-lines": (b"untouched\nsecond\n", "@@ -2,1 +2,1 @@\r\n-second\r\n+changed\r\n"),
    "non-utf8-file": (b"caf\xe9 notes\nsecond\n", "@@ -2,1 +2,1 @@\n-second\n+changed\n"),
    "no-newline-at-end": (
        b"untouched\nlast",
        "@@ -2,1 +2,1 @@\n-last\n\\ No newline at end of file\n+changed\n\\ No newline at end of file\n",
    ),
    "form-feed-line": (b"x\n\x0c\nx\nx\n", "@@ -4,1 +4,1 @@\n-x\n+y\n"),
}


@pytest.mark.parametrize("case", sorted(NOT_BYTE_FOR_BYTE))
def test_an_approved_patch_the_protected_writer_cannot_reproduce_byte_for_byte_is_refused_before_any_byte(
    world, case
) -> None:
    notes, hunk = NOT_BYTE_FOR_BYTE[case]
    root, _bare, _forge = world
    (root / "billing").mkdir()
    (root / "billing" / "vat.py").write_text(TAX_BUGGY, encoding="utf-8")
    (root / "billing" / "NOTES.md").write_bytes(notes)
    ctx = context(root, session=f"destination-patch-bytes-{case}")
    task_id, args = _approved_patch(ctx, _notes_patch(hunk))
    refused = door("code.task.step", _patch_step(task_id, args), ctx)
    written = ((root / "billing" / "vat.py").read_bytes(), (root / "billing" / "NOTES.md").read_bytes())
    assert (refused.status, written) == ("protected_patch_unsupported", (TAX_BUGGY.encode("utf-8"), notes)), (
        refused.response_text
    )
    assert refused.ok is False
    assert "workspace.write_file" in refused.response_text


def test_an_approved_patch_to_lf_utf8_text_still_applies_byte_for_byte(world) -> None:
    root, _bare, _forge = world
    (root / "billing").mkdir()
    (root / "billing" / "vat.py").write_text(TAX_BUGGY, encoding="utf-8")
    (root / "billing" / "NOTES.md").write_bytes("café notes — ünïcode\nsecond\n".encode())
    ctx = context(root, session="destination-patch-bytes-utf8")
    task_id, args = _approved_patch(ctx, _notes_patch("@@ -2,1 +2,1 @@\n-second\n+changed\n"))
    applied = door("code.task.step", _patch_step(task_id, args), ctx)
    assert applied.ok, (applied.status, applied.response_text)
    assert (root / "billing" / "vat.py").read_text(encoding="utf-8") == TAX_FIXED
    assert (root / "billing" / "NOTES.md").read_bytes() == "café notes — ünïcode\nchanged\n".encode()


def test_an_approved_patch_renders_from_the_verified_bytes_not_a_later_read_of_the_path(world, monkeypatch) -> None:
    from core.execution import workspace_tools

    root, _bare, _forge = world
    (root / "billing").mkdir()
    (root / "billing" / "vat.py").write_text(TAX_BUGGY, encoding="utf-8")
    notes = root / "billing" / "NOTES.md"
    notes.write_bytes(b"untouched\nsecond\n")
    ctx = context(root, session="destination-patch-render-source")
    task_id, args = _approved_patch(ctx, _notes_patch("@@ -2,1 +2,1 @@\n-second\n+changed\n"))
    original_headers = workspace_tools._protected_patch_text
    original_write = workspace_tools.pinned_atomic_write_text

    def change_after_verification(patch_text):
        notes.write_bytes(b"tampered\nsecond\n")  # the reviewed bytes were verified; rendering has not started
        return original_headers(patch_text)

    def restore_before_the_pinned_write(path, content, **kwargs):
        if Path(path) == notes:
            notes.write_bytes(b"untouched\nsecond\n")  # the reviewed bytes are back when the pinned write checks
        return original_write(path, content, **kwargs)

    monkeypatch.setattr(workspace_tools, "_protected_patch_text", change_after_verification)
    monkeypatch.setattr(workspace_tools, "pinned_atomic_write_text", restore_before_the_pinned_write)
    applied = door("code.task.step", _patch_step(task_id, args), ctx)
    # The patch was reviewed against "untouched"; bytes read from the path in between must not reach the file.
    assert (applied.ok, notes.read_bytes()) == (True, b"untouched\nchanged\n"), (applied.status, applied.response_text)
    assert (root / "billing" / "vat.py").read_text(encoding="utf-8") == TAX_FIXED


def test_a_gated_step_whose_reviewed_bytes_changed_is_refused_as_a_stale_base_not_offered_as_an_overwrite(world) -> None:
    """The served concurrent-edit contract (`tests/test_code_assistant_served_negative_journeys.py::
    test_served_concurrent_edits_to_the_same_file`) through the gated door, in one process: another repair landed
    first, so this task's reviewed bytes are gone. Its step reaches the task and is refused as a stale base --
    journaled, nothing written -- instead of returning an approval request for an overwrite its writer refuses."""
    from core.authorized_tool_execution import execute_authorized_runtime_tool

    root, _bare, _forge = world
    (root / "invoice").mkdir()
    (root / "invoice" / "tax.py").write_text(TAX_BUGGY, encoding="utf-8")
    ctx = context(root, session="destination-gated-stale-step")
    task_id, args = _approved(ctx, "invoice/tax.py")
    landed = TAX_BUGGY.replace("* 2", "* 0.2")  # the other repair's bytes
    (root / "invoice" / "tax.py").write_text(landed, encoding="utf-8")
    turns_before = _turn_ids()
    refused = execute_authorized_runtime_tool(
        "code.task.step", _step(task_id, args), task_id="turn-r6-stale-step", source_context=ctx
    )
    assert (refused.ok, refused.status) == (False, "stale_base"), (refused.status, refused.response_text)
    assert (root / "invoice" / "tax.py").read_text(encoding="utf-8") == landed
    assert _journal(task_id)["steps"]["apply"]["status"] == "stale_base"
    assert _new_effects(turns_before) == [("invoice/tax.py", "refused", "stale_base")]
