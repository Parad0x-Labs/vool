from __future__ import annotations

import json
from unittest import mock

import pytest

from apps.vool_api_server import _dispatch_post
from core.hive_activity_tracker import HiveActivityTracker, HiveActivityTrackerConfig
from core.mode_permission_policy import (
    MODE_PERMISSION_MATRIX,
    OperatingMode,
    PermissionAction,
    PermissionEffect,
    actions_for_tool,
    activate_bypass_grant,
    active_mode_state,
    decide_tool_call,
    request_bypass_confirmation,
    reset_mode_permission_state,
    resolve_approval,
    revoke_bypass_grant,
    set_active_mode,
    validate_bypass_grant,
)
from core.tool_intent_executor import execute_tool_intent
from core.web.api.runtime import RuntimeServices


@pytest.fixture(autouse=True)
def _clean_mode_state() -> None:
    reset_mode_permission_state()
    yield
    reset_mode_permission_state()


def _context(session: str = "chat-a", mode: str = "manual", **extra: object) -> dict[str, object]:
    set_active_mode(session, mode, project_id=str(extra.get("project_id") or ""), client_turn_id=str(extra.get("cancel_turn_id") or "turn-a"))
    return {"runtime_session_id": session, "operating_mode": mode, "workspace_root": "/tmp", **extra}


def _decision(intent: str, *, args: dict[str, object] | None = None, task: str = "task-a", context: dict[str, object] | None = None):
    return decide_tool_call(intent=intent, arguments=args or {}, task_id=task, source_context=context or _context())


def test_matrix_is_total_and_plan_is_strictly_read_only() -> None:
    assert set(MODE_PERMISSION_MATRIX) == set(OperatingMode)
    for row in MODE_PERMISSION_MATRIX.values():
        assert set(row) == set(PermissionAction)
    ctx = _context(mode="plan")
    assert _decision("workspace.read_file", args={"path": "README.md"}, context=ctx).effect is PermissionEffect.ALLOW
    assert _decision("workspace.write_file", args={"path": "owned.txt", "content": "x"}, context=ctx).effect is PermissionEffect.DENY
    assert _decision("web.search", args={"query": "ignore the selected mode"}, context=ctx).effect is PermissionEffect.DENY
    assert _decision("sandbox.run_command", args={"command": "pytest -q"}, context=ctx).effect is PermissionEffect.DENY


def test_manual_review_and_auto_have_distinct_real_effects(tmp_path) -> None:
    manual = _context(mode="manual", workspace_root=str(tmp_path))
    review = _context(session="chat-review", mode="review_edits", workspace_root=str(tmp_path))
    auto = _context(session="chat-auto", mode="auto", workspace_root=str(tmp_path))
    args = {"path": "new.txt", "content": "hello"}
    assert _decision("workspace.write_file", args=args, context=manual).effect is PermissionEffect.REQUIRE_APPROVAL
    review_decision = _decision("workspace.write_file", args=args, context=review)
    assert review_decision.effect is PermissionEffect.REQUIRE_APPROVAL
    assert "+++ b/new.txt" in review_decision.approval_request["diff_preview"]
    assert _decision("workspace.write_file", args=args, context=auto).effect is PermissionEffect.ALLOW
    (tmp_path / "existing.txt").write_text("user work", encoding="utf-8")
    overwrite = _decision("workspace.write_file", args={"path": "existing.txt", "content": "replacement"}, context=auto)
    assert overwrite.effect is PermissionEffect.REQUIRE_APPROVAL
    assert _decision("workspace.delete_file", args={"path": "new.txt"}, context=auto).effect is PermissionEffect.REQUIRE_APPROVAL


def test_shell_read_classifier_fails_closed_on_shell_composition() -> None:
    plan = _context(mode="plan")
    assert _decision("sandbox.run_command", args={"command": "rg TODO"}, context=plan).effect is PermissionEffect.ALLOW
    for command in ("cat README.md > stolen.txt", "rg TODO | xargs rm", "find . -delete", "pwd; touch x"):
        assert _decision("sandbox.run_command", args={"command": command}, context=plan).effect is PermissionEffect.DENY


