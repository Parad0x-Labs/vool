"""The runtime version must stay in sync with pyproject.toml so self-update compares right."""
from __future__ import annotations

import json
import re
from pathlib import Path

from core.app_version import VOOL_VERSION, installed_version


def _pyproject_version() -> str:
    text = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'(?m)^\s*version\s*=\s*"([^"]+)"', text)
    assert match, "pyproject.toml has no version"
    return match.group(1)


def test_runtime_version_matches_pyproject() -> None:
    assert _pyproject_version() == VOOL_VERSION


def test_installed_version_returns_the_constant() -> None:
    assert installed_version() == VOOL_VERSION


def test_beta_release_channel_matches_runtime_version() -> None:
    root = Path(__file__).resolve().parents[1]
    payload = json.loads((root / "config" / "release" / "update_channel.json").read_text(encoding="utf-8"))
    assert payload["release_version"] == f"{VOOL_VERSION}-beta"
