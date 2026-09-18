"""THE one authorized execution boundary for runtime tools.

Every runtime-tool execution -- model tool-intent dispatch AND deterministic fast path -- crosses
THIS function. It closes the measured split in which the two routes answered to different
authorities: the model loop asked ``decide_tool_call`` before dispatching, while the machine, PDF,
skill, media and currency fast paths called ``execute_runtime_tool`` directly, below the permission
controller, so the same user request was writable in one route and prompt-gated in the other
depending only on which router claimed the turn.

The contract, in order:

1. A cancelled turn executes nothing. The same typed check the model loop applies runs here, so a
   stop press reaches a fast-path dispatch too.
2. An EXPLICIT permission decision stands behind every execution. It arrives either as a held,
   typed ``PermissionDecision`` (the model loop already decided at its controller seam; handing the
   decision object here is what prevents a second prompt and a second receipt) or is taken here
   from the same ``decide_tool_call`` the model route uses. No decision is a denial.
3. DENY and REQUIRE_APPROVAL never reach a handler. Both return a typed refusal carrying the
   authority's own reason; REQUIRE_APPROVAL carries the approval request so a UI can render it.
4. Only ALLOW crosses ``execute_runtime_tool`` -- which stays the ONE effect lifecycle (reservation
   scope, execution, outcome, receipts, Activity). This module adds a decision, never a second one.
   An ALLOW that rests on an approved code proposal (a verified full-file replace) carries the
   reviewed destination that approval binds to the writer, and is refused before execution when no
   approval binds the write any more.
5. The decision is recorded on the result (``details["permission"]``), so a receipt or Activity row
   can always say WHICH authority allowed or refused the call.

Internal deterministic callers (background jobs, scheduled work) do not get authority from calling
from a trusted-looking place: they mint a narrow, bounded, binding-carrying scope with
``mode_permission_policy.grant_internal_authority`` and put its token in the consulted context
(``internal_authority_token``) or pass it here explicitly. The token is validated by the permission
authority itself -- unknown, expired, mistargeted or ceiling-exceeding tokens decide as if absent,
which fails closed to the ordinary mode matrix.

Lanes whose effect is not a runtime-tool dispatch (media local render, currency live-rate
retrieval) take their decision from ``authorize_runtime_tool`` -- the same authority consult, in
this one module -- and gate their own effect on it.
"""
from __future__ import annotations

from typing import Any

from core.mode_permission_policy import (
    PermissionDecision,
    PermissionEffect,
    decide_tool_call,
)

# The permission-verdict statuses this boundary produces for a call it did not execute. Refusal is
# typed so a lane can branch on the authority's verdict instead of string-matching prose. A write
# allowed under an approval that no longer binds it by execution time is refused with the status the
# code-task runtime and the writer give that condition (`destination_changed`, `approval_mismatch`,
# ...), which lanes handle as they handle the writer's own refusal of it, not as a verdict to render
# as a permission card (`_pin_approved_replacement`).
REFUSAL_STATUSES: frozenset[str] = frozenset({"blocked_by_mode", "pending_approval", "cancelled"})

_BOUNDARY_SURFACE = "authorized_execution_boundary"


def _runtime_execution_tools():
    from core import runtime_execution_tools

    return runtime_execution_tools


def permission_payload(decision: PermissionDecision | None) -> dict[str, Any]:
    """The typed record of the decision that stood behind ( or refused ) a call."""
    if decision is None:
        return {"effect": "none", "mode": "", "actions": [], "reason": "no decision was taken"}
    return {
        "effect": decision.effect.value,
        "mode": decision.mode.value,
        "actions": [action.value for action in decision.actions],
        "reason": str(decision.reason or ""),
        "route": _BOUNDARY_SURFACE,
    }


