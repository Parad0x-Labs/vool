"""One request that plans seven writes must cost ONE approval, not seven turns.

The live defect this file pins, measured against the code as it was: a request whose model reply
asked for several `workspace.write_file` calls raised a prompt for the first one, and the only thing
the client could do with the grant was submit the ORIGINAL SENTENCE again. The resumed turn
re-classified, re-planned, ran write number one, hit write number two, and asked again. Seven writes,
seven prompts, seven replans -- and the operator's only escape was a standing grant.

What is added is deliberately NOT a standing grant. A request-scope approval is bound to the concrete
set of calls the model had already asked for when the prompt was raised, each one spendable exactly
once, each one identified by the same byte-exact fingerprint a single "allow once" uses. Everything
outside that set -- a replanned path, swapped content, a delete the model adds afterwards, another
chat, another turn, another mode revision, a restarted process -- prompts on its own.

Coverage here: one pending write, six writes in one request, allow-once, allow-the-batch, deny,
a changed plan after approval, cross-request/chat/action theft, the waiting (not failed) state,
restart, and model-call counts across the resume.
"""

from __future__ import annotations

import contextlib
import json
from types import SimpleNamespace
from unittest import mock

import pytest

from core import runtime_paths
from core.agent_runtime.research_tool_loop_facade import resumable_pending_payload
from core.mode_permission_policy import (
    PENDING_BATCH_CALLS_KEY,
    PermissionEffect,
    decide_tool_call,
    request_batch_grant_covers,
    reset_mode_permission_state,
    resolve_approval,
    set_active_mode,
)
from core.task_event_model import build_task_event
from core.tool_intent_executor import ToolIntentExecution
from core.vool_chat_page import render_vool_chat_html

WRITES = tuple(
    {"intent": "workspace.write_file", "arguments": {"path": f"src/mod_{index}.py", "content": f"# module {index}\n"}}
    for index in range(7)
)


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    runtime_paths.configure_runtime_home(tmp_path / "home")
    reset_mode_permission_state()
    yield
    reset_mode_permission_state()
    runtime_paths.configure_runtime_home(None)


def _context(root, *, session: str = "chat-a", turn: str = "turn-a", planned: tuple[dict, ...] = ()):
    """A manual-mode chat with NO bound project, so the project escape hatch is not what is
    being measured here -- the batch grant is."""
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


def _walk(calls, context: dict, task: str = "task-a") -> tuple[list[str], list[str]]:
    """Gate every call of a plan in order, the way the loop does. Returns (ran, asked)."""
    ran: list[str] = []
    asked: list[str] = []
    for call in calls:
        decision = _decide(call, context, task=task)
        if decision.effect is PermissionEffect.ALLOW:
            ran.append(str(call["arguments"]["path"]))
        else:
            asked.append(str(call["arguments"]["path"]))
    return ran, asked


# --------------------------------------------------------------------------- #
# 1. One pending write is still one prompt, with no batch to offer
# --------------------------------------------------------------------------- #
def test_a_single_pending_write_is_not_offered_a_request_scope(tmp_path) -> None:
    context = _context(tmp_path)
    request = _decide(WRITES[0], context).approval_request
    assert request is not None
    assert "request" not in request["scope_options"]
    assert request["planned_action_count"] == 0
    # Asking for the wider scope anyway does not create one out of nothing.
    granted = resolve_approval(request["approval_id"], decision="allow", scope="request")
    assert granted["scope"] == "once"
    resumed = {**context, "mode_approval_token": request["approval_id"]}
    assert _decide(WRITES[0], resumed).effect is PermissionEffect.ALLOW
    assert _decide(WRITES[1], resumed).effect is PermissionEffect.REQUIRE_APPROVAL


# --------------------------------------------------------------------------- #
# 2. Six further writes belonging to the same exact request
# --------------------------------------------------------------------------- #
def test_one_request_planning_seven_writes_raises_one_prompt_naming_all_seven(tmp_path) -> None:
    context = _context(tmp_path, planned=WRITES[1:])
    request = _decide(WRITES[0], context).approval_request
    assert request is not None
    assert "request" in request["scope_options"]
    assert request["planned_action_count"] == 7
    named = [item["target"] for item in request["planned_actions"]]
    assert named == [str(call["arguments"]["path"]) for call in WRITES]


