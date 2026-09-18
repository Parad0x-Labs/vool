"""CP1 INTERACTION GATE — budgets and Blackbox coverage on the SAME doors.

The convergence candidate's two new authorities must hold together, not as two
separate green suites:

1. the one workspace-mutation door (`_dispatch_with_mutation_activity`) reserves
   the file_write budget AND journals through the v1 Blackbox flight recorder in
   a single real dispatch — exactly one reservation per execution (atomic, no
   double-charge), the journal pair durable before the caller hears success;
2. a budget refusal at that door prevents the recorder's handler from ever
   running: no second journal pair, no bytes on disk;
3. the capability-coverage census still fails closed for mutation-capable tools
   with NO declaration even when no budget rule exists at all — the two gates
   stack, neither replaces the other;
4. a shell mutation declares scan coverage AND its command effect receipt lands
   on the turn's one ledger — registry contract, coverage seam, and effect
   ledger all engaged by one dispatch.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.effect_budget.conftest import *  # noqa: F401,F403 — budget fixtures


@pytest.fixture(autouse=True)
def _isolated_cas_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from core.blackbox.coverage.cas_keys import CasKeyring

    keys_file = tmp_path / "cas-keys.json"
    keys_file.write_text(CasKeyring.mint().to_json(), encoding="utf-8")
    monkeypatch.setenv("VOOL_BLACKBOX_CAS_KEYS_FILE", str(keys_file))
    yield keys_file
    monkeypatch.delenv("VOOL_BLACKBOX_CAS_KEYS_FILE", raising=False)


@pytest.fixture(autouse=True)
def _isolated_registries():
    from core.blackbox.coverage import registry as coverage_registry
    from core.tool_registry import reset as reset_tools

    coverage_registry.reset_registered()
    reset_tools()
    yield
    coverage_registry.reset_registered()
    reset_tools()


@pytest.fixture
def blackbox_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "blackbox-store"
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(root))
    from core.blackbox import store as store_module

    store_module.reset_default_store()
    yield root
    store_module.reset_default_store()


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    root.mkdir()
    return root


def _ctx(workspace: Path, session: str = "ix-sess", turn: str = "ix-turn") -> dict:
    from core.mode_permission_policy import set_active_mode

    set_active_mode(session, "auto")
    return {
        "workspace": str(workspace),
        "workspace_root": str(workspace),
        "session_id": session,
        "surface": "api",
        "operating_mode": "auto",
        "turn_id": turn,
    }


def _journal_kinds(store) -> list[str]:
    return [str(e.get("kind")) for e in store.entries()]


class TestWorkspaceDoor:
    def test_one_dispatch_reserves_budget_and_journals_blackbox(
        self, set_budget, workspace, blackbox_store
    ):
        from core import effect_budget as eb

        set_budget(("file_write", eb.SCOPE_SESSION, 2))
        from core.blackbox.store import default_store
        from core.effect_gateway import (
            close_effect_receipt_scope,
            open_effect_receipt_scope,
        )
        from core.runtime_execution_tools import execute_runtime_tool

        open_effect_receipt_scope({"session_id": "ix-sess", "workspace_root": str(workspace)})
        try:
            result = execute_runtime_tool(
                "workspace.write_file",
                {"path": "budgeted.txt", "content": "one"},
                source_context=_ctx(workspace, turn="ix-turn-1"),
            )
            assert result.ok, result.response_text
            assert (workspace / "budgeted.txt").read_text() == "one"

            # BOTH authorities in this one dispatch:
            rows = eb.reservation_rows()
            assert len(rows) == 1, f"exactly one reservation, got {rows}"
            assert rows[0]["state"] == eb.RESERVATION_CONSUMED
            assert rows[0]["budget_class"] == "file_write"
            kinds = _journal_kinds(default_store())
            assert "effect_intended" in kinds and "effect_terminal" in kinds, kinds
            assert default_store().verify().ok, default_store().verify().reason
        finally:
            close_effect_receipt_scope()

    def test_budget_refusal_prevents_the_recorder_handler(
        self, set_budget, workspace, blackbox_store
    ):
        from core import effect_budget as eb

        set_budget(("file_write", eb.SCOPE_SESSION, 1))
        from core.blackbox.store import default_store
        from core.effect_gateway import (
            close_effect_receipt_scope,
            open_effect_receipt_scope,
        )
        from core.runtime_execution_tools import execute_runtime_tool

        open_effect_receipt_scope({"session_id": "ix-sess", "workspace_root": str(workspace)})
        try:
            first = execute_runtime_tool(
                "workspace.write_file",
                {"path": "first.txt", "content": "spent"},
                source_context=_ctx(workspace, turn="ix-turn-1"),
            )
            assert first.ok, first.response_text
            intended_before = _journal_kinds(default_store()).count("effect_intended")

            second = execute_runtime_tool(
                "workspace.write_file",
                {"path": "second.txt", "content": "refused"},
                source_context=_ctx(workspace, turn="ix-turn-2"),
            )
            assert second.ok is False
            assert second.status == "blocked_by_effect_budget"
            assert second.details["effect_budget"]["code"] == eb.REFUSAL_BUDGET_EXCEEDED
            assert not (workspace / "second.txt").exists(), "the handler never ran"
            intended_after = _journal_kinds(default_store()).count("effect_intended")
            assert intended_after == intended_before, "no journal pair for a refused mutation"
            assert default_store().verify().ok
        finally:
            close_effect_receipt_scope()
        # The refused dispatch reserved nothing — denial at the gate touches no unit.
        rows = [r for r in eb.reservation_rows() if r["state"] != eb.RESERVATION_RELEASED]
        consumed = [r for r in rows if r["state"] == eb.RESERVATION_CONSUMED]
        assert len(consumed) == 1


class TestCoverageCensusIndependence:
    def test_undeclared_mutation_fails_closed_with_no_budget_rules(
        self, workspace, blackbox_store
    ):
        """No budget rule exists; the coverage census must still refuse an
        undeclared mutation-capable tool — the gates stack, neither substitutes."""
        from core.blackbox.coverage.registry import reset_registered, unregister_capability
        from core.runtime_execution_tools import execute_runtime_tool

        removed = unregister_capability("machine.write_file")
        assert removed, "machine.write_file must be a declared builtin to remove"
        try:
            result = execute_runtime_tool(
                "machine.write_file",
                {"path": str(workspace / "undeclared.txt"), "content": "x"},
                source_context=_ctx(workspace, turn="ix-turn-3"),
            )
            assert result.ok is False
            assert result.status == "blackbox_coverage_required", result.status
            assert result.details["executed"] is False
            assert not (workspace / "undeclared.txt").exists()
        finally:
            reset_registered()  # re-derive the builtin seeds

    def test_builtin_census_is_zero_after_registration(self):
        from core.blackbox.coverage.registry import uncovered_local_mutating_builtins

        assert uncovered_local_mutating_builtins() == []


class TestShellCommandBothAuthorities:
    def test_shell_declares_coverage_and_receipts_the_command_effect(
        self, set_budget, workspace, blackbox_store
    ):
        from core import effect_budget as eb

        set_budget(("command", eb.SCOPE_SESSION, 5))
        from core.blackbox.coverage.registry import mutation_coverage_decision
        from core.blackbox.store import default_store
        from core.effect_gateway import (
            close_effect_receipt_scope,
            effect_outcomes,
            open_effect_receipt_scope,
        )
        from core.runtime_execution_tools import execute_runtime_tool

        decision = mutation_coverage_decision("sandbox.run_command")
        assert decision.mutation_capable and decision.covered, "census: the shell declares"

        (workspace / "seed.txt").write_bytes(b"seed-bytes-v1")
        open_effect_receipt_scope({"session_id": "ix-sess", "workspace_root": str(workspace)})
        try:
            result = execute_runtime_tool(
                "sandbox.run_command",
                {"command": "cp seed.txt copy.txt", "cwd": str(workspace)},
                source_context=_ctx(workspace, turn="ix-turn-4"),
            )
            assert result.ok, result.response_text
            assert result.details["blackbox"]["terminal_recorded"] is True

            kinds = _journal_kinds(default_store())
            assert "coverage_scan_intended" in kinds and "coverage_scan_terminal" in kinds, kinds
            # the sandbox confines the child; the mutation's truth is the journal's
            # drift row, exactly as the lane's own shell proof asserts it
            terminal = [e for e in default_store().entries() if e.get("kind") == "coverage_scan_terminal"][-1]
            drift_paths = {str(row.get("path")) for row in terminal.get("drift", [])}
            assert any(p.endswith("copy.txt") for p in drift_paths), sorted(drift_paths)
            assert default_store().verify().ok, default_store().verify().reason
            # CP2 CLOSED the recorded CP1 gap: the coverage door now reserves the
            # command budget unit for shell-shaped mutations before the recorder can
            # run the handler, so this one dispatch journals the Blackbox pair AND
            # holds exactly one consumed `command` reservation at the same authority.
            command_rows = [r for r in eb.reservation_rows() if r["budget_class"] == "command"]
            assert any(r["state"] == eb.RESERVATION_CONSUMED for r in command_rows), command_rows
            assert len(command_rows) == 1, f"no double charge: {command_rows}"
            _ = effect_outcomes  # the ledger's outcomes stay in scope for the door's receipts
        finally:
            close_effect_receipt_scope()
