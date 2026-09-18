"""Run a validated plan: ready-set dispatch, isolated failures, runtime-stamped receipts.

**Ready set, not waves.** A node is submitted the instant its dependencies reach a terminal state,
using `FIRST_COMPLETED` rather than draining a level before starting the next. Wave scheduling
makes every node in a level wait for its slowest peer; on a plan where a repository search takes
four seconds and a weather fetch takes one, a wave barrier idles the fast lane for no reason.

**Threads, not asyncio.** This is settled by the runtime rather than by preference. The ASGI edge
hands every request to `anyio.to_thread.run_sync` and everything below it is synchronous; the
adapters block in `urllib.request.urlopen`, `subprocess.run` and `sqlite3` with no yield points, so
coroutines over them would serialize completely and buy nothing but overhead.

**Every future is collected individually.** `live_data_runner` calls `future.result()` unguarded
inside its collection loop, so an exception escaping a worker -- including the module-level imports
that sit outside its inner `try` -- aborts collection, falls into a sequential re-run, raises again,
and the caller's blanket handler discards the whole plan *including the subtasks that already
succeeded*. Here a fault is attributed to the one node that raised it and its siblings are kept.

**Nothing silently disappears.** Every node in the plan gets an outcome: dispatched and run,
short-circuited to DEPENDENCY_FAILED, seeded UNRESOLVED at plan time, or failed on the plan
deadline. The loop's exit condition is that the outcome map covers the graph.
"""
from __future__ import annotations

import contextlib
import traceback
import uuid
from collections.abc import Mapping
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import replace
from typing import Any

from core import runtime_active_clock
from core.conductor.graph import unresolved_dependency_state
from core.conductor.node import (
    ConductorNode,
    NodeFailureCode,
    NodeLifecycle,
    NodeOutcome,
    node_receipt,
    now_pair,
)
from core.conductor.planner import ConductorPlan
from core.conductor.registry import (
    UNRESOLVED_OPERATION,
    NodeContext,
    exported_values,
    operation_spec,
)


def _new_receipt_id() -> str:
    """Opaque id correlating a served failure row back to its full diagnostic record.

    R3 (AUD-20260829-003). Minted once per failing node, never derived from the failure's own
    text -- an id built from exception content could itself carry the thing this exists to keep
    out of the answer.
    """
    return f"ndf-{uuid.uuid4().hex[:16]}"

#: Independent read-only nodes are cheap and network-bound; this bounds fan-out without pretending
#: to be a resource policy. Nodes that drive a model generation are separately capped below,
#: because three concurrent local generations is what exhausted Ollama's read timeout on
#: 2026-08-05 and is the reason `turn_planner.run_plan` ships pinned to one worker.
DEFAULT_MAX_WORKERS = 6
DEFAULT_MAX_GENERATION_WORKERS = 1

#: Wall-clock ceiling for the whole plan. Bounds the turn, not the spend: a node still pending when
#: this expires is reported as failed rather than left to hang the reply. Sized for the slowest
#: realistic node (a repository search over a large tree) times a small factor, AND so that the
#: node phase left after the planner cap below still covers one cold local runner: measured
#: 2026-09-10 (s51 rig, direct timing), the certified author qwen3:8b costs 20.3 s on a cold
#: Ollama runner (14.0 s load + 5.0 s prompt eval + 1.1 s generation) and 1.4 s warm. 45 s left
#: 27 s for the nodes after an 18 s cap, which a cold reload alone consumed. Served on dad61973
#: (three fresh sessions, warm runner, fresh prompt cache) the planner phase itself measured
#: 9.9-11.4 s clause split + 12.4-12.6 s semantic proof = 22.5-24.0 s, so a 27 s cap held by 3 s
#: and a cold runner (+14 s) would have declined every one of them. This is a wall-clock bound,
#: not a spend decision (CLAUDE.md 4b): the client read timeouts are far above it.
DEFAULT_PLAN_DEADLINE_S = 75.0

