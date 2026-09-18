"""A dependent arithmetic clause must not die with the model lane that used to plan it.

Reproduces the live acceptance defect (2026-09-08, built app at 1206993d): "What is 12 times 8?
Now take that result and divide it by 6." served

    12 * 8 = 96.
    not determined: the model returned no arithmetic plan for this clause (empty or unreadable reply)

The first clause computed deterministically. The second clause is ONE division of a value the
plan already held (96) by a number in the message (6) -- the only thing the dead model lane was
asked for was the EXPRESSION ("step_1 / 6"), and with no expression the quantitative node
reported "not determined". Two defects made that possible:

1. the ``calculation`` operation never EXPORTED its computed ``value`` to dependents
   (``exported_value_fields`` was empty), so even a dependent with a declared edge received
   nothing; and
2. ``_quantitative_run`` had no model-free binding for an anaphoric clause ("divide it/that
   result by 6"), unlike the purchasable-amount and fx-chain shapes that already compute
   model-free for exactly this reason ("a dead/failed model lane must not turn a derivable
   figure into an 'internal fault'" -- its own C2 comment).

This file pins both halves end-to-end through the REAL planner, scheduler and composer, with a
generation seam that returns nothing -- the live dead-lane condition.
"""

from __future__ import annotations

import json

from tests.conductor_product import compose_product
from core.conductor.planner import plan_conductor_turn
from core.conductor.registry import NodeContext
from core.conductor.scheduler import run_conductor_plan

MESSAGE = "What is 12 times 8? Now take that result and divide it by 6."

#: The clause split a working planner produces for this message: the arithmetic clause, and the
#: dependent clause over its result. The dependent names its edge (0-based earlier index,
#: as `parse_clauses` defines) -- the live plan had the same edge, which is why the missing
#: EXPORT is a defect at the operation, not at the planner.
PLAN_REPLY = json.dumps(
    [
        {"request": "What is 12 times 8?", "operation": "calculation", "depends_on": []},
        {
            "request": "Now take that result and divide it by 6",
            "operation": "quantitative_reasoning",
            "depends_on": [0],
        },
    ]
)

#: The live dead lane: called, returns nothing usable.
def _dead_generation(_system: str, _prompt: str) -> str:
    return ""


def _run(message: str, reply: str):
    plan = plan_conductor_turn(message, ask_model=lambda _s, _p: reply, plan_id="t4")
    assert plan is not None, "the conductor declined a turn it must claim"
    outcomes = run_conductor_plan(
        plan, context=NodeContext(timeout_s=5.0, run_generation=_dead_generation)
    )
    return plan, outcomes, compose_product(plan, outcomes)


def test_the_dependent_clause_computes_from_the_exported_result() -> None:
    _plan, _outcomes, composed = _run(MESSAGE, PLAN_REPLY)
    text = str(composed.body if hasattr(composed, "body") else composed)
    assert "96" in text, f"first clause's product missing from: {text!r}"
    # THE regression: the second clause's value, derived from the first, with no model lane.
    assert "16" in text, f"dependent clause not computed from the exported result: {text!r}"
    assert "not determined" not in text, f"a derivable figure reported as undetermined: {text!r}"


def test_calculation_exports_its_value_to_declared_dependents() -> None:
    """The export contract on its own, so the operation cannot silently stop exporting.

    A plan that re-runs the first clause and re-derives 96 in the dependent would pass the
    end-to-end test while the export stayed broken; this pins the DAG contract directly.
    """
    from core.conductor.registry import operation_spec

    spec = operation_spec("calculation")
    assert spec is not None
    assert "value" in spec.exported_value_fields
