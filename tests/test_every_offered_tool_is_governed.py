"""Every tool the model is offered must declare what it does to the machine.

A tool with no contract has no `side_effect_class` and no `approval_requirement`, so
`decide_tool_call` cannot gate it by anything except matching on the intent STRING — which is
exactly the mechanism `core/mode_permission_policy.py:461-465` says no new family can participate
in. Measured on `main` @ 9e6f769: 65 intents are offered to the model and 60 have contracts, so 13
are advertised ungoverned. Three of them — `operator.cleanup_temp_files`, `operator.move_path`,
`operator.schedule_calendar_event` — delete, move and write on the operator's machine.

This matters most for the models this product actually ships with. A weak free model choosing its
own tool is the scenario under test in this audit; an ungoverned destructive intent is the one it
must not be able to reach without a gate.

The test is written against the LIVE surface rather than a hardcoded list, so a new uncontracted
intent added tomorrow fails here on the day it is added.
"""
from __future__ import annotations

import pytest

from core.runtime_tool_contracts import runtime_tool_contract_map
from core.tool_intent_executor import runtime_tool_specs

# Values already in use across the 60 existing contracts. A new contract must speak this
# vocabulary rather than inventing a synonym, or the permission layer cannot read it.
_KNOWN_EFFECTS = {
    "read_only", "workspace_write", "network_send", "network_publish", "media_generation",
    "creative_state", "wallet_spend", "credit_spend", "builder_state", "validation_command",
    "task_orchestration", "sandbox_command", "runtime_capability_change",
}
_KNOWN_APPROVALS = {"none", "runtime_policy", "explicit_user_opt_in"}


def _offered_intents() -> set[str]:
    return {str(spec.get("intent")) for spec in runtime_tool_specs() if spec.get("intent")}


# One reviewed exception, with its reason, so this test still fails for anything NEW.
# `hive.list_available` is read-only, and contracting it promotes the hive capability in
# `operator_capability_ledger`, which judges hive by actual dispatch reachability — four ledger
# tests fail on a box where hive is unconfigured. Contracting it needs `supported` to track hive
# configuration, which belongs to the ledger's own lane. Deferred, not forgotten.
_REVIEWED_UNCONTRACTED = {"hive.list_available"}


def test_no_intent_is_offered_to_a_model_without_a_contract() -> None:
    ungoverned = sorted(
        _offered_intents() - set(runtime_tool_contract_map()) - _REVIEWED_UNCONTRACTED
    )

    assert not ungoverned, (
        f"{len(ungoverned)} intent(s) are advertised to every model with no contract, so they "
        f"carry no side-effect class and no approval requirement: {ungoverned}"
    )


def test_the_reviewed_exception_is_still_read_only() -> None:
    """The exception is tolerable only while it cannot change anything. If `hive.list_available`
    ever becomes effectful while uncontracted, this fails."""
    specs = {str(s.get("intent")): s for s in runtime_tool_specs() if s.get("intent")}
    for intent in _REVIEWED_UNCONTRACTED:
        spec = specs.get(intent)
        if spec is not None:
            assert spec.get("read_only") is True, f"{intent} is uncontracted AND effectful"


def test_every_contract_speaks_the_permission_layer_s_vocabulary() -> None:
    bad_effect: list[str] = []
    bad_approval: list[str] = []
    for intent, contract in runtime_tool_contract_map().items():
        if str(contract.side_effect_class) not in _KNOWN_EFFECTS:
            bad_effect.append(f"{intent}={contract.side_effect_class}")
        if str(contract.approval_requirement) not in _KNOWN_APPROVALS:
            bad_approval.append(f"{intent}={contract.approval_requirement}")

    assert not bad_effect, f"unknown side_effect_class: {bad_effect}"
    assert not bad_approval, f"unknown approval_requirement: {bad_approval}"


def test_a_tool_that_changes_the_machine_is_never_approval_none() -> None:
    """A destructive, outbound or spending effect with `approval_requirement="none"` is a gate that
    does not close.

    The exemption list is narrower than "everything harmless" on purpose, and wider than
    `read_only` because the first draft of this test was wrong. `builder_state` and
    `creative_state` are in-app, session-scoped, reversible draft edits — a site builder that
    demanded an approval per block edit would be unusable, and that is a deliberate product choice
    rather than a missing gate. What is NOT exempt is anything that leaves the process, touches the
    operator's machine, or spends: measured on `main` @ 9e6f769,
    `marketplace.purchase_knowledge` declared `credit_spend` with `approval_requirement="none"`.
    """
    exempt = {"read_only", "validation_command", "builder_state", "creative_state"}
    ungated = sorted(
        f"{intent} ({contract.side_effect_class})"
        for intent, contract in runtime_tool_contract_map().items()
        if str(contract.side_effect_class) not in exempt
        and str(contract.approval_requirement) == "none"
    )

    assert not ungated, f"mutating or outbound tools with no approval requirement: {ungated}"


@pytest.mark.parametrize(
    "intent",
    [
        "operator.cleanup_temp_files",
        "operator.move_path",
        "operator.schedule_calendar_event",
    ],
)
def test_the_destructive_operator_intents_are_gated(intent: str) -> None:
    """Named individually because these three delete, move and write on the operator's machine and
    were the specific examples recorded as BROKEN in the capability matrix."""
    contract = runtime_tool_contract_map().get(intent)

    assert contract is not None, f"{intent} is offered to models with no contract at all"
    assert str(contract.side_effect_class) != "read_only", (
        f"{intent} changes the machine but declares itself read_only"
    )
    assert str(contract.approval_requirement) != "none", f"{intent} is not gated by any approval"
