"""A read-only tool may never be DENIED for being unclassified.

Found by the 2026-08-29 tooling census. Only 11 of 73 contracts declared
`permission_actions`; the rest fall through to intent-string matching in
`actions_for_tool`, and anything that lands on `UNKNOWN_SIDE_EFFECT` is DENIED in
plan/auto/bypass. Four contracts that declare `side_effect_class="read_only"` were
denied in exactly the modes meant to be more permissive — the same defect the policy's
own comment records for `pdf.*`/`skill.*`, which were fixed by declaring their actions.

This test is the general form, not a fix for four names: EVERY read-only contract must
resolve to real read actions, so a newly added one cannot repeat the defect silently.
"""

from __future__ import annotations

import pytest

from core.mode_permission_policy import (
    MODE_PERMISSION_MATRIX,
    OperatingMode,
    PermissionAction,
    PermissionEffect,
    actions_for_tool,
)
from core.runtime_tool_contracts import runtime_tool_contracts

#: `respond.direct` returns inside the executor BEFORE the permission gate is consulted
#: (core/tool_intent_executor.py), so its classification never gates anything.
_NOT_GATED = {"respond.direct"}

#: Derives its actions from the command string it is given; with empty arguments it has
#: nothing to classify, which is correct rather than a defect.
_ARGUMENT_DERIVED = {"sandbox.run_command"}


def _read_only_intents() -> list[str]:
    return [
        contract.intent
        for contract in runtime_tool_contracts()
        if str(getattr(contract, "side_effect_class", "")) == "read_only"
        and contract.intent not in _NOT_GATED
        and contract.intent not in _ARGUMENT_DERIVED
    ]


def test_the_census_found_read_only_contracts_to_check() -> None:
    """Guard the guard: if this list ever empties, the test below proves nothing."""
    assert len(_read_only_intents()) >= 4


@pytest.mark.parametrize("intent", _read_only_intents())
def test_a_read_only_tool_never_classifies_as_an_unknown_side_effect(intent: str) -> None:
    actions = actions_for_tool(intent, {})
    assert actions, f"{intent} resolved to no permission actions at all"
    assert PermissionAction.UNKNOWN_SIDE_EFFECT not in actions, (
        f"{intent} declares side_effect_class='read_only' but classifies as an unknown "
        "side effect, which the mode matrix DENIES in plan/auto/bypass — declare "
        "permission_actions on its contract"
    )


#: Plan mode denies network and settings actions on purpose — the policy comment states the
#: carve-out exists so "Plan mode's existing flat network denial is untouched". A read-only tool
#: that reaches the network is therefore legitimately denied THERE, and only there.
_DELIBERATE_PLAN_DENIALS = {
    PermissionAction.USE_NETWORK,
    PermissionAction.USE_BROWSER,
    PermissionAction.PUBLIC_READ_ONLY_RETRIEVAL,
    PermissionAction.ACCESS_EXTERNAL_PROVIDERS,
    PermissionAction.CHANGE_SETTINGS,
    PermissionAction.CHANGE_PROVIDER_CONFIGURATION,
    PermissionAction.SEND_EXTERNAL_MESSAGES,
    PermissionAction.FINANCIAL_ACTION,
}


@pytest.mark.parametrize("intent", _read_only_intents())
def test_a_read_only_tool_is_never_denied_in_auto_mode(intent: str) -> None:
    """Auto is the most permissive non-bypass mode: a read-only tool denied here is a defect.

    This is the assertion that catches the census finding — the four tools were denied in
    BOTH plan and auto, which no deliberate policy explains.
    """
    row = MODE_PERMISSION_MATRIX[OperatingMode.AUTO]
    denied = [
        action.value
        for action in actions_for_tool(intent, {})
        if row.get(action) is PermissionEffect.DENY
    ]
    assert not denied, f"{intent} is denied in auto mode via {denied}"


@pytest.mark.parametrize("intent", _read_only_intents())
def test_plan_mode_denies_a_read_only_tool_only_for_a_stated_policy_reason(intent: str) -> None:
    """Plan may deny network/settings by design; it may not deny a LOCAL read."""
    row = MODE_PERMISSION_MATRIX[OperatingMode.PLAN]
    unexplained = [
        action.value
        for action in actions_for_tool(intent, {})
        if row.get(action) is PermissionEffect.DENY and action not in _DELIBERATE_PLAN_DENIALS
    ]
    assert not unexplained, (
        f"{intent} is denied in plan mode via {unexplained}, which no stated policy explains"
    )


def test_the_four_census_findings_specifically_resolve_to_read_actions() -> None:
    """The named regressions, pinned so a contract edit cannot quietly undo them."""
    expected = {
        "workspace.identity": {PermissionAction.READ_FILES, PermissionAction.LIST_DIRECTORIES},
        "machine.event_log_errors": {PermissionAction.READ_FILES},
        "demo.plan": {PermissionAction.READ_FILES},
        "sell.quote": {PermissionAction.READ_FILES},
    }
    declared = {contract.intent for contract in runtime_tool_contracts()}
    for intent, wanted in expected.items():
        if intent not in declared:
            continue  # a contract may be withdrawn; absence is not this test's business
        assert set(actions_for_tool(intent, {})) == wanted, intent


#: Actions that mutate something. A contract claiming `read_only` must resolve to none of them.
_MUTATING_ACTIONS = {
    PermissionAction.CREATE_FILES,
    PermissionAction.MODIFY_FILES,
    PermissionAction.OVERWRITE_EXISTING_FILES,
    PermissionAction.DELETE_FILES,
    PermissionAction.RUN_SIDE_EFFECTING_COMMANDS,
    PermissionAction.INSTALL_DEPENDENCIES,
    PermissionAction.DEPLOY,
    PermissionAction.SEND_EXTERNAL_MESSAGES,
    PermissionAction.FINANCIAL_ACTION,
    PermissionAction.CHANGE_SETTINGS,
    PermissionAction.CHANGE_SECURITY_SETTINGS,
    PermissionAction.GIT_COMMIT,
    PermissionAction.GIT_PUSH,
    PermissionAction.GIT_MERGE_REBASE,
    PermissionAction.GIT_RESET_CLEAN,
}


@pytest.mark.parametrize("intent", _read_only_intents())
def test_a_read_only_contract_never_resolves_to_a_mutating_action(intent: str) -> None:
    """`read_only` is an UNCONDITIONAL PERMIT at the audit boundary.

    `core/agent_runtime/audit_policy.py::audit_tool_permission_denial` returns "" — permit —
    the moment `side_effect_class == "read_only"`, before any write-scope check. So a tool
    that writes while claiming read_only bypasses the audit write gate entirely. Found live
    2026-08-29: `web0.open_builder_draft` (create_files) and `web0.compile_preview`
    (modify_files) both claimed read_only.
    """
    mutating = sorted(
        action.value for action in actions_for_tool(intent, {}) if action in _MUTATING_ACTIONS
    )
    assert not mutating, (
        f"{intent} declares side_effect_class='read_only' but resolves to mutating action(s) "
        f"{mutating} — read_only is an unconditional permit at the audit gate, so this tool "
        "could write during a read-only audit"
    )