def test_allow_once_still_covers_exactly_one_write_of_the_seven(tmp_path) -> None:
    """The narrow answer stays narrow: offering the batch must not widen the once button."""
    context = _context(tmp_path, planned=WRITES[1:])
    request = _decide(WRITES[0], context).approval_request
    assert resolve_approval(request["approval_id"], decision="allow", scope="once")["scope"] == "once"

    resumed = {**context, "mode_approval_token": request["approval_id"]}
    ran, asked = _walk(WRITES, resumed)
    assert ran == ["src/mod_0.py"]
    assert len(asked) == 6


def test_allow_the_whole_planned_batch_runs_all_seven_on_one_grant(tmp_path) -> None:
    context = _context(tmp_path, planned=WRITES[1:])
    request = _decide(WRITES[0], context).approval_request
    granted = resolve_approval(request["approval_id"], decision="allow", scope="request")
    assert granted["scope"] == "request"

    resumed = {**context, "mode_approval_token": request["approval_id"]}
    ran, asked = _walk(WRITES, resumed)
    assert ran == [str(call["arguments"]["path"]) for call in WRITES]
    assert asked == []
    # Spent, not standing: the eighth write of the same shape asks.
    again = {"intent": "workspace.write_file", "arguments": {"path": "src/mod_8.py", "content": "# 8\n"}}
    assert _decide(again, resumed).effect is PermissionEffect.REQUIRE_APPROVAL
    # A REPLAY of an approved-and-executed member is covered by the prior grant (an
    # unchanged action the operator already reviewed must not prompt a second time -- the
    # repeated-approval defect this lane repairs), while the new write above still asks.
    # Coverage is idempotent over the exact call, never spent by the replay itself.
    assert _decide(WRITES[2], resumed).effect is PermissionEffect.ALLOW
    for _ in range(3):
        assert _decide(WRITES[2], resumed).effect is PermissionEffect.ALLOW


def test_the_batch_grant_covers_writes_only_and_never_the_riskier_calls_in_the_same_reply(tmp_path) -> None:
    """The reply may plan anything. The grant may cover only file writes."""
    riskier = (
        {"intent": "workspace.delete_file", "arguments": {"path": "src/mod_1.py"}},
        {"intent": "workspace.move_path", "arguments": {"path": "src/mod_1.py", "destination": "gone.py"}},
        {"intent": "sandbox.run_command", "arguments": {"command": "rm -rf build"}},
        {"intent": "email.send", "arguments": {"recipient": "someone@example.com"}},
    )
    context = _context(tmp_path, planned=(WRITES[1], *riskier))
    request = _decide(WRITES[0], context).approval_request
    assert request["planned_action_count"] == 2
    assert [item["intent"] for item in request["planned_actions"]] == [
        "workspace.write_file",
        "workspace.write_file",
    ]
    resolve_approval(request["approval_id"], decision="allow", scope="request")

    resumed = {**context, "mode_approval_token": request["approval_id"]}
    assert _decide(WRITES[0], resumed).effect is PermissionEffect.ALLOW
    assert _decide(WRITES[1], resumed).effect is PermissionEffect.ALLOW
    for call in riskier:
        assert _decide(call, resumed).effect is not PermissionEffect.ALLOW, call["intent"]


def test_a_batch_headed_by_a_command_is_offered_no_request_scope_at_all(tmp_path) -> None:
    context = _context(tmp_path, planned=WRITES)
    request = _decide({"intent": "sandbox.run_command", "arguments": {"command": "pytest -q"}}, context).approval_request
    assert "request" not in request["scope_options"]


