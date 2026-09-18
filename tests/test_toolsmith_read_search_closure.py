"""TOOLSMITH closure item 4: targeted read_file/search_text contract closure.

Verifies current production behavior against the required distinctions and fixes ONLY the
confirmed violations found by probing each case directly -- no new encoding subsystem, no
new ignore-policy engine.

Confirmed violations fixed here:
- a directory (or other non-regular path) passed to read_file was reported as `not_found` --
  indistinguishable from nothing existing there at all. Now `invalid_target_type`.
- a workspace-escape (traversal, symlink pointing outside the project) raised a bare `ValueError`
  that the dispatcher's generic `except Exception` turned into `status="error"` -- indistinguishable
  from any other unexpected failure. Now `scope_violation`, for both read_file and search_text.
- reading a file that is not valid UTF-8 (Latin-1, arbitrary invalid bytes) silently replaced
  undecodable bytes with U+FFFD and reported `status="executed"` with no signal anything was lossy.
  Now flags `decode_lossy=true` and prepends a note.
- search_text swallowed per-file read exceptions and, if EVERY file in the scan failed to read,
  still reported a confident `no_results` (ok=True) -- a negative built on zero actual reads. Now
  `search_incomplete` (ok=False) when there are zero matches AND at least one file could not be read.

Confirmed NOT violations (verified, left unchanged): empty file, no-trailing-newline, CRLF, a
symlink pointing INSIDE the workspace, binary-file refusal, truncation reporting, missing-file
reporting, hidden-file/ignored-file exclusion (an existing, consistent, already-documented policy),
and search's binary-file skip / truncated-result reporting.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from core.runtime_execution_tools import execute_runtime_tool


class ReadFileMatrixTests(unittest.TestCase):
    def test_empty_utf8_file_is_a_successful_empty_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "empty.txt").write_text("", encoding="utf-8")
            result = execute_runtime_tool("workspace.read_file", {"path": "empty.txt"}, source_context={"workspace": tmpdir})
            assert result is not None
            self.assertTrue(result.ok)
            self.assertEqual(result.status, "empty_slice")

    def test_no_trailing_newline_reads_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "a.txt").write_bytes(b"line1\nline2")
            result = execute_runtime_tool("workspace.read_file", {"path": "a.txt", "verbatim": True}, source_context={"workspace": tmpdir})
            assert result is not None
            self.assertTrue(result.ok)
            self.assertEqual(result.status, "executed")
            self.assertEqual(result.response_text, "line1\nline2")
            self.assertFalse(result.details["decode_lossy"])

    def test_crlf_file_reads_cleanly_without_stray_carriage_returns(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "a.txt").write_bytes(b"line1\r\nline2\r\n")
            result = execute_runtime_tool("workspace.read_file", {"path": "a.txt", "verbatim": True}, source_context={"workspace": tmpdir})
            assert result is not None
            self.assertTrue(result.ok)
            self.assertNotIn("\r", result.response_text)
            self.assertFalse(result.details["decode_lossy"])

    def test_latin1_file_is_flagged_as_a_lossy_decode_not_silently_corrupted(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "latin1.txt").write_bytes("café résumé".encode("latin-1"))
            result = execute_runtime_tool("workspace.read_file", {"path": "latin1.txt"}, source_context={"workspace": tmpdir})
            assert result is not None
            self.assertTrue(result.ok)  # still returns best-effort content
            self.assertTrue(result.details["decode_lossy"])
            self.assertIn("not valid UTF-8", result.response_text)
            self.assertTrue(result.details["observation"]["decode_lossy"])

    def test_invalid_utf8_bytes_are_flagged_as_a_lossy_decode(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "bad.txt").write_bytes(b"hello \xff\xfe world\n")
            result = execute_runtime_tool("workspace.read_file", {"path": "bad.txt"}, source_context={"workspace": tmpdir})
            assert result is not None
            self.assertTrue(result.details["decode_lossy"])

    def test_valid_utf8_is_never_flagged_lossy(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "ok.txt").write_text("héllo wörld 日本語", encoding="utf-8")
            result = execute_runtime_tool("workspace.read_file", {"path": "ok.txt"}, source_context={"workspace": tmpdir})
            assert result is not None
            self.assertFalse(result.details["decode_lossy"])

    def test_binary_file_is_refused_distinctly(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "bin.dat").write_bytes(b"\x00\x01\x02\x03binary")
            result = execute_runtime_tool("workspace.read_file", {"path": "bin.dat"}, source_context={"workspace": tmpdir})
            assert result is not None
            self.assertFalse(result.ok)
            self.assertEqual(result.status, "binary_file")

    def test_large_file_reports_truncation_distinctly_from_a_complete_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            full_text = "\n".join(f"line{i}" for i in range(1, 5001)) + "\n"
            (Path(tmpdir) / "big.txt").write_text(full_text, encoding="utf-8")
            result = execute_runtime_tool(
                "workspace.read_file", {"path": "big.txt", "start_line": 1, "max_lines": 10}, source_context={"workspace": tmpdir}
            )
            assert result is not None
            self.assertEqual(result.status, "truncated")
            self.assertTrue(result.details["truncated"])
            self.assertEqual(result.details["total_lines"], 5000)

    def test_missing_path_is_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = execute_runtime_tool("workspace.read_file", {"path": "nope.txt"}, source_context={"workspace": tmpdir})
            assert result is not None
            self.assertFalse(result.ok)
            self.assertEqual(result.status, "not_found")

    def test_directory_passed_as_file_is_a_distinct_invalid_target_type_not_not_found(self) -> None:
        """Sabotage target: this is a confirmed violation the fix closes -- a directory used to be
        reported identically to a missing path, which is false (something IS there)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "adir").mkdir()
            result = execute_runtime_tool("workspace.read_file", {"path": "adir"}, source_context={"workspace": tmpdir})
            assert result is not None
            self.assertFalse(result.ok)
            self.assertEqual(result.status, "invalid_target_type")
            self.assertNotEqual(result.status, "not_found")
            self.assertEqual(result.details["target_type"], "directory")

    def test_symlink_inside_workspace_reads_through_normally(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            real = Path(tmpdir) / "real.txt"
            real.write_text("inside content", encoding="utf-8")
            link = Path(tmpdir) / "link.txt"
            link.symlink_to(real)
            result = execute_runtime_tool("workspace.read_file", {"path": "link.txt"}, source_context={"workspace": tmpdir})
            assert result is not None
            self.assertTrue(result.ok)
            self.assertIn("inside content", result.response_text)

    def test_symlink_escaping_workspace_is_a_clean_scope_violation_not_a_generic_error(self) -> None:
        """Sabotage target: this is a confirmed violation the fix closes -- an escape used to
        surface as status="error", indistinguishable from any other unexpected failure."""
        with tempfile.TemporaryDirectory() as workspace_dir, tempfile.TemporaryDirectory() as outside_dir:
            outside_file = Path(outside_dir) / "secret.txt"
            outside_file.write_text("outside secret", encoding="utf-8")
            escape_link = Path(workspace_dir) / "escape.txt"
            escape_link.symlink_to(outside_file)

            result = execute_runtime_tool("workspace.read_file", {"path": "escape.txt"}, source_context={"workspace": workspace_dir})
            assert result is not None
            self.assertFalse(result.ok)
            self.assertEqual(result.status, "scope_violation")
            self.assertNotEqual(result.status, "error")
            self.assertIn("escapes the active workspace", result.response_text)

    def test_traversal_path_is_a_clean_scope_violation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = execute_runtime_tool("workspace.read_file", {"path": "../../../etc/passwd"}, source_context={"workspace": tmpdir})
            assert result is not None
            self.assertFalse(result.ok)
            self.assertEqual(result.status, "scope_violation")


class SearchTextMatrixTests(unittest.TestCase):
    def test_match_is_found_with_exact_path_and_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "a.py").write_text("one\ndef needle():\n    pass\n", encoding="utf-8")
            result = execute_runtime_tool("workspace.search_text", {"query": "needle"}, source_context={"workspace": tmpdir})
            assert result is not None
            self.assertTrue(result.ok)
            self.assertEqual(result.status, "executed")
            match = result.details["observation"]["matches"][0]
            self.assertEqual(match["path"], "a.py")
            self.assertEqual(match["line"], 2)

    def test_no_match_is_a_clean_negative(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "a.py").write_text("nothing interesting here\n", encoding="utf-8")
            result = execute_runtime_tool("workspace.search_text", {"query": "absolutely_absent_zzq"}, source_context={"workspace": tmpdir})
            assert result is not None
            self.assertTrue(result.ok)
            self.assertEqual(result.status, "no_results")

    def test_execution_failure_is_not_reported_as_a_confident_no_match(self) -> None:
        """Sabotage target: this is a confirmed violation the fix closes -- a file that could not
        be read used to be silently skipped, and if EVERY file failed, the search still reported
        `no_results` with ok=True: a negative built on zero actual reads."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "unreadable.py"
            path.write_text("def needle(): pass\n", encoding="utf-8")
            os.chmod(path, 0o000)
            try:
                result = execute_runtime_tool(
                    "workspace.search_text", {"query": "needle", "path": "unreadable.py"}, source_context={"workspace": tmpdir}
                )
            finally:
                os.chmod(path, 0o644)
            assert result is not None
            self.assertFalse(result.ok)
            self.assertEqual(result.status, "search_incomplete")
            self.assertNotEqual(result.status, "no_results")
            self.assertIn("unreadable.py", result.details["read_errors"])

    def test_hidden_file_is_excluded_under_the_existing_documented_ignore_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "a.py").write_text("def needle(): pass\n", encoding="utf-8")
            (Path(tmpdir) / ".hidden.py").write_text("def needle(): pass\n", encoding="utf-8")
            result = execute_runtime_tool("workspace.search_text", {"query": "needle"}, source_context={"workspace": tmpdir})
            assert result is not None
            paths = {m["path"] for m in result.details["observation"]["matches"]}
            self.assertIn("a.py", paths)
            self.assertNotIn(".hidden.py", paths)

    def test_binary_file_is_skipped_not_matched_or_errored(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "bin.dat").write_bytes(b"\x00\x01needle\x00")
            (Path(tmpdir) / "a.py").write_text("def needle(): pass\n", encoding="utf-8")
            result = execute_runtime_tool("workspace.search_text", {"query": "needle"}, source_context={"workspace": tmpdir})
            assert result is not None
            self.assertTrue(result.ok)
            paths = {m["path"] for m in result.details["observation"]["matches"]}
            self.assertEqual(paths, {"a.py"})

    def test_truncated_result_set_is_reported_distinctly(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            for i in range(5):
                (Path(tmpdir) / f"f{i}.py").write_text("needle\n", encoding="utf-8")
            result = execute_runtime_tool("workspace.search_text", {"query": "needle", "limit": 2}, source_context={"workspace": tmpdir})
            assert result is not None
            self.assertEqual(result.status, "truncated")
            self.assertTrue(result.details["observation"]["truncated"])

    def test_scope_violation_is_clean_not_a_generic_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = execute_runtime_tool(
                "workspace.search_text", {"query": "x", "path": "../../../etc"}, source_context={"workspace": tmpdir}
            )
            assert result is not None
            self.assertFalse(result.ok)
            self.assertEqual(result.status, "scope_violation")


if __name__ == "__main__":
    unittest.main()
