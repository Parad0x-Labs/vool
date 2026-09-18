"""P0 — a registered plugin tool dispatches through the real boundaries.

RED at 96c2fb96: a plugin contract carries a subprocess handler, ``execute_runtime_tool`` declines
every non-runtime handler, and ``_intent_is_dispatchable`` read only the builtin contract map — so
a registered plugin tool was answered "unsupported" before the permission gate ever saw it, and
nothing on the machine could run it. This pack drives the real executor with a real plugin whose
handler is a real subprocess, and asserts on the permission decision, the confined child, the
effect replay cache and the execution record — never on prose.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

from tests._toolchain_fixtures import (
    PLUGIN_ID,
    calls_logged,
    executor_kwargs,
    make_plugin,
    reset_toolchain_state,
)


@pytest.fixture()
def plugin_world(tmp_path, monkeypatch):
    from core import plugin_tools
    from core.mode_permission_policy import reset_mode_permission_state
    from core.runtime_flags import override

    monkeypatch.delenv("VOOL_MCP_CONFIG", raising=False)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("SECRET_TOKEN_FOR_PLUGIN_TEST", "must-not-cross")
    plugin_dir = make_plugin(tmp_path)
    monkeypatch.setenv("VOOL_PLUGINS_DIR", str(tmp_path))
    reset_toolchain_state()
    reset_mode_permission_state()
    with override("plugin_runtime_tools", True):
        loaded, errors = plugin_tools.load_all(tmp_path)
        assert not errors, errors
        assert loaded and loaded[0].plugin_id == PLUGIN_ID
        yield plugin_dir
    reset_mode_permission_state()
    reset_toolchain_state()


def _run(intent: str, arguments: dict, session: str = "s", **context):
    from core.tool_intent_executor import execute_tool_intent

    return execute_tool_intent({"intent": intent, "arguments": arguments}, **executor_kwargs(session, **context))


def test_registered_plugin_tool_is_dispatchable_not_unsupported(plugin_world) -> None:
    from core.tool_intent_executor import _intent_is_dispatchable

    assert _intent_is_dispatchable(f"{PLUGIN_ID}.echo") is True
    assert _intent_is_dispatchable(f"{PLUGIN_ID}.nope") is False


def test_read_only_plugin_tool_executes_in_manual_mode(plugin_world) -> None:
    out = _run(f"{PLUGIN_ID}.echo", {"text": "alpha"})
    assert out.handled and out.ok, (out.status, out.response_text)
    assert out.status == "executed"
    assert out.mode == "tool_executed"
    assert out.tool_name == f"{PLUGIN_ID}.echo"
    assert "echo:alpha" in out.response_text
    observation = out.details["observation"]
    assert observation["tool_surface"] == "plugin"
    assert observation["intent"] == f"{PLUGIN_ID}.echo"
    assert out.details["resolved_target"] == "alpha"
    # A read_only child runs with ZERO writable roots, so its file-based call log is denied by
    # the kernel (the claim is mechanical); the echoed resolved_target above is the child-ran
    # proof, and the confinement's own denial is asserted here rather than an empty log.
    assert not calls_logged(plugin_world)


def test_mutating_plugin_tool_stops_at_the_permission_gate(plugin_world, tmp_path) -> None:
    """No mode in the context → MANUAL → a create_files action prompts; the handler never runs."""
    target = str(tmp_path / "home" / "should-not-exist.txt")
    out = _run(f"{PLUGIN_ID}.touch", {"path": target})
    assert out.handled and not out.ok
    assert out.status == "pending_approval"
    assert out.details["executed"] is False
    assert out.details["controller_enforced"] is True
    assert "create_files" in out.details["permission_actions"]
    assert calls_logged(plugin_world) == []
    assert not Path(target).exists()


def test_an_approved_mutating_plugin_tool_runs_and_is_replay_cached(plugin_world, tmp_path) -> None:
    from core.mode_permission_policy import resolve_approval
    from core.plugin_tools import plugin_scratch_dir

    scratch_target = str(plugin_scratch_dir(PLUGIN_ID) / f"marker-{uuid.uuid4().hex[:6]}.txt")
    payload = {"intent": f"{PLUGIN_ID}.touch", "arguments": {"path": scratch_target}}
    pending = _run(payload["intent"], payload["arguments"])
    token = str(pending.details["approval_request"]["approval_id"])
    assert resolve_approval(token, decision="allow") is not None

    from core.tool_intent_executor import execute_tool_intent

    first = execute_tool_intent(
        payload,
        **{**executor_kwargs("s", mode_approval_token=token), "checkpoint_id": "cp-1", "step_index": 0},
    )
    assert first.ok, (first.status, first.response_text)
    assert Path(scratch_target).read_text(encoding="utf-8") == "touched\n"
    assert calls_logged(plugin_world) == [f"{PLUGIN_ID}.touch"]

    # Same checkpoint + step + arguments: the effect replays from its receipt, the handler is not
    # run a second time. This is the execution-truth boundary plugins were missing.
    from tests._toolchain_fixtures import internal_scope

    # `create_files` AND `overwrite_existing_files`: the first call created the target, so the
    # re-issue is a write over an existing file. A third-party contract used to keep the cheaper
    # `create_files` row forever because a declaration skipped the argument-sensitive derivation;
    # it no longer does, and the grant has to name what this call actually is.
    scope = internal_scope(
        "test.replay", "create_files", "overwrite_existing_files", intents=(payload["intent"],)
    )
    second = execute_tool_intent(
        payload,
        **{**executor_kwargs("s", **scope), "checkpoint_id": "cp-1", "step_index": 0},
    )
    assert second.ok
    assert calls_logged(plugin_world) == [f"{PLUGIN_ID}.touch"]


def test_plugin_execution_writes_an_execution_record(plugin_world) -> None:
    from core import execution_records

    session = f"rec-{uuid.uuid4().hex[:8]}"
    execution_records.clear(session)
    out = _run(f"{PLUGIN_ID}.echo", {"text": "beta"}, session=session)
    assert out.ok
    records = [r for r in execution_records.records_for(session) if r.intent == f"{PLUGIN_ID}.echo"]
    assert len(records) == 1
    assert records[0].ok is True
    assert records[0].resolved_target == "beta"


def test_read_only_nested_handler_reads_own_assets_but_not_peer_files(plugin_world, tmp_path):
    from core.plugin_executor import run_plugin_tool

    (plugin_world / "catalog.json").write_text('{"paper": 17}')
    peer = tmp_path / "peer-private.txt"
    peer.write_text("unrelated contents")
    handler = plugin_world / "bin" / "catalog.py"
    handler.write_text(
        "import json, os, pathlib\n"
        "data = json.loads(pathlib.Path('catalog.json').read_text())\n"
        "denied = False\n"
        "try:\n    pathlib.Path(" + repr(str(peer)) + ").read_text()\n"
        "except PermissionError:\n    denied = True\n"
        "write_denied = False\n"
        "try:\n    pathlib.Path('unexpected.txt').write_text('changed')\n"
        "except PermissionError:\n    write_denied = True\n"
        "print(json.dumps({'ok': True, 'text': str(data['paper']), "
        "'observation': {'cwd': os.getcwd(), 'peer_denied': denied, 'write_denied': write_denied}}))\n"
    )
    import sys
    # The declared entry remains inside the plugin; use its executable wrapper
    # so confinement discovers the same interpreter and bin layout as real tools.
    handler.write_text('#!' + sys.executable + '\n' + handler.read_text())
    handler.chmod(0o755)
    result = run_plugin_tool(
        plugin_root=plugin_world, intent="pack.catalog", arguments={},
        handler={"entry": "bin/catalog.py"}, schema={}, read_only=True,
    )
    assert result.ok, result.error
    assert result.text == "17"
    assert result.observation == {
        "cwd": str(plugin_world), "peer_denied": True,
        "write_denied": True, "intent": "pack.catalog",
    }
    assert not (plugin_world / "unexpected.txt").exists()


def test_plugin_child_runs_confined_with_a_minimal_environment(plugin_world, tmp_path) -> None:
    from core.plugin_executor import kernel_confinement_available

    if not kernel_confinement_available():
        pytest.skip("no kernel confinement backend on this host")
    out = _run(f"{PLUGIN_ID}.echo", {"text": "gamma"})
    assert out.ok, (out.status, out.response_text)
    env_keys = set(out.details["observation"]["env_keys"])
    assert "SECRET_TOKEN_FOR_PLUGIN_TEST" not in env_keys
    assert "HOME" not in env_keys
    assert "PLUGIN_SCRATCH" in env_keys
    assert str(out.details["confinement"]).startswith("kernel:")

    # A write outside the plugin root and its scratch directory is denied by the kernel, not by
    # the handler's good manners.
    from tests._toolchain_fixtures import internal_scope

    outside = str(tmp_path / f"outside-{uuid.uuid4().hex[:6]}.txt")
    scope = internal_scope("test.confine", "create_files", intents=(f"{PLUGIN_ID}.touch",))
    denied = _run(f"{PLUGIN_ID}.touch", {"path": outside}, **scope)
    assert not denied.ok
    assert denied.status == "write_denied"
    assert not Path(outside).exists()
    assert os.environ.get("SECRET_TOKEN_FOR_PLUGIN_TEST") == "must-not-cross"  # parent untouched


def test_unavailable_confinement_refuses_rather_than_running_bare(plugin_world, monkeypatch) -> None:
    from core import plugin_executor

    monkeypatch.setattr(plugin_executor, "_kernel_confinement_prefix", lambda *a, **k: None)
    out = _run(f"{PLUGIN_ID}.echo", {"text": "delta"})
    assert out.handled and not out.ok
    assert out.status == "confinement_unavailable"
    assert "confine" in out.response_text.lower()
    assert calls_logged(plugin_world) == []


def test_a_crashing_handler_is_reported_not_fabricated(plugin_world) -> None:
    out = _run(f"{PLUGIN_ID}.crash", {})
    assert out.handled and not out.ok
    assert out.status == "handler_failed"
    assert out.mode == "tool_failed"
    assert "exit 3" in out.details["error"]
    assert out.details["observation"]["ok"] is False


def test_disabled_plugin_tool_explains_and_never_fabricates(tmp_path, monkeypatch) -> None:
    from core import plugin_tools
    from core.mode_permission_policy import reset_mode_permission_state
    from core.plugin_catalog import set_plugin_enabled
    from core.runtime_flags import override

    monkeypatch.delenv("VOOL_MCP_CONFIG", raising=False)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("VOOL_HOME", str(home))
    plugin_dir = make_plugin(tmp_path)
    monkeypatch.setenv("VOOL_PLUGINS_DIR", str(tmp_path))
    assert set_plugin_enabled(PLUGIN_ID, False)
    reset_toolchain_state()
    reset_mode_permission_state()
    with override("plugin_runtime_tools", True):
        _loaded, errors = plugin_tools.load_all(tmp_path)
        assert not errors
        from core.tool_registry import tool_for_intent

        contract = tool_for_intent(f"{PLUGIN_ID}.echo")
        assert contract is not None and contract.supported is False
        assert "disabled" in contract.unsupported_reason
        out = _run(f"{PLUGIN_ID}.echo", {"text": "epsilon"})
        assert out.handled and not out.ok
        assert out.status == "disabled"
        assert "disabled" in out.response_text.lower()
        assert calls_logged(plugin_dir) == []
        from core.capability_graph import model_visible_specs

        assert f"{PLUGIN_ID}.echo" not in {s["intent"] for s in model_visible_specs(family_hint="plugin")}
    reset_mode_permission_state()
    reset_toolchain_state()


def test_plugin_tool_registered_with_the_lane_closed_is_explained_not_run(tmp_path, monkeypatch) -> None:
    """The API server registers every installed pack at boot; the flag is the operator's switch."""
    from core import plugin_tools
    from core.mode_permission_policy import reset_mode_permission_state
    from core.runtime_flags import override

    monkeypatch.delenv("VOOL_MCP_CONFIG", raising=False)
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    plugin_dir = make_plugin(tmp_path)
    monkeypatch.setenv("VOOL_PLUGINS_DIR", str(tmp_path))
    reset_toolchain_state()
    reset_mode_permission_state()
    _loaded, errors = plugin_tools.load_all(tmp_path)  # registered, as the server does
    assert not errors
    with override("plugin_runtime_tools", False):
        out = _run(f"{PLUGIN_ID}.echo", {"text": "zeta"})
        assert out.handled and not out.ok
        assert out.status == "disabled"
        assert "plugin_runtime_tools" in out.response_text
        assert calls_logged(plugin_dir) == []
        from core.capability_census import NotModelFacingReason, capability_census

        row = capability_census(mode="manual").by_intent()[f"{PLUGIN_ID}.echo"]
        assert row.registered and not row.selectable
        assert row.not_model_facing_reason == NotModelFacingReason.PLUGIN_LANE_CLOSED.value
    reset_mode_permission_state()
    reset_toolchain_state()


def test_lying_permission_actions_are_refused_at_load(tmp_path) -> None:
    from core.plugin_tools import PluginManifestError, load_manifest
    from tests._toolchain_fixtures import default_tools

    tools = default_tools("liar")
    tools[1]["permission_actions"] = ["read_files"]  # a workspace_write tool claiming a read action
    plugin_dir = make_plugin(tmp_path, plugin_id="liar", tools=tools)
    with pytest.raises(PluginManifestError, match="permission_actions"):
        load_manifest(plugin_dir / ".codex-plugin" / "plugin.json")


def test_unknown_permission_action_is_refused_at_load(tmp_path) -> None:
    from core.plugin_tools import PluginManifestError, load_manifest
    from tests._toolchain_fixtures import default_tools

    tools = default_tools("liar2")
    tools[0]["permission_actions"] = ["bypass_everything"]
    plugin_dir = make_plugin(tmp_path, plugin_id="liar2", tools=tools)
    with pytest.raises(PluginManifestError, match="permission_actions"):
        load_manifest(plugin_dir / ".codex-plugin" / "plugin.json")
