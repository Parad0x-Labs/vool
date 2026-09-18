from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from .role_contracts import TaskRole, get_role_contract


@dataclass(frozen=True)
class TaskEnvelopeV1:
    task_id: str
    parent_task_id: str
    role: TaskRole
    goal: str
    inputs: dict[str, Any] = field(default_factory=dict)
    tool_permissions: tuple[str, ...] = ()
    model_constraints: dict[str, Any] = field(default_factory=dict)
    latency_budget: str = "balanced"
    quality_target: str = "standard"
    allowed_side_effects: tuple[str, ...] = ()
    required_receipts: tuple[str, ...] = ()
    merge_strategy: str = "first_success"
    cancellation_policy: str = "cancel_children_first"
    privacy_class: str = "local_only"

    def __post_init__(self) -> None:
        if not str(self.task_id or "").strip():
            raise ValueError("task_id is required")
        if not str(self.goal or "").strip():
            raise ValueError("goal is required")
        get_role_contract(self.role)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "vool.task_envelope.v1",
            "task_id": self.task_id,
            "parent_task_id": self.parent_task_id,
            "role": self.role,
            "goal": self.goal,
            "inputs": dict(self.inputs or {}),
            "tool_permissions": list(self.tool_permissions),
            "model_constraints": dict(self.model_constraints or {}),
            "latency_budget": self.latency_budget,
            "quality_target": self.quality_target,
            "allowed_side_effects": list(self.allowed_side_effects),
            "required_receipts": list(self.required_receipts),
            "merge_strategy": self.merge_strategy,
            "cancellation_policy": self.cancellation_policy,
            "privacy_class": self.privacy_class,
        }


def build_task_envelope(
    *,
    role: TaskRole,
    goal: str,
    task_id: str | None = None,
    parent_task_id: str = "",
    inputs: dict[str, Any] | None = None,
    tool_permissions: list[str] | tuple[str, ...] | None = None,
    model_constraints: dict[str, Any] | None = None,
    latency_budget: str = "balanced",
    quality_target: str = "standard",
    allowed_side_effects: list[str] | tuple[str, ...] | None = None,
    required_receipts: list[str] | tuple[str, ...] | None = None,
    merge_strategy: str = "first_success",
    cancellation_policy: str = "cancel_children_first",
    privacy_class: str = "local_only",
) -> TaskEnvelopeV1:
    contract = get_role_contract(role)
    return TaskEnvelopeV1(
        task_id=str(task_id or f"task-{uuid.uuid4().hex}"),
        parent_task_id=str(parent_task_id or ""),
        role=role,
        goal=str(goal or "").strip(),
        inputs=dict(inputs or {}),
        tool_permissions=tuple(tool_permissions or contract.default_tool_permissions),
        model_constraints=dict(model_constraints or {}),
        latency_budget=str(latency_budget or "balanced"),
        quality_target=str(quality_target or "standard"),
        allowed_side_effects=tuple(allowed_side_effects or contract.default_allowed_side_effects),
        required_receipts=tuple(required_receipts or ()),
        merge_strategy=str(merge_strategy or "first_success"),
        cancellation_policy=str(cancellation_policy or "cancel_children_first"),
        privacy_class=str(privacy_class or "local_only"),
    )


def task_envelope_from_dict(payload: dict[str, Any]) -> TaskEnvelopeV1:
    data = dict(payload or {})
    role = str(data.get("role") or "").strip()
    contract = get_role_contract(role)
    # K-07 ONE WIDENING AUTHORITY: the A1 decide seam (mode_permission_policy
    # via the role contracts) is the sole option source. Caller-supplied
    # permission/side-effect lists may only NARROW the role contract's grants;
    # a payload listing capabilities the contract does not grant is dropped,
    # never honored.
    declared_permissions = tuple(
        str(item).strip() for item in list(data.get("tool_permissions") or []) if str(item).strip()
    )
    granted_permissions = (
        tuple(p for p in contract.default_tool_permissions if p in set(declared_permissions))
        if declared_permissions
        else contract.default_tool_permissions
    )
    declared_effects = tuple(
        str(item).strip() for item in list(data.get("allowed_side_effects") or []) if str(item).strip()
    )
    granted_effects = (
        tuple(e for e in contract.default_allowed_side_effects if e in set(declared_effects))
        if declared_effects
        else contract.default_allowed_side_effects
    )
    return TaskEnvelopeV1(
        task_id=str(data.get("task_id") or "").strip(),
        parent_task_id=str(data.get("parent_task_id") or "").strip(),
        role=role,  # type: ignore[arg-type]
        goal=str(data.get("goal") or "").strip(),
        inputs=dict(data.get("inputs") or {}),
        tool_permissions=granted_permissions,
        model_constraints=dict(data.get("model_constraints") or {}),
        latency_budget=str(data.get("latency_budget") or "balanced"),
        quality_target=str(data.get("quality_target") or "standard"),
        allowed_side_effects=granted_effects,
        required_receipts=tuple(
            str(item).strip() for item in list(data.get("required_receipts") or []) if str(item).strip()
        ),
        merge_strategy=str(data.get("merge_strategy") or "first_success"),
        cancellation_policy=str(data.get("cancellation_policy") or "cancel_children_first"),
        privacy_class=str(data.get("privacy_class") or "local_only"),
    )
