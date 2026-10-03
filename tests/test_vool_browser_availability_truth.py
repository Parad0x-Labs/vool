"""Availability truth for the vool-browser lane — the two honest refusals.

The lane's capability is REAL availability, never a declared blessing:
with the Playwright driver module absent the runtime must refuse the lane
(unsupported contracts, a truthful disabled execution, the skill filtered
out of selection), and with the driver present the SAME Manual-mode request
must instead pend an approval while no engine starts. Which posture holds is
the interpreter's actual truth (``find_spec``), read once per test — the
product contract under test is that the runtime matches that truth exactly,
in either direction, and never fakes support or absence.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from tests._toolchain_fixtures import executor_kwargs, reset_toolchain_state
from tests._vool_browser_support import enable_browser_policy

LANE_MUTATING = (
    "vool-browser.session.open",
    "vool-browser.session.close",
    "vool-browser.screenshot",
    "vool-browser.download",
    "vool-browser.upload",
)
TRUTHFUL_REASON_MARKERS = ("chromium-family", "playwright driver")


def _driver_present() -> bool:
    return importlib.util.find_spec("playwright") is not None


@pytest.fixture()
def browser_world(tmp_path, monkeypatch):
    from core.mode_permission_policy import reset_mode_permission_state
    from core.runtime_flags import override

    monkeypatch.delenv("VOOL_MCP_CONFIG", raising=False)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_BROWSER_SCRATCH_ROOT", str(tmp_path / "browser-scratch"))
    monkeypatch.setenv("PLAYWRIGHT_ENABLED", "1")
    enable_browser_policy(monkeypatch)
    reset_toolchain_state()
    reset_mode_permission_state()
    with override("plugin_runtime_tools", True):
        yield tmp_path
    from core.vool_browser.sessions import close_all_for_test

    close_all_for_test()
    reset_mode_permission_state()
    reset_toolchain_state()


def test_contracts_match_the_real_driver_truth(browser_world) -> None:
    from core.runtime_tool_contracts import runtime_tool_contract_map

    expected = _driver_present()
    contracts = runtime_tool_contract_map()
    for intent in (*LANE_MUTATING, "vool-browser.navigate", "vool-browser.inspect"):
        contract = contracts[intent]
        assert contract.supported is expected, (
            f"{intent} supported={contract.supported} but the driver truth is {expected}")
    if not expected:
        for intent in LANE_MUTATING:
            reason = str(contracts[intent].unsupported_reason).lower()
            assert all(marker in reason for marker in TRUTHFUL_REASON_MARKERS), (
                f"{intent} refuses without naming the real missing pieces: {reason!r}")


def test_selection_reachability_matches_availability(browser_world) -> None:
    from core.native_skill_library import load_native_library

    contracts = {c.id: c for c in load_native_library().contracts}
    assert "browser" in contracts, sorted(contracts)
    skill = contracts["browser"]
    probe = skill.task_families[0] if skill.task_families else ""

    from core.native_skill_library import select_native_skills

    selection = select_native_skills(
        task_class=probe,
        user_text="browse the product page in an isolated browser session and check the price",
    )
    selected_ids = [c.id for c in selection.selected]
    if _driver_present():
        assert selected_ids, "driver present yet nothing is selectable"
    else:
        assert "browser" not in selected_ids, (
            "an unavailable lane must not be selected: availability truth, not a declared blessing")


def test_manual_mode_refusal_is_the_one_the_truth_allows_and_nothing_runs(browser_world) -> None:
    """One request, two honest outcomes by availability — and never a started engine."""
    from core.tool_intent_executor import execute_tool_intent

    scratch = Path(browser_world / "browser-scratch")
    result = execute_tool_intent(
        {"intent": "vool-browser.session.open",
         "arguments": {"session": "avail-truth-s1", "start_url": "http://127.0.0.1:9/?availability"}},
        **executor_kwargs("availability-truth"),
    )
    assert not result.ok, "a mutating browser act succeeded without any approval authority"
    response = str(getattr(result, "response_text", "")).lower()
    if _driver_present():
        assert result.status in {"approval_required", "pending_approval"}, result.status
    else:
        assert result.status == "disabled", result.status
        assert all(marker in response for marker in TRUTHFUL_REASON_MARKERS), response
    # No engine startup in either posture: the durable registry, any session
    # profile and every scratch byte are the filesystem truth of "nothing ran".
    assert not (scratch / "registry.json").exists(), "a session was registered without approval"
    leftovers = [p for p in scratch.rglob("*") if p.exists()] if scratch.exists() else []
    assert leftovers == [], f"the refused lane left filesystem effects: {leftovers}"
