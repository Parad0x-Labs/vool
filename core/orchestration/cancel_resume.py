from __future__ import annotations

from .task_graph import TaskGraph


def request_cancellation(graph: TaskGraph, task_id: str, *, reason: str = "") -> dict[str, str]:
    node = graph.mark_status(task_id, "cancel_requested", result={"reason": str(reason or "").strip()})
    for child in graph.children_of(task_id):
        if child.status in {"pending", "running"}:
            graph.mark_status(child.envelope.task_id, "cancel_requested", result={"reason": str(reason or "").strip()})
    return {"task_id": node.envelope.task_id, "status": node.status}


def resume_task(graph: TaskGraph, task_id: str) -> dict[str, str]:
    node = graph.mark_status(task_id, "pending", result={"resumed": True})
    for child in graph.children_of(task_id):
        if child.status in {"cancelled", "cancel_requested", "interrupted"}:
            graph.mark_status(child.envelope.task_id, "pending", result={"resumed": True})
    return {"task_id": node.envelope.task_id, "status": node.status}
