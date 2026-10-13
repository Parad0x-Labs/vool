"""Recorded replay contracts; only synthetic test bodies run on this host."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from ops import ci_wallet_order_replay as replay

REPO = Path(__file__).resolve().parent.parent
TARGET_FILE = replay.TARGET.split("::", 1)[0]


def _test_files(root: Path) -> list[str]:
    files = [TARGET_FILE, "tests/test_other.py"]
    for name in files:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    return files


def test_existing_unique_test_file_order_is_retained(tmp_path: Path) -> None:
    files = _test_files(tmp_path)
    raw = ("\n".join(files) + "\n").encode()
    assert replay.validate_files(raw, tmp_path, expected_count=2) == files


@pytest.mark.parametrize("bad", [
    "tests/../ops/ci_wallet_order_replay.py", "/tests/test_other.py", "--collect-only",
    "tests/test_other.py::test_selected", "tests//test_other.py", "tests/test_absent.py",
    "test-kit/test_anything_else.py",
])
def test_artifact_paths_cannot_select_options_nodes_or_other_code(tmp_path: Path, bad: str) -> None:
    _test_files(tmp_path)
    with pytest.raises(ValueError):
        replay.validate_files(f"{TARGET_FILE}\n{bad}\n".encode(), tmp_path, expected_count=2)


def test_duplicates_missing_target_and_symlinks_are_refused(tmp_path: Path) -> None:
    _test_files(tmp_path)
    with pytest.raises(ValueError, match="uniqueness"):
        replay.validate_files(f"{TARGET_FILE}\n{TARGET_FILE}\n".encode(), tmp_path, expected_count=2)
    with pytest.raises(ValueError, match="absent"):
        replay.validate_files(b"tests/test_other.py\n", tmp_path, expected_count=1)
    (tmp_path / "tests/link.py").symlink_to(tmp_path / "tests/test_other.py")
    with pytest.raises(ValueError, match="symlinked"):
        replay.validate_files(f"{TARGET_FILE}\ntests/link.py\n".encode(), tmp_path, expected_count=2)


def test_only_two_exact_recorded_test_kit_files_are_allowed(tmp_path: Path) -> None:
    _test_files(tmp_path)
    for name in ("test-kit/test_cp2_controls.py", "test-kit/test_cp1_incident_repro.py"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")
        files = [TARGET_FILE, name]
        assert replay.validate_files(("\n".join(files) + "\n").encode(), tmp_path,
                                     expected_count=2) == files


def test_ast_hash_preserves_assertions_decorators_and_body() -> None:
    name = replay.TARGET.split("::", 1)[1]
    source = f"@mark\ndef {name}(fixture):\n    assert fixture == 7\n"
    same = f"# only formatting changed\n@mark\ndef {name}(fixture):\n\n    assert fixture==7\n"
    assert replay.function_ast_hash(source) == replay.function_ast_hash(same)
    assert replay.function_ast_hash(source) != replay.function_ast_hash(source.replace("== 7", "== 8"))
    assert replay.function_ast_hash(source) != replay.function_ast_hash(source.replace("@mark", "@skip"))
    with pytest.raises(ValueError, match="exactly one"):
        replay.function_ast_hash(source + source)


def test_wrong_remote_identity_is_refused_before_git_or_collection(tmp_path: Path) -> None:
    run = tmp_path / "run.json"
    run.write_text(json.dumps({"databaseId": replay.SOURCE_RUN, "headSha": "--execute-something"}))
    with pytest.raises(ValueError, match="identity"):
        replay.prepare_plan(tmp_path / "absent-artifact", run)


def test_tampered_artifact_is_refused_before_test_source_is_read(tmp_path: Path) -> None:
    run = tmp_path / "run.json"
    run.write_text(json.dumps({"databaseId": replay.SOURCE_RUN, "headSha": replay.SOURCE_SHA,
                               "status": "completed", "conclusion": "failure"}))
    (tmp_path / "shard-1-files.txt").write_text("tests/test_selected.py\n")
    with pytest.raises(ValueError, match="digest"):
        replay.prepare_plan(tmp_path, run)


def test_nonlinux_refuses_before_parsing_paths_or_invoking_collection(monkeypatch, capsys) -> None:
    monkeypatch.setattr(replay.sys, "platform", "darwin")
    # Missing required arguments would raise SystemExit if parsing happened first.
    assert replay.main([]) == 2
    assert "requires Linux" in capsys.readouterr().err


def test_real_synthetic_pytest_preserves_all_nodes_and_red_verdict(tmp_path: Path) -> None:
    files = _test_files(tmp_path)
    output = tmp_path / "evidence"
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "conftest.py").write_text(
        "from pathlib import Path\n"
        "from ops.ci_wallet_order_replay import ReplayOrderPlugin\n"
        "def pytest_configure(config):\n"
        f"    config.pluginmanager.register(ReplayOrderPlugin({files!r}, Path({str(output)!r})))\n"
    )
    function = replay.TARGET.split("::", 1)[1]
    (tmp_path / TARGET_FILE).write_text(
        "def test_first():\n    assert True\n"
        f"def {function}():\n    assert False, 'retained diagnostic failure'\n"
        "def test_after_privacy():\n    assert True\n"
    )
    (tmp_path / files[1]).write_text("def test_other():\n    assert True\n")
    completed = subprocess.run([
        sys.executable, str(REPO / "ops/pytest_watchdog.py"), "--phase-timeout", "30",
        "--output", str(output / "watchdog"), "--", sys.executable,
        str(REPO / "ops/pytest_timing.py"), "--output", str(output / "timing.json"),
        "--", "-q", "--tb=short", "-p", "no:cacheprovider", *files,
    ], cwd=tmp_path, env={**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
        capture_output=True, text=True, timeout=60, check=False)
    assert completed.returncode == 1, completed.stdout + completed.stderr
    order = json.loads((output / "node-order.json").read_text())
    assert order["original_nodes"] == [
        f"{TARGET_FILE}::test_first", replay.TARGET,
        f"{TARGET_FILE}::test_after_privacy", f"{files[1]}::test_other",
    ]
    assert order["ordered_nodes"] == [
        f"{TARGET_FILE}::test_first", f"{TARGET_FILE}::test_after_privacy",
        f"{files[1]}::test_other", replay.TARGET,
    ]
    assert order["collected_count"] == 4
    assert (output / "started-node-order.txt").read_text().splitlines() == order["ordered_nodes"]
    timing = json.loads((output / "timing.json").read_text())
    assert timing["complete"] is True
    assert timing["exitstatus"] == 1
    assert timing["session"]["collected_total"] == 4
    assert timing["session"]["started_total"] == 4
    assert timing["failed_nodes"][0]["nodeid"] == replay.TARGET
    assert "3 passed" in completed.stdout and "1 failed" in completed.stdout


def test_workflow_is_manual_diagnostic_with_exact_existing_confinement() -> None:
    ci = yaml.safe_load((REPO / ".github/workflows/ci.yml").read_text())
    diagnostic = yaml.safe_load((REPO / ".github/workflows/wallet-order-replay.yml").read_text())
    # PyYAML's YAML 1.1 parser spells the GitHub `on` key as True.
    assert diagnostic.get("on", diagnostic.get(True)) == {"workflow_dispatch": None}
    assert diagnostic["permissions"] == {"contents": "read", "actions": "read"}
    job = diagnostic["jobs"]["replay"]
    assert job["runs-on"] == "ubuntu-latest"
    steps = job["steps"]
    originals = ci["jobs"]["tests"]["steps"]
    boundary = next(i for i, step in enumerate(originals)
                    if step.get("name") == "Resolve this shard's test files")
    assert steps[:boundary] == originals[:boundary]
    assert steps[-1]["if"] == "always()"
    assert steps[-1]["with"]["include-hidden-files"] is True
    assert "diagnostic" in job["name"]
