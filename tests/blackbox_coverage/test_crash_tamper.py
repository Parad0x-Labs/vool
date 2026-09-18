"""Crash recovery and tamper detection: an interrupted effect leaves a RECOVERABLE journal that
never claims success, and any edit to the journal is detectable -- recovery and restore both
refuse to launder a broken chain."""
from __future__ import annotations

import json

import pytest

from tests.blackbox_coverage._ctx import ctx
def _crashed_effect(workspace, turn: str = "crash-turn"):
    """Simulate a process death between INTENDED and TERMINAL: append the intended entry with its
    embedded preimage table and never write the terminal."""
    from core.blackbox.coverage.scan import scan_workspace
    from core.blackbox.identity import identity_from_context
    from core.blackbox.store import default_store, utcnow

    store = default_store()
    (workspace / "crash-seed.txt").write_bytes(b"crash-before")
    before = scan_workspace(workspace, store=store)
    identity = identity_from_context(ctx(workspace, turn=turn))
    store.append(
        {
            "schema": "blackbox_effect_v1",
            **identity.to_dict(),
            "root": str(workspace),
            "intent": "sandbox.run_command",
            "kind": "coverage_scan_intended",
            "effect_id": "effect-crash-1",
            "manifest_sha256": before.manifest_sha256,
            "files": before.files,
            "files_scanned": before.files_scanned,
            "bytes_captured": before.bytes_captured,
            "degraded": before.degraded,
            "started_at": utcnow(),
        }
    )
    # The mutation "happened" after the crash point.
    (workspace / "crash-seed.txt").write_bytes(b"crash-after")
    (workspace / "crash-new.txt").write_bytes(b"appeared")
    return store


class TestCrashRecovery:
    def test_open_effect_closes_unknown_crashed_never_success(self, workspace, store_dir):
        store = _crashed_effect(workspace)
        from core.blackbox.coverage.restore import recover_open_coverage

        closed = recover_open_coverage(store)
        assert len(closed) == 1
        terminal = closed[0]
        assert terminal["outcome"] == "unknown_crashed"
        drift = {row["path"]: row for row in terminal["drift"]}
        assert drift["crash-seed.txt"]["drift_kind"] == "changed"
        assert drift["crash-new.txt"]["drift_kind"] == "created"
        # Recovery is idempotent: the effect is closed exactly once.
        assert recover_open_coverage(store) == []

    def test_crashed_effect_still_rolls_back_exactly_from_preimages(self, workspace, store_dir):
        store = _crashed_effect(workspace)
        from core.blackbox import authority
        from core.blackbox.coverage.restore import restore_coverage_turn

        token = authority.mint_rollback_authorization(turn_id="crash-turn", workspace_root=workspace, operator="op", store=store)
        result = restore_coverage_turn(turn_id="crash-turn", workspace_root=workspace, token=token, store=store)
        assert result["ok"], result
        assert (workspace / "crash-seed.txt").read_bytes() == b"crash-before", "preimage restored"
        assert not (workspace / "crash-new.txt").exists(), "created file removed"


class TestTamperDetection:
    def test_edited_journal_line_fails_the_chain_and_gates_recovery(self, workspace, store_dir):
        store = _crashed_effect(workspace)
        journal_path = store_dir / "journal.jsonl"
        lines = journal_path.read_text().splitlines()
        edited = json.loads(lines[0])
        edited["intent"] = "tampered.intent"
        lines[0] = json.dumps(edited, sort_keys=True, separators=(",", ":"))
        journal_path.write_text("\n".join(lines) + "\n")

        report = store.verify()
        assert report.ok is False
        assert report.reason in {"mac_mismatch", "hash_chain_broken", "hash_mismatch", "sequence_gap"}

        from core.blackbox.coverage.restore import recover_open_coverage
        from storage.blackbox.journal import JournalIntegrityError

        with pytest.raises((JournalIntegrityError, Exception)):
            recover_open_coverage(store)

    def test_truncated_tail_is_detected(self, workspace, store_dir):
        from core.blackbox.coverage.recorder import recorded_capability_mutation
        from core.blackbox.store import default_store
        from core.runtime_execution_tools import RuntimeExecutionResult

        recorded_capability_mutation(
            "sandbox.run_command",
            {"command": "touch a.txt", "cwd": str(workspace)},
            handler=lambda: RuntimeExecutionResult(handled=True, ok=True, status="executed", response_text="", details={}),
            source_context=ctx(workspace, turn="t-trunc"),
            workspace_root=workspace,
        )
        store = default_store()
        journal_path = store_dir / "journal.jsonl"
        lines = journal_path.read_text().splitlines()
        journal_path.write_text("\n".join(lines[:-1]) + "\n")
        report = store.verify()
        assert report.ok is False
        assert report.reason in {"tail_truncated", "head_mismatch", "hash_chain_broken"}
