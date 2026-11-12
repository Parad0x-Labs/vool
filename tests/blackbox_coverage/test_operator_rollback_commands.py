"""Operator rollback of command runs: the turn a shell/test/lint/formatter command changed is
listed, and ONE operator rollback -- the same token, lock and permission boundary the file
effects use -- undoes the command's drift together with the turn's file effects, or refuses the
whole turn with nothing written.

Every command here is a real sandboxed shell command dispatched through the runtime, recorded by
the coverage lane; every rollback goes through ``core.blackbox.operator.rollback_turn`` and the
authorized execution boundary, exactly as ``vool blackbox rollback`` and the command API do.
"""
from __future__ import annotations

import os
from pathlib import Path

from tests.blackbox_coverage._ctx import ctx


def _py(code: str) -> str:
    """A real sandboxed command that writes files: the sandbox allows `python3`, not `printf`."""
    return f'python3 -c "{code}"'


def _run(workspace: Path, command: str, *, turn: str, session: str = "cov-sess"):
    from core.runtime_execution_tools import execute_runtime_tool

    result = execute_runtime_tool(
        "sandbox.run_command", {"command": command, "cwd": str(workspace)},
        source_context=ctx(workspace, session=session, turn=turn),
    )
    assert result.ok, result.response_text
    return result


def _write(workspace: Path, path: str, content: str, *, turn: str):
    from core.runtime_execution_tools import execute_runtime_tool

    result = execute_runtime_tool(
        "workspace.write_file", {"path": path, "content": content}, source_context=ctx(workspace, turn=turn)
    )
    assert result.ok, result.response_text
    return result


def _rollback(turn: str, workspace: Path):
    from core.blackbox.operator import rollback_turn
    from core.mode_permission_policy import PermissionAction, grant_internal_authority, set_active_mode

    session = "operator:command-rollback"
    set_active_mode(session, "auto")
    token = grant_internal_authority(
        label="blackbox-command-rollback-test",
        actions=[PermissionAction.DELETE_FILES, PermissionAction.OVERWRITE_EXISTING_FILES, PermissionAction.MODIFY_FILES],
        intents=["workspace.rollback_last_change"],
        workspace_root=str(workspace.resolve()),
        duration_seconds=120,
    )
    return rollback_turn(
        turn,
        workspace_root=workspace,
        session_id=session,
        operator="test-operator",
        source_context={"operating_mode": "auto", "surface": "api"},
        authority_token=token,
    )


def _kinds(*kinds: str) -> list[dict]:
    from core.blackbox.store import default_store

    return [entry for entry in default_store().entries() if entry.get("kind") in kinds]


