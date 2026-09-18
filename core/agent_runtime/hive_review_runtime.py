from __future__ import annotations

from typing import Any

from core.agent_runtime import hive_followups as agent_hive_followups


class HiveReviewRuntimeMixin:
    def _maybe_handle_hive_review_command(
        self,
        user_input: str,
        *,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any] | None:
        return agent_hive_followups.maybe_handle_hive_review_command(
            self,
            user_input,
            session_id=session_id,
            source_context=source_context,
        )

    def _looks_like_hive_review_queue_command(self, lowered: str) -> bool:
        return agent_hive_followups.looks_like_hive_review_queue_command(lowered)

    def _parse_hive_review_action(self, user_input: str) -> dict[str, str] | None:
        return agent_hive_followups.parse_hive_review_action(user_input)

    def _looks_like_hive_cleanup_command(self, lowered: str) -> bool:
        return agent_hive_followups.looks_like_hive_cleanup_command(lowered)

    def _handle_hive_review_queue_command(
        self,
        user_input: str,
        *,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any]:
        return agent_hive_followups.handle_hive_review_queue_command(
            self,
            user_input,
            session_id=session_id,
            source_context=source_context,
        )

    def _handle_hive_review_action(
        self,
        user_input: str,
        *,
        session_id: str,
        source_context: dict[str, object] | None,
        review_action: dict[str, str],
    ) -> dict[str, Any]:
        return agent_hive_followups.handle_hive_review_action(
            self,
            user_input,
            session_id=session_id,
            source_context=source_context,
            review_action=review_action,
        )

    def _handle_hive_cleanup_command(
        self,
        user_input: str,
        *,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any]:
        return agent_hive_followups.handle_hive_cleanup_command(
            self,
            user_input,
            session_id=session_id,
            source_context=source_context,
        )

    def _looks_like_disposable_hive_cleanup_topic(self, topic: dict[str, Any]) -> bool:
        return agent_hive_followups.looks_like_disposable_hive_cleanup_topic(topic)
