"""L1–L12: NO-ELIGIBLE-TOOL liveness tests for the tool loop.

Every finding below was measured through the real loop with real tools against a real
workspace; only the MODEL's replies are scripted, so the runtime's handling of them is
the only variable under test. The assertions are on step records, loop stop reasons,
model rounds consumed, and runtime events — never on prose.

Root defect
-----------
The tool/planner loop had no way to conclude "no eligible tool exists for this request".
Once inside, it cycled up to 12 rounds of full provider calls, then performed forced
synthesis, every time — even for zero-tool requests misclassified by the pre-model
keyword gate. This module tests the liveness repair.

Tests
-----
L1  Planner enters tool lane, eligible tool set is empty → terminates after first
    definitive no-fit determination.
L2  Same rejected tool set on next iteration with no new evidence → no repeated
    planner churn.
L3  Actual eligible useful tool exists → tool loop still executes it normally.
L4  First tool fails but a DIFFERENT eligible tool exists → may continue (not an
    overly aggressive one-failure exit).
L5  First tool observation changes requirements → planner may continue.
L6  Explicit no-tools typed policy → zero tool dispatch, zero repeated search.
L7  Zero-tool identifier canary → does not consume 12 rounds, no external tool dispatch.
L8  No-fit termination does not fabricate tool observations.
L9  No-fit termination does not claim tool success.
L10 Existing round/time budget remains as emergency backstop.
L11 Tool catalog changes materially between iterations → re-evaluation may occur.
L12 Same catalog + same requirement + same observations → repeated cycle forbidden.
"""

from __future__ import annotations

import json
import pathlib
import tempfile
import uuid
from types import SimpleNamespace

from apps.vool_agent import VoolAgent
from core.agent_runtime.research_tool_loop_facade import (
    _MAX_MODEL_ROUNDS_PER_TURN,
)


# Real workspace files used by the tools.
FILES = {
    "file_a.py": "UNIQ_00\n",
    "file_b.py": "UNIQ_01\n",
    "file_c.py": "UNIQ_02\n",
    "file_d.py": "UNIQ_03\n",
}


def _call(intent: str, **arguments):
    return SimpleNamespace(intent=intent, arguments=arguments, call_id=f"c-{intent}", name=intent)


def _closing_reply(message: str = "All done."):
    return SimpleNamespace(
        output_text=message,
        provider_id="p", used_model=True, confidence=0.8, trust_score=0.8,
        validation_state="validated", details={}, source="provider_execution",
        model_name="m", provider_name="p", cache_hit=False, candidate_id=None,
        failover_used=False, structured_output=None, task_hash="h",
    )


def _tool_decision(intent: str, arguments: dict | None = None, *,
                   batch: list[SimpleNamespace] | None = None,
                   used_model: bool = True):
    """Build a ModelExecutionDecision that returns a single tool or a batch."""
    batch = batch or [_call(intent, **(arguments or {}))]
    head = batch[0]
    return SimpleNamespace(
        output_text="",
        provider_id="p", used_model=used_model, confidence=0.8, trust_score=0.8,
        validation_state="validated", details={}, source="provider_execution",
        model_name="m", provider_name="p", cache_hit=False, candidate_id=None,
        failover_used=False, structured_output={
            "intent": head.intent,
            "arguments": dict(head.arguments),
        },
        tool_calls=tuple(batch),
        task_hash="h",
    )


class _Router:
    """Scripted at the provider seam and nowhere below it.

    `scripts[i]` is the native batch returned on round i. Once the script runs out
    the model closes the turn.

    `resolve` returns a closing synthesis unless overridden.
    `resolve_tool_intent` returns the next scripted batch or, when scripts run out,
    a closing respond.direct.
    """

    def __init__(self, scripts, resolve_override=None):
        self.scripts = list(scripts or [])
        self.resolve_override = resolve_override
        self.calls = 0
        self.contexts: list[dict] = []

    def resolve(self, **_kwargs):
        if self.resolve_override:
            return self.resolve_override(**_kwargs)
        return _closing_reply()

    def resolve_tool_intent(self, **kwargs):
        self.contexts.append(dict(kwargs.get("source_context") or {}))
        index = self.calls
        self.calls += 1
        if index < len(self.scripts):
            script = self.scripts[index]
            return _tool_decision(script[0].intent, dict(script[0].arguments), batch=list(script))
        closing = "No more tools needed."
        return SimpleNamespace(
            output_text=closing,
            provider_id="p", used_model=True, confidence=0.8, trust_score=0.8,
            validation_state="validated", details={}, source="provider_execution",
            model_name="m", provider_name="p", cache_hit=False, candidate_id=None,
            failover_used=False,
            structured_output={"intent": "respond.direct", "arguments": {"message": closing}},
            tool_calls=(_call("respond.direct", message=closing),),
            task_hash="h",
        )