# --------------------------------------------------------------------------- #
# 3. Deny stays fail-closed
# --------------------------------------------------------------------------- #
def test_denying_the_batch_authorizes_nothing_including_the_head(tmp_path) -> None:
    context = _context(tmp_path, planned=WRITES[1:])
    request = _decide(WRITES[0], context).approval_request
    denied = resolve_approval(request["approval_id"], decision="deny", scope="request")
    assert denied["status"] == "denied" and denied["scope"] == "once"

    resumed = {**context, "mode_approval_token": request["approval_id"]}
    ran, asked = _walk(WRITES, resumed)
    assert ran == []
    assert len(asked) == 7
    # A denied token cannot be re-resolved into an allow.
    assert resolve_approval(request["approval_id"], decision="allow", scope="request") is None


# --------------------------------------------------------------------------- #
# 4. A plan that changed after the approval
# --------------------------------------------------------------------------- #
def test_a_replanned_write_outside_the_approved_set_is_not_covered(tmp_path) -> None:
    context = _context(tmp_path, planned=WRITES[1:])
    request = _decide(WRITES[0], context).approval_request
    resolve_approval(request["approval_id"], decision="allow", scope="request")
    resumed = {**context, "mode_approval_token": request["approval_id"]}

    swapped_bytes = {
        "intent": "workspace.write_file",
        "arguments": {"path": "src/mod_3.py", "content": "# module 3 -- REPLACED\n"},
    }
    swapped_path = {
        "intent": "workspace.write_file",
        "arguments": {"path": "src/somewhere_else.py", "content": "# module 3\n"},
    }
    assert _decide(swapped_bytes, resumed).effect is PermissionEffect.REQUIRE_APPROVAL
    assert _decide(swapped_path, resumed).effect is PermissionEffect.REQUIRE_APPROVAL
    # The members that did NOT change are still covered: a partial replan costs a prompt for the
    # part that changed, not for the whole request over again.
    assert _decide(WRITES[4], resumed).effect is PermissionEffect.ALLOW


def test_a_mode_change_after_the_grant_invalidates_every_member(tmp_path) -> None:
    context = _context(tmp_path, planned=WRITES[1:])
    request = _decide(WRITES[0], context).approval_request
    resolve_approval(request["approval_id"], decision="allow", scope="request")
    set_active_mode("chat-a", "review_edits", client_turn_id="turn-a")

    resumed = {**context, "mode_approval_token": request["approval_id"]}
    ran, asked = _walk(WRITES, resumed)
    assert ran == []
    assert len(asked) == 7


# --------------------------------------------------------------------------- #
# 5. Nobody else can spend the grant
# --------------------------------------------------------------------------- #
def test_another_chat_turn_or_task_cannot_consume_the_batch_grant(tmp_path) -> None:
    context = _context(tmp_path, planned=WRITES[1:])
    request = _decide(WRITES[0], context).approval_request
    resolve_approval(request["approval_id"], decision="allow", scope="request")
    token = request["approval_id"]

    other_chat = {**_context(tmp_path, session="chat-b", turn="turn-a"), "mode_approval_token": token}
    assert _decide(WRITES[1], other_chat).effect is PermissionEffect.REQUIRE_APPROVAL

    next_turn = {**_context(tmp_path, session="chat-a", turn="turn-b"), "mode_approval_token": token}
    assert _decide(WRITES[1], next_turn, task="task-b").effect is PermissionEffect.REQUIRE_APPROVAL

    # The head is still spendable in the chat and turn that earned it -- the rejections above are
    # the grant refusing to travel, not the grant being destroyed by the attempt.
    assert _decide(WRITES[0], {**context, "mode_approval_token": token}).effect is PermissionEffect.ALLOW


def test_a_forged_pending_plan_cannot_arrive_over_the_wire(tmp_path) -> None:
    """The batch is server-owned. A caller that posts its own plan has it stripped before the
    controller ever sees it, so a request body can never choose what one click authorizes."""
    from core.request_trust import strip_reserved_trust_keys

    posted = {"surface": "api", PENDING_BATCH_CALLS_KEY: [dict(call) for call in WRITES[1:]]}
    assert PENDING_BATCH_CALLS_KEY not in strip_reserved_trust_keys(posted)


