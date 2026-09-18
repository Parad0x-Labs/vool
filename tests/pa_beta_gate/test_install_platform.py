"""pa_beta_gate — install / platform canaries (Codex Phase 2 F9).

The repo runs from a path WITH SPACES ("...\\Local Vool\\vool-local") and Windows
uses the `py` launcher (not `python3`). These pin the two static gaps Codex flagged:
a path-with-spaces workspace canary, and README Windows guidance using `py`.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

pytestmark = [pytest.mark.pa_beta]


def _spaced_root(base):
    root = Path(base) / "Local Vool Space"
    root.mkdir()
    return root.resolve()  # the runtime passes a resolved root; match it


def test_workspace_path_with_spaces_resolves_correctly():
    from core.execution.workspace_tools import resolve_workspace_path

    with tempfile.TemporaryDirectory() as base:
        root = _spaced_root(base)
        resolved = resolve_workspace_path("my notes.txt", workspace_root=root)
        assert resolved.parent == root
        assert resolved.name == "my notes.txt"


def test_workspace_path_with_spaces_still_blocks_escape():
    from core.execution.workspace_tools import resolve_workspace_path

    with tempfile.TemporaryDirectory() as base:
        root = _spaced_root(base)
        with pytest.raises(ValueError):
            resolve_workspace_path("../../../etc/hosts", workspace_root=root)


def test_workspace_write_and_read_under_a_spaced_path():
    from core import policy_engine
    from core.runtime_execution_tools import execute_runtime_tool

    previous = getattr(policy_engine, "_POLICY_CACHE", None)
    base_policy = dict(policy_engine.load())
    fs = dict(base_policy.get("filesystem") or {})
    fs["allow_write_workspace"] = True
    fs["allow_read_workspace"] = True
    base_policy["filesystem"] = fs
    policy_engine._POLICY_CACHE = base_policy
    try:
        with tempfile.TemporaryDirectory() as base:
            root = Path(base) / "Local Vool Space"
            root.mkdir()
            ctx = {"surface": "openclaw", "platform": "openclaw", "workspace": str(root)}
            w = execute_runtime_tool("workspace.write_file", {"path": "cap.txt", "content": "0.037 SOL\n"}, source_context=ctx)
            assert w is not None and w.ok is True
            assert (root / "cap.txt").read_text(encoding="utf-8") == "0.037 SOL\n"
            r = execute_runtime_tool("workspace.read_file", {"path": "cap.txt"}, source_context=ctx)
            assert r is not None and r.ok is True and "0.037 SOL" in r.response_text
    finally:
        policy_engine._POLICY_CACHE = previous


def test_readme_windows_quickstart_uses_py_launcher():
    readme = Path("README.md").read_text(encoding="utf-8")
    # the primary quickstart must offer the Windows `py` launcher, and warn about spaces
    assert "py -m apps.vool_api_server" in readme
    assert "quote any path with spaces" in readme.lower() or "path with spaces" in readme.lower()