#: The PLANNER phase's share of that ceiling. Planning (clause split + semantic proof) is
#: unpaid supporting work that runs FIRST, and without a bound of its own it starves the
#: answer: measured served on 79a9b357, turn 9419658a — two planner calls on a COLD
#: qwen2.5:7b (the first pays the multi-GB load) consumed the front of the 45 s turn, the
#: knowledge node's generation inherited ~12 s and died as PROVIDER_TIMEOUT. A planner that
#: cannot finish inside this cap makes `plan_conductor_turn` decline (its existing exception
#: path), and the ordinary lanes serve the turn with their own budget — a declined plan
#: costs one classification, a starved plan costs the answer. Bound through the same
#: bind-only-shortens mechanism (`bind_provider_deadline`), so it can never extend the turn.
#: Sized from measurement, not guessed: the clause split on a COLD runner is 20.3 s (qwen3:8b,
#: 2026-09-10) and, served with the runtime's real prompt, 9.9-11.4 s warm; the semantic proof
#: 12.4-12.6 s. 18 s declined the conductor every time the author's runner was cold (three of
#: three fresh sessions on 6249a0d9) and the plain lane refused the whole turn; 27 s held the
#: warm case by 3 s. 35 s covers cold split + warm proof (~33 s) and is 47 % of the turn, inside
#: the 30-50 % share pinned by test_conductor_planner_phase_budget, leaving 40 s for the nodes.
PLANNER_PHASE_CAP_S = 35.0

#: How much of a node's rendered text rides on its completion event. The event store is durable
#: and cursor-paged, so an unbounded node result would be written to disk and re-read on every
#: poll. Generous enough that an ordinary node's whole answer survives for "copy all agent work";
#: the receipt records when it did not.
_EMITTED_RENDERED_CHARS = 4000


def _emit(ctx: NodeContext, event_type: str, detail: dict[str, Any]) -> None:
    """Report node lifecycle without ever letting the report affect the work.

    Every call is swallowed on failure. An emitter that raises, or that is simply absent, must
    leave the node's outcome byte-for-byte identical -- a node marked FAILED because its observer
    threw would be a defect invented by the telemetry, and it would look exactly like a real one.
    """

    emit = getattr(ctx, "emit_node_event", None)
    if emit is None:
        return
    with contextlib.suppress(Exception):
        emit(event_type, detail)


def _transport_family(exc: BaseException) -> bool:
    """Whether an operation's exception is a TRANSPORT fact (network unreachable),
    not a runtime fault. Conservative by design: only the classes the fetch paths
    raise for connectivity -- a broad OSError would swallow file errors, and
    `RemoteFetchRefusedError` is a POLICY fact that never opened a socket and keeps
    its own refusal path. The inner `reason` of a URLError counts: urllib wraps the
    socket error that actually happened.
    """
    import urllib.error

    chain: list[BaseException] = [exc]
    inner = getattr(exc, "reason", None)
    if isinstance(inner, BaseException):
        chain.append(inner)
    return any(
        isinstance(item, (urllib.error.URLError, TimeoutError, ConnectionError))
        for item in chain
    )


def _cancellation_fired(ctx: NodeContext) -> bool:
    """Whether the OWNING turn's cancellation marker fired before this node runs.

    The marker rides the turn's source_context (`cancel_event` / `cancellation_token`,
    the `threading.Event` `core.live_turns.register_turn` hands the spine). Read here --
    on the worker, at node entry -- so a turn cancelled while its plan was mid-flight
    opens no LATE socket and starts no LATE work: the node reports a cancellation
    outcome instead of doing the work of a turn that no longer wants an answer.
    Absent or non-callable markers read as not cancelled; a marker that raises reads
    as not cancelled (fail toward doing the work, never toward silently dropping it).
    """
    context = getattr(ctx, "source_context", None)
    if not isinstance(context, dict):
        return False
    marker = context.get("cancel_event") or context.get("cancellation_token")
    if marker is None:
        return False
    probe = getattr(marker, "is_set", None)
    if not callable(probe):
        return False
    try:
        return bool(probe())
    except Exception:
        return False