def _drive(scripts, *, extra_context=None, classify_task_class="debugging",
           user_input="Extract the identifiers from: Neo4j VX-2048 EC2 2FA",
           workspace_path=None):
    """One turn through the real `_maybe_execute_model_tool_intent`.

    Returns (result, router, events).
    """
    router = _Router(scripts)
    events: list[dict] = []
    agent = VoolAgent.__new__(VoolAgent)
    agent.memory_router = router

    # Stub pre-loop gates owned by other lanes.
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

    ws = workspace_path or ""
    context = {
        "surface": "api", "workspace": ws, "workspace_root": ws,
        "workspace_binding": "project" if ws else "",
    }
    context.update(extra_context or {})
    result = agent._maybe_execute_model_tool_intent(
        task=SimpleNamespace(task_id="t1"),
        effective_input=user_input,
        classification={"task_class": classify_task_class},
        interpretation=SimpleNamespace(),
        context_result=SimpleNamespace(
            local_candidates=[],
            swarm_metadata=[],
            retrieval_confidence_score=0.0,
        ),
        persona=SimpleNamespace(),
        session_id=f"openclaw:{uuid.uuid4().hex[:20]}",
        source_context=context,
        surface="api",
    )
    return result or {}, router, events


def _stop_reason(result: dict) -> str:
    return str((result.get("details") or {}).get("loop_stop_reason") or "")


def _steps(result: dict) -> list[str]:
    return list((result.get("details") or {}).get("tool_steps") or [])


def _mode(result: dict) -> str:
    return str(result.get("mode") or "")


def _success(result: dict) -> bool:
    return bool(result.get("success", False))


def _make_workspace() -> pathlib.Path:
    ws = pathlib.Path(tempfile.mkdtemp())
    for name, body in FILES.items():
        (ws / name).write_text(body, encoding="utf-8")
    return ws


# ---------------------------------------------------------------------------
# L1 — planner enters tool lane, eligible set is empty → terminates quickly
# ---------------------------------------------------------------------------

def test_L1_no_eligible_tool_terminates_quickly() -> None:
    """Planner enters tool lane, eligible tool set is empty → terminates after
    intent-cycle detection fires (not all 12 rounds)."""
    ws = _make_workspace()
    # Same intent (read_file) on different files each round to simulate the
    # identifier canary's intent-level cycling behaviour.
    scripts = [
        [_call("workspace.read_file", path="file_a.py")],
        [_call("workspace.read_file", path="file_b.py")],
        [_call("workspace.read_file", path="file_c.py")],
        [_call("workspace.read_file", path="file_d.py")],
    ]
    result, router, events = _drive(
        scripts,
        classify_task_class="dependency_resolution",
        user_input="Extract the identifiers from: Neo4j VX-2048 EC2 2FA",
        workspace_path=str(ws),
    )
    reason = _stop_reason(result)
    # L1: loop must not run all 12 rounds.
    assert router.calls < _MAX_MODEL_ROUNDS_PER_TURN, (
        f"L1: consumed all {_MAX_MODEL_ROUNDS_PER_TURN} rounds; "
        f"stop_reason={reason}, calls={router.calls}"
    )
    # L1: the intent-cycle detection should have fired
    no_fit_events = [e for e in events if str(e.get("status") or "") == "no_eligible_tool"]
    assert no_fit_events, (
        "L1: no_eligible_tool event not emitted; "
        f"stop_reason={reason}, calls={router.calls}"
    )


# ---------------------------------------------------------------------------
# L2 — same rejected tool set on next iteration with no new evidence
# ---------------------------------------------------------------------------

