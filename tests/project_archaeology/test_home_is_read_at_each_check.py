"""The home directory is protected as it is when the check runs, not as it was when the module was first imported.

CI on fe98e849 (shard 0): core.project_archaeology was first imported while an earlier test had HOME pointed at a
scratch folder, kept that folder as "home" for the rest of the run, and then let the real home through as a crawl
root ("/home/runner must be protected", and search(workspace_root=home) did not refuse).
"""
from __future__ import annotations

import importlib
import secrets
from pathlib import Path

import pytest


def test_a_home_seen_at_import_does_not_replace_the_real_one(tmp_path, monkeypatch):
    from core import project_archaeology as pa

    real_home = Path.home()
    elsewhere = tmp_path / f"home-{secrets.token_hex(4)}"
    elsewhere.mkdir()
    with monkeypatch.context() as patched:
        patched.setenv("HOME", str(elsewhere))
        pa = importlib.reload(pa)   # first import under a different HOME
    try:
        assert Path.home() == real_home
        assert pa.is_protected_path(str(real_home))[0] is True
        with pytest.raises(pa.ScopeRefused):
            pa.search(workspace_root=str(real_home))
        # The home in force at check time is protected too.
        monkeypatch.setenv("HOME", str(elsewhere))
        assert pa.is_protected_path(str(elsewhere))[0] is True
        # And a HOME pointed elsewhere never unprotects the account's own home.
        assert pa.is_protected_path(str(real_home))[0] is True
    finally:
        monkeypatch.undo()
        importlib.reload(pa)
