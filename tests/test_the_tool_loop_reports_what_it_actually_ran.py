"""What the tool loop RAN, what it did not, and in what order — told the same way to everyone.

Phase-1 trace of the model tool loop, 2026-08-03. Every finding below was measured through the real
loop with real tools against a real workspace; only the MODEL's replies are scripted, so the
runtime's handling of them is the only variable under test. The assertions are on disk state, step
records, the observations handed back to the model and the runtime events — never on prose.

The seven defects, and the one sentence each:

  B1  A narration the monologue guard correctly identifies as scaffolding destroyed its own batch.
      `respond.direct("Let me look.")` at position 1, two real reads behind it -> ZERO tools ran and
      the loop returned None. The promotion that was supposed to rescue this was nested inside
      `if direct_message is not None`, so it only fired for narrations the guard let THROUGH. The
      better the guard scored the prose, the more certainly the work was lost.

  B2  `respond.direct` is DECLARED `read_only`, so it passed the batch walk's capability gate, ran
      as a tool, came back handled=False and broke the walk at position 0. A narrated 9-read batch
      ran ONE tool; the identical batch without the narration ran eight.

  B3  The operator's event said "requested 12 ... Ran 8". The model was told, in the same round,
      "you requested 5 tools ... Only the first ran", with all twelve paths discarded.

  B4  Inline members were never registered in the duplicate guard, so a model that re-requested one
      got it executed a second time — three steps for two distinct files.

  B5  The head was appended AFTER its members, so a 4-read batch reached the model as f01, f02, f03,
      f00, and members carried the provider's raw call id where the head carried a runtime receipt.

  B6  A member that RAN and FAILED was reported to the model as NOT RUN, with no step and no event —
      so the model never learned the file was missing and its only rational move was to ask again.

  B9  A provider read timeout was reported as "Model returned an invalid tool payload with no intent
      name." CLAUDE.md section 5 requires that distinction to be explicit, and `used_model` carries
      it. Worse, on round 3 it also DISCARDED the grounded steps of rounds 1-2.

  B10 Identical script, identical tools, identical model: 5 rounds unpinned and 12 rounds pinned,
      because `max_rounds` read `source_context["requested_model"]` — a UI selector state.
"""
from __future__ import annotations

import json
import pathlib
import tempfile
import uuid
from types import SimpleNamespace

import pytest

from apps.vool_agent import VoolAgent
from core.agent_runtime.research_tool_loop_facade import (
    _MAX_MODEL_ROUNDS_PER_TURN,
    _semantic_payload_signature,
)
from core.memory_first_router import ModelExecutionDecision
from core.tool_argument_aliases import side_effect_class_for_intent

FILES = {f"f{index:02d}.py": f"# f{index}\nUNIQ_{index:02d} = {index}\n" for index in range(12)}

# Narrations the monologue guard classifies as scaffolding rather than as a reply. These are the
# ones B1 was live for; the guard returning None on them is CORRECT and is not what is being fixed.
SWALLOWED_NARRATIONS = (
    "Let me look.",
    "Let me check the files.",
    "First I need to see the code.",
)


def _call(intent: str, **arguments):
    return SimpleNamespace(intent=intent, arguments=arguments, call_id=f"c-{intent}", name=intent)


