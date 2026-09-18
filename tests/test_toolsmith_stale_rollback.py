"""TOOLSMITH closure item 1: rollback must not overwrite work it never saw.

`rollback_last_workspace_mutation` used to restore `before_text` unconditionally. If a user or
another process touched the file after VOOL's own mutation, rollback would silently destroy that
later change with no way to get it back. It now verifies the CURRENT on-disk state still matches
what the mutation itself left behind (`after_hash`/`existed_after`, persisted on the mutation
record) before writing anything, refusing with `status=stale_revert_conflict` on any mismatch --
content changed, target missing, target's type changed, or the target replaced by a symlink.

Every test drives the real dispatcher (`execute_runtime_tool`), not the ledger functions directly,
so this proves the guarantee at the same seam a model actually calls.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.execution import artifacts as artifacts_module
from core.runtime_execution_tools import execute_runtime_tool


def _rollback(workspace: str) -> object:
    result = execute_runtime_tool("workspace.rollback_last_change", {}, source_context={"workspace": workspace})
    assert result is not None
    return result


class OrdinaryRollbackTests(unittest.TestCase):
    def test_ordinary_rollback_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "a.txt"
            path.write_text("original", encoding="utf-8")

            written = execute_runtime_tool(
                "workspace.write_file", {"path": "a.txt", "content": "changed"}, source_context={"workspace": tmpdir}
            )
            assert written is not None and written.ok
            self.assertEqual(path.read_text(encoding="utf-8"), "changed")

            reverted = _rollback(tmpdir)
            self.assertTrue(reverted.ok, reverted.response_text)
            self.assertEqual(reverted.status, "executed")
            self.assertEqual(path.read_text(encoding="utf-8"), "original")


class ExternalEditConflictTests(unittest.TestCase):
    def test_external_edit_after_mutation_causes_stale_revert_conflict(self) -> None:
        """Sabotage target: this is the exact defect the fix closes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "shared.txt"
            path.write_text("original", encoding="utf-8")

            written = execute_runtime_tool(
                "workspace.write_file", {"path": "shared.txt", "content": "vool-changed"}, source_context={"workspace": tmpdir}
            )
            assert written is not None and written.ok

            # A different process/person edits the file AFTER VOOL's mutation, with no VOOL call in
            # between -- the mutation ledger has no idea this happened.
            path.write_text("someone-elses-later-edit", encoding="utf-8")

            reverted = _rollback(tmpdir)
            self.assertFalse(reverted.ok)
            self.assertEqual(reverted.status, "stale_revert_conflict")
            conflicts = reverted.details.get("conflicts") or []
            self.assertEqual(len(conflicts), 1)
            self.assertEqual(conflicts[0]["path"], "shared.txt")
            self.assertEqual(conflicts[0]["reason"], "content_changed")
            # The load-bearing assertion: the later edit is still there, byte for byte. Neither the
            # original content nor VOOL's own change was written back over it.
            self.assertEqual(path.read_text(encoding="utf-8"), "someone-elses-later-edit")

    def test_sabotage_removing_the_hash_comparison_loses_the_external_edit(self) -> None:
        """Proves the test above actually exercises the guard: with the current-hash comparison
        disabled, rollback proceeds and the external edit is destroyed -- i.e. reverting the fix
        makes THIS test's assertion of data loss come true, and makes the test above fail."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "shared.txt"
            path.write_text("original", encoding="utf-8")
            written = execute_runtime_tool(
                "workspace.write_file", {"path": "shared.txt", "content": "vool-changed"}, source_context={"workspace": tmpdir}
            )
            assert written is not None and written.ok
            path.write_text("someone-elses-later-edit", encoding="utf-8")

            def _always_clean(_raw_target, *, workspace_root, existed_before, expected_existed_after, expected_after_hash):
                del workspace_root, existed_before, expected_existed_after, expected_after_hash
                return None

            with mock.patch.object(artifacts_module, "_diagnose_rollback_conflict", side_effect=_always_clean):
                reverted = _rollback(tmpdir)

            # With the guard disabled, rollback "succeeds" -- and the external edit is gone.
            self.assertTrue(reverted.ok)
            self.assertNotEqual(path.read_text(encoding="utf-8"), "someone-elses-later-edit")


class ExternalDeletionTests(unittest.TestCase):
    def test_externally_deleted_created_file_rolls_back_cleanly(self) -> None:
        """A creation whose file is already gone has nothing left to undo -- not a conflict."""
        with tempfile.TemporaryDirectory() as tmpdir:
            created = execute_runtime_tool(
                "workspace.write_file", {"path": "new.txt", "content": "brand new"}, source_context={"workspace": tmpdir}
            )
            assert created is not None and created.ok
            (Path(tmpdir) / "new.txt").unlink()

            reverted = _rollback(tmpdir)
            self.assertTrue(reverted.ok, reverted.response_text)
            self.assertFalse((Path(tmpdir) / "new.txt").exists())

    def test_externally_deleted_modified_file_is_not_silently_recreated(self) -> None:
        """A modification whose file was deleted afterward must NOT have rollback recreate it with
        the old content -- that would silently undo the user's deliberate deletion."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "a.txt"
            path.write_text("original", encoding="utf-8")
            written = execute_runtime_tool(
                "workspace.write_file", {"path": "a.txt", "content": "vool-changed"}, source_context={"workspace": tmpdir}
            )
            assert written is not None and written.ok
            path.unlink()

            reverted = _rollback(tmpdir)
            self.assertFalse(reverted.ok)
            self.assertEqual(reverted.status, "stale_revert_conflict")
            conflicts = reverted.details.get("conflicts") or []
            self.assertEqual(conflicts[0]["reason"], "target_missing")
            self.assertFalse(path.exists(), "rollback must not recreate a file the user deleted")


