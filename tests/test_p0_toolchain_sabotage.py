"""Sabotage proofs — registry population, dispatch and permission are each load-bearing.

Each test removes ONE mechanism and asserts the observable changes. A mechanism whose removal
changes nothing is decoration; these show the convergence lane's wiring is what produces the
behaviour, independently for the three seams the goal names.
"""
from __future__ import annotations

import pytest

from tests._toolchain_fixtures import (
    PLUGIN_ID,
    calls_logged,
    executor_kwargs,
    make_plugin,
    read_only_pin,
    reset_toolchain_state,
    widget_skill,
    write_mcp_config,
)


@pytest.fixture()
def world(tmp_path, monkeypatch):
    from core import plugin_tools
    from core.mode_permission_policy import reset_mode_permission_state
    from core.runtime_flags import override

    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    plugin_dir = make_plugin(tmp_path, skills={"widget-report": widget_skill()})
    monkeypatch.setenv("VOOL_PLUGINS_DIR", str(tmp_path))
    write_mcp_config(tmp_path, monkeypatch, trust=read_only_pin("echo"))
    reset_mode_permission_state()
    reset_toolchain_state()
    with override("plugin_runtime_tools", True):
        loaded, errors = plugin_tools.load_all(tmp_path)
        assert loaded and not errors
        yield plugin_dir
    reset_mode_permission_state()
    reset_toolchain_state()


def _run(intent: str, arguments: dict, **context):
    from core.tool_intent_executor import execute_tool_intent

    return execute_tool_intent({"intent": intent, "arguments": arguments}, **executor_kwargs("s", **context))


# --- registry population ------------------------------------------------------------------------


def test_sabotage_registry_population_hides_plugin_and_mcp_everywhere(world, monkeypatch) -> None:
    from core import tool_registry
    from core.capability_census import capability_census
    from core.capability_graph import model_visible_specs
    from core.runtime_tool_contracts import runtime_tool_contracts

    healthy = capability_census(mode="manual")
    rows = {r.intent: r for r in healthy.rows}
    assert rows[f"{PLUGIN_ID}.echo"].registered and rows["mcp.stub.echo"].registered

    # Builtins only: the registry "forgets" every registered contract.
    monkeypatch.setattr(tool_registry, "registered_tools", lambda: tuple(runtime_tool_contracts()))
    monkeypatch.setattr(tool_registry, "registry_map", lambda: {c.intent: c for c in runtime_tool_contracts()})
    monkeypatch.setattr(tool_registry, "tool_for_intent", lambda i: {c.intent: c for c in runtime_tool_contracts()}.get(i))
    monkeypatch.setattr(tool_registry, "registry_epoch", lambda: tool_registry._epoch + 1000)
    sabotaged = capability_census(mode="manual")
    rows = {r.intent: r for r in sabotaged.rows}
    assert f"{PLUGIN_ID}.echo" not in rows or not rows[f"{PLUGIN_ID}.echo"].registered
    assert "mcp.stub.echo" not in rows or not rows["mcp.stub.echo"].registered
    offered = {s["intent"] for s in model_visible_specs(family_hint="plugin")} | {
        s["intent"] for s in model_visible_specs(family_hint="mcp")
    }
    assert f"{PLUGIN_ID}.echo" not in offered
    out = _run(f"{PLUGIN_ID}.echo", {"text": "x"})
    assert not out.ok and out.status == "unsupported"


def test_sabotage_mcp_registry_sync_drops_pinned_trust(world, monkeypatch) -> None:
    from core.execution import mcp_bridge
    from core.mode_permission_policy import PermissionAction, actions_for_tool

    reset_toolchain_state()
    monkeypatch.setattr(mcp_bridge, "sync_mcp_registry", lambda *a, **k: 0)
    mcp_bridge.mcp_tool_specs()
    from core.tool_registry import tool_for_intent

    assert tool_for_intent("mcp.stub.echo") is None
    assert actions_for_tool("mcp.stub.echo") == (PermissionAction.UNKNOWN_SIDE_EFFECT,)
    out = _run("mcp.stub.echo", {"text": "x"})
    # No contract, no dispatch: the registry sync is what makes an MCP tool exist at all.
    assert not out.ok and out.status == "unsupported"
    assert "stub" in out.response_text


# --- dispatch ------------------------------------------------------------------------------------


def test_sabotage_plugin_dispatch_arm_returns_unsupported_never_success(world, monkeypatch) -> None:
    from core import tool_intent_executor

    good = _run(f"{PLUGIN_ID}.echo", {"text": "alpha"})
    # The read_only child runs with zero writable roots, so the file log stays empty; the echoed
    # resolved_target is the child-ran proof (only the child's stdout can produce it).
    assert good.ok and good.details["resolved_target"] == "alpha"

    monkeypatch.setattr(tool_intent_executor, "_execute_plugin_tool", lambda *a, **k: None)
    bad = _run(f"{PLUGIN_ID}.echo", {"text": "beta"})
    assert not bad.ok
    assert bad.status == "unsupported"
    assert bad.mode == "tool_failed"
    assert "echo:beta" not in bad.response_text  # nothing ran the second time


