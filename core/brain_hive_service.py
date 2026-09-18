from __future__ import annotations

from typing import Any

from core import (
    brain_hive_commons_interactions,
    brain_hive_commons_promotion,
    brain_hive_commons_state,
    brain_hive_queries,
    brain_hive_review_workflow,
    brain_hive_topic_lifecycle,
    brain_hive_topic_post_frontdoor,
    brain_hive_write_support,
)
from core.brain_hive_guard import guard_post_submission
from core.brain_hive_idempotency import BrainHiveIdempotencyMixin
from core.brain_hive_identity import BrainHiveIdentityMixin
from core.brain_hive_models import (
    BrainHiveStatsResponse,
    HiveAgentProfile,
    HiveCommonsCommentRecord,
    HiveCommonsCommentRequest,
    HiveCommonsEndorseRecord,
    HiveCommonsEndorseRequest,
    HiveCommonsPromotionActionRequest,
    HiveCommonsPromotionCandidateRecord,
    HiveCommonsPromotionCandidateRequest,
    HiveCommonsPromotionReviewRequest,
    HiveModerationReviewRecord,
    HiveModerationReviewRequest,
    HiveModerationReviewSummary,
    HivePostCreateRequest,
    HivePostRecord,
    HiveRegionStat,
    HiveTopicClaimRecord,
    HiveTopicClaimRequest,
    HiveTopicCreateRequest,
    HiveTopicDeleteRequest,
    HiveTopicRecord,
    HiveTopicStatusUpdateRequest,
    HiveTopicUpdateRequest,
)
from core.brain_hive_review_state import BrainHiveReviewStateMixin
from storage.brain_hive_store import get_topic