class SymlinkReplacementTests(unittest.TestCase):
    def test_target_replaced_by_symlink_does_not_follow_outside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as workspace_dir, tempfile.TemporaryDirectory() as outside_dir:
            path = Path(workspace_dir) / "secret.txt"
            path.write_text("original", encoding="utf-8")
            written = execute_runtime_tool(
                "workspace.write_file", {"path": "secret.txt", "content": "vool-changed"}, source_context={"workspace": workspace_dir}
            )
            assert written is not None and written.ok

            outside_target = Path(outside_dir) / "real_secret.txt"
            outside_target.write_text("something outside the workspace entirely", encoding="utf-8")
            path.unlink()
            path.symlink_to(outside_target)

            reverted = _rollback(workspace_dir)
            self.assertFalse(reverted.ok)
            self.assertEqual(reverted.status, "stale_revert_conflict")
            conflicts = reverted.details.get("conflicts") or []
            self.assertEqual(conflicts[0]["reason"], "target_replaced_by_symlink")
            # Never followed: the outside file is untouched, and the symlink itself is undisturbed.
            self.assertEqual(outside_target.read_text(encoding="utf-8"), "something outside the workspace entirely")
            self.assertTrue(path.is_symlink())


class OrderedMutationRollbackTests(unittest.TestCase):
    def test_multiple_ordered_mutations_roll_back_only_in_valid_reverse_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "a.txt"
            path.write_text("v0", encoding="utf-8")

            for content in ("v1", "v2", "v3"):
                result = execute_runtime_tool(
                    "workspace.write_file", {"path": "a.txt", "content": content}, source_context={"workspace": tmpdir}
                )
                assert result is not None and result.ok
            self.assertEqual(path.read_text(encoding="utf-8"), "v3")

            for expected_after_rollback in ("v2", "v1", "v0"):
                reverted = _rollback(tmpdir)
                self.assertTrue(reverted.ok, reverted.response_text)
                self.assertEqual(path.read_text(encoding="utf-8"), expected_after_rollback)

            # Nothing left to roll back.
            exhausted = _rollback(tmpdir)
            self.assertFalse(exhausted.ok)
            self.assertEqual(exhausted.status, "no_tracked_change")


class FailedPatchRollbackScopeTests(unittest.TestCase):
    def test_failed_patch_does_not_roll_back_an_unrelated_earlier_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "a.txt"
            path.write_text("v0", encoding="utf-8")

            mutation_a = execute_runtime_tool(
                "workspace.write_file", {"path": "a.txt", "content": "v1-from-mutation-a"}, source_context={"workspace": tmpdir}
            )
            assert mutation_a is not None and mutation_a.ok

            # A patch that cannot apply anywhere -- must not become "the last mutation".
            malformed_patch = "--- a/a.txt\n+++ b/a.txt\nnot a real hunk\n"
            failed = execute_runtime_tool(
                "workspace.apply_unified_diff", {"patch": malformed_patch}, source_context={"workspace": tmpdir}
            )
            assert failed is not None
            self.assertFalse(failed.ok)
            self.assertEqual(path.read_text(encoding="utf-8"), "v1-from-mutation-a", "a failed patch must not touch the file")

            reverted = _rollback(tmpdir)
            self.assertTrue(reverted.ok, reverted.response_text)
            self.assertTrue(str(reverted.details.get("mutation_id") or "").strip(), "rollback must report mutation A's real ID")
            self.assertEqual(path.read_text(encoding="utf-8"), "v0", "rollback must undo mutation A, the only real mutation recorded")

            exhausted = _rollback(tmpdir)
            self.assertFalse(exhausted.ok)
            self.assertEqual(exhausted.status, "no_tracked_change")


if __name__ == "__main__":
    unittest.main()
