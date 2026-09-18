"""A mixed workspace-setup plan gets ONE bounded grant, and that grant stops at the workspace edge.

Two live defects are pinned here, both measured against the code as it was on this branch.

DEFECT 1 -- the reported symptom. A scaffold plan of `workspace.ensure_directory` plus several
`workspace.write_file` calls showed only "Allow once / Review details / Deny". The batch machinery
was fine; the HAND-OFF was missing. The deterministic workflow planner (`core.execution.planner`)
decides the whole concrete plan up front -- directory plus every file and its bytes -- and then
emits one payload per round. On a planner round `last_tool_decision` is None, so the tool loop had
nothing to give the permission controller, the controller saw a single call, and a plan it had
already fully decided cost one approval per step.

DEFECT 2 -- an authority widening found while fixing the first. Batch membership was decided by
action class alone: `set(actions) <= _WRITE_ACTIONS`. But `machine.write_file` and
`machine.ensure_directory` write to `~/Desktop`, `~/Downloads` and `~/Documents`, `web0.*` project
mutations classify as CREATE_FILES/MODIFY_FILES, and a `workspace.write_file` whose path is
`../../escape.txt` classifies identically to one landing in `src/`. Measured before the fix: a batch
headed by an in-workspace write listed and covered a `machine.write_file` to `Desktop/b.txt`, and a
batch headed by `machine.write_file` was offered a request scope of its own. One click on a
workspace scaffold authorized a write outside the workspace.

The boundary this file holds to, stated once: a request-scope grant may cover
`workspace.ensure_directory`, `workspace.write_file`, `workspace.replace_in_file` and
`workspace.apply_unified_diff`, every path resolving inside the workspace root. Deletes, moves,
commands, network, git, settings, secrets, payments and every write landing outside the workspace
are excluded, named in the prompt as excluded, and keep asking one at a time.
"""

from __future__ import annotations

import contextlib
import json
from types import SimpleNamespace
from unittest import mock

import pytest

from core import runtime_paths
from core.execution.planner import plan_tool_workflow
from core.mode_permission_policy import (
    PENDING_BATCH_CALLS_KEY,
    PermissionEffect,
    decide_tool_call,
    reset_mode_permission_state,
    resolve_approval,
    set_active_mode,
)
from core.vool_chat_page import render_vool_chat_html
from core.task_event_model import build_task_event
from core.tool_intent_executor import ToolIntentExecution

# The mixed scaffold: one directory, then files inside it. This is the plan shape the live surface
# offered no batch for.
SETUP = {"intent": "workspace.ensure_directory", "arguments": {"path": "demo"}}
WRITES = (
    {"intent": "workspace.write_file", "arguments": {"path": "demo/a.md", "content": "alpha\n"}},
    {"intent": "workspace.write_file", "arguments": {"path": "demo/b.md", "content": "beta\n"}},
)
MIXED = (SETUP, *WRITES)

# Everything a plan may also contain that one click must never buy. Each is a real intent the
# runtime dispatches, not a synthetic name, and each classifies close enough to a write to have
# been a plausible member: the deletes and the move are workspace tools, the machine writes and
# `web0.*` mutations land in CREATE_FILES/MODIFY_FILES exactly like `workspace.write_file` does.
NEAR_MISSES = (
    {"intent": "workspace.delete_file", "arguments": {"path": "demo/a.md"}},
    {"intent": "workspace.move_path", "arguments": {"path": "demo/a.md", "destination": "demo/z.md"}},
    {"intent": "sandbox.run_command", "arguments": {"command": "npm install"}},
    {"intent": "sandbox.run_command", "arguments": {"command": "rm -rf demo"}},
    {"intent": "machine.write_file", "arguments": {"path": "Desktop/stolen.txt", "content": "x"}},
    {"intent": "machine.ensure_directory", "arguments": {"path": "Desktop/loot"}},
    {"intent": "web0.publish", "arguments": {"project": "p"}},
    {"intent": "web0.open_builder_draft", "arguments": {"project": "p"}},
    {"intent": "email.send", "arguments": {"recipient": "someone@example.com"}},
    {"intent": "wallet.transfer", "arguments": {"amount": "1"}},
    {"intent": "settings.update", "arguments": {"key": "mode", "value": "auto"}},
    {"intent": "secret.read", "arguments": {"name": "api_key"}},
    {"intent": "git.push", "arguments": {"remote": "origin"}},
)


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    runtime_paths.configure_runtime_home(tmp_path / "home")
    reset_mode_permission_state()
    yield
    reset_mode_permission_state()
    runtime_paths.configure_runtime_home(None)