class TestCommandRunTurns:
    def test_command_turn_is_listed_and_one_operator_rollback_restores_it(self, workspace, store_dir):
        keep = workspace / "keep.txt"
        keep.write_bytes(b"original\n")
        os.chmod(keep, 0o640)
        _run(workspace, _py("import os; open('keep.txt','w').write('changed\\n'); os.chmod('keep.txt', 0o600); "
                            "os.makedirs('out/deep'); open('out/deep/made.txt','w').write('new')"), turn="cmd-1")

        from core.blackbox.operator import list_turns
        from core.blackbox.store import default_store

        turns = {turn["turn_id"]: turn for turn in list_turns(workspace_root=workspace)}
        assert "cmd-1" in turns, "a command-only turn must be nameable to the operator"
        command_effects = [e for e in turns["cmd-1"]["effects"] if e.get("lane") == "command"]
        assert command_effects and command_effects[0]["rollback_capable"] is True
        assert set(command_effects[0]["paths"]) == {"keep.txt", "out/deep/made.txt"}
        status = default_store().status()
        assert status["command_effects_total"] == 1 and status["command_turns"] == 1
        assert status["command_effects_open"] == []

        result = _rollback("cmd-1", workspace)
        assert result.ok, result.response_text
        assert result.status == "executed"
        summary = result.details["blackbox_rollback"]
        assert summary["restored_paths"] == ["keep.txt"]
        assert "out/deep/made.txt" in summary["removed_paths"]
        assert keep.read_bytes() == b"original\n"
        assert (os.stat(keep).st_mode & 0o777) == 0o640, "mode returns with the bytes"
        assert not (workspace / "out").exists(), "the tree the command created goes with it"
        assert [e["rollback_of_turn"] for e in _kinds("rollback_committed")] == ["cmd-1"]
        assert {t["turn_id"]: t for t in list_turns(workspace_root=workspace)}["cmd-1"]["rolled_back"] is True

        again = _rollback("cmd-1", workspace)
        assert again.status == "already_rolled_back", again.response_text
        assert len(_kinds("rollback_committed")) == 1
        assert default_store().verify().ok

    def test_two_commands_on_one_file_unwind_to_the_first_before(self, workspace, store_dir):
        target = workspace / "same.txt"
        target.write_bytes(b"v0")
        _run(workspace, _py("open('same.txt','w').write('v1')"), turn="chain-1")
        _run(workspace, _py("open('same.txt','w').write('v2')"), turn="chain-1")

        result = _rollback("chain-1", workspace)
        assert result.ok, result.response_text
        assert target.read_bytes() == b"v0"

    def test_a_change_between_two_recorded_commands_is_never_erased(self, workspace, store_dir):
        target = workspace / "same.txt"
        target.write_bytes(b"v0")
        _run(workspace, _py("open('same.txt','w').write('v1')"), turn="chain-2")
        target.write_bytes(b"my own edit")  # not recorded: the user's, between the two commands
        _run(workspace, _py("open('same.txt','a').write(' + more')"), turn="chain-2")

        result = _rollback("chain-2", workspace)
        assert not result.ok
        assert result.status == "blackbox_rollback_conflict"
        reasons = {c["reason"] for c in result.details["blackbox_rollback"]["conflicts"]}
        assert "changed_between_effects" in reasons
        assert target.read_bytes() == b"my own edit + more", "a refused rollback writes nothing"


class TestMixedTurns:
    def _mixed_turn(self, workspace: Path, turn: str) -> None:
        (workspace / "keep.txt").write_bytes(b"original\n")
        _write(workspace, "notes.md", "# notes\n", turn=turn)
        _run(workspace, _py("open('build.txt','w').write('built'); open('keep.txt','w').write('changed\\n')"), turn=turn)

    def test_file_write_and_command_run_roll_back_together(self, workspace, store_dir):
        self._mixed_turn(workspace, "mixed-1")
        result = _rollback("mixed-1", workspace)
        assert result.ok, result.response_text
        assert not (workspace / "notes.md").exists()
        assert not (workspace / "build.txt").exists()
        assert (workspace / "keep.txt").read_bytes() == b"original\n"
        assert len(_kinds("rollback_committed")) == 1

    def test_a_later_edit_refuses_the_whole_turn(self, workspace, store_dir):
        self._mixed_turn(workspace, "mixed-2")
        (workspace / "keep.txt").write_bytes(b"edited after the turn\n")

        result = _rollback("mixed-2", workspace)
        assert not result.ok
        assert result.status == "blackbox_rollback_conflict"
        assert [(c["path"], c["reason"]) for c in result.details["blackbox_rollback"]["conflicts"]] == [
            ("keep.txt", "postimage_diverged")
        ]
        assert (workspace / "notes.md").exists(), "the file effect is not undone either: all or nothing"
        assert (workspace / "build.txt").exists()
        assert (workspace / "keep.txt").read_bytes() == b"edited after the turn\n"
        assert not _kinds("rollback_committed")

    def test_one_file_changed_by_a_file_tool_and_a_command_is_refused_whole(self, workspace, store_dir):
        _write(workspace, "notes.md", "# notes\n", turn="mixed-3")
        _run(workspace, _py("open('notes.md','a').write('more\\n')"), turn="mixed-3")

        result = _rollback("mixed-3", workspace)
        assert not result.ok
        assert result.details["blackbox_rollback"]["reason"] == "same_path_in_file_and_command_effects"
        assert (workspace / "notes.md").read_bytes() == b"# notes\nmore\n"

    def test_irreversible_local_effect_is_named_never_claimed(self, workspace, store_dir):
        from core.blackbox.coverage.recorder import recorded_capability_mutation
        from core.runtime_execution_tools import RuntimeExecutionResult

        _write(workspace, "notes.md", "# notes\n", turn="mixed-4")
        exported = workspace / "export.png"

        def export():
            exported.write_bytes(b"png")
            return RuntimeExecutionResult(handled=True, ok=True, status="executed", response_text="ok",
                                          details={"path": str(exported)})

        assert recorded_capability_mutation("media.export", {}, handler=export, source_context=ctx(workspace, turn="mixed-4"),
                                            workspace_root=workspace).ok
        result = _rollback("mixed-4", workspace)
        assert result.ok, result.response_text
        assert not (workspace / "notes.md").exists()
        assert exported.read_bytes() == b"png", "an irreversible effect is left as it is"
        not_undone = result.details["blackbox_rollback"]["not_undone"]
        assert len(not_undone) == 1 and "media.export" in not_undone[0]
        assert "cannot be undone" in result.response_text

        def export_only():
            (workspace / "second.png").write_bytes(b"png")
            return RuntimeExecutionResult(handled=True, ok=True, status="executed", response_text="ok",
                                          details={"path": str(workspace / "second.png")})

        assert recorded_capability_mutation("media.export", {}, handler=export_only,
                                            source_context=ctx(workspace, turn="post-only"), workspace_root=workspace).ok
        refused = _rollback("post-only", workspace)
        assert not refused.ok
        assert refused.status == "blackbox_not_reversible"
        assert (workspace / "second.png").exists()


