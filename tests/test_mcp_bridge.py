"""Opt-in MCP bridge: off by default; discovers + routes mcp.<server>.<tool> when configured."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from core.execution import mcp_bridge
from core.execution.capabilities import runtime_tool_specs
from core.hive_activity_tracker import HiveActivityTracker, HiveActivityTrackerConfig
from core.runtime_execution_tools import runtime_execution_tool_specs
from core.tool_intent_executor import execute_tool_intent

_STUB = str(Path(__file__).parent / "mcp_stub_server.py")


@pytest.fixture(autouse=True)
def _isolate_mcp(monkeypatch):
    # MCP caches are process-global; reset around every test so config never leaks between them.
    monkeypatch.delenv("VOOL_MCP_CONFIG", raising=False)
    monkeypatch.delenv("VOOL_HOME", raising=False)
    mcp_bridge.reset_mcp_state()
    yield
    mcp_bridge.reset_mcp_state()


def _enable_stub(tmp_path, monkeypatch, *, name: str = "stub") -> None:
    cfg = tmp_path / "mcp_servers.json"
    cfg.write_text(
        json.dumps({"servers": [{"name": name, "command": sys.executable, "args": [_STUB]}]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("VOOL_MCP_CONFIG", str(cfg))
    mcp_bridge.reset_mcp_state()


def _tracker() -> HiveActivityTracker:
    return HiveActivityTracker(config=HiveActivityTrackerConfig(enabled=False, watcher_api_url=None))


def test_mcp_is_off_by_default() -> None:
    assert mcp_bridge.mcp_enabled() is False
    assert mcp_bridge.mcp_tool_specs() == []
    assert not any(str(s["intent"]).startswith("mcp.") for s in runtime_tool_specs())


def test_configured_server_tools_appear_in_catalog(tmp_path, monkeypatch) -> None:
    _enable_stub(tmp_path, monkeypatch)
    assert mcp_bridge.mcp_enabled() is True
    specs = {s["intent"]: s for s in mcp_bridge.mcp_tool_specs()}
    assert set(specs) == {"mcp.stub.echo", "mcp.stub.add"}
    assert specs["mcp.stub.echo"]["read_only"] is False  # unpinned side effects -> effectful
    # Server-authored text reaches the catalog framed as untrusted data, one line, quoted.
    description = specs["mcp.stub.echo"]["description"]
    assert description.startswith("MCP tool 'echo' on server 'stub'")
    assert "untrusted" in description and "Echo the given text back." in description
    # And they reach the master catalog the model actually sees:
    catalog = {s["intent"] for s in runtime_tool_specs()}
    assert {"mcp.stub.echo", "mcp.stub.add"}.issubset(catalog)


def test_execute_mcp_tool_directly(tmp_path, monkeypatch) -> None:
    _enable_stub(tmp_path, monkeypatch)
    add = mcp_bridge.execute_mcp_intent("mcp.stub.add", {"a": 2, "b": 40})
    assert add.handled and add.ok and add.status == "mcp_executed"
    # Output comes back under an explicit untrusted-output frame; the data itself is intact.
    assert add.response_text == "[untrusted output from MCP server 'stub' tool 'add']\n42.0"
    assert add.mode == "tool_executed" and add.details["trust"] == "untrusted_external"
    assert add.details["mcp_server"] == "stub" and add.details["mcp_tool"] == "add"
    echo = mcp_bridge.execute_mcp_intent("mcp.stub.echo", {"text": "hi vool"})
    assert echo.ok and echo.response_text.endswith("\nhi vool")


def test_an_mcp_tool_is_not_dispatched_before_a_permission_decision(tmp_path, monkeypatch) -> None:
    """A third-party MCP tool is not exempt from the permission controller.

    `mcp.*` names are supplied by an external server, so the classifier cannot tell what one does
    and reports `unknown_side_effect`. That must stop for the operator, not run. It used to run
    unasked whenever the caller sent no mode.
    """
    from core.mode_permission_policy import reset_mode_permission_state

    reset_mode_permission_state()
    _enable_stub(tmp_path, monkeypatch)
    out = execute_tool_intent(
        {"intent": "mcp.stub.add", "arguments": {"a": 1, "b": 2}},
        task_id="t",
        session_id="s",
        source_context=None,
        hive_activity_tracker=_tracker(),
    )
    assert out.handled and not out.ok and out.status == "pending_approval"
    assert out.details["executed"] is False
    reset_mode_permission_state()


def test_execute_routes_through_the_main_dispatcher(tmp_path, monkeypatch) -> None:
    """Past the gate, the dispatcher still routes `mcp.<server>.<tool>` to the bridge.

    The approval is resolved for real rather than stubbed, so this exercises the same path a
    user-approved MCP call takes -- otherwise it would assert nothing about routing and pass purely
    on the gate above it.
    """
    from core.mode_permission_policy import reset_mode_permission_state, resolve_approval

    reset_mode_permission_state()
    _enable_stub(tmp_path, monkeypatch)
    payload = {"intent": "mcp.stub.add", "arguments": {"a": 1, "b": 2}}

    pending = execute_tool_intent(
        payload, task_id="t", session_id="s", source_context=None, hive_activity_tracker=_tracker()
    )
    token = str(pending.details["approval_request"]["approval_id"])
    assert resolve_approval(token, decision="allow") is not None

    out = execute_tool_intent(
        payload,
        task_id="t",
        session_id="s",
        source_context={"mode_approval_token": token},
        hive_activity_tracker=_tracker(),
    )
    assert out.handled and out.ok and out.response_text.endswith("\n3.0") and out.tool_name == "mcp.stub.add"
    reset_mode_permission_state()


def test_unknown_server_fails_closed(tmp_path, monkeypatch) -> None:
    _enable_stub(tmp_path, monkeypatch)
    out = mcp_bridge.execute_mcp_intent("mcp.nope.tool", {})
    assert out.handled and not out.ok and out.status == "mcp_server_not_configured"


def test_malformed_intent_is_rejected() -> None:
    out = mcp_bridge.execute_mcp_intent("mcp.badonly", {})
    assert out.handled and not out.ok and out.status == "mcp_invalid_intent"


def test_bad_config_fails_closed(tmp_path, monkeypatch) -> None:
    cfg = tmp_path / "mcp_servers.json"
    cfg.write_text("{not valid json", encoding="utf-8")
    monkeypatch.setenv("VOOL_MCP_CONFIG", str(cfg))
    mcp_bridge.reset_mcp_state()
    assert mcp_bridge.mcp_enabled() is False
    assert mcp_bridge.mcp_tool_specs() == []


def test_mcp_stays_out_of_the_guarded_runtime_execution_subset(tmp_path, monkeypatch) -> None:
    # test_runtime_tool_registry_contract guards runtime_execution_tool_specs(); MCP must NOT leak
    # into that static, capability-ledger-covered subset even when enabled — it lives one level up
    # in the aggregator, exactly like web/hive/operator tools.
    _enable_stub(tmp_path, monkeypatch)
    assert not any(str(s.get("intent")).startswith("mcp.") for s in runtime_execution_tool_specs())