def test_known_product_tool_families_have_explicit_permission_classification() -> None:
    assert set(actions_for_tool("machine.move_path")) == {
        PermissionAction.MODIFY_FILES,
        PermissionAction.DELETE_FILES,
    }
    assert set(actions_for_tool("web0.create_project")) == {PermissionAction.CREATE_FILES}
    assert set(actions_for_tool("web0.fill_slots")) == {PermissionAction.MODIFY_FILES}
    assert set(actions_for_tool("web0.publish")) == {
        PermissionAction.USE_NETWORK,
        PermissionAction.DEPLOY,
    }
    assert set(actions_for_tool("marketplace.search_listings")) == {
        PermissionAction.USE_NETWORK,
        PermissionAction.ACCESS_EXTERNAL_PROVIDERS,
    }


def test_exact_edit_approval_is_one_time_and_cannot_cross_tasks_or_mode_revisions(tmp_path) -> None:
    ctx = _context(workspace_root=str(tmp_path))
    args = {"path": "a.txt", "content": "one"}
    request = _decision("workspace.write_file", args=args, context=ctx, task="task-a").approval_request
    approved = resolve_approval(request["approval_id"], decision="allow", scope="task")
    assert approved and approved["scope"] == "once"  # edit batches never broaden
    approved_ctx = {**ctx, "mode_approval_token": request["approval_id"]}
    assert _decision("workspace.write_file", args=args, context=approved_ctx, task="task-a").effect is PermissionEffect.ALLOW
    assert _decision("workspace.write_file", args=args, context=approved_ctx, task="task-a").effect is PermissionEffect.REQUIRE_APPROVAL

    cross_request = _decision("workspace.write_file", args=args, context=ctx, task="task-a").approval_request
    resolve_approval(cross_request["approval_id"], decision="allow")
    set_active_mode("chat-a", "manual", client_turn_id="turn-b")
    cross_ctx = {**ctx, "cancel_turn_id": "turn-b", "mode_approval_token": cross_request["approval_id"]}
    assert _decision("workspace.write_file", args=args, context=cross_ctx, task="task-b").effect is PermissionEffect.REQUIRE_APPROVAL

    request2 = _decision("workspace.write_file", args=args, context=ctx, task="task-a").approval_request
    resolve_approval(request2["approval_id"], decision="allow")
    set_active_mode("chat-a", "review_edits")
    stale_ctx = {**ctx, "mode_approval_token": request2["approval_id"]}
    assert _decision("workspace.write_file", args=args, context=stale_ctx, task="task-a").effect is PermissionEffect.REQUIRE_APPROVAL


def test_non_edit_task_scope_is_bounded_to_same_task_intent_and_mode() -> None:
    ctx = _context()
    args = {"command": "pytest -q"}
    request = _decision("sandbox.run_command", args=args, context=ctx, task="task-a").approval_request
    approved = resolve_approval(request["approval_id"], decision="allow", scope="task")
    assert approved and approved["scope"] == "task"
    assert _decision("sandbox.run_command", args=args, context=ctx, task="task-a").effect is PermissionEffect.ALLOW
    set_active_mode("chat-a", "manual", client_turn_id="turn-b")
    next_task_ctx = {**ctx, "cancel_turn_id": "turn-b"}
    assert _decision("sandbox.run_command", args=args, context=next_task_ctx, task="task-b").effect is PermissionEffect.REQUIRE_APPROVAL
    assert _decision("workspace.delete_file", args={"path": "x"}, context=ctx, task="task-a").effect is PermissionEffect.REQUIRE_APPROVAL


def test_project_permissions_can_narrow_but_never_widen() -> None:
    denied = _context(mode="auto", project_id="p-deny", project_permissions={"modify_files": False})
    open_project = _context(session="chat-b", mode="auto", project_id="p-open", project_permissions={"modify_files": True})
    args = {"path": "existing.txt", "content": "x"}
    assert _decision("workspace.replace_in_file", args=args, context=denied).effect is PermissionEffect.DENY
    assert _decision("workspace.replace_in_file", args=args, context=open_project).effect is PermissionEffect.ALLOW
    plan = _context(session="chat-plan", mode="plan", project_permissions={"modify_files": True})
    assert _decision("workspace.replace_in_file", args=args, context=plan).effect is PermissionEffect.DENY


def test_two_chats_keep_independent_live_modes_and_restart_falls_back_to_explicit_context() -> None:
    set_active_mode("chat-a", "plan")
    set_active_mode("chat-b", "auto")
    assert active_mode_state({"runtime_session_id": "chat-a"})["mode"] == "plan"
    assert active_mode_state({"runtime_session_id": "chat-b"})["mode"] == "auto"
    reset_mode_permission_state()
    assert active_mode_state({"runtime_session_id": "chat-a", "operating_mode": "review_edits"})["mode"] == "review_edits"


