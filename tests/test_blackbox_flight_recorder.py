"""Blackbox: a byte-exact flight recorder for workspace file effects.

Every test drives the REAL seam -- ``execute_runtime_tool`` / ``execute_authorized_runtime_tool``
-- never the recorder's functions directly, except where a test forges or corrupts the store on
purpose (tampering, same-user forgery) and says so.

RED first (base 137f90c7): the repository advertises a Blackbox and ships no implementation; the
only mutation record is a text-only JSON list rewritten in full on every write, with no chain,
no identity beyond the session, no crash ordering, and no byte-exact capture.

Packs (each a class):

    Coverage     every authorized workspace create/write/replace/patch/mkdir journals
                 identity, canonical root/path, operation, before/after existence+bytes+sha256,
                 intended change, outcome, timestamps, chain linkage
    CrashPoints  intended-before-write, write-before-terminal, journal unavailable, terminal
                 unrecorded -- the journal never claims success before terminal state exists
    Conflict     rollback compares current bytes with the recorded after hash and refuses typed
    Escape       symlink leaf, symlinked parent, hardlink, traversal in a forged entry
    Tampering    edited line, deleted tail, deleted HEAD, corrupted blob, model-text forgery
    Rollback     all-or-nothing, idempotent, interruption-recoverable, receipted through the
                 authorized boundary
    Authority    model arguments cannot authorize; tokens are bound, single-use, and the
                 permission authority still decides (MANUAL -> pending_approval)
    Retention    bounded capture, observable pruning, pruned turns refuse recovery
"""
from __future__ import annotations

import hashlib
import itertools
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

from core.authorized_tool_execution import execute_authorized_runtime_tool
from core.mode_permission_policy import (
    PermissionAction,
    grant_internal_authority,
    reset_mode_permission_state,
    set_active_mode,
)
from core.runtime_execution_tools import execute_runtime_tool

REPO_ROOT = Path(__file__).resolve().parents[1]


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------- fixtures ----


@pytest.fixture(autouse=True)
def _permission_state():
    reset_mode_permission_state()
    yield
    reset_mode_permission_state()


@pytest.fixture
def store_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
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


def _entries(store_dir: Path) -> list[dict[str, Any]]:
    from storage.blackbox.journal import Journal

    return [dict(item) for item in Journal(store_dir).entries()]


def _effects_for(entries: list[dict[str, Any]], turn_id: str) -> dict[str, dict[str, Any]]:
    """effect_id -> {"intended": entry, "terminal": entry} for one turn."""
    grouped: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if entry.get("turn_id") != turn_id:
            continue
        kind = entry.get("kind")
        if kind in {"effect_intended", "effect_terminal"}:
            grouped.setdefault(str(entry["effect_id"]), {})[kind.split("_", 1)[1]] = entry
    return grouped


def _ctx(workspace: Path, session: str = "sess-1", mode: str = "auto", **extra: Any) -> dict[str, Any]:
    context: dict[str, Any] = {"workspace": str(workspace), "session_id": session, "surface": "api"}
    if mode:
        set_active_mode(session, mode)
        context["operating_mode"] = mode
    context.update(extra)
    return context


def _write_authority(workspace: Path, session: str) -> str:
    """The bounded authority an internal caller holds for a workspace write. Auto mode PROMPTS for
    overwrites and deletes (that is the real matrix, and the manual-mode test below pins it), so a
    test that overwrites must hold an explicit, expiring, workspace-bound scope like any background
    job would -- never a widened mode."""
    return grant_internal_authority(
        label="blackbox-test-write",
        actions=[PermissionAction.CREATE_FILES, PermissionAction.MODIFY_FILES, PermissionAction.OVERWRITE_EXISTING_FILES],
        intents=["workspace.write_file", "workspace.replace_in_file", "workspace.apply_unified_diff", "workspace.ensure_directory"],
        workspace_root=str(workspace.resolve()),
        duration_seconds=300,
    )


def _rollback_authority(workspace: Path) -> str:
    return grant_internal_authority(
        label="blackbox-test-operator-rollback",
        actions=[PermissionAction.DELETE_FILES, PermissionAction.OVERWRITE_EXISTING_FILES, PermissionAction.MODIFY_FILES],
        intents=["workspace.rollback_last_change"],
        workspace_root=str(workspace.resolve()),
        duration_seconds=300,
    )


def _write(workspace: Path, path: str, content: str, *, session: str = "sess-1", **extra: Any):
    result = execute_authorized_runtime_tool(
        "workspace.write_file",
        {"path": path, "content": content},
        task_id="task-write",
        source_context=_ctx(workspace, session, **extra),
        authority_token=_write_authority(workspace, session),
    )
    assert result is not None
    return result


def _turn_of(result) -> str:
    turn_id = str((result.details.get("blackbox") or {}).get("turn_id") or "")
    assert turn_id, result.details
    return turn_id


def _operator_rollback(turn_id: str, workspace: Path, *, session: str = "sess-1", mode: str = "auto", authority: bool = True):
    from core.blackbox.operator import rollback_turn

    set_active_mode(session, mode)
    return rollback_turn(
        turn_id,
        workspace_root=workspace,
        session_id=session,
        operator="test-operator",
        source_context={"operating_mode": mode, "surface": "api"},
        authority_token=_rollback_authority(workspace) if authority else None,
    )


def _run_in_subprocess(script: str, *, env: dict[str, str]) -> subprocess.CompletedProcess:
    merged = dict(os.environ)
    merged.update(env)
    merged["PYTHONPATH"] = str(REPO_ROOT)
    merged.pop("PYTEST_CURRENT_TEST", None)
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)],
        cwd=str(REPO_ROOT),
        env=merged,
        capture_output=True,
        text=True,
        timeout=180,
    )


