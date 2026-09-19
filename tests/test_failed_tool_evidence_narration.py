"""A failed tool's executed observation must reach the user through the loop's own synthesis.

The defect this file pins (measured by the wallet/contacts lane on the unmodified base, both
transports): on an evidence-required turn, the tool loop executed the tool, the tool owner
returned a TYPED refusal, and the loop's handoff (`return None` after a non-executed mode when
``requirements_for(...).tools_required``) discarded ``executed_steps`` -- the tools-less research
continuation answered from nothing and the owner's typed refusal never reached the user.

The repair: when real tools ran, the loop breaks to its own synthesis instead of handing the turn
back, so the executed observations are narrated (with the deterministic grounded summary of the
same steps as the fallback), the turn reports ``tool_failed``/``success=False`` -- a narrated
refusal is a truthful answer, never a completed effect -- and the empty-evidence fallthrough that
serves no-tool-ran turns is preserved unchanged.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

from apps.vool_agent import VoolAgent
from tests.test_tool_loop_liveness import _call, _Router, _tool_decision

SAVE_INPUT = "Save Alex Chen as a contact: work email alex.chen@example.test, Telegram alexchen_kiln."
UPDATE_INPUT = "Update the contact Alex Chen: work email not-a-valid-email."


def _drive(scripts, *, user_input: str, extra_context: dict | None = None):
    """One turn through the real loop with the REAL contacts tools (scripted model only)."""
    router = _Router(scripts)
    events: list[dict] = []
    agent = VoolAgent.__new__(VoolAgent)
    agent.memory_router = router
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
    # Auto mode so a WRITE tool executes (and can fail typed) rather than pausing for approval:
    # the defect under test is the executed-failure handoff, not the approval preview.
    context = {"surface": "api", "workspace": "", "workspace_root": "", "workspace_binding": "", "operating_mode": "auto"}
    context.update(extra_context or {})
    result = agent._maybe_execute_model_tool_intent(
        task=SimpleNamespace(task_id="t-" + uuid.uuid4().hex[:8]),
        effective_input=user_input,
        classification={"task_class": "integration_orchestration"},
        interpretation=SimpleNamespace(),
        context_result=SimpleNamespace(local_candidates=[], swarm_metadata=[], retrieval_confidence_score=0.0),
        persona=SimpleNamespace(),
        session_id="openclaw:" + uuid.uuid4().hex[:20],
        source_context=context,
        surface="api",
    )
    return result, router, events


def _steps(result: dict) -> list[str]:
    return list((result.get("details") or {}).get("tool_steps") or [])


# --- the repair ---------------------------------------------------------------------------------------


def test_a_failed_save_narrates_the_typed_refusal_and_reports_failure() -> None:
    """The wallet lane's exact failure shape, on the contacts owner: the tool RUNS, the owner
    answers a typed refusal, and that refusal is the turn's evidence -- it must reach the user."""
    scripts = [
        [_call("contacts.save", name="Alex Chen", email="not-a-valid-email")],
    ]
    result, router, events = _drive(scripts, user_input=SAVE_INPUT)
    assert result is not None, "the loop must not hand an executed-tool turn back to a tools-less lane"
    assert _steps(result) == ["contacts.save"], result
    assert str(result.get("mode")) == "tool_failed", result
    assert result.get("success") is False and result.get("task_outcome") == "failed", result
    response = str(result.get("response") or "")
    assert "contacts.save" in response, response[:400]
    assert "not-a-valid-email" in response or "invalid" in response.lower(), response[:600]
    # No contact was actually written by the failed turn (the store answers through its
    # module-level search seam; absence of the seam skips the effect check).
    try:
        from core.contacts import store as _store_module

        found = _store_module.search_contacts("Alex Chen") if hasattr(_store_module, "search_contacts") else []
        assert not [c for c in (found or []) if c.get("display_name") == "Alex Chen"], found
    except AttributeError:
        pass


def test_a_second_distinct_failed_tool_also_reaches_the_user() -> None:
    """A different tool, a different typed failure, the same law: evidence is narrated."""
    scripts = [
        [_call("contacts.update", contact_id="no-such-contact", email="also-not-an-email")],
    ]
    result, _router, _events = _drive(scripts, user_input=UPDATE_INPUT)
    assert result is not None and _steps(result) == ["contacts.update"], result
    assert str(result.get("mode")) == "tool_failed" and result.get("success") is False, result
    response = str(result.get("response") or "")
    assert "contacts.update" in response, response[:400]


def test_a_successful_tool_then_a_failure_preserves_both_steps() -> None:
    """The success's observation is not lost when a later step fails: both ride the synthesis."""
    scripts = [
        [_call("contacts.search", query="Alex Chen")],
        [_call("contacts.save", name="Alex Chen", email="not-a-valid-email")],
    ]
    result, _router, _events = _drive(scripts, user_input=SAVE_INPUT)
    assert result is not None and _steps(result) == ["contacts.search", "contacts.save"], result
    response = str(result.get("response") or "")
    assert "contacts.search" in response and "contacts.save" in response, response[:600]
    assert str(result.get("mode")) == "tool_failed" and result.get("success") is False, result


def test_nothing_executed_still_falls_through_to_research_honestly() -> None:
    """The empty-evidence fallthrough (nothing ran, research may still retrieve) is untouched:
    a turn whose model produced no usable tool intent returns None, not a fabricated narration."""
    scripts = [
        [_call("contacts.nonsense_tool")],
    ]
    result, _router, _events = _drive(scripts, user_input=SAVE_INPUT)
    # An unknown tool cannot execute; nothing ran. The evidence-required turn hands back to the
    # research path exactly as before the repair (asserted by the fallthrough itself: either the
    # typed rejected-call response or None -- never a success narration with zero steps).
    if result is None:
        return
    assert _steps(result) == [], result
    assert result.get("success") is False, result


def test_a_quoted_example_is_not_executed_as_a_tool() -> None:
    """A pasted example that names a tool call is documentation, not a demand: no execution."""
    quoted = (
        'For example, you might run: contacts.save with arguments {"name": "Quoted Example", '
        '"email": "quoted@example.test"} -- but do not do it now.'
    )
    scripts = [[_call("respond.direct", message="Here is what that call would look like.")]]
    result, _router, _events = _drive(scripts, user_input=quoted)
    steps = _steps(result or {})
    assert "contacts.save" not in steps, (steps, result)
    try:
        from core.contacts import store as _store_module

        found = _store_module.search_contacts("Quoted Example") if hasattr(_store_module, "search_contacts") else []
        assert not [c for c in (found or []) if c.get("display_name") == "Quoted Example"], found
    except AttributeError:
        pass