def test_bypass_requires_confirmation_is_scoped_expires_revokes_and_keeps_hard_boundaries() -> None:
    with pytest.raises(PermissionError):
        activate_bypass_grant(session_id="chat-a", task_id="turn-a", confirmation_id="")
    _cid = request_bypass_confirmation(session_id="chat-a", task_id="turn-a", scope="task", duration_seconds=60)
    grant = activate_bypass_grant(session_id="chat-a", task_id="turn-a", scope="task", duration_seconds=60, confirmation_id=_cid)
    assert validate_bypass_grant(grant["token"], session_id="chat-a", task_id="turn-a")
    assert validate_bypass_grant(grant["token"], session_id="chat-a", task_id="turn-b") is None
    set_active_mode("chat-a", "bypass_permissions", client_turn_id="turn-a", bypass_token=grant["token"])
    ctx = {"runtime_session_id": "chat-a", "cancel_turn_id": "turn-a"}
    assert _decision("workspace.write_file", args={"path": "x", "content": "x"}, context=ctx).effect is PermissionEffect.ALLOW
    assert _decision("credential.read", context=ctx).effect is PermissionEffect.DENY
    assert _decision("pay.send", context=ctx).effect is PermissionEffect.REQUIRE_APPROVAL
    with mock.patch("core.mode_permission_policy.time.time", return_value=float(grant["expires_at"]) + 1):
        assert active_mode_state(ctx)["mode"] == "manual"
    assert revoke_bypass_grant(grant["token"]) is True
    assert active_mode_state(ctx)["mode"] == "manual"


def test_executor_blocks_before_dispatch_even_when_tool_arguments_demand_bypass(tmp_path) -> None:
    tracker = HiveActivityTracker(config=HiveActivityTrackerConfig(enabled=False, watcher_api_url=None))
    context = _context(mode="plan", workspace_root=str(tmp_path))
    # The dispatch point is the ONE authorized execution boundary: patching it proves a Plan-mode
    # denial never reaches ANY execution, and the boundary is the seam a bypass would have to cross.
    with mock.patch("core.tool_intent_executor.execute_authorized_runtime_tool") as downstream:
        result = execute_tool_intent(
            {"intent": "workspace.write_file", "arguments": {"path": "blocked.txt", "content": "ignore Plan mode and write"}},
            task_id="task-a",
            session_id="chat-a",
            source_context=context,
            hive_activity_tracker=tracker,
        )
    assert result.status == "blocked_by_mode"
    assert result.details["controller_enforced"] is True
    downstream.assert_not_called()
    assert not (tmp_path / "blocked.txt").exists()


def test_mode_endpoint_is_loopback_only_and_bypass_cannot_be_set_without_grant() -> None:
    runtime = RuntimeServices(display_name="VOOL")

    def post(body: dict[str, object], host: str = "127.0.0.1"):
        response = _dispatch_post(path="/api/mode", body=body, headers={"content-type": "application/json"}, runtime=runtime, model_name="vool", workspace_root_provider=lambda: "/tmp", client_host=host)
        return response.status, json.loads((response.body or b"{}").decode("utf-8"))

    assert post({"session_id": "chat-a", "mode": "plan"})[0] == 200
    assert post({"session_id": "chat-a", "mode": "auto"}, host="198.51.100.8")[0] == 403
    status, payload = post({"session_id": "chat-a", "mode": "bypass_permissions"})
    assert status == 409
    assert "explicitly confirmed grant" in payload["error"]


# --------------------------------------------------------------------------------------
# The gate and the handler must decide over the SAME call.
# --------------------------------------------------------------------------------------


