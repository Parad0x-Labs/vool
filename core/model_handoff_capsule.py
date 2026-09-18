from __future__ import annotations

import fnmatch
import json
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.runtime_paths import active_data_dir

_SECRET_RE = re.compile(
    r"(?i)(?:\bsk-[a-z0-9_-]{16,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"(?:api[_-]?key|private[_-]?key|seed[_-]?phrase|mnemonic)\s*[:=]\s*\S+)"
)
#: The ONE canonical "credential-shaped" pattern, public so every consumer
#: (capsule scan, kernel leak gate) reuses this definition and two regexes can
#: never drift apart.
SECRET_SCAN = _SECRET_RE
#: The kernel privacy-compartment OPEN marker, mirrored here so the capsule
#: boundary refuses compartment bytes without importing the parser (the
#: parser itself lives at ``core.kernel.compartments``).
_MARKER_OPEN_RE = re.compile(r"\[private:([a-z][a-z0-9_]*)\]")
_WALLET_TERMS = ("wallet", "phantom", "seed", "mnemonic", "private-key", "private_key")
_DEFAULT_EXCLUSIONS = (".env", ".env.*", "*.pem", "*.key", "*wallet*", "*seed*", "*mnemonic*")


@dataclass(frozen=True)
class CapsuleItem:
    item_id: str
    kind: str
    content: str
    provenance: str
    source_path: str = ""


@dataclass(frozen=True)
class ModelHandoffCapsule:
    capsule_id: str
    task_id: str
    turn_id: str
    subtask_id: str
    user_goal: str
    blocked_subtask: str
    rules: tuple[str, ...]
    plan_state: tuple[str, ...]
    expected_output_schema: dict[str, Any]
    forbidden_operations: tuple[str, ...]
    verification_criteria: tuple[str, ...]
    items: tuple[CapsuleItem, ...]
    num_ctx: int
    total_chars: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "vool.model_handoff_capsule.v1",
            "capsule_id": self.capsule_id,
            "task_id": self.task_id,
            "turn_id": self.turn_id,
            "subtask_id": self.subtask_id,
            "user_goal": self.user_goal,
            "blocked_subtask": self.blocked_subtask,
            "rules": list(self.rules),
            "plan_state": list(self.plan_state),
            "expected_output_schema": dict(self.expected_output_schema),
            "forbidden_operations": list(self.forbidden_operations),
            "verification_criteria": list(self.verification_criteria),
            "items": [item.__dict__ for item in self.items],
            "num_ctx": self.num_ctx,
            "total_chars": self.total_chars,
        }

    def payload_summary(self) -> dict[str, Any]:
        return {
            "capsule_id": self.capsule_id,
            "item_count": len(self.items),
            "total_chars": self.total_chars,
            "paths": [item.source_path for item in self.items if item.source_path],
            "kinds": sorted({item.kind for item in self.items}),
            "num_ctx": self.num_ctx,
        }