# --------------------------------------------------------------------------- #
# 6. Restart
# --------------------------------------------------------------------------- #
def test_a_restart_drops_the_grant_and_the_next_call_asks_again(tmp_path) -> None:
    """Grants live in process state on purpose. A restarted runtime holds no authority at all, so
    a checkpoint that survives cannot pick up a grant that did not."""
    context = _context(tmp_path, planned=WRITES[1:])
    request = _decide(WRITES[0], context).approval_request
    resolve_approval(request["approval_id"], decision="allow", scope="request")
    resumed = {**context, "mode_approval_token": request["approval_id"]}
    assert _decide(WRITES[1], resumed).effect is PermissionEffect.ALLOW

    reset_mode_permission_state()  # process restart
    set_active_mode("chat-a", "manual", client_turn_id="turn-a")
    ran, asked = _walk(WRITES, {**context, "mode_approval_token": request["approval_id"]})
    assert ran == []
    assert len(asked) == 7


# --------------------------------------------------------------------------- #
# 7. Waiting for approval is a waiting state, not a failure
# --------------------------------------------------------------------------- #
def test_the_pending_prompt_surfaces_as_waiting_for_approval_not_as_a_failure(tmp_path) -> None:
    context = _context(tmp_path, planned=WRITES[1:])
    request = _decide(WRITES[0], context).approval_request
    event = build_task_event(
        {
            "event_type": "tool_preview",
            "message": "Approval required for workspace.write_file.",
            "approval_request": dict(request),
        }
    )
    assert event["type"] == "permission.required"
    assert event["status"] == "pending_approval"
    assert event["status"] != "failed"
    assert "request" in event["approval"]["scope_options"]
    assert event["approval"]["planned_action_count"] == 7
    assert len(event["approval"]["planned_actions"]) == 7


def test_the_chat_surface_offers_the_bounded_batch_and_no_blanket_grant() -> None:
    html = render_vool_chat_html()
    assert 'id="permRequest"' in html
    assert "resolvePermission('allow', 'request')" in html
    # Named, per request, and server-resolved. A browser-side "always allow" flag is exactly what
    # this button must never become.
    assert "a.scope_options.indexOf('request') >= 0" in html
    assert "vool_allow_" not in html
    # The waiting card says waiting, and only a denial turns it into a failure.
    assert "'Waiting for your approval'" in html
    assert "run.permissionDenied ? 'failed'" in html


# --------------------------------------------------------------------------- #
# 8. What the resume actually re-executes, and what it costs in model calls
# --------------------------------------------------------------------------- #
def test_the_pending_payload_stored_for_a_resume_is_not_clipped(tmp_path) -> None:
    """The resume slot is re-executed, not displayed. Storing the event-shaped copy clipped every
    string at 1000 characters, so an approved 3 KB write resumed by writing 1000 bytes and an
    ellipsis -- content the operator never approved -- and the clipped call also missed the
    byte-exact grant, raising a second prompt for the write just allowed."""
    body = "x" * 4096
    payload = {"intent": "workspace.write_file", "arguments": {"path": "big.txt", "content": body, "token": "synthetic-value-for-redaction"}}
    stored = resumable_pending_payload(payload)
    assert stored["arguments"]["content"] == body
    assert stored["arguments"]["token"] == "[redacted]"

    context = _context(tmp_path)
    request = _decide(payload, context).approval_request
    resolve_approval(request["approval_id"], decision="allow")
    resumed = {**context, "mode_approval_token": request["approval_id"]}
    # The stored copy is what the resumed turn runs; it must still be the approved action.
    assert _decide(stored, resumed).effect is PermissionEffect.ALLOW


class _NoWorkLeftError(Exception):
    """Raised by the model stand-in when the plan is exhausted, to end the drive deterministically."""