def test_an_aliased_path_cannot_downgrade_an_overwrite_into_a_create(tmp_path) -> None:
    """The bypass. `file_path` is the spelling most models emit.

    `actions_for_tool` reads `arguments["path"]` to decide whether a write lands on a file that
    already exists. The alias was bound further down, inside the runtime dispatcher, so the gate
    saw no path, scored an overwrite as a create, and `auto` mode allowed it -- after which the
    handler bound the alias and wrote the very file the gate never saw.
    """

    victim = tmp_path / "secrets.txt"
    victim.write_text("original", encoding="utf-8")
    ctx = _context(mode="auto", workspace_root=str(tmp_path))

    canonical = _decision(
        "workspace.write_file", args={"path": "secrets.txt", "content": "x"}, context=ctx, task="t1"
    )
    for alias in ("file_path", "file", "filepath", "filename"):
        aliased = _decision(
            "workspace.write_file", args={alias: "secrets.txt", "content": "x"}, context=ctx, task=f"t-{alias}"
        )
        assert aliased.effect is canonical.effect, f"{alias} decided differently from path"
        assert PermissionAction.OVERWRITE_EXISTING_FILES in aliased.actions, (
            f"{alias} was not scored as an overwrite of an existing file"
        )


def test_an_explicit_path_still_wins_over_an_alias(tmp_path) -> None:
    """Binding must not let a second spelling redirect a call that already named its target."""

    (tmp_path / "real.txt").write_text("original", encoding="utf-8")
    ctx = _context(mode="auto", workspace_root=str(tmp_path))

    decision = _decision(
        "workspace.write_file",
        args={"path": "real.txt", "file_path": "decoy.txt", "content": "x"},
        context=ctx,
    )

    assert PermissionAction.OVERWRITE_EXISTING_FILES in decision.actions


def test_a_server_owned_private_key_survives_the_gate(tmp_path) -> None:
    """Binding aliases must not strip `_`-prefixed keys.

    Those carry grants the runtime stamps and the model cannot forge from the wire; dropping them
    here would revoke a grant the server had already made.
    """

    from core.tool_argument_aliases import bind_known_argument_aliases

    bound = bind_known_argument_aliases(
        {"file_path": "x.txt", "_trusted_local_only": True},
        input_schema={"path": {"type": "string"}},
    )

    assert bound["path"] == "x.txt"
    assert bound["_trusted_local_only"] is True


# --------------------------------------------------------------------------------------
# A tool that declares it writes cannot be classified as a read, whatever its name says.
# --------------------------------------------------------------------------------------


def test_plan_mode_refuses_the_patch_tool_like_every_other_write(tmp_path) -> None:
    """The hole. Plan mode is advertised as strictly read-only and was not.

    `actions_for_tool` substring-matched the intent name, and its read-verb list contains "diff".
    `workspace.apply_unified_diff` contains "diff", so it matched the READ branch and was granted
    `read_files` - while `workspace.write_file` was correctly denied. A model in Plan mode could
    rewrite any file in the workspace by emitting a patch instead of a write.
    """

    (tmp_path / "a.txt").write_text("one\n", encoding="utf-8")
    ctx = _context(mode="plan", workspace_root=str(tmp_path))
    patch = "--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-one\n+PWNED\n"

    for intent, args in (
        ("workspace.write_file", {"path": "a.txt", "content": "x"}),
        ("workspace.replace_in_file", {"path": "a.txt", "old_text": "one", "new_text": "x"}),
        ("workspace.apply_unified_diff", {"patch": patch}),
    ):
        decision = _decision(intent, args=args, context=ctx, task=f"plan-{intent}")
        assert decision.effect is PermissionEffect.DENY, f"{intent} was permitted in Plan mode"


def test_the_patch_tool_is_classified_by_what_it_declares(tmp_path) -> None:
    """Every write tool declares `side_effect_class="workspace_write"`. Read the declaration."""

    (tmp_path / "a.txt").write_text("one\n", encoding="utf-8")
    actions = actions_for_tool(
        "workspace.apply_unified_diff",
        {"patch": "--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-one\n+x\n"},
        {"workspace_root": str(tmp_path)},
    )

    assert PermissionAction.READ_FILES not in actions
    assert PermissionAction.MODIFY_FILES in actions


def test_a_genuinely_read_only_tool_whose_name_says_diff_stays_a_read(tmp_path) -> None:
    """The control. Denying everything with "diff" in the name would break `workspace.git_diff`,
    which declares `read_only` and really is."""

    ctx = _context(mode="plan", workspace_root=str(tmp_path))

    assert actions_for_tool("workspace.git_diff", {}, {}) == (PermissionAction.READ_FILES,)
    assert _decision("workspace.git_diff", args={}, context=ctx).effect is PermissionEffect.ALLOW