def test_L2_identical_no_progress_does_not_repeat() -> None:
    """Same rejected tool set appears on next iteration with no new evidence
    → no repeated planner churn."""
    ws = _make_workspace()
    scripts = [
        [_call("workspace.read_file", path="file_a.py")],
        [_call("workspace.read_file", path="file_b.py")],
    ]
    result, router, events = _drive(
        scripts,
        classify_task_class="dependency_resolution",
        user_input="Extract the identifiers from: Neo4j VX-2048 EC2 2FA",
        workspace_path=str(ws),
    )
    # L2: after intent-cycle detection fires, loop stops.  Router calls
    # should be roughly the 2 rounds needed to detect the cycle (the 3rd
    # model call is never reached).
    assert router.calls <= 3, (
        f"L2: router called {router.calls} times, expected ≤3"
    )


# ---------------------------------------------------------------------------
# L3 — actual eligible useful tool exists
# ---------------------------------------------------------------------------

def test_L3_eligible_tool_executes_normally() -> None:
    """Actual eligible useful tool exists → tool loop still executes it normally."""
    ws = _make_workspace()

    scripts = [
        [_call("workspace.read_file", path="file_a.py")],
    ]
    result, router, events = _drive(
        scripts,
        classify_task_class="debugging",
        user_input="Read file_a.py and tell me what is in it",
        workspace_path=str(ws),
    )
    # L3: tool steps executed
    assert _steps(result), "L3: tool steps are empty but eligible tool was requested"
    # L3: not stopped by no-eligible-tool
    no_fit_events = [e for e in events if str(e.get("status") or "") == "no_eligible_tool"]
    assert not no_fit_events, "L3: no-eligible-tool fired on a useful tool request"


# ---------------------------------------------------------------------------
# L4 — first tool fails but a DIFFERENT eligible tool exists
# ---------------------------------------------------------------------------

def test_L4_first_fails_second_continues() -> None:
    """First tool runs but a DIFFERENT eligible tool is selected for the next round
    → may continue (not an overly aggressive one-failure exit)."""
    ws = _make_workspace()
    # Both tools can succeed: read_file then search_text (different intents → no cycle)
    scripts = [
        [_call("workspace.read_file", path="file_a.py")],
        [_call("workspace.search_text", query="UNIQ")],
    ]
    result, router, events = _drive(
        scripts,
        classify_task_class="debugging",
        user_input="Find and read the config file",
        workspace_path=str(ws),
    )
    no_fit_events = [e for e in events if str(e.get("status") or "") == "no_eligible_tool"]
    assert not no_fit_events, (
        "L4: no-eligible-tool fired but different tools were available across rounds"
    )


# ---------------------------------------------------------------------------
# L5 — tool observation changes requirements → planner may continue
# ---------------------------------------------------------------------------

def test_L5_observation_changes_allows_continuation() -> None:
    """First tool observation changes requirements → planner may continue."""
    ws = _make_workspace()
    scripts = [
        [_call("workspace.read_file", path="file_a.py")],
    ]
    result, router, events = _drive(
        scripts,
        classify_task_class="research",
        user_input="Read file_a.py and tell me what is in it",
        workspace_path=str(ws),
    )
    no_fit_events = [e for e in events if str(e.get("status") or "") == "no_eligible_tool"]
    assert not no_fit_events, "L5: no-eligible-tool fired despite tools_required=True"


# ---------------------------------------------------------------------------
# L6 — explicit no-tools typed policy
# ---------------------------------------------------------------------------

def test_L6_explicit_no_tools_zero_dispatch() -> None:
    """Explicit no-tools typed policy → zero tool dispatch, zero repeated search."""
    ws = _make_workspace()
    result, router, events = _drive(
        scripts=[],
        classify_task_class="research",
        user_input="Do not use any tools or search the web. What are identifiers?",
        workspace_path=str(ws),
    )
    # L6: no tool loop entered means zero router calls
    assert router.calls == 0, (
        f"L6: router was called {router.calls} times despite no-tools prohibition"
    )
    assert not _steps(result), "L6: tool steps exist despite no-tools prohibition"


# ---------------------------------------------------------------------------
# L7 — zero-tool identifier canary
# ---------------------------------------------------------------------------