def _drive_resumed_turn(*, granted_scope: str, tmp_path) -> dict:
    """Resume one paused turn through the real tool loop and count what it costs.

    The model seam (`resolve_tool_intent`) and the tool seam are counted separately. The permission
    gate is the real one: the executor stand-in asks `decide_tool_call` and reports back exactly
    what the controller decided, so what runs here is what the controller authorized.
    """
    from apps.vool_agent import VoolAgent

    context = _context(tmp_path, planned=WRITES[1:])
    request = _decide(WRITES[0], context).approval_request
    assert request is not None
    resolve_approval(request["approval_id"], decision="allow", scope=granted_scope)

    head = dict(WRITES[0])
    signature = json.dumps(head, sort_keys=True, ensure_ascii=True, default=str)
    checkpoint = {
        "state": {
            "seen_tool_payloads": [signature],
            "loop_source_context": {},
            "pending_tool_payload": dict(head),
            "pending_batch_calls": [dict(call) for call in WRITES[1:]],
        }
    }
    source_context = {
        **context,
        "runtime_checkpoint_resumed": True,
        "mode_approval_token": request["approval_id"],
    }

    executed: list[str] = []
    previewed: list[str] = []

    def _fake_execute(payload, *, task_id, session_id, source_context, **kwargs):
        intent = str(payload.get("intent") or "")
        arguments = dict(payload.get("arguments") or {})
        if intent in {"respond.direct", "none", "no_tool"}:
            # Not a tool. The real executor reports it unhandled and the loop closes the turn.
            return ToolIntentExecution(handled=False, ok=False, status="direct_message", tool_name=intent)
        decision = decide_tool_call(
            intent=intent, arguments=arguments, task_id=task_id, source_context=source_context
        )
        path = str(arguments.get("path") or "")
        if decision.effect is PermissionEffect.ALLOW:
            if intent == "workspace.write_file":
                executed.append(path)
            return ToolIntentExecution(
                handled=True, ok=True, status="executed", mode="tool_executed",
                tool_name=intent, response_text=f"wrote {path}", details={},
            )
        previewed.append(path)
        return ToolIntentExecution(
            handled=True, ok=False, status="pending_approval", mode="tool_preview",
            tool_name=intent, response_text="approval required",
            details={"approval_request": dict(decision.approval_request or {})},
        )

    def _model_round(*_args, **_kwargs):
        """What the model would do if asked again: re-request the writes that have not run yet.

        This is what made the defect expensive. Every write the runtime could not carry through on
        the operator's existing authorization came back as another model round -- and, on the live
        surface, another whole submission of the original sentence behind it.
        """
        remaining = [call for call in WRITES if str(call["arguments"]["path"]) not in executed]
        if not remaining:
            # Nothing left to plan. Stop the drive here rather than running the turn on into
            # synthesis: what is being measured is the work done BEFORE the model was consulted.
            raise _NoWorkLeftError
        return SimpleNamespace(
            structured_output=dict(remaining[0]),
            tool_calls=tuple(
                SimpleNamespace(
                    intent=str(call["intent"]),
                    arguments=dict(call["arguments"]),
                    call_id=f"call-{index}",
                )
                for index, call in enumerate(remaining)
            ),
            provider_id="test-provider",
            validation_state="validated",
            trust_score=0.8,
            confidence=0.8,
            used_model=True,
        )

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
                effective_input="rewrite the seven modules",
                classification={"task_class": "unknown"},
                interpretation=None,
                context_result=None,
                persona=None,
                session_id="chat-a",
                source_context=source_context,
                surface="api",
            )
    return {"executed": executed, "previewed": previewed, "model_calls": model_calls.call_count}


