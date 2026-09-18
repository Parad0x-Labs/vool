"""TOOLSMITH D15 repair: project-bound reads must not leak sibling projects.

Confirmed 2026-08-07: a chat bound to one project could `machine.list_directory` its own parent
folder (in practice `~/Desktop`, since that is where this project and its sibling worktrees all
live) and see every OTHER project repository sitting next to it, purely because Desktop/Downloads/
Documents are globally "safe" roots for the machine lane. A project binding bought no read
isolation one directory up -- `workspace.*` tools were already correctly confined to the bound
project, but nothing stopped an implicit, bare "what's around me" browse from routing through
`machine.list_directory` instead and enumerating siblings by name.

The fix (`_sibling_projects_to_hide` in `core/runtime_execution_tools.py`) hides OTHER git-project
directories from a listing only when the directory being listed is exactly the bound workspace's
own parent (the implicit case). It does not touch `workspace.*` containment (already correct, and
regression-locked below) and does not refuse an EXPLICIT request that names a sibling project's
path directly -- that is a different, allowed question.
"""
from __future__ import annotations

import contextlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.runtime_execution_tools import (
    _machine_find_file,
    _machine_find_folder,
    _machine_find_largest,
    execute_runtime_tool,
)


def _make_git_project(root: Path, name: str, *, extra_file: str | None = None) -> Path:
    project = root / name
    (project / ".git").mkdir(parents=True)
    if extra_file:
        (project / extra_file).write_text(f"secret contents of {name}\n", encoding="utf-8")
    return project