def _subprocess_env(store_dir: Path, workspace: Path) -> dict[str, str]:
    """A child process gets its OWN runtime home: a live drive that boots against the checkout's
    default home mints a key store there and reds every later suite run."""
    home = store_dir.parent / "child-home"
    home.mkdir(parents=True, exist_ok=True)
    return {
        "VOOL_BLACKBOX_DIR": str(store_dir),
        "WS": str(workspace),
        "VOOL_HOME": str(home),
        "VOOL_KEY_STORAGE_MODE": "file",
        "VOOL_KEY_PASSPHRASE": "blackbox-test-child",
        "VOOL_REGISTER_INSTALLED_OLLAMA_MODELS": "0",
    }


_SUBPROCESS_PRELUDE = """
import os, sys
sys.path.insert(0, os.environ["PYTHONPATH"])
from core.mode_permission_policy import set_active_mode
from core.authorized_tool_execution import execute_authorized_runtime_tool
from core.mode_permission_policy import PermissionAction, grant_internal_authority
set_active_mode("crash-sess", "auto")
ctx = {"workspace": os.environ["WS"], "session_id": "crash-sess", "operating_mode": "auto", "surface": "api"}
token = grant_internal_authority(
    label="blackbox-test-child", duration_seconds=300, workspace_root=os.environ["WS"],
    actions=[PermissionAction.CREATE_FILES, PermissionAction.MODIFY_FILES, PermissionAction.OVERWRITE_EXISTING_FILES,
             PermissionAction.DELETE_FILES],
)
"""


# ---------------------------------------------------------------------------- coverage ----


