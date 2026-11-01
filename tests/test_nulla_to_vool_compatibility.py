"""Contracts carried across the NULLA -> VOOL rename (see docs/UPGRADE_NULLA_TO_VOOL.md).

The compat laws as they stand after the 2026-11-01 first-run amendment:
1. VOOL_* env wins over NULLA_*; NULLA_* alone still works (explicit selection).
2. A legacy runtime store is NEVER adopted implicitly: a missing VOOL profile is a clean
   first-run setup, and the old store is left untouched on disk.
3. A legacy database filename inside an existing home is REUSED, never orphaned.
"""
from __future__ import annotations

import os
from pathlib import Path

from core import env_compat
from core.runtime_paths import user_runtime_default, vool_env


def test_env_compat_mirrors_legacy_only_when_canonical_unset(monkeypatch) -> None:
    monkeypatch.delenv("VOOL_MODEL_TAG", raising=False)
    monkeypatch.setenv("NULLA_MODEL_TAG", "qwen3:4b")
    assert env_compat.apply_legacy_nulla_env() == 1
    assert os.environ["VOOL_MODEL_TAG"] == "qwen3:4b"


def test_env_compat_never_overwrites_canonical(monkeypatch) -> None:
    env = {"VOOL_MODEL_TAG": "canonical", "NULLA_MODEL_TAG": "legacy"}
    assert env_compat.apply_legacy_nulla_env(env) == 0
    assert env["VOOL_MODEL_TAG"] == "canonical"


def test_vool_env_reads_canonical_first_then_legacy(monkeypatch) -> None:
    monkeypatch.setenv("VOOL_K", "v")
    monkeypatch.delenv("NULLA_K", raising=False)
    assert vool_env("K") == "v"
    monkeypatch.delenv("VOOL_K", raising=False)
    monkeypatch.setenv("NULLA_K", "n")
    assert vool_env("K") == "n"
    monkeypatch.delenv("NULLA_K", raising=False)
    assert vool_env("K", "d") == "d"


def test_legacy_user_home_is_never_adopted_implicitly(monkeypatch, tmp_path) -> None:
    """First-run law (2026-11-01), replacing the 2026-09-19 reuse rule: a missing VOOL
    profile is a clean first-run setup, never a silent read of the pre-rename store. The
    old store's identity material may not decrypt under this runtime (measured: InvalidTag
    boot death, no window); it is left untouched. An explicit VOOL_HOME/NULLA_HOME still
    selects a home directly (`core.runtime_paths.active_vool_home`)."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)  # type: ignore[assignment]
    legacy = tmp_path / ".nulla_runtime"
    legacy.mkdir()
    assert user_runtime_default() == tmp_path / ".vool_runtime"
    canonical = tmp_path / ".vool_runtime"
    canonical.mkdir()
    assert user_runtime_default() == canonical


def test_legacy_db_filename_is_reused_not_orphaned(tmp_path) -> None:
    from storage.db import resolve_runtime_db_filename

    data = tmp_path / "data"
    data.mkdir()
    legacy = data / "nulla_web0_v2.db"
    legacy.write_bytes(b"legacy")
    assert resolve_runtime_db_filename(data) == legacy
    (data / "vool_web0_v2.db").write_bytes(b"new")
    assert resolve_runtime_db_filename(data) == (data / "vool_web0_v2.db").resolve()


def test_legacy_memory_db_filename_is_reused(tmp_path) -> None:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from core.vool_memory import _resolve_db_path

    mem = tmp_path / "data" / "memory"
    mem.mkdir(parents=True)
    legacy = mem / "nulla_memory.db"
    legacy.write_bytes(b"legacy")
    got = _resolve_db_path(runtime_home=tmp_path, db_path=None)
    assert got == legacy
