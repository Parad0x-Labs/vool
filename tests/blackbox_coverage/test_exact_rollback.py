"""Exact rollback proof: bytes, mode and mtime restored; created trees removed; idempotent
re-runs; divergence typed and all-or-nothing."""
from __future__ import annotations

import os

from tests.blackbox_coverage._ctx import ctx
def _recorded_shell(workspace, turn: str):
    from core.blackbox.coverage.recorder import recorded_capability_mutation
    from core.runtime_execution_tools import RuntimeExecutionResult

    def handler():
        (workspace / "modified.txt").write_bytes(b"after-bytes")
        os.chmod(workspace / "modified.txt", 0o777)
        nested = workspace / "made" / "deep"
        nested.mkdir(parents=True, exist_ok=True)
        (nested / "leaf.txt").write_bytes(b"leaf")

        return RuntimeExecutionResult(handled=True, ok=True, status="executed", response_text="ok", details={})

    return recorded_capability_mutation(
        "sandbox.run_command",
        {"command": "script", "cwd": str(workspace)},
        handler=handler,
        source_context=ctx(workspace, turn=turn),
        workspace_root=workspace,
    )


def _restore(workspace, turn: str, store):
    from core.blackbox import authority
    from core.blackbox.coverage.restore import restore_coverage_turn

    token = authority.mint_rollback_authorization(turn_id=turn, workspace_root=workspace, operator="op", store=store)
    return restore_coverage_turn(turn_id=turn, workspace_root=workspace, token=token, store=store)


class TestExactRollback:
    def test_bytes_mode_and_mtime_return_exactly(self, workspace, store_dir):
        target = workspace / "modified.txt"
        target.write_bytes(b"before-bytes")
        os.chmod(target, 0o640)
        before_stat = os.stat(target)

        result = _recorded_shell(workspace, "turn-exact")
        assert result.ok, result.response_text

        from core.blackbox.store import default_store

        store = default_store()
        restored = _restore(workspace, "turn-exact", store)
        assert restored["ok"], restored

        assert target.read_bytes() == b"before-bytes"
        assert (os.stat(target).st_mode & 0o777) == 0o640, "mode must return exactly"
        assert os.stat(target).st_mtime_ns == before_stat.st_mtime_ns, "mtime must return exactly"

    def test_created_files_and_their_directories_are_removed(self, workspace, store_dir):
        (workspace / "modified.txt").write_bytes(b"before")
        _recorded_shell(workspace, "turn-tree")
        assert (workspace / "made" / "deep" / "leaf.txt").exists()

        from core.blackbox.store import default_store

        restored = _restore(workspace, "turn-tree", default_store())
        assert restored["ok"], restored
        assert not (workspace / "made" / "deep" / "leaf.txt").exists()
        assert not (workspace / "made").exists(), "an empty tree the before state never had is removed"

    def test_rollback_is_idempotent(self, workspace, store_dir):
        (workspace / "modified.txt").write_bytes(b"before")
        _recorded_shell(workspace, "turn-idem")
        from core.blackbox.store import default_store

        store = default_store()
        assert _restore(workspace, "turn-idem", store)["ok"]
        again = _restore(workspace, "turn-idem", store)
        assert again["ok"], again
        assert (workspace / "modified.txt").read_bytes() == b"before"

    def test_divergence_refuses_all_or_nothing(self, workspace, store_dir):
        (workspace / "modified.txt").write_bytes(b"before")
        _recorded_shell(workspace, "turn-div")
        # Diverge BOTH touched paths after the effect.
        (workspace / "modified.txt").write_bytes(b"clobbered")
        from core.blackbox.store import default_store

        outcome = _restore(workspace, "turn-div", default_store())
        assert outcome["ok"] is False
        assert outcome["status"] == "blackbox_rollback_conflict"
        assert (workspace / "modified.txt").read_bytes() == b"clobbered", "refused rollback wrote nothing"

    def test_token_authority_binds_turn_and_root(self, workspace, store_dir):
        from core.blackbox.store import default_store

        _recorded_shell(workspace, "turn-auth")
        from core.blackbox import authority
        from core.blackbox.coverage.restore import restore_coverage_turn

        wrong_turn = authority.mint_rollback_authorization(turn_id="other-turn", workspace_root=workspace, operator="op", store=default_store())
        outcome = restore_coverage_turn(turn_id="turn-auth", workspace_root=workspace, token=wrong_turn, store=default_store())
        assert outcome["ok"] is False
        assert outcome["status"] == "blackbox_rollback_not_authorized"


def _write_version(workspace, turn: str, version: bytes):
    """One scan-covered shell mutation whose whole effect is writing `same.txt`."""
    from core.blackbox.coverage.recorder import recorded_capability_mutation
    from core.runtime_execution_tools import RuntimeExecutionResult

    def handler():
        (workspace / "same.txt").write_bytes(version)
        return RuntimeExecutionResult(handled=True, ok=True, status="executed", response_text="ok")

    return recorded_capability_mutation(
        "sandbox.run_command",
        {"command": "script", "cwd": str(workspace)},
        handler=handler,
        source_context=ctx(workspace, turn=turn),
        workspace_root=workspace,
    )


def test_same_path_repeated_writes_unwind_in_turn_order_and_reversed_order_refuses(workspace, store_dir):
    """CP2's named law: a multi-mutation history that writes the SAME path twice restores the
    ORIGINAL bytes when replayed in turn order (newest turn first), and REVERSING that order
    must fail typed — the older turn's postimage no longer matches, so the rollback refuses
    rather than overwrite a newer truth."""
    from core.blackbox import authority
    from core.blackbox.coverage.restore import restore_coverage_turn
    from core.blackbox.store import default_store

    target = workspace / "same.txt"
    target.write_bytes(b"v0")
    assert _write_version(workspace, "same-a", b"v1").ok
    assert _write_version(workspace, "same-b", b"v2").ok
    assert target.read_bytes() == b"v2"

    store = default_store()
    token_b = authority.mint_rollback_authorization(turn_id="same-b", workspace_root=workspace, operator="op", store=store)
    token_a = authority.mint_rollback_authorization(turn_id="same-a", workspace_root=workspace, operator="op", store=store)

    # Correct order: newest first — v2 unwinds to v1, then v1 unwinds to v0.
    rolled_b = restore_coverage_turn(turn_id="same-b", workspace_root=workspace, token=token_b, store=store)
    assert rolled_b["ok"], rolled_b
    assert target.read_bytes() == b"v1", "the newest effect's preimage is v1"
    rolled_a = restore_coverage_turn(turn_id="same-a", workspace_root=workspace, token=token_a, store=store)
    assert rolled_a["ok"], rolled_a
    assert target.read_bytes() == b"v0", "the ORIGINAL bytes return through the stack"

    # Reversed order on a fresh identical history: the older turn's rollback must REFUSE —
    # its postimage (v1) no longer matches the disk (v2) — and change nothing.
    target.write_bytes(b"v0")
    assert _write_version(workspace, "same-c", b"v1").ok
    assert _write_version(workspace, "same-d", b"v2").ok
    store = default_store()
    token_c = authority.mint_rollback_authorization(turn_id="same-c", workspace_root=workspace, operator="op", store=store)
    reversed_first = restore_coverage_turn(turn_id="same-c", workspace_root=workspace, token=token_c, store=store)
    assert reversed_first["ok"] is False, reversed_first
    assert reversed_first["status"] == "blackbox_rollback_conflict"
    assert target.read_bytes() == b"v2", "a refused rollback changed nothing"
