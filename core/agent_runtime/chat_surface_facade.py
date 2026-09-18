from __future__ import annotations

from typing import Any

from core.agent_runtime import chat_surface as agent_chat_surface_runtime
from core.curiosity_roamer import AdaptiveResearchResult


class ChatSurfaceFacadeMixin:
    def _chat_surface_smalltalk_model_input(self, *, user_input: str, phrase: str) -> str:
        return agent_chat_surface_runtime.smalltalk_model_input(self, user_input=user_input, phrase=phrase)

    def _chat_surface_evaluative_model_input(self, *, user_input: str) -> str:
        return agent_chat_surface_runtime.evaluative_model_input(user_input=user_input)

    def _chat_surface_observation_prompt(
        self,
        *,
        user_input: str,
        observations: dict[str, Any],
    ) -> str:
        return agent_chat_surface_runtime.observation_prompt(
            user_input=user_input,
            observations=observations,
        )

    def _chat_surface_live_info_observations(
        self,
        *,
        query: str,
        mode: str,
        notes: list[dict[str, Any]] | None = None,
        runtime_note: str = "",
    ) -> dict[str, Any]:
        return agent_chat_surface_runtime.live_info_observations(
            query=query,
            mode=mode,
            notes=notes,
            runtime_note=runtime_note,
        )

    def _chat_surface_live_info_model_input(
        self,
        *,
        user_input: str,
        query: str,
        mode: str,
        notes: list[dict[str, Any]] | None = None,
        runtime_note: str = "",
    ) -> str:
        return agent_chat_surface_runtime.live_info_model_input(
            self,
            user_input=user_input,
            query=query,
            mode=mode,
            notes=notes,
            runtime_note=runtime_note,
        )

    def _chat_surface_adaptive_research_observations(
        self,
        *,
        task_class: str,
        research_result: AdaptiveResearchResult,
    ) -> dict[str, Any]:
        return agent_chat_surface_runtime.adaptive_research_observations(
            task_class=task_class,
            research_result=research_result,
        )

    def _chat_surface_adaptive_research_model_input(
        self,
        *,
        user_input: str,
        task_class: str,
        research_result: AdaptiveResearchResult,
    ) -> str:
        return agent_chat_surface_runtime.adaptive_research_model_input(
            self,
            user_input=user_input,
            task_class=task_class,
            research_result=research_result,
        )

    def _chat_surface_credit_status_model_input(
        self,
        *,
        user_input: str,
        credit_snapshot: str,
    ) -> str:
        return agent_chat_surface_runtime.credit_status_model_input(
            user_input=user_input,
            credit_snapshot=credit_snapshot,
        )

    def _chat_surface_hive_model_input(
        self,
        *,
        user_input: str,
        observations: dict[str, Any] | None = None,
        runtime_note: str = "",
    ) -> str:
        return agent_chat_surface_runtime.hive_model_input(
            self,
            user_input=user_input,
            observations=observations,
            runtime_note=runtime_note,
        )

    def _chat_surface_hive_queue_observations(
        self,
        queue_rows: list[dict[str, Any]],
        *,
        lead: str = "",
        truth_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return agent_chat_surface_runtime.hive_queue_observations(
            self,
            queue_rows,
            lead=lead,
            truth_payload=truth_payload,
        )

    def _chat_surface_hive_research_result_observations(
        self,
        *,
        topic_id: str,
        title: str,
        result: Any,
    ) -> dict[str, Any]:
        return agent_chat_surface_runtime.hive_research_result_observations(
            self,
            topic_id=topic_id,
            title=title,
            result=result,
        )

    def _chat_surface_hive_status_observations(
        self,
        *,
        topic_id: str,
        title: str,
        status: str,
        execution_state: str,
        active_claim_count: int,
        artifact_count: int,
        post_count: int,
        latest_post_kind: str,
        latest_post_body: str,
        truth_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return agent_chat_surface_runtime.hive_status_observations(
            self,
            topic_id=topic_id,
            title=title,
            status=status,
            execution_state=execution_state,
            active_claim_count=active_claim_count,
            artifact_count=artifact_count,
            post_count=post_count,
            latest_post_kind=latest_post_kind,
            latest_post_body=latest_post_body,
            truth_payload=truth_payload,
        )

    def _chat_surface_hive_command_observations(self, details: dict[str, Any]) -> dict[str, Any]:
        return agent_chat_surface_runtime.hive_command_observations(self, details)

    def _bridge_hive_truth_from_rows(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        return agent_chat_surface_runtime.bridge_hive_truth_from_rows(rows)

    def _hive_truth_observation_fields(self, payload: dict[str, Any] | None) -> dict[str, Any]:
        return agent_chat_surface_runtime.hive_truth_observation_fields(payload)

    def _hive_truth_prefix(self, payload: dict[str, Any] | None) -> str:
        return agent_chat_surface_runtime.hive_truth_prefix(self, payload)

    def _qualify_hive_response_text(
        self,
        response_text: str,
        *,
        payload: dict[str, Any] | None,
    ) -> str:
        return agent_chat_surface_runtime.qualify_hive_response_text(
            self,
            response_text,
            payload=payload,
        )

    def _human_age(self, age_seconds: object) -> str:
        return agent_chat_surface_runtime.human_age(age_seconds)

    def _chat_surface_hive_degraded_response(self, details: dict[str, Any]) -> str:
        return agent_chat_surface_runtime.chat_surface_hive_degraded_response(self, details)

    def _chat_surface_hive_wording_result(
        self,
        *,
        session_id: str,
        user_input: str,
        source_context: dict[str, object] | None,
        response_class: Any,
        reason: str,
        observations: dict[str, Any] | None = None,
        fallback_response: str,
    ) -> dict[str, Any]:
        return agent_chat_surface_runtime.chat_surface_hive_wording_result(
            self,
            session_id=session_id,
            user_input=user_input,
            source_context=source_context,
            response_class=response_class,
            reason=reason,
            observations=observations,
            fallback_response=fallback_response,
        )

    def _postprocess_hive_chat_surface_text(
        self,
        text: str,
        *,
        response_class: Any,
        payload: dict[str, Any],
        fallback_response: str,
    ) -> str:
        return agent_chat_surface_runtime.postprocess_hive_chat_surface_text(
            self,
            text,
            response_class=response_class,
            payload=payload,
            fallback_response=fallback_response,
        )

    def _hive_task_list_mentions_real_topics(self, text: str, *, topics: list[dict[str, Any]]) -> bool:
        return agent_chat_surface_runtime.hive_task_list_mentions_real_topics(
            self,
            text,
            topics=topics,
        )

    def _chat_surface_builder_model_input(
        self,
        *,
        user_input: str,
        observations: dict[str, Any],
    ) -> str:
        return agent_chat_surface_runtime.builder_model_input(
            self,
            user_input=user_input,
            observations=observations,
        )

    def _chat_surface_model_wording_result(
        self,
        *,
        session_id: str,
        user_input: str,
        source_context: dict[str, object] | None,
        persona: Any,
        interpretation: Any,
        task_class: str,
        response_class: Any,
        reason: str,
        model_input: str,
        fallback_response: str,
        allow_provider_inference: bool = True,
        tool_backing_sources: list[str] | None = None,
        response_postprocessor: Any | None = None,
    ) -> dict[str, Any]:
        return agent_chat_surface_runtime.chat_surface_model_wording_result(
            self,
            session_id=session_id,
            user_input=user_input,
            source_context=source_context,
            persona=persona,
            interpretation=interpretation,
            task_class=task_class,
            response_class=response_class,
            reason=reason,
            model_input=model_input,
            fallback_response=fallback_response,
            allow_provider_inference=allow_provider_inference,
            tool_backing_sources=tool_backing_sources,
            response_postprocessor=response_postprocessor,
        )