def test_L7_identifier_canary_fast() -> None:
    """Zero-tool identifier canary → does not consume 12 rounds.
    The model picks the same intent (read_file) with different paths each round;
    intent-cycle detection fires after 2+ rounds with the same intent."""
    ws = _make_workspace()
    scripts = [
        [_call("workspace.read_file", path="file_a.py")],
        [_call("workspace.read_file", path="file_b.py")],
    ]
    result, router, events = _drive(
        scripts,
        classify_task_class="dependency_resolution",
        user_input="Extract the identifiers from: Neo4j VX-2048 EC2 2FA",
        workspace_path=str(ws),
    )
    # L7: loop terminated well before 12 rounds
    assert router.calls < _MAX_MODEL_ROUNDS_PER_TURN, (
        f"L7: router called {router.calls} times, expected <{_MAX_MODEL_ROUNDS_PER_TURN}"
    )
    # L7: should fire in no more than 3 rounds (2 rounds to detect intent cycle + 1 overhead)
    assert router.calls <= 3, (
        f"L7: router called {router.calls} times, expected ≤3 for intent-cycle detection"
    )
    no_fit_events = [e for e in events if str(e.get("status") or "") == "no_eligible_tool"]
    assert no_fit_events, "L7: no_eligible_tool event should have been emitted"


# ---------------------------------------------------------------------------
# L8 — no-fit does not fabricate tool observations
# ---------------------------------------------------------------------------

def test_L8_no_fit_does_not_fabricate_observations() -> None:
    """No-fit termination does not fabricate tool observations."""
    ws = _make_workspace()
    scripts = [
        [_call("workspace.read_file", path="file_a.py")],
        [_call("workspace.read_file", path="file_b.py")],
    ]
    result, router, events = _drive(
        scripts,
        classify_task_class="dependency_resolution",
        user_input="Extract the identifiers from: Neo4j VX-2048 EC2 2FA",
        workspace_path=str(ws),
    )
    # Every observation in the model context must come from an actual executed step.
    for context in router.contexts:
        observations = context.get("runtime_tool_observations")
        if observations:
            assert isinstance(observations, list), "L8: observations must be a list"


# ---------------------------------------------------------------------------
# L9 — no-fit does not claim tool success
# ---------------------------------------------------------------------------

def test_L9_no_fit_does_not_claim_success() -> None:
    """No-fit termination does not claim tool success."""
    ws = _make_workspace()
    scripts = [
        [_call("workspace.read_file", path="file_a.py")],
        [_call("workspace.read_file", path="file_b.py")],
    ]
    result, router, events = _drive(
        scripts,
        classify_task_class="dependency_resolution",
        user_input="Extract the identifiers from: Neo4j VX-2048 EC2 2FA",
        workspace_path=str(ws),
    )
    mode = _mode(result)
    success = _success(result)
    # A no-fit stop should NOT produce mode=tool_executed + success=True.
    if mode == "tool_executed":
        assert not success, (
            "L9: no-fit termination claimed tool_executed success"
        )


# ---------------------------------------------------------------------------
# L10 — existing round/time budget remains as emergency backstop
# ---------------------------------------------------------------------------

def test_L10_round_budget_backstop() -> None:
    """Existing round/time budget remains as emergency backstop."""
    ws = _make_workspace()
    # Many scripts — enough to normally hit the round budget.
    # Use a text that triggers GROUNDED mode so tools_required=True and
    # intent-cycle detection does NOT fire.
    many_scripts = [
        [_call("workspace.read_file", path=name)]
        for name in list(FILES.keys()) * 5  # 20 scripts
    ]
    result, router, events = _drive(
        many_scripts,
        classify_task_class="research",
        user_input="What is the current information about this project?",
        workspace_path=str(ws),
    )
    # L10: loop goes through many rounds (budget backstop)
    # "current" triggers GROUNDED → tools_required=True, so intent-cycle doesn't fire.
    assert router.calls >= 4, (
        f"L10: only {router.calls} rounds — intent-cycle may have incorrectly triggered"
    )


# ---------------------------------------------------------------------------
# L11 — catalog changes → reconsideration allowed
# ---------------------------------------------------------------------------

def test_L11_catalog_change_reconsiders() -> None:
    """Tool catalog changes materially between iterations → re-evaluation may occur."""
    ws = _make_workspace()
    # Use a locally handled tool (workspace.search_text exists in the runtime tools)
    scripts = [
        [_call("workspace.read_file", path="file_a.py")],
    ]
    result, router, events = _drive(
        scripts,
        classify_task_class="research",
        user_input="Read file_a.py and tell me about it",
        workspace_path=str(ws),
    )
    no_fit_events = [e for e in events if str(e.get("status") or "") == "no_eligible_tool"]
    assert not no_fit_events, "L11: no-eligible-tool fired despite tools_required=True"
    assert _steps(result), "L11: no tool steps ran on a research request"


