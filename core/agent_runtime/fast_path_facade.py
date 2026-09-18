from __future__ import annotations

from datetime import datetime
from typing import Any

from core.agent_runtime import fast_paths as agent_fast_paths


class FastPathFacadeMixin:
    def _smalltalk_fast_path(self, normalized_input: str, *, source_surface: str, session_id: str) -> str | None:
        return agent_fast_paths.smalltalk_fast_path(
            self,
            normalized_input,
            source_surface=source_surface,
            session_id=session_id,
        )

    def _explicit_heavy_model_block_response(self, user_input: str) -> str | None:
        return agent_fast_paths.explicit_heavy_model_block_response(user_input)

    def _evaluative_conversation_fast_path(self, normalized_input: str, *, source_surface: str) -> str | None:
        return agent_fast_paths.evaluative_conversation_fast_path(
            self,
            normalized_input,
            source_surface=source_surface,
        )

    def _looks_like_evaluative_turn(self, normalized_input: str) -> bool:
        return agent_fast_paths.looks_like_evaluative_turn(normalized_input)

    def _date_time_fast_path(
        self,
        normalized_input: str,
        *,
        source_surface: str,
        session_id: str = "",
        source_context: dict[str, object] | None = None,
    ) -> str | None:
        return agent_fast_paths.date_time_fast_path(
            self,
            normalized_input,
            source_surface=source_surface,
            session_id=session_id,
            source_context=source_context,
        )

    def _direct_math_fast_path(self, normalized_input: str, *, source_surface: str) -> str | None:
        return agent_fast_paths.direct_math_fast_path(
            normalized_input,
            source_surface=source_surface,
        )

    def _same_chat_math_recall_fast_path(
        self,
        user_input: str,
        *,
        source_surface: str,
        source_context: dict[str, object] | None,
    ) -> str | None:
        return agent_fast_paths.same_chat_math_recall_fast_path(
            user_input,
            source_surface=source_surface,
            source_context=source_context,
        )

    def _same_chat_transcript_recall_fast_path(
        self,
        user_input: str,
        *,
        source_surface: str,
        source_context: dict[str, object] | None,
    ) -> object | None:
        return agent_fast_paths.same_chat_transcript_recall_fast_path(
            user_input,
            source_surface=source_surface,
            source_context=source_context,
        )

    def _action_history_honesty_fast_path(
        self,
        user_input: str,
        *,
        source_surface: str,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> str | None:
        return agent_fast_paths.action_history_honesty_fast_path(
            user_input,
            source_surface=source_surface,
            session_id=session_id,
            source_context=source_context,
        )

    def _extract_utility_timezone(self, cleaned_input: str) -> tuple[str, str]:
        return agent_fast_paths.extract_utility_timezone(cleaned_input)

    def _utility_now_for_timezone(self, timezone_name: str) -> datetime:
        return agent_fast_paths.utility_now_for_timezone(timezone_name)

    def _recent_utility_context(
        self,
        *,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> dict[str, str]:
        return agent_fast_paths.recent_utility_context(
            session_id=session_id,
            source_context=source_context,
        )

    def _contextual_time_followup_timezone(
        self,
        cleaned_input: str,
        *,
        recent_utility_context: dict[str, str] | None,
    ) -> tuple[str, str]:
        return agent_fast_paths.contextual_time_followup_timezone(
            cleaned_input,
            recent_utility_context=recent_utility_context,
        )

    def _looks_like_malformed_time_followup(
        self,
        cleaned_input: str,
        *,
        effective_timezone: str,
        recent_utility_context: dict[str, str] | None,
    ) -> bool:
        return agent_fast_paths.looks_like_malformed_time_followup(
            cleaned_input,
            effective_timezone=effective_timezone,
            recent_utility_context=recent_utility_context,
        )

    def _ui_command_fast_path(self, normalized_input: str, *, source_surface: str) -> str | None:
        return agent_fast_paths.ui_command_fast_path(
            normalized_input,
            source_surface=source_surface,
        )

    def _looks_like_supported_machine_read_request(self, user_input: str) -> bool:
        return agent_fast_paths.looks_like_supported_machine_read_request(user_input)

    def _looks_like_safe_machine_write_request(self, user_input: str) -> bool:
        return agent_fast_paths.looks_like_safe_machine_write_request(user_input)

    def _safe_machine_write_targets_workspace(
        self,
        *,
        user_input: str,
        source_context: dict[str, object] | None,
    ) -> bool:
        return agent_fast_paths.safe_machine_write_targets_workspace(
            user_input=user_input,
            source_context=source_context,
        )

    def _startup_sequence_fast_path(self, user_input: str) -> str | None:
        return agent_fast_paths.startup_sequence_fast_path(user_input)

    def _heartbeat_poll_fast_path(
        self,
        user_input: str,
        *,
        source_context: dict[str, object] | None,
    ) -> str | None:
        return agent_fast_paths.heartbeat_poll_fast_path(
            user_input,
            source_context=source_context,
        )

    def _credit_status_fast_path(self, normalized_input: str, *, source_surface: str) -> str | None:
        return agent_fast_paths.credit_status_fast_path(
            self,
            normalized_input,
            source_surface=source_surface,
        )

    def _maybe_handle_companion_memory_fast_path(
        self,
        user_input: str,
        *,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any] | None:
        return agent_fast_paths.maybe_handle_companion_memory_fast_path(
            self,
            user_input,
            session_id=session_id,
            source_context=source_context,
        )

    def _maybe_handle_web0_builder_fast_path(
        self,
        user_input: str,
        *,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any] | None:
        return agent_fast_paths.maybe_handle_web0_builder_fast_path(
            self,
            user_input,
            session_id=session_id,
            source_context=source_context,
        )

    @staticmethod
    def _looks_like_companion_continuation_request(lowered: str) -> bool:
        return agent_fast_paths.looks_like_companion_continuation_request(lowered)

    @staticmethod
    def _looks_like_personalized_plan_request(lowered: str) -> bool:
        return agent_fast_paths.looks_like_personalized_plan_request(lowered)

    def _render_companion_continuation_response(
        self,
        *,
        session_id: str,
        query_text: str,
        profile: dict[str, Any],
    ) -> str:
        from core.context_scope import ContextAccessPolicy

        return agent_fast_paths.render_companion_continuation_response(
            session_id=session_id,
            query_text=query_text,
            profile=profile,
            access_policy=ContextAccessPolicy.for_request(
                session_id=session_id,
                source_context={"surface": "local"},
            ),
        )

    def _render_personalized_plan_response(
        self,
        *,
        session_id: str,
        query_text: str,
        profile: dict[str, Any],
    ) -> str:
        from core.context_scope import ContextAccessPolicy

        return agent_fast_paths.render_personalized_plan_response(
            query_text=query_text,
            profile=profile,
            access_policy=ContextAccessPolicy.for_request(
                session_id=session_id,
                source_context={"surface": "local"},
            ),
        )

    @staticmethod
    def _dense_memory_next_step(*, project_label: str, summary_text: str, preferred_stack: str) -> str:
        return agent_fast_paths.dense_memory_next_step(
            project_label=project_label,
            summary_text=summary_text,
            preferred_stack=preferred_stack,
        )

    def _maybe_handle_live_info_fast_path(
        self,
        user_input: str,
        *,
        session_id: str,
        source_context: dict[str, object] | None,
        interpretation: Any,
    ) -> dict[str, Any] | None:
        return agent_fast_paths.maybe_handle_live_info_fast_path(
            self,
            user_input,
            session_id=session_id,
            source_context=source_context,
            interpretation=interpretation,
            response_class=self.ResponseClass.UTILITY_ANSWER,
        )

    def _maybe_handle_safe_machine_write_guard(
        self,
        user_input: str,
        *,
        session_id: str,
        source_surface: str,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any] | None:
        return agent_fast_paths.maybe_handle_safe_machine_write_guard(
            self,
            user_input,
            session_id=session_id,
            source_surface=source_surface,
            source_context=source_context,
        )

    def _maybe_handle_direct_machine_read_request(
        self,
        user_input: str,
        *,
        session_id: str,
        source_surface: str,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any] | None:
        return agent_fast_paths.maybe_handle_direct_machine_read_request(
            self,
            user_input,
            session_id=session_id,
            source_surface=source_surface,
            source_context=source_context,
        )

    def _maybe_handle_direct_machine_write_request(
        self,
        user_input: str,
        *,
        session_id: str,
        source_surface: str,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any] | None:
        return agent_fast_paths.maybe_handle_direct_machine_write_request(
            self,
            user_input,
            session_id=session_id,
            source_surface=source_surface,
            source_context=source_context,
        )

    def _maybe_handle_direct_machine_download_request(
        self,
        user_input: str,
        *,
        session_id: str,
        source_surface: str,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any] | None:
        return agent_fast_paths.maybe_handle_direct_machine_download_request(
            self,
            user_input,
            session_id=session_id,
            source_surface=source_surface,
            source_context=source_context,
        )

    def _maybe_handle_direct_workspace_runtime_request(
        self,
        user_input: str,
        *,
        session_id: str,
        source_surface: str,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any] | None:
        return agent_fast_paths.maybe_handle_direct_workspace_runtime_request(
            self,
            user_input,
            session_id=session_id,
            source_surface=source_surface,
            source_context=source_context,
        )

    def _maybe_handle_workspace_identity_request(
        self,
        user_input: str,
        *,
        session_id: str,
        source_surface: str,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any] | None:
        return agent_fast_paths.maybe_handle_workspace_identity_request(
            self,
            user_input,
            session_id=session_id,
            source_surface=source_surface,
            source_context=source_context,
        )

    def _maybe_handle_folder_overview_request(
        self,
        user_input: str,
        *,
        session_id: str,
        source_surface: str,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any] | None:
        return agent_fast_paths.maybe_handle_folder_overview_request(
            self,
            user_input,
            session_id=session_id,
            source_surface=source_surface,
            source_context=source_context,
        )

    def _maybe_handle_workspace_audit_request(
        self,
        user_input: str,
        *,
        session_id: str,
        source_surface: str,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any] | None:
        from core.agent_runtime.workspace_audit import maybe_handle_workspace_audit_request

        return maybe_handle_workspace_audit_request(
            self,
            user_input,
            session_id=session_id,
            source_surface=source_surface,
            source_context=source_context,
        )

    def _live_info_search_notes(
        self,
        *,
        query: str,
        live_mode: str,
        interpretation: Any,
    ) -> list[dict[str, Any]]:
        return agent_fast_paths.live_info_search_notes(
            self,
            query=query,
            live_mode=live_mode,
            interpretation=interpretation,
        )

    @staticmethod
    def _try_live_quote_note(query: str) -> dict[str, Any] | None:
        return agent_fast_paths.try_live_quote_note(query)

    @staticmethod
    def _try_live_quote_notes(query: str) -> list[dict[str, Any]]:
        return agent_fast_paths.try_live_quote_notes(query)

    def _live_info_mode(self, text: str, *, interpretation: Any) -> str:
        return agent_fast_paths.live_info_mode(
            self,
            text,
            interpretation=interpretation,
        )

    def _requires_ultra_fresh_insufficient_evidence(self, text: str) -> bool:
        return agent_fast_paths.requires_ultra_fresh_insufficient_evidence(text)

    def _ultra_fresh_insufficient_evidence_response(self, *, query: str) -> str:
        return agent_fast_paths.ultra_fresh_insufficient_evidence_response(query=query)

    def _looks_like_builder_request(self, lowered: str) -> bool:
        return agent_fast_paths.looks_like_builder_request(lowered)

    def _looks_like_generic_workspace_bootstrap_request(self, lowered: str) -> bool:
        return agent_fast_paths.looks_like_generic_workspace_bootstrap_request(
            self,
            lowered,
        )

    def _looks_like_explicit_workspace_file_request(self, query_text: str) -> bool:
        return agent_fast_paths.looks_like_explicit_workspace_file_request(query_text)

    def _looks_like_exact_workspace_readback_request(self, query_text: str) -> bool:
        return agent_fast_paths.looks_like_exact_workspace_readback_request(query_text)

    def _extract_requested_builder_root(self, query_text: str, *, workspace_root: str = "") -> str:
        return agent_fast_paths.extract_requested_builder_root(query_text, workspace_root=workspace_root)

    def _recover_price_lookup_query(
        self,
        text: str,
        *,
        source_context: dict[str, Any] | None,
    ) -> str:
        return agent_fast_paths.recover_price_lookup_query(
            text,
            source_context=source_context,
        )

    def _normalize_live_info_query(self, text: str, *, mode: str) -> str:
        return agent_fast_paths.normalize_live_info_query(
            text,
            mode=mode,
        )

    def _render_live_info_response(self, *, query: str, notes: list[dict[str, Any]], mode: str) -> str:
        return agent_fast_paths.render_live_info_response(
            query=query,
            notes=notes,
            mode=mode,
        )

    def _first_live_quote(self, notes: list[dict[str, Any]]) -> Any | None:
        return agent_fast_paths.first_live_quote(notes)

    def _render_weather_response(self, *, query: str, notes: list[dict[str, Any]]) -> str:
        return agent_fast_paths.render_weather_response(
            query=query,
            notes=notes,
        )

    def _render_news_response(self, *, query: str, notes: list[dict[str, Any]]) -> str:
        return agent_fast_paths.render_news_response(
            query=query,
            notes=notes,
        )

    def _unresolved_price_lookup_response(self, *, query: str, notes: list[dict[str, Any]], mode: str) -> str:
        return agent_fast_paths.unresolved_price_lookup_response(
            query=query,
            notes=notes,
            mode=mode,
        )

    def _looks_like_grounded_price_lookup(self, query: str) -> bool:
        return agent_fast_paths.looks_like_grounded_price_lookup(query)

    def _notes_include_grounded_price_signal(self, notes: list[dict[str, Any]]) -> bool:
        return agent_fast_paths.notes_include_grounded_price_signal(notes)

    def _extract_price_lookup_subject(self, query: str) -> str:
        return agent_fast_paths.extract_price_lookup_subject(query)

    def _live_info_failure_text(self, *, query: str, mode: str) -> str:
        return agent_fast_paths.live_info_failure_text(
            query=query,
            mode=mode,
        )