def _workspace(tmp_path):
    root = tmp_path / "ws"
    root.mkdir(exist_ok=True)
    return root


def _context(root, *, session: str = "chat-a", turn: str = "turn-a", planned: tuple[dict, ...] = ()):
    """Manual mode, no bound project -- the project escape hatch is not what is measured here."""
    set_active_mode(session, "manual", client_turn_id=turn)
    context = {
        "runtime_session_id": session,
        "operating_mode": "manual",
        "workspace_root": str(root),
        "workspace": str(root),
        "cancel_turn_id": turn,
    }
    if planned:
        context[PENDING_BATCH_CALLS_KEY] = [dict(item) for item in planned]
    return context


def _decide(call: dict, context: dict, task: str = "task-a"):
    return decide_tool_call(
        intent=str(call["intent"]),
        arguments=dict(call["arguments"]),
        task_id=task,
        source_context=context,
    )


def _target(call: dict) -> str:
    args = dict(call["arguments"])
    return str(args.get("path") or args.get("command") or args.get("recipient") or args.get("project") or "")


def _walk(calls, context: dict, task: str = "task-a") -> tuple[list[str], list[str]]:
    """Gate every call of a plan in order, the way the loop does. Returns (ran, asked)."""
    ran: list[str] = []
    asked: list[str] = []
    for call in calls:
        decision = _decide(call, context, task=task)
        (ran if decision.effect is PermissionEffect.ALLOW else asked).append(_target(call))
    return ran, asked


# --------------------------------------------------------------------------- #
# 1. The mixed batch the live surface refused to offer
# --------------------------------------------------------------------------- #
def test_a_directory_plus_writes_plan_raises_one_prompt_naming_every_workspace_change(tmp_path) -> None:
    root = _workspace(tmp_path)
    request = _decide(SETUP, _context(root, planned=WRITES)).approval_request
    assert request is not None
    assert "request" in request["scope_options"]
    assert request["planned_action_count"] == 3
    assert [(item["intent"], item["target"]) for item in request["planned_actions"]] == [
        ("workspace.ensure_directory", "demo"),
        ("workspace.write_file", "demo/a.md"),
        ("workspace.write_file", "demo/b.md"),
    ]
    # The wider word is earned by the directory being in the set, and is the server's to decide.
    assert request["planned_action_label"] == "planned workspace changes"


def test_one_grant_runs_the_directory_and_both_files_and_is_then_spent(tmp_path) -> None:
    root = _workspace(tmp_path)
    context = _context(root, planned=WRITES)
    request = _decide(SETUP, context).approval_request
    assert resolve_approval(request["approval_id"], decision="allow", scope="request")["scope"] == "request"

    resumed = {**context, "mode_approval_token": request["approval_id"]}
    ran, asked = _walk(MIXED, resumed)
    assert ran == ["demo", "demo/a.md", "demo/b.md"]
    assert asked == []
    # Spent, not standing: a third file of the same shape, and a replay of the directory, both ask.
    third = {"intent": "workspace.write_file", "arguments": {"path": "demo/c.md", "content": "gamma\n"}}
    assert _decide(third, resumed).effect is PermissionEffect.REQUIRE_APPROVAL
    assert _decide(SETUP, resumed).effect is PermissionEffect.REQUIRE_APPROVAL


