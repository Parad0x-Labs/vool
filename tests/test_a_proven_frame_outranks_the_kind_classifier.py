"""A proven typed frame outranks the generic clause-kind classifier.

Measured live at 5b901925 on "Convert 1000 TRY to USD. And how much free disk space do I have?":
the planner named `fx_quote` for the first clause; `parse_turn_ir` classifies "Convert ..." as
TRANSFORM; the fx capability does not serve TRANSFORM, so the clause became an unresolved node
reading "fx_quote is unavailable for this request: foreign exchange capability does not serve
transform requests" -- while the requirement ledger's OWN formal proof (`1000 TRY to USD` is an
ISO-4217 frame) projected a second fx node that ran and succeeded. The final answer printed the
conversion AND listed the same request under "Could not be answered: the available answer path
does not safely match this request". One request, two verdicts, both shipped.

The weaker reading may not refuse the stronger one: when the turn's semantic proof established a
frame of the operation's own family inside this clause, `_resolve_clause` skips the kind check
and lets the operation's own expander -- which parses "Convert 1000 TRY to USD." correctly and
declines "Convert this document to Spanish" -- be the admission.
"""
from __future__ import annotations

from core.conductor import planner
from core.conductor.planner import ProposedClause, build_plan_from_clauses
from core.conductor.shared_context import extract_shared_context

_FX_DISK = "Convert 1000 TRY to USD. And how much free disk space do I have?"
_FX_DISK_CLAUSES = [
    ("Convert 1000 TRY to USD.", "fx_quote"),
    ("how much free disk space do I have?", "quantitative_reasoning"),
]


def _plan(original: str, named: list[tuple[str, str]]):
    clauses = [ProposedClause(i, req, op, ()) for i, (req, op) in enumerate(named)]
    return build_plan_from_clauses(
        clauses,
        original_request=original,
        plan_id="test",
        shared_context=extract_shared_context(original),
    )


def test_a_proven_conversion_clause_binds_fx_despite_the_transform_verb() -> None:
    plan = _plan(_FX_DISK, _FX_DISK_CLAUSES)
    fx_nodes = [node for node in plan.nodes if node.operation == "fx_quote"]
    assert len(fx_nodes) == 1, f"plan nodes: {[(n.node_id, n.operation) for n in plan.nodes]}"
    assert fx_nodes[0].arguments.get("base") == "TRY"
    assert fx_nodes[0].arguments.get("quote") == "USD"


def test_the_same_request_is_not_also_reported_unanswered() -> None:
    """One request, one node: no refused clause node beside the serving one."""
    plan = _plan(_FX_DISK, _FX_DISK_CLAUSES)
    assert not [
        node for node in plan.nodes
        if node.operation == "unresolved" and "TRY" in node.request_text
    ]


def test_a_transform_that_is_not_a_proven_conversion_stays_refused() -> None:
    plan = _plan(
        "Convert this document to Spanish. And how much free disk space do I have?",
        [("Convert this document to Spanish", "fx_quote"),
         ("how much free disk space do I have?", "quantitative_reasoning")],
    )
    assert all(node.operation != "fx_quote" for node in plan.nodes)


def test_sabotage_without_the_proof_override_the_contradiction_returns() -> None:
    real = planner._proven_families_for_clause
    planner._proven_families_for_clause = lambda ledger, text: frozenset()
    try:
        plan = _plan(_FX_DISK, _FX_DISK_CLAUSES)
        refused = [
            node for node in plan.nodes
            if node.operation == "unresolved" and "TRY" in node.request_text
        ]
        assert refused, "without the override the refusal did not return; sabotage moot"
    finally:
        planner._proven_families_for_clause = real
    plan = _plan(_FX_DISK, _FX_DISK_CLAUSES)
    assert not [
        node for node in plan.nodes
        if node.operation == "unresolved" and "TRY" in node.request_text
    ]
