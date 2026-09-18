from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .task_envelope import TaskEnvelopeV1


@dataclass
class TaskGraphNode:
    envelope: TaskEnvelopeV1
    status: str = "pending"
    result: dict[str, Any] = field(default_factory=dict)
    children: set[str] = field(default_factory=set)


class TaskGraph:
    def __init__(self) -> None:
        self._nodes: dict[str, TaskGraphNode] = {}

    def add_task(self, envelope: TaskEnvelopeV1) -> TaskGraphNode:
        if envelope.task_id in self._nodes:
            raise ValueError(f"task_id already exists: {envelope.task_id}")
        node = TaskGraphNode(envelope=envelope)
        self._nodes[envelope.task_id] = node
        if envelope.parent_task_id and envelope.parent_task_id in self._nodes:
            self._nodes[envelope.parent_task_id].children.add(envelope.task_id)
        return node

    def get(self, task_id: str) -> TaskGraphNode | None:
        return self._nodes.get(str(task_id or "").strip())

    def mark_status(self, task_id: str, status: str, *, result: dict[str, Any] | None = None) -> TaskGraphNode:
        node = self._require(task_id)
        node.status = str(status or "").strip() or node.status
        if result:
            node.result = dict(result)
        return node

    def children_of(self, task_id: str) -> tuple[TaskGraphNode, ...]:
        node = self._require(task_id)
        return tuple(self._nodes[child_id] for child_id in sorted(node.children))

    def nodes(self) -> tuple[TaskGraphNode, ...]:
        return tuple(self._nodes[key] for key in sorted(self._nodes))

    def _require(self, task_id: str) -> TaskGraphNode:
        node = self.get(task_id)
        if node is None:
            raise KeyError(task_id)
        return node
