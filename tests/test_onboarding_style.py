"""Onboarding communication-style question: parsing + persistence (pure helpers, no live I/O)."""
from __future__ import annotations

import pytest

from core import onboarding, runtime_paths, user_preferences


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    yield
    runtime_paths.configure_runtime_home(None)


@pytest.mark.parametrize(
    "answer,expected",
    [
        ("business", "business"),
        ("keep it professional", "business"),
        ("cheeky", "cheeky"),
        ("fun, with emojis and slang", "cheeky"),
        ("casual", "casual"),
        ("", "casual"),
        ("dunno whatever", "casual"),
    ],
)
def test_parse_communication_style(answer, expected) -> None:
    assert onboarding._parse_communication_style(answer) == expected


def test_apply_persists_style() -> None:
    assert user_preferences.load_preferences().communication_style == "casual"
    assert onboarding.apply_onboarding_communication_style("go cheeky") == "cheeky"
    assert user_preferences.load_preferences().communication_style == "cheeky"


def test_apply_defaults_on_blank() -> None:
    assert onboarding.apply_onboarding_communication_style("") == "casual"
    assert user_preferences.load_preferences().communication_style == "casual"