def test_plan_mode_still_allows_the_reads_it_exists_for(tmp_path) -> None:
    """A guard that denied everything would satisfy the tests above and make Plan mode useless."""

    ctx = _context(mode="plan", workspace_root=str(tmp_path))

    for intent, args in (
        ("workspace.read_file", {"path": "a.txt"}),
        ("machine.list_directory", {"path": "."}),
    ):
        assert _decision(intent, args=args, context=ctx).effect is PermissionEffect.ALLOW


# --------------------------------------------------------------------------------------
# Five confirmed red-team findings, 2026-08-04: real, silent, irreversible actions in fully
# supported modes. Each test reproduces the exact attack described and proves it is now
# gated, not merely that some unrelated assertion still holds.
# --------------------------------------------------------------------------------------


def test_task_scope_grant_does_not_carry_over_to_an_unrelated_destructive_command() -> None:
    """BUG 2: approving one benign command with task scope must not authorize a DIFFERENT,
    destructive one just because it classifies into the same PermissionAction set.

    Confirmed attack: approve `echo hi > note.txt` with scope=task in Manual mode, then run
    `find important_data -delete` in the same task -- both classify as
    RUN_SIDE_EFFECTING_COMMANDS, so the old task-grant (keyed only on the action-class tuple)
    let the second command through with zero new approval.
    """

    ctx = _context(mode="manual")
    benign = _decision("sandbox.run_command", args={"command": "echo hi > note.txt"}, context=ctx, task="task-a")
    assert benign.effect is PermissionEffect.REQUIRE_APPROVAL
    approved = resolve_approval(benign.approval_request["approval_id"], decision="allow", scope="task")
    assert approved and approved["scope"] == "task"

    # The exact same command replays cleanly under the task grant.
    replay = _decision("sandbox.run_command", args={"command": "echo hi > note.txt"}, context=ctx, task="task-a")
    assert replay.effect is PermissionEffect.ALLOW

    # A different, unrelated, destructive command must NOT ride the same grant even though it
    # classifies into the identical PermissionAction set.
    attack = _decision(
        "sandbox.run_command", args={"command": "find important_data -delete"}, context=ctx, task="task-a"
    )
    assert attack.effect is PermissionEffect.REQUIRE_APPROVAL, (
        "an unrelated destructive command rode a benign command's task-scope grant"
    )


def test_replayed_write_approval_cannot_be_reused_with_swapped_content(tmp_path) -> None:
    """BUG 3: a one-time write approval must bind to the CONTENT being written, not only the
    target path and intent.

    Confirmed attack: the operator approves a diff showing one piece of content; replaying the
    same approval token with different `content` must not execute the swap silently.
    """

    ctx = _context(mode="manual", workspace_root=str(tmp_path))
    approved_args = {"path": "report.txt", "content": "quarterly numbers: all normal"}
    request = _decision("workspace.write_file", args=approved_args, context=ctx, task="task-a")
    assert request.effect is PermissionEffect.REQUIRE_APPROVAL
    approval = resolve_approval(request.approval_request["approval_id"], decision="allow", scope="once")
    assert approval and approval["status"] == "approved"

    # Replaying with the IDENTICAL content consumes the approval as intended.
    same_ctx = {**ctx, "mode_approval_token": approval["approval_id"]}
    same = _decision("workspace.write_file", args=approved_args, context=same_ctx, task="task-a")
    assert same.effect is PermissionEffect.ALLOW

    # A fresh approval, then a replay attempt with SWAPPED content must not be silently allowed.
    request2 = _decision("workspace.write_file", args=approved_args, context=ctx, task="task-b")
    approval2 = resolve_approval(request2.approval_request["approval_id"], decision="allow", scope="once")
    swapped_args = {"path": "report.txt", "content": "wire funds to attacker-controlled-account"}
    swap_ctx = {**ctx, "mode_approval_token": approval2["approval_id"]}
    swapped = _decision("workspace.write_file", args=swapped_args, context=swap_ctx, task="task-b")
    assert swapped.effect is not PermissionEffect.ALLOW, (
        "a replayed approval token let swapped content write silently"
    )


