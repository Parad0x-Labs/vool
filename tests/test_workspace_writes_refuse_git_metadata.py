"""File tools never write a repository's own metadata.

git runs what a repository's `.git` holds (configuration such as `core.fsmonitor`, hooks) for the user, later and
outside any sandbox. The command sandbox already denied writes there; the file tools did not, so a patch, a
`write_file` or a move could change it. Every write now asks `core.git_metadata_protection` through
`core.runtime_state_protection.refusal`, and the patch tool checks every path a patch names (git's `diff --git`,
`rename` and `copy` lines included) before any engine runs, so a refused patch changes nothing at all.

Each test runs the real tool and then the user's own git, and checks what git did.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from core.execution.workspace_tools import _extract_patch_paths, apply_unified_diff_workspace
from core.git_metadata_protection import refusal
from core.machine_file_ops import move_path
from core.runtime_execution_tools import execute_runtime_tool

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

NO_EOL = "\n\\ No newline at end of file\n"
MARK = "MARKER_RAN"


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), "-c", "user.email=a@b", "-c", "user.name=a", *args],
        capture_output=True, text=True, timeout=60,
    )


@pytest.fixture()
def repo(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("gitmeta").resolve()
    _git(root, "init", "-q")
    (root / "f.txt").write_text("one\n", encoding="utf-8")
    (root / "h.sh").write_text(f"#!/bin/sh\ntouch \"$(git rev-parse --show-toplevel)/{MARK}\"\n", encoding="utf-8")
    return root


def _snapshot(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def _apply(root: Path, patch: str) -> dict:
    return apply_unified_diff_workspace({"patch": patch.rstrip("\n") + NO_EOL}, workspace_root=root, session_id="t")


def test_a_patch_to_git_config_is_refused_and_git_status_runs_nothing(repo) -> None:
    first = (repo / ".git/config").read_text().splitlines()[0]
    before = _snapshot(repo)
    result = _apply(repo, f"--- a/.git/config\n+++ b/.git/config\n@@ -1 +1,2 @@\n-{first}\n+{first}\n+\tfsmonitor = touch {MARK}; false")
    _git(repo, "status")
    assert result["ok"] is False and result["status"] == "workspace_path_refused"
    assert ".git/config" in result["response_text"]
    assert not (repo / MARK).exists()
    assert _snapshot(repo) == before


def test_a_patch_creating_a_hook_is_refused(repo) -> None:
    result = _apply(repo, "--- /dev/null\n+++ b/.git/hooks/pre-commit\n@@ -0,0 +1,2 @@\n+#!/bin/sh\n+true")
    assert result["status"] == "workspace_path_refused"
    assert not (repo / ".git/hooks/pre-commit").exists()


@pytest.mark.parametrize("kind", ["copy", "rename"])
def test_a_copy_or_rename_into_git_beside_a_normal_edit_changes_nothing(repo, kind) -> None:
    before = _snapshot(repo)
    patch = (
        "diff --git a/f.txt b/f.txt\n--- a/f.txt\n+++ b/f.txt\n@@ -1 +1 @@\n-one\n+two\n"
        "diff --git a/h.sh b/.git/hooks/pre-commit\nold mode 100644\nnew mode 100755\nsimilarity index 100%\n"
        f"{kind} from h.sh\n{kind} to .git/hooks/pre-commit\n"
    )
    result = _apply(repo, patch)
    _git(repo, "add", "f.txt")
    _git(repo, "commit", "-qm", "x")
    assert result["status"] == "workspace_path_refused"
    assert not (repo / ".git/hooks/pre-commit").exists()
    assert not (repo / MARK).exists()
    # all or nothing: the ordinary edit beside the refused target did not land either
    assert (repo / "f.txt").read_text() == "one\n"
    assert {k: v for k, v in _snapshot(repo).items() if not k.startswith(".git/")} == {
        k: v for k, v in before.items() if not k.startswith(".git/")
    }


def test_patch_target_list_names_git_header_rename_and_copy_paths() -> None:
    patch = (
        "diff --git a/a.txt b/b.txt\nrename from a.txt\nrename to b.txt\n"
        'diff --git a/h.sh "b/x y.sh"\ncopy from h.sh\ncopy to "x y.sh"\n'
    )
    assert _extract_patch_paths(patch) == ["a.txt", "b.txt", "h.sh", "x y.sh"]


@pytest.mark.parametrize("name", [".GIT/config", ".Git/hooks/pre-commit", ".git", "sub/.git", "nested/.git/config",
                                  ".g‌it/config"])
def test_any_letter_case_a_git_file_and_nested_repos_are_refused(repo, name) -> None:
    assert refusal(repo / name)


def test_ordinary_dot_git_lookalikes_stay_writable(repo) -> None:
    for name in (".gitignore", ".github/workflows/ci.yml", ".gitattributes", "docs/git/notes.md", "x.git.txt"):
        assert refusal(repo / name) == "", name


def test_a_symlink_into_git_is_refused(repo) -> None:
    (repo / "meta").symlink_to(repo / ".git")
    result = execute_runtime_tool("workspace.write_file", {"path": "meta/config", "content": "x"},
                                  source_context={"workspace": str(repo)})
    assert result is not None and result.ok is False
    assert "[core]" in (repo / ".git/config").read_text()


def test_a_git_directory_under_another_name_is_refused(repo, tmp_path) -> None:
    other = tmp_path / "plain"
    shutil.copytree(repo / ".git", other)
    assert refusal(other / "hooks" / "pre-commit")


@pytest.mark.parametrize(
    ("intent", "arguments"),
    [
        ("workspace.write_file", {"path": ".git/hooks/pre-commit", "content": "#!/bin/sh\n"}),
        ("workspace.write_file", {"path": ".GIT/config", "content": "[core]\n"}),
        ("workspace.write_file", {"path": "sub/.git", "content": "gitdir: /tmp/elsewhere\n"}),
        ("workspace.replace_in_file", {"path": ".git/config", "old_text": "[core]", "new_text": "[core]\n\tbare = false"}),
        ("workspace.ensure_directory", {"path": ".git/hooks/new"}),
    ],
)
def test_workspace_write_tools_refuse_git_metadata(repo, intent, arguments) -> None:
    (repo / "sub").mkdir()
    before = _snapshot(repo)
    result = execute_runtime_tool(intent, arguments, source_context={"workspace": str(repo)})
    assert result is not None and result.ok is False
    assert _snapshot(repo) == before
    assert not (repo / ".git/hooks/new").exists()


def test_a_move_into_git_is_refused(repo) -> None:
    result = move_path(str(repo / "h.sh"), str(repo / ".git/hooks/pre-commit"), consent_fn=lambda _m: True,
                       allowed_roots=[repo])
    assert result.ok is False
    assert (repo / "h.sh").exists() and not (repo / ".git/hooks/pre-commit").exists()


def test_ordinary_patches_still_apply(repo) -> None:
    result = _apply(repo, "--- a/f.txt\n+++ b/f.txt\n@@ -1 +1 @@\n-one\n+two")
    assert result["ok"] is True, result["response_text"]
    assert (repo / "f.txt").read_text().startswith("two")
    created = _apply(repo, "--- /dev/null\n+++ b/.github/hooks/notes.txt\n@@ -0,0 +1 @@\n+ok")
    assert created["ok"] is True, created["response_text"]
    assert (repo / ".github/hooks/notes.txt").exists()
    assert os.access(repo / ".git/config", os.R_OK)