def _run_one(
    node: ConductorNode,
    ctx: NodeContext,
    *,
    queued_at: float,
    queued_at_iso: str,
    plan_id: str = "",
) -> NodeOutcome:
    """Execute one node. Timing is stamped inside the worker, around the work and nothing else."""
    started_at, started_at_iso = now_pair()
    outcome = NodeOutcome(
        node=node,
        state=NodeLifecycle.RUNNING,
        queued_at=queued_at,
        queued_at_iso=queued_at_iso,
        started_at=started_at,
        started_at_iso=started_at_iso,
    )
    # Emitted from inside the worker, after the started stamp, so the reported start time is the
    # one the outcome carries rather than the moment the scheduler queued the node. Those differ
    # by however long the node waited for a pool slot, which is exactly the wait a panel showing
    # "running for 12s" must not attribute to the work.
    _emit(
        ctx,
        "agent_node_started",
        {
            "schema": "agent_node_started_v1",
            "lane": "conductor",
            "plan_id": plan_id,
            "node_id": node.node_id,
            "operation": node.operation,
            "tool_intent": node.tool_intent,
            "depends_on": list(node.depends_on),
            "needs_generation": bool(node.needs_generation),
            "started_at_iso": started_at_iso,
        },
    )
    try:
        if _cancellation_fired(ctx):
            # The turn was cancelled while this node sat queued. Not an error in the
            # work: a typed CANCELLED outcome, emitted like any other completion so the
            # plan's receipt accounts for the node, and NO work -- the fetch, the
            # generation, the tool -- of a turn that no longer wants an answer starts.
            outcome.state = NodeLifecycle.FAILED
            outcome.failure_code = NodeFailureCode.CANCELLED
            outcome.receipt_id = _new_receipt_id()
            outcome.failure_reason = "the owning turn was cancelled before this node started"
            return outcome
        spec = operation_spec(node.operation)
        if spec is None:
            raise ValueError(f"no registered operation named {node.operation!r}")
        result = spec.run(node, ctx)
        if not isinstance(result, dict):
            raise TypeError(f"{node.operation} returned {type(result).__name__}, expected a mapping")
        outcome.result = result
        missing = [name for name in node.required_result_fields if name not in result]
        if missing:
            # The check `live_data_plan` declares and never performs. A node whose adapter returned
            # a shape its own contract does not satisfy has not done its job, and letting it count
            # as success is how a missing field becomes a confident sentence downstream.
            outcome.state = NodeLifecycle.FAILED
            outcome.failure_code = NodeFailureCode.RESULT_MISSING_FIELDS
            outcome.receipt_id = _new_receipt_id()
            outcome.failure_reason = f"result missing required fields: {', '.join(missing)}"
        else:
            if spec.assess_fulfillment is not None:
                from core.runtime_task_outcome import FulfillmentStatus

                verdict = spec.assess_fulfillment(result)
                if not isinstance(verdict, FulfillmentStatus):
                    raise TypeError("operation fulfillment assessment must return FulfillmentStatus")
                outcome.result_fulfillment = verdict
            outcome.state = NodeLifecycle.SUCCEEDED
            if outcome.result_fulfillment is not None and outcome.result_fulfillment not in {
                FulfillmentStatus.FULFILLED, FulfillmentStatus.PARTIALLY_FULFILLED,
            }:
                outcome.state = NodeLifecycle.FAILED
                outcome.failure_code = NodeFailureCode.RESULT_UNFULFILLED
                outcome.receipt_id = _new_receipt_id()
                outcome.failure_reason = "the operation established no requested result"
            try:
                outcome.rendered = str(spec.render(node, result) or "").strip()
            except Exception as exc:
                # R3 (AUD-20260829-003): the full text and traceback go to the receipt (via
                # node_receipt below, keyed by receipt_id) -- never to failure_reason's 200-char
                # cut, and never to the renderer, which reads failure_code only.
                outcome.state = NodeLifecycle.FAILED
                outcome.failure_code = NodeFailureCode.RENDER_FAILED
                outcome.receipt_id = _new_receipt_id()
                outcome.failure_detail_full = f"{type(exc).__name__}: {exc}"
                outcome.failure_traceback = traceback.format_exc()
                outcome.failure_reason = f"render failed: {type(exc).__name__}: {exc}"[:200]
    except Exception as exc:
        outcome.state = NodeLifecycle.FAILED
        outcome.receipt_id = _new_receipt_id()
        if _transport_family(exc):
            # A network fact, not a runtime fault: the node's live source was
            # unreachable. The reader-facing reason is a TYPE-derived token
            # (`core.effect_gateway.safe_transport_failure_reason`) -- a URLError's
            # message can embed the full request URL, so exception text never
            # travels here. The full detail + traceback stay on the receipt.
            from core.effect_gateway import safe_transport_failure_reason

            outcome.failure_code = NodeFailureCode.TRANSPORT_FAILED
            outcome.failure_reason = safe_transport_failure_reason(exc)[:200]
            outcome.failure_detail_full = f"{type(exc).__name__}: {exc}"
            outcome.failure_traceback = traceback.format_exc()
        else:
            outcome.failure_code = NodeFailureCode.NODE_EXCEPTION
            outcome.failure_detail_full = f"{type(exc).__name__}: {exc}"
            outcome.failure_traceback = traceback.format_exc()
            outcome.failure_reason = f"{type(exc).__name__}: {exc}"[:200]
    finally:
        completed_at, completed_at_iso = now_pair()
        outcome.completed_at = completed_at
        outcome.completed_at_iso = completed_at_iso
        # `node_receipt` rather than a second hand-built shape: the runtime already has one record
        # of what a node did, and inventing a parallel one is how two accounts of the same
        # execution drift apart. `rendered` is carried alongside it because a panel offering
        # "copy all agent work" needs the work, and the receipt deliberately holds only field
        # names. Bounded here rather than at the reader -- an unbounded node result would
        # otherwise reach the event store, which is durable and paged.
        detail = dict(node_receipt(outcome, plan_id=plan_id))
        detail["schema"] = "agent_node_completed_v1"
        detail["lane"] = "conductor"
        rendered = str(outcome.rendered or "")
        detail["rendered"] = rendered[:_EMITTED_RENDERED_CHARS]
        detail["rendered_truncated"] = len(rendered) > _EMITTED_RENDERED_CHARS
        # The observation identity (bounded values + named source), for consumers that
        # bind this completion as evidence. Generic over results: a dict is rendered, a
        # non-dict contributes nothing, and the payload never affects the node's outcome.
        with contextlib.suppress(Exception):
            from core.conductor.evidence import observation_event_payload

            observed = observation_event_payload(outcome.result)
            if observed:
                detail["observed"] = observed
        _emit(ctx, "agent_node_completed", detail)
    return outcome


