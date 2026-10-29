"""Deterministic fast path for saving a named creative set.

"save this as a set called night owl: a tired detective in a trench coat, rainy noir city" asks for the `set.save`
tool (audit N6 settings:E10): the memory lane used to answer it "Locked in. I'll remember that." and no set was kept,
so the next shot "with the night owl set" had nothing to lock. The save crosses the one permission authority like
every runtime tool call; a request with no description of its own ("save this as a set called night owl") is left to
the model, which can read what "this" points back at.
"""
from __future__ import annotations

from typing import Any


def maybe_handle_creative_set_save(
    agent: Any,
    user_input: str,
    *,
    session_id: str,
    source_context: dict[str, Any] | None,
) -> dict[str, Any] | None:
    from core.creative_set import set_save_request

    request = set_save_request(user_input)
    if request is None or not request[1]:
        return None
    name, description = request

    from core.authorized_tool_execution import authorize_runtime_tool, execute_authorized_runtime_tool
    from core.mode_permission_policy import PermissionEffect

    from core.authorized_tool_execution import permission_payload

    arguments = {"name": name, "description": description}
    decision = authorize_runtime_tool("set.save", arguments, source_context=source_context)
    if decision is None:
        return None
    if decision.effect is not PermissionEffect.ALLOW:
        # The same verdict the model route would get, presented the way the image lane presents it.
        pending = decision.effect is PermissionEffect.REQUIRE_APPROVAL
        result = agent._fast_path_result(
            session_id=session_id, user_input=user_input,
            response=(f"Saving the creative set '{name}' needs your approval first. {decision.reason} "
                      "Review the details, then allow or deny this request." if pending
                      else f"I did not save the set '{name}': {decision.reason}"),
            confidence=0.85, source_context=source_context,
            reason="creative_set_pending_approval" if pending else "creative_set_blocked_by_mode",
        )
        result["status"] = "pending_approval" if pending else "blocked_by_mode"
        result["success"] = False
        result["details"] = {"permission": permission_payload(decision)}
        approval_request = dict(decision.approval_request or {})
        if pending and approval_request:
            result["approval_request"] = approval_request
            result["task_outcome"] = "pending_approval"
            agent._emit_runtime_event(
                source_context, event_type="task_pending_approval", message=result["response"],
                tool_name="set.save", approval_request=approval_request,
            )
        return result
    execution = execute_authorized_runtime_tool(
        "set.save", arguments, source_context=source_context, permission_decision=decision,
    )
    ok = bool(getattr(execution, "ok", False))
    text = str(getattr(execution, "response_text", "") or "") if execution is not None else ""
    if ok:
        text = f"Saved the creative set '{name}'. Ask for the next shot with the {name} set and I'll keep it on-model."
    result = agent._fast_path_result(
        session_id=session_id, user_input=user_input,
        response=text or f"I couldn't save the set '{name}'.",
        confidence=0.95 if ok else 0.85, source_context=source_context,
        reason="creative_set_saved" if ok else "creative_set_save_failed",
    )
    if not ok:
        result["success"] = False
    if execution is not None:
        result["details"] = dict(getattr(execution, "details", {}) or {})
    return result


__all__ = ["maybe_handle_creative_set_save"]