class _Router:
    """Scripted at the provider seam and nowhere below it.

    `scripts[i]` is the native batch returned on round i. Once the script runs out the model closes
    the turn, which is what a real model does when it has what it asked for.
    """

    def __init__(self, scripts, decision_for_round=None):
        self.scripts = list(scripts)
        self.decision_for_round = decision_for_round
        self.calls = 0
        self.contexts: list[dict] = []

    def resolve(self, **_kwargs):
        return SimpleNamespace(
            output_text="Grounded synthesis over the tool results.",
            provider_id="p", used_model=True, confidence=0.8, trust_score=0.8,
            validation_state="validated", details={}, source="provider_execution",
            model_name="m", provider_name="p", cache_hit=False, candidate_id=None,
            failover_used=False, structured_output=None, task_hash="h",
        )

    def resolve_tool_intent(self, **kwargs):
        self.contexts.append(dict(kwargs.get("source_context") or {}))
        index = self.calls
        self.calls += 1
        if self.decision_for_round is not None:
            override = self.decision_for_round(index)
            if override is not None:
                return override
        if index < len(self.scripts):
            batch = self.scripts[index]
            head = batch[0]
            return ModelExecutionDecision(
                source="scripted", task_hash="h", used_model=True, confidence=0.8,
                structured_output={"intent": head.intent, "arguments": dict(head.arguments)},
                tool_calls=tuple(batch), validation_state="validated", provider_id="p",
            )
        closing = "All done. Here is what the files show."
        return ModelExecutionDecision(
            source="scripted", task_hash="h", used_model=True, confidence=0.8,
            structured_output={"intent": "respond.direct", "arguments": {"message": closing}},
            tool_calls=(_call("respond.direct", message=closing),),
            validation_state="validated", provider_id="p",
        )


def _drive(scripts, *, extra_context=None, decision_for_round=None):
    """One turn through the real `_maybe_execute_model_tool_intent`, real tools, real workspace."""

    workspace = pathlib.Path(tempfile.mkdtemp())
    for name, body in FILES.items():
        (workspace / name).write_text(body, encoding="utf-8")

    router = _Router(scripts, decision_for_round=decision_for_round)
    events: list[dict] = []
    agent = VoolAgent.__new__(VoolAgent)
    agent.memory_router = router
    # Three PRE-LOOP gates owned by other lanes. Each has its own tests; leaving them live would
    # make this file about their predicates instead of about the loop.
    agent._should_keep_ai_first_chat_lane = lambda **_kw: False
    agent._should_run_builder_controller = lambda **_kw: False
    agent._plan_tool_workflow = lambda **_kw: SimpleNamespace(
        handled=False, stop_after=False, next_payload=None, reason=""
    )
    agent.hive_activity_tracker = None
    agent.public_hive_bridge = None
    real_emit = agent._emit_runtime_event

    def _spy(context, **payload):
        events.append(payload)
        return real_emit(context, **payload)

    agent._emit_runtime_event = _spy

    context = {
        "surface": "api", "workspace": str(workspace), "workspace_root": str(workspace),
        "workspace_binding": "project", "project_id": workspace.name,
    }
    context.update(extra_context or {})
    result = agent._maybe_execute_model_tool_intent(
        task=SimpleNamespace(task_id="t1"),
        effective_input="check the project and tell me what is broken",
        classification={"task_class": "debugging"},
        interpretation=SimpleNamespace(),
        context_result=SimpleNamespace(),
        persona=SimpleNamespace(),
        session_id=f"openclaw:{uuid.uuid4().hex[:20]}",
        source_context=context,
        surface="api",
    )
    return result or {}, router, events, workspace


def _steps(result: dict) -> list[str]:
    return list((result.get("details") or {}).get("tool_steps") or [])


def _observations(router: _Router) -> list[dict]:
    for context in reversed(router.contexts):
        observations = context.get("runtime_tool_observations")
        if observations:
            return list(observations)
    return []


def _model_note(router: _Router) -> str:
    for context in reversed(router.contexts):
        for item in list(context.get("conversation_history") or []):
            content = str((item or {}).get("content") or "")
            if "NOT run" in content:
                return content
    return ""


def _event(events: list[dict], event_type: str) -> dict:
    for payload in events:
        if payload.get("event_type") == event_type:
            return payload
    return {}


# ======================================================================================
# B1 — the narration the guard swallows
# ======================================================================================