def _drive_pausing_turn(tmp_path) -> dict:
    """Run the FRESH turn that pauses, through the real loop, and return the approval it raised.

    This is the seam the policy tests above cannot reach: the loop is what tells the controller
    which further calls this one reply already asked for. Without that hand-off the controller sees
    a single write, offers a single prompt, and the batch grant can never be built at all.
    """
    from apps.vool_agent import VoolAgent

    context = _context(tmp_path)
    raised: list[dict] = []
    recorded: list[dict] = []

    def _fake_execute(payload, *, task_id, session_id, source_context, **kwargs):
        intent = str(payload.get("intent") or "")
        arguments = dict(payload.get("arguments") or {})
        decision = decide_tool_call(
            intent=intent, arguments=arguments, task_id=task_id, source_context=source_context
        )
        if decision.effect is PermissionEffect.ALLOW:
            return ToolIntentExecution(
                handled=True, ok=True, status="executed", mode="tool_executed",
                tool_name=intent, response_text="wrote", details={},
            )
        request = dict(decision.approval_request or {})
        raised.append(request)
        return ToolIntentExecution(
            handled=True, ok=False, status="pending_approval", mode="tool_preview",
            tool_name=intent, response_text="approval required",
            details={"approval_request": request},
        )

    decision = SimpleNamespace(
        structured_output=dict(WRITES[0]),
        tool_calls=tuple(
            SimpleNamespace(intent=str(call["intent"]), arguments=dict(call["arguments"]), call_id=f"call-{index}")
            for index, call in enumerate(WRITES)
        ),
        provider_id="test-provider",
        validation_state="validated",
        trust_score=0.8,
        confidence=0.8,
        used_model=True,
    )
    def _record(_checkpoint_id, **kwargs):
        recorded.append(dict(kwargs))
        return None

    agent = VoolAgent(backend_name="test-backend", device="test", persona_id="default")
    with (
        mock.patch.object(agent, "_should_attempt_tool_intent", return_value=True),
        mock.patch.object(agent, "_should_keep_ai_first_chat_lane", return_value=False),
        mock.patch.object(agent, "_should_run_builder_controller", return_value=False),
        mock.patch.object(agent, "_runtime_checkpoint_id", return_value="cp-1"),
        mock.patch.object(agent, "_get_runtime_checkpoint", return_value={"state": {}}),
        mock.patch.object(agent, "_record_runtime_tool_progress", side_effect=_record),
        mock.patch.object(agent, "_emit_runtime_event", return_value={}),
        mock.patch.object(agent, "_plan_tool_workflow", return_value=None),
        mock.patch.object(agent, "_execute_tool_intent", side_effect=_fake_execute),
        mock.patch.object(agent.memory_router, "resolve_tool_intent", return_value=decision),
    ):
        result = agent._maybe_execute_model_tool_intent(
            task=SimpleNamespace(task_id="task-a"),
            effective_input="rewrite the seven modules",
            classification={"task_class": "unknown"},
            interpretation=None,
            context_result=None,
            persona=None,
            session_id="chat-a",
            source_context=dict(context),
            surface="api",
        )
    return {"raised": raised, "result": result or {}, "recorded": recorded}


def test_the_pausing_turn_hands_the_whole_planned_batch_to_the_permission_controller(tmp_path) -> None:
    driven = _drive_pausing_turn(tmp_path)
    assert len(driven["raised"]) == 1, "one reply planning seven writes must raise exactly one prompt"
    request = driven["raised"][0]
    assert "request" in request["scope_options"]
    assert request["planned_action_count"] == 7
    assert [item["target"] for item in request["planned_actions"]] == [
        str(call["arguments"]["path"]) for call in WRITES
    ]
    # And the turn reports itself as waiting, not as a failure.
    assert driven["result"]["task_outcome"] == "pending_approval"
    assert driven["result"]["mode"] == "tool_preview"
    # The pause is checkpointed with the whole plan, not just its head. A record written after the
    # gate that dropped the batch would leave the resume with nothing to run but write number one.
    paused = [row for row in driven["recorded"] if row.get("status") == "pending_approval"]
    assert paused, "a turn that stopped for approval must checkpoint itself as pending_approval"
    assert paused[-1]["pending_tool_payload"]["arguments"]["path"] == "src/mod_0.py"
    assert [item["arguments"]["path"] for item in paused[-1]["pending_batch_calls"]] == [
        str(call["arguments"]["path"]) for call in WRITES[1:]
    ]


