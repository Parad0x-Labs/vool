from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from core.task_capsule import TaskCapsule
from network.assist_models import CapabilityAd, TaskOffer


@dataclass
class IdleAssistConfig:
    mode: str = "passive"  # off | passive | active
    max_concurrent_tasks: int = 2
    trusted_peers_only: bool = False
    min_reward_points: int = 0
    allow_research: bool = True
    allow_code_reasoning: bool = False
    allow_validation: bool = True
    strict_privacy_only: bool = True
    require_idle_status: bool = True


@dataclass
class AssistDecision:
    accept: bool
    reason: str


def _task_type_allowed(config: IdleAssistConfig, offer: TaskOffer) -> bool:
    if offer.task_type == "research":
        return config.allow_research
    if offer.task_type in {"validation", "ranking", "classification"}:
        return config.allow_validation
    if offer.task_type == "code_reasoning":
        return config.allow_code_reasoning
    # planning/documentation default to passive-safe
    return True


def should_accept_offer(
    *,
    config: IdleAssistConfig,
    capability_ad: CapabilityAd,
    offer: TaskOffer,
    capsule: TaskCapsule,
    parent_trust: float,
    current_assignments: int,
    same_host_group_suspect: bool = False,
) -> AssistDecision:
    if config.mode == "off":
        return AssistDecision(False, "Idle Assist is off.")

    if config.require_idle_status and capability_ad.status != "idle":
        return AssistDecision(False, "Agent is not idle.")

    if current_assignments >= config.max_concurrent_tasks:
        return AssistDecision(False, "Local assist capacity reached.")

    if config.trusted_peers_only and parent_trust < 0.65:
        return AssistDecision(False, "Parent peer trust below local threshold.")

    if offer.reward_hint.points < config.min_reward_points:
        # Phase 25: Bypass reward minimums if we can farm the memory context for our local AI
        if not capsule.learning_allowed:
            return AssistDecision(False, "Reward below local threshold and no learning allowed.")

    if not _task_type_allowed(config, offer):
        return AssistDecision(False, "Task type disabled by local policy.")

    if config.strict_privacy_only and capsule.privacy_level != "strict":
        return AssistDecision(False, "Only strict privacy capsules allowed.")

    if same_host_group_suspect:
        return AssistDecision(False, "Same-host-group suspect; possible same-machine farm.")

    # Capability intersection check
    local_caps = set(capability_ad.capabilities)
    needed = set(offer.required_capabilities)
    if needed and local_caps.isdisjoint(needed):
        return AssistDecision(False, "Required capabilities do not match local capabilities.")

    # Phase 30: Capability-Aware Model Hashes Check
    if capsule.required_model and capsule.required_model not in capability_ad.supported_models:
        return AssistDecision(False, f"Required LLM model hash '{capsule.required_model}' is not supported locally.")

    # Enforce safe capsule scope
    forbidden = set(capsule.forbidden_operations)
    required_forbidden = {"execute", "access_db", "call_shell", "request_secrets", "install_packages"}
    if not required_forbidden.issubset(forbidden):
        return AssistDecision(False, "Capsule scope is too permissive.")

    if capsule.deadline_ts <= datetime.now(timezone.utc):
        return AssistDecision(False, "Task already expired.")

    return AssistDecision(True, "Offer accepted under local Idle Assist policy.")