def _submit_node(
    pool: ThreadPoolExecutor,
    node: ConductorNode,
    ctx: NodeContext,
    *,
    queued_at: float,
    queued_at_iso: str,
    plan_id: str,
) -> Future[NodeOutcome]:
    """Submit one node so the WORKER inherits the originating turn's context.

    THE DEFECT THIS CLOSES, measured live on the isolated daemon: the turn's effect
    ledger, frozen fetch policy and fetch accounting are ContextVars opened by
    `remote_fetch_policy_scope` on the TURN thread. A plain `pool.submit` hands the
    worker none of them, so the network door denied every observation node before
    any socket -- `RemoteFetchRefusedError: no active turn or background effect
    ledger` -- and a mixed turn could never execute its live-data demand.

    The propagation is the one `contextvars` sanctions and `turn_planner.run_plan`
    already uses: ONE fresh `copy_context()` PER TASK, taken here on the submitting
    (turn) thread, run on the worker via `Context.run`. Each task owns its copy --
    a reused pool thread never retains a previous task's context (the copy lives
    with the task, not the thread), a worker's own context mutations stay in its
    copy, and ContextVar VALUES the copy holds by reference (the effect ledger, the
    policy) are the turn's own objects, so worker-recorded receipts land on the
    turn's ledger under the turn's frozen identity.

    Nested workers keep the root identity for the same reason: a copy of a copy
    still binds the same ledger object, and the spine's turn scope outlives both.
    """
    import contextvars

    task_context = contextvars.copy_context()
    return pool.submit(
        task_context.run,
        _run_one,
        node,
        ctx,
        queued_at=queued_at,
        queued_at_iso=queued_at_iso,
        plan_id=plan_id,
    )


