"""C06 gate law — the permission gate, the native skill, and the typed surface.

- GATE: in the daemon's default Manual mode a browser session (a real capability
  grant: process + disposable profile) PENDS an approval; read ops on an OPEN
  session ride as public read-only retrieval; Plan mode denies the lane flat.
- SELECTION: the shipped native skill is reachable through the production typed
  signals and names only tools the contract map knows.
- SURFACE: the lane's intents are contracted, dispatchable and census-visible;
  the lane declares no authority of its own (no skill grants, no plugin self-approval).
"""
from __future__ import annotations

import pytest

from tests._toolchain_fixtures import executor_kwargs, reset_toolchain_state
from tests._vool_browser_support import enable_browser_policy


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


LANE_INTENTS = (
    "vool-browser.session.open",
    "vool-browser.session.close",
    "vool-browser.session.status",
    "vool-browser.navigate",
    "vool-browser.inspect",
    "vool-browser.click",
    "vool-browser.type",
    "vool-browser.assert",
    "vool-browser.screenshot",
    "vool-browser.download",
    "vool-browser.upload.stage",
    "vool-browser.upload",
    "vool-browser.permission.grant",
    "vool-browser.permission.list",
    "vool-browser.cancel",
)


def test_every_lane_intent_is_contracted_and_dispatchable(browser_world) -> None:
    from core.runtime_tool_contracts import runtime_tool_contract_map

    contracts = runtime_tool_contract_map()
    missing = [intent for intent in LANE_INTENTS if intent not in contracts]
    assert missing == [], f"uncontracted lane intents: {missing}"
    for intent in LANE_INTENTS:
        contract = contracts[intent]
        assert contract.supported, f"{intent} contracted but unsupported"
        assert contract.side_effect_class in {"read_only", "workspace_write"}, intent
        assert contract.permission_actions, f"{intent} declares no permission actions"


def test_manual_mode_pends_a_browser_session_and_nothing_runs(browser_world) -> None:
    """Manual mode (the default): a session request with NO approval authority
    must pend with an approval request, and no engine may start."""
    from core.mode_permission_policy import reset_mode_permission_state

    reset_mode_permission_state()
    from core.tool_intent_executor import execute_tool_intent

    result = execute_tool_intent(
        {"intent": "vool-browser.session.open",
         "arguments": {"session": "s1", "start_url": "http://127.0.0.1:9/?gate"}},
        **executor_kwargs("gate-check"),
    )
    assert not result.ok, "a mutating session act ran without any approval authority"
    assert result.status in {"approval_required", "pending_approval"}, result.status


def test_plan_mode_denies_the_lane_flat(browser_world) -> None:
    from core.mode_permission_policy import (
        OperatingMode,
        decide_tool_call,
        reset_mode_permission_state,
        set_active_mode,
    )

    reset_mode_permission_state()
    set_active_mode("plan-gate-session", OperatingMode.PLAN)
    decision = decide_tool_call(
        intent="vool-browser.navigate",
        arguments={"session": "s1", "url": "http://example.com/"},
        task_id="plan-gate",
        source_context={"runtime_session_id": "plan-gate-session"},
    )
    assert decision.effect.name == "DENY", decision


def test_the_native_skill_is_shipped_selectable_and_well_formed(browser_world) -> None:
    from core.native_skill_library import load_native_library, select_native_skills

    contracts = {c.id: c for c in load_native_library().contracts}
    assert "browser" in contracts, sorted(contracts)
    skill = contracts["browser"]
    assert skill.task_families or skill.capability_families

    selection = select_native_skills(
        task_class=skill.task_families[0] if skill.task_families else "",
        user_text="browse the product page in an isolated browser session and check the price",
    )
    if not any(c.id == "browser" for c in selection.selected):
        # constructive reachability witness: disable blockers per the library law
        from core.native_skill_library import set_skill_enabled

        disabled = []
        try:
            for _ in range(8):
                blockers = [c.id for c in select_native_skills(
                    task_class=skill.task_families[0] if skill.task_families else "",
                    user_text="browse the product page in an isolated browser session and check the price",
                ).selected]
                if "browser" in blockers:
                    break
                assert blockers, "selection empty and the browser skill unreachable"
                victim = blockers[0]
                assert set_skill_enabled(victim, False)["status"] == "ok"
                disabled.append(victim)
            else:
                raise AssertionError("browser skill unreachable with blockers disabled")
        finally:
            for victim in disabled:
                set_skill_enabled(victim, True)


def test_the_skill_validates_against_the_real_contract_map(browser_world) -> None:
    from pathlib import Path

    from core.skill_tools import validate_skill

    skill_md = Path(__file__).resolve().parents[1] / "skills" / "vool-browser" / "SKILL.md"
    assert skill_md.is_file(), "the lane ships no native skill"
    verdict = validate_skill(str(skill_md.parent))
    assert verdict["status"] == "ok", verdict.get("problems")
    assert verdict["unknown_tools"] == [], verdict["unknown_tools"]


def test_the_lane_declares_no_self_authority(browser_world) -> None:
    """Don't-do #11: skills grant no permissions; the lane's SKILL.md must not
    claim approval powers, and the mutating intents must stay behind the gate."""
    from pathlib import Path

    from core.mode_permission_policy import PermissionEffect, decide_tool_call

    skill_md = Path(__file__).resolve().parents[1] / "skills" / "vool-browser" / "SKILL.md"
    text = skill_md.read_text(encoding="utf-8").lower()
    assert "approval_requirement" not in text and "explicit_user_opt_in: true" not in text

    for intent in ("vool-browser.session.open", "vool-browser.download",
                   "vool-browser.upload", "vool-browser.session.close"):
        decision = decide_tool_call(
            intent=intent,
            arguments={},
            task_id="no-self-authority",
            source_context={},
        )
        assert decision.effect is not PermissionEffect.ALLOW, (
            f"{intent} is ALLOW in an authority-less manual context")


def test_the_census_pin_covers_the_new_surfaces(browser_world) -> None:
    """The lane added tool intents as builtin contracts; the census law is that
    the pin still matches discovery exactly and nothing fell out as
    LEGACY_UNMIGRATED (the same complete-and-pinned law the convergence lane
    pins, re-proven here WITH the lane loaded)."""
    from core.command_registry.census import load_pin, run_census

    report = run_census()
    totals = report.totals()
    assert totals["LEGACY_UNMIGRATED"] == 0
    pin = load_pin()
    assert pin and pin.get("items"), "census snapshot must exist"
    assert set(pin["items"]) == {i.census_id for i in report.items}, "pin must match discovery exactly"
    assert pin["totals"] == totals
