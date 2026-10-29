"""A model-store candidate this process may not stat (sandbox, volume without access) counts as not
available instead of raising and taking the daemon down (benchmark sandbox, 2026-10-29)."""
from __future__ import annotations

from pathlib import Path

from core import runtime_install_profiles as rip


def test_a_denied_stat_is_not_available(monkeypatch, tmp_path):
    denied = tmp_path / "locked" / ".ollama" / "models"
    real_exists = Path.exists

    def exists(self, *args, **kwargs):
        if str(self).startswith(str(tmp_path / "locked")):
            raise PermissionError(1, "Operation not permitted", str(self))
        return real_exists(self, *args, **kwargs)

    monkeypatch.setattr(Path, "exists", exists)
    assert rip._path_target_available(denied) is False
    assert rip._path_target_available(tmp_path / "open" / "models") is True