def test_a_pure_write_batch_keeps_its_narrower_wording_and_still_works(tmp_path) -> None:
    """The control for the label. A batch with no directory setup is not a "workspace changes"
    batch, and the phrase has to move with the contents rather than being pinned to one case."""
    root = _workspace(tmp_path)
    context = _context(root, planned=WRITES[1:])
    request = _decide(WRITES[0], context).approval_request
    assert request["planned_action_count"] == 2
    assert request["planned_action_label"] == "planned changes"
    assert request["excluded_actions"] == []
    resolve_approval(request["approval_id"], decision="allow", scope="request")
    ran, asked = _walk(WRITES, {**context, "mode_approval_token": request["approval_id"]})
    assert ran == ["demo/a.md", "demo/b.md"]
    assert asked == []


def test_a_directory_setup_with_nothing_behind_it_gets_no_batch_at_all(tmp_path) -> None:
    """One action is one action. A lone `ensure_directory` must not acquire a batch button that
    would cover a single call and read as though it covered a plan."""
    root = _workspace(tmp_path)
    request = _decide(SETUP, _context(root)).approval_request
    assert "request" not in request["scope_options"]
    assert request["planned_action_count"] == 0
    granted = resolve_approval(request["approval_id"], decision="allow", scope="request")
    assert granted["scope"] == "once"


# --------------------------------------------------------------------------- #
# 2. What a coupled command, delete, move or external write does to the batch
# --------------------------------------------------------------------------- #
def test_a_command_beside_the_scaffold_is_excluded_named_and_still_asks(tmp_path) -> None:
    root = _workspace(tmp_path)
    plan = (SETUP, *WRITES, {"intent": "sandbox.run_command", "arguments": {"command": "npm install"}})
    context = _context(root, planned=plan[1:])
    request = _decide(SETUP, context).approval_request
    assert request["planned_action_count"] == 3
    assert [item["intent"] for item in request["excluded_actions"]] == ["sandbox.run_command"]
    resolve_approval(request["approval_id"], decision="allow", scope="request")

    resumed = {**context, "mode_approval_token": request["approval_id"]}
    ran, asked = _walk(plan, resumed)
    assert ran == ["demo", "demo/a.md", "demo/b.md"]
    assert asked == ["npm install"]  # the command asked on its own, as it must
    assert _decide(plan[-1], resumed).effect is PermissionEffect.REQUIRE_APPROVAL


@pytest.mark.parametrize("risky", NEAR_MISSES, ids=lambda call: str(call["intent"]))
def test_no_destructive_external_or_outward_call_ever_joins_the_batch(tmp_path, risky) -> None:
    """Each near-miss run on its own, in a plan that IS otherwise batch-eligible, so the batch is
    genuinely offered and the only question is whether this one call rode along."""
    root = _workspace(tmp_path)
    plan = (SETUP, *WRITES, dict(risky))
    context = _context(root, planned=plan[1:])
    request = _decide(SETUP, context).approval_request
    assert request is not None and "request" in request["scope_options"]
    covered = {item["intent"] for item in request["planned_actions"]}
    assert risky["intent"] not in covered
    assert risky["intent"] in {item["intent"] for item in request["excluded_actions"]}
    resolve_approval(request["approval_id"], decision="allow", scope="request")
    resumed = {**context, "mode_approval_token": request["approval_id"]}
    assert _decide(risky, resumed).effect is not PermissionEffect.ALLOW


def test_a_plan_headed_by_a_machine_write_is_offered_no_request_scope(tmp_path) -> None:
    """`machine.write_file` lands in ~/Desktop, ~/Downloads or ~/Documents and classifies into the
    same CREATE_FILES the workspace writer does. Before the fix that was enough to head a batch."""
    root = _workspace(tmp_path)
    head = {"intent": "machine.write_file", "arguments": {"path": "Desktop/a.txt", "content": "a"}}
    members = ({"intent": "machine.write_file", "arguments": {"path": "Desktop/b.txt", "content": "b"}},)
    request = _decide(head, _context(root, planned=members)).approval_request
    assert "request" not in request["scope_options"]
    assert request["planned_action_count"] == 0