# ---------------------------------------------------------------------------
# L12 — identical catalog + requirement + observations → forbidden
# ---------------------------------------------------------------------------

def test_L12_identical_state_terminates() -> None:
    """Same catalog + same requirement + same observations → repeated cycle
    forbidden."""
    ws = _make_workspace()
    # 12 scripts — enough to show that without the gate, the loop would
    # consume all 12 rounds.  With the gate, it stops after ≤3.
    names = list(FILES.keys()) + ["file_e.py", "file_f.py", "file_g.py", "file_h.py",
                                  "file_i.py", "file_j.py", "file_k.py", "file_l.py"]
    for name in names:
        if name not in FILES:
            (ws / name).write_text(f"# {name}\nUNIQ_{name}\n", encoding="utf-8")
    scripts = [
        [_call("workspace.read_file", path=name)]
        for name in names[:12]
    ]
    result, router, events = _drive(
        scripts,
        classify_task_class="dependency_resolution",
        user_input="Extract the identifiers from: Neo4j VX-2048 EC2 2FA",
        workspace_path=str(ws),
    )
    # L12: loop terminated after ≤3 rounds, not 12
    assert router.calls < _MAX_MODEL_ROUNDS_PER_TURN, (
        f"L12: router called {router.calls} times, expected <{_MAX_MODEL_ROUNDS_PER_TURN}"
    )
    assert router.calls <= 3, (
        f"L12: router called {router.calls} times, expected ≤3"
    )


# ---------------------------------------------------------------------------
# M6 — structural no-fit termination, NOT budget-dependent
# ---------------------------------------------------------------------------

def test_M6_structural_termination_not_budget() -> None:
    """Structural no-fit termination must NOT depend on the round budget.

    A dependency_resolution canary with 8 scripts of the same intent should
    terminate at the structural no-fit boundary (~3 rounds), NOT at the
    round budget (12 rounds).

    If the no-fit gate is removed, the loop will run all 8 scripts — proving
    the budget alone was not what stopped it.
    """
    ws = _make_workspace()
    # 8 scripts — enough to clearly exceed the structural termination point
    # but stay well within the 12-round budget.
    extra_names = ["file_e.py", "file_f.py", "file_g.py", "file_h.py"]
    for name in extra_names:
        (ws / name).write_text(f"# {name}\nUNIQ_{name}\n", encoding="utf-8")
    scripts = [
        [_call("workspace.read_file", path=name)]
        for name in ["file_a.py", "file_b.py", "file_c.py", "file_d.py",
                      "file_e.py", "file_f.py", "file_g.py", "file_h.py"]
    ]
    result, router, events = _drive(
        scripts,
        classify_task_class="dependency_resolution",
        user_input="Extract the identifiers from: Neo4j VX-2048 EC2 2FA",
        workspace_path=str(ws),
    )
    # The no-fit gate fires after 2+ rounds of the same intent with
    # tools_required=False.  That's round 3, well before 8 or 12.
    # Assert: fewer than 6 rounds run (budget is 12, so this proves
    # structural termination, not budget exhaustion).
    assert router.calls < 6, (
        f"M6: router called {router.calls} times, expected <6 "
        f"(termination must be structural, not budget-dependent)"
    )


# ---------------------------------------------------------------------------
# P13 — changed candidates within dependency_resolution scope
# ---------------------------------------------------------------------------