class TestDiskSafety:
    def test_parent_swapped_for_a_symlink_out_of_the_workspace_is_refused(self, workspace, store_dir, tmp_path):
        _run(workspace, _py("import os; os.makedirs('sub'); open('sub/made.txt','w').write('made')"), turn="swap-1")
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "made.txt").write_bytes(b"made")  # same bytes: only the path law can tell
        (workspace / "sub" / "made.txt").unlink()
        (workspace / "sub").rmdir()
        (workspace / "sub").symlink_to(outside, target_is_directory=True)

        result = _rollback("swap-1", workspace)
        assert not result.ok
        reasons = {c["reason"] for c in result.details["blackbox_rollback"]["conflicts"]}
        assert reasons == {"parent_directory_escapes_workspace"}
        assert (outside / "made.txt").read_bytes() == b"made", "nothing outside the workspace is removed"

    def test_git_metadata_drift_is_refused(self, workspace, store_dir):
        (workspace / ".git").mkdir()
        (workspace / ".git" / "description").write_bytes(b"repo\n")
        # `python3 -c` names no file operand, so the command-line parser declares nothing and the
        # scan is what catches the write.
        _run(workspace, _py("open('.git/description','w').write('changed'); open('a.txt','w').write('x')"), turn="git-1")

        result = _rollback("git-1", workspace)
        assert not result.ok
        conflicts = result.details["blackbox_rollback"]["conflicts"]
        assert (".git/description", "repository_metadata") in {(c["path"], c["reason"]) for c in conflicts}
        assert (workspace / ".git" / "description").read_bytes() == b"changed"
        assert (workspace / "a.txt").exists(), "all or nothing: the ordinary file is not undone either"

    def test_effects_of_the_same_turn_under_another_root_are_out_of_reach(self, workspace, store_dir, tmp_path):
        other = tmp_path / "other-ws"
        other.mkdir()
        _run(workspace, _py("open('a.txt','w').write('a')"), turn="two-roots")
        _run(other, _py("open('b.txt','w').write('b')"), turn="two-roots")

        result = _rollback("two-roots", workspace)
        assert result.ok, result.response_text
        assert not (workspace / "a.txt").exists()
        assert (other / "b.txt").read_bytes() == b"b", "the token is bound to one root"


