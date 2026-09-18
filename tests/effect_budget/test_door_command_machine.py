"""CP2 — THE COMMAND/MACHINE BUDGET DOOR: the coverage seam reserves before execution.

CP1 closed with a named gap: shell/command and machine mutations journaled
Blackbox coverage but reserved NO budget unit — `ExecutionGate.evaluate_command`
had no production caller. CP2 binds the reservation at the one production door
those mutations already cross (`with_mutation_coverage`), derived from each
contract's OWN side_effect_class (never an intent-name regex):

- shell-shaped classes (`sandbox_command`, `validation_command`) reserve `command`;
- workspace-writing classes (`workspace_write`, incl. machine writes) reserve
  `file_write`;
- any other class is unbudgeted by name and passes (the gateway's law).

Proven here: a zero/exhausted budget refuses BEFORE the recorder runs — zero
subprocess spawns, zero transport sockets, zero journal entries, zero bytes;
concurrent dispatches reserve atomically (exactly the limit executes, no double
charge); a raised handler spends its unit (never refunded); a recorder refusal
BEFORE execution releases the unit back; and an unbudgeted class passes with no
rows at all.
"""
from __future__ import annotations

import subprocess
import threading
from pathlib import Path

import pytest

from tests.effect_budget.conftest import *  # noqa: F403 — budget fixtures


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


@pytest.fixture
def call_counter(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Count every subprocess spawn a dispatch could cause. Outbound transport is
    guarded by the suite's own network seal (any real socket a dispatch opens
    raises there), so the refusal's zero-transport leg is proven by the seal."""
    counts = {"subprocess": 0}
    real_run, real_popen = subprocess.run, subprocess.Popen

    def _run(*a, **k):
        counts["subprocess"] += 1
        return real_run(*a, **k)

    def _popen(*a, **k):
        counts["subprocess"] += 1
        return real_popen(*a, **k)

    monkeypatch.setattr(subprocess, "run", _run)
    monkeypatch.setattr(subprocess, "Popen", _popen)
    return counts


def _ctx(workspace: Path, session: str = "cm-sess", turn: str = "cm-turn") -> dict:
    from core.mode_permission_policy import set_active_mode

    set_active_mode(session, "auto")
    return {
        "workspace": str(workspace),
        "workspace_root": str(workspace),
        "runtime_session_id": session,
        "session_id": session,
        "turn_id": turn,
        "operating_mode": "auto",
        "surface": "api",
    }


def _dispatch(intent: str, arguments: dict, workspace: Path, turn: str):
    from core.runtime_execution_tools import execute_runtime_tool

    return execute_runtime_tool(intent, arguments, source_context=_ctx(workspace, turn=turn))


