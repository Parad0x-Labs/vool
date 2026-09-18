"""The dependency graph: real edges, validated once, then iterated as a ready set.

Two design choices worth stating because the tree contains the alternative to each.

**Edges live on the graph, not in an untyped bag.** `core.orchestration.task_graph.TaskGraphNode`
models `parent_task_id`/`children` -- parentage, not dependency -- while the actual `depends_on`
edges sit inside `TaskEnvelopeV1.inputs["depends_on"]` and are read only by the scheduler. The
graph object never sees the relation it is named for, and a child registered before its parent is
silently never linked. Here the edge set *is* the graph, and validation happens once, up front,
for the whole plan.

**Ready set, not waves.** Kahn layering (`turn_planner.execution_waves`) barriers each level: a
node whose dependencies have all resolved still waits for its slowest wave-mate. `run_conductor_plan`
dispatches a node the instant its dependencies are terminal. `core.orchestration.executor` already
computes exactly this ready set at each pass and then throws it away in favour of a flat list.

Rejection is whole-plan and up front. A duplicate id, an unknown dependency or a cycle rejects the
entire plan rather than silently repairing it -- verified on main, two live-data subtasks sharing an
id collapse into one outcome object returned twice under different names, with no error anywhere.
A plan whose shape cannot be trusted must not be half-run.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from core.conductor.node import TERMINAL_STATES, ConductorNode, NodeLifecycle, NodeOutcome


class GraphRejectionError(ValueError):
    """The proposed plan is not a usable DAG. Carries the reason for the ledger and the tests."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class ConductorGraph:
    """A validated DAG. Construction is the only way to get one, so an invalid graph cannot exist."""

    nodes: tuple[ConductorNode, ...]

    @property
    def by_id(self) -> dict[str, ConductorNode]:
        return {node.node_id: node for node in self.nodes}

    @property
    def roots(self) -> tuple[ConductorNode, ...]:
        """Nodes with no dependencies -- the ones that may start immediately and overlap."""
        return tuple(node for node in self.nodes if not node.depends_on)

    def dependents_of(self, node_id: str) -> tuple[ConductorNode, ...]:
        return tuple(node for node in self.nodes if node_id in node.depends_on)

    def ready(self, outcomes: dict[str, NodeOutcome], dispatched: Iterable[str]) -> tuple[ConductorNode, ...]:
        """Nodes whose dependencies have all reached a terminal state and that have not been sent yet.

        Deliberately says nothing about whether those dependencies *succeeded* -- the scheduler
        decides that, because a dependency that failed produces DEPENDENCY_FAILED rather than a
        node that never appears. A node silently absent from the outcome map is the failure mode
        this whole module exists to make impossible.
        """
        sent = set(dispatched)
        out: list[ConductorNode] = []
        for node in self.nodes:
            if node.node_id in sent:
                continue
            if all(
                dep in outcomes and outcomes[dep].state in TERMINAL_STATES
                for dep in node.depends_on
            ):
                out.append(node)
        return tuple(out)


def build_graph(nodes: Sequence[ConductorNode]) -> ConductorGraph:
    """Validate `nodes` into a DAG, or raise `GraphRejectionError` naming exactly what is wrong."""
    if not nodes:
        raise GraphRejectionError("empty_plan")

    seen: set[str] = set()
    for node in nodes:
        node_id = str(node.node_id or "").strip()
        if not node_id:
            raise GraphRejectionError("blank_node_id")
        if node_id in seen:
            # Verified on main: the live-data runner keys outcomes by subtask id in a plain dict,
            # so two subtasks sharing an id return the SAME outcome object twice under different
            # entity names, with no error. A model-proposed plan has none of the incidental
            # uniqueness the recognizers provided, so this must be checked, not assumed.
            raise GraphRejectionError("duplicate_node_id", node_id)
        seen.add(node_id)

    for node in nodes:
        for dep in node.depends_on:
            if dep == node.node_id:
                raise GraphRejectionError("self_dependency", node.node_id)
            if dep not in seen:
                raise GraphRejectionError("unknown_dependency", f"{node.node_id} -> {dep}")

    _reject_cycles(nodes)
    return ConductorGraph(nodes=tuple(nodes))


def _reject_cycles(nodes: Sequence[ConductorNode]) -> None:
    """Kahn sweep: if a pass resolves nothing while nodes remain, the remainder holds a cycle."""
    remaining = {node.node_id: set(node.depends_on) for node in nodes}
    resolved: set[str] = set()
    while remaining:
        ready = [nid for nid, deps in remaining.items() if deps <= resolved]
        if not ready:
            raise GraphRejectionError("cyclic_plan", ",".join(sorted(remaining)))
        for nid in ready:
            resolved.add(nid)
            remaining.pop(nid, None)


def unresolved_dependency_state(
    node: ConductorNode, outcomes: dict[str, NodeOutcome]
) -> NodeLifecycle | None:
    """DEPENDENCY_FAILED when any dependency did not usefully succeed, else None.

    "Usefully" is `NodeOutcome.succeeded`, which requires the declared result fields to be present
    -- a dependency that returned a shape its own contract does not satisfy cannot feed a derived
    node, and letting it through is how a missing field becomes a confident comparison.

    Only the REQUIRED dependencies decide this. `required_node_ids` is the subset the dependent's
    own clause names; empty means all of them, which is what every node meant before the field
    existed. A dependency that is not required may fail without cancelling the dependent -- a third
    city in the same lookup does not make a difference between the other two unanswerable -- while a
    required one blocks it, which is the only thing standing between "one operand is missing" and a
    model writing `x / 1` and calling it an answer.
    """
    required = node.required_node_ids or node.depends_on
    for dep in required:
        outcome = outcomes.get(dep)
        if outcome is None or not outcome.succeeded:
            return NodeLifecycle.DEPENDENCY_FAILED
    return None


__all__ = [
    "ConductorGraph",
    "GraphRejectionError",
    "build_graph",
    "unresolved_dependency_state",
]
