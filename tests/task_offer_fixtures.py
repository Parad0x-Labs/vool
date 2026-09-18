from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from core.task_capsule import TaskCapsule, build_task_capsule
from network.assist_models import RewardHint, TaskOffer
from network.protocol import encode_message
from network.signer import get_local_peer_id as local_peer_id


def future_deadline(minutes: int = 20) -> datetime:
    return datetime.now(timezone.utc) + timedelta(minutes=minutes)


def build_signed_capsule(
    *,
    task_id: str,
    points: int = 5,
    parent_task_ref: str | None = None,
    verification_of: str | None = None,
) -> TaskCapsule:
    constraints = ["no execution", "strict privacy capsule"]
    if parent_task_ref:
        constraints.append(f"parent_task_ref:{parent_task_ref}")
    if verification_of:
        constraints.append(f"verification_of:{verification_of}")
    return build_task_capsule(
        parent_agent_id=local_peer_id(),
        task_id=task_id,
        task_type="research",
        subtask_type="research_summary",
        summary="Summarize the abstract problem space.",
        sanitized_context={
            "problem_class": "research",
            "environment_tags": {},
            "abstract_inputs": ["abstract item one"],
            "known_constraints": constraints,
        },
        allowed_operations=["reason", "research", "summarize"],
        deadline_ts=future_deadline(),
        reward_hint={"points": points, "wnull_pending": 0},
    )


def build_offer_for_capsule(capsule: TaskCapsule, *, points: int = 5, max_helpers: int = 1) -> TaskOffer:
    return TaskOffer(
        task_id=capsule.task_id,
        parent_agent_id=capsule.parent_agent_id,
        capsule_id=capsule.capsule_id,
        task_type="research",
        subtask_type="research_summary",
        summary="Summarize the abstract problem space.",
        required_capabilities=["research"],
        max_helpers=max_helpers,
        priority="normal",
        reward_hint=RewardHint(points=points, wnull_pending=0),
        capsule=capsule.model_dump(mode="json"),
        deadline_ts=future_deadline(),
    )


def build_signed_task_offer_message(
    *,
    points: int = 5,
    parent_task_ref: str | None = None,
    verification_of: str | None = None,
) -> tuple[bytes, TaskOffer, TaskCapsule]:
    task_id = f"task-{uuid.uuid4().hex}"
    capsule = build_signed_capsule(
        task_id=task_id,
        points=points,
        parent_task_ref=parent_task_ref,
        verification_of=verification_of,
    )
    offer = build_offer_for_capsule(capsule, points=points)
    raw = encode_message(
        msg_id=str(uuid.uuid4()),
        msg_type="TASK_OFFER",
        sender_peer_id=local_peer_id(),
        nonce=uuid.uuid4().hex,
        payload=offer.model_dump(mode="json"),
    )
    return raw, offer, capsule