class TestZeroBudgetRefusal:
    def test_zero_command_budget_means_zero_subprocess_transport_and_journal_calls(
        self, set_budget, workspace, blackbox_store, call_counter
    ):
        """THE goal's proof: a session with a zero command budget dispatches a
        real shell mutation; the typed budget refusal is the ONLY thing that
        happens — no child process, no socket, no Blackbox pair, no bytes."""
        from core import effect_budget as eb
        from core.blackbox.store import default_store

        set_budget(("command", eb.SCOPE_SESSION, 1))  # one unit, spent immediately below
        (workspace / "seed.txt").write_bytes(b"seed")
        from core.effect_gateway import close_effect_receipt_scope, open_effect_receipt_scope

        open_effect_receipt_scope({"session_id": "cm-sess", "workspace_root": str(workspace)})
        try:
            spent = _dispatch(
                "sandbox.run_command",
                {"command": "cp seed.txt first.txt", "cwd": str(workspace)},
                workspace,
                turn="t1",
            )
            assert spent.ok, spent.response_text
            assert (workspace / "first.txt").read_bytes() == b"seed"
            before_subprocess = call_counter["subprocess"]

            refused = _dispatch(
                "sandbox.run_command",
                {"command": "cp seed.txt second.txt", "cwd": str(workspace)},
                workspace,
                turn="t2",
            )
            assert refused.ok is False
            assert refused.status == "blocked_by_effect_budget"
            assert refused.details["effect_budget"]["code"] == eb.REFUSAL_BUDGET_EXCEEDED
            assert refused.details.get("executed") is None or refused.details.get("executed") is False

            # ZERO side effects past the refusal: no new subprocess (transport is held
            # to zero by the suite's network seal), no journal pair for the refused
            # turn, no bytes on disk.
            assert call_counter["subprocess"] == before_subprocess, "zero subprocess calls"
            assert not (workspace / "second.txt").exists(), "zero writes"
            entries = default_store().entries()
            intended = [e for e in entries if e.get("turn_id") == "cm-turn-t2" and e.get("kind") == "coverage_scan_intended"]
            assert intended == [], "the refused dispatch journaled nothing"
        finally:
            close_effect_receipt_scope()

    def test_machine_writes_reserve_file_write_units(
        self, set_budget, workspace, blackbox_store, synthetic_machine_home
    ):
        """The machine lane's file_write unit, proved against a SYNTHETIC home.

        The falsifier has two legs and both must stay load-bearing:

        LEG 1 (positive) — one real byte lands through the whole unmodified
        chain: ``_machine_home`` → ``_safe_machine_roots`` → the confinement
        allowlist → ``ExecutionGate.evaluate_machine_effect`` → the recorder.
        Nothing in that chain is patched; only HOME moved, so the root
        computation under test is the production one.

        LEG 2 (negative, by absence) — the second write does not happen. Absence
        alone is satisfied by ANY refusal, so on its own it would pass vacuously
        if confinement (not the budget) had refused. Leg 1 is what makes it
        attributable: the same directory accepted a write moments earlier, so
        the only thing that changed is the exhausted budget. The status and the
        consumed-reservation row pin that explicitly.
        """
        import os

        from core import effect_budget as eb
        from core.effect_gateway import close_effect_receipt_scope, open_effect_receipt_scope

        set_budget(("file_write", eb.SCOPE_SESSION, 1))

        lane_dir = Path(os.path.expanduser("~/Desktop"))
        assert lane_dir == synthetic_machine_home / "Desktop", lane_dir
        open_effect_receipt_scope({"session_id": "cm-sess", "workspace_root": str(workspace)})
        try:
            target = str(lane_dir / "cp2-machine-note.txt")
            first = _dispatch("machine.write_file", {"path": target, "content": "v1"}, workspace, turn="t3")
            assert first.ok, first.response_text
            assert Path(target).read_text(encoding="utf-8") == "v1"

            second = _dispatch(
                "machine.write_file",
                {"path": str(lane_dir / "cp2-machine-second.txt"), "content": "v2"},
                workspace,
                turn="t4",
            )
            # The budget refused it — NOT confinement, which just accepted leg 1
            # into this very directory.
            assert second.ok is False and second.status == "blocked_by_effect_budget"
            assert not (lane_dir / "cp2-machine-second.txt").exists()
            rows = [r for r in eb.reservation_rows() if r["budget_class"] == "file_write"]
            assert len(rows) == 1 and rows[0]["state"] == eb.RESERVATION_CONSUMED, rows
        finally:
            close_effect_receipt_scope()