@pytest.mark.parametrize(
    "escape",
    [
        "../../escape.txt",
        "../sibling.txt",
        "/etc/hosts",
        "~/Desktop/escape.txt",
        "demo/../../escape.txt",
    ],
)
def test_a_workspace_write_whose_path_leaves_the_workspace_is_not_a_workspace_change(tmp_path, escape) -> None:
    """Same intent, same action class, same tool -- only the resolved destination differs, and that
    is the whole boundary. A count-based or intent-based member test cannot see this."""
    root = _workspace(tmp_path)
    outside = {"intent": "workspace.write_file", "arguments": {"path": escape, "content": "x"}}
    context = _context(root, planned=(*WRITES, outside))
    request = _decide(SETUP, context).approval_request
    assert [item["target"] for item in request["planned_actions"]] == ["demo", "demo/a.md", "demo/b.md"]
    assert [item["target"] for item in request["excluded_actions"]] == [escape]
    resolve_approval(request["approval_id"], decision="allow", scope="request")
    resumed = {**context, "mode_approval_token": request["approval_id"]}
    assert _decide(outside, resumed).effect is PermissionEffect.REQUIRE_APPROVAL


def test_a_symlinked_directory_inside_the_workspace_cannot_smuggle_a_member_out(tmp_path) -> None:
    """The containment check resolves symlinks. A path that reads as in-workspace and lands outside
    it is the one an intent allow-list alone would wave through."""
    root = _workspace(tmp_path)
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    (root / "link").symlink_to(outside_dir, target_is_directory=True)
    smuggled = {"intent": "workspace.write_file", "arguments": {"path": "link/escape.txt", "content": "x"}}

    context = _context(root, planned=(*WRITES, smuggled))
    request = _decide(SETUP, context).approval_request
    assert [item["target"] for item in request["planned_actions"]] == ["demo", "demo/a.md", "demo/b.md"]
    assert [item["target"] for item in request["excluded_actions"]] == ["link/escape.txt"]


def test_a_batch_headed_by_a_delete_or_a_command_offers_no_request_scope(tmp_path) -> None:
    root = _workspace(tmp_path)
    for head in (
        {"intent": "workspace.delete_file", "arguments": {"path": "demo/a.md"}},
        {"intent": "sandbox.run_command", "arguments": {"command": "npm install"}},
        {"intent": "workspace.move_path", "arguments": {"path": "demo/a.md", "destination": "demo/z.md"}},
    ):
        reset_mode_permission_state()
        request = _decide(head, _context(root, planned=MIXED)).approval_request
        assert "request" not in request["scope_options"], head["intent"]


# --------------------------------------------------------------------------- #
# 3. Allow-once, deny, and the plan changing under the grant
# --------------------------------------------------------------------------- #
def test_allow_once_on_a_mixed_plan_still_buys_exactly_the_directory(tmp_path) -> None:
    root = _workspace(tmp_path)
    context = _context(root, planned=WRITES)
    request = _decide(SETUP, context).approval_request
    assert resolve_approval(request["approval_id"], decision="allow", scope="once")["scope"] == "once"
    ran, asked = _walk(MIXED, {**context, "mode_approval_token": request["approval_id"]})
    assert ran == ["demo"]
    assert asked == ["demo/a.md", "demo/b.md"]


def test_denying_a_mixed_batch_authorizes_nothing_including_the_directory(tmp_path) -> None:
    root = _workspace(tmp_path)
    context = _context(root, planned=WRITES)
    request = _decide(SETUP, context).approval_request
    denied = resolve_approval(request["approval_id"], decision="deny", scope="request")
    assert denied["status"] == "denied" and denied["scope"] == "once"
    ran, asked = _walk(MIXED, {**context, "mode_approval_token": request["approval_id"]})
    assert ran == []
    assert asked == ["demo", "demo/a.md", "demo/b.md"]
    assert resolve_approval(request["approval_id"], decision="allow", scope="request") is None


