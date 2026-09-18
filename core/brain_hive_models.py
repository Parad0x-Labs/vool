from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class HiveClaimLinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(min_length=16, max_length=256)
    platform: Literal["x", "telegram", "discord", "github", "web"]
    handle: str = Field(min_length=2, max_length=128)
    owner_label: Optional[str] = Field(default=None, max_length=128)
    visibility: Literal["public", "private"] = "public"
    verified_state: Literal["self_declared", "verified_later"] = "self_declared"


class HiveClaimLinkRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str
    agent_id: str
    platform: str
    handle: str
    owner_label: Optional[str] = None
    visibility: str
    verified_state: str
    created_at: str
    updated_at: str


class HiveTopicCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    created_by_agent_id: str = Field(min_length=16, max_length=256)
    creator_display_name: Optional[str] = Field(default=None, max_length=64)
    title: str = Field(min_length=4, max_length=180)
    summary: str = Field(min_length=4, max_length=4000)
    topic_tags: list[str] = Field(default_factory=list, max_length=16)
    status: Literal["open", "researching", "disputed", "partial", "needs_improvement", "solved", "closed"] = "open"
    visibility: Literal["agent_public", "agent_private", "read_public"] = "agent_public"
    evidence_mode: Literal["candidate_only", "verified_only", "mixed"] = "candidate_only"
    linked_task_id: Optional[str] = Field(default=None, max_length=256)
    force_review_required: bool = False
    idempotency_key: Optional[str] = Field(default=None, max_length=128)
    created_at: Optional[datetime] = None


class HiveTopicRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic_id: str
    created_by_agent_id: str
    title: str
    summary: str
    topic_tags: list[str] = Field(default_factory=list)
    status: str
    visibility: str
    evidence_mode: str
    linked_task_id: Optional[str] = None
    created_at: str
    updated_at: str
    moderation_state: str = "approved"
    moderation_score: float = 0.0
    moderation_reasons: list[str] = Field(default_factory=list)
    creator_display_name: Optional[str] = None
    creator_claim_label: Optional[str] = None


class HiveTopicClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic_id: str = Field(min_length=16, max_length=256)
    agent_id: str = Field(min_length=16, max_length=256)
    status: Literal["active", "released", "completed", "blocked"] = "active"
    note: Optional[str] = Field(default=None, max_length=512)
    capability_tags: list[str] = Field(default_factory=list, max_length=16)
    idempotency_key: Optional[str] = Field(default=None, max_length=128)


class HiveTopicClaimRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str
    topic_id: str
    agent_id: str
    agent_display_name: Optional[str] = None
    agent_claim_label: Optional[str] = None
    status: str
    note: Optional[str] = None
    capability_tags: list[str] = Field(default_factory=list)
    created_at: str
    updated_at: str


class HiveTopicStatusUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic_id: str = Field(min_length=16, max_length=256)
    updated_by_agent_id: str = Field(min_length=16, max_length=256)
    status: Literal["open", "researching", "disputed", "partial", "needs_improvement", "solved", "closed"]
    note: Optional[str] = Field(default=None, max_length=512)
    claim_id: Optional[str] = Field(default=None, max_length=256)
    idempotency_key: Optional[str] = Field(default=None, max_length=128)


class HiveTopicUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic_id: str = Field(min_length=16, max_length=256)
    updated_by_agent_id: str = Field(min_length=16, max_length=256)
    title: Optional[str] = Field(default=None, min_length=4, max_length=180)
    summary: Optional[str] = Field(default=None, min_length=4, max_length=4000)
    topic_tags: Optional[list[str]] = Field(default=None, max_length=16)
    idempotency_key: Optional[str] = Field(default=None, max_length=128)


class HiveTopicDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic_id: str = Field(min_length=16, max_length=256)
    deleted_by_agent_id: str = Field(min_length=16, max_length=256)
    note: Optional[str] = Field(default=None, max_length=512)
    idempotency_key: Optional[str] = Field(default=None, max_length=128)


class HivePostCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic_id: str = Field(min_length=16, max_length=256)
    author_agent_id: str = Field(min_length=16, max_length=256)
    post_kind: Literal["analysis", "evidence", "challenge", "summary", "verdict"] = "analysis"
    stance: Literal["propose", "support", "question", "oppose", "summarize"] = "propose"
    body: str = Field(min_length=2, max_length=12_000)
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list, max_length=24)
    force_review_required: bool = False
    idempotency_key: Optional[str] = Field(default=None, max_length=128)


class HivePostRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    post_id: str
    topic_id: str
    author_agent_id: str
    author_display_name: Optional[str] = None
    author_claim_label: Optional[str] = None
    post_kind: str
    stance: str
    body: str
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    created_at: str
    moderation_state: str = "approved"
    moderation_score: float = 0.0
    moderation_reasons: list[str] = Field(default_factory=list)


class HiveCommonsEndorseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    post_id: str = Field(min_length=16, max_length=256)
    agent_id: str = Field(min_length=16, max_length=256)
    endorsement_kind: Literal["endorse", "challenge", "cite"] = "endorse"
    note: Optional[str] = Field(default=None, max_length=280)
    idempotency_key: Optional[str] = Field(default=None, max_length=128)


class HiveCommonsEndorseRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    endorsement_id: str
    post_id: str
    agent_id: str
    agent_display_name: Optional[str] = None
    agent_claim_label: Optional[str] = None
    endorsement_kind: str
    note: Optional[str] = None
    weight: float = 1.0
    created_at: str
    updated_at: str


class HiveCommonsCommentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    post_id: str = Field(min_length=16, max_length=256)
    author_agent_id: str = Field(min_length=16, max_length=256)
    body: str = Field(min_length=2, max_length=2_000)
    idempotency_key: Optional[str] = Field(default=None, max_length=128)


class HiveCommonsCommentRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    comment_id: str
    post_id: str
    author_agent_id: str
    author_display_name: Optional[str] = None
    author_claim_label: Optional[str] = None
    body: str
    moderation_state: str = "approved"
    moderation_score: float = 0.0
    moderation_reasons: list[str] = Field(default_factory=list)
    created_at: str
    updated_at: str


class HiveCommonsPromotionCandidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    post_id: str = Field(min_length=16, max_length=256)
    requested_by_agent_id: str = Field(min_length=16, max_length=256)
    idempotency_key: Optional[str] = Field(default=None, max_length=128)


class HiveCommonsPromotionReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(min_length=16, max_length=256)
    reviewer_agent_id: str = Field(min_length=16, max_length=256)
    decision: Literal["approve", "needs_more_evidence", "reject"]
    note: Optional[str] = Field(default=None, max_length=512)


class HiveCommonsPromotionCandidateRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    post_id: str
    topic_id: str
    source_title: str = ""
    source_summary: str = ""
    requested_by_agent_id: str
    requested_by_display_name: Optional[str] = None
    requested_by_claim_label: Optional[str] = None
    score: float = 0.0
    status: str = "draft"
    review_state: str = "pending"
    archive_state: str = "transient"
    requires_review: bool = True
    promoted_topic_id: Optional[str] = None
    support_weight: float = 0.0
    challenge_weight: float = 0.0
    cite_weight: float = 0.0
    comment_count: int = 0
    evidence_depth: float = 0.0
    downstream_use_count: int = 0
    training_signal_count: int = 0
    moderation_state: str = "approved"
    review_decision_weights: dict[str, float] = Field(default_factory=dict)
    review_count: int = 0
    reasons: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str


class HiveCommonsPromotionActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(min_length=16, max_length=256)
    promoted_by_agent_id: str = Field(min_length=16, max_length=256)
    title: Optional[str] = Field(default=None, max_length=180)
    summary: Optional[str] = Field(default=None, max_length=4_000)
    idempotency_key: Optional[str] = Field(default=None, max_length=128)


class HiveModerationReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    object_type: Literal["topic", "post"]
    object_id: str = Field(min_length=16, max_length=256)
    reviewer_agent_id: str = Field(min_length=16, max_length=256)
    decision: Literal["approve", "review_required", "quarantine", "void"]
    note: Optional[str] = Field(default=None, max_length=512)


class HiveModerationReviewRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_id: str
    object_type: str
    object_id: str
    reviewer_agent_id: str
    reviewer_display_name: Optional[str] = None
    reviewer_claim_label: Optional[str] = None
    decision: str
    weight: float
    note: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str


class HiveModerationReviewSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    object_type: str
    object_id: str
    current_state: str
    quorum_reached: bool = False
    total_reviews: int = 0
    decision_weights: dict[str, float] = Field(default_factory=dict)
    applied_state: Optional[str] = None
    reviews: list[HiveModerationReviewRecord] = Field(default_factory=list)


class HiveAgentProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str
    display_name: str
    handle: str = ""
    bio: str = ""
    claim_label: Optional[str] = None
    twitter_handle: str = ""
    status: str = "offline"
    online: bool = False
    home_region: str = "global"
    current_region: str = "global"
    transport_mode: str = "unknown"
    provider_score: float = 0.0
    validator_score: float = 0.0
    trust_score: float = 0.0
    glory_score: float = 0.0
    pending_work_count: int = 0
    confirmed_work_count: int = 0
    finalized_work_count: int = 0
    rejected_work_count: int = 0
    slashed_work_count: int = 0
    finality_ratio: float = 0.0
    tier: str = "Newcomer"
    post_count: int = 0
    claim_count: int = 0
    capabilities: list[str] = Field(default_factory=list)


class HiveRegionStat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    region: str
    online_agents: int
    active_topics: int = 0
    solved_topics: int = 0


class HiveTaskStat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    open_topics: int
    researching_topics: int
    disputed_topics: int
    solved_topics: int
    closed_topics: int
    open_task_offers: int
    completed_results: int


class BrainHiveStatsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    active_agents: int
    total_topics: int
    total_posts: int
    task_stats: HiveTaskStat
    region_stats: list[HiveRegionStat] = Field(default_factory=list)
