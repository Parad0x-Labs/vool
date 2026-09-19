"""High-risk detection and the no-secrets law: credential/Git/startup/configuration/release-key
paths refuse mutation unless explicitly allowed; secret bytes never enter the journal or any log
line; no whole-machine snapshots; a scan never follows a symlink out of its root."""
from __future__ import annotations

import pytest

from tests.blackbox_coverage._ctx import ctx


class TestHighRiskClassification:
    @pytest.mark.parametrize(
        "path,kind",
        [
            ("/home/u/.ssh/id_rsa", "credentials"),
            ("/home/u/.ssh/authorized_keys", "credentials"),
            ("project/.env", "credentials"),
            ("project/secrets.yaml", "credentials"),
            ("project/server.pem", "credentials"),
            ("/home/u/.aws/credentials", "credentials"),
            ("repo/.git/config", "git_metadata"),
            ("/home/u/.zshrc", "startup"),
            ("/Users/u/Library/LaunchAgents/com.evil.plist", "startup"),
            ("/home/u/.gitconfig", "configuration"),
            ("project/AuthKey_9X2L.p8", "release_keys"),
            ("project/release-key.pub", "release_keys"),
        ],
    )
    def test_paths_classify(self, path, kind):
        from core.blackbox.coverage.highrisk import classify_path

        assert classify_path(path) == kind

    def test_ordinary_paths_do_not(self):
        from core.blackbox.coverage.highrisk import classify_path

        assert classify_path("project/src/main.py") is None
        assert classify_path("project/keyboard.txt") is None


class TestHighRiskGate:
    def _run_shell(self, workspace, command, *, extra=None):
        from core.runtime_execution_tools import execute_runtime_tool

        return execute_runtime_tool(
            "sandbox.run_command",
            {"command": command, "cwd": str(workspace)},
            source_context=ctx(workspace, turn="risk-turn", **(extra or {})),
        )

    def test_declared_high_risk_target_refuses_before_any_byte_moves(self, workspace, store_dir):
        target = workspace / ".env"
        result = self._run_shell(workspace, "touch .env")
        assert result.ok is False
        assert result.status == "blackbox_high_risk_refused"
        assert result.details["executed"] is False
        assert not target.exists(), "the refusal ran the command anyway"
        assert "credentials" in result.response_text

    def test_git_metadata_target_refuses(self, workspace, store_dir):
        (workspace / ".git").mkdir()
        result = self._run_shell(workspace, "touch .git/config")
        assert result.status == "blackbox_high_risk_refused"

    def test_explicit_operator_allow_runs_and_journals_the_risk(self, workspace, store_dir):
        result = self._run_shell(workspace, "touch .env", extra={"_blackbox_high_risk_allowed": True})
        assert result.ok, result.response_text
        from core.blackbox.store import default_store

        terminals = [e for e in default_store().entries() if e.get("kind") == "coverage_scan_terminal"]
        assert terminals
        drift = [row for row in terminals[-1]["drift"] if row["path"] == ".env"]
        assert drift and drift[0]["high_risk"] == "credentials", "the journal names the risk it allowed"


class TestNoSecretsInLogs:
    def test_secret_bytes_never_enter_the_journal_only_the_cas(self, workspace, store_dir):
        secret_marker = b"TOP-SECRET-8f3a91-material"
        (workspace / "creds.env").write_bytes(secret_marker)
        # The operator allowed the credential mutation; what must follow is a journal that holds
        # hashes and metadata -- never the bytes -- while the CAS holds the bytes for rollback.
        from core.runtime_execution_tools import execute_runtime_tool

        result = execute_runtime_tool(
            "sandbox.run_command",
            {"command": "cp creds.env creds.bak", "cwd": str(workspace)},
            source_context=ctx(workspace, turn="secret-turn", _blackbox_high_risk_allowed=True),
        )
        assert result.ok, result.response_text
        assert (workspace / "creds.bak").read_bytes() == secret_marker

        journal_bytes = (store_dir / "journal.jsonl").read_bytes()
        assert secret_marker not in journal_bytes, "secret bytes leaked into the journal"
        head_bytes = (store_dir / "HEAD").read_bytes()
        assert secret_marker not in head_bytes
        # And the preimage IS in the CAS (rollback keeps its bytes) -- SEALED, not plaintext:
        # no file under the store decrypts by inspection, and no path carries the raw digest.
        import hashlib

        from core.blackbox.store import default_store

        store = default_store()
        sha = hashlib.sha256(secret_marker).hexdigest()
        sealed_path = store.blobs.path_for(sha)
        assert sealed_path.exists(), "the CAS must hold the sealed preimage for rollback"
        assert sealed_path.read_bytes() != secret_marker, "the blob is stored in plaintext"
        assert sha not in str(sealed_path), "the raw digest leaked into a filename"
        assert store.blobs.get(sha) == secret_marker, "the right key still decrypts it exactly"

    def test_journal_carries_hashes_not_content_keys(self, workspace, store_dir):
        from core.runtime_execution_tools import execute_runtime_tool

        execute_runtime_tool(
            "sandbox.run_command",
            {"command": "touch plain.txt", "cwd": str(workspace)},
            source_context=ctx(workspace, turn="shape-turn"),
        )
        import json

        for line in (store_dir / "journal.jsonl").read_text().splitlines():
            entry = json.loads(line)
            for key in entry:
                assert key not in {"content", "bytes", "stdout_raw", "preimage", "postimage"}, (
                    f"content-shaped key {key!r} in journal entry kind={entry.get('kind')}"
                )


class TestScanDiscipline:
    def test_no_whole_machine_snapshot_is_possible(self):
        from core.blackbox.coverage.capability import MutationCapability

        capability = MutationCapability(
            tool="x.machine_scan",
            scope="machine",
            effect_class="reversible",
            snapshot_strategy="workspace_scan",
            receipt_lifecycle="intent_then_terminal",
            rollback_support="exact",
            recorder="blackbox.coverage_scan",
        )
        assert any("whole-machine" in problem for problem in capability.problems())

    def test_scan_never_follows_symlinks_out_of_the_root(self, workspace, store_dir, tmp_path):
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "leak.txt").write_bytes(b"should-not-be-scanned")
        (workspace / "link").symlink_to(outside)

        from core.blackbox.coverage.scan import scan_workspace
        from core.blackbox.store import default_store

        result = scan_workspace(workspace, store=default_store())
        assert all(not path.startswith("link") for path in result.files), "the scan crossed a symlink out of the root"

    def test_store_directory_is_never_scan_payload(self, workspace, store_dir):
        from core.blackbox.coverage.scan import scan_workspace
        from core.blackbox.store import default_store

        scan_workspace(workspace, store=default_store())  # journal + HEAD + blobs now exist under store_dir
        result = scan_workspace(workspace, store=default_store())
        assert result.files == {}, f"the store journaled itself: {sorted(result.files)}"
