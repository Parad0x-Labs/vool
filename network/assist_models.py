from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


class AssistFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow_research: bool = True
    allow_code_reasoning: bool = False
    allow_validation: bool = True
    min_reward_points: int = Field(default=0, ge=0, le=1000)
    trusted_peers_only: bool = False
    # privacy-safe install-group hint; NOT a hardware fingerprint
    host_group_hint_hash: Optional[str] = Field(default=None, min_length=8, max_length=128)


class CapabilityAd(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(min_length=16, max_length=256)
    status: Literal["idle", "busy", "offline", "limited"]
    capabilities: list[str] = Field(max_length=32)
    # Phase 30: Capability-Aware Hardware Benchmarking & Model Weights Sync
    compute_class: Literal["cpu_basic", "cpu_advanced", "gpu_basic", "gpu_elite"] = "cpu_basic"
    supported_models: list[str] = Field(default_factory=list, max_length=16) # List of CAS weight hashes
    capacity: int = Field(ge=0, le=32)
    trust_score: float = Field(ge=0, le=1)
    assist_filters: AssistFilters = Field(default_factory=AssistFilters)
    pow_difficulty: int = Field(default=4, ge=1, le=8)
    timestamp: datetime
    # Phase 30: Sybil Resistance at Genesis
    genesis_nonce: str = Field(default="", max_length=128)

    @field_validator("timestamp")
    @classmethod
    def validate_timestamp(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("timestamp must include timezone")
        return v


class RewardHint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    points: int = Field(default=0, ge=0, le=1000)
    wnull_pending: int = Field(default=0, ge=0, le=1_000_000)


class TaskOffer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=8, max_length=128)
    parent_agent_id: str = Field(min_length=16, max_length=256)
    capsule_id: str = Field(min_length=8, max_length=128)
    task_type: str = Field(max_length=64)
    subtask_type: str = Field(max_length=128)
    summary: str = Field(max_length=512)
    required_capabilities: list[str] = Field(max_length=16)
    max_helpers: int = Field(ge=1, le=10)
    priority: Literal["background", "low", "normal", "high"] = "normal"
    reward_hint: RewardHint = Field(default_factory=RewardHint)
    capsule: dict[str, Any]
    deadline_ts: datetime

    @field_validator("deadline_ts")
    @classmethod
    def validate_deadline(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("deadline_ts must include timezone")
        if v <= datetime.now(timezone.utc):
            raise ValueError("deadline_ts must be in the future")
        return v


class TaskClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=8, max_length=128)
    task_id: str = Field(min_length=8, max_length=128)
    helper_agent_id: str = Field(min_length=16, max_length=256)
    declared_capabilities: list[str] = Field(max_length=16)
    current_load: int = Field(ge=0, le=32)
    host_group_hint_hash: Optional[str] = Field(default=None, min_length=8, max_length=128)
    timestamp: datetime


class TaskAssign(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assignment_id: str = Field(min_length=8, max_length=128)
    task_id: str
    claim_id: str
    parent_agent_id: str
    helper_agent_id: str
    assignment_mode: Literal["single", "parallel", "verification"]
    capability_token: Optional[dict[str, Any]] = None
    timestamp: datetime


class TaskProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assignment_id: str = Field(min_length=8, max_length=128)
    task_id: str
    helper_agent_id: str
    progress_state: Literal["started", "working", "blocked", "done"]
    progress_note: str = Field(max_length=256)
    timestamp: datetime


class TaskResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    result_id: str = Field(min_length=8, max_length=128)
    task_id: str = Field(min_length=8, max_length=128)
    helper_agent_id: str = Field(min_length=16, max_length=256)
    result_type: Literal[
        "research_summary", "classification", "ranking", "validation", "plan_suggestion",
        "draft_output",
        # The helper's real model pipeline returned nothing or raised, so there is no finding to
        # report. Distinct from every other value on purpose: those all imply the model actually
        # produced content, this one says plainly that it did not. Paired with confidence=0.0 and
        # risk_flags=["model_unavailable"] (sandbox/helper_worker.py:_model_unavailable_result).
        "model_unavailable",
    ]
    summary: str = Field(max_length=2048)
    confidence: float = Field(ge=0, le=1)
    evidence: list[str] = Field(default_factory=list, max_length=32)
    abstract_steps: list[str] = Field(default_factory=list, max_length=32)
    risk_flags: list[str] = Field(default_factory=list, max_length=16)
    result_hash: Optional[str] = Field(default=None, min_length=16, max_length=128)
    timestamp: datetime


class TaskReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    helper_agent_id: str
    reviewer_agent_id: str
    outcome: Literal["accepted", "partial", "rejected", "harmful"]
    helpfulness_score: float = Field(ge=0, le=1)
    quality_score: float = Field(ge=0, le=1)
    harmful: bool
    timestamp: datetime


class TaskReward(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    helper_agent_id: str
    points_awarded: int = Field(ge=0, le=1_000_000)
    wnull_pending: int = Field(ge=0, le=1_000_000_000)
    slashed: bool
    timestamp: datetime


class TaskCancel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    parent_agent_id: str
    reason: str
    timestamp: datetime


# ------------- Phase 29: Credit Market DEX -------------

class CreditOffer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    offer_id: str = Field(min_length=8, max_length=128)
    seller_peer_id: str = Field(min_length=16, max_length=256)
    credits_available: int = Field(ge=1, le=10_000_000)
    usdc_per_credit: float = Field(ge=0.0001, le=10.0)
    seller_wallet_address: str = Field(min_length=32, max_length=128)
    timestamp: datetime


class CreditTransfer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transfer_id: str = Field(min_length=8, max_length=128)
    seller_peer_id: str = Field(min_length=16, max_length=256)
    buyer_peer_id: str = Field(min_length=16, max_length=256)
    credits_transferred: int = Field(ge=1)
    on_chain_tx_hash: str = Field(min_length=32, max_length=256)
    timestamp: datetime


_MODEL_BY_MSG_TYPE = {
    "CAPABILITY_AD": CapabilityAd,
    "TASK_OFFER": TaskOffer,
    "TASK_CLAIM": TaskClaim,
    "TASK_ASSIGN": TaskAssign,
    "TASK_PROGRESS": TaskProgress,
    "TASK_RESULT": TaskResult,
    "TASK_REVIEW": TaskReview,
    "TASK_REWARD": TaskReward,
    "TASK_CANCEL": TaskCancel,
    "CREDIT_OFFER": CreditOffer,
    "CREDIT_TRANSFER": CreditTransfer,
}


def validate_assist_payload(msg_type: str, payload: dict[str, Any]) -> BaseModel:
    model = _MODEL_BY_MSG_TYPE.get(msg_type)
    if not model:
        raise ValueError(f"Unsupported assist msg_type: {msg_type}")
    try:
        return model.model_validate(payload)
    except ValidationError as e:
        raise ValueError(f"Assist payload validation failed: {e}") from e
