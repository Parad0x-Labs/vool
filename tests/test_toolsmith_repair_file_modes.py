"""TOOLSMITH ANVIL repair 1: file mode preservation through atomic writes.

ANVIL-confirmed regression: `tempfile.mkstemp()` always creates its temp file at 0600, and
`os.replace()` carries THAT mode onto the destination -- not the destination's own prior mode. An
existing 0755 executable script silently became 0600 (unexecutable) after a single
`workspace.write_file` call; `replace_in_file` had the identical defect; rollback restored content
but not the mode that had been in place before the mutation.

Every test here drives the real dispatcher (`execute_runtime_tool`), not `atomic_write_text`
directly, so this proves the guarantee at the seam a model actually calls.
"""

from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.runtime_execution_tools import execute_runtime_tool


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


class WriteFilePreservesModeTests(unittest.TestCase):
    def test_0755_executable_remains_0755_after_write_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "script.sh"
            path.write_text("#!/bin/sh\necho original\n", encoding="utf-8")
            os.chmod(path, 0o755)

            result = execute_runtime_tool(
                "workspace.write_file",
                {"path": "script.sh", "content": "#!/bin/sh\necho updated\n"},
                source_context={"workspace": tmpdir},
            )
            assert result is not None
            self.assertTrue(result.ok, result.response_text)
            self.assertEqual(_mode(path), 0o755)
            self.assertEqual(path.read_text(encoding="utf-8"), "#!/bin/sh\necho updated\n")
            self.assertEqual(result.details["before_mode"], 0o755)
            self.assertEqual(result.details["after_mode"], 0o755)

    def test_0644_remains_0644_after_write_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "a.txt"
            path.write_text("original", encoding="utf-8")
            os.chmod(path, 0o644)

            result = execute_runtime_tool(
                "workspace.write_file", {"path": "a.txt", "content": "changed"}, source_context={"workspace": tmpdir}
            )
            assert result is not None and result.ok
            self.assertEqual(_mode(path), 0o644)

    def test_new_file_follows_umask_semantics_not_forced_0600(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = execute_runtime_tool(
                "workspace.write_file", {"path": "new.txt", "content": "hello"}, source_context={"workspace": tmpdir}
            )
            assert result is not None and result.ok
            path = Path(tmpdir) / "new.txt"
            current_umask = os.umask(0)
            os.umask(current_umask)
            expected_mode = 0o666 & ~current_umask
            self.assertEqual(_mode(path), expected_mode)
            self.assertNotEqual(_mode(path), 0o600, "a brand-new workspace file must not be forced to 0600")

    def test_simulated_write_failure_preserves_original_content_and_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "a.txt"
            path.write_text("original", encoding="utf-8")
            os.chmod(path, 0o755)

            with mock.patch("core.execution.artifacts.os.fsync", side_effect=OSError("simulated write failure")):
                result = execute_runtime_tool(
                    "workspace.write_file", {"path": "a.txt", "content": "clobbered"}, source_context={"workspace": tmpdir}
                )
            assert result is not None
            self.assertFalse(result.ok)
            self.assertEqual(path.read_text(encoding="utf-8"), "original")
            self.assertEqual(_mode(path), 0o755)

    def test_simulated_os_replace_failure_preserves_original_content_and_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "a.txt"
            path.write_text("original", encoding="utf-8")
            os.chmod(path, 0o750)

            with mock.patch("core.execution.artifacts.os.replace", side_effect=OSError("simulated replace failure")):
                result = execute_runtime_tool(
                    "workspace.write_file", {"path": "a.txt", "content": "clobbered"}, source_context={"workspace": tmpdir}
                )
            assert result is not None
            self.assertFalse(result.ok)
            self.assertEqual(path.read_text(encoding="utf-8"), "original")
            self.assertEqual(_mode(path), 0o750)


class ReplaceInFilePreservesModeTests(unittest.TestCase):
    def test_0750_remains_0750_after_replace_in_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "script.sh"
            path.write_text("#!/bin/sh\necho old\n", encoding="utf-8")
            os.chmod(path, 0o750)

            result = execute_runtime_tool(
                "workspace.replace_in_file",
                {"path": "script.sh", "old_text": "old", "new_text": "new"},
                source_context={"workspace": tmpdir},
            )
            assert result is not None
            self.assertTrue(result.ok, result.response_text)
            self.assertEqual(_mode(path), 0o750)
            self.assertEqual(result.details["before_mode"], 0o750)
            self.assertEqual(result.details["after_mode"], 0o750)


class MutationLedgerPermissionsTests(unittest.TestCase):
    def test_mutation_ledger_remains_0600_after_a_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = execute_runtime_tool(
                "workspace.write_file", {"path": "a.txt", "content": "hello"}, source_context={"workspace": tmpdir, "session_id": "ledger-mode-test"}
            )
            assert result is not None and result.ok
            from core.runtime_paths import active_data_dir

            ledger_path = active_data_dir() / "runtime_execution" / "mutations" / "ledger-mode-test.json"
            self.assertTrue(ledger_path.exists())
            self.assertEqual(_mode(ledger_path), 0o600, "the mutation ledger must stay pinned to 0600 regardless of atomic_write_text's own new-file umask behavior")


class RollbackRestoresModeTests(unittest.TestCase):
    def test_rollback_restores_both_content_and_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "script.sh"
            path.write_text("#!/bin/sh\necho original\n", encoding="utf-8")
            os.chmod(path, 0o755)

            written = execute_runtime_tool(
                "workspace.write_file",
                {"path": "script.sh", "content": "#!/bin/sh\necho changed\n"},
                source_context={"workspace": tmpdir},
            )
            assert written is not None and written.ok
            self.assertEqual(_mode(path), 0o755)  # unchanged by the write itself, per the fix above

            # Simulate something ELSE re-chmoding the file (without touching content) between the
            # mutation and the rollback -- rollback must restore the ORIGINAL mode explicitly, not
            # just whatever atomic_write_text finds sitting on the file right now.
            os.chmod(path, 0o644)

            reverted = execute_runtime_tool(
                "workspace.rollback_last_change", {}, source_context={"workspace": tmpdir}
            )
            assert reverted is not None
            self.assertTrue(reverted.ok, reverted.response_text)
            self.assertEqual(path.read_text(encoding="utf-8"), "#!/bin/sh\necho original\n")
            self.assertEqual(_mode(path), 0o755, "rollback must restore the pre-mutation mode, not merely leave whatever mode was on the file at rollback time")

    def test_sabotage_removing_mode_preservation_makes_the_executable_test_fail(self) -> None:
        """Sabotage target: with mode-preservation removed from atomic_write_text (reverted to a
        bare mkstemp-default write), the 0755 executable-preservation test must fail by observing
        the file became non-executable."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "script.sh"
            path.write_text("#!/bin/sh\necho original\n", encoding="utf-8")
            os.chmod(path, 0o755)

            def _unsafe_atomic_write_text(target_path, content, *, encoding="utf-8", mode=None):
                import tempfile as _tempfile

                target_path = Path(target_path)
                fd, tmp_name = _tempfile.mkstemp(dir=str(target_path.parent))
                with os.fdopen(fd, "w", encoding=encoding) as handle:
                    handle.write(content)
                os.replace(tmp_name, target_path)  # no chmod at all -- the sabotaged behavior

            with mock.patch("core.runtime_execution_tools.atomic_write_text", side_effect=_unsafe_atomic_write_text):
                result = execute_runtime_tool(
                    "workspace.write_file",
                    {"path": "script.sh", "content": "#!/bin/sh\necho updated\n"},
                    source_context={"workspace": tmpdir},
                )
            assert result is not None and result.ok
            self.assertNotEqual(
                _mode(path), 0o755,
                "control check: the sabotaged writer really did lose the executable bit (mkstemp default 0600)",
            )
            was_executable = bool(_mode(path) & stat.S_IXUSR)
            self.assertFalse(was_executable, "confirms the sabotage reproduces the ANVIL-reported regression")


if __name__ == "__main__":
    unittest.main()