def build_model_handoff_capsule(
    *,
    task_id: str,
    turn_id: str,
    subtask_id: str,
    user_goal: str,
    blocked_subtask: str,
    rules: tuple[str, ...],
    plan_state: tuple[str, ...],
    expected_output_schema: dict[str, Any],
    forbidden_operations: tuple[str, ...],
    verification_criteria: tuple[str, ...],
    items: tuple[CapsuleItem, ...],
    approved_workspace_roots: tuple[str, ...],
    exclusions: tuple[str, ...] = _DEFAULT_EXCLUSIONS,
    max_chars: int = 60_000,
    num_ctx: int = 4096,
) -> ModelHandoffCapsule:
    roots = tuple(Path(root).expanduser().resolve() for root in approved_workspace_roots)
    if not task_id or not turn_id or not subtask_id or not roots:
        raise ValueError("task, turn, subtask, and approved workspace roots are required")
    accepted: list[CapsuleItem] = []
    total = 0
    for item in items:
        if not item.provenance.strip():
            raise ValueError(f"capsule item {item.item_id} has no provenance")
        content = str(item.content or "")
        path = Path(item.source_path).expanduser().resolve() if item.source_path else None
        if path is not None:
            if not any(path == root or root in path.parents for root in roots):
                raise PermissionError(f"capsule path outside approved roots: {path}")
            lowered = str(path).lower()
            if any(fnmatch.fnmatch(path.name.lower(), pattern.lower()) for pattern in exclusions) or any(
                term in lowered for term in _WALLET_TERMS
            ):
                raise PermissionError(f"capsule path is excluded: {path.name}")
        if _SECRET_RE.search(content):
            raise PermissionError(f"secret detected in capsule item: {item.item_id}")
        if _MARKER_OPEN_RE.search(content):
            # Privacy compartments (``[private:id] ... [/private]``) are the kernel's
            # need-to-know grammar. A marker still present in a capsule item means
            # the caller never minimized the view for this PAID/third-party lane:
            # the private bytes are riding toward a third party inside the marker.
            # Refuse — the caller must send ``parse_compartments(...)[0]`` (the
            # public frame) instead. Broken markup refuses equally: a close with
            # no open is a parse the capsule must not guess its way through.
            raise PermissionError(
                f"privacy compartment marker in capsule item {item.item_id}: "
                "compartment bytes never cross to a paid lane — minimize the view first"
            )
        if total + len(content) > max_chars:
            raise ValueError("context capsule size limit exceeded")
        total += len(content)
        accepted.append(item)
    if _MARKER_OPEN_RE.search(str(user_goal or "")) or "[/private]" in str(user_goal or ""):
        # Same law as the item scan, for the goal string: a compartment marker
        # (open or orphaned close) in the goal means private bytes — or broken
        # privacy markup — are riding to a paid lane. Refuse; send the public frame.
        raise PermissionError(
            "privacy compartment marker in capsule user_goal: minimize the view "
            "(core.kernel.compartments.parse_compartments) before the paid handoff"
        )
    capsule = ModelHandoffCapsule(
        capsule_id=f"capsule-{uuid.uuid4().hex}",
        task_id=task_id,
        turn_id=turn_id,
        subtask_id=subtask_id,
        user_goal=user_goal,
        blocked_subtask=blocked_subtask,
        rules=rules,
        plan_state=plan_state,
        expected_output_schema=dict(expected_output_schema),
        forbidden_operations=forbidden_operations,
        verification_criteria=verification_criteria,
        items=tuple(accepted),
        num_ctx=max(1024, int(num_ctx)),
        total_chars=total,
    )
    _persist_capsule(capsule)
    return capsule


def validate_paid_result(
    result: dict[str, Any],
    *,
    capsule: ModelHandoffCapsule,
    allowed_files: tuple[str, ...] = (),
) -> tuple[bool, tuple[str, ...]]:
    errors: list[str] = []
    for key, expected in (
        ("task_id", capsule.task_id),
        ("turn_id", capsule.turn_id),
        ("subtask_id", capsule.subtask_id),
    ):
        if str(result.get(key) or "") != expected:
            errors.append(f"{key}_mismatch")
    required = tuple(capsule.expected_output_schema.get("required") or ())
    for key in required:
        if key not in result:
            errors.append(f"missing_required_field:{key}")
    allowed = {str(Path(path).expanduser().resolve()) for path in allowed_files}
    for path in list(result.get("proposed_files") or []):
        resolved = str(Path(str(path)).expanduser().resolve())
        if resolved not in allowed:
            errors.append(f"file_out_of_scope:{path}")
    serialized = json.dumps(result, sort_keys=True, default=str)
    if _SECRET_RE.search(serialized):
        errors.append("secret_request_or_content")
    return not errors, tuple(errors)


def select_return_local_lane(*, remaining_work: str, context_tokens: int, heavy_available: bool) -> str:
    work = str(remaining_work or "").strip().lower()
    if work in {"summary", "status", "classification", "done"}:
        return "LOCAL_FAST"
    if heavy_available and (work in {"architecture", "repository_review", "complex_reasoning"} or context_tokens > 16_000):
        return "LOCAL_HEAVY"
    return "LOCAL_DAILY"


def build_local_return_handoff(
    *,
    decision: str,
    proposed_changes: tuple[str, ...],
    constraints: tuple[str, ...],
    verification_steps: tuple[str, ...],
    unresolved_concerns: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "schema": "vool.local_return_handoff.v1",
        "decision": decision,
        "proposed_changes": list(proposed_changes),
        "constraints": list(constraints),
        "verification_steps": list(verification_steps),
        "unresolved_concerns": list(unresolved_concerns),
        "paid_provider_active": False,
    }


def _persist_capsule(capsule: ModelHandoffCapsule) -> None:
    directory = (active_data_dir() / "model_handoff_capsules").resolve()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{capsule.capsule_id}.json"
    temp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(capsule.to_dict(), sort_keys=True, separators=(",", ":")), encoding="utf-8")
    os.replace(temp, path)


__all__ = [
    "CapsuleItem",
    "ModelHandoffCapsule",
    "build_local_return_handoff",
    "build_model_handoff_capsule",
    "select_return_local_lane",
    "validate_paid_result",
]
