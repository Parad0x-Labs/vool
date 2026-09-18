"""Generic multi-intent orchestration: one message, several requests, one accountable answer.

The defect this exists for, measured on the shipped build:

    "What is 137 x 29? Explain the calculation briefly.
     Also get the current weather for Kaunas and Tallinn and tell me which city is warmer."

answered the weather and the comparison, and the arithmetic *disappeared* -- not refused, not
reported as unserved, simply absent from a confident reply. The cause is structural rather than a
missing feature: `core.execution_requirements.requirements_for()` classifies that whole message
LIVE_DATA because it names two cities, `build_live_data_plan` then enumerates only the entities its
two recognizers know about, and the live-data lane returns before any other lane runs. A message
that spans two domains gets answered by whichever single domain claimed it first.

The conductor is the missing layer above those lanes: it decomposes a turn into typed nodes, runs
the independent ones concurrently, keeps a runtime-observed receipt per node, resolves derived
nodes against their dependencies' *results* rather than their prose, and composes an answer that is
required to account for every node it planned.

Three properties are load-bearing and each has a mutation test that turns red without it:

* **Completeness.** Every planned node is represented in the final answer -- succeeded, failed, or
  unresolved. `compose_answer` refuses to emit prose that silently omits one.
* **Overlap is proved, never asserted.** Concurrency is claimed only from monotonic start/finish
  intervals recorded by the runtime inside each worker. Model prose saying "I ran these in
  parallel" is not evidence and is never consulted.
* **Fail closed.** A clause the planner names but the registry cannot serve becomes an
  ``UNRESOLVED`` node that appears in the answer as unresolved. It is never dropped.
* **Shared facts, scoped adapters.** A message can state its numbers in one sentence and ask about
  them in six others; handed only its own clause, every one of those six finds nothing to act on.
  `core.conductor.shared_context` extracts the message's numbers, ambiguous units and unsupplied
  references ONCE, and operations that declare `wants_shared_context` receive it. The declaration is
  opt-in precisely so entity-recognizing adapters stay clause-scoped -- see that module for why
  sharing typed facts is safe where sharing the raw text is not.

Scope of this slice: intra-turn orchestration only. Cross-chat scheduling, the Activity same-tool
call-id pairing, queue pumping and stream durability are deliberately out.
"""
from __future__ import annotations

from core.conductor.compose import ComposedAnswer, compose_answer
from core.conductor.graph import ConductorGraph, GraphRejectionError, build_graph
from core.conductor.node import (
    ConductorNode,
    NodeLifecycle,
    NodeOutcome,
    node_receipt,
)
from core.conductor.planner import ConductorPlan, plan_conductor_turn
from core.conductor.receipts import ConcurrencyReport, concurrency_report
from core.conductor.registry import (
    OperationSpec,
    known_operations,
    operation_spec,
    register_operation,
)
from core.conductor.scheduler import run_conductor_plan
from core.conductor.shared_context import (
    MissingInformation,
    NumericFact,
    SharedTurnContext,
    UnitAmbiguity,
    extract_shared_context,
)

__all__ = [
    "ComposedAnswer",
    "ConcurrencyReport",
    "ConductorGraph",
    "ConductorNode",
    "ConductorPlan",
    "GraphRejectionError",
    "MissingInformation",
    "NodeLifecycle",
    "NodeOutcome",
    "NumericFact",
    "OperationSpec",
    "SharedTurnContext",
    "UnitAmbiguity",
    "build_graph",
    "compose_answer",
    "concurrency_report",
    "extract_shared_context",
    "known_operations",
    "node_receipt",
    "operation_spec",
    "plan_conductor_turn",
    "register_operation",
    "run_conductor_plan",
]