def test_resuming_a_batch_grant_runs_every_approved_write_without_replanning_them(tmp_path) -> None:
    """The invariant the operator actually feels: one approval, one resume, seven writes.

    The seven writes cost ZERO model rounds. The head and every authorized member come from the
    plan state stored when the turn paused, so the resume executes what was approved rather than
    asking the model to produce it again. The single model call counted here is the round AFTER the
    work, where the model sees seven results and closes the turn -- which is the loop doing its job,
    not a replan.
    """
    result = _drive_resumed_turn(granted_scope="request", tmp_path=tmp_path)
    assert result["executed"] == [str(call["arguments"]["path"]) for call in WRITES]
    assert result["previewed"] == []
    assert result["model_calls"] == 1


def test_resuming_a_once_grant_runs_only_the_head_and_asks_again_for_the_next_write(tmp_path) -> None:
    """The control, and the shape of the defect stated as a measurement.

    A once grant authorizes exactly one of the seven, so the walk stops dead at write number two --
    a member the operator did not authorize never rides the resume. Getting to that write costs a
    model round, and the turn then ends waiting for another approval. Repeat six more times and that
    is the seven-prompt request the batch grant above collapses into one.
    """
    result = _drive_resumed_turn(granted_scope="once", tmp_path=tmp_path)
    assert result["executed"] == ["src/mod_0.py"]
    assert result["previewed"] == ["src/mod_1.py"]
    assert result["model_calls"] == 1


def test_request_batch_grant_covers_never_spends_the_grant_it_reports_on(tmp_path) -> None:
    """The walk peeks before it runs a member. A peek that consumed would burn the authorization
    for a call that then never executes."""
    context = _context(tmp_path, planned=WRITES[1:])
    request = _decide(WRITES[0], context).approval_request
    resolve_approval(request["approval_id"], decision="allow", scope="request")
    resumed = {**context, "mode_approval_token": request["approval_id"]}

    for _ in range(5):
        assert request_batch_grant_covers(
            intent=WRITES[3]["intent"],
            arguments=dict(WRITES[3]["arguments"]),
            task_id="task-a",
            source_context=resumed,
        )
    assert _decide(WRITES[3], resumed).effect is PermissionEffect.ALLOW
    assert not request_batch_grant_covers(
        intent=WRITES[3]["intent"],
        arguments=dict(WRITES[3]["arguments"]),
        task_id="task-a",
        source_context=resumed,
    )
    # Never reports on something outside the set, and never on a missing token.
    assert not request_batch_grant_covers(
        intent="workspace.delete_file",
        arguments={"path": "src/mod_1.py"},
        task_id="task-a",
        source_context=resumed,
    )
    assert not request_batch_grant_covers(
        intent=WRITES[4]["intent"],
        arguments=dict(WRITES[4]["arguments"]),
        task_id="task-a",
        source_context=context,
    )


# --------------------------------------------------------------------------- #
# 9. The plan survives the pause in storage, and does not survive the turn
# --------------------------------------------------------------------------- #
def test_the_paused_plan_is_persisted_with_the_pending_head_and_cleared_when_the_turn_ends(tmp_path) -> None:
    """A pause is durable or it is nothing: the operator may take a minute, and the resume has to
    know the same plan the prompt described. It must also NOT outlive the turn -- a completed or
    failed checkpoint that kept a plan around is stale authority waiting for a later turn to pick up.
    """
    from core.runtime_continuity import (
        configure_runtime_continuity_db_path,
        create_runtime_checkpoint,
        finalize_runtime_checkpoint,
        get_runtime_checkpoint,
        record_runtime_tool_progress,
        reset_runtime_continuity_state,
    )
    from storage.migrations import run_migrations

    db_path = tmp_path / "continuity.db"
    run_migrations(db_path=db_path)
    configure_runtime_continuity_db_path(str(db_path))
    reset_runtime_continuity_state()
    try:
        checkpoint = create_runtime_checkpoint(
            session_id="chat-a",
            request_text="rewrite the seven modules",
            source_context={"runtime_session_id": "chat-a"},
        )
        checkpoint_id = str(checkpoint["checkpoint_id"])
        record_runtime_tool_progress(
            checkpoint_id,
            executed_steps=[],
            loop_source_context={"runtime_session_id": "chat-a"},
            seen_tool_payloads=[],
            pending_tool_payload=dict(WRITES[0]),
            pending_batch_calls=[dict(call) for call in WRITES[1:]],
            last_tool_payload=None,
            last_tool_response=None,
            last_tool_name="workspace.write_file",
            task_class="unknown",
            status="pending_approval",
        )
        stored = dict(get_runtime_checkpoint(checkpoint_id) or {})
        state = dict(stored.get("state") or {})
        assert state["pending_tool_payload"]["arguments"]["path"] == "src/mod_0.py"
        assert [item["arguments"]["path"] for item in state["pending_batch_calls"]] == [
            str(call["arguments"]["path"]) for call in WRITES[1:]
        ]

        finalize_runtime_checkpoint(checkpoint_id, status="completed", final_response="done")
        ended = dict(dict(get_runtime_checkpoint(checkpoint_id) or {}).get("state") or {})
        assert ended["pending_batch_calls"] == []
        assert ended["pending_tool_payload"] is None
    finally:
        reset_runtime_continuity_state()
        configure_runtime_continuity_db_path(None)