def test_sabotage_plugin_executor_failure_is_a_structured_failure(world, monkeypatch) -> None:
    from core import plugin_tools

    def _boom(*a, **k):
        raise RuntimeError("executor exploded")

    monkeypatch.setattr(plugin_tools, "execute_plugin_contract", _boom)
    out = _run(f"{PLUGIN_ID}.echo", {"text": "gamma"})
    assert not out.ok
    assert out.status == "handler_failed"
    assert out.mode == "tool_failed"
    assert out.details["observation"]["ok"] is False


# --- permission ----------------------------------------------------------------------------------


def test_sabotage_permission_gate_is_what_stops_a_mutating_plugin_tool(world, monkeypatch, tmp_path) -> None:
    from core import tool_intent_executor
    from core.plugin_tools import plugin_scratch_dir

    target = str(plugin_scratch_dir(PLUGIN_ID) / "gate.txt")
    gated = _run(f"{PLUGIN_ID}.touch", {"path": target})
    assert gated.status == "pending_approval"
    assert calls_logged(world) == []

    # CONVERGENCE, 2026-09-02: two things about this boundary changed under this lane, and the
    # sabotage has to name both or it stops biting.
    #   1. The gate is TWO bound names. The model tool-intent route consults `decide_tool_call`
    #      here; `authorized_tool_execution` imported the same function by value and consults it
    #      again for the machine/fast-path route. Neutralising one leaves the other refusing.
    #   2. The machine route is FAIL-CLOSED: "no decision is a denial". So a gate that returns
    #      None is not a removed gate — it is a denying one, and `blocked_by_mode` is the proof.
    #      Removing this boundary means making it decide ALLOW; that is what a severed gate
    #      actually looks like here, and the mutation landing is what makes the gate load-bearing.
    from core import authorized_tool_execution
    from core.mode_permission_policy import (
        OperatingMode,
        PermissionDecision,
        PermissionEffect,
    )

    def _severed(**kwargs):
        return PermissionDecision(
            effect=PermissionEffect.ALLOW,
            mode=OperatingMode.AUTO,
            actions=(),
            reason="sabotage: permission boundary severed",
        )

    monkeypatch.setattr(tool_intent_executor, "decide_tool_call", _severed)
    monkeypatch.setattr(authorized_tool_execution, "decide_tool_call", _severed)
    ungated = _run(f"{PLUGIN_ID}.touch", {"path": target})
    assert ungated.ok  # only reachable with the gate removed — the gate is the boundary
    assert calls_logged(world) == [f"{PLUGIN_ID}.touch"]


def test_sabotage_declared_actions_read_from_registry_are_load_bearing(world, monkeypatch) -> None:
    from core import tool_registry
    from core.mode_permission_policy import PermissionAction, actions_for_tool

    assert actions_for_tool(f"{PLUGIN_ID}.echo") == (PermissionAction.READ_FILES,)
    monkeypatch.setattr(tool_registry, "registry_map", lambda: {})
    assert actions_for_tool(f"{PLUGIN_ID}.echo") == (PermissionAction.UNKNOWN_SIDE_EFFECT,)


# --- skills --------------------------------------------------------------------------------------


def test_sabotage_skill_ranking_removes_the_injection(world, monkeypatch) -> None:
    from core import plugin_skills
    from core.tool_offer_assembly import assemble_tool_offer, reset_skill_cache

    with_skill = assemble_tool_offer(user_text="widget report", task_class="unknown")
    assert with_skill.skill_guidance.skills
    reset_skill_cache()
    monkeypatch.setattr(plugin_skills, "rank_skills", lambda *a, **k: ())
    without = assemble_tool_offer(user_text="widget report", task_class="unknown")
    assert without.skill_guidance.skills == ()
    assert without.skill_guidance.text == ""


# --- confinement ---------------------------------------------------------------------------------


def test_sabotage_confinement_prefix_is_load_bearing(world, monkeypatch) -> None:
    from core import plugin_executor

    if not plugin_executor.kernel_confinement_available():
        pytest.skip("no kernel confinement backend on this host")
    seen: list[list[str]] = []
    real = plugin_executor._kernel_confinement_prefix

    def _spy(argv, **kwargs):
        wrapped = real(argv, **kwargs)
        seen.append(list(wrapped or []))
        return wrapped

    monkeypatch.setattr(plugin_executor, "_kernel_confinement_prefix", _spy)
    out = _run(f"{PLUGIN_ID}.echo", {"text": "delta"})
    assert out.ok
    assert seen and seen[0][0] != str(world / "bin" / "run")  # the wrapper, not the bare handler
