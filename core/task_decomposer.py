from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from core import audit_logger, policy_engine
from core.evidence_bundle import build_evidence_bundle
from core.liquefy_bridge import stream_telemetry_event
from core.provenance_store import store_manifest
from core.task_capsule import TaskCapsule, build_task_capsule, sanitize_for_capsule
from core.task_router import redact_text
from core.task_state_machine import transition
from core.trace_id import ensure_trace
from network.assist_models import RewardHint, TaskOffer
from network.signer import get_local_peer_id as local_peer_id
from retrieval.swarm_query import broadcast_task_offer


@dataclass
class DecomposedSubtask:
    subtask_id: str
    required_capabilities: list[str]
    capsule: TaskCapsule
    offer: TaskOffer


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _split_abstract_inputs(user_input: str, *, max_items: int = 6) -> list[str]:
    # The general redaction first, then the capsule's own strict classes: the destination is a
    # strict TaskCapsule, and the two sanitizers disagree (the router's path list names only
    # sensitive absolute prefixes; the capsule rejects every raw path). Sanitizing to the
    # destination's contract here is what keeps an interrupted-work resume carrying its
    # workspace path from dying at sanitized_context.abstract_inputs.
    text = sanitize_for_capsule(redact_text(user_input))
    raw_parts = []
    for sep in ["\n", ".", ";", ",", " and ", " but "]:
        if sep in text:
            raw_parts = [p.strip() for p in text.split(sep) if p.strip()]
            if len(raw_parts) > 1:
                break
    if not raw_parts:
        raw_parts = [text]

    out: list[str] = []
    for part in raw_parts:
        norm = " ".join(part.split())
        if len(norm) < 8:
            continue
        out.append(norm[:256])
        if len(out) >= max_items:
            break

    return out or [text[:256]]


def should_decompose(user_input: str, classification: dict[str, Any]) -> bool:
    task_class = classification.get("task_class", "unknown")
    text = redact_text(user_input)

    if task_class in {"risky_system_action", "unknown"}:
        return False

    if len(text) >= 280:
        return True

    if task_class in {"research", "debugging", "dependency_resolution", "config"}:
        markers = sum(
            1
            for token in [" and ", " compare ", " multiple ", " options ", " validate ", " check ", " research "]
            if token in text.lower()
        )
        return markers >= 1

    return False


def _task_templates(task_class: str, abstract_inputs: list[str]) -> list[dict[str, Any]]:
    """
    Max 3 useful subtasks. No over-splitting.
    """
    if task_class == "research":
        return [
            {
                "task_type": "research",
                "subtask_type": "source_comparison",
                "required_capabilities": ["research", "ranking"],
                "summary": "Compare the strongest abstract options and identify the safest likely fit.",
                "reward": {"points": 12, "wnull_pending": 6},
            },
            {
                "task_type": "validation",
                "subtask_type": "constraint_check",
                "required_capabilities": ["validation"],
                "summary": "Validate that the likely option fits the known constraints.",
                "reward": {"points": 10, "wnull_pending": 5},
            },
        ]

    if task_class in {"debugging", "dependency_resolution"}:
        return [
            {
                "task_type": "classification",
                "subtask_type": "root_cause_mapping",
                "required_capabilities": ["classification"],
                "summary": "Map the redacted issue to the most likely root-cause class.",
                "reward": {"points": 10, "wnull_pending": 5},
            },
            {
                "task_type": "ranking",
                "subtask_type": "fix_option_ranking",
                "required_capabilities": ["ranking", "validation"],
                "summary": "Rank the safest abstract fix paths by likely fit and risk.",
                "reward": {"points": 14, "wnull_pending": 7},
            },
            {
                "task_type": "validation",
                "subtask_type": "constraint_fit",
                "required_capabilities": ["validation"],
                "summary": "Check which proposed fix path best respects the known constraints.",
                "reward": {"points": 10, "wnull_pending": 5},
            },
        ]

    if task_class == "config":
        return [
            {
                "task_type": "classification",
                "subtask_type": "config_pattern_id",
                "required_capabilities": ["classification"],
                "summary": "Identify the config-pattern mismatch from sanitized clues.",
                "reward": {"points": 9, "wnull_pending": 4},
            },
            {
                "task_type": "validation",
                "subtask_type": "safe_default_validation",
                "required_capabilities": ["validation"],
                "summary": "Validate the safest likely default configuration path.",
                "reward": {"points": 9, "wnull_pending": 4},
            },
        ]

    # generic fallback
    return [
        {
            "task_type": "planning",
            "subtask_type": "safe_plan_assist",
            "required_capabilities": ["research", "validation"],
            "summary": "Produce a safe abstract next-step plan from the sanitized context.",
            "reward": {"points": 8, "wnull_pending": 4},
        }
    ]


def _scaled_templates(
    templates: list[dict[str, Any]],
    *,
    abstract_inputs: list[str],
    target_count: int,
) -> list[dict[str, Any]]:
    if target_count <= 0:
        return []
    if len(templates) >= target_count:
        return templates[:target_count]

    out = list(templates)
    lane = 0
    while len(out) < target_count:
        seed = abstract_inputs[lane % max(1, len(abstract_inputs))]
        clipped = sanitize_for_capsule(" ".join(str(seed).split())[:140])
        lane_index = len(out) + 1
        out.append(
            {
                "task_type": "research",
                "subtask_type": f"parallel_evidence_lane_{lane_index}",
                "required_capabilities": ["research", "validation"],
                "summary": f"Independently analyze this sanitized lane and extract actionable evidence: {clipped}",
                "reward": {"points": 8, "wnull_pending": 4},
            }
        )
        lane += 1

    return out[:target_count]