# --------------------------------------------------------------------------- #
# 10. The operator's scope choice survives the resolution CHANNEL
# --------------------------------------------------------------------------- #
def test_the_registry_resolution_channel_carries_the_operators_scope_choice(tmp_path) -> None:
    """The scope the operator clicked is the grant's width. The chat page resolves through
    /api/mode -> ``approvals.resolve``; that channel used to forward only (approval_id, decision),
    so every "Allow all planned changes for this request" click silently minted a ONCE grant --
    the head write rode it and every remaining planned write re-prompted, which is the
    repeated-approval loop the beta report named. The channel now carries the scope, and the
    policy remains the sole authority on whether a scope may mint at all.
    """
    from core.command_registry.legacy import forward

    context = _context(tmp_path, planned=WRITES[1:])
    request = _decide(WRITES[0], context).approval_request
    assert request is not None and "request" in request["scope_options"]

    status, granted = forward(
        "approvals.resolve",
        {"approval_id": request["approval_id"], "decision": "allow", "scope": "request"},
    )
    assert status == 200
    assert granted["scope"] == "request"
    assert len(granted["remaining_batch"]) == 7

    # The channel-minted batch grant is the real thing the gate spends: one resume runs all seven
    # planned writes and asks for none of them again.
    resumed = {**context, "mode_approval_token": request["approval_id"]}
    ran, asked = _walk(WRITES, resumed)
    assert ran == [str(call["arguments"]["path"]) for call in WRITES]
    assert asked == []

    # The narrow answer through the same channel stays narrow: "once" covers exactly the head.
    # A FRESH turn id: the prior sections' grants bind to chat-a/turn-a, and coverage of an
    # already-approved exact call persists within its own task -- a new request is a new task.
    narrow_context = _context(tmp_path, session="chat-c", turn="turn-c", planned=WRITES[2:])
    narrow = _decide(WRITES[1], narrow_context).approval_request
    status_narrow, granted_narrow = forward(
        "approvals.resolve",
        {"approval_id": narrow["approval_id"], "decision": "allow", "scope": "once"},
    )
    assert status_narrow == 200 and granted_narrow["scope"] == "once"
    narrow_resumed = {**narrow_context, "mode_approval_token": narrow["approval_id"]}
    ran_narrow, asked_narrow = _walk(WRITES[1:], narrow_resumed, task="task-c")
    assert ran_narrow == [str(WRITES[1]["arguments"]["path"])]
    assert asked_narrow == [str(call["arguments"]["path"]) for call in WRITES[2:]]

    # An unrecognized scope string through the channel mints nothing wider than an edit batch may
    # ever have: the policy's own fallthrough, reached through the real channel.
    odd_context = _context(tmp_path, session="chat-d", turn="turn-d", planned=WRITES[2:])
    odd = _decide(WRITES[1], odd_context).approval_request
    status_odd, granted_odd = forward(
        "approvals.resolve",
        {"approval_id": odd["approval_id"], "decision": "allow", "scope": "everything-forever"},
    )
    assert status_odd == 200 and granted_odd["scope"] == "once"