def _base_context(plan: ConductorPlan, context: NodeContext | None) -> NodeContext:
    """The caller's context with the plan's own shared context filled in if it carried none.

    The plan owns the extraction -- it happened there, once, before any node was built -- so a
    caller that only wanted to inject a session id and a tool seam does not have to know that
    shared context exists in order for nodes to receive it.
    """
    base = context or NodeContext()
    if base.shared_context is None and plan.shared_context is not None:
        return replace(base, shared_context=plan.shared_context)
    return base


def _derived_facts(
    node: ConductorNode, outcomes: dict[str, NodeOutcome]
) -> dict[str, Any]:
    """Values earlier nodes produced, named for the node that reasons over them.

    Sourced from DECLARED dependency edges and nowhere else, so this is a function of the DAG
    rather than of scheduling order. Reading every finished sibling instead would make what a node
    sees depend on which fetch happened to return first, and a plan that answers differently on a
    slow network is a plan no receipt can account for.

    Two sources, and the second is the repair. A ``values`` mapping is what a computed-value
    operation returns, and harvesting it was ALL this function did -- so a SUCCEEDED live
    observation, whose result is ``{"price": ..., "currency": ...}`` and carries no ``values`` key,
    handed its dependent nothing at all. Measured at 866cf12a: a quote of 64000.0 rendered one line
    above a dependent node reporting that the very same price "is not a fact this message
    established". The producer now DECLARES what it exports (`OperationSpec.exported_value_fields`)
    and the projection reads the declaration, so the contract belongs to the operation instead of
    to a shape only some operations happen to emit.

    Only dependencies that actually SUCCEEDED contribute. `NodeOutcome.result` is populated before
    the required-field check runs, so a FAILED node can carry a partial result; feeding that to a
    dependent as though it were an input is how a missing field becomes a confident number.
    """
    facts: dict[str, Any] = {}
    for dep in node.depends_on:
        outcome = outcomes.get(dep)
        if outcome is None or not outcome.succeeded or not outcome.result:
            continue
        result = outcome.result
        values = result.get("values")
        if isinstance(values, Mapping):
            for name, value in values.items():
                facts[str(name)] = value
        spec = operation_spec(outcome.node.operation)
        if spec is None:
            continue
        entity = str(outcome.node.arguments.get("entity") or outcome.node.node_id)
        facts.update(exported_values(spec, entity, result))
    return facts


def _context_for(node: ConductorNode, base: NodeContext, outcomes: dict[str, NodeOutcome]) -> NodeContext:
    """A per-node context carrying its dependencies' structured results -- never their prose.

    `replace` rather than a field-by-field rebuild. The rebuild enumerated eight fields and was
    correct only for as long as nobody added a ninth: a new field on `NodeContext` was silently
    dropped here and never reached the worker, with no error and no test failure anywhere -- the
    node simply ran without it. That is how `emit_node_event` was lost on its first run.

    Only the two genuinely per-node fields are overridden; everything the caller injected rides
    along by construction, including whatever is added next.
    """
    dependency_results = {
        dep: dict(outcomes[dep].result or {})
        for dep in node.depends_on
        if dep in outcomes and outcomes[dep].succeeded and outcomes[dep].result
    }
    return replace(
        base,
        dependency_results=dependency_results,
        derived_facts=_derived_facts(node, outcomes),
    )