def test_a_narration_the_guard_swallows_does_not_destroy_its_batch() -> None:
    """The measured shape: zero tools, zero steps, and the loop returned None entirely.

    Environmental, not prose: UNIQ_00/UNIQ_01 exist only inside files on disk, so they can only be
    in the observations if the reads actually happened.
    """

    for narration in SWALLOWED_NARRATIONS:
        batch = [
            _call("respond.direct", message=narration),
            _call("workspace.read_file", path="f00.py"),
            _call("workspace.read_file", path="f01.py"),
        ]
        result, router, _events, _ws = _drive([batch])
        blob = json.dumps(_observations(router), default=str)

        assert _steps(result) == ["workspace.read_file"] * 2, (
            f"{narration!r}: the batch behind the narration never ran"
        )
        assert "UNIQ_00" in blob and "UNIQ_01" in blob, (
            f"{narration!r}: the reads ran but their results never reached the model"
        )


def test_the_guard_still_scores_those_narrations_as_scaffolding() -> None:
    """The premise. If the guard ever started letting these through, the test above would go green
    for a reason that has nothing to do with the fix — it would be exercising the OLD promotion
    branch. Pinning the premise keeps that from happening silently."""

    from core.agent_runtime.response_policy_classification import tool_intent_direct_message

    for narration in SWALLOWED_NARRATIONS:
        assert tool_intent_direct_message(
            {"intent": "respond.direct", "arguments": {"message": narration}}
        ) is None, f"{narration!r} is no longer scaffolding; this file's premise has moved"


def test_an_ordinary_closing_reply_still_ends_the_turn() -> None:
    """The control that matters most. Every turn that finishes, finishes through this branch."""

    result, router, _events, _ws = _drive(
        [[_call("respond.direct", message="Here is your finished answer.")]]
    )

    assert "Here is your finished answer." in str(result.get("response") or "")
    assert _steps(result) == []
    assert router.calls == 1


# ======================================================================================
# B2 — respond.direct is not a tool and must not occupy a member slot
# ======================================================================================


def test_respond_direct_is_declared_read_only_which_is_why_it_reached_the_walk() -> None:
    """The premise, pinned. The capability gate is a positive allow on `side_effect_class`, and
    `respond.direct` satisfies it — which is exactly how a narration got executed as a tool."""

    assert side_effect_class_for_intent("respond.direct") == "read_only"


def test_a_narrated_batch_runs_the_same_members_as_an_unnarrated_one() -> None:
    """No literal on either side: two drives, compared. Measured before the fix — 1 vs 8."""

    reads = [_call("workspace.read_file", path=f"f{index:02d}.py") for index in range(9)]
    narrated, _r1, _e1, _w1 = _drive(
        [[_call("respond.direct", message="I'll audit this. Let me read the files first."), *reads]]
    )
    plain, _r2, _e2, _w2 = _drive([list(reads)])

    assert _steps(narrated) == _steps(plain), (
        "a narration in front of the batch still costs the batch its members"
    )
    assert len(_steps(plain)) > 1, "the control itself stopped batching; this test proves nothing"


def test_a_non_executing_member_is_never_reported_as_a_deferred_tool() -> None:
    """`respond.direct` cannot be "requested again" — naming it is a falsehood in the note and
    noise in the event."""

    batch = [
        _call("respond.direct", message="Let me look."),
        _call("workspace.read_file", path="f00.py"),
    ]
    _result, router, events, _ws = _drive([batch])

    assert "respond.direct" not in _model_note(router)
    assert "respond.direct" not in str(_event(events, "tool_batch_deferred").get("message") or "")


# ======================================================================================
# B3 — the model and the operator are told the same thing
# ======================================================================================


def test_the_model_is_told_the_real_counts_and_the_real_targets() -> None:
    """Both numbers are read off THIS drive, not off the code: 12 is the batch this test built and
    the run count is whatever the loop reports in `tool_steps`."""

    batch = [_call("workspace.read_file", path=f"f{index:02d}.py") for index in range(12)]
    result, router, _events, _ws = _drive([batch])
    note = _model_note(router)
    ran = len(_steps(result))

    assert note, "the model was told nothing about the members that did not run"
    assert f"you requested {len(batch)} tools" in note, note
    assert f"{ran} ran" in note, note
    assert "Only the first ran" not in note, note
    # Names alone are unactionable when twelve reads were asked for.
    assert "f11.py" in note, note


