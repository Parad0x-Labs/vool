from __future__ import annotations

from typing import Any

from core.user_preferences import load_preferences

_CHAT_WORKFLOW_SURFACES = {"channel", "openclaw", "api"}


def workflow_debug_requested(source_context: dict[str, object] | None) -> bool:
    payload = dict(source_context or {})
    surface = str(payload.get("surface") or "").strip().lower()
    if surface in {"trace", "trace_rail"}:
        return True
    return any(
        bool(payload.get(key))
        for key in (
            "workflow_debug",
            "show_workflow_debug",
            "show_internal_workflow",
            "debug_workflow",
        )
    )


def maybe_attach_workflow(
    agent: Any,
    response: str,
    workflow_summary: str,
    *,
    source_context: dict[str, object] | None = None,
) -> str:
    prefs = load_preferences()
    if not getattr(prefs, "show_workflow", False):
        return str(response or "")
    surface = str((source_context or {}).get("surface", "") or "").strip().lower()
    if surface in _CHAT_WORKFLOW_SURFACES and not workflow_debug_requested(source_context):
        return str(response or "")
    summary = str(workflow_summary or "").strip()
    if not summary:
        return str(response or "")
    if not should_show_workflow_summary(
        response=response,
        workflow_summary=summary,
        source_context=source_context,
    ):
        return str(response or "")
    return f"Workflow:\n{summary}\n\n{str(response or '').strip()}".strip()


def should_attach_hive_footer(
    agent: Any,
    result: Any,
    *,
    source_context: dict[str, object] | None,
) -> bool:
    surface = str((source_context or {}).get("surface", "") or "").strip().lower()
    if surface not in _CHAT_WORKFLOW_SURFACES:
        return False
    if result.response_class not in {
        agent.ResponseClass.TASK_SELECTION_CLARIFICATION,
        agent.ResponseClass.APPROVAL_REQUIRED,
    }:
        return False
    lowered = str(result.text or "").strip().lower()
    if "ready to post this to the public hive" in lowered or "confirm? (yes / no)" in lowered:
        return False
    return not ("reply with:" in lowered and "approve " in lowered)


def should_show_workflow_summary(
    *,
    response: str,
    workflow_summary: str,
    source_context: dict[str, object] | None,
) -> bool:
    surface = str((source_context or {}).get("surface", "") or "").strip().lower()
    response_text = str(response or "").strip()
    if surface not in _CHAT_WORKFLOW_SURFACES:
        return True
    if workflow_debug_requested(source_context):
        return True
    if "recognized operator action" in workflow_summary:
        return True
    if "classified task as `research`" in workflow_summary:
        return True
    if "classified task as `integration_orchestration`" in workflow_summary:
        return True
    if "classified task as `system_design`" in workflow_summary:
        return True
    if "classified task as `debugging`" in workflow_summary:
        return True
    if "classified task as `code_" in workflow_summary:
        return True
    if "curiosity/research lane: `executed`" in workflow_summary:
        return True
    if "execution posture: `tool_" in workflow_summary:
        return True
    return len(response_text) >= 280


def append_footer(response: str, *, prefix: str, footer: str) -> str:
    clean_response = str(response or "").strip()
    clean_footer = str(footer or "").strip()
    if not clean_footer:
        return clean_response
    if clean_footer.lower().startswith(f"{str(prefix or '').strip().lower()}:"):
        return f"{clean_response}\n\n{clean_footer}".strip()
    return f"{clean_response}\n\n{prefix}:\n{clean_footer}".strip()
