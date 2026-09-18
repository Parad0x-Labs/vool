"""C02 — the registered-effect budget door, proven through the PRODUCTION entry.

Every test drives `core.tool_intent_executor.execute_tool_intent` — the one function the
served model loop resolves to — under an `open_effect_receipt_scope` turn, the same shape the
served turn runs under. What is asserted is each authority's own durable truth: the budget
store's reservation rows, the plugin child's call log, the MCP stub's call log, the Blackbox
journal, and the execution-record store. The reply text is never the evidence.

Confinement truth the fixtures respect: the plugin child's only writable root is its scratch
directory, and the MCP stub child runs confined to its cwd (the test workspace). Effect
targets are chosen so the CHILD can write them — the budget door sits before any of that.
"""
from __future__ import annotations

import json
import sys
import threading
import uuid
from pathlib import Path

import pytest

from tests._toolchain_fixtures import (
    PLUGIN_ID,
    calls_logged,
    reset_toolchain_state,
    tracker,
)


def _run(intent: str, arguments: dict, *, session_id: str, workspace: Path):
    from core.tool_intent_executor import execute_tool_intent

    return execute_tool_intent(
        {"intent": intent, "arguments": arguments},
        task_id="t1",
        session_id=session_id,
        source_context={
            "session_id": session_id,
            "workspace_root": str(workspace),
            "workspace": str(workspace),
            "surface": "api",
        },
        hive_activity_tracker=tracker(),
    )