def test_the_note_and_the_operator_event_do_not_contradict_each_other() -> None:
    """They are the two sides of one event and they disagreed in the same round."""

    batch = [_call("workspace.read_file", path=f"f{index:02d}.py") for index in range(12)]
    result, router, events, _ws = _drive([batch])
    message = str(_event(events, "tool_batch_deferred").get("message") or "")
    note = _model_note(router)
    ran = len(_steps(result))

    assert f"requested {len(batch)} tools" in message and f"Ran {ran}" in message, message
    for deferred in _event(events, "tool_batch_deferred").get("deferred_calls") or []:
        assert deferred in note, f"{deferred} was reported to the operator and hidden from the model"


# ======================================================================================
# B4 — an inline member is a call the duplicate guard has seen
# ======================================================================================


def test_a_re_read_of_a_batch_member_costs_a_read_and_not_the_turn() -> None:
    """The original version of this test pinned the opposite, and the opposite is worse.

    It asserted that an inline batch member is registered in `seen_tool_payloads`, so re-requesting
    it would be suppressed. But the guard at `research_tool_loop_facade.py:1009` does not SKIP a
    repeat — it sets `loop_stop_reason = "repeated_tool_request"` and `break`s, ENDING the turn.

    So registering members means any file first read as a batch member can never be legitimately
    re-read in that turn, and the attempt kills every round behind it. "read a.py and b.py, fix
    b.py, now show me it is fixed" dies at round 3 — on the verification step, which is the one
    step that proves the work was done.

    The registration also never bought what it was added for: the member walk only ADDS to the set,
    it never checks it, so the in-batch duplicate it targeted (the same file twice in one reply)
    executes twice with or without it.

    A duplicate read costs one cheap deterministic call. A dead turn costs the answer. This pins
    the cheaper failure.
    """

    first = [
        _call("workspace.read_file", path="f00.py"),
        _call("workspace.read_file", path="f01.py"),
    ]
    repeat = [_call("workspace.read_file", path="f01.py")]
    result, _router, _events, _ws = _drive([first, repeat])

    assert len(_steps(result)) == 3, (
        "the re-read was suppressed, which means the guard ended the turn instead of answering it"
    )
    assert result.get("success") is not False
    assert "repeated_tool_request" not in str(result.get("status") or "")


def test_the_head_and_a_member_produce_the_same_call_identity() -> None:
    """The mechanism under the test above. Two spellings of the same call — one arriving as the
    head with its provider call id attached, one as a member — must be ONE identity, or the guard
    is comparing strings that can never match."""

    head = {
        "intent": "workspace.read_file",
        "arguments": {"path": "f01.py"},
        "_native_tool_call_id": "call-abc",
    }
    member = {"intent": "workspace.read_file", "arguments": {"path": "f01.py"}}

    assert _semantic_payload_signature(head) == _semantic_payload_signature(member)
    assert _semantic_payload_signature(member) != _semantic_payload_signature(
        {"intent": "workspace.read_file", "arguments": {"path": "f02.py"}}
    )


# ======================================================================================
# B5 — recorded in the order it ran
# ======================================================================================


def test_the_observations_reach_the_model_in_execution_order() -> None:
    """The head ran first and its observation was appended last; every pair came out inverted."""

    batch = [_call("workspace.read_file", path=f"f{index:02d}.py") for index in range(4)]
    _result, router, _events, _ws = _drive([batch])
    paths = [str(item.get("path") or "") for item in _observations(router)]

    assert paths == ["f00.py", "f01.py", "f02.py", "f03.py"], paths


def test_the_operator_sees_the_steps_in_execution_order() -> None:
    """The STEP RECORD, which is a second and separate ordering from the observations above.

    The head's `executed_steps` entry was appended after the walk, so the user-visible list read
    "Real steps completed: - loader.py - parser_util.py - README.md - calc.py" for a turn whose
    FIRST tool was calc.py. Sabotaging only the observation append leaves this green and vice
    versa, which is why both are asserted.
    """

    batch = [_call("workspace.read_file", path=f"f{index:02d}.py") for index in range(4)]
    result, _router, _events, _ws = _drive([batch])
    response = str(result.get("response") or "")
    positions = [response.find(f"f{index:02d}.py") for index in range(4)]

    assert all(offset >= 0 for offset in positions), response
    assert positions == sorted(positions), (
        f"the steps are listed out of the order they ran:\n{response}"
    )


