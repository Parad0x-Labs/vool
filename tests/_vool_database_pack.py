"""Test-only installer for the vool-database pack.

Copies the repository pack at ``plugins/vool-database`` into an isolated plugins
root and pins the handler interpreter to the running one (the confined child
environment is an allowlist, so ``#!/usr/bin/env python3`` is only correct on a
PATH the test controls). Mirrors ``tests/_toolchain_fixtures.make_plugin``.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

DB_PLUGIN_ID = "vool-database"
REPO_PACK = Path(__file__).resolve().parent.parent / "plugins" / "vool-database"


def install_pack(root: Path) -> Path:
    """Install the pack under ``root/plugins/vool-database`` and return its directory."""

    destination = root / "plugins" / DB_PLUGIN_ID
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(REPO_PACK, destination)
    handler = destination / "bin" / "db_handler"
    text = handler.read_text(encoding="utf-8")
    handler.write_text(text.replace("#!/usr/bin/env python3", "#!" + sys.executable, 1), encoding="utf-8")
    handler.chmod(handler.stat().st_mode | 0o111)
    return destination


def activate_pack(pack_dir: Path):
    """Take the copied pack through the EXISTING lifecycle: install, verify, enable.

    The integration branch's canonical ``register_plugin`` offers a pack's tools only when
    ``core.plugin_lifecycle.is_available`` says so — presence on disk is not installation,
    and the three acts are separate on purpose. The lifecycle store lives under VOOL_HOME,
    so a daemon booted in the same isolated home sees the same activation.
    """

    from core.plugin_lifecycle import enable, install, verify

    install(DB_PLUGIN_ID, root=pack_dir, source="test-isolated")
    verify(DB_PLUGIN_ID, root=pack_dir)
    return enable(DB_PLUGIN_ID)


def isolated_db_world(tmp_path, monkeypatch):
    """One isolated home + plugins root + scratch per test. No operator state."""

    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.delenv("VOOL_MCP_CONFIG", raising=False)
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_PLUGINS_DIR", str(tmp_path))
    monkeypatch.setenv("VOOL_PLUGIN_SCRATCH_ROOT", str(tmp_path / "scratch"))
    monkeypatch.delenv("VOOL_PLUGIN_CONFINEMENT", raising=False)
    return activate_pack(install_pack(tmp_path))


def databases_root(tmp_path: Path) -> Path:
    return tmp_path / "scratch" / DB_PLUGIN_ID / "databases"


__all__ = ["DB_PLUGIN_ID", "activate_pack", "databases_root", "install_pack", "isolated_db_world"]
