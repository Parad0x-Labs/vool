"""ANVIL F6, round 3, HIGH: `machine.move_path` was root-bound in name only.

Confirmed: `move_path` resolved source/destination with `Path(raw).expanduser()` -- never
`.resolve()`. The only containment was a small protected-path DENYLIST (system/wallet
directories); anything else, including `workspace/../../outside_target/exfil.txt`, was taken at
face value, so the escape was not a protected path and the move proceeded: the file landed
outside the workspace and the source disappeared. The consent prompt showed the unresolved
string, which hid the escape from the one human check that remained rather than surfacing it.

Fix (`core/machine_file_ops.py`): both paths are canonically resolved (`_canonicalize`, following
symlinks in existing ancestors, normalizing `..` against the real filesystem) BEFORE the
protected-path check, BEFORE any new positive `allowed_roots` containment check, and BEFORE
consent is ever requested -- consent is not a containment boundary and never was; it confirms an
action a human can evaluate, and a human cannot evaluate an escape they cannot see. The real
dispatcher (`core/runtime_execution_tools.py::_machine_move_path`) always supplies the bound
workspace plus the safe machine roots as `allowed_roots` -- a positive allowlist, not another
denylist; `move_path`'s own pre-existing unit tests keep passing unmodified because
`allowed_roots=None` (their default) skips only the NEW containment layer, not the canonical
resolution or the pre-existing denylist.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import core.os_consent_gate as consent_gate
from core.machine_file_ops import move_path
from core.runtime_execution_tools import execute_runtime_tool


def _allow(reason: str) -> bool:
    return True


class TraversalMatrixTests(unittest.TestCase):
    """Every case ANVIL named: .., symlinks, symlink ancestors, absolute outside paths, relative
    escapes, and a nonexistent leaf beneath an outside ancestor -- all must fail before the move,
    driven through the real public dispatcher (`execute_runtime_tool`), not the bare function."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.outside = self.root / "outside"
        self.outside.mkdir()
        self.source_context = {"workspace": str(self.workspace)}
        consent_gate.set_consent_override_for_tests(_allow)
        self.addCleanup(consent_gate.set_consent_override_for_tests, None)

    def _secret(self, name: str = "secret.txt") -> Path:
        secret = self.workspace / name
        secret.write_text("top secret", encoding="utf-8")
        return secret

    def test_dotdot_traversal_escape_is_blocked(self) -> None:
        secret = self._secret()
        dest = f"{self.workspace}/../outside/exfil.txt"
        result = execute_runtime_tool(
            "machine.move_path", {"source": str(secret), "destination": dest}, source_context=self.source_context
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "blocked_outside_scope")
        self.assertTrue(secret.exists())
        self.assertFalse((self.outside / "exfil.txt").exists())

    def test_symlinked_destination_leaf_escape_is_blocked(self) -> None:
        """The destination NAME itself is a symlink pointing outside the workspace."""
        secret = self._secret()
        real_outside_target = self.outside / "real_target.txt"
        symlink_dest = self.workspace / "link_out.txt"
        symlink_dest.symlink_to(real_outside_target)
        result = execute_runtime_tool(
            "machine.move_path",
            {"source": str(secret), "destination": str(symlink_dest)},
            source_context=self.source_context,
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "blocked_outside_scope")
        self.assertTrue(secret.exists())
        self.assertFalse(real_outside_target.exists())

    def test_symlinked_ancestor_directory_escape_is_blocked(self) -> None:
        """An intermediate directory in the destination path is a symlink to outside -- the
        escape is in an ANCESTOR, not the leaf name."""
        secret = self._secret()
        outside_subdir = self.outside / "attacker_dir"
        outside_subdir.mkdir()
        symlinked_ancestor = self.workspace / "looks_local"
        symlinked_ancestor.symlink_to(outside_subdir)
        dest = symlinked_ancestor / "exfil.txt"
        result = execute_runtime_tool(
            "machine.move_path", {"source": str(secret), "destination": str(dest)}, source_context=self.source_context
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "blocked_outside_scope")
        self.assertTrue(secret.exists())
        self.assertFalse((outside_subdir / "exfil.txt").exists())

    def test_absolute_outside_path_is_blocked(self) -> None:
        secret = self._secret()
        dest = self.outside / "exfil.txt"
        result = execute_runtime_tool(
            "machine.move_path", {"source": str(secret), "destination": str(dest)}, source_context=self.source_context
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "blocked_outside_scope")
        self.assertTrue(secret.exists())
        self.assertFalse(dest.exists())

    def test_relative_escape_is_blocked(self) -> None:
        """A bare relative destination resolves against the process CWD, not the workspace --
        it must not land somewhere the workspace/safe-root containment cannot see."""
        secret = self._secret()
        original_cwd = os.getcwd()
        os.chdir(str(self.root))
        try:
            result = execute_runtime_tool(
                "machine.move_path",
                {"source": str(secret), "destination": "outside/exfil.txt"},
                source_context=self.source_context,
            )
        finally:
            os.chdir(original_cwd)
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "blocked_outside_scope")
        self.assertTrue(secret.exists())
        self.assertFalse((self.outside / "exfil.txt").exists())

    def test_nonexistent_leaf_beneath_an_outside_ancestor_is_blocked(self) -> None:
        """The destination's own parent directory does not exist yet -- containment must still
        catch the escape by resolving the ancestor chain, not defer to the (also correct, but
        different) 'destination folder does not exist' check."""
        secret = self._secret()
        dest = self.outside / "brand_new_dir" / "exfil.txt"
        self.assertFalse(dest.parent.exists())
        result = execute_runtime_tool(
            "machine.move_path", {"source": str(secret), "destination": str(dest)}, source_context=self.source_context
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "blocked_outside_scope")
        self.assertTrue(secret.exists())

    def test_in_workspace_move_still_succeeds(self) -> None:
        secret = self._secret()
        dest = self.workspace / "subdir" / "moved.txt"
        dest.parent.mkdir()
        result = execute_runtime_tool(
            "machine.move_path", {"source": str(secret), "destination": str(dest)}, source_context=self.source_context
        )
        self.assertTrue(result.ok, result.response_text)
        self.assertEqual(result.status, "moved")
        self.assertTrue(dest.exists())
        self.assertFalse(secret.exists())

    def test_move_into_a_safe_machine_root_also_succeeds(self) -> None:
        """The allowed scope is the workspace PLUS the safe machine roots, matching the sibling
        read/write lanes -- not the workspace alone."""
        secret = self._secret()
        with mock.patch("core.runtime_execution_tools._machine_home", lambda: self.root):
            desktop = self.root / "Desktop"
            desktop.mkdir()
            dest = desktop / "moved.txt"
            result = execute_runtime_tool(
                "machine.move_path",
                {"source": str(secret), "destination": str(dest)},
                source_context=self.source_context,
            )
        self.assertTrue(result.ok, result.response_text)
        self.assertTrue(dest.exists())