def test_a_changed_directory_or_changed_file_bytes_are_not_covered_by_the_grant(tmp_path) -> None:
    root = _workspace(tmp_path)
    context = _context(root, planned=WRITES)
    request = _decide(SETUP, context).approval_request
    resolve_approval(request["approval_id"], decision="allow", scope="request")
    resumed = {**context, "mode_approval_token": request["approval_id"]}

    moved_setup = {"intent": "workspace.ensure_directory", "arguments": {"path": "demo_v2"}}
    swapped_bytes = {"intent": "workspace.write_file", "arguments": {"path": "demo/a.md", "content": "REPLACED\n"}}
    swapped_path = {"intent": "workspace.write_file", "arguments": {"path": "demo/elsewhere.md", "content": "alpha\n"}}
    for changed in (moved_setup, swapped_bytes, swapped_path):
        assert _decide(changed, resumed).effect is PermissionEffect.REQUIRE_APPROVAL, changed
    # The unchanged member is still covered: a partial replan costs a prompt for the part that moved.
    assert _decide(WRITES[1], resumed).effect is PermissionEffect.ALLOW


def test_a_mode_change_after_a_mixed_grant_invalidates_every_member(tmp_path) -> None:
    root = _workspace(tmp_path)
    context = _context(root, planned=WRITES)
    request = _decide(SETUP, context).approval_request
    resolve_approval(request["approval_id"], decision="allow", scope="request")
    set_active_mode("chat-a", "review_edits", client_turn_id="turn-a")
    ran, asked = _walk(MIXED, {**context, "mode_approval_token": request["approval_id"]})
    assert ran == []
    assert len(asked) == 3


# --------------------------------------------------------------------------- #
# 4. Forged and stolen grants
# --------------------------------------------------------------------------- #
def test_a_forged_mixed_plan_posted_by_a_client_never_reaches_the_controller(tmp_path) -> None:
    """The batch is server-owned. A body that posts its own plan -- here one smuggling a command and
    a Desktop write in among the workspace changes -- has the whole key stripped."""
    from core.request_trust import strip_reserved_trust_keys

    posted = {
        "surface": "api",
        PENDING_BATCH_CALLS_KEY: [
            dict(WRITES[0]),
            {"intent": "sandbox.run_command", "arguments": {"command": "rm -rf /"}},
            {"intent": "machine.write_file", "arguments": {"path": "Desktop/x", "content": "x"}},
        ],
    }
    stripped = strip_reserved_trust_keys(posted)
    assert PENDING_BATCH_CALLS_KEY not in stripped
    assert stripped.get("surface") == "api"

    # And with the key gone the controller offers no batch at all, so a forged plan cannot even
    # widen the button from one action to three.
    root = _workspace(tmp_path)
    request = _decide(SETUP, {**_context(root), **stripped}).approval_request
    assert "request" not in request["scope_options"]


def test_a_mixed_grant_does_not_travel_to_another_chat_turn_or_task(tmp_path) -> None:
    root = _workspace(tmp_path)
    context = _context(root, planned=WRITES)
    request = _decide(SETUP, context).approval_request
    resolve_approval(request["approval_id"], decision="allow", scope="request")
    token = request["approval_id"]

    other_chat = {**_context(root, session="chat-b", turn="turn-a"), "mode_approval_token": token}
    assert _decide(WRITES[0], other_chat).effect is PermissionEffect.REQUIRE_APPROVAL

    next_turn = {**_context(root, session="chat-a", turn="turn-b"), "mode_approval_token": token}
    assert _decide(WRITES[0], next_turn, task="task-b").effect is PermissionEffect.REQUIRE_APPROVAL

    # Rejections are the grant refusing to travel, not the grant being destroyed by the attempt.
    assert _decide(SETUP, {**context, "mode_approval_token": token}).effect is PermissionEffect.ALLOW


def test_a_request_grant_creates_no_standing_project_permission(tmp_path) -> None:
    """The batch must stay a longer approval, never a wider one. Nothing about granting it may leave
    a standing low-risk permission behind for the next turn to pick up."""
    from core.mode_permission_policy import project_approval_state

    root = _workspace(tmp_path)
    context = {**_context(root, planned=WRITES), "_trusted_project_id": "proj-1"}
    request = _decide(SETUP, context).approval_request
    resolved = resolve_approval(request["approval_id"], decision="allow", scope="request")
    assert resolved["scope"] == "request"
    assert not project_approval_state("proj-1").get("granted_at")


