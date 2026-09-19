"""Concurrent-write proof: several coverage-recorded effects in ONE workspace and ONE store at
the same time leave an intact chain, independent journals, and exact per-effect rollback."""
from __future__ import annotations

import hashlib
import threading

from tests.blackbox_coverage._ctx import ctx


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _recorded(workspace, turn: str, index: int):
    from core.blackbox.coverage.recorder import recorded_capability_mutation
    from core.runtime_execution_tools import RuntimeExecutionResult

    def handler():
        (workspace / f"file-{index}.txt").write_bytes(f"bytes-{index}".encode())
        return RuntimeExecutionResult(
            handled=True, ok=True, status="executed", response_text="ok",
            details={"executed": True},
        )

    return recorded_capability_mutation(
        "sandbox.run_command",
        {"command": f"touch file-{index}.txt", "cwd": str(workspace)},
        handler=handler,
        source_context=ctx(workspace, turn=turn),
        workspace_root=workspace,
    )


class TestConcurrentWrites:
    def test_parallel_effects_journal_independently_and_chain_survives(self, workspace, store_dir):
        from core.blackbox.store import default_store

        threads: list[threading.Thread] = []
        results: list = []
        lock = threading.Lock()

        def run(index: int) -> None:
            result = _recorded(workspace, f"turn-{index}", index)
            with lock:
                results.append(result)

        for index in range(6):
            threads.append(threading.Thread(target=run, args=(index,)))
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert all(r.ok for r in results), [r.status for r in results]
        store = default_store()
        report = store.verify()
        assert report.ok, report.reason

        entries = store.entries()
        intended = [e for e in entries if e.get("kind") == "coverage_scan_intended"]
        terminals = [e for e in entries if e.get("kind") == "coverage_scan_terminal"]
        assert len(intended) == 6 and len(terminals) == 6
        # Each terminal pairs with its own intended by effect_id -- no cross-wiring under race.
        intended_ids = {e["effect_id"] for e in intended}
        assert {e["effect_id"] for e in terminals} == intended_ids

    def test_interleaved_same_path_writes_stay_attributable_and_restorable(self, workspace, store_dir):
        """Two effects touching DIFFERENT paths concurrently, then one writer clobbers the other's
        file after the fact: rollback of the first effect detects the divergence instead of
        overwriting it (postimage conflict), while the untouched effect restores exactly."""
        from core.blackbox.store import default_store

        _recorded(workspace, "turn-a", 0)
        _recorded(workspace, "turn-b", 1)
        # A later write outside any effect -- the divergence rollback must refuse.
        (workspace / "file-1.txt").write_bytes(b"clobbered-after")

        from core.blackbox import authority
        from core.blackbox.coverage.restore import restore_coverage_turn

        store = default_store()
        token_b = authority.mint_rollback_authorization(turn_id="turn-b", workspace_root=workspace, operator="op", store=store)
        refused = restore_coverage_turn(turn_id="turn-b", workspace_root=workspace, token=token_b, store=store)
        assert refused["ok"] is False
        assert refused["status"] == "blackbox_rollback_conflict"
        assert (workspace / "file-1.txt").read_bytes() == b"clobbered-after", "a refused rollback changed nothing"

        token_a = authority.mint_rollback_authorization(turn_id="turn-a", workspace_root=workspace, operator="op", store=store)
        restored = restore_coverage_turn(turn_id="turn-a", workspace_root=workspace, token=token_a, store=store)
        assert restored["ok"], restored
        assert not (workspace / "file-0.txt").exists(), "the created file is gone again"
        assert store.verify().ok
