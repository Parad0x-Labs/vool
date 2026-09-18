"""Runtime-observed evidence that independent nodes actually overlapped.

The rule this enforces: a claim of concurrency is only ever made from intervals the runtime
recorded inside each worker. Model prose -- "I ran these in parallel", "executed concurrently" --
is not consulted, cannot be consulted, and would not change the answer if it were. `proves_concurrency`
is computed from `time.monotonic()` stamps taken immediately before and after each node's work.

`max_concurrent >= 2` alone is not the test, and neither is total elapsed time. A seven-node
sequential run still has seven timed nodes, and a fast provider can make a sequential run look
suspiciously quick. The predicate requires a genuine overlapping *pair*, so the only way to satisfy
it is for two intervals to actually intersect.

The sweep is deliberately its own implementation rather than an import from
`core.agent_runtime.live_data_runner`. That module is a domain lane; a generic conductor depending
on the weather/markets lane inverts the layering the whole design exists to fix.
`tests/test_conductor_concurrency.py` asserts the two agree on identical interval sets, so the
duplication is drift-detected rather than hoped about.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from core.conductor.node import NodeOutcome, node_receipt


@dataclass(frozen=True)
class ConcurrencyReport:
    """What the clocks say about overlap. Same contract and same predicate as the live-data report."""

    max_concurrent: int
    overlapping_pairs: tuple[tuple[str, str], ...]
    timed_node_count: int

    @property
    def proves_concurrency(self) -> bool:
        """True only when two nodes genuinely shared wall-clock time.

        Both halves are load-bearing. Without the pair requirement a run where nodes merely queued
        against the same instant could score highly; without the count requirement a single long
        node would trivially satisfy the pair check against itself.
        """
        return self.max_concurrent >= 2 and bool(self.overlapping_pairs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_concurrent": self.max_concurrent,
            "overlapping_pairs": [list(pair) for pair in self.overlapping_pairs],
            "timed_node_count": self.timed_node_count,
            "proves_concurrency": self.proves_concurrency,
        }


def concurrency_report(outcomes: Sequence[NodeOutcome]) -> ConcurrencyReport:
    """Sweep-line over `[started_at, completed_at)` across every node that actually ran.

    Nodes without both stamps are excluded rather than treated as zero-duration. A node that never
    ran -- unresolved, dependency-failed -- has no interval, and counting it as an instantaneous
    one would let a plan full of skipped work report a healthy timed count.
    """
    timed = [
        outcome
        for outcome in outcomes
        if outcome.started_at is not None and outcome.completed_at is not None
    ]

    events: list[tuple[float, int]] = []
    for outcome in timed:
        events.append((float(outcome.started_at), 1))    # type: ignore[arg-type]
        events.append((float(outcome.completed_at), -1))  # type: ignore[arg-type]
    # Ends sort before starts at the same instant (-1 < 1), so a handoff at an identical timestamp
    # is not counted as overlap. That matches `overlaps()`'s half-open interval exactly.
    events.sort()

    running = 0
    max_concurrent = 0
    for _instant, delta in events:
        running += delta
        max_concurrent = max(max_concurrent, running)

    pairs: list[tuple[str, str]] = []
    for index, left in enumerate(timed):
        for right in timed[index + 1 :]:
            if left.overlaps(right):
                pairs.append((left.node.node_id, right.node.node_id))

    return ConcurrencyReport(
        max_concurrent=max_concurrent,
        overlapping_pairs=tuple(pairs),
        timed_node_count=len(timed),
    )


def plan_receipt(
    outcomes: Sequence[NodeOutcome], *, plan_id: str, parallel_preferred: bool
) -> dict[str, Any]:
    """The whole plan's runtime record: one receipt per node plus the overlap proof.

    `parallel_preferred` is carried so a reader can tell "did not overlap" apart from "was never
    meant to". A plan of one node, or a strict dependency chain, correctly proves no concurrency
    and that is not a defect.
    """
    report = concurrency_report(outcomes)
    return {
        "schema": "conductor_plan_receipt_v1",
        "plan_id": plan_id,
        "parallel_preferred": bool(parallel_preferred),
        "node_count": len(outcomes),
        "succeeded_count": sum(1 for outcome in outcomes if outcome.succeeded),
        "concurrency": report.to_dict(),
        "nodes": [node_receipt(outcome, plan_id=plan_id) for outcome in outcomes],
    }


__all__ = ["ConcurrencyReport", "concurrency_report", "plan_receipt"]