# --------------------------------------------------------------------------- #
# 5. The planner hand-off -- the actual cause of the reported symptom
# --------------------------------------------------------------------------- #
def test_the_real_planner_states_the_rest_of_a_scaffold_plan_it_has_already_decided(tmp_path) -> None:
    """Driven with a real sentence through the real planner, not a hand-built plan dict."""
    root = _workspace(tmp_path)
    decision = plan_tool_workflow(
        user_text=(
            'create a folder called demo and write file demo/a.md with the text "alpha" '
            'and write file demo/b.md with the text "beta"'
        ),
        task_class="unknown",
        executed_steps=[],
        source_context={"workspace_root": str(root), "workspace": str(root)},
    )
    assert decision.handled
    assert decision.next_payload == {"intent": "workspace.ensure_directory", "arguments": {"path": "demo"}}
    assert [(item["intent"], item["arguments"]["path"]) for item in decision.planned_batch] == [
        ("workspace.write_file", "demo/a.md"),
        ("workspace.write_file", "demo/b.md"),
    ]


def test_the_planners_stated_batch_is_the_payload_it_will_actually_emit_next(tmp_path) -> None:
    """A stated member whose bytes differ from the call the planner later makes would be listed on
    the prompt, fingerprinted into the grant, and then miss it -- an approval that silently buys
    nothing. So the hand-off is checked against the planner's OWN next payload, one round on."""
    root = _workspace(tmp_path)
    text = (
        'create a folder called demo and write file demo/a.md with the text "alpha" '
        'and write file demo/b.md with the text "beta"'
    )
    context = {"workspace_root": str(root), "workspace": str(root)}
    first = plan_tool_workflow(user_text=text, task_class="unknown", executed_steps=[], source_context=context)
    steps = [{"tool_name": "workspace.ensure_directory", "arguments": {"path": "demo"}}]
    second = plan_tool_workflow(user_text=text, task_class="unknown", executed_steps=steps, source_context=context)
    assert second.next_payload == first.planned_batch[0]


def test_an_append_write_stops_the_stated_batch_because_a_read_comes_first(tmp_path) -> None:
    """The planner answers an append with a `workspace.read_file`, so an append and anything behind
    it are not the next writes. Listing them would name changes the grant could not spend."""
    from core.execution.planner import _planned_write_batch

    stated = _planned_write_batch(
        [
            {"path": "demo/a.md", "content": "alpha", "mode": "write"},
            {"path": "demo/b.md", "content": "beta", "mode": "append"},
            {"path": "demo/c.md", "content": "gamma", "mode": "write"},
        ]
    )
    assert [item["arguments"]["path"] for item in stated] == ["demo/a.md"]


def _drive_planner_scaffold(tmp_path) -> dict:
    """Run a planner-driven mixed scaffold through the real tool loop and collect what it asked.

    Only the model seam and the tool seam are stood in for. The planner is the real one, the loop is
    the real one, and the permission gate is the real one -- the executor stand-in asks
    `decide_tool_call` and reports back exactly what the controller decided.
    """
    from apps.vool_agent import VoolAgent

    root = _workspace(tmp_path)
    context = _context(root)
    raised: list[dict] = []

    def _fake_execute(payload, *, task_id, session_id, source_context, **kwargs):
        intent = str(payload.get("intent") or "")
        arguments = dict(payload.get("arguments") or {})
        decision = decide_tool_call(
            intent=intent, arguments=arguments, task_id=task_id, source_context=source_context
        )
        if decision.effect is PermissionEffect.ALLOW:
            return ToolIntentExecution(
                handled=True, ok=True, status="executed", mode="tool_executed",
                tool_name=intent, response_text="done", details={},
            )
        request = dict(decision.approval_request or {})
        raised.append(request)
        return ToolIntentExecution(
            handled=True, ok=False, status="pending_approval", mode="tool_preview",
            tool_name=intent, response_text="approval required",
            details={"approval_request": request},
        )

    model_calls = mock.Mock(side_effect=AssertionError("the planner owns this turn; no model round is due"))
    agent = VoolAgent(backend_name="test-backend", device="test", persona_id="default")
    with (
        mock.patch.object(agent, "_should_attempt_tool_intent", return_value=True),
        mock.patch.object(agent, "_should_keep_ai_first_chat_lane", return_value=False),
        mock.patch.object(agent, "_should_run_builder_controller", return_value=False),
        mock.patch.object(agent, "_runtime_checkpoint_id", return_value="cp-1"),
        mock.patch.object(agent, "_get_runtime_checkpoint", return_value={"state": {}}),
        mock.patch.object(agent, "_record_runtime_tool_progress", return_value=None),
        mock.patch.object(agent, "_emit_runtime_event", return_value={}),
        mock.patch.object(agent, "_execute_tool_intent", side_effect=_fake_execute),
        mock.patch.object(agent.memory_router, "resolve_tool_intent", model_calls),
    ):
        result = agent._maybe_execute_model_tool_intent(
            task=SimpleNamespace(task_id="task-a"),
            effective_input=(
                'create a folder called demo and write file demo/a.md with the text "alpha" '
                'and write file demo/b.md with the text "beta"'
            ),
            classification={"task_class": "unknown"},
            interpretation=None,
            context_result=None,
            persona=None,
            session_id="chat-a",
            source_context=dict(context),
            surface="api",
        )
    return {"raised": raised, "result": result or {}}