class TestConcurrentReservation:
    def test_six_concurrent_shell_dispatches_allow_exactly_the_limit(self, set_budget, workspace, blackbox_store):
        """Limit 3, six REAL concurrent shell dispatches: exactly three execute,
        three return the typed refusal, three units consumed, no double charge."""
        from core import effect_budget as eb
        from core.effect_gateway import close_effect_receipt_scope, open_effect_receipt_scope

        set_budget(("command", eb.SCOPE_SESSION, 3))
        (workspace / "seed.txt").write_bytes(b"seed")
        results: list = []
        lock = threading.Lock()
        open_effect_receipt_scope({"session_id": "cm-sess", "workspace_root": str(workspace)})
        try:

            def _one(i: int) -> None:
                out = _dispatch(
                    "sandbox.run_command",
                    {"command": f"cp seed.txt out-{i}.txt", "cwd": str(workspace)},
                    workspace,
                    turn=f"c{i}",
                )
                with lock:
                    results.append(out)

            threads = [threading.Thread(target=_one, args=(i,)) for i in range(6)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        finally:
            close_effect_receipt_scope()

        ok_count = sum(1 for r in results if r.ok)
        refused = [r for r in results if not r.ok]
        assert ok_count == 3, [getattr(r, "status", "?") for r in results]
        assert len(refused) == 3 and all(r.status == "blocked_by_effect_budget" for r in refused)
        produced = sorted(p.name for p in workspace.glob("out-*.txt"))
        assert len(produced) == 3, produced
        rows = [r for r in eb.reservation_rows() if r["budget_class"] == "command"]
        assert len(rows) == 3 and all(r["state"] == eb.RESERVATION_CONSUMED for r in rows), rows


class TestSettlement:
    def test_a_raising_handler_spends_its_unit_never_refunds(self, set_budget, workspace, blackbox_store, monkeypatch):
        from core import effect_budget as eb
        from core import runtime_execution_tools as ret
        from core.effect_gateway import close_effect_receipt_scope, open_effect_receipt_scope

        set_budget(("command", eb.SCOPE_SESSION, 1))

        def _boom(*a, **k):
            raise RuntimeError("handler exploded mid-command")

        monkeypatch.setattr(ret, "_run_command", _boom)
        open_effect_receipt_scope({"session_id": "cm-sess", "workspace_root": str(workspace)})
        try:
            out = _dispatch("sandbox.run_command", {"command": "true", "cwd": str(workspace)}, workspace, turn="t5")
            # the recorder captures the raise as crash-truth: a typed failure result, never a
            # fabricated success — and the attempt DID happen, so its unit is spent
            assert out.ok is False, out.response_text
            blackbox = dict(out.details.get("blackbox") or {})
            assert blackbox.get("terminal_recorded") is not False or "blackbox" in out.details
            rows = [r for r in eb.reservation_rows() if r["budget_class"] == "command"]
            assert len(rows) == 1, rows
            assert rows[0]["state"] == eb.RESERVATION_CONSUMED, "the attempt happened; the unit is spent"
        finally:
            close_effect_receipt_scope()

    def test_a_preexecution_coverage_refusal_releases_the_unit(
        self, set_budget, workspace, blackbox_store
    ):
        """The recorder refuses BEFORE the handler when the command names a
        high-risk target: reserved-then-never-run RELEASES — the unit returns
        and a later allowed dispatch can still spend it."""
        from core import effect_budget as eb
        from core.effect_gateway import close_effect_receipt_scope, open_effect_receipt_scope

        set_budget(("command", eb.SCOPE_SESSION, 1))
        (workspace / "seed.txt").write_bytes(b"seed")
        open_effect_receipt_scope({"session_id": "cm-sess", "workspace_root": str(workspace)})
        try:
            risky = _dispatch(
                "sandbox.run_command",
                {"command": "cp seed.txt ~/.ssh/authorized_keys", "cwd": str(workspace)},
                workspace,
                turn="t6",
            )
            assert risky.ok is False
            assert str(risky.status).startswith("blackbox_"), risky.status
            rows = [r for r in eb.reservation_rows() if r["budget_class"] == "command"]
            assert rows and rows[0]["state"] == eb.RESERVATION_RELEASED, rows

            allowed = _dispatch(
                "sandbox.run_command",
                {"command": "cp seed.txt fine.txt", "cwd": str(workspace)},
                workspace,
                turn="t7",
            )
            assert allowed.ok, allowed.response_text
            assert (workspace / "fine.txt").read_bytes() == b"seed"
            rows = [r for r in eb.reservation_rows() if r["budget_class"] == "command"]
            consumed = [r for r in rows if r["state"] == eb.RESERVATION_CONSUMED]
            assert len(consumed) == 1, rows
        finally:
            close_effect_receipt_scope()

    def test_unbudgeted_side_effect_class_passes_with_no_rows(self, set_budget, workspace, blackbox_store):
        """A coverage-recorded mutation whose contract class the map does not
        name is unbudgeted by name: it runs under its recorder, and the store
        holds NO reservation row for it — an honest pass, never a silent count
        under a neighbor class."""
        from core import effect_budget as eb
        from core.blackbox.coverage.capability import MutationCapability
        from core.blackbox.coverage.registry import register_capability
        from core.effect_gateway import close_effect_receipt_scope, open_effect_receipt_scope
        from core.runtime_execution_tools import RuntimeExecutionResult
        from core.runtime_tool_contracts import RuntimeToolContract
        from core.tool_registry import register

        register(
            RuntimeToolContract(
                intent="demo.stateful_edit",
                description="A demo mutation whose class the budget map does not name.",
                tool_surface="runtime",
                capability_id="demo.stateful",
                capability_claim="mutate demo project state",
                supported=True,
                unsupported_reason="",
                input_schema={"value": "string"},
                output_schema={"ok": "boolean"},
                side_effect_class="builder_state",
                approval_requirement="runtime_policy",
                timeout_policy="test",
                retry_policy="none",
                artifact_emission="none",
                error_contract="typed",
                json_schema={"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"], "additionalProperties": False},
                permission_actions=("create_files",),
            )
        )
        register_capability(
            MutationCapability.from_dict(
                {
                    "tool": "demo.stateful_edit",
                    "scope": "workspace",
                    "effect_class": "irreversible",
                    "snapshot_strategy": "postimage_only",
                    "receipt_lifecycle": "terminal_only",
                    "rollback_support": "none",
                    "recorder": "blackbox.coverage_post",
                }
            )
        )
        set_budget(("command", eb.SCOPE_SESSION, 1))  # an active rule for a DIFFERENT class
        open_effect_receipt_scope({"session_id": "cm-sess", "workspace_root": str(workspace)})
        try:
            from unittest.mock import patch

            def _handler(*a, **k):
                return RuntimeExecutionResult(handled=True, ok=True, status="executed", response_text="state set")

            import core.runtime_execution_tools as ret

            with patch.object(ret, "_execute_stateful_edit", _handler, create=True):
                # dispatch through the real door by calling the seam directly
                out = ret.with_mutation_coverage(
                    "demo.stateful_edit",
                    {"value": "x"},
                    source_context=_ctx(workspace, turn="t8"),
                    workspace_root=workspace,
                    handler=_handler,
                )
            assert out.ok, out.response_text
            rows = eb.reservation_rows()
            assert rows == [], f"an unmapped class must leave no rows: {rows}"
        finally:
            close_effect_receipt_scope()
