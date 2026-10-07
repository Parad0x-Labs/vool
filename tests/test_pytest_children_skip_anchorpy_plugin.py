"""The optional `pay` extra must not break VOOL's own pytest runs.

Solana pay-kit pulls in anchorpy, which registers a pytest plugin (``pytest11: pytest_anchorpy``)
that imports pytest-asyncio and pytest-xprocess and stops pytest at startup without them. These
tests install a stand-in plugin of that name that always fails to import, then prove VOOL's
pytest entry points never load it: the gate's collection and execution children (which clear
addopts), a direct run under the repository's own addopts, and a child session a test starts in a
scratch directory (which inherits the block through PYTEST_ADDOPTS).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

from ops.pytest_shards import collect_test_manifest
from tests.pytest_plugin_blocks import block_for_child_sessions

REPO = Path(__file__).resolve().parents[1]


def _broken_plugin_site(root: Path) -> Path:
    site = root / "site"
    dist = site / "broken_anchorpy_stand_in-0.0.dist-info"
    dist.mkdir(parents=True)
    (dist / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: broken-anchorpy-stand-in\nVersion: 0.0\n", encoding="utf-8"
    )
    (dist / "entry_points.txt").write_text(
        "[pytest11]\npytest_anchorpy = broken_anchorpy_stand_in\n", encoding="utf-8"
    )
    (site / "broken_anchorpy_stand_in.py").write_text(
        'raise ModuleNotFoundError("No module named \'pytest_asyncio\'", name="pytest_asyncio")\n',
        encoding="utf-8",
    )
    return site


def _tiny_repo(root: Path) -> Path:
    repo = root / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n', encoding="utf-8"
    )
    (repo / "tests" / "test_tiny.py").write_text(
        "def test_one():\n    assert True\n", encoding="utf-8"
    )
    return repo


def _with_site(site: Path) -> dict[str, str]:
    # a bare child: without the PYTEST_ADDOPTS block this session hands its children when the real
    # plugin is installed here (tests/pytest_plugin_blocks.py)
    env = {key: value for key, value in os.environ.items() if key != "PYTEST_ADDOPTS"}
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (str(site), env.get("PYTHONPATH", ""))))
    return env


def test_the_stand_in_plugin_breaks_a_bare_pytest_run(tmp_path: Path) -> None:
    # the hazard is real: without a block, the stand-in stops pytest before any test runs
    site, repo = _broken_plugin_site(tmp_path), _tiny_repo(tmp_path)
    done = subprocess.run(
        (sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"),
        cwd=repo, env=_with_site(site), capture_output=True, text=True, timeout=120, check=False,
    )
    assert done.returncode != 0
    assert "pytest_asyncio" in done.stdout + done.stderr


def test_the_gate_collection_child_never_loads_the_anchorpy_plugin(
    tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    site, repo = _broken_plugin_site(tmp_path), _tiny_repo(tmp_path)
    monkeypatch.setenv("PYTHONPATH", _with_site(site)["PYTHONPATH"])
    run_root = tmp_path / "run"
    run_root.mkdir()

    rc, manifest = collect_test_manifest(repo_root=repo, run_root=run_root, timeout_seconds=120)

    assert rc == 0 and manifest is not None
    assert manifest.targets == ("tests/test_tiny.py",)


def test_the_gate_execution_child_never_loads_the_anchorpy_plugin(tmp_path: Path) -> None:
    site, repo = _broken_plugin_site(tmp_path), _tiny_repo(tmp_path)
    output = tmp_path / "execution.json"
    done = subprocess.run(
        (sys.executable, str(REPO / "ops" / "pytest_execution.py"), "--output", str(output), "--",
         "-q", "-p", "no:cacheprovider", "tests/test_tiny.py"),
        cwd=repo, env=_with_site(site), capture_output=True, text=True, timeout=120, check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert json.loads(output.read_text(encoding="utf-8"))["started_nodeids"] == ["tests/test_tiny.py::test_one"]


def test_a_direct_run_under_the_repository_addopts_never_loads_the_anchorpy_plugin(tmp_path: Path) -> None:
    site, repo = _broken_plugin_site(tmp_path), _tiny_repo(tmp_path)
    addopts = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["pytest"]["ini_options"]["addopts"]
    assert addopts[addopts.index("no:pytest_anchorpy") - 1] == "-p"
    (repo / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\naddopts = ' + json.dumps(addopts) + "\n", encoding="utf-8"
    )
    done = subprocess.run(
        (sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"),
        cwd=repo, env=_with_site(site), capture_output=True, text=True, timeout=120, check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert "1 passed" in done.stdout


def test_child_sessions_inherit_the_block_only_when_the_plugin_is_installed() -> None:
    untouched: dict[str, str] = {}
    block_for_child_sessions(untouched, installed={"xdist.plugin", "anyio"})
    assert untouched == {}, "without the plugin installed nothing changes"
    blocked: dict[str, str] = {"PYTEST_ADDOPTS": "--tb=short"}
    block_for_child_sessions(blocked, installed={"pytest_anchorpy"})
    block_for_child_sessions(blocked, installed={"pytest_anchorpy"})
    assert blocked == {"PYTEST_ADDOPTS": "--tb=short -p no:pytest_anchorpy"}


def test_a_child_session_in_a_scratch_directory_never_loads_the_anchorpy_plugin(tmp_path: Path) -> None:
    site, repo = _broken_plugin_site(tmp_path), _tiny_repo(tmp_path)
    env = _with_site(site)
    block_for_child_sessions(env, installed={"pytest_anchorpy"})
    done = subprocess.run(
        (sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"),
        cwd=repo, env=env, capture_output=True, text=True, timeout=120, check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert "1 passed" in done.stdout