class BrainHiveService(
    BrainHiveIdentityMixin,
    BrainHiveReviewStateMixin,
    BrainHiveIdempotencyMixin,
):
    _guard_post_submission = staticmethod(guard_post_submission)
    _post_model_cls = HivePostRecord

    def create_topic(self, request: HiveTopicCreateRequest) -> HiveTopicRecord:
        return brain_hive_topic_post_frontdoor.create_topic_record(self, request)

    def get_topic(self, topic_id: str, *, include_flagged: bool = False) -> HiveTopicRecord:
        return brain_hive_topic_post_frontdoor.get_topic_record(self, topic_id, include_flagged=include_flagged)

    def list_topics(self, *, status: str | None = None, limit: int = 100, include_flagged: bool = False) -> list[HiveTopicRecord]:
        return brain_hive_topic_post_frontdoor.list_topic_records(
            self,
            status=status,
            limit=limit,
            include_flagged=include_flagged,
        )

    def create_post(self, request: HivePostCreateRequest) -> HivePostRecord:
        return brain_hive_topic_post_frontdoor.create_post_record(self, request)

    def endorse_post(self, request: HiveCommonsEndorseRequest) -> HiveCommonsEndorseRecord:
        return brain_hive_commons_interactions.endorse_post(self, request)

    def list_post_endorsements(self, post_id: str, *, limit: int = 200) -> list[HiveCommonsEndorseRecord]:
        return brain_hive_commons_interactions.list_endorsements(self, post_id, limit=limit)

    def comment_on_post(self, request: HiveCommonsCommentRequest) -> HiveCommonsCommentRecord:
        return brain_hive_commons_interactions.comment_on_post(self, request)

    def list_post_comments(self, post_id: str, *, limit: int = 200, include_flagged: bool = False) -> list[HiveCommonsCommentRecord]:
        return brain_hive_commons_interactions.list_comments(self, post_id, limit=limit, include_flagged=include_flagged)

    def evaluate_promotion_candidate(self, request: HiveCommonsPromotionCandidateRequest) -> HiveCommonsPromotionCandidateRecord:
        return brain_hive_commons_promotion.evaluate_promotion_candidate(self, request)

    def list_commons_promotion_candidates(self, *, limit: int = 100, status: str | None = None) -> list[HiveCommonsPromotionCandidateRecord]:
        return brain_hive_commons_promotion.list_candidates(self, limit=limit, status=status)

    def review_promotion_candidate(self, request: HiveCommonsPromotionReviewRequest) -> HiveCommonsPromotionCandidateRecord:
        return brain_hive_commons_promotion.review_promotion_candidate(self, request)

    def promote_commons_candidate(self, request: HiveCommonsPromotionActionRequest) -> HiveTopicRecord:
        return brain_hive_commons_promotion.promote_commons_candidate(self, request)

    def claim_topic(self, request: HiveTopicClaimRequest) -> HiveTopicClaimRecord:
        return brain_hive_topic_lifecycle.claim_topic(self, request)

    def list_topic_claims(self, topic_id: str, *, active_only: bool = False, limit: int = 200) -> list[HiveTopicClaimRecord]:
        return brain_hive_topic_lifecycle.list_claims(self, topic_id, active_only=active_only, limit=limit)

    def list_recent_topic_claims_feed(self, *, limit: int = 50) -> list[dict[str, Any]]:
        return brain_hive_queries.list_recent_topic_claims_feed(self, limit=limit, topic_lookup=get_topic)

    def update_topic_status(self, request: HiveTopicStatusUpdateRequest) -> HiveTopicRecord:
        return brain_hive_topic_lifecycle.update_topic_status(self, request)

    def update_topic(self, request: HiveTopicUpdateRequest) -> HiveTopicRecord:
        return brain_hive_topic_lifecycle.update_topic(self, request)

    def delete_topic(self, request: HiveTopicDeleteRequest) -> HiveTopicRecord:
        return brain_hive_topic_lifecycle.delete_topic(self, request)

    def list_posts(self, topic_id: str, *, limit: int = 200, include_flagged: bool = False) -> list[HivePostRecord]:
        return brain_hive_topic_post_frontdoor.list_post_records(
            self,
            topic_id,
            limit=limit,
            include_flagged=include_flagged,
        )

    def review_object(self, request: HiveModerationReviewRequest) -> HiveModerationReviewSummary:
        return brain_hive_review_workflow.review_object(self, request)

    def get_review_summary(self, object_type: str, object_id: str) -> HiveModerationReviewSummary:
        return brain_hive_review_workflow.get_review_summary(self, object_type, object_id)

    def list_reviews(self, *, object_type: str, object_id: str, limit: int = 200) -> list[HiveModerationReviewRecord]:
        return brain_hive_review_workflow.list_reviews(self, object_type=object_type, object_id=object_id, limit=limit)

    def list_review_queue(self, *, object_type: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        return brain_hive_queries.list_review_queue(self, object_type=object_type, limit=limit)

    def get_topic_research_packet(self, topic_id: str) -> dict[str, Any]:
        return brain_hive_queries.get_topic_research_packet(self, topic_id)

    def list_research_queue(self, *, limit: int = 24) -> list[dict[str, Any]]:
        return brain_hive_queries.list_research_queue(self, limit=limit)

    def search_artifacts(self, query_text: str, *, topic_id: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        return brain_hive_queries.search_artifacts(query_text, topic_id=topic_id, limit=limit)

    def list_recent_posts_feed(self, *, limit: int = 50) -> list[dict[str, Any]]:
        return brain_hive_queries.list_recent_posts_feed(self, limit=limit, topic_lookup=get_topic)

    def list_agent_profiles(self, *, limit: int = 100, online_only: bool = False) -> list[HiveAgentProfile]:
        return brain_hive_queries.list_agent_profiles(self, limit=limit, online_only=online_only)

    def get_stats(self) -> BrainHiveStatsResponse:
        return brain_hive_queries.get_stats(self)

    def _require_commons_post(self, post_id: str) -> dict[str, Any]:
        return brain_hive_commons_state.require_commons_post(self, post_id)

    def _visibility_requires_public_guard(self, visibility: str | None) -> bool:
        return brain_hive_write_support.visibility_requires_public_guard(visibility)

    def _topic_requires_public_guard(self, topic_id: str) -> bool:
        return brain_hive_write_support.topic_requires_public_guard(topic_id)

    def _post_requires_public_guard(self, post_row: dict[str, Any]) -> bool:
        return brain_hive_write_support.post_requires_public_guard(post_row)

    def _is_commons_topic_row(self, topic: dict[str, Any]) -> bool:
        return brain_hive_commons_state.is_commons_topic_row(topic)

    def _post_commons_meta(self, post_id: str) -> dict[str, Any]:
        return brain_hive_commons_state.post_commons_meta(post_id)

    def _recompute_promotion_candidate(
        self,
        *,
        post_id: str,
        requested_by_agent_id: str,
        review_override: str | None = None,
        status_override: str | None = None,
        archive_state_override: str | None = None,
        promoted_topic_id: str | None = None,
    ) -> HiveCommonsPromotionCandidateRecord:
        return brain_hive_commons_promotion._recompute_promotion_candidate(
            self,
            post_id=post_id,
            requested_by_agent_id=requested_by_agent_id,
            review_override=review_override,
            status_override=status_override,
            archive_state_override=archive_state_override,
            promoted_topic_id=promoted_topic_id,
        )

    def _promotion_score_payload(self, post: dict[str, Any], topic: dict[str, Any]) -> dict[str, Any]:
        return brain_hive_commons_promotion._promotion_score_payload(self, post, topic)

    def _commons_downstream_signal_counts(self, post_id: str, topic_id: str) -> tuple[int, int]:
        return brain_hive_commons_state.commons_downstream_signal_counts(post_id, topic_id)

    def _candidate_review_summary(self, candidate_row: dict[str, Any] | None) -> dict[str, Any]:
        return brain_hive_commons_promotion._candidate_review_summary(candidate_row)

    def _promotion_candidate_record(self, row: dict[str, Any]) -> HiveCommonsPromotionCandidateRecord:
        return brain_hive_commons_promotion._promotion_candidate_record(self, row)

    def _promotion_candidate_record_by_id(self, candidate_id: str) -> HiveCommonsPromotionCandidateRecord:
        return brain_hive_commons_promotion._promotion_candidate_record_by_id(self, candidate_id)

    def _refresh_reviewed_candidate(self, candidate_id: str) -> HiveCommonsPromotionCandidateRecord:
        return brain_hive_commons_promotion._refresh_reviewed_candidate(self, candidate_id)

    def _promoted_topic_title(self, post: dict[str, Any], topic: HiveTopicRecord) -> str:
        return brain_hive_commons_promotion._promoted_topic_title(post, topic)

    def _promoted_topic_summary(
        self,
        post: dict[str, Any],
        topic: HiveTopicRecord,
        candidate: HiveCommonsPromotionCandidateRecord,
    ) -> str:
        return brain_hive_commons_promotion._promoted_topic_summary(post, topic, candidate)

    def _build_agent_profile(self, agent_id: str, presence_row: dict[str, Any] | None) -> HiveAgentProfile:
        return brain_hive_queries._build_agent_profile(self, agent_id, presence_row)

    def _commons_research_signal_map(self, *, limit: int) -> dict[str, dict[str, Any]]:
        return brain_hive_commons_state.commons_research_signal_map(limit=limit)

    def _region_stats(self, topic_counts: dict[str, int]) -> list[HiveRegionStat]:
        return brain_hive_queries._region_stats(self, topic_counts)

    def _topic_claim_record(self, claim_id: str) -> HiveTopicClaimRecord:
        return brain_hive_topic_lifecycle._topic_claim_record(self, claim_id)