def run_conductor_plan(
    plan: ConductorPlan,
    *,
    context: NodeContext | None = None,
    max_workers: int = DEFAULT_MAX_WORKERS,
    plan_deadline_s: float = DEFAULT_PLAN_DEADLINE_S,
) -> list[NodeOutcome]:
    """Run every node, concurrently where the graph allows. Returns outcomes in plan order."""
    base = _base_context(plan, context)
    graph = plan.graph
    outcomes: dict[str, NodeOutcome] = {}
    dispatched: set[str] = set()

    # Unresolved nodes are terminal at plan time: there is nothing to run, and their whole purpose
    # is to be carried into the answer as unserved rather than dropped.
    for node in graph.nodes:
        if node.operation == UNRESOLVED_OPERATION:
            outcomes[node.node_id] = NodeOutcome(
                node=node,
                state=NodeLifecycle.UNRESOLVED,
                failure_code=NodeFailureCode.UNRESOLVED,
                receipt_id=_new_receipt_id(),
                failure_reason=node.unresolved_reason or "no operation could serve this request",
            )
            dispatched.add(node.node_id)

    generation_workers = max(1, min(max_workers, DEFAULT_MAX_GENERATION_WORKERS))
    pool = ThreadPoolExecutor(max_workers=max(1, max_workers))
    pending: dict[Future[NodeOutcome], ConductorNode] = {}
    started_monotonic = runtime_active_clock.monotonic()

    try:
        while len(outcomes) < len(graph.nodes):
            in_flight_generations = sum(1 for node in pending.values() if node.needs_generation)
            for node in graph.ready(outcomes, dispatched):
                dependency_state = unresolved_dependency_state(node, outcomes)
                if dependency_state is not None:
                    failed = [
                        dep
                        for dep in node.depends_on
                        if dep not in outcomes or not outcomes[dep].succeeded
                    ]
                    outcomes[node.node_id] = NodeOutcome(
                        node=node,
                        state=dependency_state,
                        failure_code=NodeFailureCode.DEPENDENCY_FAILED,
                        receipt_id=_new_receipt_id(),
                        failure_reason=f"depends on {', '.join(failed)}, which did not succeed",
                    )
                    dispatched.add(node.node_id)
                    continue
                if node.needs_generation and in_flight_generations >= generation_workers:
                    continue  # retried on the next pass, once a generation slot frees
                if node.needs_generation:
                    in_flight_generations += 1
                queued_at, queued_at_iso = now_pair()
                future = _submit_node(
                    pool,
                    node,
                    _context_for(node, base, outcomes),
                    queued_at=queued_at,
                    queued_at_iso=queued_at_iso,
                    plan_id=plan.plan_id,
                )
                pending[future] = node
                dispatched.add(node.node_id)

            if not pending:
                break  # a validated DAG cannot reach here with nodes outstanding

            remaining = plan_deadline_s - (runtime_active_clock.monotonic() - started_monotonic)
            if remaining <= 0:
                break
            done, _still_running = wait(set(pending), timeout=min(remaining, 1.0) if runtime_active_clock.enabled() else remaining, return_when=FIRST_COMPLETED)
            if not done:
                if runtime_active_clock.enabled():
                    continue  # price waiting excludes only paused execution time
                break  # deadline expired mid-wait
            for future in done:
                node = pending.pop(future)
                try:
                    outcomes[node.node_id] = future.result()
                except Exception as exc:
                    # A pool-level fault belongs to this node alone. Its siblings keep their results.
                    # Full text + traceback go to the receipt (node_receipt, via plan_receipt);
                    # never to failure_reason's 200-char cut and never to the renderer.
                    outcomes[node.node_id] = NodeOutcome(
                        node=node,
                        state=NodeLifecycle.FAILED,
                        failure_code=NodeFailureCode.SCHEDULER_FAULT,
                        receipt_id=_new_receipt_id(),
                        failure_detail_full=f"{type(exc).__name__}: {exc}",
                        failure_traceback=traceback.format_exc(),
                        failure_reason=f"scheduler fault: {type(exc).__name__}: {exc}"[:200],
                    )
    finally:
        # Cancel work that never started before the deadline.  ``wait=False`` alone leaves queued
        # futures owned by the executor, so they begin *after* the turn already returned and each
        # expired turn can accumulate more orphan work.  Running provider calls have the earlier
        # transport deadline carried by their ModelRequest and are deliberately not joined here:
        # this function may itself be running inside another executor, where waiting on self-owned
        # shutdown is a deadlock shape rather than cleanup.
        for future in pending:
            future.cancel()
        pool.shutdown(wait=False, cancel_futures=True)

    for node in graph.nodes:
        if node.node_id not in outcomes:
            outcomes[node.node_id] = NodeOutcome(
                node=node,
                state=NodeLifecycle.FAILED,
                failure_code=NodeFailureCode.PLAN_DEADLINE_EXPIRED,
                receipt_id=_new_receipt_id(),
                failure_reason="plan deadline expired before this request could be answered",
            )

    return [outcomes[node.node_id] for node in graph.nodes]