def decompose_task(
    *,
    parent_task_id: str,
    user_input: str,
    classification: dict[str, Any],
    environment_tags: dict[str, str] | None = None,
    deadline_minutes: int = 20,
    max_subtasks: int = 3,
    bid_multiplier: float = 1.0,
) -> list[DecomposedSubtask]:
    task_class = classification.get("task_class", "unknown")
    if not should_decompose(user_input, classification):
        return []

    parent_trace = ensure_trace(parent_task_id, trace_id=parent_task_id)
    env = environment_tags or {}
    abstract_inputs = _split_abstract_inputs(user_input, max_items=6)
    subtask_hard_cap = max(1, int(policy_engine.get("orchestration.max_subtasks_hard_cap", 10)))
    target_subtasks = max(1, min(int(max_subtasks), subtask_hard_cap))
    templates = _scaled_templates(
        _task_templates(task_class, abstract_inputs),
        abstract_inputs=abstract_inputs,
        target_count=target_subtasks,
    )

    helpers_hard_cap = max(1, int(policy_engine.get("orchestration.max_helpers_hard_cap", 10)))
    configured_helpers = int(policy_engine.get("orchestration.max_helpers_per_subtask", 1))
    max_helpers_per_subtask = max(1, min(configured_helpers, helpers_hard_cap))

    out: list[DecomposedSubtask] = []
    parent_peer = local_peer_id()
    deadline = _utcnow() + timedelta(minutes=max(5, deadline_minutes))

    for template in templates:
        subtask_id = str(uuid.uuid4())
        subtask_trace = ensure_trace(subtask_id, parent_trace_id=parent_trace.trace_id)
        known_constraints = [
            "no execution",
            "no raw secrets",
            "strict privacy capsule",
            # The reference ids ride inside constraint strings the capsule scans with the same
            # strict law; sanitize so a long or hex-like task id cannot fail the handoff either.
            sanitize_for_capsule(f"parent_task_ref:{parent_task_id}"),
        ]
        bundle = build_evidence_bundle(
            task_id=subtask_id,
            trace_id=subtask_trace.trace_id,
            summary=template["summary"],
            abstract_inputs=abstract_inputs[:6],
            constraints=known_constraints,
            environment_tags=env,
        )
        store_manifest(bundle.manifest)
        known_constraints.append(sanitize_for_capsule(f"context_manifest:{bundle.manifest.manifest_id}"))

        # Phase 27: Dynamic Bidding Order Book
        bid_points = int(template["reward"]["points"] * bid_multiplier)
        reward_dict = {"points": bid_points, "wnull_pending": template["reward"]["wnull_pending"]}

        capsule = build_task_capsule(
            parent_agent_id=parent_peer,
            task_id=subtask_id,
            task_type=template["task_type"],
            subtask_type=template["subtask_type"],
            summary=template["summary"],
            sanitized_context={
                "problem_class": task_class,
                "environment_tags": env,
                "abstract_inputs": abstract_inputs[:6],
                "known_constraints": known_constraints,
            },
            allowed_operations=["reason", "research", "compare", "rank", "summarize", "validate", "draft"],
            deadline_ts=deadline,
            reward_hint=reward_dict,
        )
        transition(
            entity_type="subtask",
            entity_id=subtask_id,
            to_state="created",
            details={"task_type": template["task_type"], "subtask_type": template["subtask_type"]},
            trace_id=subtask_trace.trace_id,
        )

        offer = TaskOffer(
            task_id=subtask_id,
            parent_agent_id=parent_peer,
            capsule_id=capsule.capsule_id,
            task_type=template["task_type"],
            subtask_type=template["subtask_type"],
            summary=template["summary"],
            required_capabilities=template["required_capabilities"],
            max_helpers=max_helpers_per_subtask,
            priority="high" if bid_points > template["reward"]["points"] else "normal",
            reward_hint=RewardHint(**reward_dict),
            capsule=capsule.model_dump(mode="json"),
            deadline_ts=deadline,
        )

        out.append(
            DecomposedSubtask(
                subtask_id=subtask_id,
                required_capabilities=list(template["required_capabilities"]),
                capsule=capsule,
                offer=offer,
            )
        )

        stream_telemetry_event(
            event_type="TASK_OFFER",
            target_id=subtask_id,
            details={"parent_task_id": parent_task_id, "summary": template["summary"]}
        )

    audit_logger.log(
        "task_decomposed",
        target_id=parent_task_id,
        target_type="task",
        details={
            "task_class": task_class,
            "subtask_count": len(out),
        },
    )

    return out


def broadcast_decomposed_subtasks(
    subtasks: list[DecomposedSubtask],
    *,
    exclude_host_group_hint_hash: str | None = None,
) -> int:
    sent = 0

    for subtask in subtasks:
        payload = subtask.offer.model_dump(mode="json")
        sent += broadcast_task_offer(
            offer_payload=payload,
            required_capabilities=subtask.required_capabilities,
            exclude_host_group_hint_hash=exclude_host_group_hint_hash,
            limit=8,
        )
        trace = ensure_trace(subtask.subtask_id)
        transition(
            entity_type="subtask",
            entity_id=subtask.subtask_id,
            to_state="offered",
            details={"required_capabilities": subtask.required_capabilities},
            trace_id=trace.trace_id,
        )

    audit_logger.log(
        "decomposed_subtasks_broadcast",
        target_id=local_peer_id(),
        target_type="peer",
        details={
            "subtasks": len(subtasks),
            "offers_sent": sent,
        },
    )

    return sent
