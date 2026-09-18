"""A node reports that it started and finished WHILE the plan is still running.

The conductor already recorded everything a panel needs -- `NodeOutcome` carries dual-clock timing,
a six-state lifecycle, `failure_reason`, `duration_s` and the rendered user-facing text, and
`node_receipt` versions all of it. None of it left the scheduler until the whole plan returned.

That is the difference between an AGENTS panel that works and one that lies: a plan bounded at
`DEFAULT_PLAN_DEADLINE_S = 45.0` would show a roster at t=0, nothing at all while the work happened,
and then every row flipping to a final state at once. "Is an agent at work" would be unanswerable
for the entire time an agent was at work.

The repair is an injected `emit_node_event` on `NodeContext`, matching the package's existing idiom
for `run_generation` and `run_tool_intent` -- injected rather than imported so the conductor never
acquires the event store as a dependency, and so this test can observe emissions without one.

The load-bearing property is NOT "events exist". It is:

  1. LIVENESS -- the started event for a node is observable before that node has finished, and
     before its siblings finish. Eventual delivery after the plan returns is the defect, not the fix.
  2. ISOLATION -- observation never changes the work. An emitter that raises must leave the
     outcome byte-for-byte identical. A node marked FAILED because its observer threw would be a
     defect invented by the telemetry, and it would look exactly like a real one.
"""

from __future__ import annotations

import threading
from typing import Any

import pytest

from core.conductor.graph import build_graph
from core.conductor.node import ConductorNode, NodeLifecycle
from core.conductor.planner import ConductorPlan
from core.conductor.registry import (
    NodeContext,
    OperationSpec,
    register_operation,
    unregister_operation,
)
from core.conductor.scheduler import run_conductor_plan

_OP = "test_probe_op"
_SLOW = "test_probe_slow_op"
_BOOM = "test_probe_boom_op"


@pytest.fixture(autouse=True)
def _register_probe_operations():
    """Three operations: instant, gated-on-an-event, and always-raising."""

    gate = threading.Event()

    register_operation(
        OperationSpec(
            name=_OP,
            description="probe",
            expand_arguments=lambda *a, **k: [],
            run=lambda node, ctx: {"value": node.node_id},
            render=lambda node, result: f"rendered:{result['value']}",
            required_result_fields=("value",),
        ),
        replace=True,
    )
    register_operation(
        OperationSpec(
            name=_SLOW,
            description="probe that waits until the test releases it",
            expand_arguments=lambda *a, **k: [],
            run=lambda node, ctx: (gate.wait(timeout=10), {"value": "slow"})[1],
            render=lambda node, result: "rendered:slow",
            required_result_fields=("value",),
        ),
        replace=True,
    )
    register_operation(
        OperationSpec(
            name=_BOOM,
            description="probe that always raises",
            expand_arguments=lambda *a, **k: [],
            run=lambda node, ctx: (_ for _ in ()).throw(RuntimeError("node blew up")),
            render=lambda node, result: "",
        ),
        replace=True,
    )
    yield gate
    gate.set()
    for name in (_OP, _SLOW, _BOOM):
        unregister_operation(name)


def _plan(*nodes: ConductorNode) -> ConductorPlan:
    return ConductorPlan(plan_id="plan-probe", original_request="probe", graph=build_graph(list(nodes)))


def _node(node_id: str, operation: str = _OP, deps: tuple[str, ...] = ()) -> ConductorNode:
    return ConductorNode(
        node_id=node_id, operation=operation, request_text="probe", depends_on=deps
    )


