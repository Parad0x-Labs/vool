"""TOOLSMITH ANVIL repair 3: block rollback through a symlinked PARENT directory.

ANVIL-confirmed escape: the prior fix only checked `raw_target.is_symlink()` -- true for a symlink
AT the final path component, but false for an ordinary filename sitting under a PARENT directory
that was replaced by a symlink pointing outside the workspace. Rollback then read/wrote/deleted
through that path transparently, following the symlinked parent wherever it actually pointed,
returning ok=true/status=executed while touching a file entirely outside the workspace.

The fix (`_target_parent_within_workspace`) resolves the target's PARENT chain and confirms it
still lands inside the workspace root, mirroring what `resolve_workspace_path` already enforces on
the forward write path. Every test drives the real dispatcher.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.execution import artifacts as artifacts_module
from core.runtime_execution_tools import execute_runtime_tool


def _rollback(workspace: str):
    result = execute_runtime_tool("workspace.rollback_last_change", {}, source_context={"workspace": workspace})
    assert result is not None
    return result


class OrdinaryNestedParentTests(unittest.TestCase):
    def test_ordinary_nested_parent_directories_roll_back_successfully(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            nested = Path(tmpdir) / "a" / "b" / "c"
            nested.mkdir(parents=True)
            path = nested / "file.txt"
            path.write_text("original", encoding="utf-8")

            written = execute_runtime_tool(
                "workspace.write_file", {"path": "a/b/c/file.txt", "content": "changed"}, source_context={"workspace": tmpdir}
            )
            assert written is not None and written.ok

            reverted = _rollback(tmpdir)
            self.assertTrue(reverted.ok, reverted.response_text)
            self.assertEqual(path.read_text(encoding="utf-8"), "original")


class LeafSymlinkEscapeTests(unittest.TestCase):
    def test_leaf_symlink_escape_fails(self) -> None:
        with tempfile.TemporaryDirectory() as workspace_dir, tempfile.TemporaryDirectory() as outside_dir:
            path = Path(workspace_dir) / "secret.txt"
            path.write_text("original", encoding="utf-8")
            written = execute_runtime_tool(
                "workspace.write_file", {"path": "secret.txt", "content": "changed"}, source_context={"workspace": workspace_dir}
            )
            assert written is not None and written.ok

            outside_target = Path(outside_dir) / "real_secret.txt"
            outside_target.write_text("outside content", encoding="utf-8")
            path.unlink()
            path.symlink_to(outside_target)

            reverted = _rollback(workspace_dir)
            self.assertFalse(reverted.ok)
            self.assertEqual(reverted.status, "stale_revert_conflict")
            self.assertEqual(outside_target.read_text(encoding="utf-8"), "outside content")


class SymlinkedParentDirectoryEscapeTests(unittest.TestCase):
    def test_symlinked_parent_directory_write_escape_fails(self) -> None:
        """The exact ANVIL-confirmed scenario: an ordinary filename under a parent that was
        replaced by a symlink, not a symlinked leaf."""
        with tempfile.TemporaryDirectory() as workspace_dir, tempfile.TemporaryDirectory() as outside_dir:
            subdir = Path(workspace_dir) / "subdir"
            subdir.mkdir()
            path = subdir / "file.txt"
            path.write_text("original", encoding="utf-8")

            written = execute_runtime_tool(
                "workspace.write_file", {"path": "subdir/file.txt", "content": "changed-by-vool"}, source_context={"workspace": workspace_dir}
            )
            assert written is not None and written.ok

            # Externally: the real "subdir" is replaced by a symlink to an unrelated directory
            # outside the workspace, which happens to have its OWN file.txt with sensitive content.
            outside_subdir = Path(outside_dir) / "attacker_controlled"
            outside_subdir.mkdir()
            outside_file = outside_subdir / "file.txt"
            outside_file.write_text("sensitive content that must never be touched", encoding="utf-8")

            import shutil

            shutil.rmtree(subdir)
            subdir.symlink_to(outside_subdir)

            reverted = _rollback(workspace_dir)
            self.assertFalse(reverted.ok, "rollback through a symlinked parent must be refused, not silently followed")
            self.assertEqual(reverted.status, "stale_revert_conflict")
            conflicts = reverted.details.get("conflicts") or []
            self.assertEqual(conflicts[0]["reason"], "parent_directory_escapes_workspace")
            self.assertEqual(
                outside_file.read_text(encoding="utf-8"),
                "sensitive content that must never be touched",
                "the file outside the workspace must remain byte-for-byte unchanged",
            )

    def test_delete_through_symlinked_parent_fails(self) -> None:
        """Rollback's DELETE action (undoing a create) must not delete through a symlinked parent
        either -- not just the restore/write action."""
        with tempfile.TemporaryDirectory() as workspace_dir, tempfile.TemporaryDirectory() as outside_dir:
            subdir = Path(workspace_dir) / "subdir"
            subdir.mkdir()

            created = execute_runtime_tool(
                "workspace.write_file", {"path": "subdir/newfile.txt", "content": "brand new"}, source_context={"workspace": workspace_dir}
            )
            assert created is not None and created.ok

            outside_subdir = Path(outside_dir) / "attacker_controlled"
            outside_subdir.mkdir()
            outside_file = outside_subdir / "newfile.txt"
            outside_file.write_text("a file that happens to share the same name outside the workspace", encoding="utf-8")

            import shutil

            shutil.rmtree(subdir)
            subdir.symlink_to(outside_subdir)

            reverted = _rollback(workspace_dir)
            self.assertFalse(reverted.ok, "rollback's delete-through-symlinked-parent must be refused")
            self.assertEqual(reverted.status, "stale_revert_conflict")
            self.assertTrue(
                outside_file.exists() and outside_file.read_text(encoding="utf-8") == "a file that happens to share the same name outside the workspace",
                "the outside file must not have been deleted",
            )


class MultiFileRollbackZeroWritesTests(unittest.TestCase):
    def test_multi_file_rollback_with_one_escaping_target_performs_zero_writes(self) -> None:
        with tempfile.TemporaryDirectory() as workspace_dir, tempfile.TemporaryDirectory() as outside_dir:
            (Path(workspace_dir) / "a.txt").write_text("a-original", encoding="utf-8")
            subdir = Path(workspace_dir) / "subdir"
            subdir.mkdir()
            (subdir / "b.txt").write_text("b-original", encoding="utf-8")

            patch_text = (
                "--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-a-original\n+a-changed\n"
                "--- a/subdir/b.txt\n+++ b/subdir/b.txt\n@@ -1 +1 @@\n-b-original\n+b-changed\n"
            )
            applied = execute_runtime_tool(
                "workspace.apply_unified_diff", {"patch": patch_text}, source_context={"workspace": workspace_dir}
            )
            assert applied is not None and applied.ok, getattr(applied, "response_text", "")
            post_mutation_a = (Path(workspace_dir) / "a.txt").read_text(encoding="utf-8")
            self.assertEqual(post_mutation_a.strip(), "a-changed")
            self.assertEqual((subdir / "b.txt").read_text(encoding="utf-8").strip(), "b-changed")

            # Escape subdir AFTER the mutation, before rollback.
            outside_subdir = Path(outside_dir) / "attacker_controlled"
            outside_subdir.mkdir()
            (outside_subdir / "b.txt").write_text("outside content that must not be touched", encoding="utf-8")

            import shutil

            shutil.rmtree(subdir)
            subdir.symlink_to(outside_subdir)

            reverted = _rollback(workspace_dir)
            self.assertFalse(reverted.ok)
            self.assertEqual(reverted.status, "stale_revert_conflict")
            # ZERO writes: a.txt (the non-escaping file in the SAME mutation) must remain at its
            # post-mutation ("a-changed") state -- not rolled back, not touched at all.
            self.assertEqual(
                (Path(workspace_dir) / "a.txt").read_text(encoding="utf-8"), post_mutation_a,
                "a mutation with one escaping target must roll back NOTHING, not partially succeed",
            )
            self.assertEqual((outside_subdir / "b.txt").read_text(encoding="utf-8"), "outside content that must not be touched")


class ParentContainmentSabotageTests(unittest.TestCase):
    def test_sabotage_removing_parent_containment_check_lets_the_escape_through(self) -> None:
        with tempfile.TemporaryDirectory() as workspace_dir, tempfile.TemporaryDirectory() as outside_dir:
            subdir = Path(workspace_dir) / "subdir"
            subdir.mkdir()
            path = subdir / "file.txt"
            path.write_text("original", encoding="utf-8")
            written = execute_runtime_tool(
                "workspace.write_file", {"path": "subdir/file.txt", "content": "changed-by-vool"}, source_context={"workspace": workspace_dir}
            )
            assert written is not None and written.ok

            outside_subdir = Path(outside_dir) / "attacker_controlled"
            outside_subdir.mkdir()
            outside_file = outside_subdir / "file.txt"
            # Deliberately matches the mutation's OWN after-content: isolates the parent-containment
            # check as the only thing standing between "safe" and "escaped" -- if the outside file's
            # content differed, the pre-existing content-hash check would independently (and
            # correctly) refuse too, which would prove nothing about THIS specific guard.
            outside_file.write_text("changed-by-vool", encoding="utf-8")

            import shutil

            shutil.rmtree(subdir)
            subdir.symlink_to(outside_subdir)

            with mock.patch.object(artifacts_module, "_target_parent_within_workspace", return_value=True):
                reverted = _rollback(workspace_dir)

            # With the containment check disabled (and content matching, so no OTHER check catches
            # it), the escape goes through: outside file gets silently overwritten by the restore.
            self.assertTrue(reverted.ok, "with the guard disabled, the sabotaged path 'succeeds' -- and writes outside the workspace")
            self.assertEqual(
                outside_file.read_text(encoding="utf-8"), "original",
                "sabotaged path must have overwritten the outside file with the mutation's BEFORE text -- proving the real guard is load-bearing",
            )

    def test_sabotage_removing_parent_containment_check_lets_the_delete_escape_through(self) -> None:
        """Same isolation as the write-escape sabotage above, for the DELETE action (undoing a
        create): the outside decoy's content is made to match what the ORIGINAL create wrote, so
        the pre-existing content-hash check cannot independently catch this -- only the
        parent-containment check can, isolating it specifically."""
        with tempfile.TemporaryDirectory() as workspace_dir, tempfile.TemporaryDirectory() as outside_dir:
            subdir = Path(workspace_dir) / "subdir"
            subdir.mkdir()
            created = execute_runtime_tool(
                "workspace.write_file", {"path": "subdir/newfile.txt", "content": "brand new"}, source_context={"workspace": workspace_dir}
            )
            assert created is not None and created.ok

            outside_subdir = Path(outside_dir) / "attacker_controlled"
            outside_subdir.mkdir()
            outside_file = outside_subdir / "newfile.txt"
            # Matches the mutation's own after-content exactly -- isolates the parent-containment
            # check as the only thing that can refuse this specific delete.
            outside_file.write_text("brand new", encoding="utf-8")

            import shutil

            shutil.rmtree(subdir)
            subdir.symlink_to(outside_subdir)

            with mock.patch.object(artifacts_module, "_target_parent_within_workspace", return_value=True):
                reverted = _rollback(workspace_dir)

            self.assertTrue(reverted.ok, "with the guard disabled, the sabotaged path 'succeeds' -- and deletes outside the workspace")
            self.assertFalse(
                outside_file.exists(),
                "sabotaged path must have DELETED the outside file -- proving the real guard is load-bearing for the delete action too",
            )


if __name__ == "__main__":
    unittest.main()
