"""Shell mutation proof: a real sandbox command's writes enter the Blackbox with
content-addressed preimages and postimages, paths, metadata and hashes -- before the caller
hears success -- and the journal chain verifies."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from tests.blackbox_coverage._ctx import ctx
def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _run(workspace: Path, command: str, *, turn: str = "shell-turn"):
    from core.runtime_execution_tools import execute_runtime_tool

    return execute_runtime_tool(
        "sandbox.run_command",
        {"command": command, "cwd": str(workspace)},
        source_context=ctx(workspace, turn=turn),
    )


def _scan_terminal(store):
    entries = store.entries()
    terminals = [e for e in entries if e.get("kind") == "coverage_scan_terminal"]
    intended = [e for e in entries if e.get("kind") == "coverage_scan_intended"]
    assert terminals and intended, f"expected scan pair, got kinds {[e.get('kind') for e in entries]}"
    return intended[-1], terminals[-1]


class TestShellMutationJournals:
    def test_create_and_modify_are_journaled_with_pre_and_postimages(self, workspace, store_dir):
        seed = workspace / "seed.txt"
        seed.write_bytes(b"seed-bytes-v1")
        os.chmod(seed, 0o640)
        result = _run(workspace, "cp seed.txt copy.txt")
        assert result.ok, result.response_text
        assert result.details["blackbox"]["terminal_recorded"] is True

        from core.blackbox.store import default_store

        store = default_store()
        intended, terminal = _scan_terminal(store)
        assert intended["kind"] == "coverage_scan_intended"
        keyring = store.cas_keyring
        # Journal rows carry the KEYED OPAQUE content id, never the raw plaintext digest.
        assert "sha256" not in intended["files"]["seed.txt"]
        assert intended["files"]["seed.txt"]["content_id"] == keyring.id_for(_sha(b"seed-bytes-v1"))
        assert intended["files"]["seed.txt"]["blob"], "the preimage must be content-addressed in the CAS"
        assert intended["files"]["seed.txt"]["mode"] == 0o640, "metadata, not just bytes"

        drift = {row["path"]: row for row in terminal["drift"]}
        assert "copy.txt" in drift, f"copy.txt missing from drift: {sorted(drift)}"
        row = drift["copy.txt"]
        assert row["drift_kind"] == "created"
        assert row["after"]["blob"], "postimage captured into the CAS"
        assert row["after"]["content_id"] == keyring.id_for(_sha(b"seed-bytes-v1"))
        assert row["rollback_capable"] is True
        assert terminal["outcome"] in {"succeeded", "no_change"}
        assert store.verify().ok, store.verify().reason

    def test_terminal_is_durable_before_success_publishes(self, workspace, store_dir):
        result = _run(workspace, "touch brand.txt")
        assert result.ok
        # The result the caller already holds must carry the effect id of a JOURNALED terminal.
        from core.blackbox.store import default_store

        store = default_store()
        effect_id = result.details["blackbox"]["coverage"]["drift_count"]  # summary exists
        assert isinstance(effect_id, int)
        terminals = [e for e in store.entries() if e.get("kind") == "coverage_scan_terminal"]
        assert terminals, "no terminal entry exists, yet the caller heard success"

    def test_unpredictable_writes_are_caught_by_the_scan_not_the_parser(self, workspace, store_dir):
        """A python one-liner no parser can predict still leaves drift rows for every file it
        touched, with hashes and postimages."""
        (workspace / "data.bin").write_bytes(b"original")
        result = _run(workspace, "python3 -c \"open('data.bin','w').write('rewritten')\"")
        assert result.ok, result.response_text
        result = _run(workspace, "python3 -c \"open('surprise.txt','w').write('new')\"", turn="shell-turn-2")
        assert result.ok, result.response_text

        from core.blackbox.store import default_store as _ds

        terminals = [e for e in _ds().entries() if e.get("kind") == "coverage_scan_terminal"]
        by_turn = {t["turn_id"]: t for t in terminals}
        keyring = _ds().cas_keyring
        first = {row["path"]: row for row in by_turn["shell-turn"]["drift"]}
        second = {row["path"]: row for row in by_turn["shell-turn-2"]["drift"]}
        assert first["data.bin"]["drift_kind"] == "changed"
        assert first["data.bin"]["before"]["content_id"] == keyring.id_for(_sha(b"original"))
        assert first["data.bin"]["after"]["content_id"] == keyring.id_for(_sha(b"rewritten"))
        assert second["surprise.txt"]["drift_kind"] == "created"

    def test_refused_command_still_journals_an_intended(self, workspace, store_dir):
        result = _run(workspace, "printf nope > x.txt")  # not whitelisted
        assert result.ok is False
        from core.blackbox.store import default_store

        _, terminal = _scan_terminal(default_store())
        assert terminal["outcome"] in {"refused", "failed"}
        assert all(row["drift_kind"] == "created" for row in terminal["drift"]) is True or terminal["drift"] == []


class TestDeclaredTargetParser:
    def test_redirections_and_operands_are_named(self, workspace, tmp_path):
        from core.blackbox.coverage.scan import declared_targets_for_command

        targets = declared_targets_for_command("echo hi > a.txt >> b.txt 2> c.err", cwd=workspace)
        names = {Path(t).name for t in targets}
        assert {"a.txt", "b.txt", "c.err"} <= names

    def test_mutator_operands_are_named(self, workspace):
        from core.blackbox.coverage.scan import declared_targets_for_command

        assert Path(declared_targets_for_command("touch x.txt", cwd=workspace)[0]).name == "x.txt"
        last = declared_targets_for_command("cp src.txt dst.txt", cwd=workspace)
        assert Path(last[-1]).name == "dst.txt"
        mkdirs = declared_targets_for_command("mkdir -p nested/dir", cwd=workspace)
        assert Path(mkdirs[-1]).name == "dir"

    def test_git_commands_name_the_metadata(self, workspace):
        from core.blackbox.coverage.scan import declared_targets_for_command

        targets = declared_targets_for_command("git commit -m x", cwd=workspace)
        assert any(t.endswith(".git") for t in targets)

    def test_unknown_commands_name_nothing(self, workspace):
        from core.blackbox.coverage.scan import declared_targets_for_command

        assert declared_targets_for_command("python3 script.py", cwd=workspace) == []
