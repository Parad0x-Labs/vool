"""A command whose working directory names the workspace runs in the workspace, and a failure never
shows the person a raw error or a path on their machine.

Measured 2026-11-12 on the real app (0.8.0-beta, 247124ed, qwen3.5:4b): "run the command: sed -n 2p
orders/week.csv" was sent with cwd "workspace". The tool joined that onto the workspace root, the
process could not start in ".../workspace/workspace", and the person read

    Sandbox execution failure: [Errno 2] No such file or directory: '<their home>/.../workspace/workspace'

-- a raw exception, an error number and the folder layout of their machine, filed under the status
"blocked_by_policy" although no policy was involved. Reproduced on the old build in this file's own
shape (the end-to-end case below) before the fix.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

from core.runtime_execution_tools import (
    execute_runtime_tool,
    sandbox_cwd_refusal,
    sandbox_working_directory,
    without_local_paths,
)
from sandbox.sandbox_runner import SandboxRunner


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    (root / "orders").mkdir(parents=True)
    (root / "orders" / "week.csv").write_text("day,total\nmon,125\ntue,90\n", encoding="utf-8")
    return root


@pytest.mark.parametrize(
    ("asked", "meant"),
    (
        ("workspace", ""),
        ("/workspace", ""),
        ("./workspace", ""),
        ("workspace/orders", "orders"),
        ("/workspace/orders/", "orders"),
        ("orders", "orders"),
        (".", "."),
        ("", ""),
    ),
)
def test_a_cwd_that_names_the_workspace_is_read_from_its_root(workspace: Path, asked: str, meant: str) -> None:
    assert sandbox_working_directory(asked, workspace_root=workspace) == meant


def test_the_folder_s_own_name_names_it_too(tmp_path: Path) -> None:
    root = tmp_path / "my-project"
    (root / "src").mkdir(parents=True)

    assert sandbox_working_directory("my-project/src", workspace_root=root) == "src"


def test_a_real_folder_named_workspace_is_taken_as_written(workspace: Path) -> None:
    (workspace / "workspace").mkdir()

    assert sandbox_working_directory("workspace", workspace_root=workspace) == "workspace"


def test_a_missing_folder_is_refused_before_anything_starts_in_plain_words(workspace: Path) -> None:
    refusal = sandbox_cwd_refusal("reports/2026", workspace_root=workspace)

    assert refusal is not None and not refusal.ok
    assert refusal.status == "cwd_not_found"
    assert "`reports/2026`" in refusal.response_text and "Nothing was run" in refusal.response_text
    assert str(workspace) not in refusal.response_text
    assert refusal.details.get("executed") is False


def test_an_outside_refusal_does_not_print_the_workspace_s_full_path(workspace: Path) -> None:
    refusal = sandbox_cwd_refusal("/Users", workspace_root=workspace)

    assert refusal is not None and refusal.status == "cwd_outside_workspace"
    assert str(workspace) not in refusal.response_text


def test_local_paths_and_error_numbers_are_kept_out_of_an_error(workspace: Path) -> None:
    raw = f"Sandbox execution failure: [Errno 2] No such file or directory: '{workspace}/workspace'"

    shown = without_local_paths(raw, workspace_root=workspace)

    assert "[Errno" not in shown
    assert str(workspace) not in shown
    assert "'workspace'" in shown
    assert without_local_paths("open /etc/hosts failed.", workspace_root=workspace) == "open hosts failed."
    assert without_local_paths("see https://example.com/a/b", workspace_root=workspace) == "see https://example.com/a/b"


def test_a_command_that_cannot_start_is_said_in_words(workspace: Path) -> None:
    missing = FileNotFoundError(2, "No such file or directory", str(workspace / "workspace"))
    runner = SandboxRunner(mock.Mock(evaluate_command=lambda *_a, **_k: {"decision": "allow"}), str(workspace))
    with mock.patch.object(type(runner.job_runner), "run", side_effect=missing):
        result = runner.run_command("ls", cwd=str(workspace))

    assert result["status"] == "failed_to_start"
    assert result["error"] == "The command could not start: no such file or directory (workspace)."
    assert str(workspace) not in result["error"] and "Errno" not in result["error"]


def _bwrap_usable() -> bool:
    bwrap = shutil.which("bwrap")
    if not bwrap or not sys.platform.startswith("linux"):
        return False
    probe = subprocess.run(
        [bwrap, "--unshare-net", "--ro-bind", "/", "/", "--tmpfs", "/tmp", "--", "true"],
        capture_output=True,
        check=False,
    )
    return probe.returncode == 0


@pytest.mark.skipif(not _bwrap_usable(), reason="needs a usable bwrap backend")
@pytest.mark.parametrize(("cwd", "command"), (
    ("workspace", "sed -n 2p orders/week.csv"),
    ("/workspace", "sed -n 2p orders/week.csv"),
    ("workspace/orders", "sed -n 2p week.csv"),
))
def test_the_measured_command_runs_in_the_workspace(workspace: Path, cwd: str, command: str) -> None:
    result = execute_runtime_tool(
        "sandbox.run_command",
        {"command": command, "cwd": cwd},
        source_context={"workspace_root": str(workspace), "workspace": str(workspace)},
    )

    assert result.ok and result.status == "executed", result.response_text
    assert "mon,125" in result.response_text
    assert str(workspace) not in result.response_text
