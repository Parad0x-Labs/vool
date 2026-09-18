"""Isolated budget store + a clean plugin/MCP/permission world for the C02 lane packs.

Budget isolation is imported verbatim from the P1 lane's conftest (the one authority's own
fixtures: per-test sqlite store, operator token, `set_budget`); this file adds the toolchain
world reset so a pack's synthetic packs and MCP configs never leak into a later test.
"""
from __future__ import annotations

import pytest

from tests.effect_budget.conftest import *  # noqa: F403 — the one budget fixture set
from tests.effect_budget.conftest import _isolated_budget_store, operator_token, set_budget  # noqa: F401


@pytest.fixture(autouse=True)
def _clean_toolchain_world(tmp_path, monkeypatch):
    from core.mode_permission_policy import reset_mode_permission_state
    from tests._toolchain_fixtures import reset_toolchain_state

    empty_native = tmp_path / "no-native-skills"
    empty_native.mkdir()
    monkeypatch.setenv("VOOL_NATIVE_SKILLS_DIR", str(empty_native))
    monkeypatch.setenv("VOOL_PLUGIN_SCRATCH_ROOT", str(tmp_path / "scratch"))
    yield
    reset_mode_permission_state()
    reset_toolchain_state()


@pytest.fixture()
def plugin_world(tmp_path, monkeypatch):
    """A really-admitted synthetic pack (echo / touch / mutating-crash) with the lane open.

    The crash tool is a MUTATING tool whose handler exits non-zero: crashed effects must stay
    charged, and a read-only crasher would prove nothing about the budget door.
    """
    from core import plugin_tools
    from core.runtime_flags import override
    from tests._toolchain_fixtures import PLUGIN_ID, default_tools, make_plugin

    crash_tool = {
        "intent": f"{PLUGIN_ID}.mut.crash",
        "description": "A mutating handler that exits non-zero, for charged-crash proofs.",
        "handler": {"kind": "subprocess", "entry": "bin/run"},
        "input_schema": {"type": "object", "additionalProperties": False, "required": [], "properties": {}},
        "side_effect_class": "workspace_write",
        "approval_requirement": "runtime_policy",
        "permission_actions": ["create_files"],
        "claim": {},
        "mutation": {
            "tool": f"{PLUGIN_ID}.mut.crash",
            "scope": "workspace",
            "effect_class": "reversible",
            "snapshot_strategy": "declared_paths",
            "receipt_lifecycle": "intent_then_terminal",
            "rollback_support": "exact",
            "recorder": "blackbox.coverage_declared",
        },
    }
    tools = [tool for tool in default_tools(PLUGIN_ID) if not tool["intent"].endswith(".crash")]
    tools.append(crash_tool)
    plugin_dir = make_plugin(tmp_path, tools=tools)
    monkeypatch.setenv("VOOL_PLUGINS_DIR", str(tmp_path))

    from core.mode_permission_policy import reset_mode_permission_state
    from tests._toolchain_fixtures import reset_toolchain_state

    reset_toolchain_state()
    with override("plugin_runtime_tools", True):
        loaded, errors = plugin_tools.load_all(tmp_path)
        assert loaded and not errors, errors
        yield {"plugin_dir": plugin_dir, "plugin_id": PLUGIN_ID}
    reset_mode_permission_state()
    reset_toolchain_state()


@pytest.fixture()
def auto_mode():
    """AUTO operating mode for a test session — mutating tools dispatch instead of prompting."""

    from core.mode_permission_policy import set_active_mode

    def _set(session_id: str) -> str:
        set_active_mode(session_id, "auto")
        return session_id

    return _set


@pytest.fixture()
def journal(tmp_path, monkeypatch):
    """The Blackbox journal, isolated to this test's store dir, for receipt-chain assertions."""
    store_dir = tmp_path / "blackbox-store"
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(store_dir))
    try:
        from core.blackbox.store import reset_default_store

        reset_default_store()
    except Exception:
        pass

    def _entries() -> list[dict]:
        from storage.blackbox.journal import Journal

        if not store_dir.exists():
            return []
        return [dict(entry) for entry in Journal(store_dir).entries()]

    yield _entries
    try:
        from core.blackbox.store import reset_default_store

        reset_default_store()
    except Exception:
        pass