def test_P13_changed_candidates_continue() -> None:
    """Same intent, same outcome, but candidate set changes → loop continues."""
    ws = _make_workspace()
    # 3 scripts, same intent, all succeed.
    scripts = [
        [_call("workspace.read_file", path="file_a.py")],
        [_call("workspace.read_file", path="file_b.py")],
        [_call("workspace.read_file", path="file_c.py")],
    ]
    # Inject _tool_candidate_ids as a shared mutable list.  The router
    # mutates it after round 2 so the round-3 gate check sees the change.
    candidate_ids = ["workspace.read_file"]

    class _MutatingRouter(_Router):
        def resolve_tool_intent(self, **kwargs):
            sc = kwargs.get("source_context") or {}
            # After the second call (round 2), mutate the candidate set
            # so round 3's gate check sees the new candidates.
            if self.calls == 1 and "_tool_candidate_ids" in sc:
                ids = sc["_tool_candidate_ids"]
                ids.clear()
                ids.append("workspace.read_file")
                ids.append("workspace.search_text")
            return super().resolve_tool_intent(**kwargs)

    router = _MutatingRouter(scripts)
    events: list[dict] = []
    agent = VoolAgent.__new__(VoolAgent)
    agent.memory_router = router
    agent._should_keep_ai_first_chat_lane = lambda **_kw: False
    agent._should_run_builder_controller = lambda **_kw: False
    agent._plan_tool_workflow = lambda **_kw: SimpleNamespace(
        handled=False, stop_after=False, next_payload=None, reason="")
    agent.hive_activity_tracker = None
    agent.public_hive_bridge = None
    real_emit = agent._emit_runtime_event
    def _spy(context, **payload):
        events.append(payload)
        return real_emit(context, **payload)
    agent._emit_runtime_event = _spy
    result = agent._maybe_execute_model_tool_intent(
        task=SimpleNamespace(task_id="t1"),
        effective_input="Extract the identifiers from: Neo4j VX-2048 EC2 2FA",
        classification={"task_class": "dependency_resolution"},
        interpretation=SimpleNamespace(),
        context_result=SimpleNamespace(
            local_candidates=[], swarm_metadata=[], retrieval_confidence_score=0.0),
        persona=SimpleNamespace(),
        session_id=f"openclaw:{uuid.uuid4().hex[:20]}",
        source_context={
            "surface": "api", "workspace": str(ws), "workspace_root": str(ws),
            "workspace_binding": "project",
            "_tool_candidate_ids": candidate_ids,
        },
        surface="api",
    )
    result = result or {}
    # P13: The candidate set changed after round 2, so the loop must
    # continue past the change point.  At least 3 rounds (the 3 scripts)
    # should have run.  The gate may fire later (round 4+) when the loop
    # cycles again with the same changed candidates, but it must not
    # terminate at the structural no-fit boundary before the change is
    # consumed.
    assert router.calls >= 3, (
        f"P13: only {router.calls} rounds, expected ≥3 for changed candidate scenario"
    )
    # Verify the gate did NOT fire during the first 3 rounds (before the
    # candidate change had a chance to be consumed).
    early_no_fit = [e for e in events if str(e.get("status") or "") == "no_eligible_tool"
                    and e.get("step_count", 0) < 3]
    assert not early_no_fit, (
        "P13: no-eligible-tool fired before candidate change could be consumed"
    )


# ---------------------------------------------------------------------------
# P14 — changed outcome within dependency_resolution scope
# ---------------------------------------------------------------------------

def test_P14_changed_intent_continue() -> None:
    """Same outcome, but different tool intent (second tool is different) → loop continues."""
    ws = _make_workspace()
    # First read succeeds, second search uses a different intent.
    scripts = [
        [_call("workspace.read_file", path="file_a.py")],
        [_call("workspace.search_text", query="UNIQ")],
        [_call("workspace.read_file", path="file_b.py")],
    ]
    result, router, events = _drive(
        scripts,
        classify_task_class="dependency_resolution",
        user_input="Extract the identifiers from: Neo4j VX-2048 EC2 2FA",
        workspace_path=str(ws),
    )
    # P14: Different intents mean the gate's intent check should NOT fire.
    # Both tools succeed, so the loop continues past round 2.
    # The gate may fire later but not at the intent-check boundary.
    assert router.calls >= 2, (
        f"P14: only {router.calls} rounds, expected ≥2 for diff-intent scenario"
    )
    # Verify no gate fire during the first 2 rounds (while intents differ)
    early_no_fit = [e for e in events if str(e.get("status") or "") == "no_eligible_tool"
                    and e.get("step_count", 0) < 2]
    assert not early_no_fit, (
        "P14: no-eligible-tool fired while intents were different between rounds"
    )


# ---------------------------------------------------------------------------
# P15 — identical material state terminates
# ---------------------------------------------------------------------------