def run_conductor_plan_sequential(
    plan: ConductorPlan,
    *,
    context: NodeContext | None = None,
    plan_deadline_s: float = DEFAULT_PLAN_DEADLINE_S,
    **_ignored: Any,
) -> list[NodeOutcome]:
    """Dependency-correct but strictly serial. Exists ONLY as the sabotage target.

    `tests/test_conductor_concurrency.py` runs an identical plan through this and asserts the
    overlap proof collapses -- `max_concurrent == 1`, no overlapping pairs, `proves_concurrency`
    False. Without a control that is known to fail, an assertion that concurrency was proved is
    indistinguishable from an assertion that the code ran at all.

    Nothing in production calls this, and nothing should.
    """
    base = _base_context(plan, context)
    graph = plan.graph
    outcomes: dict[str, NodeOutcome] = {}
    dispatched: set[str] = set()
    started_monotonic = runtime_active_clock.monotonic()

    for node in graph.nodes:
        if node.operation == UNRESOLVED_OPERATION:
            outcomes[node.node_id] = NodeOutcome(
                node=node,
                state=NodeLifecycle.UNRESOLVED,
                failure_code=NodeFailureCode.UNRESOLVED,
                receipt_id=_new_receipt_id(),
                failure_reason=node.unresolved_reason or "no operation could serve this request",
            )
            dispatched.add(node.node_id)

    while len(outcomes) < len(graph.nodes):
        ready = graph.ready(outcomes, dispatched)
        if not ready:
            break
        for node in ready:
            dependency_state = unresolved_dependency_state(node, outcomes)
            if dependency_state is not None:
                outcomes[node.node_id] = NodeOutcome(
                    node=node,
                    state=dependency_state,
                    failure_code=NodeFailureCode.DEPENDENCY_FAILED,
                    receipt_id=_new_receipt_id(),
                    failure_reason="dependency did not succeed",
                )
                dispatched.add(node.node_id)
                continue
            if runtime_active_clock.monotonic() - started_monotonic > plan_deadline_s:
                outcomes[node.node_id] = NodeOutcome(
                    node=node,
                    state=NodeLifecycle.FAILED,
                    failure_code=NodeFailureCode.PLAN_DEADLINE_EXPIRED,
                    receipt_id=_new_receipt_id(),
                    failure_reason="plan deadline expired before this request could be answered",
                )
                dispatched.add(node.node_id)
                continue
            queued_at, queued_at_iso = now_pair()
            outcomes[node.node_id] = _run_one(
                node,
                _context_for(node, base, outcomes),
                queued_at=queued_at,
                queued_at_iso=queued_at_iso,
            )
            dispatched.add(node.node_id)

    for node in graph.nodes:
        outcomes.setdefault(
            node.node_id,
            NodeOutcome(
                node=node,
                state=NodeLifecycle.FAILED,
                failure_code=NodeFailureCode.NOT_REACHED,
                receipt_id=_new_receipt_id(),
                failure_reason="not reached",
            ),
        )
    return [outcomes[node.node_id] for node in graph.nodes]


__all__ = [
    "DEFAULT_MAX_WORKERS",
    "DEFAULT_PLAN_DEADLINE_S",
    "run_conductor_plan",
    "run_conductor_plan_sequential",
]