class ImplicitWorkspaceToolsNeverSurfaceSiblingProjectTests(unittest.TestCase):
    """Regression lock: the existing workspace-scoped tools were already correctly confined.
    This proves it, so the D15 fix is not mistaken for something these tools also needed."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.desktop = Path(self._tmp.name) / "Desktop"
        self.desktop.mkdir()
        self.project_a = _make_git_project(self.desktop, "project-a", extra_file="a_secret.txt")
        self.project_b = _make_git_project(self.desktop, "project-b", extra_file="b_secret.txt")
        (self.project_a / "notes.txt").write_text("hello from A\n", encoding="utf-8")
        self.source_context = {"workspace": str(self.project_a)}

    def test_implicit_list_files_never_surfaces_project_b(self) -> None:
        result = execute_runtime_tool("workspace.list_files", {}, source_context=self.source_context)
        assert result is not None and result.ok
        self.assertNotIn("project-b", result.response_text)
        self.assertNotIn("b_secret", result.response_text)

    def test_implicit_search_text_never_surfaces_project_b(self) -> None:
        result = execute_runtime_tool(
            "workspace.search_text", {"query": "secret"}, source_context=self.source_context
        )
        assert result is not None
        self.assertNotIn("project-b", result.response_text)
        self.assertNotIn("b_secret", result.response_text)

    def test_implicit_identity_never_surfaces_project_b(self) -> None:
        result = execute_runtime_tool("workspace.identity", {}, source_context=self.source_context)
        assert result is not None and result.ok
        self.assertNotIn("project-b", result.response_text)


class MachineListDirectoryProjectIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        self.desktop = self.home / "Desktop"
        self.desktop.mkdir()
        self.project_a = _make_git_project(self.desktop, "project-a")
        self.project_b = _make_git_project(self.desktop, "project-b")
        (self.desktop / "notes.txt").write_text("not a project\n", encoding="utf-8")
        self.source_context = {"workspace": str(self.project_a)}
        patcher = mock.patch("core.runtime_execution_tools._machine_home", lambda: self.home)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_implicit_bare_desktop_listing_hides_sibling_project(self) -> None:
        result = execute_runtime_tool(
            "machine.list_directory", {"path": "~/Desktop"}, source_context=self.source_context
        )
        assert result is not None and result.ok
        self.assertIn("project-a", result.response_text)
        self.assertIn("notes.txt", result.response_text)
        self.assertNotIn("project-b", result.response_text)
        self.assertEqual(result.details.get("hidden_sibling_projects"), 1)

    def test_implicit_count_only_desktop_listing_also_hides_sibling_project(self) -> None:
        result = execute_runtime_tool(
            "machine.list_directory",
            {"path": "~/Desktop", "count_only": True},
            source_context=self.source_context,
        )
        assert result is not None and result.ok
        # 2 visible entries: project-a and notes.txt. project-b does not count.
        self.assertEqual(result.details.get("count"), 2)
        self.assertEqual(result.details.get("hidden_sibling_projects"), 1)

    def test_explicit_request_for_the_sibling_project_still_succeeds(self) -> None:
        """Naming project-b's own path directly is a different, allowed question -- not refused,
        not silently emptied. It lists exactly what is there."""
        (self.project_b / "readme.txt").write_text("hi\n", encoding="utf-8")
        result = execute_runtime_tool(
            "machine.list_directory",
            {"path": "~/Desktop/project-b"},
            source_context=self.source_context,
        )
        assert result is not None and result.ok
        self.assertEqual(result.status, "executed")
        self.assertIn("readme.txt", result.response_text)

    def test_unbound_context_lists_desktop_without_hiding_anything(self) -> None:
        """No workspace bound under Desktop at all -> nothing is a 'sibling', nothing is hidden."""
        result = execute_runtime_tool(
            "machine.list_directory",
            {"path": "~/Desktop"},
            source_context={"workspace": str(self.home / "elsewhere" / "not-under-desktop")},
        )
        assert result is not None and result.ok
        self.assertIn("project-a", result.response_text)
        self.assertIn("project-b", result.response_text)
        self.assertEqual(result.details.get("hidden_sibling_projects"), 0)


class MutationSabotageTests(unittest.TestCase):
    """Mutation 3: restore broad implicit Desktop reads in project-bound context -- the
    sibling-project isolation test must turn RED."""

    def test_restoring_broad_implicit_reads_turns_the_isolation_test_red(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            desktop = home / "Desktop"
            desktop.mkdir()
            project_a = _make_git_project(desktop, "project-a")
            _make_git_project(desktop, "project-b")
            source_context = {"workspace": str(project_a)}

            with mock.patch("core.runtime_execution_tools._machine_home", lambda: home), mock.patch(
                "core.runtime_execution_tools._sibling_projects_to_hide",
                lambda target, *, workspace_root: set(),
            ):
                result = execute_runtime_tool(
                    "machine.list_directory", {"path": "~/Desktop"}, source_context=source_context
                )
            assert result is not None and result.ok
            self.assertIn(
                "project-b",
                result.response_text,
                "sabotage did not reproduce the D15 symptom -- sibling project should have leaked",
            )


# --------------------------------------------------------------------------------------
# ANVIL F3: implicit discovery via find_folder/find_file must not surface a sibling project.
# machine.list_directory alone was not the whole leak -- discovery-by-name could rediscover
# project-b even after list_directory stopped naming it.
# --------------------------------------------------------------------------------------


class MachineFindFolderProjectIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        self.desktop = self.home / "Desktop"
        self.desktop.mkdir()
        self.project_a = _make_git_project(self.desktop, "project-a")
        self.project_b = _make_git_project(self.desktop, "project-b")
        self.source_context = {"workspace": str(self.project_a)}
        home_patch = mock.patch("core.runtime_execution_tools._machine_home", lambda: self.home)
        home_patch.start()
        self.addCleanup(home_patch.stop)
        # `machine.find_folder` walks `machine_diagnostics.disk_usage()`'s mounts, not the three
        # safe roots -- scope it to this fixture's Desktop the same way the pre-existing firmlink
        # test does, so the BFS is small, fast, and deterministic instead of walking the real disk.
        disk_patch = mock.patch(
            "core.machine_diagnostics.disk_usage", lambda *a, **k: [{"mount": str(self.desktop)}]
        )
        disk_patch.start()
        self.addCleanup(disk_patch.stop)

    def test_implicit_find_folder_never_rediscovers_sibling_project(self) -> None:
        result = execute_runtime_tool(
            "machine.find_folder", {"name": "project-b"}, source_context=self.source_context
        )
        assert result is not None
        self.assertNotIn(str(self.project_b), result.response_text)
        self.assertEqual(result.details.get("matches"), [])

    def test_find_folder_still_finds_the_bound_project_itself(self) -> None:
        """Isolation must not turn into total confinement: searching for the BOUND project's own
        name still works."""
        result = execute_runtime_tool(
            "machine.find_folder", {"name": "project-a"}, source_context=self.source_context
        )
        assert result is not None and result.ok
        self.assertIn(str(self.project_a), result.response_text)

    def test_no_workspace_root_at_all_finds_both(self) -> None:
        """`workspace_root=None` is the direct/legacy-caller default (nothing is bound, so
        nothing is isolated from) -- distinct from going through the real dispatcher with
        `source_context=None`, which still resolves to a concrete CWD-based workspace_root and
        would correctly keep isolating from THAT. Exercised directly since there is no `source_
        context` shape that reaches a real `None` workspace_root through `execute_runtime_tool`.
        """
        result = _machine_find_folder({"name": "project-b"}, workspace_root=None)
        assert result.ok
        self.assertIn(str(self.project_b), result.response_text)


class MachineFindFileProjectIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        self.desktop = self.home / "Desktop"
        self.desktop.mkdir()
        self.project_a = _make_git_project(self.desktop, "project-a")
        self.project_b = _make_git_project(self.desktop, "project-b", extra_file="b_secret.txt")
        self.source_context = {"workspace": str(self.project_a)}
        patcher = mock.patch("core.runtime_execution_tools._machine_home", lambda: self.home)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_implicit_find_file_never_discovers_a_file_inside_the_sibling_project(self) -> None:
        result = execute_runtime_tool(
            "machine.find_file", {"name": "b_secret.txt"}, source_context=self.source_context
        )
        assert result is not None
        # The not-found message legitimately echoes the searched-for NAME back ("No file
        # matching 'b_secret.txt' found...") -- what must never appear is the actual PATH,
        # proof the walk never reached inside project-b.
        self.assertNotIn(str(self.project_b / "b_secret.txt"), result.response_text)
        self.assertEqual(result.details.get("matches"), [])

    def test_a_file_read_from_an_explicit_path_to_the_sibling_project_is_unaffected(self) -> None:
        """machine.read_file needs no change: discovery is what leaked, not explicit access."""
        result = execute_runtime_tool(
            "machine.read_file",
            {"path": str(self.project_b / "b_secret.txt")},
            source_context=self.source_context,
        )
        assert result is not None and result.ok
        self.assertIn("secret contents of project-b", result.response_text)

    def test_no_workspace_root_at_all_still_finds_it(self) -> None:
        """`workspace_root=None` is the direct/legacy-caller default -- see the equivalent
        find_folder test for why this isn't exercised via `source_context=None` through the real
        dispatcher (that still resolves to a concrete CWD-based workspace_root)."""
        result = _machine_find_file({"name": "b_secret.txt"}, workspace_root=None)
        assert result.ok
        self.assertEqual(
            result.details.get("matches"), [str((self.project_b / "b_secret.txt").resolve())]
        )


class FindFolderFindFileMutationSabotageTests(unittest.TestCase):
    """Mutation for F3: neutralizing `_is_other_git_project` reproduces the exact rediscovery
    the fix exists to close."""

    def test_neutralizing_is_other_git_project_turns_find_folder_red(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            desktop = home / "Desktop"
            desktop.mkdir()
            project_a = _make_git_project(desktop, "project-a")
            project_b = _make_git_project(desktop, "project-b")
            source_context = {"workspace": str(project_a)}

            with mock.patch("core.runtime_execution_tools._machine_home", lambda: home), mock.patch(
                "core.machine_diagnostics.disk_usage", lambda *a, **k: [{"mount": str(desktop)}]
            ), mock.patch(
                "core.runtime_execution_tools._is_other_git_project", lambda *a, **k: False
            ):
                result = execute_runtime_tool(
                    "machine.find_folder", {"name": "project-b"}, source_context=source_context
                )
            assert result is not None and result.ok
            self.assertIn(
                str(project_b),
                result.response_text,
                "sabotage did not reproduce the F3 symptom -- find_folder should have rediscovered it",
            )


# --------------------------------------------------------------------------------------
# ANVIL, final round, item 3: `_has_git_marker` already protects `_is_other_git_project`'s scan
# loops in `_machine_find_folder`/`_machine_find_file` (R4's guard covers all three call sites),
# but only `machine.list_directory` had a NAMED test proving an unprobeable entry does not abort
# the scan of sibling entries. This closes that same coverage gap for the search tools.
# --------------------------------------------------------------------------------------


@contextlib.contextmanager
def _name_sorted_scandir(real_scandir, path):
    """`os.scandir` gives no ordering guarantee, so a fixture relying on "unprobeable entry
    first, valid entry later" needs one imposed deterministically -- otherwise the test's outcome
    would depend on filesystem/OS-specific iteration order, exactly the kind of flake that makes
    a security regression lock worthless. Delegates to the real `scandir` and only reorders."""
    with real_scandir(path) as it:
        yield sorted(it, key=lambda entry: entry.name)


class FindFolderOSErrorContinuationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        self.desktop = self.home / "Desktop"
        self.desktop.mkdir()
        self.project_a = _make_git_project(self.desktop, "project-a")
        # Named to sort BEFORE the valid target under the deterministic ordering below.
        self.unprobeable = self.desktop / "aaa_unprobeable_folder"
        self.unprobeable.mkdir()
        self.target = self.desktop / "zzz_target_folder"
        self.target.mkdir()
        self.source_context = {"workspace": str(self.project_a)}
        home_patch = mock.patch("core.runtime_execution_tools._machine_home", lambda: self.home)
        home_patch.start()
        self.addCleanup(home_patch.stop)
        disk_patch = mock.patch(
            "core.machine_diagnostics.disk_usage", lambda *a, **k: [{"mount": str(self.desktop)}]
        )
        disk_patch.start()
        self.addCleanup(disk_patch.stop)
        real_scandir = os.scandir
        scandir_patch = mock.patch(
            "core.runtime_execution_tools.os.scandir",
            side_effect=lambda path: _name_sorted_scandir(real_scandir, path),
        )
        scandir_patch.start()
        self.addCleanup(scandir_patch.stop)

    def _flaky_exists(self, real_exists):
        unprobeable = self.unprobeable

        def _exists(path_self):
            if path_self.parent == unprobeable and path_self.name == ".git":
                raise OSError("simulated: permission denied probing .git")
            return real_exists(path_self)

        return _exists

    def test_unprobeable_entry_does_not_abort_the_search_for_a_later_valid_entry(self) -> None:
        real_exists = Path.exists
        with mock.patch.object(Path, "exists", self._flaky_exists(real_exists)):
            result = execute_runtime_tool(
                "machine.find_folder",
                {"name": "zzz_target_folder"},
                source_context=self.source_context,
            )
        assert result is not None and result.ok, getattr(result, "response_text", result)
        self.assertIn(str(self.target), result.response_text)


class FindFolderOSErrorMutationSabotageTests(unittest.TestCase):
    """Mandatory mutation 5: remove the guarded git-marker handling -- the search-continuation
    test must turn RED."""

    def test_removing_the_guard_turns_the_search_continuation_test_red(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            desktop = home / "Desktop"
            desktop.mkdir()
            project_a = _make_git_project(desktop, "project-a")
            unprobeable = desktop / "aaa_unprobeable_folder"
            unprobeable.mkdir()
            target = desktop / "zzz_target_folder"
            target.mkdir()
            source_context = {"workspace": str(project_a)}

            def _unguarded_git_marker(path):
                # Sabotage: the exact pre-R4 shape -- no try/except around the probe.
                if path == unprobeable:
                    raise OSError("simulated: permission denied probing .git")
                return (path / ".git").exists()

            real_scandir = os.scandir
            with mock.patch("core.runtime_execution_tools._machine_home", lambda: home), mock.patch(
                "core.machine_diagnostics.disk_usage", lambda *a, **k: [{"mount": str(desktop)}]
            ), mock.patch(
                "core.runtime_execution_tools._has_git_marker", side_effect=_unguarded_git_marker
            ), mock.patch(
                "core.runtime_execution_tools.os.scandir",
                side_effect=lambda path: _name_sorted_scandir(real_scandir, path),
            ):
                try:
                    result = execute_runtime_tool(
                        "machine.find_folder",
                        {"name": "zzz_target_folder"},
                        source_context=source_context,
                    )
                except OSError:
                    result = None

            found = bool(result is not None and result.ok and str(target) in result.response_text)
            self.assertFalse(
                found,
                "sabotage did not reproduce the scan-abort symptom -- the later valid entry "
                "should have been lost (either an uncaught OSError or a truncated search)",
            )


class FindFileOSErrorContinuationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        self.desktop = self.home / "Desktop"
        self.desktop.mkdir()
        self.project_a = _make_git_project(self.desktop, "project-a")
        self.unprobeable = self.desktop / "aaa_unprobeable_folder"
        self.unprobeable.mkdir()
        self.target_dir = self.desktop / "zzz_container"
        self.target_dir.mkdir()
        self.target_file = self.target_dir / "target_file.txt"
        self.target_file.write_text("findable", encoding="utf-8")
        self.source_context = {"workspace": str(self.project_a)}
        home_patch = mock.patch("core.runtime_execution_tools._machine_home", lambda: self.home)
        home_patch.start()
        self.addCleanup(home_patch.stop)
        real_scandir = os.scandir
        scandir_patch = mock.patch(
            "core.runtime_execution_tools.os.scandir",
            side_effect=lambda path: _name_sorted_scandir(real_scandir, path),
        )
        scandir_patch.start()
        self.addCleanup(scandir_patch.stop)

    def test_unprobeable_entry_does_not_abort_the_search_for_a_later_valid_entry(self) -> None:
        # `_machine_find_file` walks `_safe_machine_roots()`, which resolves through macOS's
        # /var -> /private/var alias -- the fixture's own paths must be compared in that same
        # resolved form, or the flaky-exists patch below silently never matches anything.
        unprobeable = self.unprobeable.resolve()
        real_exists = Path.exists

        def _flaky_exists(path_self):
            if path_self.parent == unprobeable and path_self.name == ".git":
                raise OSError("simulated: permission denied probing .git")
            return real_exists(path_self)

        with mock.patch.object(Path, "exists", _flaky_exists):
            result = execute_runtime_tool(
                "machine.find_file", {"name": "target_file.txt"}, source_context=self.source_context
            )
        assert result is not None and result.ok, getattr(result, "response_text", result)
        self.assertIn(str(self.target_file.resolve()), result.response_text)


class FindFileOSErrorMutationSabotageTests(unittest.TestCase):
    """Mandatory mutation 5 (find_file side): remove the guarded git-marker handling -- the
    search-continuation test must turn RED."""

    def test_removing_the_guard_turns_the_search_continuation_test_red(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            desktop = home / "Desktop"
            desktop.mkdir()
            project_a = _make_git_project(desktop, "project-a")
            unprobeable = desktop / "aaa_unprobeable_folder"
            unprobeable.mkdir()
            unprobeable_resolved = unprobeable.resolve()
            target_dir = desktop / "zzz_container"
            target_dir.mkdir()
            target_file = target_dir / "target_file.txt"
            target_file.write_text("findable", encoding="utf-8")
            source_context = {"workspace": str(project_a)}

            def _unguarded_git_marker(path):
                if path == unprobeable_resolved:
                    raise OSError("simulated: permission denied probing .git")
                return (path / ".git").exists()

            real_scandir = os.scandir
            with mock.patch("core.runtime_execution_tools._machine_home", lambda: home), mock.patch(
                "core.runtime_execution_tools._has_git_marker", side_effect=_unguarded_git_marker
            ), mock.patch(
                "core.runtime_execution_tools.os.scandir",
                side_effect=lambda path: _name_sorted_scandir(real_scandir, path),
            ):
                try:
                    result = execute_runtime_tool(
                        "machine.find_file",
                        {"name": "target_file.txt"},
                        source_context=source_context,
                    )
                except OSError:
                    result = None

            found = bool(
                result is not None
                and result.ok
                and str(target_file.resolve()) in result.response_text
            )
            self.assertFalse(
                found,
                "sabotage did not reproduce the scan-abort symptom -- the later valid entry "
                "should have been lost (either an uncaught OSError or a truncated search)",
            )


# --------------------------------------------------------------------------------------
# ANVIL F4: canonical ancestor identity, not raw string equality -- nested projects, a `.git`
# FILE (worktree), and a symlinked workspace root must all be handled correctly.
# --------------------------------------------------------------------------------------


class NestedAndCanonicalIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        self.desktop = self.home / "Desktop"
        self.desktop.mkdir()
        patcher = mock.patch("core.runtime_execution_tools._machine_home", lambda: self.home)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_nested_bound_project_hides_top_level_siblings_when_listing_a_grandparent(self) -> None:
        """Desktop/project-a, Desktop/project-b, Desktop/group/project-c (bound). Listing
        Desktop itself -- a real ancestor, just not the immediate parent -- must still hide
        project-a and project-b, while `group/` (on the way to the bound project) stays visible."""
        _make_git_project(self.desktop, "project-a")
        _make_git_project(self.desktop, "project-b")
        group = self.desktop / "group"
        project_c = _make_git_project(group, "project-c")
        source_context = {"workspace": str(project_c)}

        result = execute_runtime_tool(
            "machine.list_directory", {"path": "~/Desktop"}, source_context=source_context
        )
        assert result is not None and result.ok
        self.assertIn("group", result.response_text)
        self.assertNotIn("project-a", result.response_text)
        self.assertNotIn("project-b", result.response_text)
        self.assertEqual(result.details.get("hidden_sibling_projects"), 2)

    def test_listing_the_immediate_parent_of_a_nested_project_is_unaffected(self) -> None:
        group = self.desktop / "group"
        project_c = _make_git_project(group, "project-c")
        source_context = {"workspace": str(project_c)}

        result = execute_runtime_tool(
            "machine.list_directory", {"path": "~/Desktop/group"}, source_context=source_context
        )
        assert result is not None and result.ok
        self.assertIn("project-c", result.response_text)
        self.assertEqual(result.details.get("hidden_sibling_projects"), 0)

    def test_git_worktree_file_is_recognized_the_same_as_a_git_directory(self) -> None:
        """A worktree's `.git` is a FILE containing `gitdir: ...`, not a directory -- must be
        hidden exactly like an ordinary clone."""
        project_a = self.desktop / "project-a"
        project_a.mkdir()
        (project_a / ".git").write_text("gitdir: /elsewhere/.git/worktrees/project-a\n")
        source_context = {"workspace": str(self.desktop / "bound")}
        (self.desktop / "bound" / ".git").mkdir(parents=True)

        result = execute_runtime_tool(
            "machine.list_directory", {"path": "~/Desktop"}, source_context=source_context
        )
        assert result is not None and result.ok
        self.assertNotIn("project-a", result.response_text)
        self.assertEqual(result.details.get("hidden_sibling_projects"), 1)

    def test_symlinked_workspace_root_still_recognizes_itself_when_listing_its_parent(self) -> None:
        """The bound workspace reached through a symlink must still compare equal to itself via
        canonical identity, not be mistaken for an unrelated sibling and hidden."""
        real_project = self.desktop / "real-project"
        (real_project / ".git").mkdir(parents=True)
        symlinked_root = self.desktop / "project-link"
        symlinked_root.symlink_to(real_project)
        source_context = {"workspace": str(symlinked_root)}

        result = execute_runtime_tool(
            "machine.list_directory", {"path": "~/Desktop"}, source_context=source_context
        )
        assert result is not None and result.ok
        self.assertIn("real-project", result.response_text)
        self.assertEqual(result.details.get("hidden_sibling_projects"), 0)

    def test_trailing_slash_on_the_bound_workspace_path_does_not_break_identity(self) -> None:
        project_a = self.desktop / "project-a"
        (project_a / ".git").mkdir(parents=True)
        _make_git_project(self.desktop, "project-b")
        source_context = {"workspace": str(project_a) + "/"}

        result = execute_runtime_tool(
            "machine.list_directory", {"path": "~/Desktop"}, source_context=source_context
        )
        assert result is not None and result.ok
        self.assertIn("project-a", result.response_text)
        self.assertNotIn("project-b", result.response_text)


# --------------------------------------------------------------------------------------
# ANVIL R2, round 3: machine.find_largest was never brought into the project-binding contract.
# list_directory/find_file/find_folder hide sibling projects; find_largest could still return a
# sibling project's name and full paths to files inside it, including a real multi-GB file.
# --------------------------------------------------------------------------------------


class MachineFindLargestProjectIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        self.desktop = self.home / "Desktop"
        self.desktop.mkdir()
        self.project_a = _make_git_project(self.desktop, "project-a")
        self.project_b = _make_git_project(self.desktop, "project-b")
        (self.project_a / "small.bin").write_bytes(b"x" * 1024)
        (self.project_b / "huge.bin").write_bytes(b"x" * (2 * 1024 * 1024))
        self.source_context = {"workspace": str(self.project_a)}
        patcher = mock.patch("core.runtime_execution_tools._machine_home", lambda: self.home)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_sibling_project_file_is_excluded_from_the_ranking(self) -> None:
        result = execute_runtime_tool(
            "machine.find_largest",
            {"drive": str(self.desktop), "kind": "files", "top": 5},
            source_context=self.source_context,
        )
        assert result is not None and result.ok
        entry_paths = [entry["path"] for entry in result.details.get("entries", [])]
        self.assertNotIn(str((self.project_b / "huge.bin").resolve()), entry_paths)
        self.assertNotIn("huge.bin", result.response_text)
        self.assertGreaterEqual(result.details.get("hidden_other_project_results", 0), 1)

    def test_legitimate_result_inside_the_bound_project_is_preserved(self) -> None:
        """The sibling's huge file must not squeeze out project-a's own (smaller) file by
        occupying a ranked slot -- over-fetching before filtering is what this proves."""
        result = execute_runtime_tool(
            "machine.find_largest",
            {"drive": str(self.desktop), "kind": "files", "top": 5},
            source_context=self.source_context,
        )
        assert result is not None and result.ok
        entry_paths = [entry["path"] for entry in result.details.get("entries", [])]
        self.assertIn(str((self.project_a / "small.bin").resolve()), entry_paths)

    def test_no_binding_still_returns_the_sibling_result(self) -> None:
        result = _machine_find_largest(
            {"drive": str(self.desktop), "kind": "files", "top": 5}, workspace_root=None
        )
        assert result.ok
        entry_paths = [entry["path"] for entry in result.details.get("entries", [])]
        self.assertIn(str((self.project_b / "huge.bin").resolve()), entry_paths)


class FindLargestMutationSabotageTests(unittest.TestCase):
    """Mandatory mutation 3: remove find_largest's project binding -- the sibling-disclosure
    test must turn RED."""

    def test_removing_the_project_binding_turns_the_isolation_test_red(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            desktop = home / "Desktop"
            desktop.mkdir()
            project_a = _make_git_project(desktop, "project-a")
            project_b = _make_git_project(desktop, "project-b")
            (project_b / "huge.bin").write_bytes(b"x" * (2 * 1024 * 1024))
            source_context = {"workspace": str(project_a)}

            with mock.patch("core.runtime_execution_tools._machine_home", lambda: home), mock.patch(
                "core.runtime_execution_tools._is_under_other_git_project", lambda *a, **k: False
            ):
                result = execute_runtime_tool(
                    "machine.find_largest",
                    {"drive": str(desktop), "kind": "files", "top": 5},
                    source_context=source_context,
                )
            assert result is not None and result.ok
            self.assertIn(
                "huge.bin",
                result.response_text,
                "sabotage did not reproduce the R2 symptom -- sibling file should have leaked",
            )


# --------------------------------------------------------------------------------------
# ANVIL, final root-floor round, HIGH: `machine.find_largest` never validated `requested_drive`
# at all. `machine.list_directory`/`machine.read_file` correctly refuse `/etc`, `/var/log`,
# `~/.ssh`; an explicit `find_largest` call with those SAME paths scanned them for real, from a
# chat bound to an unrelated Desktop project. Fixed by routing `requested_drive` through
# `_resolve_find_largest_root`, which reuses `_resolve_machine_directory` -- the exact contract
# the sibling read/list tools already enforce -- while still allowing the tool's own
# pre-existing legitimate targets (the whole home directory, a bare Windows drive letter) that
# `list_directory`/`read_file` never needed. Project-binding filtering layers ON TOP, unchanged.
# --------------------------------------------------------------------------------------


class MachineFindLargestSafeRootFloorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        self.desktop = self.home / "Desktop"
        self.desktop.mkdir()
        self.project_a = _make_git_project(self.desktop, "project-a")
        (self.project_a / "small.bin").write_bytes(b"x" * 1024)
        self.source_context = {"workspace": str(self.project_a)}
        patcher = mock.patch("core.runtime_execution_tools._machine_home", lambda: self.home)
        patcher.start()
        self.addCleanup(patcher.stop)
        # A real, sensitive-looking area outside every safe root -- /etc and /var/log do not
        # exist under a scratch $HOME, so build equivalents inside this fixture's home instead.
        self.etc = self.home / "etc"
        self.etc.mkdir()
        (self.etc / "services").write_text("real system data\n", encoding="utf-8")
        self.ssh = self.home / ".ssh"
        self.ssh.mkdir()
        (self.ssh / "known_hosts").write_text("real ssh data\n", encoding="utf-8")

    @mock.patch("core.runtime_execution_tools._machine_home")
    def test_outside_safe_root_paths_are_not_allowed(self, mocked_home) -> None:
        mocked_home.return_value = self.home
        for outside in (str(self.etc), str(self.ssh), "~/.ssh"):
            result = execute_runtime_tool(
                "machine.find_largest",
                {"drive": outside, "kind": "files"},
                source_context=self.source_context,
            )
            self.assertFalse(result.ok, outside)
            self.assertEqual(result.status, "not_allowed", outside)
            self.assertNotIn("services", result.response_text)
            self.assertNotIn("known_hosts", result.response_text)

    def test_allowed_machine_roots_still_work(self) -> None:
        result = execute_runtime_tool(
            "machine.find_largest",
            {"drive": str(self.desktop), "kind": "files"},
            source_context=self.source_context,
        )
        self.assertTrue(result.ok, result.response_text)
        self.assertEqual(result.status, "executed")

    def test_tilde_form_behaves_identically_to_the_canonical_absolute_path(self) -> None:
        canonical = execute_runtime_tool(
            "machine.find_largest",
            {"drive": str(self.desktop), "kind": "files"},
            source_context=self.source_context,
        )
        tilde = execute_runtime_tool(
            "machine.find_largest",
            {"drive": "~/Desktop", "kind": "files"},
            source_context=self.source_context,
        )
        self.assertTrue(tilde.ok, tilde.response_text)
        self.assertEqual(tilde.status, canonical.status)
        self.assertEqual(
            [e["path"] for e in tilde.details["entries"]],
            [e["path"] for e in canonical.details["entries"]],
        )

    def test_explicit_whole_home_still_works(self) -> None:
        """The tool's own pre-existing default (no `drive` at all) already scans the whole home
        directory -- naming it explicitly must not become a new refusal."""
        result = execute_runtime_tool(
            "machine.find_largest",
            {"drive": str(self.home), "kind": "files"},
            source_context=self.source_context,
        )
        self.assertTrue(result.ok, result.response_text)
        self.assertEqual(result.status, "executed")


class FindLargestRootFloorMutationSabotageTests(unittest.TestCase):
    """Mandatory mutation 1: remove find_largest's safe-root resolver -- the outside-root
    refusal test must turn RED."""

    def test_removing_the_safe_root_resolver_turns_the_refusal_test_red(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            desktop = home / "Desktop"
            desktop.mkdir()
            project_a = _make_git_project(desktop, "project-a")
            source_context = {"workspace": str(project_a)}
            etc = home / "etc"
            etc.mkdir()
            (etc / "services").write_text("real system data\n", encoding="utf-8")

            with mock.patch("core.runtime_execution_tools._machine_home", lambda: home), mock.patch(
                "core.runtime_execution_tools._resolve_find_largest_root",
                lambda requested_drive: (requested_drive, ""),
            ):
                result = execute_runtime_tool(
                    "machine.find_largest",
                    {"drive": str(etc), "kind": "files"},
                    source_context=source_context,
                )
            self.assertTrue(
                result.ok, "sabotage did not reproduce the root-floor symptom -- /etc should scan"
            )
            self.assertIn("services", result.response_text)


# --------------------------------------------------------------------------------------
# ANVIL, final round: the FIRST fix over-fetched a fixed 25 raw results before filtering --
# ANVIL demonstrated more than 25 dominating hidden-sibling entries can still under-return
# legitimate results that genuinely exist further down the real ranking. Bounded (not
# unbounded) over-fetch scaling with what was actually requested replaces the fixed constant.
# --------------------------------------------------------------------------------------


class MachineFindLargestUnderReturnTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        self.desktop = self.home / "Desktop"
        self.desktop.mkdir()
        self.project_a = _make_git_project(self.desktop, "project-a")
        self.project_b = _make_git_project(self.desktop, "project-b")
        # 40 dominating hidden-sibling files (> the old fixed 25 over-fetch), each larger than
        # the legitimate project-a files that must still be found further down the ranking.
        for i in range(40):
            (self.project_b / f"huge_{i}.bin").write_bytes(b"x" * (5 * 1024 * 1024 + i))
        self.legit_names = [f"legit_{i}.bin" for i in range(3)]
        for i, name in enumerate(self.legit_names):
            (self.project_a / name).write_bytes(b"x" * (1024 + i))
        self.source_context = {"workspace": str(self.project_a)}
        patcher = mock.patch("core.runtime_execution_tools._machine_home", lambda: self.home)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_requested_count_of_legitimate_results_is_returned_despite_dominant_hidden_siblings(
        self,
    ) -> None:
        result = execute_runtime_tool(
            "machine.find_largest",
            {"drive": str(self.desktop), "kind": "files", "top": 3},
            source_context=self.source_context,
        )
        self.assertTrue(result.ok, result.response_text)
        entry_names = [e["name"] for e in result.details["entries"]]
        self.assertEqual(len(entry_names), 3, entry_names)
        for name in self.legit_names:
            self.assertIn(name, entry_names)
        self.assertEqual(result.details.get("hidden_other_project_results"), 40)


class UnderReturnMutationSabotageTests(unittest.TestCase):
    """Mandatory mutation 2: restore the fixed, insufficient over-fetch (25) -- the under-return
    test must turn RED."""

    def test_restoring_the_fixed_over_fetch_turns_the_under_return_test_red(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            desktop = home / "Desktop"
            desktop.mkdir()
            project_a = _make_git_project(desktop, "project-a")
            project_b = _make_git_project(desktop, "project-b")
            for i in range(40):
                (project_b / f"huge_{i}.bin").write_bytes(b"x" * (5 * 1024 * 1024 + i))
            legit_names = [f"legit_{i}.bin" for i in range(3)]
            for i, name in enumerate(legit_names):
                (project_a / name).write_bytes(b"x" * (1024 + i))
            source_context = {"workspace": str(project_a)}

            with mock.patch("core.runtime_execution_tools._machine_home", lambda: home), mock.patch(
                "core.runtime_execution_tools._find_largest_scan_ceiling", lambda top: 25
            ):
                result = execute_runtime_tool(
                    "machine.find_largest",
                    {"drive": str(desktop), "kind": "files", "top": 3},
                    source_context=source_context,
                )
            # All 25 of the fixed-ceiling's raw slots are consumed by the 40 dominating hidden
            # files, so this can manifest as either a partial success with too few entries, or
            # (as it does here) zero survivors at all -- `ok=False, status="no_results"`. Both
            # are the same under-return symptom this mutation exists to reproduce.
            entry_names = [e["name"] for e in (result.details or {}).get("entries", [])]
            self.assertLess(
                len(entry_names),
                3,
                "sabotage did not reproduce the under-return symptom -- expected fewer than "
                "the 3 requested legitimate results once over-fetch was capped back at 25",
            )


# --------------------------------------------------------------------------------------
# ANVIL R3, round 3: hiding only protected DIRECT children of a protected ancestor. A sibling
# project reachable through one extra non-project container (a level the user can walk into
# without ever naming the sibling project itself) was fully visible and readable.
# --------------------------------------------------------------------------------------


class NestedSiblingProjectEscapeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        self.desktop = self.home / "Desktop"
        self.container = self.desktop / "container"
        self.container.mkdir(parents=True)
        self.project_a = _make_git_project(self.container, "project-a")
        self.sub = self.container / "sub"
        self.project_b = _make_git_project(self.sub, "project-b", extra_file="secret.txt")
        self.source_context = {"workspace": str(self.project_a)}
        patcher = mock.patch("core.runtime_execution_tools._machine_home", lambda: self.home)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_listing_the_container_hides_the_direct_sibling(self) -> None:
        result = execute_runtime_tool(
            "machine.list_directory",
            {"path": "~/Desktop/container"},
            source_context=self.source_context,
        )
        assert result is not None and result.ok
        self.assertIn("project-a", result.response_text)

    def test_listing_one_level_deeper_through_a_non_project_container_still_hides_the_sibling(
        self,
    ) -> None:
        """The exact ANVIL R3 escape: `sub` is not on project-a's ancestor chain at all -- it is
        a SIBLING of project-a's own container -- so the previous ancestor-only check let
        anything inside it through unfiltered."""
        result = execute_runtime_tool(
            "machine.list_directory",
            {"path": "~/Desktop/container/sub"},
            source_context=self.source_context,
        )
        assert result is not None and result.ok
        self.assertNotIn("project-b", result.response_text)
        self.assertEqual(result.details.get("hidden_sibling_projects"), 1)

    def test_current_project_and_its_own_descendants_remain_visible(self) -> None:
        nested = self.project_a / "src" / "deep"
        nested.mkdir(parents=True)
        result = execute_runtime_tool(
            "machine.list_directory",
            {"path": str(self.project_a)},
            source_context=self.source_context,
        )
        assert result is not None and result.ok
        self.assertIn("src", result.response_text)
        self.assertEqual(result.details.get("hidden_sibling_projects"), 0)

    def test_explicit_request_for_the_nested_sibling_still_succeeds(self) -> None:
        result = execute_runtime_tool(
            "machine.list_directory",
            {"path": "~/Desktop/container/sub/project-b"},
            source_context=self.source_context,
        )
        assert result is not None and result.ok
        self.assertIn("secret.txt", result.response_text)


class NestedSiblingMutationSabotageTests(unittest.TestCase):
    """Mandatory mutation 4: restore direct-child-only sibling hiding -- the grandchild/nested-
    sibling test must turn RED."""

    def test_restoring_direct_child_only_hiding_turns_the_nested_test_red(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            desktop = home / "Desktop"
            container = desktop / "container"
            container.mkdir(parents=True)
            project_a = _make_git_project(container, "project-a")
            sub = container / "sub"
            _make_git_project(sub, "project-b")
            source_context = {"workspace": str(project_a)}

            def _direct_child_only_hiding_applies(target, *, workspace_root):
                return workspace_root.parent == target

            with mock.patch("core.runtime_execution_tools._machine_home", lambda: home), mock.patch(
                "core.runtime_execution_tools._hiding_applies", _direct_child_only_hiding_applies
            ):
                result = execute_runtime_tool(
                    "machine.list_directory",
                    {"path": "~/Desktop/container/sub"},
                    source_context=source_context,
                )
            assert result is not None and result.ok
            self.assertIn(
                "project-b",
                result.response_text,
                "sabotage did not reproduce the R3 symptom -- nested sibling should have leaked",
            )


# --------------------------------------------------------------------------------------
# ANVIL R4, round 3: an OSError from a `.git` marker probe (unreadable entry, race with
# deletion, a broken mount) must not abort the scan of SIBLING entries in the same directory.
# --------------------------------------------------------------------------------------


class GitMarkerProbeOSErrorGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        self.desktop = self.home / "Desktop"
        self.desktop.mkdir()
        self.project_a = _make_git_project(self.desktop, "project-a")
        # An entry whose OWN `.git` probe will raise -- simulating an unreadable/unprobeable
        # directory (permission denied, race with deletion, a broken mount).
        self.unprobeable = self.desktop / "unprobeable-entry"
        self.unprobeable.mkdir()
        # A perfectly ordinary, valid entry that must still be returned even though it sorts
        # AFTER the unprobeable one alphabetically.
        self.visible_after = self.desktop / "zzz-valid-entry"
        self.visible_after.mkdir()
        self.source_context = {"workspace": str(self.project_a)}
        patcher = mock.patch("core.runtime_execution_tools._machine_home", lambda: self.home)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_unprobeable_entry_does_not_abort_the_scan_of_later_entries(self) -> None:
        real_exists = Path.exists

        def _flaky_exists(self):
            if self.parent.name == "unprobeable-entry" and self.name == ".git":
                raise OSError("simulated: permission denied probing .git")
            return real_exists(self)

        with mock.patch.object(Path, "exists", _flaky_exists):
            result = execute_runtime_tool(
                "machine.list_directory", {"path": "~/Desktop"}, source_context=self.source_context
            )
        assert result is not None and result.ok, getattr(result, "response_text", result)
        self.assertIn("zzz-valid-entry", result.response_text)
        self.assertIn("unprobeable-entry", result.response_text)

    def test_unprobeable_entry_is_not_treated_as_trusted_or_hidden_by_default(self) -> None:
        """An unprobeable entry is simply not classified as a git project -- `_has_git_marker`
        returns False for it directly, the same as any other unreadable filesystem entry, never
        True (which would wrongly grant it trusted/hidden treatment)."""
        from core.runtime_execution_tools import _has_git_marker

        unprobeable_dir = self.desktop / "flaky"
        unprobeable_dir.mkdir()
        real_exists = Path.exists

        def _flaky_exists(self):
            if self.name == ".git" and self.parent == unprobeable_dir:
                raise OSError("simulated")
            return real_exists(self)

        with mock.patch.object(Path, "exists", _flaky_exists):
            self.assertFalse(_has_git_marker(unprobeable_dir))


# --------------------------------------------------------------------------------------
# Canonical-identity fail-OPEN mutation. Every earlier F4/canonical-identity mutation exercised
# the fail-CLOSED direction only (weakening the check makes MORE things look foreign, or lets a
# real escape through a DIFFERENT mechanism). This proves the opposite failure mode: weakening
# the identity primitive so it misidentifies an outside/sibling path AS the bound workspace must
# also cause a real disclosure, not merely demonstrate that over-hiding is possible.
# --------------------------------------------------------------------------------------


class CanonicalIdentityFailOpenMutationTests(unittest.TestCase):
    def test_weakening_identity_to_misidentify_a_sibling_as_self_turns_isolation_red(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            desktop = home / "Desktop"
            desktop.mkdir()
            project_a = _make_git_project(desktop, "project-a")
            project_b = _make_git_project(desktop, "project-b")
            source_context = {"workspace": str(project_a)}

            import core.runtime_execution_tools as ret

            real_same_directory = ret._same_directory
            confusable = {str(project_a.resolve()), str(project_b.resolve())}

            def _fail_open_same_directory(a, b):
                # Sabotage: canonical identity is weakened so the SIBLING project resolves as
                # "the same directory" as the bound workspace -- the exact shape of bug that
                # would let an outside/sibling path be wrongly granted current-project scope,
                # not just an over-broad hide.
                if {str(a), str(b)} == confusable:
                    return True
                return real_same_directory(a, b)

            with mock.patch("core.runtime_execution_tools._machine_home", lambda: home), mock.patch(
                "core.runtime_execution_tools._same_directory", side_effect=_fail_open_same_directory
            ):
                result = execute_runtime_tool(
                    "machine.list_directory", {"path": "~/Desktop"}, source_context=source_context
                )
            assert result is not None and result.ok
            self.assertIn(
                "project-b",
                result.response_text,
                "sabotage did not reproduce a fail-open identity confusion -- the sibling "
                "should have leaked because it was misidentified as the bound workspace itself",
            )
            self.assertEqual(result.details.get("hidden_sibling_projects"), 0)

    def test_weakening_identity_causes_find_largest_to_disclose_a_sibling_file(self) -> None:
        """ANVIL, final round: the `list_directory` coverage above proves the fail-open
        direction once, but `machine.find_largest`'s project filtering (`_is_under_other_git_
        project`) depends on the exact same `_same_directory` primitive through a DIFFERENT
        call path (a post-hoc filter over already-produced results, not a live BFS) -- it needs
        its own proof, not an inference from the list_directory case."""
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            desktop = home / "Desktop"
            desktop.mkdir()
            project_a = _make_git_project(desktop, "project-a")
            project_b = _make_git_project(desktop, "project-b")
            (project_b / "huge.bin").write_bytes(b"x" * (2 * 1024 * 1024))
            source_context = {"workspace": str(project_a)}

            import core.runtime_execution_tools as ret

            real_same_directory = ret._same_directory
            confusable = {str(project_a.resolve()), str(project_b.resolve())}

            def _fail_open_same_directory(a, b):
                if {str(a), str(b)} == confusable:
                    return True
                return real_same_directory(a, b)

            with mock.patch("core.runtime_execution_tools._machine_home", lambda: home), mock.patch(
                "core.runtime_execution_tools._same_directory", side_effect=_fail_open_same_directory
            ):
                result = execute_runtime_tool(
                    "machine.find_largest",
                    {"drive": str(desktop), "kind": "files", "top": 5},
                    source_context=source_context,
                )
            assert result is not None and result.ok
            self.assertIn(
                "huge.bin",
                result.response_text,
                "sabotage did not reproduce a fail-open identity confusion in find_largest -- "
                "the sibling's file should have leaked because its project was misidentified "
                "as the bound workspace itself",
            )


class SameDirectoryUnitLevelTests(unittest.TestCase):
    """Direct unit coverage for `_same_directory` itself, independent of any specific tool --
    the underlying claim every fail-open regression above depends on."""

    def test_two_genuinely_different_directories_are_never_conflated(self) -> None:
        import core.runtime_execution_tools as ret

        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "a"
            b = Path(tmp) / "b"
            a.mkdir()
            b.mkdir()
            self.assertFalse(ret._same_directory(a, b))

    def test_a_symlinked_alias_of_the_same_directory_is_recognized_as_the_same(self) -> None:
        """The correctness this primitive exists for: a firmlink/symlink alias must be
        recognized as identical by canonical (st_dev, st_ino) identity, not string equality."""
        import core.runtime_execution_tools as ret

        with tempfile.TemporaryDirectory() as tmp:
            real = Path(tmp) / "real"
            real.mkdir()
            alias = Path(tmp) / "alias"
            alias.symlink_to(real)
            self.assertTrue(ret._same_directory(real, alias.resolve()))


if __name__ == "__main__":
    unittest.main()
