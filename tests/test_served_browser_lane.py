"""The served-browser lane may not silently become green-with-zero-executed.

F2 from the false-green census (2026-08-27): the five required served-chat-UI suites skipped
their entire run whenever playwright or a chromium build was missing -- collected, started,
setup-skipped, green. These tests pin the repair from both ends: the launcher fails under the
authoritative gate (VOOL_GATE), and the shard machinery refuses a required lane that executed
nothing. No real browser is needed here; every launcher seam is monkeypatched.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ops.pytest_shards import REQUIRED_EXECUTED_FILES
from tests import served_browser

REPO_ROOT = Path(__file__).resolve().parent.parent

EXPECTED_REQUIRED_FILES = (
    "tests/test_activity_chat_scope.py",
    "tests/test_chat_activity_search.py",
    "tests/test_chat_page_layout_geometry.py",
    "tests/test_composer_model_anchor.py",
    "tests/test_master_live_incident_contracts.py",
)


class _FakeManager:
    """Shape of the playwright sync manager the launcher relies on."""

    def __init__(self, launch_error: Exception | None) -> None:
        self._launch_error = launch_error
        self.stop_calls = 0
        self.chromium = self

    def launch(self) -> object:
        if self._launch_error is not None:
            raise self._launch_error
        return object()

    def stop(self) -> None:
        self.stop_calls += 1


def test_the_required_browser_lane_is_pinned_in_one_place() -> None:
    assert REQUIRED_EXECUTED_FILES == EXPECTED_REQUIRED_FILES


def test_every_required_browser_file_launches_through_the_gate_helper() -> None:
    for name in REQUIRED_EXECUTED_FILES:
        path = REPO_ROOT / name
        assert path.is_file(), f"required served-browser suite missing: {name}"
        source = path.read_text(encoding="utf-8")
        assert "served_browser.launch_chromium()" in source, (
            f"{name} must obtain its browser through tests.served_browser.launch_chromium()"
        )
        # An inline importorskip/pytest.skip in these files is exactly the false-green shape this
        # lane exists to prevent; every availability decision lives in the gate-aware helper.
        assert "importorskip" not in source, f"{name} re-introduced importorskip"
        assert "pytest.skip" not in source, f"{name} re-introduced an inline availability skip"


def test_the_gate_env_var_is_the_authoritative_lane_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(served_browser.GATE_ENV_VAR, raising=False)
    assert served_browser.gate_active() is False
    monkeypatch.setenv(served_browser.GATE_ENV_VAR, "1")
    assert served_browser.gate_active() is True


def _gate_launch_must_fail(*, match: str | None = None) -> None:
    """Run launch_chromium and require a loud pytest failure.

    Asserted structurally rather than via pytest.raises(pytest.fail.Exception): a regression of
    the helper from fail to skip would raise Skipped, which pytest reports as a green-ish skip
    instead of this test failing -- exactly the false-green shape this lane exists to prevent.
    """

    try:
        served_browser.launch_chromium()
    except pytest.fail.Exception as exc:
        if match is not None:
            assert match in str(exc), f"gate failure must name the defect: {exc}"
        return
    except BaseException as exc:  # a skip here would be silent green
        raise AssertionError(
            f"the gate lane must FAIL loudly, not {type(exc).__name__}: {exc}"
        ) from exc
    raise AssertionError("the gate lane neither failed nor raised")


def test_under_the_gate_a_dead_playwright_fails_never_skips(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(served_browser.GATE_ENV_VAR, "1")

    def broken_start() -> object:
        raise RuntimeError("no playwright here")

    monkeypatch.setattr(served_browser, "_start_manager", broken_start)
    _gate_launch_must_fail(match="playwright could not start")


def test_under_the_gate_a_missing_chromium_fails_and_stops_the_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(served_browser.GATE_ENV_VAR, "1")
    manager = _FakeManager(RuntimeError("Executable doesn't exist at ..."))
    monkeypatch.setattr(served_browser, "_start_manager", lambda: manager)
    _gate_launch_must_fail(match="no chromium build available")
    assert manager.stop_calls == 1, "a failed gate launch must not leak the playwright manager"


def test_outside_the_gate_availability_problems_still_skip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(served_browser.GATE_ENV_VAR, raising=False)

    def broken_start() -> object:
        raise RuntimeError("no playwright here")

    monkeypatch.setattr(served_browser, "_start_manager", broken_start)
    with pytest.raises(pytest.skip.Exception, match="playwright could not start"):
        served_browser.launch_chromium()

    manager = _FakeManager(RuntimeError("no chromium build"))
    monkeypatch.setattr(served_browser, "_start_manager", lambda: manager)
    with pytest.raises(pytest.skip.Exception, match="no chromium build available"):
        served_browser.launch_chromium()
    assert manager.stop_calls == 1
