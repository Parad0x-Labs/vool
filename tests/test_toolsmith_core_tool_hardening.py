"""TOOLSMITH lane: hardening fixes for the deterministic workspace/repository tools.

Covers, against the REAL dispatcher (`execute_runtime_tool`) wherever the code path allows it,
not a mock of it:

- atomic writes for write_file/replace_in_file/apply_unified_diff (a failed write must never leave
  a partially-written file on disk);
- a `hash` field on read_file, and `before_hash`/`after_hash` on write_file/replace_in_file, so a
  caller can prove file identity instead of comparing full text;
- an `expected_hash` optimistic-concurrency precondition on write_file/replace_in_file that refuses
  a stale write with `status=stale_base` instead of silently overwriting a change it never saw;
- `replace_in_file` refusing an ambiguous (>1 occurrence) `old_text` instead of silently editing
  the first match;
- `apply_unified_diff` classifying WHY a patch failed (invalid_patch_syntax / stale_base) instead of
  one opaque `apply_failed` for every cause, and pinning the last-resort `patch` binary to
  `--fuzz=0` so it cannot silently apply to the wrong location;
- the argument-alias table and `_tool_observation` builder each having exactly one implementation.

Each destructive-precondition test also asserts the file on disk is UNCHANGED after the refusal —
a status string alone does not prove nothing was written.
"""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.execution.artifacts import atomic_write_text
from core.execution.workspace_tools import apply_unified_diff_workspace
from core.runtime_execution_tools import execute_runtime_tool


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class AtomicWriteTests(unittest.TestCase):
    def test_atomic_write_replaces_content_in_one_step(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "file.txt"
            target.write_text("before", encoding="utf-8")
            atomic_write_text(target, "after")
            self.assertEqual(target.read_text(encoding="utf-8"), "after")

    def test_a_failed_atomic_write_leaves_the_original_file_untouched(self) -> None:
        """Sabotage target: revert `_write_file`/`_replace_in_file`/`_apply_unified_diff_python`
        back to a direct `Path.write_text()` call and this test's guarantee no longer holds for
        them, because a direct write truncates the target before writing the new bytes -- a failure
        partway through leaves a corrupt file. `atomic_write_text` writes to a temp file and swaps
        it in with a single `os.replace`, so a failure anywhere before that swap must leave the
        original bytes on disk, unchanged, with no leftover temp file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "file.txt"
            target.write_text("original content", encoding="utf-8")
            with mock.patch("core.execution.artifacts.os.replace", side_effect=OSError("simulated crash")):
                with self.assertRaises(OSError):
                    atomic_write_text(target, "new content that must never land")
            self.assertEqual(target.read_text(encoding="utf-8"), "original content")
            leftover = [p.name for p in Path(tmpdir).iterdir() if p.name != "file.txt"]
            self.assertEqual(leftover, [], f"temp file(s) left behind: {leftover}")

    def test_write_file_through_the_dispatcher_never_leaves_a_partial_file(self) -> None:
        """Same guarantee, exercised through the real workspace.write_file tool rather than the
        helper directly. `execute_runtime_tool` catches the underlying OSError and reports a clean
        `status="error"` result -- the invariant under test is not the exception, it's that the
        file on disk was never truncated/overwritten while the (simulated) crash was in progress."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "a.txt"
            path.write_text("original", encoding="utf-8")
            with mock.patch("core.execution.artifacts.os.replace", side_effect=OSError("disk full")):
                result = execute_runtime_tool(
                    "workspace.write_file",
                    {"path": "a.txt", "content": "clobbered"},
                    source_context={"workspace": tmpdir},
                )
            assert result is not None
            self.assertFalse(result.ok)
            self.assertEqual(path.read_text(encoding="utf-8"), "original")


class ReadFileHashTests(unittest.TestCase):
    def test_read_file_reports_a_content_hash_matching_the_file_on_disk(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "notes.txt").write_text("alpha\nbeta\n", encoding="utf-8")
            result = execute_runtime_tool(
                "workspace.read_file", {"path": "notes.txt"}, source_context={"workspace": tmpdir}
            )
            assert result is not None
            self.assertTrue(result.ok, result.response_text)
            expected = _sha256("alpha\nbeta\n")
            self.assertEqual(result.details["hash"], expected)
            self.assertEqual(result.details["observation"]["hash"], expected)

    def test_read_file_hash_is_over_the_whole_file_not_just_the_shown_slice(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            full_text = "\n".join(f"line{i}" for i in range(1, 21)) + "\n"
            (Path(tmpdir) / "big.txt").write_text(full_text, encoding="utf-8")
            result = execute_runtime_tool(
                "workspace.read_file",
                {"path": "big.txt", "start_line": 1, "max_lines": 3},
                source_context={"workspace": tmpdir},
            )
            assert result is not None
            self.assertTrue(result.ok)
            self.assertTrue(result.details["truncated"])
            self.assertEqual(result.details["hash"], _sha256(full_text))


class WriteFileStaleBaseTests(unittest.TestCase):
    def test_write_file_reports_before_and_after_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = execute_runtime_tool(
                "workspace.write_file",
                {"path": "a.txt", "content": "hello"},
                source_context={"workspace": tmpdir},
            )
            assert result is not None
            self.assertTrue(result.ok, result.response_text)
            self.assertEqual(result.details["before_hash"], "")
            self.assertEqual(result.details["after_hash"], _sha256("hello"))

    def test_write_file_rejects_a_stale_expected_hash_without_writing(self) -> None:
        """Sabotage target: remove the `expected_hash` precondition in `_write_file` and this test
        must fail -- a caller working from a stale read must not have its write silently clobber a
        change made by someone else in between."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "shared.txt"
            path.write_text("original", encoding="utf-8")
            stale_hash = _sha256("a version this caller never actually read")

            result = execute_runtime_tool(
                "workspace.write_file",
                {"path": "shared.txt", "content": "clobbered", "expected_hash": stale_hash},
                source_context={"workspace": tmpdir},
            )
            assert result is not None
            self.assertFalse(result.ok)
            self.assertEqual(result.status, "stale_base")
            self.assertEqual(path.read_text(encoding="utf-8"), "original")

    def test_write_file_with_the_correct_expected_hash_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "shared.txt"
            path.write_text("original", encoding="utf-8")
            correct_hash = _sha256("original")

            result = execute_runtime_tool(
                "workspace.write_file",
                {"path": "shared.txt", "content": "updated", "expected_hash": correct_hash},
                source_context={"workspace": tmpdir},
            )
            assert result is not None
            self.assertTrue(result.ok, result.response_text)
            self.assertEqual(path.read_text(encoding="utf-8"), "updated")

    def test_write_file_without_expected_hash_is_unchanged_backward_compatible(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "shared.txt"
            path.write_text("original", encoding="utf-8")
            result = execute_runtime_tool(
                "workspace.write_file",
                {"path": "shared.txt", "content": "updated"},
                source_context={"workspace": tmpdir},
            )
            assert result is not None
            self.assertTrue(result.ok, result.response_text)
            self.assertEqual(path.read_text(encoding="utf-8"), "updated")


class ReplaceInFileAmbiguityTests(unittest.TestCase):
    def test_replace_in_file_refuses_an_ambiguous_match(self) -> None:
        """Sabotage target: this is the exact bug the fix closes. Before it,
        `content.replace(old_text, new_text, 1)` silently edited the FIRST of the two occurrences
        below with no signal that a second, identical, un-edited block still exists -- a model
        intending to change one specific occurrence could silently change the wrong one."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.py"
            original = "TIMEOUT = 30\nother = 1\nTIMEOUT = 30\n"
            path.write_text(original, encoding="utf-8")

            result = execute_runtime_tool(
                "workspace.replace_in_file",
                {"path": "config.py", "old_text": "TIMEOUT = 30", "new_text": "TIMEOUT = 60"},
                source_context={"workspace": tmpdir},
            )
            assert result is not None
            self.assertFalse(result.ok)
            self.assertEqual(result.status, "ambiguous_match")
            self.assertEqual(result.details["match_count"], 2)
            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_replace_all_true_intentionally_replaces_every_occurrence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.py"
            path.write_text("TIMEOUT = 30\nother = 1\nTIMEOUT = 30\n", encoding="utf-8")

            result = execute_runtime_tool(
                "workspace.replace_in_file",
                {
                    "path": "config.py",
                    "old_text": "TIMEOUT = 30",
                    "new_text": "TIMEOUT = 60",
                    "replace_all": True,
                },
                source_context={"workspace": tmpdir},
            )
            assert result is not None
            self.assertTrue(result.ok, result.response_text)
            self.assertEqual(result.details["replacements"], 2)
            self.assertEqual(path.read_text(encoding="utf-8"), "TIMEOUT = 60\nother = 1\nTIMEOUT = 60\n")

    def test_a_single_match_is_still_replaced_without_needing_replace_all(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.py"
            path.write_text("TIMEOUT = 30\nother = 1\n", encoding="utf-8")

            result = execute_runtime_tool(
                "workspace.replace_in_file",
                {"path": "config.py", "old_text": "TIMEOUT = 30", "new_text": "TIMEOUT = 60"},
                source_context={"workspace": tmpdir},
            )
            assert result is not None
            self.assertTrue(result.ok, result.response_text)
            self.assertEqual(result.details["replacements"], 1)

    def test_replace_in_file_rejects_a_stale_expected_hash_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "a.txt"
            path.write_text("one two three", encoding="utf-8")

            result = execute_runtime_tool(
                "workspace.replace_in_file",
                {
                    "path": "a.txt",
                    "old_text": "two",
                    "new_text": "TWO",
                    "expected_hash": _sha256("a stale view of this file"),
                },
                source_context={"workspace": tmpdir},
            )
            assert result is not None
            self.assertFalse(result.ok)
            self.assertEqual(result.status, "stale_base")
            self.assertEqual(path.read_text(encoding="utf-8"), "one two three")


class ApplyUnifiedDiffFailureClassificationTests(unittest.TestCase):
    def test_a_malformed_patch_is_reported_as_invalid_syntax_not_a_generic_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir).resolve()
            garbage_patch = "--- a/f.txt\n+++ b/f.txt\nnot a real hunk header\n"

            with mock.patch("core.execution.workspace_tools.shutil.which", return_value=None):
                payload = apply_unified_diff_workspace(
                    {"patch": garbage_patch}, workspace_root=workspace, session_id="s1"
                )

            self.assertFalse(payload["ok"])
            self.assertEqual(payload["status"], "invalid_patch_syntax")

    def test_a_patch_whose_context_no_longer_matches_is_reported_as_stale_base(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir).resolve()
            (workspace / "f.txt").write_text("completely different content\n", encoding="utf-8")
            patch = "--- a/f.txt\n+++ b/f.txt\n@@ -1,2 +1,2 @@\n-one\n-two\n+ONE\n+TWO\n"

            with mock.patch("core.execution.workspace_tools.shutil.which", return_value=None):
                payload = apply_unified_diff_workspace(
                    {"patch": patch}, workspace_root=workspace, session_id="s1"
                )

            self.assertFalse(payload["ok"])
            self.assertEqual(payload["status"], "stale_base")

    def test_patch_fallback_pins_fuzz_zero_to_avoid_a_wrong_location_apply(self) -> None:
        """Sabotage target: drop `--fuzz=0` from the system-`patch` invocation and this test fails.
        GNU patch's default fuzz factor (2) drops mismatched context lines and searches nearby for
        an approximate match -- exactly the "minor whitespace tolerance applies to the wrong
        location" failure mode this tool must not have at its last-resort engine."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir).resolve()
            captured: dict[str, list[str]] = {}

            def fake_run(cmd, **kwargs):
                captured["cmd"] = cmd
                return mock.Mock(returncode=1, stdout="", stderr="simulated failure")

            with mock.patch(
                "core.execution.workspace_tools.shutil.which",
                side_effect=lambda name: None if name == "git" else "/usr/bin/patch",
            ), mock.patch(
                "core.execution.workspace_tools._apply_unified_diff_python",
                side_effect=ValueError("Unified diff context mismatch for `f.txt`."),
            ), mock.patch("core.execution.workspace_tools.subprocess.run", side_effect=fake_run):
                apply_unified_diff_workspace(
                    {"patch": "--- a/f.txt\n+++ b/f.txt\n@@ -1,1 +1,1 @@\n-x\n+y\n"},
                    workspace_root=workspace,
                    session_id="s1",
                )

            self.assertIn("--fuzz=0", captured["cmd"])


class SharedHelperConsolidationTests(unittest.TestCase):
    """CLAUDE.md 0.5: a subsystem touched twice by two independently-maintained copies is a
    near-zero-blast-radius defect, not a style nit -- these two duplicates had already caused a
    real permission-gate/dispatcher mismatch (see core/tool_argument_aliases.py's docstring)."""

    def test_tool_observation_is_the_single_shared_implementation(self) -> None:
        from core.execution.models import _tool_observation as models_tool_observation
        from core.runtime_execution_tools import _tool_observation as dispatcher_tool_observation

        self.assertIs(dispatcher_tool_observation, models_tool_observation)

    def test_argument_alias_binding_is_sourced_from_the_one_shared_table(self) -> None:
        from core.runtime_execution_tools import _bind_known_argument_aliases
        from core.runtime_tool_contracts import runtime_tool_contract_map
        from core.tool_argument_aliases import ARGUMENT_ALIASES

        contract = runtime_tool_contract_map()["machine.list_directory"]
        bound = _bind_known_argument_aliases({"directory": "~/Desktop"}, contract=contract)
        self.assertEqual(bound, {"path": "~/Desktop"})
        self.assertIn("directory", ARGUMENT_ALIASES)


if __name__ == "__main__":
    unittest.main()
