"""Sabotage proofs for the adaptive tool-offer authority.

Each test breaks ONE mechanism on purpose and asserts the observable changes —
mutation-testing style. A green suite therefore proves the mechanisms are
load-bearing: the good behaviour comes from the wiring, not from luck, ranking
order, or a stale cache.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from core.capability_graph import (
    _FAMILY_REPRESENTATIVE,
    assert_representatives_resolve,
    model_visible_specs,
)

_STUB_MCP = str(Path(__file__).parent / "mcp_stub_server.py")


def _intents(specs: list[dict]) -> set[str]:
    return {str(s.get("intent") or "") for s in specs}


def test_sabotage_explicit_priority_is_load_bearing(monkeypatch) -> None:
    """Neuter the demand-signal resolver and the explicit seat must vanish."""
    from core import tool_demand_signals
    from core.tool_demand_signals import DemandSignals

    text = "run the test suite in this repo"
    with_explicit = model_visible_specs(family_hint="workspace", user_text=text)
    assert "workspace.run_tests" in _intents(with_explicit)

    monkeypatch.setattr(
        tool_demand_signals, "resolve_demand_signals", lambda _t: DemandSignals()
    )
    sabotaged = model_visible_specs(family_hint="workspace", user_text=text)
    assert "workspace.run_tests" not in _intents(sabotaged)
    assert with_explicit != sabotaged


def test_sabotage_expansion_budget_is_load_bearing(monkeypatch) -> None:
    """Zero the expansion bonus and the long tail must fall back under the cap."""
    from core import capability_graph
    from core.tool_offer_state import record_family_expansion, reset_offer_state

    context = {"turn_id": "sab-expand"}
    reset_offer_state()
    record_family_expansion(context, "filesystem")
    full = model_visible_specs(source_context=dict(context))
    assert "machine.write_file" in _intents(full)  # the long tail seats with the bonus

    monkeypatch.setattr(capability_graph, "_EXPANSION_SEAT_BONUS", 0)
    reset_offer_state()
    record_family_expansion(context, "filesystem")
    capped = model_visible_specs(source_context=dict(context))
    assert "machine.write_file" not in _intents(capped)
    assert len(capped) <= capability_graph._DEFAULT_MAX_CANDIDATES

    # The ceiling itself is bounded: even a double expansion cannot flood the offer.
    monkeypatch.undo()
    reset_offer_state()
    record_family_expansion(context, "workspace")
    record_family_expansion(context, "filesystem")
    doubled = model_visible_specs(source_context=dict(context))
    assert len(doubled) <= capability_graph._HARD_MAX_CANDIDATES


@pytest.fixture()
def _stub_mcp(tmp_path, monkeypatch):
    from core.execution import mcp_bridge

    cfg = tmp_path / "mcp_servers.json"
    cfg.write_text(
        json.dumps(
            {"servers": [{"name": "stub", "command": sys.executable, "args": [_STUB_MCP]}]}
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("VOOL_MCP_CONFIG", str(cfg))
    mcp_bridge.reset_mcp_state()
    yield
    mcp_bridge.reset_mcp_state()


def test_sabotage_mcp_seating_is_load_bearing(_stub_mcp, monkeypatch) -> None:
    """Neuter the registry sync and the MCP seats must vanish.

    MCP tools reach the graph ONLY as registry contracts (`mcp_bridge.sync_mcp_registry`); the
    former spec-fingerprint side channel is gone. With the sync a no-op the registry carries no
    `mcp:` row, the epoch-indexed projection has nothing to seat, and the offer shows no MCP tool —
    proving the seats come from the registry, not from the catalog spec list alone.
    """
    from core import capability_graph, tool_registry
    from core.execution import mcp_bridge

    seated = model_visible_specs(family_hint="mcp")
    assert "mcp.stub.echo" in _intents(seated)
    assert tool_registry.tool_for_intent("mcp.stub.echo") is not None

    mcp_bridge.reset_mcp_state()  # withdraws the mcp: rows; a healthy sync would put them back
    monkeypatch.setattr(mcp_bridge, "sync_mcp_registry", lambda *a, **k: 0)
    capability_graph.ensure_registry_bootstrap()
    assert tool_registry.tool_for_intent("mcp.stub.echo") is None
    assert not [i for i in capability_graph.all_implementations() if str(i.source).startswith("mcp:")]
    assert "mcp.stub.echo" not in _intents(model_visible_specs(family_hint="mcp"))


def test_sabotage_representative_coherence_catches_phantom(monkeypatch) -> None:
    """A phantom representative must raise, exactly like a vocabulary drift."""
    assert_representatives_resolve()  # the real map is coherent

    sabotaged = dict(_FAMILY_REPRESENTATIVE)
    sabotaged["workspace"] = "workspace.does_not_exist"
    monkeypatch.setattr(
        "core.capability_graph._FAMILY_REPRESENTATIVE",
        sabotaged,
    )
    with pytest.raises(RuntimeError, match=r"workspace\.does_not_exist"):
        assert_representatives_resolve()


def test_sabotage_plugin_hiding_is_load_bearing(tmp_path, monkeypatch) -> None:
    """A plugin graph row without its flag-gated catalog spec must not seat."""
    from core import capability_graph, plugin_tools, tool_registry
    from core.runtime_flags import override

    manifest = tmp_path / "plugins" / "sab-pack" / ".codex-plugin"
    manifest.mkdir(parents=True)
    (manifest / "plugin.json").write_text(
        json.dumps(
            {
                "name": "sab-pack",
                "version": "1.0.0",
                "description": "sabotage pack",
                "runtime": {"contract_version": 1},
                "tools": [
                    {
                        "intent": "sab-pack.probe",
                        "description": "Sabotage probe tool with a real sentence.",
                        "handler": {"kind": "subprocess", "entry": "bin/run", "args": ["probe"]},
                        "input_schema": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["query"],
                            "properties": {"query": {"type": "string"}},
                        },
                        "side_effect_class": "read_only",
                        "approval_requirement": "none",
                        "claim": {"target_argument": "query", "resolved_target_key": "query"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    tool_registry.reset()
    monkeypatch.setenv("VOOL_PLUGINS_DIR", str(tmp_path))
    # Writing the manifest discovers the pack; it does not install it. The seat this test is
    # about only exists once the lifecycle admits the pack.
    from tests._toolchain_fixtures import admit_plugin

    admit_plugin("sab-pack", tmp_path / "plugins" / "sab-pack")
    try:
        with override("plugin_runtime_tools", True):
            plugin_tools.load_all(tmp_path)
            capability_graph.bootstrap_from_registry()
            assert "sab-pack.probe" in _intents(model_visible_specs(family_hint="plugin"))

        # Flag OFF: the graph row persists but its catalog spec is gone — the
        # seat must disappear with the lane, not survive on the stale row.
        with override("plugin_runtime_tools", False):
            assert "sab-pack.probe" not in _intents(model_visible_specs(family_hint="plugin"))
    finally:
        tool_registry.reset()
        capability_graph.reset()
        capability_graph.init_graph()
        capability_graph.bootstrap_from_registry()