def test_a_planner_driven_scaffold_asks_once_for_the_whole_workspace_plan(tmp_path) -> None:
    """The reported defect, end to end: this is the turn that showed Allow once / Deny and nothing
    else. One prompt, three named workspace changes, and no model round spent to discover them."""
    driven = _drive_planner_scaffold(tmp_path)
    assert len(driven["raised"]) == 1, "a planner-decided scaffold must raise exactly one prompt"
    request = driven["raised"][0]
    assert "request" in request["scope_options"]
    assert request["planned_action_count"] == 3
    assert [(item["intent"], item["target"]) for item in request["planned_actions"]] == [
        ("workspace.ensure_directory", "demo"),
        ("workspace.write_file", "demo/a.md"),
        ("workspace.write_file", "demo/b.md"),
    ]
    assert request["planned_action_label"] == "planned workspace changes"
    assert driven["result"].get("task_outcome") == "pending_approval"


# --------------------------------------------------------------------------- #
# 6. What the operator is actually shown
# --------------------------------------------------------------------------- #
def test_the_event_carries_both_the_covered_and_the_excluded_calls(tmp_path) -> None:
    root = _workspace(tmp_path)
    plan = (*WRITES, {"intent": "sandbox.run_command", "arguments": {"command": "npm install"}})
    request = _decide(SETUP, _context(root, planned=plan)).approval_request
    event = build_task_event(
        {
            "event_type": "tool_preview",
            "message": "Approval required for workspace.ensure_directory.",
            "approval_request": dict(request),
        }
    )
    assert event["type"] == "permission.required"
    assert event["status"] == "pending_approval"
    approval = event["approval"]
    assert approval["planned_action_count"] == 3
    assert [item["target"] for item in approval["planned_actions"]] == ["demo", "demo/a.md", "demo/b.md"]
    assert [item["intent"] for item in approval["excluded_actions"]] == ["sandbox.run_command"]
    assert approval["planned_action_label"] == "planned workspace changes"


def test_the_chat_surface_names_the_excluded_calls_and_takes_its_wording_from_the_server() -> None:
    html = render_vool_chat_html()
    assert "resolvePermission('allow', 'request')" in html
    # The button phrase comes from the server, so the browser never classifies a plan itself.
    assert "a.planned_action_label" in html
    # And the review panel says what one click does NOT buy, by name.
    assert "a.excluded_actions" in html
    assert "Not covered -- each of these asks separately" in html
    # Still no browser-side standing grant, in any form.
    assert "vool_allow_" not in html


