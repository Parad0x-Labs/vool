from __future__ import annotations

from typing import Any

from core.agent_runtime import fast_command_surface as agent_fast_command_surface
from core.agent_runtime import response as agent_response_runtime
from core.agent_runtime import response_policy as agent_response_policy


class ToolResultTextSurfaceMixin:
    def _render_credit_status(self, normalized_input: str) -> str:
        return agent_fast_command_surface.render_credit_status(normalized_input)

    def _maybe_attach_workflow(
        self,
        response: str,
        workflow_summary: str,
        *,
        source_context: dict[str, object] | None = None,
    ) -> str:
        return agent_response_policy.maybe_attach_workflow(
            self,
            response,
            workflow_summary,
            source_context=source_context,
        )

    def _turn_result(
        self,
        text: str,
        response_class: Any,
        *,
        workflow_summary: str = "",
        debug_origin: str | None = None,
        allow_planner_style: bool = False,
    ) -> Any:
        return agent_response_runtime.turn_result(
            type(self).ChatTurnResult,
            text,
            response_class,
            workflow_summary=workflow_summary,
            debug_origin=debug_origin,
            allow_planner_style=allow_planner_style,
        )

    def _decorate_chat_response(
        self,
        response: Any,
        *,
        session_id: str,
        source_context: dict[str, object] | None,
        workflow_summary: str = "",
        include_hive_footer: bool | None = None,
    ) -> str:
        return agent_response_runtime.decorate_chat_response(
            self,
            response,
            session_id=session_id,
            source_context=source_context,
            workflow_summary=workflow_summary,
            include_hive_footer=include_hive_footer,
        )

    def _shape_user_facing_text(self, result: Any) -> str:
        return agent_response_runtime.shape_user_facing_text(self, result)

    def _should_show_workflow_for_result(
        self,
        result: Any,
        *,
        source_context: dict[str, object] | None,
    ) -> bool:
        return agent_response_runtime.should_show_workflow_for_result(
            self,
            result,
            source_context=source_context,
        )

    def _sanitize_user_chat_text(
        self,
        text: str,
        *,
        response_class: Any,
        allow_planner_style: bool = False,
    ) -> str:
        return agent_response_runtime.sanitize_user_chat_text(
            self,
            text,
            response_class=response_class,
            allow_planner_style=allow_planner_style,
        )

    def _strip_runtime_preamble(self, text: str, *, allow_planner_style: bool = False) -> str:
        return agent_response_runtime.strip_runtime_preamble(text, allow_planner_style=allow_planner_style)

    def _strip_planner_leakage(self, text: str) -> str:
        return agent_response_runtime.strip_planner_leakage(self, text)

    def _contains_generic_planner_scaffold(self, text: str) -> bool:
        return agent_response_runtime.contains_generic_planner_scaffold(self, text)

    def _unwrap_summary_or_action_payload(self, text: str) -> str:
        return agent_response_runtime.unwrap_summary_or_action_payload(text)

    def _fast_path_response_class(
        self,
        *,
        reason: str,
        response: str,
        details: dict[str, Any] | None = None,
    ) -> Any:
        return agent_response_policy.fast_path_response_class(self, reason=reason, response=response, details=details)

    def _classify_hive_text_response(self, response: str) -> Any:
        return agent_response_policy.classify_hive_text_response(self, response)

    def _classify_hive_command_details(
        self,
        details: dict[str, Any] | None,
        *,
        response: str = "",
    ) -> Any:
        return agent_response_policy.classify_hive_command_details(self, details, response=response)

    def _action_response_class(
        self,
        *,
        reason: str,
        success: bool,
        task_outcome: str | None,
        response: str,
    ) -> Any:
        return agent_response_policy.action_response_class(
            self,
            reason=reason,
            success=success,
            task_outcome=task_outcome,
            response=response,
        )

    def _grounded_response_class(self, *, gate: Any, classification: dict[str, Any]) -> Any:
        return agent_response_policy.grounded_response_class(self, gate=gate)

    def _should_show_workflow_summary(
        self,
        *,
        response: str,
        workflow_summary: str,
        source_context: dict[str, object] | None,
    ) -> bool:
        return agent_response_policy.should_show_workflow_summary(
            response=response,
            workflow_summary=workflow_summary,
            source_context=source_context,
        )

    def _tool_intent_direct_message(self, structured_output: Any) -> str | None:
        return agent_response_policy.tool_intent_direct_message(structured_output)