class ConsentOrderingTests(unittest.TestCase):
    """Consent happens only AFTER safe canonical resolution, and the prompt names the resolved
    path -- an unresolved escape string is not what a human is asked to confirm."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.outside = self.root / "outside"
        self.outside.mkdir()

    def test_consent_is_never_requested_for_a_blocked_escape(self) -> None:
        secret = self.workspace / "secret.txt"
        secret.write_text("x", encoding="utf-8")
        consent_calls: list[str] = []

        def _spy(reason: str) -> bool:
            consent_calls.append(reason)
            return True

        result = move_path(
            str(secret),
            f"{self.workspace}/../outside/exfil.txt",
            consent_fn=_spy,
            allowed_roots=[self.workspace],
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "blocked_outside_scope")
        self.assertEqual(consent_calls, [], "consent must not be requested before containment passes")

    def test_consent_prompt_names_the_resolved_path_not_the_raw_escape_string(self) -> None:
        secret = self.workspace / "secret.txt"
        secret.write_text("x", encoding="utf-8")
        dest = self.workspace / "sub" / "moved.txt"
        dest.parent.mkdir()
        consent_calls: list[str] = []

        def _spy(reason: str) -> bool:
            consent_calls.append(reason)
            return True

        result = move_path(
            str(secret), f"{self.workspace}/./sub/moved.txt", consent_fn=_spy, allowed_roots=[self.workspace]
        )
        self.assertTrue(result.ok, result.message)
        self.assertEqual(len(consent_calls), 1)
        # The raw string had a "./" segment in it; the prompt must show the canonicalized form.
        self.assertNotIn("/./", consent_calls[0])
        self.assertIn(str(dest.resolve()), consent_calls[0])


class MutationSabotageTests(unittest.TestCase):
    """Mandatory mutation 5: remove destination root validation -- the real escape test must
    turn RED, driven through the real dispatcher (`machine.move_path`'s real callable seam)."""

    def test_removing_destination_root_validation_turns_the_escape_test_red(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            outside = root / "outside"
            outside.mkdir()
            secret = workspace / "secret.txt"
            secret.write_text("top secret", encoding="utf-8")
            source_context = {"workspace": str(workspace)}
            consent_gate.set_consent_override_for_tests(_allow)
            try:
                real_within = __import__(
                    "core.machine_file_ops", fromlist=["_within_allowed_roots"]
                )._within_allowed_roots

                def _source_only_validation(resolved, allowed_roots):
                    # Sabotage: only the SOURCE is checked against allowed_roots; the
                    # destination is waved through regardless -- the exact shape of the
                    # original defect (containment existed for the denylist, never for a
                    # positive destination boundary).
                    return True

                with mock.patch(
                    "core.machine_file_ops._within_allowed_roots",
                    side_effect=lambda resolved, allowed_roots: (
                        real_within(resolved, allowed_roots)
                        if resolved == secret.resolve()
                        else _source_only_validation(resolved, allowed_roots)
                    ),
                ):
                    dest = outside / "exfil.txt"
                    result = execute_runtime_tool(
                        "machine.move_path",
                        {"source": str(secret), "destination": str(dest)},
                        source_context=source_context,
                    )
            finally:
                consent_gate.set_consent_override_for_tests(None)

            self.assertTrue(result.ok, "sabotage did not reproduce the F6 escape")
            self.assertTrue(dest.exists(), "sabotage should have let the file escape the workspace")
            self.assertFalse(secret.exists())


# --------------------------------------------------------------------------------------
# ANVIL, final round, item 2: behavior is already correct (an outside SOURCE moved into the
# workspace is already refused), but no test named that specific direction -- ANVIL's mutation
# showed all 24 pre-existing tests staying green while removing SOURCE validation alone would
# let an outside file be pulled into the workspace and its original location destroyed. This
# closes that test gap symmetrically with the destination-side coverage above.
# --------------------------------------------------------------------------------------


class SourceContainmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.outside = self.root / "outside"
        self.outside.mkdir()
        self.source_context = {"workspace": str(self.workspace)}
        consent_gate.set_consent_override_for_tests(_allow)
        self.addCleanup(consent_gate.set_consent_override_for_tests, None)

    def test_an_outside_source_cannot_be_pulled_into_the_workspace(self) -> None:
        outside_file = self.outside / "not_yours.txt"
        outside_file.write_text("belongs outside", encoding="utf-8")
        dest = self.workspace / "pulled_in.txt"
        result = execute_runtime_tool(
            "machine.move_path",
            {"source": str(outside_file), "destination": str(dest)},
            source_context=self.source_context,
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "blocked_outside_scope")
        self.assertTrue(
            outside_file.exists(), "the outside file's original location must survive untouched"
        )
        self.assertFalse(dest.exists())


class SourceContainmentMutationSabotageTests(unittest.TestCase):
    """Mandatory mutation 4: remove SOURCE validation only (destination validation intact) --
    the source-containment test must turn RED: an outside file gets pulled into the workspace
    and its original location is destroyed, exactly as ANVIL demonstrated (24/24 pre-existing
    tests stayed green under this exact sabotage, which is precisely why it needed a named test)."""

    def test_removing_source_root_validation_turns_the_source_containment_test_red(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            outside = root / "outside"
            outside.mkdir()
            outside_file = outside / "not_yours.txt"
            outside_file.write_text("belongs outside", encoding="utf-8")
            source_context = {"workspace": str(workspace)}
            consent_gate.set_consent_override_for_tests(_allow)
            try:
                real_within = __import__(
                    "core.machine_file_ops", fromlist=["_within_allowed_roots"]
                )._within_allowed_roots

                def _dest_only_validation(resolved, allowed_roots):
                    # Sabotage: only the DESTINATION is checked against allowed_roots; the
                    # source is waved through regardless -- the mirror image of the F6
                    # destination-only sabotage above, and the exact shape ANVIL's mutation
                    # proved 24/24 pre-existing tests could not detect.
                    return True

                dest = workspace / "pulled_in.txt"
                with mock.patch(
                    "core.machine_file_ops._within_allowed_roots",
                    side_effect=lambda resolved, allowed_roots: (
                        real_within(resolved, allowed_roots)
                        if resolved == dest.resolve()
                        else _dest_only_validation(resolved, allowed_roots)
                    ),
                ):
                    result = execute_runtime_tool(
                        "machine.move_path",
                        {"source": str(outside_file), "destination": str(dest)},
                        source_context=source_context,
                    )
            finally:
                consent_gate.set_consent_override_for_tests(None)

            self.assertTrue(result.ok, "sabotage did not reproduce the source-escape symptom")
            self.assertTrue(dest.exists(), "sabotage should have pulled the outside file in")
            self.assertFalse(
                outside_file.exists(),
                "sabotage should have destroyed the file's original outside location",
            )


if __name__ == "__main__":
    unittest.main()
