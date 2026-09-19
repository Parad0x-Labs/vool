"""Revision 6: every decision about a write uses the writer's own target authority.

The permission gate, the operator's approval preview, internal-authority scope bindings and the
code-task step wrapper each located a write by their own reading of the trusted context --
`workspace_root` before `workspace` -- while the physical writer prefers `workspace`
(`core.runtime_execution_tools._workspace_root`). A context carrying both roots is legitimate: the
envelope executor, its restore step and the blackbox operator re-root `workspace` over an inherited
`workspace_root`. Under such a context the gate inspected a different file than the one written,
classified an overwrite of an existing target as a create, and allowed it in auto mode.

Every physical effect here goes through THE gated door (`execute_authorized_runtime_tool`), so a
decision and the bytes it produces are observed together. Data differs from the review's probes
(`notes.txt`, `reports/status.md`): other files and nesting, the reverse conflict, a context with no
root at all, an internal-authority scope, the operator's preview, and real code-task steps whose
caller names another root."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.authorized_tool_execution import execute_authorized_runtime_tool
from core.mode_permission_policy import (
    PermissionAction,
    PermissionEffect,
    decide_tool_call,
    grant_internal_authority,
)
from tests.repoops._harness import context, door
from tests.repoops.test_forge_actions import world
from tests.test_approval_destination_binding_r5 import BUGGY_MONEY, FIXED_MONEY, MONEY, _approved, _step

ALLOW, PROMPT = PermissionEffect.ALLOW, PermissionEffect.REQUIRE_APPROVAL
ORIGINAL = "tier,rate\nbasic,0.10\n"
REPLACEMENT = "tier,rate\nbasic,0.12\n"
TOTALS_BUGGY = "def totals(lines):\n    return sum(lines[:-1])\n"
TOTALS_FIXED = "def totals(lines):\n    return sum(lines)\n"


def _decide(intent: str, args: dict, ctx: dict):
    return decide_tool_call(intent=intent, arguments=args, task_id="turn-r6", source_context=ctx)


def _write(intent: str, args: dict, ctx: dict, **kwargs):
    return execute_authorized_runtime_tool(intent, args, task_id="turn-r6", source_context=ctx, **kwargs)


@pytest.mark.parametrize("relative", ["ledger.csv", "billing/rates/tiers.csv"])
def test_an_existing_writer_target_prompts_even_when_the_inherited_root_is_empty(world, tmp_path, relative) -> None:
    root, _bare, _forge = world
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(ORIGINAL, encoding="utf-8")
    inherited = tmp_path / "inherited-root"
    inherited.mkdir()
    ctx = context(root, session="root-authority", workspace=str(root), workspace_root=str(inherited))
    args = {"path": relative, "content": REPLACEMENT}

    decision = _decide("workspace.write_file", args, ctx)
    assert decision.effect is PROMPT, decision
    assert PermissionAction.OVERWRITE_EXISTING_FILES in decision.actions, decision.actions
    result = _write("workspace.write_file", args, ctx)
    assert result.ok is False and result.status == "pending_approval", (result.status, result.response_text)
    assert target.read_text(encoding="utf-8") == ORIGINAL
    assert list(inherited.rglob("*")) == []


def test_a_new_file_lands_in_the_writer_root_and_the_inherited_root_stays_untouched(world, tmp_path) -> None:
    """The reverse conflict: the INHERITED root holds a file at this relative path and the writer's
    root does not. The writer creates a new file in its own root, so the gate classifies a create:
    it follows the writer, it does not prompt merely because some root holds the path."""
    root, _bare, _forge = world
    inherited = root / "config" / "limits.toml"
    inherited.parent.mkdir(parents=True, exist_ok=True)
    inherited.write_text("max = 3\n", encoding="utf-8")
    writer_root = tmp_path / "re-rooted-step"
    writer_root.mkdir()
    ctx = context(root, session="root-authority", workspace=str(writer_root), workspace_root=str(root))
    args = {"path": "config/limits.toml", "content": "max = 5\n"}

    decision = _decide("workspace.write_file", args, ctx)
    assert decision.effect is ALLOW and PermissionAction.CREATE_FILES in decision.actions, decision
    result = _write("workspace.write_file", args, ctx)
    assert result.ok, (result.status, result.response_text)
    assert (writer_root / "config" / "limits.toml").read_text(encoding="utf-8") == "max = 5\n"
    assert inherited.read_text(encoding="utf-8") == "max = 3\n"


def test_consistent_roots_keep_create_and_overwrite_semantics(world) -> None:
    root, _bare, _forge = world
    ctx = context(root, session="root-authority")
    created = {"path": "notes/fresh.md", "content": "# fresh\n"}
    assert _decide("workspace.write_file", created, ctx).effect is ALLOW
    assert _write("workspace.write_file", created, ctx).ok
    assert (root / "notes" / "fresh.md").read_text(encoding="utf-8") == "# fresh\n"
    overwrite = {"path": "notes/fresh.md", "content": "# replaced\n"}
    assert _decide("workspace.write_file", overwrite, ctx).effect is PROMPT
    assert _write("workspace.write_file", overwrite, ctx).status == "pending_approval"
    assert (root / "notes" / "fresh.md").read_text(encoding="utf-8") == "# fresh\n"


def test_a_root_alias_is_the_same_workspace(world, tmp_path) -> None:
    root, _bare, _forge = world
    alias = tmp_path / "alias-of-checkout"
    alias.symlink_to(root, target_is_directory=True)
    (root / "rates.csv").write_text(ORIGINAL, encoding="utf-8")
    ctx = context(root, session="root-authority", workspace=str(alias), workspace_root=str(root))
    assert _decide("workspace.write_file", {"path": "rates.csv", "content": REPLACEMENT}, ctx).effect is PROMPT
    created = {"path": "rates_2027.csv", "content": REPLACEMENT}
    assert _decide("workspace.write_file", created, ctx).effect is ALLOW
    assert _write("workspace.write_file", created, ctx).ok
    assert (root / "rates_2027.csv").read_text(encoding="utf-8") == REPLACEMENT
    assert (root / "rates.csv").read_text(encoding="utf-8") == ORIGINAL


def test_a_context_without_roots_is_classified_against_the_writers_own_fallback(world, tmp_path, monkeypatch) -> None:
    fallback = tmp_path / "configured-workspace"
    (fallback / "reports").mkdir(parents=True)
    (fallback / "reports" / "q3.md").write_text("# q3\n", encoding="utf-8")
    monkeypatch.setenv("VOOL_WORKSPACE_ROOT", str(fallback))
    ctx = {"session_id": "root-authority", "operating_mode": "auto"}
    existing = {"path": "reports/q3.md", "content": "# q3 revised\n"}
    assert _decide("workspace.write_file", existing, ctx).effect is PROMPT
    assert _write("workspace.write_file", existing, ctx).status == "pending_approval"
    assert (fallback / "reports" / "q3.md").read_text(encoding="utf-8") == "# q3\n"
    fresh = {"path": "reports/q4.md", "content": "# q4\n"}
    assert _decide("workspace.write_file", fresh, ctx).effect is ALLOW
    assert _write("workspace.write_file", fresh, ctx).ok
    assert (fallback / "reports" / "q4.md").read_text(encoding="utf-8") == "# q4\n"


def test_a_path_the_writer_refuses_writes_nothing_outside_the_workspace(world) -> None:
    """Confinement stays the writer's: the gate cannot locate an escaping path, and the writer's
    resolver refuses it -- the dispatcher returns that refusal as a typed error result."""
    root, _bare, _forge = world
    outside = root.parent / "outside-the-checkout.txt"
    ctx = context(root, session="root-authority")
    result = _write("workspace.write_file", {"path": "../outside-the-checkout.txt", "content": "escape\n"}, ctx)
    assert result is not None and result.ok is False and result.status == "error", (result.status, result.response_text)
    assert result.details.get("exception_class") == "ValueError", result.details
    assert "escapes the active workspace" in result.response_text, result.response_text
    assert not outside.exists()


def test_an_existing_file_behind_a_name_the_writer_refuses_still_prompts(world, tmp_path) -> None:
    """The writer refuses a name that resolves outside the workspace, so nothing can be written
    through it -- and the gate must not read it as a create either: an existing file behind the name
    keeps prompting, exactly as before the target authority. (Found as a regression of this very
    change by the cumulative run: the r5 symlinked-checkout control.)"""
    root, _bare, _forge = world
    outside = tmp_path / "supplier-exports" / "rates.csv"
    outside.parent.mkdir()
    outside.write_text(ORIGINAL, encoding="utf-8")
    (root / "vendor_rates.csv").symlink_to(outside)
    ctx = context(root, session="root-authority")
    args = {"path": "vendor_rates.csv", "content": REPLACEMENT}
    decision = _decide("workspace.write_file", args, ctx)
    assert decision.effect is PROMPT, decision
    assert PermissionAction.OVERWRITE_EXISTING_FILES in decision.actions, decision.actions
    assert _write("workspace.write_file", args, ctx).status == "pending_approval"
    assert outside.read_text(encoding="utf-8") == ORIGINAL


@pytest.mark.parametrize("home_directory", ["Documents", "Desktop"])
def test_machine_writes_keep_the_safe_directory_authority(world, tmp_path, monkeypatch, home_directory) -> None:
    root, _bare, _forge = world
    home = tmp_path / "disposable-home"
    for name in ("Desktop", "Downloads", "Documents"):
        (home / name).mkdir(parents=True)
    (home / home_directory / "budget.txt").write_text(ORIGINAL, encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    decoy = tmp_path / "decoy-root"
    decoy.mkdir()
    ctx = context(root, session="root-authority", workspace=str(decoy), workspace_root=str(root))
    assert _decide("machine.write_file", {"path": f"{home_directory}/budget.txt", "content": REPLACEMENT}, ctx).effect is PROMPT
    assert (home / home_directory / "budget.txt").read_text(encoding="utf-8") == ORIGINAL
    assert _decide("machine.write_file", {"path": f"{home_directory}/budget_2027.txt", "content": REPLACEMENT}, ctx).effect is ALLOW


def test_an_internal_authority_bound_to_one_root_never_covers_a_write_landing_in_another(world, tmp_path) -> None:
    """A bounded internal scope names the workspace it may write. A context whose writer root is
    ANOTHER directory, while `workspace_root` still names the granted one, is not covered: the scope
    would otherwise authorize bytes landing outside the root it was minted for."""
    root, _bare, _forge = world
    elsewhere = tmp_path / "ungranted-root"
    elsewhere.mkdir()
    (elsewhere / "policy.md").write_text(ORIGINAL, encoding="utf-8")
    token = grant_internal_authority(
        label="revision-6 root-bound maintenance scope",
        actions=[PermissionAction.CREATE_FILES, PermissionAction.MODIFY_FILES, PermissionAction.OVERWRITE_EXISTING_FILES],
        duration_seconds=300,
        workspace_root=str(root),
    )
    args = {"path": "policy.md", "content": REPLACEMENT}
    ctx = context(root, session="root-authority", operating_mode="manual", workspace=str(elsewhere), workspace_root=str(root))
    result = _write("workspace.write_file", args, ctx, authority_token=token)
    assert result.ok is False, (result.status, result.response_text)
    assert (elsewhere / "policy.md").read_text(encoding="utf-8") == ORIGINAL
    # Control: the same scope covers the same write when the writer lands it inside the granted root.
    (root / "policy.md").write_text(ORIGINAL, encoding="utf-8")
    covered = _write("workspace.write_file", args, context(root, session="root-authority", operating_mode="manual"), authority_token=token)
    assert covered.ok, (covered.status, covered.response_text)
    assert (root / "policy.md").read_text(encoding="utf-8") == REPLACEMENT


def test_the_operator_preview_shows_the_file_the_writer_would_replace(world, tmp_path) -> None:
    root, _bare, _forge = world
    (root / "pricing.md").write_text("price: alpha\n", encoding="utf-8")
    inherited = tmp_path / "inherited-root"
    inherited.mkdir()
    (inherited / "pricing.md").write_text("price: beta\n", encoding="utf-8")
    ctx = context(root, session="root-authority", workspace=str(root), workspace_root=str(inherited))
    decision = _decide("workspace.write_file", {"path": "pricing.md", "content": "price: gamma\n"}, ctx)
    assert decision.effect is PROMPT, decision
    preview = str((decision.approval_request or {}).get("diff_preview") or "")
    assert "alpha" in preview and "gamma" in preview and "beta" not in preview, preview


def test_an_unapproved_task_step_is_classified_where_the_task_writer_writes(world, tmp_path) -> None:
    root, _bare, _forge = world
    ctx = context(root, session="step-authority")
    module = root / "billing" / "totals.py"
    module.parent.mkdir(parents=True, exist_ok=True)
    module.write_text(TOTALS_BUGGY, encoding="utf-8")
    opened = door("code.task.open", {"objective": "Find why totals() drops the last line and repair it"}, ctx)
    assert opened.ok, opened.response_text
    elsewhere = tmp_path / "caller-root"
    elsewhere.mkdir()
    caller_ctx = {**ctx, "workspace": str(elsewhere), "workspace_root": str(elsewhere)}
    step = {"task_id": opened.details["task_id"], "step_id": "blind", "intent": "workspace.write_file",
            "arguments": {"path": "billing/totals.py", "content": TOTALS_FIXED}}
    decision = _decide("code.task.step", step, caller_ctx)
    assert decision.effect is PROMPT, decision
    assert PermissionAction.OVERWRITE_EXISTING_FILES in decision.actions, decision.actions
    assert _write("code.task.step", step, caller_ctx).status == "pending_approval"
    assert module.read_text(encoding="utf-8") == TOTALS_BUGGY
    assert list(elsewhere.rglob("*")) == []


def test_an_approved_task_step_lands_in_the_task_workspace_whatever_root_the_caller_names(world, tmp_path) -> None:
    root, _bare, _forge = world
    ctx = context(root, session="step-authority")
    task_id, args = _approved(ctx)
    elsewhere = tmp_path / "caller-root"
    (elsewhere / "ledger").mkdir(parents=True)
    (elsewhere / MONEY).write_text(BUGGY_MONEY, encoding="utf-8")
    caller_ctx = {**ctx, "workspace": str(elsewhere), "workspace_root": str(elsewhere)}
    assert _decide("code.task.step", _step(task_id, args), caller_ctx).effect is ALLOW
    applied = _write("code.task.step", _step(task_id, args), caller_ctx)
    assert applied.ok, (applied.status, applied.response_text)
    assert (root / MONEY).read_text(encoding="utf-8") == FIXED_MONEY
    assert (elsewhere / MONEY).read_text(encoding="utf-8") == BUGGY_MONEY
    assert Path(root / MONEY).is_file()