def authorize_runtime_tool(
    intent: str,
    arguments: dict[str, Any] | None = None,
    *,
    task_id: str = "",
    source_context: dict[str, Any] | None = None,
    authority_token: str | None = None,
) -> PermissionDecision | None:
    """Take the permission decision for one runtime-tool call from THE authority.

    One consult, the same ``decide_tool_call`` the model tool-intent route runs. An explicit
    ``authority_token`` (a scope minted by ``grant_internal_authority``) is placed into the
    CONSULTED copy of the context -- never into the caller's dict -- and is validated there:
    unknown/expired/mistargeted tokens are absent, which decides by the ordinary mode matrix.
    ``None`` means no decision could be taken; callers must treat it as a denial.
    """
    consulted = dict(source_context or {})
    token = str(authority_token or "").strip()
    if token:
        consulted["internal_authority_token"] = token
    return decide_tool_call(
        intent=str(intent or ""),
        arguments=dict(arguments or {}),
        task_id=str(task_id or ""),
        source_context=consulted,
    )


def _refusal_result(
    intent: str,
    decision: PermissionDecision | None,
    *,
    status: str,
    response_text: str,
    approval_request: dict[str, Any] | None = None,
    cancelled: bool = False,
) -> Any:
    from core.runtime_execution_tools import RuntimeExecutionResult

    runtime = _runtime_execution_tools()
    details: dict[str, Any] = {
        "executed": False,
        "permission": permission_payload(decision),
        "observation": runtime._tool_observation(
            intent=intent,
            tool_surface=_BOUNDARY_SURFACE,
            ok=False,
            status=status,
            operating_mode=(decision.mode.value if decision is not None else ""),
        ),
    }
    if approval_request is not None:
        details["approval_request"] = approval_request
    if cancelled:
        details["cancelled"] = True
    return RuntimeExecutionResult(
        handled=True,
        ok=False,
        status=status,
        response_text=response_text,
        details=details,
    )


def _pin_approved_replacement(
    intent: str, arguments: dict[str, Any], decision: PermissionDecision, context: dict[str, Any]
) -> Any:
    """A full-file write over an existing file is allowed as a verified replace
    (`PermissionAction.MODIFY_FILES`) only because an approved code proposal binds its recorded
    destination. The writer must hold that record -- it re-checks the destination after its own
    resolution and the reviewed bytes inside the flight recorder, and writes inside the pinned
    reviewed directory -- or the decision would cover the reviewed file while the write lands
    wherever the path points by then. Attaches the bound record to the consulted context. When no
    approval binds the write any more (the path resolves elsewhere, or the approval was used,
    cancelled or expired after the decision), refuses before execution with the status the task
    runtime gives that condition. The live bytes are left to the writer, as at a task step's
    admission, so a stale base is refused inside the flight recorder and journaled. None for every
    other call."""
    from core.mode_permission_policy import PermissionAction

    if intent not in {"workspace.write_file", "machine.write_file"}:
        return None
    if PermissionAction.MODIFY_FILES not in tuple(decision.actions or ()):
        return None
    from core.code_assistant.task_runtime import approved_replacement_destination
    from core.runtime_execution_tools import APPROVED_DESTINATIONS_CONTEXT_KEY

    path = str(arguments.get("path") or "")
    record, reason = approved_replacement_destination(
        context,
        intent=intent,
        path=path,
        content=str(arguments.get("content") or ""),
        expected_hash=str(arguments.get("expected_hash") or ""),
        reviewed_bytes=False,
    )
    if record is None:
        return _refusal_result(
            intent,
            decision,
            status=reason,
            response_text=(
                f"`{intent}` was not executed: it was allowed as the replacement of `{path}` that an approved "
                f"code proposal binds, and no approval binds it any more ({reason.replace('_', ' ')}). Nothing "
                "was written; re-propose against the file as it is now."
            ),
        )
    context[APPROVED_DESTINATIONS_CONTEXT_KEY] = [record]
    return None


