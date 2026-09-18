from __future__ import annotations

from core import brain_hive_review_workflow, brain_hive_write_support
from core.brain_hive_moderation import ModerationDecision
from core.scoreboard_engine import get_peer_scoreboard


class BrainHiveReviewStateMixin:
    def _forced_review_decision(self, moderation: ModerationDecision) -> ModerationDecision:
        return brain_hive_write_support.forced_review_decision(moderation)

    def _reviewer_weight(self, reviewer_agent_id: str) -> float:
        board = get_peer_scoreboard(reviewer_agent_id)
        trust = max(0.0, float(board.trust or 0.0))
        validator = max(0.0, float(board.validator or 0.0))
        return round(max(0.5, min(4.0, 1.0 + (trust * 0.25) + (validator * 0.02))), 3)

    def _current_moderation_state(self, *, object_type: str, object_id: str) -> str:
        return brain_hive_review_workflow._current_moderation_state(self, object_type=object_type, object_id=object_id)

    def _quorum_applied_state(self, decision_weights: dict[str, float]) -> str | None:
        return brain_hive_review_workflow._quorum_applied_state(decision_weights)

    def _apply_review_state(
        self,
        *,
        object_type: str,
        object_id: str,
        actor_agent_id: str,
        current_state: str,
        applied_state: str,
        decision_weights: dict[str, float],
    ) -> None:
        brain_hive_review_workflow._apply_review_state(
            self,
            object_type=object_type,
            object_id=object_id,
            actor_agent_id=actor_agent_id,
            current_state=current_state,
            applied_state=applied_state,
            decision_weights=decision_weights,
        )