class TestCoverage:
    def test_create_journals_identity_paths_bytes_and_outcome(self, store_dir: Path, workspace: Path) -> None:
        result = _write(workspace, "notes/a.txt", "hello\n")
        assert result.ok, result.response_text
        turn_id = _turn_of(result)
        effects = _effects_for(_entries(store_dir), turn_id)
        assert len(effects) == 1
        (effect,) = effects.values()
        intended, terminal = effect["intended"], effect["terminal"]
        assert intended["session_id"] == "sess-1"
        assert intended["intent"] == "workspace.write_file"
        assert intended["operation"] == "create"
        assert intended["root"] == str(workspace.resolve())
        assert intended["path"] == "notes/a.txt"
        assert intended["before"] == {
            "exists": False, "kind": "missing", "size": 0, "sha256": "", "mode": None,
            "nlink": 0, "blob": None, "bytes_captured": True, "capture_limit_exceeded": False,
        }
        assert intended["intended"]["after_sha256"] == _sha(b"hello\n")
        assert intended["intended"]["after_exists"] is True
        assert terminal["outcome"] == "succeeded"
        assert terminal["after"]["exists"] is True
        assert terminal["after"]["sha256"] == _sha(b"hello\n")
        assert terminal["after"]["size"] == 6
        assert terminal["started_at"] and terminal["completed_at"] >= terminal["started_at"]
        assert terminal["prev"] == intended["entry_hash"]
        assert intended["authority"]["effect"] == "allow"
        assert intended["attempt_id"] == "task-write"

    def test_overwrite_captures_before_bytes_exactly_including_binary(self, store_dir: Path, workspace: Path) -> None:
        raw = b"\x00\xff\xfe binary \x80\x81 not utf-8 \n\r\n"
        (workspace / "blob.bin").write_bytes(raw)
        os.chmod(workspace / "blob.bin", 0o640)
        result = _write(workspace, "blob.bin", "text now\n")
        assert result.ok
        (effect,) = _effects_for(_entries(store_dir), _turn_of(result)).values()
        before = effect["intended"]["before"]
        assert before["exists"] and before["kind"] == "file"
        assert before["sha256"] == _sha(raw)
        assert before["mode"] == 0o640
        # 2026-09-02 amendment: the blob ref is the OPAQUE keyed address, never the raw digest,
        # and the bytes behind it are SEALED -- readable only through the keyed store.
        assert before["blob"] != _sha(raw)
        from core.blackbox.store import default_store

        assert default_store().blobs.get(before["blob"]) == raw
        assert effect["intended"]["operation"] == "write"

    def test_replace_patch_and_mkdir_are_journaled_with_their_operations(self, store_dir: Path, workspace: Path) -> None:
        (workspace / "r.txt").write_text("alpha beta\n", encoding="utf-8")
        (workspace / "p.txt").write_text("line1\nline2\n", encoding="utf-8")
        ctx = _ctx(workspace)
        replaced = execute_runtime_tool(
            "workspace.replace_in_file", {"path": "r.txt", "old_text": "beta", "new_text": "gamma"}, source_context=ctx
        )
        assert replaced is not None and replaced.ok, replaced.response_text
        patch = "--- a/p.txt\n+++ b/p.txt\n@@ -1,2 +1,2 @@\n line1\n-line2\n+line2 patched\n"
        patched = execute_runtime_tool("workspace.apply_unified_diff", {"patch": patch}, source_context=ctx)
        assert patched is not None and patched.ok, patched.response_text
        made = execute_runtime_tool("workspace.ensure_directory", {"path": "newdir/inner"}, source_context=ctx)
        assert made is not None and made.ok, made.response_text

        entries = _entries(store_dir)
        by_op = {e["operation"]: e for e in entries if e.get("kind") == "effect_intended"}
        assert set(by_op) == {"replace", "patch", "mkdir"}
        assert by_op["replace"]["path"] == "r.txt"
        assert by_op["replace"]["before"]["sha256"] == _sha(b"alpha beta\n")
        assert by_op["patch"]["path"] == "p.txt"
        assert by_op["mkdir"]["path"] == "newdir/inner"
        assert by_op["mkdir"]["before"]["exists"] is False
        terminals = {e["effect_id"]: e for e in entries if e.get("kind") == "effect_terminal"}
        assert terminals[by_op["replace"]["effect_id"]]["after"]["sha256"] == _sha(b"alpha gamma\n")
        assert terminals[by_op["patch"]["effect_id"]]["after"]["sha256"] == _sha(b"line1\nline2 patched\n")
        assert terminals[by_op["mkdir"]["effect_id"]]["after"]["kind"] == "dir"
        for terminal in terminals.values():
            assert terminal["outcome"] == "succeeded"

    def test_refused_write_is_journaled_as_refused_and_bytes_unchanged(self, store_dir: Path, workspace: Path) -> None:
        (workspace / "s.txt").write_text("v1", encoding="utf-8")
        result = execute_runtime_tool(
            "workspace.write_file",
            {"path": "s.txt", "content": "v2", "expected_hash": "0" * 64},
            source_context=_ctx(workspace),
        )
        assert result is not None and result.status == "stale_base"
        (effect,) = _effects_for(_entries(store_dir), _turn_of(result)).values()
        assert effect["terminal"]["outcome"] == "refused"
        assert effect["terminal"]["status"] == "stale_base"
        assert effect["terminal"]["after"]["sha256"] == effect["intended"]["before"]["sha256"] == _sha(b"v1")

    def test_handler_exception_is_journaled_as_failed_with_disk_observed(
        self, store_dir: Path, workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import core.runtime_execution_tools as ret

        def _boom(*_a: Any, **_k: Any) -> None:
            raise OSError("disk exploded")

        monkeypatch.setattr(ret, "atomic_write_text", _boom)
        result = execute_runtime_tool("workspace.write_file", {"path": "x.txt", "content": "x"}, source_context=_ctx(workspace))
        assert result is not None and not result.ok
        entries = _entries(store_dir)
        terminal = [e for e in entries if e.get("kind") == "effect_terminal"][-1]
        assert terminal["outcome"] == "failed"
        assert terminal["exception"].startswith("OSError")
        assert terminal["after"]["exists"] is False

    def test_chain_links_every_entry_and_verifies(self, store_dir: Path, workspace: Path) -> None:
        for index in range(3):
            assert _write(workspace, f"f{index}.txt", f"content {index}").ok
        entries = _entries(store_dir)
        assert len(entries) >= 6
        assert entries[0]["prev"] == ""
        for previous, current in itertools.pairwise(entries):
            assert current["prev"] == previous["entry_hash"]
            assert current["seq"] == previous["seq"] + 1
        from storage.blackbox.journal import Journal

        report = Journal(store_dir).verify()
        assert report.ok, report
        assert report.entries == len(entries)

    def test_direct_dispatch_without_a_turn_still_gets_identity(self, store_dir: Path, workspace: Path) -> None:
        result = execute_runtime_tool("workspace.write_file", {"path": "d.txt", "content": "d"}, source_context={"workspace": str(workspace)})
        assert result is not None and result.ok
        turn_id = _turn_of(result)
        assert turn_id.startswith("direct:")
        (effect,) = _effects_for(_entries(store_dir), turn_id).values()
        assert effect["intended"]["route"] == "runtime_tool.direct_call"
        assert effect["intended"]["authority"]["effect"] == "none"


# ------------------------------------------------------------------------- crash points ----


class TestCrashPoints:
    def _crash_env(self, store_dir: Path, workspace: Path) -> dict[str, str]:
        return _subprocess_env(store_dir, workspace)

    def test_process_death_between_intended_and_write_recovers_as_unknown_matching_before(
        self, store_dir: Path, workspace: Path
    ) -> None:
        (workspace / "c.txt").write_text("before", encoding="utf-8")
        script = _SUBPROCESS_PRELUDE + textwrap.dedent("""
            import core.runtime_execution_tools as ret
            def _die(*a, **k):
                os._exit(9)   # the process dies after INTENDED was durably appended, before any byte moved
            ret.atomic_write_text = _die
            execute_authorized_runtime_tool("workspace.write_file", {"path": "c.txt", "content": "after"}, task_id="t", source_context=ctx, authority_token=token)
        """)
        completed = _run_in_subprocess(script, env=self._crash_env(store_dir, workspace))
        assert completed.returncode == 9, completed.stderr
        assert (workspace / "c.txt").read_text(encoding="utf-8") == "before"

        from core.blackbox.store import default_store

        store = default_store()
        recovered = store.recover_open_effects()
        assert len(recovered) == 1
        entries = _entries(store_dir)
        terminal = [e for e in entries if e.get("kind") == "effect_terminal"][-1]
        assert terminal["outcome"] == "unknown_crashed"
        assert terminal["recovery"]["reconciled"] == "matches_before"
        assert terminal["after"]["sha256"] == _sha(b"before")
        turn_id = terminal["turn_id"]
        rolled = _operator_rollback(turn_id, workspace)
        assert rolled.ok, rolled.response_text
        assert rolled.details["blackbox_rollback"]["restored_paths"] == []
        assert (workspace / "c.txt").read_text(encoding="utf-8") == "before"

    def test_process_death_after_write_before_terminal_recovers_and_rolls_back_exactly(
        self, store_dir: Path, workspace: Path
    ) -> None:
        (workspace / "c.txt").write_bytes(b"before\x00bytes")
        script = _SUBPROCESS_PRELUDE + textwrap.dedent("""
            from storage.blackbox import journal as journal_module
            real_append = journal_module.Journal.append
            def _append(self, entry):
                if entry.get("kind") == "effect_terminal":
                    os._exit(7)   # bytes landed on disk; the process dies before TERMINAL exists
                return real_append(self, entry)
            journal_module.Journal.append = _append
            execute_authorized_runtime_tool("workspace.write_file", {"path": "c.txt", "content": "after"}, task_id="t", source_context=ctx, authority_token=token)
        """)
        completed = _run_in_subprocess(script, env=self._crash_env(store_dir, workspace))
        assert completed.returncode == 7, completed.stderr
        assert (workspace / "c.txt").read_bytes() == b"after"

        from core.blackbox.store import default_store

        recovered = default_store().recover_open_effects()
        assert len(recovered) == 1
        terminal = [e for e in _entries(store_dir) if e.get("kind") == "effect_terminal"][-1]
        assert terminal["outcome"] == "unknown_crashed"
        assert terminal["recovery"]["reconciled"] == "matches_intended_after"
        rolled = _operator_rollback(terminal["turn_id"], workspace)
        assert rolled.ok, rolled.response_text
        assert (workspace / "c.txt").read_bytes() == b"before\x00bytes"

    def test_journal_unavailable_means_the_effect_never_runs(self, store_dir: Path, workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from storage.blackbox import journal as journal_module

        def _refuse(self: Any, entry: dict[str, Any]) -> Any:
            raise OSError("journal disk full")

        monkeypatch.setattr(journal_module.Journal, "append", _refuse)
        result = execute_runtime_tool("workspace.write_file", {"path": "never.txt", "content": "x"}, source_context=_ctx(workspace))
        assert result is not None
        assert not result.ok
        assert result.status == "blackbox_unavailable"
        assert not (workspace / "never.txt").exists()

    def test_terminal_append_failure_never_claims_success(self, store_dir: Path, workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from storage.blackbox import journal as journal_module

        real_append = journal_module.Journal.append

        def _fail_terminal(self: Any, entry: dict[str, Any]) -> Any:
            if entry.get("kind") == "effect_terminal":
                raise OSError("journal disk full at terminal")
            return real_append(self, entry)

        monkeypatch.setattr(journal_module.Journal, "append", _fail_terminal)
        result = execute_runtime_tool("workspace.write_file", {"path": "t.txt", "content": "landed"}, source_context=_ctx(workspace))
        assert result is not None
        assert not result.ok
        assert result.status == "blackbox_unrecorded"
        # The truth is stated, not hidden: the bytes DID land and the handler's own result rides along.
        assert (workspace / "t.txt").read_text(encoding="utf-8") == "landed"
        assert result.details["handler_result"]["status"] == "executed"
        assert result.details["blackbox"]["terminal_recorded"] is False


# ----------------------------------------------------------------------------- conflict ----


class TestConflict:
    def test_external_edit_refuses_typed_and_never_overwrites(self, store_dir: Path, workspace: Path) -> None:
        (workspace / "shared.txt").write_text("original", encoding="utf-8")
        turn_id = _turn_of(_write(workspace, "shared.txt", "vool-change"))
        (workspace / "shared.txt").write_text("user edit after", encoding="utf-8")
        rolled = _operator_rollback(turn_id, workspace)
        assert not rolled.ok
        assert rolled.status == "blackbox_rollback_conflict"
        conflicts = rolled.details["blackbox_rollback"]["conflicts"]
        assert conflicts == [
            {
                "path": "shared.txt", "reason": "content_changed",
                "expected_sha256": _sha(b"vool-change"), "current_sha256": _sha(b"user edit after"),
            }
        ]
        assert (workspace / "shared.txt").read_text(encoding="utf-8") == "user edit after"
        refusals = [e for e in _entries(store_dir) if e.get("kind") == "rollback_refused"]
        assert refusals and refusals[-1]["rollback_of_turn"] == turn_id

    def test_a_later_turn_on_the_same_path_blocks_the_earlier_rollback(self, store_dir: Path, workspace: Path) -> None:
        first = _turn_of(_write(workspace, "same.txt", "turn one", session="s-a"))
        second = _turn_of(_write(workspace, "same.txt", "turn two", session="s-b"))
        assert first != second
        rolled = _operator_rollback(first, workspace)
        assert rolled.status == "blackbox_rollback_conflict"
        (conflict,) = rolled.details["blackbox_rollback"]["conflicts"]
        assert conflict["reason"] == "content_changed"
        assert (workspace / "same.txt").read_text(encoding="utf-8") == "turn two"
        # Rolling back the LATER turn is clean and restores turn one's bytes exactly.
        rolled_second = _operator_rollback(second, workspace)
        assert rolled_second.ok, rolled_second.response_text
        assert (workspace / "same.txt").read_text(encoding="utf-8") == "turn one"

    def test_a_create_the_user_already_deleted_is_already_restored(self, store_dir: Path, workspace: Path) -> None:
        turn_id = _turn_of(_write(workspace, "gone.txt", "temp"))
        (workspace / "gone.txt").unlink()
        rolled = _operator_rollback(turn_id, workspace)
        assert rolled.ok, rolled.response_text
        assert rolled.details["blackbox_rollback"]["already_restored_paths"] == ["gone.txt"]
        assert not (workspace / "gone.txt").exists()


# ------------------------------------------------------------------------------- escape ----


class TestEscape:
    def test_symlink_swapped_in_at_the_leaf_is_refused(self, store_dir: Path, workspace: Path, tmp_path: Path) -> None:
        (workspace / "leaf.txt").write_text("orig", encoding="utf-8")
        turn_id = _turn_of(_write(workspace, "leaf.txt", "changed"))
        outside = tmp_path / "outside-target.txt"
        outside.write_text("precious", encoding="utf-8")
        (workspace / "leaf.txt").unlink()
        os.symlink(outside, workspace / "leaf.txt")
        rolled = _operator_rollback(turn_id, workspace)
        assert rolled.status == "blackbox_rollback_conflict"
        assert rolled.details["blackbox_rollback"]["conflicts"][0]["reason"] == "target_replaced_by_symlink"
        assert outside.read_text(encoding="utf-8") == "precious"
        assert (workspace / "leaf.txt").is_symlink()

    def test_parent_directory_replaced_by_symlink_outside_is_refused(self, store_dir: Path, workspace: Path, tmp_path: Path) -> None:
        (workspace / "sub").mkdir()
        (workspace / "sub" / "f.txt").write_text("orig", encoding="utf-8")
        turn_id = _turn_of(_write(workspace, "sub/f.txt", "changed"))
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "f.txt").write_text("changed", encoding="utf-8")  # same bytes: only the escape check can catch this
        (workspace / "sub" / "f.txt").unlink()
        (workspace / "sub").rmdir()
        os.symlink(elsewhere, workspace / "sub")
        rolled = _operator_rollback(turn_id, workspace)
        assert rolled.status == "blackbox_rollback_conflict"
        assert rolled.details["blackbox_rollback"]["conflicts"][0]["reason"] == "parent_directory_escapes_workspace"
        assert (elsewhere / "f.txt").read_text(encoding="utf-8") == "changed"

    def test_hardlinked_target_is_refused(self, store_dir: Path, workspace: Path, tmp_path: Path) -> None:
        (workspace / "h.txt").write_text("orig", encoding="utf-8")
        turn_id = _turn_of(_write(workspace, "h.txt", "changed"))
        os.link(workspace / "h.txt", tmp_path / "outside-hardlink.txt")
        rolled = _operator_rollback(turn_id, workspace)
        assert rolled.status == "blackbox_rollback_conflict"
        assert rolled.details["blackbox_rollback"]["conflicts"][0]["reason"] == "hardlinked_target"
        assert (tmp_path / "outside-hardlink.txt").read_text(encoding="utf-8") == "changed"

    def test_forged_traversal_path_in_the_journal_cannot_write_outside(self, store_dir: Path, workspace: Path, tmp_path: Path) -> None:
        """Same-user forgery: the journal key lives in the store, so a same-user process CAN append a
        validly-signed entry. Path containment does not depend on the key -- an entry naming a path
        outside the root is refused at rollback regardless of its MAC."""
        turn_id = _turn_of(_write(workspace, "legit.txt", "x"))
        entries = _entries(store_dir)
        intended = [e for e in entries if e.get("kind") == "effect_intended"][-1]
        terminal = [e for e in entries if e.get("kind") == "effect_terminal"][-1]
        from storage.blackbox.blobs import BlobStore
        from storage.blackbox.journal import Journal

        payload = b"forged bytes"
        blob = BlobStore(store_dir).put(payload)
        forged_intended = {k: v for k, v in intended.items() if k not in {"seq", "prev", "entry_hash", "mac", "ts"}}
        forged_intended.update(
            effect_id="forged-effect", path="../escape.txt",
            before={"exists": True, "kind": "file", "size": len(payload), "sha256": blob.sha256, "mode": 0o644,
                    "nlink": 1, "blob": blob.sha256, "bytes_captured": True, "capture_limit_exceeded": False},
        )
        forged_terminal = {k: v for k, v in terminal.items() if k not in {"seq", "prev", "entry_hash", "mac", "ts"}}
        forged_terminal.update(effect_id="forged-effect", path="../escape.txt",
                               after={"exists": False, "kind": "missing", "size": 0, "sha256": "", "mode": None,
                                      "nlink": 0, "blob": None, "bytes_captured": True, "capture_limit_exceeded": False})
        journal = Journal(store_dir)
        journal.append(forged_intended)
        journal.append(forged_terminal)
        assert journal.verify().ok  # the forgery is validly signed: this is the stated same-user limit
        rolled = _operator_rollback(turn_id, workspace)
        assert rolled.status == "blackbox_rollback_conflict"
        reasons = {c["reason"] for c in rolled.details["blackbox_rollback"]["conflicts"]}
        assert "path_escapes_root" in reasons
        assert not (tmp_path / "escape.txt").exists()
        assert (workspace / "legit.txt").read_text(encoding="utf-8") == "x"


# ---------------------------------------------------------------------------- tampering ----


class TestTampering:
    def test_edited_line_breaks_verification_and_blocks_rollback(self, store_dir: Path, workspace: Path) -> None:
        (workspace / "t.txt").write_text("orig", encoding="utf-8")
        turn_id = _turn_of(_write(workspace, "t.txt", "changed"))
        journal_path = store_dir / "journal.jsonl"
        lines = journal_path.read_text(encoding="utf-8").splitlines()
        first = json.loads(lines[0])
        first["session_id"] = "someone-else"
        lines[0] = json.dumps(first, sort_keys=True, separators=(",", ":"))
        journal_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        from storage.blackbox.journal import Journal

        report = Journal(store_dir).verify()
        assert not report.ok
        assert report.reason == "mac_mismatch"
        assert report.first_bad_seq == 0
        rolled = _operator_rollback(turn_id, workspace)
        assert rolled.status == "blackbox_journal_integrity"
        assert (workspace / "t.txt").read_text(encoding="utf-8") == "changed"

    def test_deleted_tail_is_detected_by_head(self, store_dir: Path, workspace: Path) -> None:
        _write(workspace, "a.txt", "1")
        _write(workspace, "b.txt", "2")
        journal_path = store_dir / "journal.jsonl"
        lines = journal_path.read_text(encoding="utf-8").splitlines()
        journal_path.write_text("\n".join(lines[:-2]) + "\n", encoding="utf-8")
        from storage.blackbox.journal import Journal

        report = Journal(store_dir).verify()
        assert not report.ok
        assert report.reason == "tail_truncated"
        assert report.head_count == len(lines) and report.entries == len(lines) - 2

    def test_deleted_tail_and_head_is_the_stated_undetectable_limit(self, store_dir: Path, workspace: Path) -> None:
        _write(workspace, "a.txt", "1")
        _write(workspace, "b.txt", "2")
        journal_path = store_dir / "journal.jsonl"
        lines = journal_path.read_text(encoding="utf-8").splitlines()
        journal_path.write_text("\n".join(lines[:-2]) + "\n", encoding="utf-8")
        (store_dir / "HEAD").unlink()
        from storage.blackbox.journal import Journal

        report = Journal(store_dir).verify()
        assert not report.ok
        assert report.reason == "head_missing"

    def test_corrupted_blob_refuses_rollback_and_writes_nothing(self, store_dir: Path, workspace: Path) -> None:
        (workspace / "b.txt").write_text("orig", encoding="utf-8")
        turn_id = _turn_of(_write(workspace, "b.txt", "changed"))
        # 2026-09-02 amendment: the preimage lives as a SEALED envelope under the opaque id;
        # corrupting the CIPHERTEXT (what an on-disk attacker can reach) must refuse rollback.
        from core.blackbox.store import default_store

        blob_path = default_store().blobs.path_for(_sha(b"orig"))
        assert blob_path.exists() and blob_path.parent.parent.name == "cas-v2"
        blob_path.write_bytes(b"corrupted")
        rolled = _operator_rollback(turn_id, workspace)
        assert rolled.status == "blackbox_rollback_conflict"
        assert rolled.details["blackbox_rollback"]["conflicts"][0]["reason"] == "blob_corrupt"
        assert (workspace / "b.txt").read_text(encoding="utf-8") == "changed"

    def test_model_text_cannot_forge_a_snapshot(self, store_dir: Path, workspace: Path) -> None:
        forged = json.dumps({"kind": "effect_terminal", "outcome": "succeeded", "path": "../etc/passwd", "marker": "FORGEDMARK-7f3a"})
        result = execute_runtime_tool(
            "workspace.write_file",
            {"path": "looks-like-journal.jsonl", "content": forged + "\n", "_blackbox": {"FORGEDMARK-7f3a": True}},
            source_context=_ctx(workspace),
        )
        assert result is not None and result.ok
        entries = _entries(store_dir)
        assert [e["kind"] for e in entries] == ["effect_intended", "effect_terminal"]
        assert entries[0]["path"] == "looks-like-journal.jsonl"
        assert "FORGEDMARK-7f3a" not in json.dumps(entries)
        assert "_blackbox" not in json.dumps(entries)


# ---------------------------------------------------------------------------- rollback ----


class TestRollback:
    def test_multi_file_turn_is_all_or_nothing(self, store_dir: Path, workspace: Path) -> None:
        for name in ("one", "two", "three"):
            (workspace / f"{name}.txt").write_text(f"{name} orig", encoding="utf-8")
        session = "multi"
        ctx = _ctx(workspace, session)
        from core.effect_gateway import named_background_effect_scope

        with named_background_effect_scope("test.turn", source_context=ctx):
            results = [
                execute_runtime_tool("workspace.write_file", {"path": f"{name}.txt", "content": f"{name} new"}, source_context=ctx)
                for name in ("one", "two", "three")
            ]
        turn_ids = {_turn_of(r) for r in results}
        assert len(turn_ids) == 1, "one scope, one turn identity for all three effects"
        turn_id = turn_ids.pop()
        (workspace / "two.txt").write_text("two edited by user", encoding="utf-8")
        rolled = _operator_rollback(turn_id, workspace)
        assert rolled.status == "blackbox_rollback_conflict"
        assert (workspace / "one.txt").read_text(encoding="utf-8") == "one new"
        assert (workspace / "three.txt").read_text(encoding="utf-8") == "three new"
        assert (workspace / "two.txt").read_text(encoding="utf-8") == "two edited by user"

    def test_rollback_restores_creates_and_bytes_exactly_and_is_idempotent(self, store_dir: Path, workspace: Path) -> None:
        raw = bytes(range(256)) * 3
        (workspace / "bin.dat").write_bytes(raw)
        os.chmod(workspace / "bin.dat", 0o600)
        ctx = _ctx(workspace, "idem")
        from core.effect_gateway import named_background_effect_scope

        with named_background_effect_scope("test.turn", source_context=ctx):
            wrote = execute_runtime_tool("workspace.write_file", {"path": "bin.dat", "content": "text"}, source_context=ctx)
            created = execute_runtime_tool("workspace.write_file", {"path": "made/new.txt", "content": "new"}, source_context=ctx)
            made = execute_runtime_tool("workspace.ensure_directory", {"path": "empty-dir"}, source_context=ctx)
        assert wrote.ok and created.ok and made.ok
        turn_id = _turn_of(wrote)
        first = _operator_rollback(turn_id, workspace)
        assert first.ok, first.response_text
        assert first.status == "executed"
        assert (workspace / "bin.dat").read_bytes() == raw
        assert (os.stat(workspace / "bin.dat").st_mode & 0o7777) == 0o600
        assert not (workspace / "made" / "new.txt").exists()
        # The parent directory the create brought into being is journaled as `missing_parents`
        # and removed with it: a rollback leaves no empty scaffolding the turn created.
        assert not (workspace / "made").exists()
        assert not (workspace / "empty-dir").exists()
        summary = first.details["blackbox_rollback"]
        assert summary["restored_paths"] == ["bin.dat"]
        assert sorted(summary["removed_paths"]) == ["empty-dir", "made", "made/new.txt"]

        second = _operator_rollback(turn_id, workspace)
        assert second.ok
        assert second.status == "already_rolled_back"
        assert (workspace / "bin.dat").read_bytes() == raw
        committed = [e for e in _entries(store_dir) if e.get("kind") == "rollback_committed"]
        assert len(committed) == 1

    def test_interrupted_rollback_resumes_to_exact_bytes(self, store_dir: Path, workspace: Path) -> None:
        for name in ("a", "b", "c"):
            (workspace / f"{name}.txt").write_text(f"{name} orig", encoding="utf-8")
        ctx = _ctx(workspace, "resume")
        from core.effect_gateway import named_background_effect_scope

        with named_background_effect_scope("test.turn", source_context=ctx):
            results = [
                execute_runtime_tool("workspace.write_file", {"path": f"{name}.txt", "content": f"{name} new"}, source_context=ctx)
                for name in ("a", "b", "c")
            ]
        turn_id = _turn_of(results[0])
        script = _SUBPROCESS_PRELUDE + textwrap.dedent("""
            from core.blackbox import rollback as rollback_module
            real_restore = rollback_module._restore_file_bytes
            calls = {"n": 0}
            def _restore(*a, **k):
                real_restore(*a, **k)
                calls["n"] += 1
                if calls["n"] == 1:
                    os._exit(5)   # one file restored, the process dies mid-rollback
            rollback_module._restore_file_bytes = _restore
            from core.blackbox.operator import rollback_turn
            rollback_turn(os.environ["TURN"], workspace_root=os.environ["WS"], session_id="crash-sess", operator="op",
                          source_context={"operating_mode": "auto", "surface": "api"}, authority_token=token)
        """)
        completed = _run_in_subprocess(script, env={**_subprocess_env(store_dir, workspace), "TURN": turn_id})
        assert completed.returncode == 5, completed.stderr
        texts = {name: (workspace / f"{name}.txt").read_text(encoding="utf-8") for name in ("a", "b", "c")}
        assert sorted(texts.values()) == sorted(["a new", "b new", "c orig"]) or sorted(texts.values()) != ["a new", "b new", "c new"]
        resumed = _operator_rollback(turn_id, workspace)
        assert resumed.ok, resumed.response_text
        for name in ("a", "b", "c"):
            assert (workspace / f"{name}.txt").read_text(encoding="utf-8") == f"{name} orig"
        entries = _entries(store_dir)
        assert len([e for e in entries if e.get("kind") == "rollback_started"]) == 2
        assert len([e for e in entries if e.get("kind") == "rollback_committed"]) == 1
        assert resumed.details["blackbox_rollback"]["resumed"] is True

    def test_rollback_is_itself_receipted_through_the_boundary(self, store_dir: Path, workspace: Path) -> None:
        (workspace / "r.txt").write_text("orig", encoding="utf-8")
        turn_id = _turn_of(_write(workspace, "r.txt", "changed"))
        rolled = _operator_rollback(turn_id, workspace)
        assert rolled.ok
        assert rolled.details["permission"]["effect"] == "allow"
        assert rolled.details["permission"]["route"] == "authorized_execution_boundary"
        entries = _entries(store_dir)
        restore_intended = [e for e in entries if e.get("kind") == "effect_intended" and e.get("operation") == "restore"]
        assert len(restore_intended) == 1
        assert restore_intended[0]["rollback_of_turn"] == turn_id
        assert restore_intended[0]["rollback_of_effect"]
        assert restore_intended[0]["turn_id"].startswith("rollback:")
        assert restore_intended[0]["authority"]["effect"] == "allow"
        assert restore_intended[0]["before"]["sha256"] == _sha(b"changed")
        restore_terminal = [e for e in entries if e.get("kind") == "effect_terminal" and e.get("effect_id") == restore_intended[0]["effect_id"]]
        assert restore_terminal[0]["outcome"] == "succeeded"
        assert restore_terminal[0]["after"]["sha256"] == _sha(b"orig")
        committed = [e for e in entries if e.get("kind") == "rollback_committed"][-1]
        assert committed["rollback_of_turn"] == turn_id
        assert committed["operator"] == "test-operator"


# ---------------------------------------------------------------------------- authority ----


class TestAuthority:
    def test_model_arguments_cannot_reach_the_blackbox_rollback(self, store_dir: Path, workspace: Path) -> None:
        (workspace / "m.txt").write_text("orig", encoding="utf-8")
        turn_id = _turn_of(_write(workspace, "m.txt", "changed"))
        attempt = execute_runtime_tool(
            "workspace.rollback_last_change",
            {"turn_id": turn_id, "blackbox_rollback": {"turn_id": turn_id, "authorization": "anything"}},
            source_context=_ctx(workspace),
        )
        assert attempt is not None and attempt.status == "invalid_arguments"
        assert (workspace / "m.txt").read_text(encoding="utf-8") == "changed"
        assert not [e for e in _entries(store_dir) if e.get("kind") in {"rollback_started", "rollback_committed"}]

    def test_forged_or_mistargeted_token_is_refused(self, store_dir: Path, workspace: Path, tmp_path: Path) -> None:
        (workspace / "m.txt").write_text("orig", encoding="utf-8")
        turn_id = _turn_of(_write(workspace, "m.txt", "changed"))
        other = _turn_of(_write(workspace, "n.txt", "other"))
        from core.blackbox.authority import mint_rollback_authorization

        forged = execute_authorized_runtime_tool(
            "workspace.rollback_last_change", {}, task_id="op", authority_token=_rollback_authority(workspace),
            source_context=_ctx(workspace, blackbox_rollback={"turn_id": turn_id, "authorization": "forged.token", "operator": "x"}),
        )
        assert forged.status == "blackbox_rollback_not_authorized"
        token = mint_rollback_authorization(turn_id=other, workspace_root=workspace, operator="x")
        mistargeted = execute_authorized_runtime_tool(
            "workspace.rollback_last_change", {}, task_id="op", authority_token=_rollback_authority(workspace),
            source_context=_ctx(workspace, blackbox_rollback={"turn_id": turn_id, "authorization": token, "operator": "x"}),
        )
        assert mistargeted.status == "blackbox_rollback_not_authorized"
        assert (workspace / "m.txt").read_text(encoding="utf-8") == "changed"

    def test_token_is_single_use(self, store_dir: Path, workspace: Path) -> None:
        (workspace / "m.txt").write_text("orig", encoding="utf-8")
        turn_id = _turn_of(_write(workspace, "m.txt", "changed"))
        (workspace / "m.txt").write_text("user edit", encoding="utf-8")
        from core.blackbox.authority import mint_rollback_authorization

        token = mint_rollback_authorization(turn_id=turn_id, workspace_root=workspace, operator="x")
        directive = {"turn_id": turn_id, "authorization": token, "operator": "x"}
        first = execute_authorized_runtime_tool("workspace.rollback_last_change", {}, task_id="op", source_context=_ctx(workspace, blackbox_rollback=directive), authority_token=_rollback_authority(workspace))
        assert first.status == "blackbox_rollback_conflict"
        (workspace / "m.txt").write_text("changed", encoding="utf-8")  # the conflict is gone; the token is still spent
        second = execute_authorized_runtime_tool("workspace.rollback_last_change", {}, task_id="op", source_context=_ctx(workspace, blackbox_rollback=directive), authority_token=_rollback_authority(workspace))
        assert second.status == "blackbox_rollback_not_authorized"
        assert (workspace / "m.txt").read_text(encoding="utf-8") == "changed"

    def test_permission_authority_still_decides_without_a_bounded_scope(self, store_dir: Path, workspace: Path) -> None:
        """A rollback is classified as a delete-class effect. Manual prompts; so does Auto (the
        real matrix prompts for DELETE_FILES). Only an explicit, bounded, workspace-bound authority
        executes -- and the recorded decision names it."""
        (workspace / "m.txt").write_text("orig", encoding="utf-8")
        turn_id = _turn_of(_write(workspace, "m.txt", "changed"))
        pending = _operator_rollback(turn_id, workspace, mode="manual", authority=False)
        assert pending.status == "pending_approval"
        assert (workspace / "m.txt").read_text(encoding="utf-8") == "changed"
        pending_auto = _operator_rollback(turn_id, workspace, mode="auto", authority=False)
        assert pending_auto.status == "pending_approval"
        assert (workspace / "m.txt").read_text(encoding="utf-8") == "changed"
        assert not [e for e in _entries(store_dir) if e.get("kind") in {"rollback_started", "rollback_committed"}]
        allowed = _operator_rollback(turn_id, workspace, mode="auto")
        assert allowed.ok, allowed.response_text
        assert allowed.details["permission"]["effect"] == "allow"
        assert (workspace / "m.txt").read_text(encoding="utf-8") == "orig"


# ---------------------------------------------------------------------------- retention ----


class TestRetention:
    def test_pruning_is_observable_and_pruned_turns_refuse_recovery(self, store_dir: Path, workspace: Path) -> None:
        (workspace / "old.txt").write_text("old orig", encoding="utf-8")
        (workspace / "new.txt").write_text("new orig", encoding="utf-8")
        old_turn = _turn_of(_write(workspace, "old.txt", "old changed", session="s-old"))
        new_turn = _turn_of(_write(workspace, "new.txt", "new changed", session="s-new"))
        from core.blackbox.store import default_store

        store = default_store()
        before = store.status()
        assert before["turns_total"] == 2 and before["turns_recoverable"] == 2 and before["turns_pruned"] == 0
        pruned = store.prune(keep_turns=1)
        assert pruned["pruned_turn_ids"] == [old_turn]
        after = store.status()
        assert after["turns_pruned"] == 1 and after["turns_recoverable"] == 1
        assert after["blob_count"] < before["blob_count"]
        assert [e for e in _entries(store_dir) if e.get("kind") == "retention_pruned"][-1]["turn_ids"] == [old_turn]
        refused = _operator_rollback(old_turn, workspace)
        assert refused.status == "blackbox_rollback_unrecoverable"
        assert refused.details["blackbox_rollback"]["reason"] == "blobs_pruned"
        assert (workspace / "old.txt").read_text(encoding="utf-8") == "old changed"
        kept = _operator_rollback(new_turn, workspace)
        assert kept.ok
        assert (workspace / "new.txt").read_text(encoding="utf-8") == "new orig"

    def test_capture_limit_is_stated_not_hidden(self, store_dir: Path, workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VOOL_BLACKBOX_MAX_BLOB_BYTES", "16")
        from core.blackbox import store as store_module

        store_module.reset_default_store()
        (workspace / "big.txt").write_text("x" * 64, encoding="utf-8")
        result = _write(workspace, "big.txt", "small")
        assert result.ok
        (effect,) = _effects_for(_entries(store_dir), _turn_of(result)).values()
        before = effect["intended"]["before"]
        assert before["bytes_captured"] is False and before["capture_limit_exceeded"] is True
        assert before["sha256"] == _sha(b"x" * 64) and before["blob"] is None
        assert result.details["blackbox"]["bytes_captured"] is False
        refused = _operator_rollback(_turn_of(result), workspace)
        assert refused.status == "blackbox_rollback_unrecoverable"
        assert refused.details["blackbox_rollback"]["reason"] == "bytes_not_captured"
        assert (workspace / "big.txt").read_text(encoding="utf-8") == "small"
        assert store_module.default_store().status()["effects_uncaptured"] == 1


# ------------------------------------------------------------------- sabotage regression ----


class TestSabotagePins:
    """Each pin disables ONE guard in-process and shows the specific damage it would allow, so a
    green run of the packs above cannot be a vacuous pass: the guard is what stands between the
    scenario and the loss."""

    def test_without_before_capture_rollback_cannot_restore(self, store_dir: Path, workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from core.blackbox import snapshot as snapshot_module

        real = snapshot_module.snapshot_path

        def _no_bytes(*args: Any, **kwargs: Any):
            observed = real(*args, **kwargs)
            return observed.__class__(**{**observed.__dict__, "blob": None, "bytes_captured": False})

        monkeypatch.setattr(snapshot_module, "snapshot_path", _no_bytes)
        (workspace / "s.txt").write_text("orig", encoding="utf-8")
        turn_id = _turn_of(_write(workspace, "s.txt", "changed"))
        refused = _operator_rollback(turn_id, workspace)
        assert refused.status == "blackbox_rollback_unrecoverable"
        assert (workspace / "s.txt").read_text(encoding="utf-8") == "changed"

    def test_without_conflict_detection_a_user_edit_would_be_destroyed(self, store_dir: Path, workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from core.blackbox import rollback as rollback_module

        (workspace / "s.txt").write_text("orig", encoding="utf-8")
        turn_id = _turn_of(_write(workspace, "s.txt", "changed"))
        (workspace / "s.txt").write_text("user edit", encoding="utf-8")
        monkeypatch.setattr(rollback_module, "_diagnose_conflict", lambda *a, **k: None)
        rolled = _operator_rollback(turn_id, workspace)
        assert rolled.ok
        assert (workspace / "s.txt").read_text(encoding="utf-8") == "orig", "the guard is the only thing protecting the user's edit"

    def test_without_authorization_a_context_directive_alone_would_roll_back(self, store_dir: Path, workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from core.blackbox import authority as authority_module

        (workspace / "s.txt").write_text("orig", encoding="utf-8")
        turn_id = _turn_of(_write(workspace, "s.txt", "changed"))
        monkeypatch.setattr(authority_module, "verify_rollback_authorization", lambda *a, **k: authority_module.AuthorizationVerdict(ok=True, reason="sabotaged", nonce="x"))
        rolled = execute_authorized_runtime_tool(
            "workspace.rollback_last_change", {}, task_id="op", authority_token=_rollback_authority(workspace),
            source_context=_ctx(workspace, blackbox_rollback={"turn_id": turn_id, "authorization": "forged", "operator": "x"}),
        )
        assert rolled.ok
        assert (workspace / "s.txt").read_text(encoding="utf-8") == "orig"