def test_every_inline_member_carries_a_runtime_receipt() -> None:
    """The head's observation referenced a `tool-receipt-…`; a member's carried the provider's raw
    call id, because the member's observation was appended before any event produced a receipt."""

    batch = [_call("workspace.read_file", path=f"f{index:02d}.py") for index in range(4)]
    _result, router, _events, _ws = _drive([batch])

    receipts = [str(item.get("receipt_id") or "") for item in _observations(router)]
    assert receipts and all(value.startswith("tool-receipt-") for value in receipts), receipts


# ======================================================================================
# B6 — a member that ran and failed said so
# ======================================================================================


def test_a_member_that_ran_and_failed_is_not_reported_as_not_run() -> None:
    """The model was told the missing read "was NOT run", so asking again was its only move."""

    batch = [
        _call("workspace.read_file", path="f00.py"),
        _call("workspace.read_file", path="does_not_exist.py"),
        _call("workspace.read_file", path="f01.py"),
    ]
    result, router, events, _ws = _drive([batch])
    note = _model_note(router)
    blob = json.dumps(_observations(router), default=str)

    assert len(_steps(result)) == 2, "the member that ran and errored left no step record"
    assert "does_not_exist" in blob, "the model was never told the file is missing"
    assert "does_not_exist" not in note, "a member that RAN was reported to the model as not run"
    assert any(
        payload.get("event_type") == "tool_failed"
        and "does_not_exist" in json.dumps(payload, default=str)
        for payload in events
    ), "a tool failed inside the batch and left no failure event"


def test_the_walk_still_stops_at_the_member_that_failed() -> None:
    """Ordering is the safety property: nothing behind a failure runs, exactly as nothing behind a
    write or a refusal runs."""

    batch = [
        _call("workspace.read_file", path="f00.py"),
        _call("workspace.read_file", path="does_not_exist.py"),
        _call("workspace.read_file", path="f01.py"),
    ]
    _result, router, _events, _ws = _drive([batch])

    assert "UNIQ_01" not in json.dumps(_observations(router), default=str), (
        "the read behind the failed member was pulled forward"
    )


# ======================================================================================
# B9 — a provider that never answered is not a model that answered badly
# ======================================================================================


def _timed_out_decision(_round_index: int) -> ModelExecutionDecision:
    """Exactly what a 60s provider read timeout produces: nothing, and `used_model` False."""

    return ModelExecutionDecision(
        source="provider_execution", task_hash="h", used_model=False,
        structured_output=None, provider_id=None, validation_state="not_run",
    )


def test_a_provider_that_never_answered_is_not_blamed_on_the_model() -> None:
    _result, _router, events, _ws = _drive([], decision_for_round=_timed_out_decision)

    failures = [
        str(payload.get("message") or "")
        for payload in events
        if payload.get("event_type") == "tool_failed"
    ]
    assert failures, "a step that produced nothing left no failure event at all"
    assert not any("invalid tool payload" in text for text in failures), failures
    assert any("provider" in text.lower() for text in failures), failures


@pytest.mark.parametrize("attempted", [False, True, None])
def test_a_blocked_pin_reports_no_send_only_with_explicit_dispatch_evidence(attempted):
    details = {"block_reason": "internal_tool_intent_call" if attempted is False else "EMPTY_PROVIDER_RESPONSE"}
    if attempted is not None:
        details["model_was_attempted"] = attempted
    decision = ModelExecutionDecision(
        source="selected_model_blocked", task_hash="h", used_model=False,
        structured_output=None, details=details,
    )
    _, _, events, _ = _drive([], decision_for_round=lambda _: decision)
    failures = [event for event in events if event.get("event_type") == "tool_failed"]
    assert failures
    local = [event for event in failures if event.get("status") == "tool_selection_refused_before_send"]
    assert bool(local) is (attempted is False)
    assert any("Nothing was sent" in str(event.get("message")) for event in failures) is (attempted is False)


