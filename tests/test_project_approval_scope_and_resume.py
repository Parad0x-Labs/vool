"""Approvals an operator can actually live with: a project scope, a resume that resumes, a ledger.

Three measured failures drove this file.

1. A multi-step editing task asked for approval on EVERY write. `resolve_approval` forced the scope
   to "once" whenever the action was an edit batch and `scope_options` never offered anything wider,
   so an in-project edit could not escalate at all. The only escape was switching the whole session to
   bypass permissions -- trading one prompt for no prompts.
2. An approved turn was re-run from the top. The approval fingerprint hashed the exact argument dict,
   so a re-plan that differed by a byte of content missed the token the client had already spent, and
   a second prompt appeared for the action the operator had just approved. When the re-plan WAS
   identical, the tool loop's duplicate guard read it as a loop and broke to grounded synthesis --
   the approved action never ran.
3. 893 routing decisions were logged in one day and exactly one mentioned an approval, so none of the
   above was visible in the ledger.

Every test here fails against the code as it was.

UPDATE 2026-08-04: (2) went too far. Excluding file content from the fingerprint entirely made a
legitimate byte-different replan and an attacker replaying the same approval token with swapped
`content` look identical to `decide_tool_call` -- there is no server-side signal that tells them
apart, so tolerating "differs by a byte" also tolerated "content silently swapped after approval"
(confirmed red-team finding; the operator approves a diff showing one thing and the file on disk
ends up containing something else). The fingerprint is byte-exact on content again; a replan must
reproduce the approved content to resume without a new prompt. See
`test_an_identical_replan_of_an_approved_edit_still_matches_the_token` and
`test_a_replan_that_changes_even_one_byte_of_approved_content_requires_a_new_approval` below.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest import mock

import pytest

from core import project_store, routing_decision_log, runtime_paths
from core.mode_permission_policy import (
    PermissionEffect,
    decide_tool_call,
    project_approval_state,
    reset_mode_permission_state,
    resolve_approval,
    revoke_project_approval,
    set_active_mode,
)
from core.task_event_model import build_task_event
from core.tool_intent_executor import ToolIntentExecution
from core.vool_chat_page import render_vool_chat_html


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    runtime_paths.configure_runtime_home(tmp_path / "home")
    reset_mode_permission_state()
    yield
    reset_mode_permission_state()
    runtime_paths.configure_runtime_home(None)


def _bind_project(tmp_path, name: str = "repo") -> tuple[str, str]:
    folder = tmp_path / name
    folder.mkdir(exist_ok=True)
    ok, project = project_store.create_project(name, str(folder))
    assert ok
    return str(project["id"]), str(project["root"])


def _context(session: str, root: str, project_id: str, *, mode: str = "manual", turn: str = "turn-a", **extra):
    set_active_mode(session, mode, project_id=project_id, client_turn_id=turn)
    return {
        "runtime_session_id": session,
        "operating_mode": mode,
        "workspace_root": root,
        "workspace": root,
        "project_id": project_id,
        "cancel_turn_id": turn,
        **extra,
    }


def _decide(intent: str, args: dict, context: dict, task: str = "task-a"):
    return decide_tool_call(intent=intent, arguments=args, task_id=task, source_context=context)


# --------------------------------------------------------------------------- #
# A: a project-scoped grant that covers low-risk work and nothing else
# --------------------------------------------------------------------------- #
def test_an_in_project_edit_can_escalate_to_the_project_and_stops_reprompting(tmp_path) -> None:
    project_id, root = _bind_project(tmp_path)
    first = _context("chat-a", root, project_id)
    request = _decide("workspace.write_file", {"path": "one.txt", "content": "1"}, first).approval_request
    assert request is not None
    # The bug: an edit batch was offered "once" and nothing else, so this list read ["once"].
    assert "project" in request["scope_options"]

    granted = resolve_approval(request["approval_id"], decision="allow", scope="project")
    assert granted and granted["scope"] == "project"

    # A DIFFERENT chat, a different turn, a different file: no prompt, no token, still allowed.
    later = _context("chat-b", root, project_id, turn="turn-z")
    assert _decide("workspace.write_file", {"path": "two.txt", "content": "2"}, later, task="task-z").effect is PermissionEffect.ALLOW
    assert _decide("workspace.replace_in_file", {"path": "nested/three.txt", "old_text": "a", "new_text": "b"}, later).effect is PermissionEffect.ALLOW


def test_the_project_grant_never_covers_deletes_moves_sends_spend_or_paths_outside_the_root(tmp_path) -> None:
    project_id, root = _bind_project(tmp_path)
    other = tmp_path / "not-the-project"
    other.mkdir()
    ctx = _context("chat-a", root, project_id)
    request = _decide("workspace.write_file", {"path": "one.txt", "content": "1"}, ctx).approval_request
    assert resolve_approval(request["approval_id"], decision="allow", scope="project")["scope"] == "project"

    for intent, args in (
        ("workspace.delete_file", {"path": "one.txt"}),
        ("workspace.move_path", {"path": "one.txt", "destination": "two.txt"}),
        ("email.send", {"recipient": "someone@example.com"}),
        ("sandbox.run_command", {"command": "rm -rf build"}),
    ):
        assert _decide(intent, args, ctx).effect is not PermissionEffect.ALLOW, intent

    # A write that lands outside the bound root is not an in-project write, absolute or relative.
    assert _decide("workspace.write_file", {"path": str(other / "escape.txt"), "content": "x"}, ctx).effect is PermissionEffect.REQUIRE_APPROVAL
    assert _decide("workspace.write_file", {"path": "../not-the-project/escape.txt", "content": "x"}, ctx).effect is PermissionEffect.REQUIRE_APPROVAL


def test_money_stays_gated_under_the_project_grant_exactly_as_it_is_under_bypass(tmp_path) -> None:
    project_id, root = _bind_project(tmp_path)
    ctx = _context("chat-a", root, project_id)
    request = _decide("workspace.write_file", {"path": "one.txt", "content": "1"}, ctx).approval_request
    resolve_approval(request["approval_id"], decision="allow", scope="project")

    spend = _decide("pay.send", {"amount": "5"}, ctx)
    assert spend.effect is PermissionEffect.REQUIRE_APPROVAL
    # ... and a financial action can never be escalated to the project either.
    assert "project" not in spend.approval_request["scope_options"]


def test_the_project_grant_cannot_widen_a_read_only_mode_or_a_project_deny(tmp_path) -> None:
    project_id, root = _bind_project(tmp_path)
    ctx = _context("chat-a", root, project_id)
    request = _decide("workspace.write_file", {"path": "one.txt", "content": "1"}, ctx).approval_request
    resolve_approval(request["approval_id"], decision="allow", scope="project")

    plan = _context("chat-plan", root, project_id, mode="plan")
    assert _decide("workspace.write_file", {"path": "one.txt", "content": "1"}, plan).effect is PermissionEffect.DENY

    # `project_permissions` remains deny-only: a standing grant must not resurrect what it forbids.
    denied = _context("chat-deny", root, project_id, project_permissions={"modify_files": False})
    assert _decide("workspace.replace_in_file", {"path": "one.txt", "old_text": "a", "new_text": "b"}, denied).effect is PermissionEffect.DENY


def test_the_grant_is_server_side_persistent_and_revocable(tmp_path) -> None:
    project_id, root = _bind_project(tmp_path)
    ctx = _context("chat-a", root, project_id)
    request = _decide("workspace.write_file", {"path": "one.txt", "content": "1"}, ctx).approval_request
    resolve_approval(request["approval_id"], decision="allow", scope="project")

    # It lives on the project entry, not in a process variable: a restart keeps it.
    assert project_store.get_project_low_risk_approval(project_id).get("granted_at")
    reset_mode_permission_state()
    assert project_approval_state(project_id).get("granted_at")
    assert _decide("workspace.write_file", {"path": "two.txt", "content": "2"}, ctx).effect is PermissionEffect.ALLOW

    assert revoke_project_approval(project_id) is True
    assert _decide("workspace.write_file", {"path": "two.txt", "content": "2"}, ctx).effect is PermissionEffect.REQUIRE_APPROVAL


def test_an_unbound_chat_is_never_offered_a_project_scope(tmp_path) -> None:
    ctx = _context("chat-a", str(tmp_path), "")
    request = _decide("workspace.write_file", {"path": "one.txt", "content": "1"}, ctx).approval_request
    assert "project" not in request["scope_options"]
    # And a scope the controller never offered cannot be claimed by the client.
    assert resolve_approval(request["approval_id"], decision="allow", scope="project")["scope"] == "once"


def test_the_project_scope_survives_the_task_event_projection() -> None:
    payload = build_task_event(
        {
            "event_type": "tool_preview",
            "approval_request": {
                "approval_id": "a1",
                "task_id": "t1",
                "intent": "workspace.write_file",
                "action": "Run workspace.write_file",
                "scope_options": ["once", "project", "nonsense"],
            },
        }
    )
    # The event model dropped anything that was not "once"/"task", so the button never rendered.
    assert payload["approval"]["scope_options"] == ["once", "project"]


def test_the_permission_bar_offers_the_project_button_without_a_browser_side_always_allow() -> None:
    html = render_vool_chat_html()
    assert 'id="permProject"' in html
    assert "resolvePermission('allow', 'project')" in html
    assert "indexOf('project') >= 0" in html
    # The grant is server-side. A blanket browser flag is exactly what this must not become.
    assert "localStorage.setItem('vool_allow_" not in html


# --------------------------------------------------------------------------- #
# B: an approved turn resumes instead of asking again
# --------------------------------------------------------------------------- #
def test_an_identical_replan_of_an_approved_edit_still_matches_the_token(tmp_path) -> None:
    """Superseded 2026-08-04: this used to tolerate a BYTE-DIFFERENT replan (see the red-team
    finding referenced below) and has been narrowed to what is actually safe to allow -- a
    replan that reproduces the exact approved content still resumes without a second prompt.

    A content-blind fingerprint here is a confirmed vulnerability, not just a convenience: from
    inside `decide_tool_call` a legitimate "re-plan differs by a trailing byte" and an attacker
    replaying the same approval token with swapped `content` are the SAME shape -- same token,
    same target, first consumption, different bytes than the diff the operator actually approved.
    There is no server-side signal that tells them apart, so the fingerprint must be byte-exact
    on content; see `test_replayed_write_approval_cannot_be_reused_with_swapped_content` in
    `tests/test_mode_permission_policy.py` for the attack this closes.
    """
    project_id, root = _bind_project(tmp_path)
    ctx = _context("chat-a", root, project_id)
    request = _decide("workspace.write_file", {"path": "one.txt", "content": "hello"}, ctx).approval_request
    assert resolve_approval(request["approval_id"], decision="allow")["scope"] == "once"

    resumed = {**ctx, "mode_approval_token": request["approval_id"]}
    # The re-planned turn writes back the EXACT content the operator approved.
    assert _decide("workspace.write_file", {"path": "one.txt", "content": "hello"}, resumed).effect is PermissionEffect.ALLOW
    # Identical retries in the same task retain the reviewed authority; a new
    # task does not inherit it. Physical duplicate effects are gated downstream.
    assert _decide("workspace.write_file", {"path": "one.txt", "content": "hello"}, resumed).effect is PermissionEffect.ALLOW
    set_active_mode("chat-a", "manual", project_id=project_id, client_turn_id="new-turn")
    assert _decide("workspace.write_file", {"path": "one.txt", "content": "hello"},
                   {**resumed, "cancel_turn_id": "new-turn"}, task="new-task").effect is PermissionEffect.REQUIRE_APPROVAL


def test_a_replan_that_changes_even_one_byte_of_approved_content_requires_a_new_approval(tmp_path) -> None:
    """The other half of the narrowing above: content is now part of what was approved, so a
    replan differing by even a trailing newline is a NEW action, not a resume of the old one.
    """
    project_id, root = _bind_project(tmp_path)
    ctx = _context("chat-a", root, project_id)
    request = _decide("workspace.write_file", {"path": "one.txt", "content": "hello"}, ctx).approval_request
    resolve_approval(request["approval_id"], decision="allow")

    resumed = {**ctx, "mode_approval_token": request["approval_id"]}
    assert _decide("workspace.write_file", {"path": "one.txt", "content": "hello\n"}, resumed).effect is PermissionEffect.REQUIRE_APPROVAL


def test_the_resolved_action_fingerprint_stays_bound_to_session_task_target_and_mode(tmp_path) -> None:
    project_id, root = _bind_project(tmp_path)
    ctx = _context("chat-a", root, project_id)

    def approved_token(path: str = "one.txt") -> str:
        # Each boundary control starts with a fresh approval, not an exact-action
        # grant left by the previous control in this same task.
        reset_mode_permission_state()
        set_active_mode("chat-a", "manual", project_id=project_id, client_turn_id="turn-a")
        request = _decide("workspace.write_file", {"path": path, "content": "x"}, ctx).approval_request
        resolve_approval(request["approval_id"], decision="allow")
        return str(request["approval_id"])

    # A different target file is a different action.
    other_target = {**ctx, "mode_approval_token": approved_token()}
    assert _decide("workspace.write_file", {"path": "elsewhere.txt", "content": "x"}, other_target).effect is PermissionEffect.REQUIRE_APPROVAL

    # A different task id.
    token = approved_token()
    set_active_mode("chat-a", "manual", project_id=project_id, client_turn_id="turn-b")
    next_task = {**ctx, "cancel_turn_id": "turn-b", "mode_approval_token": token}
    assert _decide("workspace.write_file", {"path": "one.txt", "content": "x"}, next_task, task="task-b").effect is PermissionEffect.REQUIRE_APPROVAL

    # A different session.
    token = approved_token()
    elsewhere = _context("chat-other", root, project_id, turn="turn-a")
    elsewhere["mode_approval_token"] = token
    assert _decide("workspace.write_file", {"path": "one.txt", "content": "x"}, elsewhere).effect is PermissionEffect.REQUIRE_APPROVAL

    # A mode change mid-approval.
    token = approved_token()
    set_active_mode("chat-a", "review_edits", project_id=project_id, client_turn_id="turn-a")
    assert _decide("workspace.write_file", {"path": "one.txt", "content": "x"}, {**ctx, "mode_approval_token": token}).effect is PermissionEffect.REQUIRE_APPROVAL


def test_command_and_message_approvals_are_still_byte_exact(tmp_path) -> None:
    project_id, root = _bind_project(tmp_path)
    ctx = _context("chat-a", root, project_id)
    request = _decide("sandbox.run_command", {"command": "pytest -q"}, ctx).approval_request
    resolve_approval(request["approval_id"], decision="allow")
    resumed = {**ctx, "mode_approval_token": request["approval_id"]}
    # Loosening the fingerprint must never let a different command ride an approval for another.
    assert _decide("sandbox.run_command", {"command": "pytest -q --exitfirst"}, resumed).effect is PermissionEffect.REQUIRE_APPROVAL
    assert _decide("sandbox.run_command", {"command": "pytest -q"}, resumed).effect is PermissionEffect.ALLOW


def test_the_client_holds_the_approval_token_until_the_server_acts_on_it() -> None:
    html = render_vool_chat_html()
    # The old body spent the token at send: `const token = _approvalToken; _approvalToken = '';`
    # DISPATCHER phase 1 moved the grant from one global into the granting chat's bucket, so the
    # hold-until-the-server-acts contract is now expressed per chat -- and a grant made in one
    # chat can no longer be attached to another chat's turn.
    assert "approval_token: owner.approvalToken," in html
    assert "function releaseApprovalTokenFor(chatId)" in html
    # Released only when the server superseded it, denied it, or the turn ended without asking again.
    assert "if (owner.approvalToken && owner.approvalToken !== a.approval_id) releaseApprovalTokenFor(chatId);" in html
    assert "if (!run.permission) releaseApprovalTokenFor(run.chatId);" in html


def _drive_tool_loop(*, approval_token: str) -> mock.Mock:
    """Run one tool-loop pass over a payload the checkpoint has already seen. Returns the exec mock."""
    from apps.vool_agent import VoolAgent

    agent = VoolAgent(backend_name="test-backend", device="test", persona_id="default")
    payload = {"intent": "workspace.write_file", "arguments": {"path": "a.txt", "content": "x"}}
    signature = json.dumps(payload, sort_keys=True, ensure_ascii=True, default=str)
    checkpoint = {"state": {"seen_tool_payloads": [signature], "loop_source_context": {}}}
    decision = SimpleNamespace(
        structured_output=dict(payload),
        provider_id="test-provider",
        validation_state="validated",
        trust_score=0.8,
        confidence=0.8,
    )
    executor = mock.Mock(
        return_value=ToolIntentExecution(
            handled=False,
            ok=False,
            status="stopped_for_test",
            tool_name="workspace.write_file",
        )
    )
    source_context = {
        "runtime_session_id": "chat-a",
        "runtime_checkpoint_resumed": True,
        "operating_mode": "auto",
        "mode_approval_token": approval_token,
    }
    with (
        mock.patch.object(agent, "_should_attempt_tool_intent", return_value=True),
        mock.patch.object(agent, "_should_keep_ai_first_chat_lane", return_value=False),
        mock.patch.object(agent, "_should_run_builder_controller", return_value=False),
        mock.patch.object(agent, "_runtime_checkpoint_id", return_value="cp-1"),
        mock.patch.object(agent, "_get_runtime_checkpoint", return_value=checkpoint),
        mock.patch.object(agent, "_record_runtime_tool_progress", return_value=None),
        mock.patch.object(agent, "_emit_runtime_event", return_value={}),
        mock.patch.object(agent, "_plan_tool_workflow", return_value=None),
        mock.patch.object(agent, "_execute_tool_intent", executor),
        mock.patch.object(agent.memory_router, "resolve_tool_intent", return_value=decision),
    ):
        agent._maybe_execute_model_tool_intent(
            task=SimpleNamespace(task_id="task-a"),
            effective_input="write the file",
            classification={"task_class": "unknown"},
            interpretation=None,
            context_result=None,
            persona=None,
            session_id="chat-a",
            source_context=source_context,
            surface="api",
        )
    return executor


def test_a_resumed_approval_is_exempt_from_the_duplicate_guard_and_actually_executes() -> None:
    # Without a token the repeat guard is right: this is a loop, stop it.
    assert _drive_tool_loop(approval_token="").call_count == 0
    # With one, the operator just approved this exact call. Breaking to grounded synthesis here is
    # how an approved action silently never ran.
    assert _drive_tool_loop(approval_token="approval-token-1").call_count == 1


# --------------------------------------------------------------------------- #
# C: the ledger can see an approval loop
# --------------------------------------------------------------------------- #
def _approval_families() -> list[str]:
    return [str(row.get("family") or "") for row in routing_decision_log.recent_decisions(200)]


def test_every_prompt_allow_deny_and_resume_lands_in_the_routing_ledger(tmp_path) -> None:
    project_id, root = _bind_project(tmp_path)
    ctx = _context("chat-a", root, project_id)

    request = _decide("workspace.write_file", {"path": "one.txt", "content": "1"}, ctx).approval_request
    assert "approval_required" in _approval_families()

    resolve_approval(request["approval_id"], decision="allow")
    assert "approval_allowed" in _approval_families()

    _decide("workspace.write_file", {"path": "one.txt", "content": "1"}, {**ctx, "mode_approval_token": request["approval_id"]})
    assert "approval_resumed" in _approval_families()

    denied = _decide("workspace.write_file", {"path": "two.txt", "content": "2"}, ctx).approval_request
    resolve_approval(denied["approval_id"], decision="deny")
    assert "approval_denied" in _approval_families()

    granted = _decide("workspace.write_file", {"path": "three.txt", "content": "3"}, ctx).approval_request
    resolve_approval(granted["approval_id"], decision="allow", scope="project")
    assert "approval_project_granted" in _approval_families()
    _decide("workspace.write_file", {"path": "four.txt", "content": "4"}, ctx)
    assert "approval_project_grant" in _approval_families()

    rows = [row for row in routing_decision_log.recent_decisions(200) if str(row.get("family") or "").startswith("approval")]
    assert rows and all(row.get("ts") and "session_id" in row and "message" in row and "handled" in row for row in rows)