class TestCrashClosure:
    def _crash_mid_command(self, workspace: Path, turn: str):
        """A process that died between the command's INTENDED and its TERMINAL: the intended entry
        is journaled exactly as the recorder writes it, then the command's writes happen."""
        from core.blackbox.coverage.recorder import _opaque_row
        from core.blackbox.coverage.scan import scan_workspace
        from core.blackbox.identity import identity_from_context
        from core.blackbox.store import default_store, utcnow

        store = default_store()
        (workspace / "seed.txt").write_bytes(b"before")
        before = scan_workspace(workspace, store=store)
        store.append(
            {
                "schema": "blackbox_effect_v1",
                **identity_from_context(ctx(workspace, turn=turn)).to_dict(),
                "root": str(workspace.resolve()),
                "intent": "sandbox.run_command",
                "kind": "coverage_scan_intended",
                "effect_id": f"effect-crash-{turn}",
                "manifest_sha256": before.manifest_sha256,
                "files": {rel: _opaque_row(row, store.cas_keyring) for rel, row in before.files.items()},
                "files_scanned": before.files_scanned,
                "bytes_captured": before.bytes_captured,
                "degraded": before.degraded,
                "started_at": utcnow(),
            }
        )
        (workspace / "seed.txt").write_bytes(b"after the crash point")
        (workspace / "new.txt").write_bytes(b"appeared")
        return store

    def test_store_recovery_closes_an_open_command_truthfully(self, workspace, store_dir):
        store = self._crash_mid_command(workspace, "crash-a")
        assert store.status()["command_effects_open"] == ["effect-crash-crash-a"]
        store.ensure_recovered()
        assert store.status()["command_effects_open"] == []
        terminal = [e for e in store.entries() if e.get("kind") == "coverage_scan_terminal"]
        assert len(terminal) == 1 and terminal[0]["outcome"] == "unknown_crashed"

    def test_a_crashed_command_is_rolled_back_from_its_preimages(self, workspace, store_dir):
        self._crash_mid_command(workspace, "crash-b")
        result = _rollback("crash-b", workspace)
        assert result.ok, result.response_text
        assert (workspace / "seed.txt").read_bytes() == b"before"
        assert not (workspace / "new.txt").exists()


class TestForgetReachesCommandCopies:
    def test_a_chat_forget_drops_its_command_copies_and_the_turn_then_refuses(self, workspace, store_dir, tmp_path):
        """The forget seam (``core.memory.copies`` calls exactly these two store methods): a command
        run's undo copies carrying the words go, the turn refuses restoration afterwards, and the
        words never come back to disk through a rollback. Another chat's copies stay."""
        diary = workspace / "diary.txt"
        diary.write_bytes(b"my bank pin is 4321\n")
        _run(workspace, _py("open('diary.txt','w').write('cleaned')"), turn="forget-1", session="chat-a")
        other = tmp_path / "other-ws"
        other.mkdir()
        (other / "note.txt").write_bytes(b"pin 4321 for the other chat\n")
        _run(other, _py("open('note.txt','a').write('more')"), turn="forget-other", session="chat-b")

        from core.blackbox.store import default_store

        store = default_store()

        def carries(data: bytes) -> bool:
            return b"4321" in data

        assert store.copies_carrying(carries, sessions={"chat-a", ""}) == ["forget-1"]
        assert store.forget_copies(carries, sessions={"chat-a", ""}) >= 1
        assert store.copies_carrying(carries, sessions={"chat-a", ""}) == []
        assert store.copies_carrying(carries, sessions={"chat-b"}) == ["forget-other"]
        assert store.verify().ok

        result = _rollback("forget-1", workspace)
        assert not result.ok
        assert result.status == "blackbox_rollback_unrecoverable"
        assert result.details["blackbox_rollback"]["reason"] == "blobs_pruned"
        assert diary.read_bytes() == b"cleaned", "forgotten words never return through a rollback"