def test_the_batch_button_is_hidden_when_the_controller_offered_no_batch() -> None:
    """The scaffold that could not be batched must fall back to the exact prompt it has today --
    the hidden state is what makes "Allow once / Review details / Deny" the correct answer there."""
    html = render_vool_chat_html()
    assert "reqBtn.hidden = !reqOffered" in html
    assert "a.scope_options.indexOf('request') >= 0" in html


# --------------------------------------------------------------------------- #
# 7. The resumed turn actually carries the mixed grant through
# --------------------------------------------------------------------------- #
def test_resuming_a_mixed_grant_runs_the_directory_and_both_files_with_no_replan(tmp_path) -> None:
    """The invariant the operator feels: one approval, one resume, the whole scaffold -- and zero
    model rounds spent re-deriving a plan that was already approved."""
    from apps.vool_agent import VoolAgent

    root = _workspace(tmp_path)
    context = _context(root, planned=WRITES)
    request = _decide(SETUP, context).approval_request
    resolve_approval(request["approval_id"], decision="allow", scope="request")

    signature = json.dumps(dict(SETUP), sort_keys=True, ensure_ascii=True, default=str)
    checkpoint = {
        "state": {
            "seen_tool_payloads": [signature],
            "loop_source_context": {},
            "pending_tool_payload": dict(SETUP),
            "pending_batch_calls": [dict(call) for call in WRITES],
        }
    }
    source_context = {
        **context,
        "runtime_checkpoint_resumed": True,
        "mode_approval_token": request["approval_id"],
    }

    executed: list[str] = []
    previewed: list[str] = []

    class _NoWorkLeftError(Exception):
        """Ends the drive deterministically once the approved plan is exhausted."""

    def _fake_execute(payload, *, task_id, session_id, source_context, **kwargs):
        intent = str(payload.get("intent") or "")
        arguments = dict(payload.get("arguments") or {})
        if intent in {"respond.direct", "none", "no_tool"}:
            return ToolIntentExecution(handled=False, ok=False, status="direct_message", tool_name=intent)
        decision = decide_tool_call(
            intent=intent, arguments=arguments, task_id=task_id, source_context=source_context
        )
        path = str(arguments.get("path") or "")
        if decision.effect is PermissionEffect.ALLOW:
            executed.append(path)
            return ToolIntentExecution(
                handled=True, ok=True, status="executed", mode="tool_executed",
                tool_name=intent, response_text=f"did {path}", details={},
            )
        previewed.append(path)
        return ToolIntentExecution(
            handled=True, ok=False, status="pending_approval", mode="tool_preview",
            tool_name=intent, response_text="approval required",
            details={"approval_request": dict(decision.approval_request or {})},
        )

    def _model_round(*_args, **_kwargs):
        raise _NoWorkLeftError

    agent = VoolAgent(backend_name="test-backend", device="test", persona_id="default")
    model_calls = mock.Mock(side_effect=_model_round)
    with (
        mock.patch.object(agent, "_should_attempt_tool_intent", return_value=True),
        mock.patch.object(agent, "_should_keep_ai_first_chat_lane", return_value=False),
        mock.patch.object(agent, "_should_run_builder_controller", return_value=False),
        mock.patch.object(agent, "_runtime_checkpoint_id", return_value="cp-1"),
        mock.patch.object(agent, "_get_runtime_checkpoint", return_value=checkpoint),
        mock.patch.object(agent, "_record_runtime_tool_progress", return_value=None),
        mock.patch.object(agent, "_emit_runtime_event", return_value={}),
        mock.patch.object(agent, "_plan_tool_workflow", return_value=None),
        mock.patch.object(agent, "_execute_tool_intent", side_effect=_fake_execute),
        mock.patch.object(agent.memory_router, "resolve_tool_intent", model_calls),
    ):
        with contextlib.suppress(_NoWorkLeftError):
            agent._maybe_execute_model_tool_intent(
                task=SimpleNamespace(task_id="task-a"),
                effective_input="create the demo folder and the two files",
                classification={"task_class": "unknown"},
                interpretation=None,
                context_result=None,
                persona=None,
                session_id="chat-a",
                source_context=source_context,
                surface="api",
            )

    assert executed == ["demo", "demo/a.md", "demo/b.md"]
    assert previewed == []
