from __future__ import annotations

from importlib import import_module
from typing import Any

from core.agent_runtime import orchestrator as agent_orchestrator_runtime


def _agent_module() -> Any:
    return import_module("core.agent_runtime.agent")


class ToolResultWorkflowSurfaceMixin:
    def _task_workflow_summary(
        self,
        *,
        classification: dict[str, Any],
        context_result: Any,
        model_execution: dict[str, Any],
        media_analysis: dict[str, Any],
        curiosity_result: dict[str, Any],
        gate_mode: str,
    ) -> str:
        return agent_orchestrator_runtime.task_workflow_summary(
            classification=classification,
            context_result=context_result,
            model_execution=model_execution,
            media_analysis=media_analysis,
            curiosity_result=curiosity_result,
            gate_mode=gate_mode,
        )

    def _action_workflow_summary(
        self,
        *,
        operator_kind: str,
        dispatch_status: str,
        details: dict[str, Any] | None,
    ) -> str:
        return agent_orchestrator_runtime.action_workflow_summary(
            operator_kind=operator_kind,
            dispatch_status=dispatch_status,
            details=details,
        )

    def _tool_intent_workflow_summary(
        self,
        *,
        tool_name: str,
        dispatch_status: str,
        provider_id: str | None,
        validation_state: str,
    ) -> str:
        lines = [
            f"- model selected tool intent `{tool_name or 'unknown'}`",
            f"- tool state: `{dispatch_status}`",
        ]
        provider = str(provider_id or "").strip()
        if provider:
            lines.append(f"- tool intent provider: `{provider}`")
        validation = str(validation_state or "").strip()
        if validation:
            lines.append(f"- tool intent validation: `{validation}`")
        return "\n".join(lines)

    def _tool_loop_final_message(self, synthesis: Any, executed_steps: list[dict[str, Any]]) -> str:
        return agent_orchestrator_runtime.tool_loop_final_message(synthesis, executed_steps)

    def _render_tool_loop_response(
        self,
        *,
        final_message: str,
        executed_steps: list[dict[str, Any]],
        include_step_summary: bool = True,
    ) -> str:
        return agent_orchestrator_runtime.render_tool_loop_response(
            final_message=final_message,
            executed_steps=executed_steps,
            include_step_summary=include_step_summary,
        )

    def _tool_intent_loop_workflow_summary(
        self,
        *,
        executed_steps: list[dict[str, Any]],
        provider_id: str | None,
        validation_state: str,
    ) -> str:
        return agent_orchestrator_runtime.tool_intent_loop_workflow_summary(
            executed_steps=executed_steps,
            provider_id=provider_id,
            validation_state=validation_state,
        )

    def _tool_step_summary(self, response_text: str, *, fallback: str) -> str:
        return agent_orchestrator_runtime.tool_step_summary(response_text, fallback=fallback)

    def _runtime_preview(self, text: str, *, limit: int = 220) -> str:
        return agent_orchestrator_runtime.runtime_preview(text, limit=limit)

    def _emit_runtime_event(
        self,
        source_context: dict[str, Any] | None,
        *,
        event_type: str,
        message: str,
        **details: Any,
    ) -> dict[str, Any] | None:
        return agent_orchestrator_runtime.emit_runtime_event(
            self,
            source_context,
            event_type=event_type,
            message=message,
            emit_runtime_event_fn=_agent_module().emit_runtime_event,
            **details,
        )

    def _live_runtime_stream_enabled(self, source_context: dict[str, Any] | None) -> bool:
        return agent_orchestrator_runtime.live_runtime_stream_enabled(source_context)