def execute_authorized_runtime_tool(
    intent: str,
    arguments: dict[str, Any] | None = None,
    *,
    task_id: str = "",
    source_context: dict[str, Any] | None = None,
    trusted_local_only: bool = False,
    permission_decision: PermissionDecision | None = None,
    authority_token: str | None = None,
) -> Any:
    """Decide, then execute -- or refuse typed. The one boundary both routes cross.

    ``permission_decision`` is a decision the caller ALREADY holds (the model loop decided at its
    controller seam); handing the typed object here executes without a second consult, so a caller
    that holds authority is never re-prompted and never double-receipted. Without one, the decision
    is taken here. ``None`` decisions fail closed. The return value is the underlying
    ``RuntimeExecutionResult`` (``None`` stays ``None`` for an intent the runtime cannot dispatch,
    preserving the model loop's fall-through-to-lane contract), with the decision recorded in
    ``details["permission"]``.
    """
    from core.tool_intent_executor import _turn_was_cancelled

    intent = str(intent or "")
    arguments = dict(arguments or {})
    context = dict(source_context or {})

    # Cancellation precedes everything, exactly as on the model route: a cancellation observed
    # after the write has landed is a log line, not a cancellation.
    if _turn_was_cancelled(context):
        return _refusal_result(
            intent,
            None,
            status="cancelled",
            response_text="The turn was cancelled before this tool ran.",
            cancelled=True,
        )

    if permission_decision is not None:
        decision: PermissionDecision | None = permission_decision
    else:
        decision = authorize_runtime_tool(
            intent,
            arguments,
            task_id=task_id,
            source_context=context,
            authority_token=authority_token,
        )

    if decision is None:
        # Fail closed: no decision is not an allow. Nothing here infers authority from who called.
        return _refusal_result(
            intent,
            None,
            status="blocked_by_mode",
            response_text=(
                f"`{intent}` was not executed: no permission decision was taken for this call, "
                "and an undecided call does not run."
            ),
        )

    if decision.effect is PermissionEffect.DENY:
        return _refusal_result(
            intent,
            decision,
            status="blocked_by_mode",
            response_text=f"`{intent}` was not executed: {decision.reason}",
        )

    if decision.effect is PermissionEffect.REQUIRE_APPROVAL:
        request = dict(decision.approval_request or {})
        resources = [str(item) for item in list(request.get("affected_resources") or []) if str(item).strip()]
        resource_text = f" Affected: {', '.join(resources)}." if resources else ""
        return _refusal_result(
            intent,
            decision,
            status="pending_approval",
            response_text=(
                f"`{intent}` needs your approval before it runs. {decision.reason}{resource_text} "
                "Review the details, then allow or deny this request."
            ),
            approval_request=request,
        )

    refused = _pin_approved_replacement(intent, arguments, decision, context)
    if refused is not None:
        return refused

    # The Blackbox flight recorder journals every workspace mutation below this seam; it reads
    # the decision that stood behind the call and the attempt it belongs to from the CONSULTED
    # copy of the context -- never from the caller's dict, never from tool arguments.
    context["_blackbox_permission"] = permission_payload(decision)
    if task_id:
        context.setdefault("_blackbox_task_id", str(task_id))
    runtime = _runtime_execution_tools()
    execution = runtime.execute_runtime_tool(
        intent,
        arguments,
        source_context=context,
        trusted_local_only=trusted_local_only,
    )
    if execution is None:
        return None
    details = dict(getattr(execution, "details", {}) or {})
    # Record, never overwrite: whatever the handler observed stands; the decision rides beside it.
    details.setdefault("permission", permission_payload(decision))
    return type(execution)(
        handled=bool(getattr(execution, "handled", True)),
        ok=bool(getattr(execution, "ok", False)),
        status=str(getattr(execution, "status", "") or ""),
        response_text=str(getattr(execution, "response_text", "") or ""),
        details=details,
    )


__all__ = [
    "REFUSAL_STATUSES",
    "authorize_runtime_tool",
    "execute_authorized_runtime_tool",
]
