from __future__ import annotations

from typing import Any

from core.agent_runtime import (
    runtime_checkpoint_io_adapter,
    runtime_checkpoint_lane_policy,
    runtime_gate_policy,
)
from core.hive_activity_tracker import (
    clear_hive_interaction_state,
    session_hive_state,
    set_hive_interaction_state,
)
from core.identity_manager import load_active_persona
from core.runtime_execution_tools import execute_runtime_tool


class RuntimeCheckpointSupportMixin:
    def _model_routing_profile(
        self,
        *,
        user_input: str,
        classification: dict[str, Any],
        interpretation: Any,
        source_context: dict[str, object] | None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        return runtime_checkpoint_lane_policy.model_routing_profile(
            self,
            user_input=user_input,
            classification=classification,
            interpretation=interpretation,
            source_context=source_context,
        )

    def _explicit_runtime_workflow_request(
        self,
        *,
        user_input: str,
        task_class: str,
    ) -> bool:
        return runtime_checkpoint_lane_policy.explicit_runtime_workflow_request(
            user_input=user_input,
            task_class=task_class,
        )

    def _should_keep_ai_first_chat_lane(
        self,
        *,
        user_input: str,
        classification: dict[str, Any],
        interpretation: Any,
        source_context: dict[str, object] | None,
        checkpoint_state: dict[str, Any] | None,
    ) -> bool:
        return runtime_checkpoint_lane_policy.should_keep_ai_first_chat_lane(
            self,
            user_input=user_input,
            classification=classification,
            interpretation=interpretation,
            source_context=source_context,
            checkpoint_state=checkpoint_state,
        )

    def _prepare_runtime_checkpoint(
        self,
        *,
        session_id: str,
        raw_user_input: str,
        effective_input: str,
        source_context: dict[str, object] | None,
        allow_followup_resume: bool = True,
    ) -> dict[str, Any]:
        return runtime_checkpoint_io_adapter.prepare_runtime_checkpoint(
            self,
            session_id=session_id,
            raw_user_input=raw_user_input,
            effective_input=effective_input,
            source_context=source_context,
            allow_followup_resume=allow_followup_resume,
        )

    def _blocks_runtime_followup_resume(
        self,
        *,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> bool:
        if self._voolbook_pending.get(session_id):
            return True
        hive_state = session_hive_state(session_id)
        if self._has_pending_hive_create_confirmation(
            session_id=session_id,
            hive_state=hive_state,
            source_context=source_context,
        ):
            return True
        interaction_mode = str(hive_state.get("interaction_mode") or "").strip().lower()
        return interaction_mode in {"hive_task_active", "hive_task_selection_pending"}

    def _resolve_runtime_task(
        self,
        *,
        effective_input: str,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> Any:
        return runtime_checkpoint_io_adapter.resolve_runtime_task(
            self,
            effective_input=effective_input,
            session_id=session_id,
            source_context=source_context,
        )

    def _update_runtime_checkpoint_context(
        self,
        source_context: dict[str, object] | None,
        *,
        task_id: str | None = None,
        task_class: str | None = None,
    ) -> None:
        runtime_checkpoint_io_adapter.update_runtime_checkpoint_context(
            source_context,
            task_id=task_id,
            task_class=task_class,
        )

    def _finalize_runtime_checkpoint(
        self,
        source_context: dict[str, object] | None,
        *,
        status: str,
        final_response: str = "",
        failure_text: str = "",
        outcome: dict[str, object] | None = None,
    ) -> None:
        import logging

        from core import audit_logger
        from core.runtime_continuity import CheckpointTransitionRefused

        try:
            runtime_checkpoint_io_adapter.finalize_runtime_checkpoint(
                source_context,
                status=status,
                final_response=final_response,
                failure_text=failure_text,
                outcome=outcome,
            )
        except CheckpointTransitionRefused as exc:
            # A7 W1: the durable checkpoint already carries terminal truth that
            # this late write would have replaced. The refusal is honest and
            # auditable; the durable row stays untouched. (W2 rebinding makes
            # these lanes finalize-then-write so refusals stop occurring.)
            logging.getLogger(__name__).warning("a7 checkpoint finalization refused: %s", exc)
            audit_logger.log(
                "a7_checkpoint_transition_refused",
                target_id=str((source_context or {}).get("runtime_checkpoint_id") or ""),
                target_type="checkpoint",
                details={"error": str(exc), "attempted_status": status},
            )

    def _runtime_checkpoint_id(self, source_context: dict[str, object] | None) -> str:
        return runtime_checkpoint_io_adapter.runtime_checkpoint_id(source_context)

    def _merge_runtime_source_contexts(
        self,
        primary: dict[str, Any] | None,
        secondary: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return runtime_checkpoint_io_adapter.merge_runtime_source_contexts(self, primary, secondary)

    def _session_hive_state(self, session_id: str) -> dict[str, Any]:
        return runtime_checkpoint_io_adapter.agent_module_attr("session_hive_state", session_id)

    def _set_hive_interaction_state(self, session_id: str, *, mode: str, payload: dict[str, Any]) -> None:
        set_hive_interaction_state(session_id, mode=mode, payload=payload)

    def _clear_hive_interaction_state(self, session_id: str) -> None:
        clear_hive_interaction_state(session_id)

    def _research_topic_from_signal(self, signal: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        return runtime_checkpoint_io_adapter.agent_module_attr("research_topic_from_signal", signal, **kwargs)

    def _pick_autonomous_research_signal(self, rows: list[dict[str, Any]]) -> dict[str, Any] | None:
        return runtime_checkpoint_io_adapter.agent_module_attr("pick_autonomous_research_signal", rows)

    def _plan_tool_workflow(self, *args: Any, **kwargs: Any) -> Any:
        return runtime_checkpoint_io_adapter.agent_module_attr("plan_tool_workflow", *args, **kwargs)

    def _execute_tool_intent(self, *args: Any, **kwargs: Any) -> Any:
        return runtime_checkpoint_io_adapter.agent_module_attr("execute_tool_intent", *args, **kwargs)

    def _planned_search_query(self, *args: Any, **kwargs: Any) -> Any:
        return runtime_checkpoint_io_adapter._agent_module().WebAdapter.planned_search_query(*args, **kwargs)

    def _search_query(self, *args: Any, **kwargs: Any) -> Any:
        return runtime_checkpoint_io_adapter._agent_module().WebAdapter.search_query(*args, **kwargs)

    def _should_attempt_tool_intent(self, *args: Any, **kwargs: Any) -> Any:
        return runtime_checkpoint_io_adapter.agent_module_attr("should_attempt_tool_intent", *args, **kwargs)

    def _get_runtime_checkpoint(self, *args: Any, **kwargs: Any) -> Any:
        return runtime_checkpoint_io_adapter.agent_module_attr("get_runtime_checkpoint", *args, **kwargs)

    def _record_runtime_tool_progress(self, *args: Any, **kwargs: Any) -> Any:
        return runtime_checkpoint_io_adapter.agent_module_attr("record_runtime_tool_progress", *args, **kwargs)

    def _render_capability_truth_response(self, *args: Any, **kwargs: Any) -> Any:
        return runtime_checkpoint_io_adapter.agent_module_attr("render_capability_truth_response", *args, **kwargs)

    def _load_active_persona(self, *args: Any, **kwargs: Any) -> Any:
        return load_active_persona(*args, **kwargs)

    def _execute_runtime_tool(self, *args: Any, **kwargs: Any) -> Any:
        return execute_runtime_tool(*args, **kwargs)

    def _search_user_heuristics(self, query: str, **kwargs: Any) -> Any:
        return runtime_checkpoint_io_adapter.agent_module_attr("search_user_heuristics", query, **kwargs)

    def _looks_like_workspace_bootstrap_request(self, text: str) -> bool:
        return runtime_checkpoint_io_adapter.agent_module_attr("_looks_like_workspace_bootstrap_request", text)

    def _default_gate(self, plan: Any, classification: dict[str, Any]) -> Any:
        return runtime_gate_policy.default_gate(self, plan, classification)
