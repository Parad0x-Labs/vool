"""Launch chromium for the required served-browser lane, or fail the gate loudly.

The five required served-chat-UI suites (ops.pytest_shards.REQUIRED_EXECUTED_FILES) are the only
tests in the tree that drive a real browser against the served chat page. Their historical
behavior -- ``importorskip("playwright")`` then ``pytest.skip("no chromium build available")`` --
meant a runner without a chromium binary collected the whole lane, started it, setup-skipped all
of it, and reported green: the execution manifest still saw started == collected and pytest's
exit status stayed 0, so the gate passed having proven nothing about the UI.

The authoritative lane (ops/verify.py, and any full-scope ops/pytest_shards.py run) sets
``VOOL_GATE`` for its children. Under that flag an unavailable browser FAILS with provisioning
instructions -- never skips. Outside the gate (a developer's laptop, a focused non-release run)
availability skips remain, so working without chromium locally is not blocked.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from ops.pytest_shards import GATE_ENV_VAR

PROVISION_HINT = "python -m playwright install chromium"


def gate_active() -> bool:
    """True inside the authoritative verification lane, whose runner sets the variable."""

    return os.environ.get(GATE_ENV_VAR) == "1"


def _start_manager() -> Any:
    from playwright.sync_api import sync_playwright

    return sync_playwright().start()


def launch_chromium() -> tuple[Any, Any]:
    """Start Playwright and launch chromium as ``(manager, browser)``.

    Availability problems skip outside the gate and FAIL under it: the gate must never report
    the served-browser proof as green when no browser ever ran.
    """

    # Test seam (no product code): an explicit executable lets a machine whose playwright
    # package expects a different browser BUILD still run the lane on the chromium it has,
    # instead of silently skipping the UI proof.
    import os as _os

    override = _os.environ.get("VOOL_TEST_CHROMIUM_EXECUTABLE", "").strip()
    kwargs = {"executable_path": override} if override else {}

    if gate_active():
        try:
            manager = _start_manager()
        except Exception as exc:
            pytest.fail(f"required served-browser lane: playwright could not start: {exc}")
        try:
            return manager, manager.chromium.launch(**kwargs)
        except Exception as exc:
            manager.stop()
            pytest.fail(
                f"required served-browser lane: no chromium build available: {exc}. "
                f"Provision it: {PROVISION_HINT}"
            )
    try:
        manager = _start_manager()
    except Exception as exc:
        pytest.skip(f"playwright could not start: {exc}")
    try:
        return manager, manager.chromium.launch(**kwargs)
    except Exception as exc:
        manager.stop()
        pytest.skip(f"no chromium build available: {exc}")