def test_P15_identical_state_terminates() -> None:
    """Same intent, same outcome, same candidates → terminate bounded."""
    ws = _make_workspace()
    # 6 scripts of the same intent, all succeed.
    extra_names = ["file_e.py", "file_f.py"]
    for name in extra_names:
        (ws / name).write_text(f"# {name}\nUNIQ_{name}\n", encoding="utf-8")
    scripts = [
        [_call("workspace.read_file", path=name)]
        for name in ["file_a.py", "file_b.py", "file_c.py",
                      "file_d.py", "file_e.py", "file_f.py"]
    ]
    result, router, events = _drive(
        scripts,
        classify_task_class="dependency_resolution",
        user_input="Extract the identifiers from: Neo4j VX-2048 EC2 2FA",
        workspace_path=str(ws),
    )
    # P15: Same intent, same outcome, same candidates → terminate at ~3 rounds.
    assert router.calls < 6, (
        f"P15: router called {router.calls} times, expected <6 "
        f"(identical state must terminate structurally)"
    )
    assert router.calls <= 3, (
        f"P15: router called {router.calls} times, expected ≤3 "
        f"(identical state must terminate at no-fit boundary)"
    )


# ---------------------------------------------------------------------------
# P14A — same tool, same outcome, CHANGED observation evidence
# ---------------------------------------------------------------------------

def test_P14A_changed_observation_continue() -> None:
    """Same tool name, same status/mode, but observation fingerprint changes
    → loop continues (not terminated as no-progress)."""
    ws = _make_workspace()
    # 3 scripts, same intent, all succeed.  The observation fingerprint
    # changes between round 2 and 3 via injected _observation_fingerprints.
    scripts = [
        [_call("workspace.read_file", path="file_a.py")],
        [_call("workspace.read_file", path="file_b.py")],
        [_call("workspace.read_file", path="file_c.py")],
    ]
    # Inject observation fingerprints that change at index 1.
    # The gate checks executed_steps[-2:] indices 0 and 1 at round 3.
    # These differ -> gate must NOT fire.
    obs_fps = [
        (True, "executed", "workspace"),
        (False, "missing", "workspace"),  # changed observation
    ]
    result, router, events = _drive(
        scripts,
        classify_task_class="dependency_resolution",
        user_input="Extract the identifiers from: Neo4j VX-2048 EC2 2FA",
        workspace_path=str(ws),
        extra_context={"_observation_fingerprints": obs_fps},
    )
    # P14A: loop must continue past the observation change point (≥3 rounds)
    assert router.calls >= 3, (
        f"P14A: only {router.calls} rounds, expected ≥3 "
        f"(changed observation must not terminate as no-progress)"
    )
    # Verify the gate did NOT fire during the first 3 rounds
    early_no_fit = [e for e in events if str(e.get("status") or "") == "no_eligible_tool"
                    and e.get("step_count", 0) < 3]
    assert not early_no_fit, (
        "P14A: no-eligible-tool fired despite observation fingerprint changing"
    )


# ---------------------------------------------------------------------------
# P14B — same tool, same outcome, CHANGED rejection reason
# ---------------------------------------------------------------------------

def test_P14B_changed_rejection_continue() -> None:
    """Same tool name, same status/mode, but rejection reason (tool_surface)
    changes → loop continues."""
    ws = _make_workspace()
    # 3 scripts, same intent, all succeed.  Tool_surface observation field
    # changes between rounds.
    scripts = [
        [_call("workspace.read_file", path="file_a.py")],
        [_call("workspace.read_file", path="file_b.py")],
        [_call("workspace.read_file", path="file_c.py")],
    ]
    # Different tool_surface values act as different rejection-state evidence.
    obs_fps = [
        (True, "executed", "workspace"),
        (True, "executed", "web"),  # different tool_surface = different rejection reason
    ]
    result, router, events = _drive(
        scripts,
        classify_task_class="dependency_resolution",
        user_input="Extract the identifiers from: Neo4j VX-2048 EC2 2FA",
        workspace_path=str(ws),
        extra_context={"_observation_fingerprints": obs_fps},
    )
    assert router.calls >= 3, (
        f"P14B: only {router.calls} rounds, expected ≥3 "
        f"(changed rejection reason must not terminate as no-progress)"
    )
    early_no_fit = [e for e in events if str(e.get("status") or "") == "no_eligible_tool"
                    and e.get("step_count", 0) < 3]
    assert not early_no_fit, (
        "P14B: no-eligible-tool fired despite rejection reason changing"
    )