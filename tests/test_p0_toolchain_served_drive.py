"""Served drive — one turn through the real tool loop discovers and executes all four tool kinds.

The model is scripted at the provider seam and nowhere below it (the established rig from
tests/test_tool_loop_liveness.py): the loop, the executor, the permission gate, the plugin
subprocess, the MCP stdio server and the execution records are all real. What is asserted is the
executed step list, the per-call statuses and the records — never prose.

Scope stated plainly: this is the in-process tool loop (`_maybe_execute_model_tool_intent`, the
body every served turn runs), not an HTTP daemon. A served daemon drive is out of this lane's
reach on this host (cloud-only testing rule, no daemon edits).
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from tests._toolchain_fixtures import (
    PLUGIN_ID,
    SKILL_MARKER,
    calls_logged,
    make_plugin,
    read_only_pin,
    reset_toolchain_state,
    widget_skill,
    write_mcp_config,
)


def _call(intent: str, **arguments):
    return SimpleNamespace(intent=intent, arguments=arguments, call_id=f"c-{intent}", name=intent)


def _decision(batch, *, closing: str = ""):
    if closing:
        return SimpleNamespace(
            output_text=closing, provider_id="p", used_model=True, confidence=0.8, trust_score=0.8,
            validation_state="validated", details={}, source="provider_execution", model_name="m",
            provider_name="p", cache_hit=False, candidate_id=None, failover_used=False,
            structured_output={"intent": "respond.direct", "arguments": {"message": closing}},
            tool_calls=(_call("respond.direct", message=closing),), task_hash="h",
        )
    head = batch[0]
    return SimpleNamespace(
        output_text="", provider_id="p", used_model=True, confidence=0.8, trust_score=0.8,
        validation_state="validated", details={}, source="provider_execution", model_name="m",
        provider_name="p", cache_hit=False, candidate_id=None, failover_used=False,
        structured_output={"intent": head.intent, "arguments": dict(head.arguments)},
        tool_calls=tuple(batch), task_hash="h",
    )


class _Router:
    """Scripted at the provider seam. Each round returns the next scripted batch; then closes.

    The offer the REAL router would have built is recorded per round through the production
    assembly seam, so the drive can assert the scripted calls were all discoverable.
    """

    def __init__(self, scripts, user_text):
        self.scripts = list(scripts)
        self.user_text = user_text
        self.calls = 0
        self.offers: list[set[str]] = []
        self.skill_names: list[list[str]] = []
        self.stamps: list[dict] = []

    def resolve(self, **_kwargs):
        return _decision([], closing="All done.")

    def resolve_tool_intent(self, **kwargs):
        from core.tool_offer_assembly import assemble_tool_offer

        context = dict(kwargs.get("source_context") or {})
        offer = assemble_tool_offer(
            user_text=self.user_text, task_class="debugging", source_context=context,
            family_hints=("plugin", "mcp"),
        )
        self.offers.append({str(s.get("intent")) for s in offer.specs})
        self.skill_names.append([s["name"] for s in offer.skill_guidance.skills])
        self.stamps.append(dict(context.get("_tool_offer") or {}))
        index = self.calls
        self.calls += 1
        if index < len(self.scripts):
            return _decision(self.scripts[index])
        return _decision([], closing="No more tools needed.")


@pytest.fixture()
def served_world(tmp_path, monkeypatch):
    from core import plugin_tools
    from core.mode_permission_policy import reset_mode_permission_state
    from core.runtime_flags import override

    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("SECRET_TOKEN_FOR_DRIVE", "must-not-cross")
    # This pack's contract is the PLUGIN + MCP drive: the widget skill must take its seat.
    # The native library (a separate lane, its own pack) would otherwise claim the skill
    # budget on debugging-class turns, so it is isolated to an empty root here.
    empty_native = tmp_path / "no-native-skills"
    empty_native.mkdir()
    monkeypatch.setenv("VOOL_NATIVE_SKILLS_DIR", str(empty_native))
    plugin_dir = make_plugin(tmp_path, skills={"widget-report": widget_skill()})
    monkeypatch.setenv("VOOL_PLUGINS_DIR", str(tmp_path))
    write_mcp_config(tmp_path, monkeypatch, trust=read_only_pin("add"))
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "README.md").write_text("widget report source\n", encoding="utf-8")
    reset_mode_permission_state()
    reset_toolchain_state()
    with override("plugin_runtime_tools", True):
        loaded, errors = plugin_tools.load_all(tmp_path)
        assert loaded and not errors
        yield plugin_dir, ws
    reset_mode_permission_state()
    reset_toolchain_state()


def _drive(scripts, *, user_text, workspace, session_id):
    from apps.vool_agent import VoolAgent

    router = _Router(scripts, user_text)
    events: list[dict] = []
    agent = VoolAgent.__new__(VoolAgent)
    agent.memory_router = router
    agent._should_keep_ai_first_chat_lane = lambda **_kw: False
    agent._should_run_builder_controller = lambda **_kw: False
    agent._plan_tool_workflow = lambda **_kw: SimpleNamespace(handled=False, stop_after=False, next_payload=None, reason="")
    agent.hive_activity_tracker = None
    agent.public_hive_bridge = None
    real_emit = agent._emit_runtime_event

    def _spy(context, **payload):
        events.append(payload)
        return real_emit(context, **payload)

    agent._emit_runtime_event = _spy
    context = {
        "surface": "api", "workspace": str(workspace), "workspace_root": str(workspace),
        "workspace_binding": "project", "runtime_session_id": session_id,
    }
    result = agent._maybe_execute_model_tool_intent(
        task=SimpleNamespace(task_id="t1"),
        effective_input=user_text,
        classification={"task_class": "debugging"},
        interpretation=SimpleNamespace(),
        context_result=SimpleNamespace(local_candidates=[], swarm_metadata=[], retrieval_confidence_score=0.0),
        persona=SimpleNamespace(),
        session_id=session_id,
        source_context=context,
        surface="api",
    )
    return result or {}, router, events, context


def test_one_turn_discovers_and_executes_builtin_plugin_and_mcp_tools(served_world) -> None:
    from core import execution_records

    plugin_dir, ws = served_world
    session_id = f"openclaw:{uuid.uuid4().hex[:20]}"
    execution_records.clear(session_id)
    user_text = "prepare the widget report: read README.md, then use pack.echo and the mcp add tool"
    scripts = [
        [_call("workspace.read_file", path="README.md")],
        [_call(f"{PLUGIN_ID}.echo", text="widget")],
        [_call("mcp.stub.add", a=40, b=2)],
        [_call("email.send", to="x@example.com", subject="widget", body="report")],
    ]
    result, router, events, _context = _drive(scripts, user_text=user_text, workspace=ws, session_id=session_id)

    steps = [str(s) for s in (result.get("details") or {}).get("tool_steps") or []]
    assert "workspace.read_file" in steps
    assert f"{PLUGIN_ID}.echo" in steps
    assert "mcp.stub.add" in steps
    assert "email.send" in steps
    # A read_only child runs with ZERO writable roots (the kernel makes the claim mechanical),
    # so its file-based call log is correctly denied: the child-ran proof is its own stdout --
    # the record below carries the echoed resolved_target only the child could produce.
    assert not calls_logged(plugin_dir)

    records = {r.intent: r for r in execution_records.records_for(session_id)}
    assert records["workspace.read_file"].ok
    assert records[f"{PLUGIN_ID}.echo"].ok and records[f"{PLUGIN_ID}.echo"].resolved_target == "widget"
    assert records["mcp.stub.add"].ok
    assert records["email.send"].ok is False
    assert records["email.send"].status in {"disabled", "unsupported", "blocked_by_mode"}

    # Discovery: every scripted call was in the assembled offer of some round (the model could
    # only have chosen what it was shown), and the skill guided the turn with provenance.
    offered = set().union(*router.offers)
    for intent in ("workspace.read_file", f"{PLUGIN_ID}.echo", "mcp.stub.add"):
        assert intent in offered, (intent, router.offers)
    assert any("widget-report" in names for names in router.skill_names)
    # The seam stamps provenance on the turn context it is handed (the loop hands the router a
    # per-round copy, so the stamp is read back from the round's context, not the caller's).
    assert router.stamps and all(s.get("provenance") == "core.tool_offer_assembly" for s in router.stamps)

    # The disabled tool explained itself in the loop's own events rather than claiming success.
    email_events = [e for e in events if e.get("tool_name") == "email.send"]
    assert email_events
    assert not any(str(e.get("status")) in {"ok", "executed", "tool_executed"} for e in email_events)


def test_the_offer_for_the_drive_carries_the_skill_body(served_world) -> None:
    from core.tool_offer_assembly import assemble_tool_offer

    offer = assemble_tool_offer(user_text="prepare the widget report", task_class="debugging")
    assert SKILL_MARKER in offer.skill_guidance.text
    assert offer.skill_guidance.skills[0]["plugin_id"] == PLUGIN_ID