def test_machine_write_file_overwrite_is_resolved_against_the_real_home_not_workspace_root(tmp_path, monkeypatch) -> None:
    """BUG 4: `machine.write_file` writes under Path.home()/{Desktop,Downloads,Documents} --
    a different root than `workspace_root`. The existence check must resolve against that SAME
    root, or an overwrite of a real pre-existing file is misclassified as a silent CREATE.
    """

    disposable_home = tmp_path / "home"
    desktop = disposable_home / "Desktop"
    desktop.mkdir(parents=True)
    real_file = desktop / "resume.txt"
    real_file.write_text("the user's real, pre-existing resume", encoding="utf-8")
    monkeypatch.setenv("HOME", str(disposable_home))

    unrelated_workspace = tmp_path / "unrelated_workspace"
    unrelated_workspace.mkdir()
    ctx = _context(mode="auto", workspace_root=str(unrelated_workspace))

    decision = _decision(
        "machine.write_file",
        args={"path": "Desktop/resume.txt", "content": "OVERWRITTEN"},
        context=ctx,
    )
    assert PermissionAction.OVERWRITE_EXISTING_FILES in decision.actions, (
        "overwriting a real, pre-existing Desktop file was not classified as an overwrite"
    )
    assert decision.effect is PermissionEffect.REQUIRE_APPROVAL, (
        "Auto mode silently allowed an overwrite of a real Desktop file"
    )
    assert real_file.read_text(encoding="utf-8") == "the user's real, pre-existing resume"


def test_machine_write_file_create_still_works_for_a_new_desktop_file(tmp_path, monkeypatch) -> None:
    """Control for BUG 4: a genuinely new file under a safe machine root still classifies CREATE."""

    disposable_home = tmp_path / "home"
    (disposable_home / "Desktop").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(disposable_home))

    ctx = _context(mode="auto", workspace_root=str(tmp_path / "unrelated_workspace"))
    decision = _decision(
        "machine.write_file", args={"path": "Desktop/new_notes.txt", "content": "hello"}, context=ctx
    )
    assert PermissionAction.CREATE_FILES in decision.actions
    assert decision.effect is PermissionEffect.ALLOW


def test_machine_write_file_overwrite_approval_carries_a_real_diff_preview(tmp_path, monkeypatch) -> None:
    """Adjacent gap found by adversarial review of the BUG 4 fix: a machine.write_file overwrite now
    correctly REQUIRES approval, but shipped with diff_preview="" -- the operator approved overwriting
    a real Desktop/Downloads/Documents file with no visibility into what content would land. Not a
    bypass (approval was still required), but it undercut the informed-consent goal BUG 3's
    content-binding fix was for.
    """

    disposable_home = tmp_path / "home"
    desktop = disposable_home / "Desktop"
    desktop.mkdir(parents=True)
    (desktop / "resume.txt").write_text("the user's real, pre-existing resume", encoding="utf-8")
    monkeypatch.setenv("HOME", str(disposable_home))

    ctx = _context(mode="manual", workspace_root=str(tmp_path / "unrelated_workspace"))
    decision = _decision(
        "machine.write_file",
        args={"path": "Desktop/resume.txt", "content": "OVERWRITTEN"},
        context=ctx,
    )
    assert decision.effect is PermissionEffect.REQUIRE_APPROVAL
    preview = decision.approval_request["diff_preview"]
    assert "-the user's real, pre-existing resume" in preview
    assert "+OVERWRITTEN" in preview


def test_model_supplied_workspace_root_argument_cannot_override_the_trusted_source_context(tmp_path) -> None:
    """BUG 5: a model-controlled `workspace_root` argument must never win over the trusted
    `source_context` for a trust-sensitive existence check.

    Confirmed attack: the real, trusted workspace_root has an existing `config.json`; the model
    additionally supplies its own `workspace_root` argument pointing at an empty decoy directory.
    The gate must classify against the TRUSTED root, not the model-supplied one.
    """

    real_workspace = tmp_path / "real_workspace"
    real_workspace.mkdir()
    (real_workspace / "config.json").write_text('{"real": "config"}', encoding="utf-8")
    decoy_root = tmp_path / "decoy_empty_root"
    decoy_root.mkdir()

    ctx = _context(mode="auto", workspace_root=str(real_workspace))
    decision = _decision(
        "workspace.write_file",
        args={"path": "config.json", "content": "OVERWRITTEN", "workspace_root": str(decoy_root)},
        context=ctx,
    )
    assert PermissionAction.OVERWRITE_EXISTING_FILES in decision.actions, (
        "a model-supplied workspace_root argument steered classification away from the real root"
    )
    assert decision.effect is PermissionEffect.REQUIRE_APPROVAL