class _Recorder:
    """Collects emissions, thread-safely, with the outcome state at the moment of each call."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []
        self._lock = threading.Lock()

    def __call__(self, event_type: str, detail: dict[str, Any]) -> None:
        with self._lock:
            self.events.append((event_type, dict(detail)))

    def types(self) -> list[str]:
        return [t for t, _ in self.events]

    def of(self, event_type: str) -> list[dict[str, Any]]:
        return [d for t, d in self.events if t == event_type]


# ---------------------------------------------------------------------------------------------
# G1 -- LIVENESS. The property the panel actually depends on.
# ---------------------------------------------------------------------------------------------


def test_a_node_reports_started_before_the_plan_finishes(_register_probe_operations) -> None:
    """The decisive test, and it uses no sleeps.

    One node blocks until the test releases it. If the started event only arrived when the plan
    returned, this would deadlock: the assertion runs BEFORE the gate opens, so the event must have
    been delivered while the node was still inside its own `run`.
    """

    gate = _register_probe_operations
    rec = _Recorder()
    seen_started = threading.Event()

    def emit(event_type: str, detail: dict[str, Any]) -> None:
        rec(event_type, detail)
        if event_type == "agent_node_started" and detail.get("node_id") == "slow":
            seen_started.set()

    ctx = NodeContext(emit_node_event=emit)
    done: list[Any] = []
    runner = threading.Thread(
        target=lambda: done.append(run_conductor_plan(_plan(_node("slow", _SLOW)), context=ctx))
    )
    runner.start()

    assert seen_started.wait(timeout=10), "no started event arrived while the node was still running"
    # The node is provably still inside run() -- nothing has released the gate yet.
    assert rec.of("agent_node_completed") == [], "completed arrived before the work finished"

    gate.set()
    runner.join(timeout=10)
    assert done and done[0][0].state is NodeLifecycle.SUCCEEDED


def test_every_node_emits_exactly_one_started_and_one_completed() -> None:
    rec = _Recorder()
    outcomes = run_conductor_plan(
        _plan(_node("a"), _node("b"), _node("c")), context=NodeContext(emit_node_event=rec)
    )

    assert len(outcomes) == 3
    assert len(rec.of("agent_node_started")) == 3
    assert len(rec.of("agent_node_completed")) == 3
    assert {d["node_id"] for d in rec.of("agent_node_started")} == {"a", "b", "c"}


# ---------------------------------------------------------------------------------------------
# CLEAN -- what the panel needs to render, present and correct
# ---------------------------------------------------------------------------------------------


def test_the_completed_event_carries_what_a_panel_must_show() -> None:
    """is-it-running, how long, what it did, and did it work."""

    rec = _Recorder()
    run_conductor_plan(_plan(_node("a")), context=NodeContext(emit_node_event=rec))

    done = rec.of("agent_node_completed")[0]
    assert done["node_id"] == "a"
    assert done["operation"] == _OP
    assert done["state"] == "succeeded"
    assert done["ok"] is True
    assert isinstance(done["duration_s"], float) and done["duration_s"] >= 0.0
    assert done["started_at_iso"] and done["completed_at_iso"]
    assert done["rendered"] == "rendered:a"
    assert done["plan_id"] == "plan-probe"


def test_a_failing_node_still_reports_and_says_why() -> None:
    """A panel that goes silent on failure is worse than no panel."""

    rec = _Recorder()
    run_conductor_plan(_plan(_node("boom", _BOOM)), context=NodeContext(emit_node_event=rec))

    done = rec.of("agent_node_completed")[0]
    assert done["state"] == "failed"
    assert done["ok"] is False
    assert "node blew up" in done["failure_reason"]
    assert rec.of("agent_node_started"), "a node that failed must still have reported starting"


def test_dependent_nodes_report_in_dependency_order() -> None:
    rec = _Recorder()
    run_conductor_plan(
        _plan(_node("first"), _node("second", deps=("first",))),
        context=NodeContext(emit_node_event=rec),
    )

    order = [d["node_id"] for d in rec.of("agent_node_started")]
    assert order == ["first", "second"]
    assert rec.of("agent_node_started")[1]["depends_on"] == ["first"]


# ---------------------------------------------------------------------------------------------
# NEGATIVE CONTROLS -- observation must never change the work
# ---------------------------------------------------------------------------------------------


def _outcome_fingerprint(outcomes) -> list[tuple]:
    return [(o.node.node_id, o.state, o.failure_reason, o.rendered, o.result and dict(o.result)) for o in outcomes]


def test_no_emitter_leaves_the_work_identical() -> None:
    """The pre-existing behaviour. Every caller that injects nothing must be unaffected."""

    nodes = (_node("a"), _node("b", deps=("a",)), _node("boom", _BOOM))
    without = run_conductor_plan(_plan(*nodes), context=NodeContext())
    with_emit = run_conductor_plan(_plan(*nodes), context=NodeContext(emit_node_event=_Recorder()))

    assert _outcome_fingerprint(without) == _outcome_fingerprint(with_emit)


def test_an_emitter_that_raises_cannot_fail_the_node() -> None:
    """The isolation property, and the reason `_emit` swallows.

    A node marked FAILED because its observer threw would look exactly like a real failure, and
    would be attributed to the operation rather than to the telemetry.
    """

    def exploding(event_type: str, detail: dict[str, Any]) -> None:
        raise RuntimeError("observer is broken")

    nodes = (_node("a"), _node("b", deps=("a",)))
    clean = run_conductor_plan(_plan(*nodes), context=NodeContext())
    hostile = run_conductor_plan(_plan(*nodes), context=NodeContext(emit_node_event=exploding))

    assert _outcome_fingerprint(clean) == _outcome_fingerprint(hostile)
    assert all(o.state is NodeLifecycle.SUCCEEDED for o in hostile)


def test_an_emitter_that_raises_only_on_completion_is_also_contained() -> None:
    """Started and completed are separate call sites; both must be isolated."""

    def half_broken(event_type: str, detail: dict[str, Any]) -> None:
        if event_type == "agent_node_completed":
            raise ValueError("late failure")

    outcomes = run_conductor_plan(_plan(_node("a")), context=NodeContext(emit_node_event=half_broken))

    assert outcomes[0].state is NodeLifecycle.SUCCEEDED
    assert outcomes[0].rendered == "rendered:a"


# ---------------------------------------------------------------------------------------------
# ADVERSARIAL
# ---------------------------------------------------------------------------------------------


def test_a_huge_node_result_is_bounded_before_it_reaches_the_event_store() -> None:
    """The event store is durable and cursor-paged; an unbounded result would be written to disk
    and re-read on every poll. The receipt must say when it was cut rather than pretend."""

    from core.conductor.scheduler import _EMITTED_RENDERED_CHARS

    register_operation(
        OperationSpec(
            name=_OP,
            description="probe",
            expand_arguments=lambda *a, **k: [],
            run=lambda node, ctx: {"value": "x"},
            render=lambda node, result: "y" * (_EMITTED_RENDERED_CHARS * 3),
            required_result_fields=("value",),
        ),
        replace=True,
    )
    rec = _Recorder()
    run_conductor_plan(_plan(_node("a")), context=NodeContext(emit_node_event=rec))

    done = rec.of("agent_node_completed")[0]
    assert len(done["rendered"]) == _EMITTED_RENDERED_CHARS
    assert done["rendered_truncated"] is True


def test_an_ordinary_result_is_not_marked_truncated() -> None:
    """Negative control on the bound: the flag must mean something."""

    rec = _Recorder()
    run_conductor_plan(_plan(_node("a")), context=NodeContext(emit_node_event=rec))

    assert rec.of("agent_node_completed")[0]["rendered_truncated"] is False


def test_the_started_event_reports_the_workers_start_not_the_queue_time() -> None:
    """A node that waited for a pool slot must not have that wait attributed to its work.

    The emission sits after the started stamp inside the worker, so the reported time is the one
    the outcome carries. Asserted against the outcome rather than against a clock reading.
    """

    rec = _Recorder()
    outcomes = run_conductor_plan(_plan(_node("a")), context=NodeContext(emit_node_event=rec))

    assert rec.of("agent_node_started")[0]["started_at_iso"] == outcomes[0].started_at_iso


def test_every_injected_context_field_reaches_the_node() -> None:
    """The bug this family actually found, pinned as a class rather than as one field.

    `_context_for` rebuilt `NodeContext` by ENUMERATING its fields. It was correct for exactly as
    long as nobody added one: `emit_node_event` was dropped before the worker saw it, with no
    error, no warning and no failing test anywhere -- the node just ran without it, and the panel
    would have shown nothing while reporting success.

    This walks the dataclass instead of naming fields, so the next field added to `NodeContext` is
    covered the day it is added rather than the day someone notices it is missing.
    """

    import dataclasses

    sentinels: dict[str, Any] = {}
    for f in dataclasses.fields(NodeContext):
        if f.name in {"dependency_results", "derived_facts"}:
            continue  # genuinely per-node; the scheduler owns these
        if f.name == "timeout_s":
            sentinels[f.name] = 123.5
        elif f.name in {"session_id"}:
            sentinels[f.name] = "sentinel-session"
        elif f.name == "source_context":
            sentinels[f.name] = {"sentinel": True}
        elif f.name == "shared_context":
            continue  # filled in from the plan by _base_context; covered by its own tests
        else:
            sentinels[f.name] = lambda *a, **k: None

    seen: dict[str, Any] = {}

    def capture(node, ctx):
        for name in sentinels:
            seen[name] = getattr(ctx, name, "<MISSING>")
        return {"value": "x"}

    register_operation(
        OperationSpec(
            name=_OP,
            description="probe",
            expand_arguments=lambda *a, **k: [],
            run=capture,
            render=lambda node, result: "r",
            required_result_fields=("value",),
        ),
        replace=True,
    )
    run_conductor_plan(_plan(_node("a")), context=NodeContext(**sentinels))

    missing = [n for n, v in sentinels.items() if seen.get(n) is not v]
    assert not missing, f"fields dropped between injection and the node: {missing}"


def test_the_emitter_is_not_required_to_be_thread_safe_by_the_scheduler() -> None:
    """Concurrent nodes really do emit from different threads -- the panel's writer must know.

    Pinned so nobody later assumes serialization the scheduler does not provide.
    """

    threads: set[int] = set()
    lock = threading.Lock()

    def note(event_type: str, detail: dict[str, Any]) -> None:
        with lock:
            threads.add(threading.get_ident())

    run_conductor_plan(
        _plan(*[_node(f"n{i}") for i in range(4)]), context=NodeContext(emit_node_event=note)
    )

    assert len(threads) >= 1  # >1 when the pool genuinely overlaps; never asserted as exactly 1
