"""A packaged runtime must never write its workspace inside the bundle.

`core/runtime_paths.active_workspace_dir` already states the law: with VOOL_PROJECT_ROOT set,
writable state resolves beneath the active vool home "so a read-only .app bundle stays
byte-identical while running". `resolve_workspace_root` contradicted it by falling back to
VOOL_PROJECT_ROOT itself -- which IS the packaged case.

Measured on a real build before the fix: one chat turn through the packaged app left 67 files
under Contents/Resources/app/workspace/control (approvals, budgets, deadletters, leases,
metrics) that the pristine bundle inside the .dmg does not contain.
"""
from __future__ import annotations

from pathlib import Path

from core import runtime_paths


def test_a_packaged_root_never_becomes_the_workspace(monkeypatch, tmp_path):
    bundle = tmp_path / "VOOL.app" / "Contents" / "Resources" / "app"
    bundle.mkdir(parents=True)
    home = tmp_path / "support" / "runtime"
    monkeypatch.setenv("VOOL_PROJECT_ROOT", str(bundle))
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.delenv("VOOL_WORKSPACE_ROOT", raising=False)

    resolved = runtime_paths.resolve_workspace_root()

    assert bundle not in resolved.parents and resolved != bundle, (
        f"the workspace resolved INSIDE the bundle: {resolved}"
    )
    # Assert the LAW, not a path spelling: the workspace lives beneath the active home.
    # (Reconstructing the string is fragile on macOS, where /tmp resolves to /private/tmp.)
    assert resolved == (runtime_paths.active_vool_home() / "workspace").resolve()


def test_the_two_resolvers_agree_under_a_packaged_root(monkeypatch, tmp_path):
    """The defect was two functions in one file disagreeing about the same law."""
    bundle = tmp_path / "app"
    bundle.mkdir()
    home = tmp_path / "home"
    monkeypatch.setenv("VOOL_PROJECT_ROOT", str(bundle))
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.delenv("VOOL_WORKSPACE_ROOT", raising=False)

    assert runtime_paths.resolve_workspace_root() == runtime_paths.active_workspace_dir()


def test_an_explicit_workspace_override_still_wins(monkeypatch, tmp_path):
    explicit = tmp_path / "elsewhere"
    monkeypatch.setenv("VOOL_PROJECT_ROOT", str(tmp_path / "app"))
    monkeypatch.setenv("VOOL_WORKSPACE_ROOT", str(explicit))
    assert runtime_paths.resolve_workspace_root() == explicit.resolve()


def test_an_explicit_argument_still_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("VOOL_PROJECT_ROOT", str(tmp_path / "app"))
    chosen = tmp_path / "chosen"
    assert runtime_paths.resolve_workspace_root(chosen) == chosen.resolve()


def test_an_unpackaged_run_is_unaffected(monkeypatch, tmp_path):
    """With no VOOL_PROJECT_ROOT this stays the cwd-based resolution it always was."""
    monkeypatch.delenv("VOOL_PROJECT_ROOT", raising=False)
    monkeypatch.delenv("VOOL_WORKSPACE_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)
    assert runtime_paths.resolve_workspace_root() == Path(tmp_path).resolve()
