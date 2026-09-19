from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ops import repo_hygiene_check
from ops.repo_hygiene_check import build_report


def _init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)


class LegacyRootClutterGitAwarenessTests(unittest.TestCase):
    def test_gitignored_untracked_legacy_doc_is_not_repo_clutter(self) -> None:
        # IDENTITY.md is gitignored, so a developer's local copy never reaches a clean
        # checkout; flagging it could only ever fail on a dev machine while CI stayed green.
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve()
            _init_repo(root)
            (root / ".gitignore").write_text("IDENTITY.md\n", encoding="utf-8")
            (root / "IDENTITY.md").write_text("local scratch", encoding="utf-8")

            with mock.patch.object(repo_hygiene_check, "PROJECT_ROOT", root):
                self.assertEqual(repo_hygiene_check._legacy_root_clutter(), [])

    def test_untracked_legacy_doc_that_is_not_ignored_is_still_clutter(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve()
            _init_repo(root)
            (root / "IDENTITY.md").write_text("committed clutter", encoding="utf-8")

            with mock.patch.object(repo_hygiene_check, "PROJECT_ROOT", root):
                self.assertEqual(repo_hygiene_check._legacy_root_clutter(), ["IDENTITY.md"])

    def test_tracked_legacy_doc_is_clutter_even_when_an_ignore_rule_matches(self) -> None:
        # git ignores nothing it already tracks, so neither may this check.
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve()
            _init_repo(root)
            (root / ".gitignore").write_text("IDENTITY.md\n", encoding="utf-8")
            (root / "IDENTITY.md").write_text("tracked clutter", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "-f", "IDENTITY.md"], check=True, capture_output=True)

            with mock.patch.object(repo_hygiene_check, "PROJECT_ROOT", root):
                self.assertEqual(repo_hygiene_check._legacy_root_clutter(), ["IDENTITY.md"])

    def test_root_test_module_is_clutter_outside_a_checkout(self) -> None:
        # Nothing git can answer for is treated as repo content: the check fails closed.
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve()
            (root / "test_stray.py").write_text("", encoding="utf-8")

            with mock.patch.object(repo_hygiene_check, "PROJECT_ROOT", root):
                self.assertEqual(repo_hygiene_check._legacy_root_clutter(), ["test_stray.py"])


class RepoHygieneCheckTests(unittest.TestCase):
    def test_repo_hygiene_report_is_clean(self) -> None:
        report = build_report()
        self.assertEqual(report["status"], "CLEAN")
        self.assertEqual(report["issues"], [])

    def test_repo_key_artifacts_ignore_generated_acceptance_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            key_dir = root / "artifacts" / "acceptance_runs" / "run-1" / "runtime_home" / "data" / "keys"
            key_dir.mkdir(parents=True)
            (key_dir / "node_signing_key.b64").write_text("test-key", encoding="utf-8")

            with mock.patch.object(repo_hygiene_check, "PROJECT_ROOT", root):
                self.assertEqual(repo_hygiene_check._repo_key_artifacts(), [])

    def test_repo_key_artifacts_still_flag_non_generated_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            key_dir = root / "network" / "fixtures"
            key_dir.mkdir(parents=True)
            (key_dir / "node_signing_key.b64").write_text("test-key", encoding="utf-8")

            with mock.patch.object(repo_hygiene_check, "PROJECT_ROOT", root):
                self.assertEqual(repo_hygiene_check._repo_key_artifacts(), ["network/fixtures/node_signing_key.b64"])

    def test_repo_key_artifacts_flag_keyring_metadata_records_too(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            key_dir = root / "network" / "fixtures"
            key_dir.mkdir(parents=True)
            (key_dir / "node_signing_key.keyring.json").write_text("{}", encoding="utf-8")

            with mock.patch.object(repo_hygiene_check, "PROJECT_ROOT", root):
                self.assertEqual(repo_hygiene_check._repo_key_artifacts(), ["network/fixtures/node_signing_key.keyring.json"])

    def test_repo_key_artifacts_ignore_generated_greenloop_runtime_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            key_dir = root / "reports" / "greenloop" / "live_acceptance" / "runtime_home" / "data" / "keys"
            key_dir.mkdir(parents=True)
            (key_dir / "node_signing_key.b64").write_text("test-key", encoding="utf-8")

            with mock.patch.object(repo_hygiene_check, "PROJECT_ROOT", root):
                self.assertEqual(repo_hygiene_check._repo_key_artifacts(), [])

    def test_repo_key_artifacts_ignore_generated_vool_evaluation_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            key_dir = root / ".vool-eval" / "runs" / "run-1" / "runtime-home" / "data" / "keys"
            key_dir.mkdir(parents=True)
            (key_dir / "node_signing_key.b64").write_text("test-key", encoding="utf-8")

            with mock.patch.object(repo_hygiene_check, "PROJECT_ROOT", root):
                self.assertEqual(repo_hygiene_check._repo_key_artifacts(), [])

    def test_repo_key_artifacts_ignore_generated_local_evidence_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            key_dir = root / "local_evidence" / "run-1" / "runtime-home" / "data" / "keys"
            key_dir.mkdir(parents=True)
            (key_dir / "node_signing_key.b64").write_text("test-key", encoding="utf-8")

            with mock.patch.object(repo_hygiene_check, "PROJECT_ROOT", root):
                self.assertEqual(repo_hygiene_check._repo_key_artifacts(), [])

    def test_public_docs_do_not_embed_absolute_local_paths(self) -> None:
        public_docs = [
            "README.md",
            "docs/REPO_MAP.md",
            "CONTRIBUTING.md",
            "docs/README.md",
            "docs/STATUS.md",
            "docs/SYSTEM_SPINE.md",
            "docs/CONTROL_PLANE.md",
        ]
        forbidden_fragments = ("/Users/", "/private/tmp/")

        for relative_path in public_docs:
            content = (repo_hygiene_check.PROJECT_ROOT / relative_path).read_text(encoding="utf-8")
            for fragment in forbidden_fragments:
                self.assertNotIn(fragment, content, msg=f"{relative_path} leaked {fragment}")


if __name__ == "__main__":
    unittest.main()
