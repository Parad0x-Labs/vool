"""A machine clause in a conductor plan binds the machine capability, not a model-named guess.

Measured live at 1f8dba98 on "Convert 1000 TRY to USD. And how much free disk space do I have?":
the conductor claimed the mixed turn (correctly -- the arbitration now sees the machine clause),
the planner model named `quantitative_reasoning` for the disk clause, `_resolve_clause` let the
named operation win because it expanded, and the node failed against a fact the host could have
stated exactly. The runtime's own claim registry (`core.agent_runtime.intent_claims`) already
knew that clause was `disk_usage`; nothing propagated that proof into the plan.

The repair has two halves, and the test asserts both:

* `machine_observation` is registered and expands a machine-owned clause to the read-only
  machine intent the claim names, through the conductor's permission-gated tool seam;
* `_resolve_clause` lets an operation whose recognizer IS the runtime's deterministic claim
  (`outranks_planner_naming`) bind before the planner's naming -- so even when the model names
  the wrong operation, the binding does not follow it.
"""
from __future__ import annotations

from core.conductor.planner import ProposedClause, build_plan_from_clauses
from core.conductor.registry import operation_spec
from core.conductor.shared_context import extract_shared_context


def _plan(original: str, named: list[tuple[str, str]]):
    clauses = [ProposedClause(i, req, op, ()) for i, (req, op) in enumerate(named)]
    return build_plan_from_clauses(
        clauses,
        original_request=original,
        plan_id="test",
        shared_context=extract_shared_context(original),
    )


def test_the_operation_is_registered_and_outranks_planner_naming() -> None:
    spec = operation_spec("machine_observation")
    assert spec is not None, "machine_observation is not registered; the rest of this file is moot"
    assert spec.outranks_planner_naming


def test_a_misnamed_disk_clause_binds_machine_disk_usage() -> None:
    plan = _plan(
        "Convert 1000 TRY to USD. And how much free disk space do I have?",
        [("1000 TRY to USD", "fx_quote"),
         ("how much free disk space do I have?", "quantitative_reasoning")],
    )
    by_operation = {node.operation: node for node in plan.nodes}
    machine = by_operation.get("machine_observation")
    assert machine is not None, f"no machine node; plan is {[(n.node_id, n.operation) for n in plan.nodes]}"
    assert machine.arguments["intent"] == "machine.disk_usage"


def test_a_misnamed_specs_clause_binds_machine_inspect_specs() -> None:
    plan = _plan(
        "How much RAM does this machine have? And 1000 TRY to USD?",
        [("How much RAM does this machine have?", "quantitative_reasoning"),
         ("1000 TRY to USD", "fx_quote")],
    )
    intents = [
        node.arguments.get("intent") for node in plan.nodes if node.operation == "machine_observation"
    ]
    assert intents == ["machine.inspect_specs"]


def test_a_process_question_beside_a_weather_clause_binds_list_processes() -> None:
    plan = _plan(
        "What's the weather in Kaunas? And what processes are running?",
        [("What's the weather in Kaunas?", "weather_lookup"),
         ("what processes are running?", "factual_explanation")],
    )
    intents = [
        node.arguments.get("intent") for node in plan.nodes if node.operation == "machine_observation"
    ]
    assert intents == ["machine.list_processes"]


def test_a_target_the_recognizer_did_not_extract_is_not_invented() -> None:
    """"my Downloads folder" claims list_directory but names no explicit path; binding one would
    be a guess, so the clause must not become a machine node with a fabricated path."""
    plan = _plan(
        "1000 TRY to USD. Also list the files in my Downloads folder.",
        [("1000 TRY to USD", "fx_quote"),
         ("list the files in my Downloads folder", "quantitative_reasoning")],
    )
    for node in plan.nodes:
        assert node.arguments.get("intent") != "machine.list_directory" or node.arguments.get(
            "intent_arguments"
        )


def test_non_machine_clauses_are_not_claimed() -> None:
    plan = _plan(
        "What is 137 x 29? Also get the current weather for Kaunas.",
        [("What is 137 x 29?", "calculation"),
         ("get the current weather for Kaunas", "weather_lookup")],
    )
    assert all(node.operation != "machine_observation" for node in plan.nodes)


def test_sabotage_without_precedence_the_wrong_operation_wins() -> None:
    """Disabling the precedence flag must restore the mis-binding, so this cannot pass vacuously."""
    from core.conductor import registry

    spec = operation_spec("machine_observation")
    assert spec is not None and spec.outranks_planner_naming, "precedence flag missing; sabotage moot"
    registry.register_operation(
        registry.OperationSpec(
            **{**spec.__dict__, "outranks_planner_naming": False}
        ),
        replace=True,
    )
    try:
        plan = _plan(
            "Convert 1000 TRY to USD. And how much free disk space do I have?",
            [("1000 TRY to USD", "fx_quote"),
             ("how much free disk space do I have?", "quantitative_reasoning")],
        )
        assert all(node.operation != "machine_observation" for node in plan.nodes)
    finally:
        registry.register_operation(spec, replace=True)