@pytest.fixture()
def workspace_and_scratch(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    scratch = tmp_path / "scratch"
    scratch.mkdir(exist_ok=True)
    return workspace, scratch


@pytest.fixture()
def mcp_world(tmp_path, monkeypatch, workspace_and_scratch):
    """A live stub MCP server with a read-only `echo` and a workspace_write-pinned `write_outside`,
    synced into the real registry, plus its per-call log. The server child runs confined to its
    cwd — the test workspace — so its writes land inside the asserted tree."""
    _workspace, _scratch = workspace_and_scratch
    stub = Path(__file__).resolve().parents[1] / "mcp_stub_server.py"
    # a neutral cwd for the confined stub child: it holds the call log, the launch marker and
    # the effect targets, so the workspace tree stays clean for the read-only drift scans
    mcp_cwd = tmp_path / "mcp-cwd"
    mcp_cwd.mkdir()
    call_log = mcp_cwd / "calls.log"
    marker = mcp_cwd / "started.marker"
    cfg = tmp_path / "mcp_servers.json"
    cfg.write_text(
        json.dumps(
            {
                "servers": [
                    {
                        "name": "stub",
                        "command": sys.executable,
                        "args": [
                            str(stub),
                            "--extra-tools",
                            "--marker",
                            str(marker),
                            "--call-log",
                            str(call_log),
                        ],
                        "cwd": str(mcp_cwd),
                        "enabled": True,
                        "trust": {
                            "echo": {"side_effect_class": "read_only", "permission_actions": ["read_files"]},
                            "write_outside": {
                                "side_effect_class": "workspace_write",
                                "permission_actions": ["create_files"],
                            },
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("VOOL_MCP_CONFIG", str(cfg))
    from core.execution import mcp_bridge

    reset_toolchain_state()
    mcp_bridge.sync_mcp_registry()
    from core.tool_registry import registry_map

    contract = registry_map().get("mcp.stub.write_outside")
    assert contract is not None and contract.supported, (
        "a pinned workspace_write MCP tool must register (the bridge carries the operator's "
        "declaration); sync returned without it"
    )
    yield {"call_log": call_log, "marker": marker, "cwd": mcp_cwd}
    reset_toolchain_state()


def _mcp_calls(call_log: Path) -> list[str]:
    if not call_log.is_file():
        return []
    return [line for line in call_log.read_text(encoding="utf-8").splitlines() if line.strip()]


class TestPluginBudgetDoor:
    def test_exhausted_session_budget_refuses_plugin_effect_with_zero_handler_calls(
        self, plugin_world, auto_mode, set_budget, workspace_and_scratch, journal
    ):
        from core import effect_budget as eb
        from core import execution_records

        workspace, _scratch = workspace_and_scratch
        plugin_dir = plugin_world["plugin_dir"]
        session = auto_mode(f"openclaw:{uuid.uuid4().hex[:20]}")
        set_budget(("file_write", eb.SCOPE_SESSION, 1))

        from core.effect_gateway import close_effect_receipt_scope, open_effect_receipt_scope

        open_effect_receipt_scope({"session_id": session, "workspace_root": str(workspace)})
        try:
            first = _run(
                f"{plugin_world['plugin_id']}.touch",
                {"path": str(plugin_dir / "a.txt")},
                session_id=session,
                workspace=workspace,
            )
            second = _run(
                f"{plugin_world['plugin_id']}.touch",
                {"path": str(plugin_dir / "b.txt")},
                session_id=session,
                workspace=workspace,
            )
        finally:
            close_effect_receipt_scope()

        assert first.ok, first.response_text
        assert (plugin_dir / "a.txt").exists(), "the budgeted effect ran once"
        assert second.ok is False
        assert second.status == "blocked_by_effect_budget"
        assert second.details["effect_budget"]["code"] == eb.REFUSAL_BUDGET_EXCEEDED
        assert second.details["executed"] is False
        assert not (plugin_dir / "b.txt").exists(), "zero writes on refusal"
        assert calls_logged(plugin_world["plugin_dir"]) == [f"{plugin_world['plugin_id']}.touch"], (
            "zero handler calls on refusal: the confined child never ran a second time"
        )
        rows = eb.reservation_rows()
        assert len(rows) == 1 and rows[0]["state"] == eb.RESERVATION_CONSUMED
        entries = journal()
        assert [e for e in entries if str(e.get("path", "")).endswith("b.txt")] == [], (
            "a refused registered effect leaves no Blackbox observation for itself"
        )
        records = {r.intent: r for r in execution_records.records_for(session)}
        refused = records[f"{plugin_world['plugin_id']}.touch"]
        assert refused.ok is False and refused.status == "blocked_by_effect_budget"

    def test_success_charges_one_unit_and_journals_the_chain_together(
        self, plugin_world, auto_mode, set_budget, workspace_and_scratch, journal
    ):
        from core import effect_budget as eb

        workspace, _scratch = workspace_and_scratch
        plugin_dir = plugin_world["plugin_dir"]
        session = auto_mode(f"openclaw:{uuid.uuid4().hex[:20]}")
        set_budget(("file_write", eb.SCOPE_SESSION, 5))

        from core.effect_gateway import close_effect_receipt_scope, open_effect_receipt_scope

        open_effect_receipt_scope({"session_id": session, "workspace_root": str(workspace)})
        try:
            result = _run(
                f"{plugin_world['plugin_id']}.touch",
                {"path": str(plugin_dir / "ok.txt")},
                session_id=session,
                workspace=workspace,
            )
        finally:
            close_effect_receipt_scope()

        assert result.ok, result.response_text
        rows = eb.reservation_rows()
        assert len(rows) == 1 and rows[0]["state"] == eb.RESERVATION_CONSUMED
        entries = journal()
        terminals = [e for e in entries if str(e.get("kind", "")).endswith("_terminal")]
        assert terminals, "the success journaled its terminal observation"
        assert calls_logged(plugin_world["plugin_dir"]) == [f"{plugin_world['plugin_id']}.touch"]

    def test_read_only_plugin_tools_never_consume_mutation_budgets(
        self, plugin_world, auto_mode, set_budget, workspace_and_scratch
    ):
        from core import effect_budget as eb

        workspace, _scratch = workspace_and_scratch
        session = auto_mode(f"openclaw:{uuid.uuid4().hex[:20]}")
        set_budget(("file_write", eb.SCOPE_SESSION, 1), ("command", eb.SCOPE_SESSION, 1))

        from core.effect_gateway import close_effect_receipt_scope, open_effect_receipt_scope

        open_effect_receipt_scope({"session_id": session, "workspace_root": str(workspace)})
        try:
            for index in range(3):
                result = _run(
                    f"{plugin_world['plugin_id']}.echo",
                    {"text": f"probe-{index}"},
                    session_id=session,
                    workspace=workspace,
                )
                assert result.ok, result.response_text
        finally:
            close_effect_receipt_scope()

        assert eb.reservation_rows() == [], "a read-only tool never reserves a mutation unit"

    def test_crashed_mutating_plugin_handler_stays_charged(
        self, plugin_world, auto_mode, set_budget, workspace_and_scratch
    ):
        from core import effect_budget as eb

        workspace, _scratch = workspace_and_scratch
        plugin_dir = plugin_world["plugin_dir"]
        session = auto_mode(f"openclaw:{uuid.uuid4().hex[:20]}")
        set_budget(("file_write", eb.SCOPE_SESSION, 1))

        from core.effect_gateway import close_effect_receipt_scope, open_effect_receipt_scope

        open_effect_receipt_scope({"session_id": session, "workspace_root": str(workspace)})
        try:
            crashed = _run(
                f"{plugin_world['plugin_id']}.mut.crash", {}, session_id=session, workspace=workspace
            )
            after = _run(
                f"{plugin_world['plugin_id']}.touch",
                {"path": str(plugin_dir / "after-crash.txt")},
                session_id=session,
                workspace=workspace,
            )
        finally:
            close_effect_receipt_scope()

        assert crashed.ok is False, "the crash is a failure result, never a claimed success"
        assert crashed.status == "handler_failed"
        assert calls_logged(plugin_world["plugin_dir"]) == [
            f"{plugin_world['plugin_id']}.mut.crash"
        ], "the crashing child ran exactly once"
        rows = eb.reservation_rows()
        assert len(rows) == 1 and rows[0]["state"] == eb.RESERVATION_CONSUMED, (
            "a crashed effect's unit is spent, never silently released"
        )
        assert after.ok is False and after.status == "blocked_by_effect_budget", (
            "the crash exhausted the session budget: the next effect refuses"
        )
        assert not (plugin_dir / "after-crash.txt").exists()
        assert calls_logged(plugin_world["plugin_dir"]) == [f"{plugin_world['plugin_id']}.mut.crash"]

    def test_retry_of_a_refused_effect_stays_refused_and_uncharged(
        self, plugin_world, auto_mode, set_budget, workspace_and_scratch
    ):
        from core import effect_budget as eb

        workspace, _scratch = workspace_and_scratch
        plugin_dir = plugin_world["plugin_dir"]
        session = auto_mode(f"openclaw:{uuid.uuid4().hex[:20]}")
        set_budget(("file_write", eb.SCOPE_SESSION, 1))

        from core.effect_gateway import close_effect_receipt_scope, open_effect_receipt_scope

        open_effect_receipt_scope({"session_id": session, "workspace_root": str(workspace)})
        try:
            first = _run(
                f"{plugin_world['plugin_id']}.touch",
                {"path": str(plugin_dir / "one.txt")},
                session_id=session,
                workspace=workspace,
            )
            refusals = [
                _run(
                    f"{plugin_world['plugin_id']}.touch",
                    {"path": str(plugin_dir / f"retry-{index}.txt")},
                    session_id=session,
                    workspace=workspace,
                )
                for index in range(3)
            ]
        finally:
            close_effect_receipt_scope()

        assert first.ok
        assert len(calls_logged(plugin_world["plugin_dir"])) == 1, "retries never reach the handler"
        for refusal in refusals:
            assert refusal.status == "blocked_by_effect_budget" and refusal.details["executed"] is False
        assert len(eb.reservation_rows()) == 1, "a refusal writes no reservation row"

    def test_session_scope_isolates_sessions_and_project_scope_binds_the_workspace(
        self, plugin_world, auto_mode, set_budget, workspace_and_scratch
    ):
        from core import effect_budget as eb

        workspace, _scratch = workspace_and_scratch
        plugin_dir = plugin_world["plugin_dir"]
        session_a = auto_mode(f"openclaw:{uuid.uuid4().hex[:20]}")
        session_b = auto_mode(f"openclaw:{uuid.uuid4().hex[:20]}")
        set_budget(("file_write", eb.SCOPE_SESSION, 1), ("file_write", eb.SCOPE_PROJECT, 2))

        from core.effect_gateway import close_effect_receipt_scope, open_effect_receipt_scope

        open_effect_receipt_scope({"session_id": session_a, "workspace_root": str(workspace)})
        try:
            _run(
                f"{plugin_world['plugin_id']}.touch",
                {"path": str(plugin_dir / "a-one.txt")},
                session_id=session_a,
                workspace=workspace,
            )
            blocked = _run(
                f"{plugin_world['plugin_id']}.touch",
                {"path": str(plugin_dir / "a-two.txt")},
                session_id=session_a,
                workspace=workspace,
            )
        finally:
            close_effect_receipt_scope()

        assert blocked.status == "blocked_by_effect_budget"

        # a DIFFERENT session against the same workspace: its own session budget is untouched
        open_effect_receipt_scope({"session_id": session_b, "workspace_root": str(workspace)})
        try:
            other = _run(
                f"{plugin_world['plugin_id']}.touch",
                {"path": str(plugin_dir / "b-one.txt")},
                session_id=session_b,
                workspace=workspace,
            )
        finally:
            close_effect_receipt_scope()
        assert other.ok, other.response_text

        # the PROJECT budget is shared: a-one and b-one spent its two units; a fresh session
        # refuses even though its own session rule never bound a unit
        session_c = auto_mode(f"openclaw:{uuid.uuid4().hex[:20]}")
        open_effect_receipt_scope({"session_id": session_c, "workspace_root": str(workspace)})
        try:
            exhausted = _run(
                f"{plugin_world['plugin_id']}.touch",
                {"path": str(plugin_dir / "c-one.txt")},
                session_id=session_c,
                workspace=workspace,
            )
        finally:
            close_effect_receipt_scope()
        assert exhausted.status == "blocked_by_effect_budget", (
            "the project rule refused the effect the session rule would have allowed"
        )
        assert not (plugin_dir / "c-one.txt").exists()

    def test_parallel_sessions_race_for_the_last_unit_and_exactly_one_wins(
        self, plugin_world, auto_mode, set_budget, workspace_and_scratch
    ):
        from core import effect_budget as eb

        workspace, _scratch = workspace_and_scratch
        plugin_dir = plugin_world["plugin_dir"]
        set_budget(("file_write", eb.SCOPE_PROJECT, 1))
        barrier = threading.Barrier(4, timeout=30)
        outcomes: dict[str, object] = {}
        lock = threading.Lock()

        def _attempt(name: str) -> None:
            from core.effect_gateway import close_effect_receipt_scope, open_effect_receipt_scope

            session = f"openclaw:{uuid.uuid4().hex[:20]}"
            auto_mode(session)
            open_effect_receipt_scope({"session_id": session, "workspace_root": str(workspace)})
            try:
                barrier.wait()
                outcome = _run(
                    f"{plugin_world['plugin_id']}.touch",
                    {"path": str(plugin_dir / f"{name}.txt")},
                    session_id=session,
                    workspace=workspace,
                )
            finally:
                close_effect_receipt_scope()
            with lock:
                outcomes[name] = outcome

        threads = [threading.Thread(target=_attempt, args=(f"racer-{i}",)) for i in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)

        winners = [name for name, outcome in outcomes.items() if getattr(outcome, "ok", False)]
        losers = [name for name, outcome in outcomes.items() if not getattr(outcome, "ok", False)]
        assert len(winners) == 1, f"exactly one effect wins the last unit, got {winners}"
        assert len(losers) == 3
        for name in losers:
            outcome = outcomes[name]
            assert outcome.status == "blocked_by_effect_budget", outcome.status
        assert calls_logged(plugin_world["plugin_dir"]) == [
            f"{plugin_world['plugin_id']}.touch"
        ], "the handler ran for the winner and nobody else"
        assert (plugin_dir / f"{winners[0]}.txt").exists()
        for name in losers:
            assert not (plugin_dir / f"{name}.txt").exists()

    def test_restart_keeps_the_exhaustion_and_reconciliation_releases_only_stale_units(
        self, plugin_world, auto_mode, set_budget, workspace_and_scratch
    ):
        from core import effect_budget as eb

        workspace, _scratch = workspace_and_scratch
        plugin_dir = plugin_world["plugin_dir"]
        session = auto_mode(f"openclaw:{uuid.uuid4().hex[:20]}")
        set_budget(("file_write", eb.SCOPE_SESSION, 1))

        from core.effect_gateway import close_effect_receipt_scope, open_effect_receipt_scope

        open_effect_receipt_scope({"session_id": session, "workspace_root": str(workspace)})
        try:
            _run(
                f"{plugin_world['plugin_id']}.touch",
                {"path": str(plugin_dir / "spent.txt")},
                session_id=session,
                workspace=workspace,
            )
        finally:
            close_effect_receipt_scope()

        # "restart": the process instance state dies; the store does not
        eb.reset_effect_budget_process_state()

        open_effect_receipt_scope({"session_id": session, "workspace_root": str(workspace)})
        try:
            after_restart = _run(
                f"{plugin_world['plugin_id']}.touch",
                {"path": str(plugin_dir / "post-restart.txt")},
                session_id=session,
                workspace=workspace,
            )
        finally:
            close_effect_receipt_scope()
        assert after_restart.status == "blocked_by_effect_budget", (
            "budgets hold across restart: the spent unit stays spent"
        )
        assert not (plugin_dir / "post-restart.txt").exists()

        # a reservation that never began its attempt (a crash between reserve and consume)
        # is the one thing reconciliation releases — and the returned unit is spendable.
        # session_b holds the session's last unit via a bare reservation: a dispatched effect
        # refuses while it is HELD, and runs only after the stale reservation is released.
        session_b = auto_mode(f"openclaw:{uuid.uuid4().hex[:20]}")
        receipt = eb.reserve_effect_units("file_write", owner_ref="restart-proof", session_id=session_b)
        assert not receipt.unbudgeted and receipt.reservation_id
        open_effect_receipt_scope({"session_id": session_b, "workspace_root": str(workspace)})
        try:
            held = _run(
                f"{plugin_world['plugin_id']}.touch",
                {"path": str(plugin_dir / "while-held.txt")},
                session_id=session_b,
                workspace=workspace,
            )
        finally:
            close_effect_receipt_scope()
        assert held.status == "blocked_by_effect_budget", "the held unit is not spendable twice"

        stale = [row for row in eb.reservation_rows() if row["state"] == eb.RESERVATION_RESERVED]
        assert len(stale) == 1
        for row in stale:
            eb.release_reservation(row["reservation_id"], reason="reconciled stale after restart")
        open_effect_receipt_scope({"session_id": session_b, "workspace_root": str(workspace)})
        try:
            recovered = _run(
                f"{plugin_world['plugin_id']}.touch",
                {"path": str(plugin_dir / "recovered.txt")},
                session_id=session_b,
                workspace=workspace,
            )
        finally:
            close_effect_receipt_scope()
        assert recovered.ok, "the reconciled unit returns to the pool and is spendable again"


class TestMCPBudgetDoor:
    def test_pinned_mutating_mcp_tool_registers_with_the_bridge_declaration(self, mcp_world):
        from core.blackbox.coverage import registry as coverage_registry
        from core.tool_registry import tool_for_intent

        contract = tool_for_intent("mcp.stub.write_outside")
        assert contract.side_effect_class == "workspace_write"
        assert coverage_registry.capability_for("mcp.stub.write_outside") is not None, (
            "the pinned mutation carries its coverage declaration into the registry seat"
        )

    def test_exhausted_budget_refuses_mcp_effect_with_zero_server_calls(
        self, mcp_world, auto_mode, set_budget, workspace_and_scratch
    ):
        from core import effect_budget as eb

        workspace, _scratch = workspace_and_scratch
        mcp_cwd = mcp_world["cwd"]
        session = auto_mode(f"openclaw:{uuid.uuid4().hex[:20]}")
        set_budget(("file_write", eb.SCOPE_SESSION, 1))

        from core.effect_gateway import close_effect_receipt_scope, open_effect_receipt_scope

        open_effect_receipt_scope({"session_id": session, "workspace_root": str(workspace)})
        try:
            first = _run(
                "mcp.stub.write_outside",
                {"path": str(mcp_cwd / "mcp-one.txt")},
                session_id=session,
                workspace=workspace,
            )
            second = _run(
                "mcp.stub.write_outside",
                {"path": str(mcp_cwd / "mcp-two.txt")},
                session_id=session,
                workspace=workspace,
            )
        finally:
            close_effect_receipt_scope()

        assert first.ok, first.response_text
        assert (mcp_cwd / "mcp-one.txt").exists()
        assert second.ok is False and second.status == "blocked_by_effect_budget"
        assert second.details["effect_budget"]["code"] == eb.REFUSAL_BUDGET_EXCEEDED
        assert not (mcp_cwd / "mcp-two.txt").exists()
        assert _mcp_calls(mcp_world["call_log"]) == ["write_outside"], (
            "zero server calls on refusal: the stdio server was never asked to run the tool again"
        )
        rows = eb.reservation_rows()
        assert len(rows) == 1 and rows[0]["state"] == eb.RESERVATION_CONSUMED

    def test_read_only_mcp_tool_never_consumes_mutation_budgets(
        self, mcp_world, auto_mode, set_budget, workspace_and_scratch
    ):
        from core import effect_budget as eb

        workspace, _scratch = workspace_and_scratch
        session = auto_mode(f"openclaw:{uuid.uuid4().hex[:20]}")
        set_budget(("file_write", eb.SCOPE_SESSION, 1))

        from core.effect_gateway import close_effect_receipt_scope, open_effect_receipt_scope

        open_effect_receipt_scope({"session_id": session, "workspace_root": str(workspace)})
        try:
            for index in range(2):
                result = _run(
                    "mcp.stub.echo",
                    {"text": f"probe-{index}"},
                    session_id=session,
                    workspace=workspace,
                )
                assert result.ok, result.response_text
        finally:
            close_effect_receipt_scope()
        assert eb.reservation_rows() == []
        assert _mcp_calls(mcp_world["call_log"]) == ["echo", "echo"]


class TestRegistrationRejection:
    def test_undeclared_mutating_plugin_tool_never_becomes_dispatchable(self, tmp_path, monkeypatch):
        from core import plugin_tools
        from core.runtime_flags import override
        from core.tool_registry import registry_map
        from tests._toolchain_fixtures import default_tools, make_plugin, reset_toolchain_state

        bad_tool = {
            "intent": f"{PLUGIN_ID}.undeclared",
            "description": "A workspace writer that declares no Blackbox coverage.",
            "handler": {"kind": "subprocess", "entry": "bin/run"},
            "input_schema": {"type": "object", "additionalProperties": False, "required": [], "properties": {}},
            "side_effect_class": "workspace_write",
            "approval_requirement": "runtime_policy",
            "permission_actions": ["create_files"],
            "claim": {},
            # no `mutation` — the rejection is the point
        }
        tools = [tool for tool in default_tools(PLUGIN_ID) if not tool["intent"].endswith(".crash")]
        tools.append(bad_tool)
        make_plugin(tmp_path, tools=tools, admit=False)
        monkeypatch.setenv("VOOL_PLUGINS_DIR", str(tmp_path))
        reset_toolchain_state()
        with override("plugin_runtime_tools", True):
            loaded, errors = plugin_tools.load_all(tmp_path)
            undeclared_registered = registry_map().get(f"{PLUGIN_ID}.undeclared")
        assert f"{PLUGIN_ID}.undeclared" not in {contract.intent for contract in loaded}, (
            "an undeclared mutation is not an installable tool"
        )
        assert errors or undeclared_registered is None or not undeclared_registered.supported, (
            "the rejection is explained, not silent"
        )
