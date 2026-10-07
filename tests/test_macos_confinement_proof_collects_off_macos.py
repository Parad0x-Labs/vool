"""The macOS-only confinement proof is collectable where `pwd` does not exist (Windows CI).

Pack 2b re-audit, 2026-10-07: tests/test_toolsmith_run_tests_fs_confinement.py imported `pwd` and read
the account database at module level, before its Darwin skip, so collecting it on Windows raised
ModuleNotFoundError and failed the whole shard instead of skipping the class.
"""
from __future__ import annotations

import builtins
import runpy
from pathlib import Path

_PROOF = Path(__file__).with_name("test_toolsmith_run_tests_fs_confinement.py")


def test_the_proof_module_loads_without_pwd_on_a_non_macos_platform(monkeypatch):
    original = builtins.__import__

    def no_pwd(name, *args, **kwargs):
        if name == "pwd":
            raise ModuleNotFoundError("No module named 'pwd'")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_pwd)
    monkeypatch.setattr("sys.platform", "win32")
    namespace = runpy.run_path(str(_PROOF))
    assert namespace["_REAL_HOME"] is None