def test_a_timeout_mid_turn_keeps_the_steps_that_already_ran() -> None:
    """`_should_fallback_after_tool_failure` returns False once any step has run, so this turn used
    to return the tooling error and throw both grounded reads away."""

    scripts = [
        [_call("workspace.read_file", path="f00.py")],
        [_call("workspace.read_file", path="f01.py")],
    ]
    result, router, _events, _ws = _drive(
        scripts, decision_for_round=lambda index: _timed_out_decision(index) if index >= 2 else None
    )
    blob = json.dumps(_observations(router), default=str)

    assert len(_steps(result)) == 2, "a provider timeout discarded the work that had already run"
    assert "UNIQ_00" in blob and "UNIQ_01" in blob
    assert (result.get("details") or {}).get("loop_stop_reason") == "provider_did_not_answer"


# ======================================================================================
# B8 — a turn that was cut short says so
# ======================================================================================


def test_the_direct_render_return_says_why_the_loop_stopped() -> None:
    """This path returned success=True, mode=tool_executed and no stop reason at all, so a turn the
    repeat guard cut short was indistinguishable from a completed one."""

    same_read = [_call("workspace.read_file", path="f00.py")]
    result, _router, _events, _ws = _drive([list(same_read), list(same_read)])

    assert result.get("status") == "multi_step_executed", result.get("status")
    assert (result.get("details") or {}).get("loop_stop_reason") == "repeated_tool_request"


# ======================================================================================
# B10 — depth is a wall-clock bound, not a UI selector state
# ======================================================================================


def test_the_same_task_gets_the_same_depth_whether_or_not_a_model_is_pinned() -> None:
    """One script, driven twice. The only difference is `requested_model` in the composer.

    Measured before the fix: unpinned ran 5 rounds and answered "Incomplete — the 5-round tool
    budget was exhausted"; pinned ran 12 and answered. CLAUDE.md section 1: a model must never do
    worse because the runtime allowed it less work.
    """

    script = [[_call("workspace.read_file", path=f"f{index:02d}.py")] for index in range(11)]
    unpinned, router_a, _ea, _wa = _drive([list(batch) for batch in script])
    pinned, router_b, _eb, _wb = _drive(
        [list(batch) for batch in script], extra_context={"requested_model": "qwen3:8b"}
    )

    assert router_a.calls == router_b.calls, (
        f"unpinned got {router_a.calls} rounds and pinned got {router_b.calls}"
    )
    assert _steps(unpinned) == _steps(pinned)
    assert len(_steps(unpinned)) == len(script), (
        "the budget cut the turn short before the script finished"
    )


def test_the_round_budget_constant_is_what_this_file_assumes() -> None:
    """Pinned as a LITERAL, on its own, like the batch cap next door.

    Which limit it serves (CLAUDE.md 4b): WALL CLOCK. Each round is one provider round-trip bounded
    by the provider read timeout, so this number times that timeout is the turn's worst case. It is
    not a token budget — a round costs no output tokens of its own.
    """

    assert _MAX_MODEL_ROUNDS_PER_TURN == 12


def test_the_budget_still_bounds_a_model_that_never_stops() -> None:
    """The control for the test above. Raising a ceiling must not remove it."""

    # Distinct arguments each round so the duplicate guard is not what stops it.
    script = [
        [_call("workspace.read_file", path="f00.py", start_line=index + 1)]
        for index in range(40)
    ]
    result, router, _events, _ws = _drive(script)

    assert router.calls <= 12, f"the loop went back to the model {router.calls} times"
    assert (result.get("details") or {}).get("loop_stop_reason") == "step_budget_exhausted"
    assert "incomplete" in str(result.get("response") or "").lower()
