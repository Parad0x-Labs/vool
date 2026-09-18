"""One intent, one declaration — and the contract is the authority.

Found 2026-08-29 by the tooling census. `core/execution/capabilities.py::runtime_tool_specs()`
assembles hard-coded external-lane entries and then appends the contract-derived rows, so twelve
intents were declared TWICE. `build_cloud_tool_definitions` merges duplicates with `.update()`,
which means the description the model receives was decided by list order, not by any declaration.

The worst case was the runtime's only funds-touching tool. The two `pay.x402` declarations said
opposite things:

    hard-coded : "... Safe by default: only returns the quote ... so a call never moves funds"
    contract   : "Settle an x402 payment. Moves real value off this machine."

These tests fail if duplicates return, if a non-contract description wins, or if the model-facing
definition count silently grows again.
"""

from __future__ import annotations

from collections import Counter

from core.execution.capabilities import runtime_tool_specs
from core.runtime_tool_contracts import runtime_tool_contracts


def _intent_of(spec: dict) -> str:
    return str(spec.get("intent") or spec.get("name") or "")


def test_no_intent_is_declared_to_the_model_twice() -> None:
    specs = runtime_tool_specs()
    counts = Counter(_intent_of(spec) for spec in specs)
    duplicated = sorted(intent for intent, count in counts.items() if count > 1)
    assert not duplicated, (
        f"{len(duplicated)} intent(s) declared more than once: {duplicated}. A duplicate is merged "
        "by .update(), so the surviving description depends on list order rather than on the contract"
    )


def test_every_spec_carries_an_intent() -> None:
    """The dedupe keys on intent; a spec without one would be dropped silently."""
    assert all(_intent_of(spec) for spec in runtime_tool_specs())


def test_no_spec_contradicts_its_contract_about_moving_value() -> None:
    """A richer lane-specific description is fine; a CONTRADICTING one is the defect.

    The hard-coded rows deliberately carry wording the terser contract line lacks ("after explicit
    approval", "the heaviest processes by CPU and memory"), so identity is the wrong invariant.
    What may never happen is a spec telling the model a tool is harmless while its contract says it
    moves real value — which is exactly what `pay.x402` did.
    """
    by_intent = {_intent_of(spec): spec for spec in runtime_tool_specs()}
    contracts = {c.intent: c for c in runtime_tool_contracts() if getattr(c, "supported", True)}

    offenders = []
    for intent, contract in contracts.items():
        spec = by_intent.get(intent)
        if spec is None:
            continue
        contract_moves_value = "moves real value" in str(contract.description or "").lower()
        if not contract_moves_value:
            continue
        spec_text = str(spec.get("description") or "").lower()
        reassures = "never moves funds" in spec_text or "charges nothing" in spec_text
        if reassures or "moves real value" not in spec_text:
            offenders.append(intent)
    assert not offenders, (
        f"{offenders} are declared to the model without the value-moving warning their contract "
        "carries; a spend tool may never be described as harmless"
    )


def test_the_funds_touching_tool_states_that_it_moves_value() -> None:
    """The regression that made this test family exist. Never let it read as harmless again."""
    by_intent = {_intent_of(spec): spec for spec in runtime_tool_specs()}
    pay = by_intent.get("pay.x402")
    if pay is None:
        return  # the tool may be withdrawn; absence is not this test's business
    description = str(pay.get("description") or "").lower()
    assert "moves real value" in description, (
        "pay.x402 must be described to the model as moving real value; the stale hard-coded "
        "declaration claimed a call 'never moves funds'"
    )


def test_deduping_did_not_drop_any_intent() -> None:
    """Dedupe must collapse duplicates, never remove a capability."""
    intents = {_intent_of(spec) for spec in runtime_tool_specs()}
    for expected in ("web.search", "web.research", "pay.x402", "sell.quote", "respond.direct"):
        assert expected in intents, f"{expected} disappeared from the model-facing surface"


# --------------------------------------------------------------- the size cap is not a gate

def test_the_payload_size_cap_does_not_pretend_to_be_a_safety_gate() -> None:
    """`apply_local_execution_safety` returned True for `rm -rf /` while being called at the
    sandbox chokepoint under the comment "Pre-flight check with the ExecutionGate" and reporting
    "Liquefy safety guard blocked execution" on False. It is a 5 MiB size cap. The real gate is
    `core/execution_gate.py::evaluate_command`, which does block those commands — verified below,
    so this test also proves the rename did not remove a real protection.
    """
    from core.execution_gate import ExecutionGate
    from core.liquefy_bridge import execution_payload_within_size_limit

    # The cap permits dangerous commands, because bounding size is all it does.
    assert execution_payload_within_size_limit({"workspace": "/tmp"}, {"cmd": "rm -rf /"}) is True
    # ...and refuses only oversized payloads.
    assert execution_payload_within_size_limit({"workspace": "/tmp"}, {"cmd": "x" * (6 * 1024 * 1024)}) is False

    # The REAL gate is the thing that protects the machine.
    verdict = ExecutionGate().evaluate_command("rm -rf /")
    assert verdict["decision"] == "blocked", verdict


def test_the_sandbox_chokepoint_no_longer_reports_a_safety_block_for_a_size_cap() -> None:
    """The operator-facing string must name the real reason."""
    import pathlib

    source = pathlib.Path("sandbox/sandbox_runner.py").read_text(encoding="utf-8")
    assert "Liquefy safety guard blocked execution" not in source
    assert "command payload exceeded the local size cap" in source
