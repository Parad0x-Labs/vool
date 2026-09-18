from __future__ import annotations

from typing import Any

from core import audit_logger, policy_engine
from core.agent_runtime import fast_command_surface as agent_fast_command_surface
from core.agent_runtime import response_policy as agent_response_policy
from core.tool_intent_executor import (
    capability_truth_for_request,
    render_capability_truth_response,
    runtime_capability_ledger,
    supported_public_capability_tags,
)
from core.user_preferences import load_preferences


class PublicHiveSupportMixin:
    def _maybe_handle_capability_truth_request(
        self,
        user_input: str,
        *,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any] | None:
        return agent_fast_command_surface.maybe_handle_capability_truth_request(
            self,
            user_input,
            session_id=session_id,
            source_context=source_context,
            capability_truth_for_request_fn=capability_truth_for_request,
            render_capability_truth_response_fn=render_capability_truth_response,
        )

    def _maybe_handle_mission_render_request(
        self,
        user_input: str,
        *,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any] | None:
        from core.active_mission import current_active_mission_slots, render_mission_answer

        return agent_fast_command_surface.maybe_handle_mission_render_request(
            self,
            user_input,
            session_id=session_id,
            source_context=source_context,
            current_active_mission_slots_fn=current_active_mission_slots,
            render_mission_answer_fn=render_mission_answer,
        )

    def _help_capabilities_text(self) -> str:
        return agent_fast_command_surface.help_capabilities_text(self)

    def _should_attach_hive_footer(
        self,
        result: Any,
        *,
        source_context: dict[str, object] | None,
    ) -> bool:
        return agent_response_policy.should_attach_hive_footer(
            self,
            result,
            source_context=source_context,
        )

    def _public_transport_source(self, source_context: dict[str, object] | None) -> dict[str, object]:
        if source_context:
            return dict(source_context)
        with self._public_presence_lock:
            return dict(self._public_presence_source_context or {})

    def _maybe_publish_public_task(
        self,
        *,
        task: Any,
        classification: dict[str, Any],
        assistant_response: str,
        session_id: str,
    ) -> dict[str, Any] | None:
        if str(getattr(task, "share_scope", "local_only") or "local_only") != "public_knowledge":
            return None
        try:
            result = self.public_hive_bridge.publish_public_task(
                task_id=str(getattr(task, "task_id", "") or ""),
                task_summary=str(getattr(task, "task_summary", "") or ""),
                task_class=str(classification.get("task_class") or "unknown"),
                assistant_response=assistant_response,
                topic_tags=[str(tag) for tag in list(classification.get("topic_hints") or [])[:6]],
            )
            audit_logger.log(
                "public_hive_task_export",
                target_id=str(getattr(task, "task_id", "") or ""),
                target_type="task",
                details={
                    "share_scope": getattr(task, "share_scope", "local_only"),
                    "session_id": session_id,
                    **dict(result or {}),
                },
            )
            return dict(result or {})
        except Exception as exc:
            audit_logger.log(
                "public_hive_task_export_error",
                target_id=str(getattr(task, "task_id", "") or ""),
                target_type="task",
                details={
                    "error": str(exc),
                    "share_scope": getattr(task, "share_scope", "local_only"),
                    "session_id": session_id,
                },
            )
        return None

    def _maybe_hive_footer(
        self,
        *,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> str:
        surface = str((source_context or {}).get("surface", "") or "").lower()
        if surface not in {"channel", "openclaw", "api"}:
            return ""
        prefs = load_preferences()
        try:
            return self.hive_activity_tracker.build_chat_footer(
                session_id=session_id,
                hive_followups_enabled=bool(getattr(prefs, "hive_followups", True)),
                idle_research_assist=bool(getattr(prefs, "idle_research_assist", True)),
            )
        except Exception as exc:
            audit_logger.log(
                "hive_activity_footer_error",
                target_id=session_id,
                target_type="session",
                details={"error": str(exc)},
            )
            return ""

    def _append_footer(self, response: str, *, prefix: str, footer: str) -> str:
        return agent_response_policy.append_footer(response, prefix=prefix, footer=footer)

    def _public_capabilities(self) -> list[str]:
        capabilities = [
            "persistent_memory",
            "chat_continuity",
            *supported_public_capability_tags(limit=12),
        ]
        build_entry = self._workspace_build_capability_entry()
        if build_entry.get("supported"):
            capabilities.append(str(build_entry.get("capability_id") or "workspace.build_scaffold"))
        seen: set[str] = set()
        out: list[str] = []
        for item in capabilities:
            if item in seen:
                continue
            seen.add(item)
            out.append(item[:64])
            if len(out) >= 16:
                break
        return out

    def _capability_ledger_entries(self) -> list[dict[str, Any]]:
        entries = [dict(entry) for entry in runtime_capability_ledger()]
        entries.append(self._workspace_build_capability_entry())
        return entries

    def _workspace_build_capability_entry(self) -> dict[str, Any]:
        write_enabled = bool(policy_engine.get("filesystem.allow_write_workspace", False))
        sandbox_enabled = bool(policy_engine.get("execution.allow_sandbox_execution", False))
        verification_note = (
            "bounded verification can run through local commands"
            if sandbox_enabled
            else "verification is limited because sandbox execution is disabled"
        )
        return {
            "capability_id": "workspace.build_scaffold",
            "surface": "workspace",
            "supported": write_enabled,
            "support_level": "partial" if write_enabled else "unsupported",
            "claim": (
                "run bounded local build/edit/run/inspect loops in the active workspace, including starter folders/files and narrow Telegram or Discord bot scaffolds; "
                f"{verification_note}"
            ),
            "partial_reason": (
                "This is still a bounded local builder controller, not a full autonomous research -> build -> debug -> test loop for arbitrary software."
                if write_enabled
                else ""
            ),
            "unsupported_reason": "Workspace scaffold generation is disabled because workspace writes are not enabled on this runtime.",
            "nearby_capability_ids": ["workspace.write", "sandbox.command"],
            "public_tag": "workspace.build_scaffold",
        }

    def _public_transport_mode(self, source_context: dict[str, object] | None) -> str:
        resolved_context = self._public_transport_source(source_context)
        surface = str((resolved_context or {}).get("surface") or "").strip().lower()
        platform = str((resolved_context or {}).get("platform") or "").strip().lower()
        if surface and platform:
            return f"{surface}_{platform}"[:64]
        if surface:
            return surface[:64]
        if platform:
            return platform[:64]
        return "vool_agent"
