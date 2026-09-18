from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .role_contracts import provider_role_for_task_role
from .task_envelope import TaskEnvelopeV1


@dataclass(frozen=True)
class ScheduledTask:
    task_id: str
    provider_role: str
    priority: float
    availability_state: str = "ready"
    queue_pressure: float = 0.0
    selected_provider_id: str = ""
    provider_locality: str = ""
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "provider_role": self.provider_role,
            "priority": self.priority,
            "availability_state": self.availability_state,
            "queue_pressure": self.queue_pressure,
            "selected_provider_id": self.selected_provider_id,
            "provider_locality": self.provider_locality,
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class TaskCapacityState:
    task_id: str
    provider_role: str
    availability_state: str
    queue_pressure: float
    selected_provider_id: str
    provider_locality: str
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "provider_role": self.provider_role,
            "availability_state": self.availability_state,
            "queue_pressure": self.queue_pressure,
            "selected_provider_id": self.selected_provider_id,
            "provider_locality": self.provider_locality,
            "notes": list(self.notes),
        }


def schedule_task_envelopes(envelopes: list[TaskEnvelopeV1]) -> list[ScheduledTask]:
    scored: list[tuple[tuple[int, float, float, str], ScheduledTask]] = []
    for envelope in envelopes:
        latency_bonus = {"low_latency": 30, "balanced": 20, "deep": 10}.get(str(envelope.latency_budget or "balanced"), 20)
        quality_bonus = {"high": 20, "standard": 10, "draft": 5}.get(str(envelope.quality_target or "standard"), 10)
        side_effect_penalty = 5 if envelope.allowed_side_effects else 0
        capacity = evaluate_task_envelope_capacity(envelope)
        priority = float(latency_bonus + quality_bonus - side_effect_penalty)
        if capacity.availability_state == "degraded":
            priority -= min(12.0, 4.0 + capacity.queue_pressure * 3.0)
        elif capacity.availability_state == "blocked":
            priority -= 100.0
        scheduled = ScheduledTask(
            task_id=envelope.task_id,
            provider_role=capacity.provider_role,
            priority=priority,
            availability_state=capacity.availability_state,
            queue_pressure=capacity.queue_pressure,
            selected_provider_id=capacity.selected_provider_id,
            provider_locality=capacity.provider_locality,
            notes=capacity.notes,
        )
        availability_rank = {"ready": 2, "degraded": 1, "blocked": 0}.get(capacity.availability_state, 1)
        scored.append(((availability_rank, priority, -capacity.queue_pressure, envelope.task_id), scheduled))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [item[1] for item in scored]


def evaluate_task_envelope_capacity(envelope: TaskEnvelopeV1) -> TaskCapacityState:
    provider_role = provider_role_for_task_role(envelope.role)
    truth_rows = _provider_capability_truth_rows(envelope)
    selected = truth_rows[0] if truth_rows else {}
    selected_provider_id = str(selected.get("provider_id") or "").strip()
    selected_locality = str(selected.get("locality") or "").strip()
    queue_depth = max(0, int(selected.get("queue_depth") or 0))
    max_safe_concurrency = max(1, int(selected.get("max_safe_concurrency") or 1))
    queue_pressure = float(queue_depth) / float(max_safe_concurrency)
    required_locality = _required_locality(envelope)
    notes: list[str] = []
    availability_state = "ready"
    if required_locality and truth_rows and selected_locality and selected_locality != required_locality:
        availability_state = "blocked"
        notes.append("requires_local_provider")
    elif required_locality and not truth_rows:
        availability_state = "degraded"
        notes.append("provider_truth_missing")
    if truth_rows:
        role_fit = str(selected.get("role_fit") or "").strip()
        if role_fit and role_fit not in {"auto", provider_role}:
            notes.append("role_fit_mismatch")
            if availability_state == "ready":
                availability_state = "degraded"
        if queue_pressure >= 1.0 and availability_state == "ready":
            availability_state = "degraded"
            notes.append("queue_pressure_high")
        tool_support = {
            str(item).strip().lower()
            for item in list(selected.get("tool_support") or [])
            if str(item).strip()
        }
        if envelope.role == "coder" and "code_complex" not in tool_support and availability_state == "ready":
            notes.append("weak_code_support")
        if envelope.role in {"queen", "narrator", "coder", "verifier"} and not bool(selected.get("structured_output_support", False)):
            notes.append("structured_output_missing")
            if availability_state == "ready":
                availability_state = "degraded"
    return TaskCapacityState(
        task_id=envelope.task_id,
        provider_role=provider_role,
        availability_state=availability_state,
        queue_pressure=queue_pressure,
        selected_provider_id=selected_provider_id,
        provider_locality=selected_locality,
        notes=tuple(notes),
    )


def _required_locality(envelope: TaskEnvelopeV1) -> str:
    privacy_class = str(envelope.privacy_class or "").strip().lower()
    if privacy_class == "local_private":
        return "local"
    if any(effect in {"workspace_write", "memory_write"} for effect in envelope.allowed_side_effects):
        return "local"
    return ""


def _provider_capability_truth_rows(envelope: TaskEnvelopeV1) -> list[dict[str, Any]]:
    raw = envelope.model_constraints.get("provider_capability_truth")
    if isinstance(raw, dict):
        return [dict(raw)]
    if isinstance(raw, list):
        return [dict(item) for item in raw if isinstance(item, dict)]
    return []
