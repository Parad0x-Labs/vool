"""The system prompt grounds cloud actions: the model must never narrate a switch/refresh/
connection check/key save that did not run — both with and without wired OpenClaw tools."""
from __future__ import annotations

import pytest

from core.prompt_normalizer import _tooling_guidance

_REQUIRED_FRAGMENTS = (
    "cloud key / cloud model / cloud models / cloud status",
    "never state that a switch, refresh, key save, or connection check",
)


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    from core import runtime_paths

    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    yield
    runtime_paths.configure_runtime_home(None)


def test_no_tools_branch_carries_the_cloud_grounding():
    guidance = _tooling_guidance(has_openclaw_tools=False)
    for fragment in _REQUIRED_FRAGMENTS:
        assert fragment in guidance, fragment


def test_wired_tools_branch_carries_the_cloud_grounding():
    guidance = _tooling_guidance(has_openclaw_tools=True)
    for fragment in _REQUIRED_FRAGMENTS:
        assert fragment in guidance, fragment
