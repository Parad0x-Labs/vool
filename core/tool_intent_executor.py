from __future__ import annotations

import logging
import uuid
from typing import Any

_log = logging.getLogger(__name__)

from core import audit_logger, policy_engine
from core.authorized_tool_execution import execute_authorized_runtime_tool
from core.autonomous_topic_research import research_topic_from_signal
from core.curiosity_roamer import CuriosityRoamer
from core.effect_reconciliation import (
    UNKNOWN_OUTCOME_MODE,
    UNKNOWN_OUTCOME_PROSE,
    UNKNOWN_OUTCOME_STATUS,
    EffectOutcomeUnknown,
    a6_reserved_intent,
    build_write_file_expected_evidence,
    clear_in_flight_effect,
    reconcilability_for_intent,
    set_in_flight_effect,
)
from core.execution import capabilities as execution_capabilities
from core.execution.constants import (
    _HIVE_TOOL_INTENTS,
    _MUTATING_OPERATOR_INTENTS,
    _PAYMENT_TOOL_INTENTS,
    _READ_ONLY_OPERATOR_INTENTS,
    _WEB_TOOL_INTENTS,
)
from core.execution.hive_tools import execute_hive_list_available as _execute_hive_list_available_impl
from core.execution.hive_tools import execute_hive_tool as _execute_hive_tool_impl
from core.execution.hive_tools import failed_hive_execution as _failed_hive_execution_impl
from core.execution.mcp_bridge import execute_mcp_intent, is_mcp_intent
from core.execution.models import ToolIntentExecution, _tool_observation
from core.execution.operator_tools import (
    build_operator_action_intent as _build_operator_action_intent_impl,
)
from core.execution.operator_tools import execute_operator_tool as _execute_operator_tool_impl
from core.execution.payment_tools import execute_payment_tool as _execute_payment_tool_impl
from core.execution.planner import (
    _looks_like_workspace_bootstrap_request,
    plan_tool_workflow,
    should_attempt_tool_intent,
)
from core.execution.receipts import (
    execution_from_receipt as _execution_from_receipt_impl,
)
from core.execution.receipts import (
    execution_to_receipt as _execution_to_receipt_impl,
)
from core.execution.receipts import inject_idempotency_key as _inject_idempotency_key_impl
from core.execution.receipts import normalize_payload as _normalize_payload_impl
from core.execution.web_tools import execute_web_tool as _execute_web_tool_impl
from core.execution.web_tools import normalize_item as _normalize_item_impl
from core.hive_activity_tracker import HiveActivityTracker, load_hive_activity_tracker_config
from core.local_operator_actions import (
    OperatorActionIntent,
    dispatch_operator_action,
    list_operator_tools,
    operator_capability_ledger,
)
from core.mode_permission_policy import PermissionEffect, decide_tool_call
from core.public_hive_bridge import PublicHiveBridge, load_public_hive_bridge_config, public_hive_write_enabled
from core.runtime_continuity import (
    build_tool_receipt_key,
    classify_effect_outcome,
    compute_logical_effect_id,
    find_active_unresolved_effect,
    is_mutating_tool_intent,
    load_tool_receipt,
    mark_effect_dispatched,
    reserve_logical_effect,
    store_tool_receipt,
)
from core.runtime_execution_tools import (
    _resolve_workspace_path,
    _workspace_root,
    runtime_execution_capability_ledger,
    runtime_execution_tool_specs,
)
from retrieval.web_adapter import WebAdapter
from tools.registry import call_tool, load_builtin_tools

# Intents that EXECUTE something rather than read or write a file. These are what "do not run
# tests" / "do not run anything" withdraws; a write in the same turn is untouched, which is the
# whole point of separating the two.
_COMMAND_INTENTS = frozenset(
    {
        "sandbox.command",
        "sandbox.run_command",
        "workspace.run_formatter",
        "workspace.run_lint",
        "workspace.run_tests",
    }
)

__all__ = [
    "ToolIntentExecution",
    "_looks_like_workspace_bootstrap_request",
    "execute_tool_intent",
    "plan_tool_workflow",
    "runtime_capability_ledger",
    "runtime_tool_specs",
    "should_attempt_tool_intent",
]


def runtime_capability_ledger(*, hive_read_reachable_fn=None) -> list[dict[str, Any]]:
    return execution_capabilities.runtime_capability_ledger(
        allow_web_fallback_fn=policy_engine.allow_web_fallback,
        allow_browser_fallback_fn=policy_engine.allow_browser_fallback,
        hive_read_reachable_fn=hive_read_reachable_fn,
        load_hive_activity_tracker_config_fn=load_hive_activity_tracker_config,
        load_public_hive_bridge_config_fn=load_public_hive_bridge_config,
        public_hive_write_enabled_fn=public_hive_write_enabled,
        runtime_execution_capability_ledger_fn=runtime_execution_capability_ledger,
        operator_capability_ledger_fn=operator_capability_ledger,
    )


def capability_entry_for_intent(intent: str) -> dict[str, Any] | None:
    return execution_capabilities.capability_entry_for_intent(
        intent,
        runtime_capability_ledger_fn=runtime_capability_ledger,
    )


def capability_gap_for_intent(
    intent: str,
    *,
    extra_entries: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return execution_capabilities.capability_gap_for_intent(
        intent,
        extra_entries=extra_entries,
        runtime_capability_ledger_fn=runtime_capability_ledger,
    )


def capability_truth_for_request(
    user_text: str,
    *,
    extra_entries: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    return execution_capabilities.capability_truth_for_request(
        user_text,
        extra_entries=extra_entries,
        runtime_capability_ledger_fn=runtime_capability_ledger,
    )


def render_capability_truth_response(report: dict[str, Any] | None) -> str:
    return execution_capabilities.render_capability_truth_response(report)


def supported_public_capability_tags(*, limit: int = 16) -> list[str]:
    return execution_capabilities.supported_public_capability_tags(
        limit=limit,
        runtime_capability_ledger_fn=runtime_capability_ledger,
    )


def _unsupported_execution_for_intent(
    intent: str,
    *,
    status: str,
    user_safe_override: str | None = None,
    extra_details: dict[str, Any] | None = None,
) -> ToolIntentExecution:
    gap = capability_gap_for_intent(intent)
    if status in {"disabled", "not_configured", "missing_auth"}:
        gap["gap_kind"] = status
    response = render_capability_truth_response(gap)
    user_safe = str(user_safe_override or response).strip()
    details = {
        "capability_gap": gap,
        **dict(extra_details or {}),
        "observation": _tool_observation(
            intent=intent,
            tool_surface="tool_intent",
            ok=False,
            status=status,
            capability_gap=gap,
        ),
    }
    return ToolIntentExecution(
        handled=True,
        ok=False,
        status=status,
        response_text=response,
        user_safe_response_text=user_safe,
        mode="tool_failed",
        tool_name=intent,
        details=details,
    )


def runtime_tool_specs() -> list[dict[str, Any]]:
    return execution_capabilities.runtime_tool_specs(
        allow_web_fallback_fn=policy_engine.allow_web_fallback,
        allow_browser_fallback_fn=policy_engine.allow_browser_fallback,
        runtime_execution_tool_specs_fn=runtime_execution_tool_specs,
        load_hive_activity_tracker_config_fn=load_hive_activity_tracker_config,
        load_public_hive_bridge_config_fn=load_public_hive_bridge_config,
        public_hive_write_enabled_fn=public_hive_write_enabled,
        list_operator_tools_fn=list_operator_tools,
    )


def _turn_was_cancelled(source_context: dict[str, Any] | None) -> bool:
    """Whether the turn carrying this call has already been cancelled.

    The server owns the per-turn cancel signal and hands it down in `source_context`. It arrives as
    a `threading.Event` today; a callable or a bare flag is accepted too, because the mesh and the
    API layer each build their own and the tool loop must not care which. Anything that raises
    while being read counts as CANCELLED: a signal that cannot be read is not permission to run.
    """

    context = source_context or {}
    token = context.get("cancel_event") or context.get("cancellation_token")
    if token is None:
        return False
    checker = getattr(token, "is_set", None)
    if callable(checker):
        try:
            return bool(checker())
        except Exception:
            return True
    if callable(token):
        try:
            return bool(token())
        except Exception:
            return True
    return bool(token)


def execute_tool_intent(
    payload: Any,
    *,
    task_id: str,
    session_id: str,
    source_context: dict[str, Any] | None,
    hive_activity_tracker: HiveActivityTracker,
    public_hive_bridge: PublicHiveBridge | None = None,
    checkpoint_id: str | None = None,
    step_index: int = 0,
    tool_call_id: str | None = None,
    trusted_local_only: bool = False,
) -> ToolIntentExecution:
    """Execute one tool intent. Every execution is stamped with a unique tool_call_id
    (details['tool_call_id']) so results stay bound to the call that produced them.

    `trusted_local_only` is a server-only channel: it may be set solely by a Python caller that
    built the command itself, never from anything carried inside `payload`."""
    call_id = str(tool_call_id or "").strip() or f"tool-{uuid.uuid4().hex}"

    # A DIRECT call — no turn ledger active — is legitimate library use of the
    # executor, and THIS function is its owning entry point. It opens the R2b1
    # sanctioned named scope itself, so every effect door below has a ledger to
    # attribute the execution to instead of denying it as an unattributable
    # effect; the permission authority still decides the dispatch inside, and a
    # turn-scoped caller never reaches this branch (their ledger defers the
    # scope). One recursion deep: the re-entry sees the scope's ledger.
    from core.effect_gateway import current_effect_ledger, named_background_effect_scope

    if current_effect_ledger() is None:
        with named_background_effect_scope(
            "executor.direct_call", source_context=dict(source_context or {})
        ):
            return execute_tool_intent(
                payload,
                task_id=task_id,
                session_id=session_id,
                source_context=source_context,
                hive_activity_tracker=hive_activity_tracker,
                public_hive_bridge=public_hive_bridge,
                checkpoint_id=checkpoint_id,
                step_index=step_index,
                tool_call_id=call_id,
                trusted_local_only=trusted_local_only,
            )

    # Every tool dispatch in this runtime funnels through here, which makes it the one place a
    # cancelled turn can be stopped before it does something. Nothing checked it: the streaming
    # adapters consult `request.is_cancelled()` between chunks, but the TOOL loop consulted nothing,
    # so pressing stop ended the visible reply while an in-flight destructive call ran to completion
    # anyway. Checked BEFORE dispatch, never after -- a cancellation observed after the write has
    # landed is a log line, not a cancellation.
    if _turn_was_cancelled(source_context):
        return ToolIntentExecution(
            handled=True,
            ok=False,
            status="cancelled",
            mode="cancelled",
            tool_name=str(_normalize_payload(payload).get("intent") or ""),
            response_text="The turn was cancelled before this tool ran.",
            user_safe_response_text="The turn was cancelled before this tool ran.",
            details={"tool_call_id": call_id, "executed": False, "cancelled": True},
        )

    # The coding lane's one execution law: while this session has a code task open, its mutations
    # are proposed as contracted `code.task.*` calls -- never executed directly, by a model, a
    # plugin tool or a skill-shaped intent. Decided from the task journal's typed state and the
    # intent's declared side-effect class, before any authority is spent. The executor's own
    # session id is authoritative, so the guard cannot be sidestepped by a context that omits it.
    try:
        from core.code_assistant.task_runtime import active_task_mutation_refusal

        task_guard = active_task_mutation_refusal(
            str(_normalize_payload(payload).get("intent") or ""),
            {**(source_context or {}), "session_id": session_id, "runtime_session_id": session_id},
        )
    except Exception:
        task_guard = ""
    if task_guard:
        return ToolIntentExecution(
            handled=True,
            ok=False,
            status="code_task_control_required",
            mode="refused",
            tool_name=str(_normalize_payload(payload).get("intent") or ""),
            response_text=task_guard,
            user_safe_response_text=task_guard,
            details={"tool_call_id": call_id, "executed": False, "reason": "code_task_control_required"},
        )

    execution = _execute_tool_intent_impl(
        payload,
        task_id=task_id,
        session_id=session_id,
        source_context=source_context,
        hive_activity_tracker=hive_activity_tracker,
        public_hive_bridge=public_hive_bridge,
        checkpoint_id=checkpoint_id,
        step_index=step_index,
        trusted_local_only=trusted_local_only,
    )
    try:
        if isinstance(execution.details, dict):
            execution.details.setdefault("tool_call_id", call_id)
    except Exception:
        pass
    return execution


def _intent_is_dispatchable(intent: str) -> bool:
    """Does SOME handler in this runtime claim this intent?

    Mirrors the dispatch chain below, in the same order, so the answer cannot drift from what
    actually runs. Anything this says False about reaches the unsupported fall-through and executes
    nothing, which is why answering it before the permission gate is safe.
    """
    name = str(intent or "").strip()
    if not name:
        return False
    if name in _WEB_TOOL_INTENTS | _HIVE_TOOL_INTENTS | _PAYMENT_TOOL_INTENTS:
        return True
    if name in _READ_ONLY_OPERATOR_INTENTS | _MUTATING_OPERATOR_INTENTS:
        return True
    try:
        # The ONE registry: builtins plus every registered plugin and MCP contract. Reading the
        # builtin literal map here is what answered a registered plugin tool "unsupported" before
        # the permission gate ever saw it (measured 2026-09-02 at 96c2fb96). An `mcp.*` name is
        # dispatchable only while its server is up and listed it — the bare prefix used to count,
        # which put a tool nothing could run to the permission controller.
        from core.tool_registry import registry_map

        contract = registry_map().get(name)
        if contract is not None:
            # A contract the runtime has DISABLED (policy, owner, unreachable server) cannot
            # dispatch either. Asking for approval to run it would be untrue; it is answered
            # with its own reason instead (see `_undispatchable_execution`).
            if not bool(getattr(contract, "supported", False)):
                return False
            # A plugin tool dispatches only while its lane is open: the API server registers
            # every installed pack at boot regardless of the flag, and the flag is what the
            # operator turned off. Not offered AND not dispatchable, or a model naming the
            # tool would run what the operator closed.
            is_plugin = str(getattr(contract, "source", "") or "").startswith("plugin:")
            return not (is_plugin and not _plugin_lane_open())
    except Exception:
        # A registry that cannot be read must not turn every tool into "unsupported". Fall through
        # to dispatchable: the permission gate below still decides, and the chain still ends at the
        # honest unsupported result if nothing handles it.
        return True
    return False


def _is_plugin_contract(intent: str) -> bool:
    try:
        from core.tool_registry import tool_for_intent

        contract = tool_for_intent(intent)
    except Exception:
        return False
    return contract is not None and str(getattr(contract, "source", "") or "").startswith("plugin:")


def _plugin_lane_open() -> bool:
    try:
        from core.runtime_flags import flag_enabled

        return bool(flag_enabled("plugin_runtime_tools"))
    except Exception:
        return False


def _sync_registry_sources() -> None:
    """Bring the dynamic tool sources (MCP servers, plugin packs) into the registry before deciding.

    The registry is the authority the dispatchability check and the permission classifier read.
    A configured MCP tool called before anything offered it would otherwise be classified with no
    contract at all — unknown side effect, prompted — instead of by its pinned trust. Fail-soft.
    """
    try:
        from core.capability_graph import ensure_registry_bootstrap

        ensure_registry_bootstrap()
    except Exception:
        pass


def _undispatchable_execution(intent: str, arguments: dict[str, Any]) -> ToolIntentExecution:
    """The answer for an intent no lane can run right now: its own reason, never a prompt.

    Three shapes, each explained rather than fabricated: a DISABLED contract (policy flag, owner
    switch, unreachable server) answers with the contract's reason; an `mcp.*` name whose server
    is not configured, not answering, or does not list the tool says which; anything else is the
    plain "not wired" capability gap.
    """
    try:
        from core.tool_registry import tool_for_intent

        contract = tool_for_intent(intent)
    except Exception:
        contract = None
    def _explained(status: str, reason: str) -> ToolIntentExecution:
        execution = _unsupported_execution_for_intent(
            intent,
            status=status,
            user_safe_override=reason,
            extra_details={"intent": intent, "arguments": arguments, "executed": False, "reason": reason},
        )
        # The reason IS the answer, on both channels: the model reads why, the user reads why.
        execution.response_text = reason
        execution.user_safe_response_text = reason
        return execution

    if contract is not None and not bool(getattr(contract, "supported", False)):
        reason = str(getattr(contract, "unsupported_reason", "") or "").strip() or (
            f"`{intent}` is disabled on this runtime."
        )
        return _explained("disabled", reason)
    if (
        contract is not None
        and str(getattr(contract, "source", "") or "").startswith("plugin:")
        and not _plugin_lane_open()
    ):
        return _explained(
            "disabled",
            f"`{intent}` is a plugin tool and plugin runtime tools are disabled on this runtime "
            "(runtime flag plugin_runtime_tools / VOOL_PLUGIN_RUNTIME_TOOLS).",
        )
    if is_mcp_intent(intent):
        try:
            from core.execution.mcp_bridge import load_mcp_server_configs, server_failure_reason

            parts = str(intent).split(".", 2)
            server = parts[1] if len(parts) == 3 else ""
            configured = any(s.get("name") == server for s in load_mcp_server_configs())
            if not configured:
                status, reason = "mcp_server_not_configured", (
                    f"MCP server {server!r} is not configured (MCP is opt-in)."
                )
            else:
                failure = server_failure_reason(server)
                if failure:
                    status, reason = "mcp_server_unavailable", (
                        f"MCP server {server!r} could not be started: {failure}"
                    )
                else:
                    status, reason = "unsupported", (
                        f"MCP server {server!r} does not list a tool named "
                        f"{parts[2] if len(parts) == 3 else intent!r}."
                    )
        except Exception:
            status, reason = "unsupported", "That action is not wired on this runtime yet."
        return _explained(status, reason)
    return _unsupported_execution_for_intent(
        intent,
        status="unsupported",
        extra_details={"intent": intent, "arguments": arguments},
        user_safe_override="That action is not wired on this runtime yet.",
    )


def _registered_mutating_contract(intent: str) -> bool:
    """Whether a REGISTERED (plugin/MCP) contract declares a mutating side-effect class.

    The static mutating-intent table only knows the builtins. A plugin tool that declares
    `workspace_write` earns the same receipt/replay boundary as `workspace.write_file`, and it
    earns it from its own declaration — the one authority — not from a table nobody updates.
    """
    try:
        from core.tool_registry import tool_for_intent

        contract = tool_for_intent(intent)
    except Exception:
        return False
    if contract is None or str(getattr(contract, "source", "builtin") or "builtin") == "builtin":
        return False
    return str(getattr(contract, "side_effect_class", "") or "") not in {"", "read_only"}


def _with_blackbox_coverage(
    intent: str,
    arguments: dict[str, Any],
    *,
    source_context: dict[str, Any] | None,
    dispatch,
) -> ToolIntentExecution:
    """Blackbox coverage for the executor's own lanes (plugin, MCP): a mutating contract that
    declared a coverage recorder runs between durable Blackbox observations; one that declares
    nothing is a typed refusal -- an undeclared local mutation does not run. Read-only contracts
    dispatch unchanged.

    C02 — THE REGISTERED-EFFECT BUDGET DOOR: the same seat is also where a REGISTERED contract's
    declared effect reserves its session/turn budget unit, before the recorder or the handler can
    run. The class comes from the contract's OWN side_effect_class through the one shared map
    (no plugin-specific policy); a refusal returns a typed refusal execution with the handler,
    the recorder, and every subprocess/transport/write the effect would have touched UNRUN —
    zero handler calls, zero Blackbox observations, zero success-shaped receipts.
    """

    from core.blackbox.coverage.registry import (
        RECORDER_COVERAGE_DECLARED,
        RECORDER_COVERAGE_POST,
        RECORDER_COVERAGE_SCAN,
        mutation_coverage_decision,
    )
    from core.effect_budget import EffectBudgetRefusedError

    decision = mutation_coverage_decision(intent)
    if not decision.covered:
        text = (
            f"`{intent}` was not executed: it is capable of local mutation and carries no "
            "Blackbox coverage declaration (scope, reversibility, snapshot strategy, receipt "
            "lifecycle, rollback support, recorder). An undeclared mutation does not run."
        )
        return ToolIntentExecution(
            handled=True,
            ok=False,
            status="blackbox_coverage_required",
            response_text=text,
            user_safe_response_text=text,
            mode="tool_failed",
            tool_name=intent,
            details={
                "executed": False,
                "coverage_reason": decision.reason,
                "controller_enforced": True,
            },
        )
    try:
        budget_effect = _open_registered_budget_effect(intent, source_context=source_context)
    except EffectBudgetRefusedError as budget_refusal:
        return _budget_refused_execution(intent, budget_refusal)
    if decision.recorder not in {RECORDER_COVERAGE_SCAN, RECORDER_COVERAGE_DECLARED, RECORDER_COVERAGE_POST}:
        # A REGISTERED contract claiming read-only gets a mechanical check of its own: the
        # workspace is observed before and after dispatch, and any local mutation means the
        # claim was false -- the execution cannot publish success. (The confined plugin child
        # already ran with zero writable roots; this catches MCP servers and confinement-less
        # hosts the kernel cannot speak for.)
        from core.tool_registry import tool_for_intent as _contract_for

        contract = _contract_for(intent)
        if contract is not None and str(getattr(contract, "side_effect_class", "") or "") == "read_only":
            from core.blackbox.coverage.recorder import workspace_root_from_context
            from core.blackbox.coverage.scan import diff_scans, scan_workspace
            from core.blackbox.store import default_store

            root = workspace_root_from_context(source_context)
            if root is not None:
                before = scan_workspace(root, store=default_store())
                execution = dispatch()
                after = scan_workspace(root, store=default_store())
                drift = diff_scans(before, after)
                if drift:
                    text = (
                        f"`{intent}` declared itself read-only but changed local state "
                        f"({len(drift)} path(s), e.g. {drift[0].path}). A misdeclared read-only "
                        "tool cannot publish success; its output is void."
                    )
                    return ToolIntentExecution(
                        handled=True,
                        ok=False,
                        status="blackbox_read_only_mutated",
                        response_text=text,
                        user_safe_response_text=text,
                        mode="tool_failed",
                        tool_name=intent,
                        details={
                            "executed": True,
                            "read_only_violation": True,
                            "mutated_paths": [item.path for item in drift[:32]],
                        },
                    )
                return _settle_registered_budget_effect(intent, budget_effect, execution)
        return _settle_registered_budget_effect(intent, budget_effect, _dispatch_settling_raise(intent, budget_effect, dispatch))

    from core.blackbox.coverage.recorder import (
        recorded_capability_mutation,
        workspace_root_from_context,
    )

    def _factory(intent_name: str, status: str, text: str, details: dict[str, Any]) -> ToolIntentExecution:
        return ToolIntentExecution(
            handled=True,
            ok=False,
            status=status,
            response_text=text,
            user_safe_response_text=text,
            mode="tool_failed",
            tool_name=intent_name,
            details=details,
        )

    execution = recorded_capability_mutation(
        intent,
        arguments,
        handler=dispatch,
        source_context=source_context,
        workspace_root=workspace_root_from_context(source_context),
        result_factory=_factory,
    )
    return _settle_registered_budget_effect(intent, budget_effect, execution)


def _open_registered_budget_effect(intent: str, *, source_context: dict[str, Any] | None):
    """Reserve the budget unit a REGISTERED contract's declared effect owes, at THIS door.

    Same shape as the runtime tools' coverage door: None when the contract's class is unbudgeted
    by name (or no operator rule is active); a lifecycle-shaped handle whose unit is consumed
    when the handler actually runs and settles from the effect's real outcome; a typed
    `EffectBudgetRefusedError` when the budget gate says no — in which case nothing dispatches.
    """
    from core.blackbox.coverage.recorder import workspace_root_from_context
    from core.runtime_execution_tools import _open_mutation_budget_effect

    return _open_mutation_budget_effect(
        intent, workspace_root=workspace_root_from_context(source_context)
    )


def _budget_refused_execution(intent: str, refusal) -> ToolIntentExecution:
    """The typed refusal when the budget gate refuses a registered effect:
    the handler never ran, `ok=False`, and the typed code is on the record."""
    rule = f" [rule {refusal.rule}]" if getattr(refusal, "rule", "") else ""
    text = (
        f"`{intent}` was not executed: the effect budget refused it "
        f"({refusal.code}: {refusal.detail}{rule})."
    )
    return ToolIntentExecution(
        handled=True,
        ok=False,
        status="blocked_by_effect_budget",
        response_text=text,
        user_safe_response_text=text,
        mode="tool_failed",
        tool_name=intent,
        details={
            "executed": False,
            "controller_enforced": True,
            "effect_budget": {
                "code": refusal.code,
                "detail": refusal.detail,
                "rule": refusal.rule,
            },
            "observation": _tool_observation(
                intent=intent,
                tool_surface="effect_budget",
                ok=False,
                status="blocked_by_effect_budget",
            ),
        },
    )


def _dispatch_settling_raise(intent: str, budget_effect, dispatch):
    """Run the passthrough dispatch, settling the reservation if the handler RAISES:
    the attempt happened, its unit is spent (never refunded), and the effect settles
    failed from the raise before the exception continues unwinding."""
    try:
        return dispatch()
    except BaseException:
        if budget_effect is not None:
            budget_effect.begin_attempt()
            budget_effect.fail(reason="registered effect raised before an outcome")
        raise


def _settle_registered_budget_effect(intent: str, budget_effect, execution: ToolIntentExecution) -> ToolIntentExecution:
    """Settle the door's reservation from the effect's real outcome — the same law as the
    runtime tools' coverage door: a recorder/coverage refusal BEFORE execution releases the
    reserved unit (reserved-then-never-run); anything whose handler actually ran consumes the
    unit and reconciles the terminal from the outcome. Crashed and failed effects stay charged."""
    if budget_effect is None:
        return execution
    details = dict(getattr(execution, "details", None) or {})
    refused_before_execution = (
        not bool(details.get("executed", True))
        and str(getattr(execution, "status", "") or "").startswith("blackbox_")
    )
    if refused_before_execution:
        from core.effect_budget import release_effect_reservations

        budget_effect.cancel(reason=f"coverage refused {intent} before execution")
        release_effect_reservations(getattr(budget_effect, "effect_id", "") or "")
        return execution
    budget_effect.begin_attempt()
    if getattr(execution, "ok", False):
        budget_effect.succeed(reason="coverage-recorded effect settled")
    else:
        budget_effect.fail(reason="coverage-recorded effect returned a failure result")
    return execution


def _execute_plugin_tool(intent: str, arguments: dict[str, Any]) -> ToolIntentExecution | None:
    """Dispatch a REGISTERED plugin contract through the confined plugin executor.

    Returns None when `intent` is not a plugin contract, so the chain continues to the next lane.
    A disabled plugin's tool answers with its own reason and never runs; a crashing executor is a
    structured failure, never an exception up the loop and never a claimed success.
    """
    from core import plugin_tools
    from core.tool_registry import tool_for_intent

    contract = tool_for_intent(intent)
    if contract is None or not str(getattr(contract, "source", "") or "").startswith("plugin:"):
        return None
    if not bool(getattr(contract, "supported", False)):
        reason = str(getattr(contract, "unsupported_reason", "") or "").strip() or (
            f"`{intent}` is not available on this runtime."
        )
        return ToolIntentExecution(
            handled=True,
            ok=False,
            status="disabled",
            response_text=reason,
            user_safe_response_text=reason,
            mode="tool_failed",
            tool_name=intent,
            details={
                "executed": False,
                "observation": _tool_observation(
                    intent=intent, tool_surface="plugin", ok=False, status="disabled", reason=reason
                ),
            },
        )
    try:
        result = plugin_tools.execute_plugin_contract(contract, dict(arguments or {}))
    except Exception as exc:
        error = f"plugin executor failed: {type(exc).__name__}: {exc}"[:500]
        return ToolIntentExecution(
            handled=True,
            ok=False,
            status="handler_failed",
            response_text=error,
            user_safe_response_text="The plugin tool failed before it could run.",
            mode="tool_failed",
            tool_name=intent,
            details={
                "error": error,
                "observation": _tool_observation(
                    intent=intent, tool_surface="plugin", ok=False, status="handler_failed"
                ),
            },
        )
    details = result.as_details()
    payload = {k: v for k, v in dict(result.observation or {}).items() if k != "intent"}
    details["observation"] = _tool_observation(
        intent=intent,
        tool_surface="plugin",
        ok=bool(result.ok),
        status=str(result.status or ""),
        **payload,
    )
    details["executed"] = bool(result.confinement) and result.status not in {
        "invalid_arguments",
        "invalid_argument_shape",
        "handler_missing",
        "handler_escaped",
        "confinement_unavailable",
    }
    text = str(result.text or "").strip()
    if not result.ok:
        text = text or str(result.error or "").strip() or f"`{intent}` reported {result.status}."
        if result.status == "confinement_unavailable":
            text = f"`{intent}` was not run: this host cannot confine the plugin process. {result.error}"
    return ToolIntentExecution(
        handled=True,
        ok=bool(result.ok),
        status=str(result.status or ("executed" if result.ok else "failed")),
        response_text=text,
        # The plugin's stdout is an observation, not a host-attested completion receipt.
        # Keep it available to synthesis through response_text/observation, but do not
        # promote its self-reported file or test claims into the activity/fallback summary.
        user_safe_response_text=(
            f"Plugin `{intent}` returned output; its claims need independent verification."
            if result.ok else text
        ),
        mode="tool_executed" if result.ok else "tool_failed",
        tool_name=intent,
        details=details,
    )


def _a6_reservation_required(intent: str) -> bool:
    """The ONE canonical reservation eligibility rule (A6).

    Any intent that A6 POLICY classifies as a mutating logical effect must carry
    a canonical A6 reservation across its actual dispatching lane — runtime,
    hive, operator fall-through, or otherwise. The previous predicate demanded
    `handler == "runtime"` here, which let eight served mutating intents (five
    hive.* with no contract, three operator.* external_lane) satisfy the
    reservation policy yet skip the reserve/claim path entirely and dispatch
    bare. The dispatch machinery below already carries a claim onto whichever
    lane actually executes (`_dispatch_with_claim`), and `execute_runtime_tool`
    deterministically declines non-runtime handlers, so the lane is chosen by
    where the claim LANDS — never by whether one may be held.
    """
    return a6_reserved_intent(intent)


def _a6_expected_evidence_for_intent(intent: str, arguments: dict[str, Any], source_context: dict[str, Any] | None) -> dict[str, Any]:
    """Capture canonical resource identity + expected content hash at reserve time,
    reusing the handler layer's own path resolution so reconciliation compares the
    SAME canonical target the mechanism writes."""
    try:
        if intent == "workspace.write_file":
            target = _resolve_workspace_path(
                arguments.get("path"), workspace_root=_workspace_root(source_context)
            )
            return build_write_file_expected_evidence(
                canonical_path=str(target), content=str(arguments.get("content") or "")
            )
    except Exception:
        pass
    return {}



def _obligation_register_effect(logical_effect_id: str) -> None:
    """Residue-2 (K-05): a reserved mutating effect joins the turn's active
    obligation set as kind='effect' — finalization cannot close over it
    without A6 reconciled evidence. No-op when no set is bound."""
    try:
        from core.conductor import obligation_ledger as _ol

        _active = _ol.active_set()
        if _active is None:
            return
        _ol.register_effect_obligation(
            _active[0], _active[1], f"ob:effect:{logical_effect_id}"
        )
    except Exception:
        raise


def _obligation_discharge_effect(logical_effect_id: str, *, applied: bool | None) -> None:
    """Discharge from A6 RECONCILED evidence only; unknown stays open."""
    try:
        from core.conductor import obligation_ledger as _ol

        _active = _ol.active_set()
        if _active is None:
            return
        _ol.discharge_effect_obligation(
            _active[0], _active[1], logical_effect_id, applied=applied
        )
    except Exception:
        raise

def _a6_reserve_before_dispatch(
    *,
    intent: str,
    arguments: dict[str, Any],
    task_id: str,
    session_id: str,
    checkpoint_id: str | None,
    source_context: dict[str, Any] | None,
):
    """A6 SP-2: reserve the logical effect after authorization, before first mutation.

    Returns a ToolIntentExecution to short-circuit with (blocked pending
    reconciliation) or a reservation claim dict — or None when this intent is
    not policy-reserved.
    """
    if checkpoint_id is None or not _a6_reservation_required(intent):
        return None
    logical_effect_id = compute_logical_effect_id(intent=intent, arguments=arguments)
    active = find_active_unresolved_effect(logical_effect_id)
    if active is not None:
        # Same intended real-world mutation while an instance is still
        # unresolved: RETRY_OF_UNKNOWN, blocked pending reconciliation. The
        # distinction from an intentional future repeat is carried entirely by
        # this durable state, never by prompt text.
        return _a6_unknown_blocked_execution(intent, logical_effect_id, active_row=active)
    evidence = _a6_expected_evidence_for_intent(intent, arguments, source_context)
    semantic_result_id = ""
    context = dict(source_context or {})
    for key in ("semantic_result_id", "_semantic_result_id"):
        candidate = str(context.get(key) or "").strip()
        if candidate:
            semantic_result_id = candidate
            break
    reservation = reserve_logical_effect(
        intent=intent,
        arguments=arguments,
        resource_identity=str(evidence.get("path") or ""),
        expected_evidence=evidence,
        session_id=str(session_id or ""),
        checkpoint_id=str(checkpoint_id or ""),
        turn_id=str(context.get("_canonical_user_turn_id") or ""),
        receipt_key="",
        semantic_result_id=semantic_result_id,
        attempt_id=str(task_id or ""),
        reconcilability=reconcilability_for_intent(intent).value,
    )
    if reservation.get("outcome") != "reserved":
        # Lost the race against a concurrent identical effect (or a stale row
        # survived): block pending that instance's reconciliation.
        return _a6_unknown_blocked_execution(
            intent, logical_effect_id, active_row=reservation.get("row")
        )
    return {
        "logical_effect_id": str(reservation["logical_effect_id"]),
        "effect_instance_id": str(reservation["effect_instance_id"]),
    }


def _a6_unknown_blocked_execution(
    intent: str,
    logical_effect_id: str,
    *,
    active_row: dict[str, Any] | None,
) -> ToolIntentExecution:
    response = UNKNOWN_OUTCOME_PROSE
    details = {
        "reconciliation_required": True,
        "blocked_pending_reconciliation": True,
        "logical_effect_id": logical_effect_id,
        "executed": False,
        "observation": _tool_observation(
            intent=intent,
            tool_surface="runtime_execution",
            ok=False,
            status=UNKNOWN_OUTCOME_STATUS,
            logical_effect_id=logical_effect_id,
        ),
    }
    if isinstance(active_row, dict):
        details["active_effect_instance_id"] = str(active_row.get("effect_instance_id") or "")
        details["active_state"] = str(active_row.get("state") or "")
        details["unknown_reason"] = str(active_row.get("reason") or "")
        prior_detail = str(active_row.get("detail") or "").strip()
        if prior_detail:
            response = f"{response} (Prior dispatch detail: {prior_detail[:200]})"
    return ToolIntentExecution(
        handled=True,
        ok=False,
        status=UNKNOWN_OUTCOME_STATUS,
        response_text=response,
        user_safe_response_text=response,
        mode=UNKNOWN_OUTCOME_MODE,
        tool_name=intent,
        details=details,
    )


def _a6_unknown_outcome_execution(
    intent: str,
    reason: str,
    detail: str,
    *,
    logical_effect_id: str = "",
    effect_instance_id: str = "",
) -> ToolIntentExecution:
    details = {
        "reconciliation_required": True,
        "unknown_reason": str(reason or ""),
        "logical_effect_id": logical_effect_id,
        "effect_instance_id": effect_instance_id,
        "observation": _tool_observation(
            intent=intent,
            tool_surface="runtime_execution",
            ok=False,
            status=UNKNOWN_OUTCOME_STATUS,
            error=str(detail or ""),
        ),
    }
    return ToolIntentExecution(
        handled=True,
        ok=False,
        status=UNKNOWN_OUTCOME_STATUS,
        response_text=UNKNOWN_OUTCOME_PROSE,
        user_safe_response_text=UNKNOWN_OUTCOME_PROSE,
        mode=UNKNOWN_OUTCOME_MODE,
        tool_name=intent,
        details=details,
    )


def _execute_tool_intent_impl(
    payload: Any,
    *,
    task_id: str,
    session_id: str,
    source_context: dict[str, Any] | None,
    hive_activity_tracker: HiveActivityTracker,
    public_hive_bridge: PublicHiveBridge | None = None,
    checkpoint_id: str | None = None,
    step_index: int = 0,
    trusted_local_only: bool = False,
) -> ToolIntentExecution:
    normalized = _normalize_payload(payload)
    intent = str(normalized.get("intent") or "").strip()
    arguments = dict(normalized.get("arguments") or {})
    if not intent:
        return ToolIntentExecution(
            handled=True,
            ok=False,
            status="missing_intent",
            response_text="I won't fake it: the model returned an invalid tool payload with no intent name.",
            user_safe_response_text="I couldn't map that cleanly to a real action.",
            mode="tool_failed",
            tool_name="unknown",
            details={"payload": normalized},
        )
    if intent in {"respond.direct", "none", "no_tool"}:
        return ToolIntentExecution(handled=False, ok=True, status="direct_response")

    # `machine.list_directory` is a particularly sharp lexical collision: "List only members of
    # this standard" is a response-shape/knowledge request, while the tool reads a local path.
    # The front door stamps this proof only when the existing typed list-directory probe found an
    # explicit machine/path target in the authoritative post-resume raw turn. A model-selected
    # tool name is a proposal, never substitute evidence for that binding.
    if (
        intent == "machine.list_directory"
        and "_semantic_machine_list_directory_admitted" in (source_context or {})
        and not bool((source_context or {}).get("_semantic_machine_list_directory_admitted"))
    ):
        response = (
            "I did not inspect a local directory because this turn did not establish a "
            "machine or path target for that action."
        )
        return ToolIntentExecution(
            handled=True,
            ok=False,
            status="blocked_by_semantic_preflight",
            response_text=response,
            user_safe_response_text=response,
            mode="tool_failed",
            tool_name=intent,
            details={
                "controller_enforced": True,
                "executed": False,
                "semantic_proof": "absent",
                "observation": _tool_observation(
                    intent=intent,
                    tool_surface="semantic_preflight",
                    ok=False,
                    status="blocked_by_semantic_preflight",
                ),
            },
        )

    # The front door derives this from clear user language.  Enforce it here as well so a model
    # proposal cannot create an approval card (or execute) after action-capable fast paths skipped.
    from core.agent_runtime.intent_claims import ActionPolicy, action_policy_from_context

    if action_policy_from_context(source_context) is ActionPolicy.FORBIDDEN:
        response = "I will keep this turn conversational and will not propose or run an action."
        return ToolIntentExecution(
            handled=True,
            ok=False,
            status="blocked_by_action_policy",
            response_text=response,
            user_safe_response_text=response,
            mode="tool_failed",
            tool_name=intent,
            details={
                "action_policy": ActionPolicy.FORBIDDEN.value,
                "controller_enforced": True,
                "executed": False,
                "observation": _tool_observation(
                    intent=intent,
                    tool_surface="action_policy",
                    ok=False,
                    status="blocked_by_action_policy",
                ),
            },
        )

    # "Do not run tests" is not "do not act". It withdraws execution and leaves the writes the same
    # sentence asked for standing, so it is enforced HERE -- one gate, after the turn policy and
    # before dispatch -- rather than by each lane deciding for itself what the words meant. The
    # build lane's `MutationScope.allow_commands` refuses the same command earlier; this is the gate
    # that holds for every other lane that can reach one.
    from core.agent_runtime.intent_claims import commands_forbidden_in_context

    if intent in _COMMAND_INTENTS and commands_forbidden_in_context(source_context):
        response = "You asked me not to run anything this turn, so I did not run that command."
        return ToolIntentExecution(
            handled=True,
            ok=False,
            status="blocked_by_no_run_constraint",
            response_text=response,
            user_safe_response_text=response,
            mode="tool_failed",
            tool_name=intent,
            details={
                "controller_enforced": True,
                "executed": False,
                "no_command_execution": True,
                "observation": _tool_observation(
                    intent=intent,
                    tool_surface="action_policy",
                    ok=False,
                    status="blocked_by_no_run_constraint",
                ),
            },
        )

    # One controller-owned permission seam for every model-selected tool.  This runs before
    # dispatch to web, workspace, sandbox, Hive, operator, payment, or MCP implementations, so a
    # downstream path cannot accidentally bypass Plan/Review/Manual restrictions.
    # An intent this runtime cannot dispatch is answered as unsupported, and is never put to the
    # permission controller. Asking the operator to approve `fake.magic` would be a prompt nobody
    # can evaluate -- the approval card cannot name an effect the runtime has no handler for -- and
    # answering "Manual mode requires approval" for a tool that does not exist is simply untrue.
    # This is not a permission bypass: an intent with no handler cannot reach one. Everything that
    # CAN dispatch falls through to the unconditional decision below.
    _sync_registry_sources()
    if not _intent_is_dispatchable(intent):
        undispatchable = _undispatchable_execution(intent, arguments)
        _record_execution(
            undispatchable,
            session_id=session_id,
            intent=intent,
            arguments=arguments,
            source_context=source_context,
        )
        return undispatchable

    # UNCONDITIONAL. This used to run only `if mode_policy_is_active(source_context)`, which made
    # the absence of a mode a total bypass: a caller carrying no `operating_mode` and no session
    # record wrote files and ran side-effecting commands with no decision taken at all. Measured on
    # the parent commit, a modeless `workspace.write_file` created the file and this function was
    # never called. Defaulting the missing mode to AUTO would not have closed it — AUTO permits
    # writes and side-effecting commands — so the default is MANUAL, decided here, every time.
    #
    # Background/internal callers are served by an explicit typed scope
    # (`mode_permission_policy.grant_internal_authority`) that names the actions it needs. Not
    # sending a mode is not that scope, and must never be read as one again.
    if intent == "sandbox.run_command" and str(arguments.get("cwd") or "").strip():
        # A working directory outside the workspace is refused HERE, before the permission
        # decision: measured live 2026-09-07, the controller asked the operator to approve
        # `cwd=/Users`, the operator did, and the handler then refused -- the approval bought
        # nothing and the refusal surfaced as an unknown outcome. Same predicate as the handler.
        from core.runtime_execution_tools import _workspace_root, sandbox_cwd_refusal

        refusal = sandbox_cwd_refusal(arguments.get("cwd"), workspace_root=_workspace_root(source_context))
        if refusal is not None:
            return ToolIntentExecution(
                handled=True,
                ok=False,
                status=refusal.status,
                response_text=refusal.response_text,
                user_safe_response_text=refusal.response_text,
                mode="tool_failed",
                tool_name=intent,
                details=dict(refusal.details or {}),
            )
    if intent == "code.task.step":
        from core.code_assistant.step_validation import step_argument_error

        argument_error = step_argument_error(arguments)
        if argument_error:
            return ToolIntentExecution(
                handled=True, ok=False, status="invalid_arguments",
                response_text=argument_error, user_safe_response_text=argument_error,
                mode="tool_failed", tool_name=intent,
                details={"executed": False, "observation": _tool_observation(
                    intent=intent, tool_surface="code_task", ok=False, status="invalid_arguments",
                )},
            )
    permission = decide_tool_call(
        intent=intent,
        arguments=arguments,
        task_id=task_id,
        source_context=source_context,
    )
    if permission is not None and permission.effect is PermissionEffect.DENY:
        return ToolIntentExecution(
            handled=True,
            ok=False,
            status="blocked_by_mode",
            response_text=permission.reason,
            user_safe_response_text=permission.reason,
            mode="tool_failed",
            tool_name=intent,
            details={
                "operating_mode": permission.mode.value,
                "permission_actions": [action.value for action in permission.actions],
                "controller_enforced": True,
                "executed": False,
                "observation": _tool_observation(
                    intent=intent,
                    tool_surface="mode_permission_controller",
                    ok=False,
                    status="blocked_by_mode",
                    operating_mode=permission.mode.value,
                ),
            },
        )
    if permission is not None and permission.effect is PermissionEffect.REQUIRE_APPROVAL:
        # ROOT-CAUSE CONTRACT (amendment gap 1) — the repair diagnosis opens
        # BEFORE the approved mutation executes, when the turn's own plan is
        # repair-shaped (this mutation + a validation in the pending batch).
        # Ordinary gated edits open nothing. Fail-soft: the prompt is the
        # product of this branch regardless.
        try:
            from core.root_cause_contract import note_gated_repair as _rcc_note

            _rcc_note(source_context, intent=intent, arguments=arguments)
        except Exception:
            pass
        request = dict(permission.approval_request or {})
        resources = [str(item) for item in list(request.get("affected_resources") or []) if str(item).strip()]
        resource_text = f" Affected: {', '.join(resources)}." if resources else ""
        response = f"{permission.reason}{resource_text} Review the details, then allow or deny this request."
        return ToolIntentExecution(
            handled=True,
            ok=False,
            status="pending_approval",
            response_text=response,
            user_safe_response_text=response,
            mode="tool_preview",
            tool_name=intent,
            details={
                "operating_mode": permission.mode.value,
                "permission_actions": [action.value for action in permission.actions],
                "controller_enforced": True,
                "executed": False,
                "approval_request": request,
                "observation": _tool_observation(
                    intent=intent,
                    tool_surface="mode_permission_controller",
                    ok=False,
                    status="pending_approval",
                    operating_mode=permission.mode.value,
                    approval_id=str(request.get("approval_id") or ""),
                ),
            },
        )

    receipt_key = ""
    idempotency_key = ""
    # Canonical A6 effect identity is the CALLER-TYPED arguments, before any
    # per-checkpoint idempotency key is folded in for receipt caching. Injecting
    # first would make every fresh turn mint a different logical_effect_id for
    # hive/operator intents (the injection varies by checkpoint), letting a
    # retry/replay of the same intended mutation slip past its own unresolved
    # reservation. Receipt keys are computed from these raw arguments too.
    logical_identity_arguments = dict(arguments)
    if checkpoint_id and (is_mutating_tool_intent(intent) or _registered_mutating_contract(intent)):
        receipt_key = build_tool_receipt_key(
            checkpoint_id=str(checkpoint_id),
            step_index=max(0, int(step_index)),
            intent=intent,
            arguments=arguments,
        )
        cached = load_tool_receipt(receipt_key)
        if cached:
            cached_execution = _execution_from_receipt(cached)
            if cached_execution is not None:
                return cached_execution
        idempotency_key = receipt_key
        arguments = _inject_idempotency_key(intent, arguments, idempotency_key=idempotency_key)

    if intent in _WEB_TOOL_INTENTS:
        execution = _execute_web_tool(intent, arguments, task_id=task_id, source_context=source_context)
        _maybe_store_tool_receipt(
            execution,
            receipt_key=receipt_key,
            session_id=session_id,
            checkpoint_id=checkpoint_id,
            intent=intent,
            arguments=arguments,
            idempotency_key=idempotency_key,
            source_context=source_context,
        )
        return execution
    # ── A6 effect reconciliation (assembly W001) ─────────────────────────
    # A1 authorization already happened above (decide_tool_call / handler
    # gates). A6 now reserves the LOGICAL effect durably BEFORE any physical
    # mutation, and blocks a fresh dispatch of an effect whose outcome is still
    # unresolved. UNKNOWN is never FAILED: the blocked retry below does not
    # claim the effect failed — it claims its truth is not established yet.
    a6_claim = _a6_reserve_before_dispatch(
        intent=intent,
        arguments=logical_identity_arguments,
        task_id=task_id,
        session_id=session_id,
        checkpoint_id=checkpoint_id,
        source_context=source_context,
    )
    if isinstance(a6_claim, ToolIntentExecution):
        return a6_claim

    if a6_claim is not None:
        if not mark_effect_dispatched(
            logical_effect_id=a6_claim["logical_effect_id"],
            effect_instance_id=a6_claim["effect_instance_id"],
            claimed_by=f"{session_id or ''}:{task_id or ''}",
            execution_identity=(source_context or {}).get("_execution_identity")
            if isinstance(source_context, dict)
            else None,
        ):
            return _a6_unknown_blocked_execution(
                intent, str(a6_claim["logical_effect_id"]), active_row=None
            )

    if a6_claim is not None:
        _obligation_register_effect(str(a6_claim["logical_effect_id"]))
    claim_token = None
    if a6_claim is not None:
        claim_token = set_in_flight_effect(
            {
                "tool_name": intent,
                "logical_effect_id": a6_claim["logical_effect_id"],
                "effect_instance_id": a6_claim["effect_instance_id"],
            }
        )
    try:
        # THE ONE authorized execution boundary. The decision above is handed in as the typed
        # object the controller already holds, so nothing here re-decides (no second prompt, no
        # second receipt) -- but the EXECUTION itself crosses the same seam every deterministic
        # fast path crosses, where the cancellation check, the fail-closed decision contract, and
        # the recorded permission verdict live.
        runtime_execution = execute_authorized_runtime_tool(
            intent,
            arguments,
            source_context=source_context,
            trusted_local_only=trusted_local_only,
            permission_decision=permission,
        )
    except EffectOutcomeUnknown as exc:
        # Handler raised AFTER crossing the dispatch boundary: whether the
        # physical effect landed is unprovable -> durable UNKNOWN, never a
        # plain retryable failure.
        if a6_claim is not None:
            classify_effect_outcome(
                logical_effect_id=str(a6_claim["logical_effect_id"]),
                effect_instance_id=str(a6_claim["effect_instance_id"]),
                outcome="unknown",
                reason=exc.reason,
                detail=exc.detail,
            )
        unknown_execution = _a6_unknown_outcome_execution(
            intent,
            exc.reason,
            exc.detail,
            logical_effect_id=str(a6_claim["logical_effect_id"]) if a6_claim else "",
            effect_instance_id=str(a6_claim["effect_instance_id"]) if a6_claim else "",
        )
        _maybe_store_tool_receipt(
            unknown_execution,
            receipt_key=receipt_key,
            session_id=session_id,
            checkpoint_id=checkpoint_id,
            intent=intent,
            arguments=arguments,
            idempotency_key=idempotency_key,
            source_context=source_context,
        )
        return unknown_execution
    finally:
        if claim_token is not None:
            clear_in_flight_effect(claim_token)
    if runtime_execution is not None:
        # ROOT-CAUSE CONTRACT (amendment gap 1) — the approval-continuation
        # writer. This is the one seam every dispatched tool call crosses, so
        # an APPROVED repair's mutation and validation land on the typed
        # diagnosis no matter which lane resumed the turn. Fail-soft and a
        # no-op for ordinary traffic (record_repair_step decides repair shape
        # from concrete intents, never prose).
        try:
            from core.root_cause_contract import record_repair_step as _rcc_step

            _rcc_step(
                source_context,
                intent=intent,
                arguments=arguments,
                ok=bool(runtime_execution.ok),
            )
        except Exception:
            pass
        if a6_claim is not None:
            # Ack point: write the PROVEN outcome. ok -> applied; the typed
            # unknown literal -> unknown; every ordinary failure result was
            # produced before any physical mutation could be attributed to it.
            classify_effect_outcome(
                logical_effect_id=str(a6_claim["logical_effect_id"]),
                effect_instance_id=str(a6_claim["effect_instance_id"]),
                outcome=(
                    "applied"
                    if runtime_execution.ok
                    else "unknown"
                    if str(runtime_execution.status) == UNKNOWN_OUTCOME_STATUS
                    else "failed_safe_to_retry"
                ),
                reason="dispatch_ack",
                detail=f"status={runtime_execution.status}",
            )
            _obligation_discharge_effect(
                str(a6_claim["logical_effect_id"]),
                applied=(
                    True
                    if runtime_execution.ok
                    else None
                    if str(runtime_execution.status) == UNKNOWN_OUTCOME_STATUS
                    else False
                ),
            )
        runtime_details = dict(runtime_execution.details or {})
        runtime_details.setdefault(
            "observation",
            _tool_observation(
                intent=intent,
                tool_surface="runtime_execution",
                ok=runtime_execution.ok,
                status=runtime_execution.status,
                response_preview=str(runtime_execution.response_text or "")[:280],
            ),
        )
        runtime_mode = (
            UNKNOWN_OUTCOME_MODE
            if str(runtime_execution.status) == UNKNOWN_OUTCOME_STATUS
            else "tool_preview"
            if runtime_execution.status in {"user_action_required", "simulate_only"}
            else "tool_executed"
            if runtime_execution.ok
            else "tool_failed"
        )
        # A runtime tool whose contract declares its result IS the answer (the RepoOps
        # control plane's plans, exact results, refusals and uncertainty) hands its own text
        # to the user-safe channel: the exact refusal is published verbatim instead of
        # collapsing into a generic "couldn't turn that into an action" message.
        runtime_contract = None
        try:
            from core.runtime_tool_contracts import runtime_tool_contract_map

            runtime_contract = runtime_tool_contract_map().get(str(intent))
        except Exception:
            runtime_contract = None
        renders_final = bool(getattr(runtime_contract, "renders_final_answer", False))
        safe_text = runtime_execution.response_text if renders_final else ""
        execution = ToolIntentExecution(
            handled=runtime_execution.handled,
            ok=runtime_execution.ok,
            status=runtime_execution.status,
            response_text=runtime_execution.response_text,
            user_safe_response_text=str(safe_text or ""),
            mode=runtime_mode,
            tool_name=intent,
            details=runtime_details,
        )
        if not runtime_execution.ok and str((runtime_details.get("observation") or {}).get("tool_surface") or "") == "email":
            # The email surface authors its refusals for the person (which account, what it lacks, what
            # to do next: reconnect it, choose another, wait for an approval); the same text the model
            # reads is what the turn shows when the refusal ends it. Measured served 2026-09-14: without
            # this, a needs_setup read ended the turn on the generic "I wasn't able to turn that into a
            # completed action" and the reconnect instruction never reached the user.
            execution.user_safe_response_text = str(runtime_execution.response_text or "")
        _maybe_store_tool_receipt(
            execution,
            receipt_key=receipt_key,
            session_id=session_id,
            checkpoint_id=checkpoint_id,
            intent=intent,
            arguments=arguments,
            idempotency_key=idempotency_key,
            source_context=source_context,
        )
        return execution
    if a6_claim is not None:
        # K-06 RESERVE-BEFORE-SYSCALL: the contract fell through to
        # hive/operator/payment, so the reservation carries to THIS actual
        # dispatching lane — it stays ACTIVE and is marked dispatched before
        # the first mutation, then reconciled from the mechanical outcome.
        # Cancel-then-dispatch-bare is the bypass this replaces.

        def _dispatch_with_claim(dispatch):
            from core.runtime_continuity import (
                resolve_unresolved_effect,
            )

            # The canonical claim is ALREADY marked dispatched by the
            # reserve/claim seam above -- mark_effect_dispatched is a strict
            # PREPARED -> DISPATCHED CAS, so a second mark here could never
            # succeed and would strand every fall-through mutation as a false
            # "blocked pending reconciliation". If this wrapper runs at all,
            # the claim holds DISPATCHED state over THIS dispatching lane.
            execution = dispatch()
            try:
                if bool(getattr(execution, "ok", False)):
                    resolution = "CONFIRMED_APPLIED"
                elif str(execution.status or "").strip().lower() == "unknown":
                    return execution  # UNKNOWN-STAYS-UNKNOWN: stays blocking
                else:
                    resolution = "CONFIRMED_FAILED_SAFE_TO_RETRY"
                resolve_unresolved_effect(
                    logical_effect_id=str(a6_claim["logical_effect_id"]),
                    resolution=resolution,
                    source="mechanical",
                    evidence=str(execution.response_text or "")[:500],
                )
                _obligation_discharge_effect(
                    str(a6_claim["logical_effect_id"]),
                    applied=(resolution == "CONFIRMED_APPLIED"),
                )
            except Exception:
                _log.warning(
                    "A6 fall-through reconciliation failed for %s; effect stays unresolved",
                    a6_claim.get("logical_effect_id"),
                    exc_info=True,
                )
            return execution

    else:

        def _dispatch_with_claim(dispatch):
            return dispatch()

    # Only a REGISTERED plugin contract enters the claim wrapper: wrapping a None return for a
    # hive/operator/payment intent would trip the wrapper's reconciliation on the way out.
    plugin_execution = (
        _dispatch_with_claim(
            lambda: _with_blackbox_coverage(
                intent, arguments, source_context=source_context, dispatch=lambda: _execute_plugin_tool(intent, arguments)
            )
        )
        if _is_plugin_contract(intent)
        else None
    )
    if plugin_execution is not None:
        _maybe_store_tool_receipt(
            plugin_execution,
            receipt_key=receipt_key,
            session_id=session_id,
            checkpoint_id=checkpoint_id,
            intent=intent,
            arguments=arguments,
            idempotency_key=idempotency_key,
            source_context=source_context,
        )
        return plugin_execution

    if intent in _HIVE_TOOL_INTENTS:
        execution = _dispatch_with_claim(
            lambda: _execute_hive_tool(
                intent,
                arguments,
                hive_activity_tracker=hive_activity_tracker,
                public_hive_bridge=public_hive_bridge,
            )
        )
        _maybe_store_tool_receipt(
            execution,
            receipt_key=receipt_key,
            session_id=session_id,
            checkpoint_id=checkpoint_id,
            intent=intent,
            arguments=arguments,
            idempotency_key=idempotency_key,
            source_context=source_context,
        )
        return execution
    if intent.startswith("operator.command."):
        # Command-registry model tools: ONLY explicitly model-offerable commands are
        # contracted; the registry executes with principal="model" so operator-only
        # commands refuse at their gate even if invoked here.
        from core.command_registry.model_tools import execute_model_command as _execute_model_command

        execution = _dispatch_with_claim(
            lambda: _execute_model_command(intent, arguments, task_id=task_id, session_id=session_id)
        )
        _maybe_store_tool_receipt(
            execution,
            receipt_key=receipt_key,
            session_id=session_id,
            checkpoint_id=checkpoint_id,
            intent=intent,
            arguments=arguments,
            idempotency_key=idempotency_key,
            source_context=source_context,
        )
        return execution
    if intent in _READ_ONLY_OPERATOR_INTENTS | _MUTATING_OPERATOR_INTENTS:
        execution = _dispatch_with_claim(
            lambda: _with_blackbox_coverage(
                intent,
                arguments,
                source_context=source_context,
                dispatch=lambda: _execute_operator_tool(
                    intent,
                    arguments,
                    task_id=task_id,
                    session_id=session_id,
                    source_context=source_context,
                ),
            )
        )
        _maybe_store_tool_receipt(
            execution,
            receipt_key=receipt_key,
            session_id=session_id,
            checkpoint_id=checkpoint_id,
            intent=intent,
            arguments=arguments,
            idempotency_key=idempotency_key,
            source_context=source_context,
        )
        return execution
    if intent in _PAYMENT_TOOL_INTENTS:
        execution = _dispatch_with_claim(
            lambda: _execute_payment_tool(intent, arguments, source_context=source_context)
        )
        _maybe_store_tool_receipt(
            execution,
            receipt_key=receipt_key,
            session_id=session_id,
            checkpoint_id=checkpoint_id,
            intent=intent,
            arguments=arguments,
            idempotency_key=idempotency_key,
            source_context=source_context,
        )
        return execution

    if is_mcp_intent(intent):
        execution = _with_blackbox_coverage(
            intent, arguments, source_context=source_context, dispatch=lambda: execute_mcp_intent(intent, arguments)
        )
        _maybe_store_tool_receipt(
            execution,
            receipt_key=receipt_key,
            session_id=session_id,
            checkpoint_id=checkpoint_id,
            intent=intent,
            arguments=arguments,
            idempotency_key=idempotency_key,
            source_context=source_context,
        )
        return execution

    audit_logger.log(
        "tool_intent_unsupported",
        target_id=task_id,
        target_type="task",
        details={"intent": intent, "arguments": arguments, "source_context": dict(source_context or {})},
    )
    return _unsupported_execution_for_intent(
        intent,
        status="unsupported",
        extra_details={
            "intent": intent,
            "arguments": arguments,
        },
        user_safe_override="That action is not wired on this runtime yet.",
    )


def _normalize_payload(payload: Any) -> dict[str, Any]:
    return _normalize_payload_impl(payload)


def _inject_idempotency_key(intent: str, arguments: dict[str, Any], *, idempotency_key: str) -> dict[str, Any]:
    return _inject_idempotency_key_impl(intent, arguments, idempotency_key=idempotency_key)


def _record_execution(
    execution: ToolIntentExecution,
    *,
    session_id: str,
    intent: str,
    arguments: dict[str, Any],
    source_context: dict[str, Any] | None = None,
) -> None:
    """Keep what this call actually resolved to and returned, for the answer binder.

    Reads the tool's own declared `claim` block, so the keys to look at come from the tool rather
    than from a table here — which is what lets a plugin's tool be checked with no code change.

    `source_context` is forwarded only so the record carries the turn id. Records are kept per
    SESSION and nothing clears them between turns, so an unstamped record makes "did you call
    GitHub on THIS turn?" unanswerable — the evidence binder would either bind an earlier turn's
    call to this turn's claim, or refuse to answer at all. This is the one place the turn id and
    the record meet.

    Never raises and never touches the execution. Observation must not be able to break a turn.
    """

    try:
        from core.runtime_flags import flag_enabled

        if not flag_enabled("execution_records"):
            return
        from core import execution_records
        from core.tool_registry import claim_for_intent

        details = dict(getattr(execution, "details", None) or {})
        observation = details.get("observation")
        execution_records.record(
            session_id=str(session_id or ""),
            intent=str(intent or ""),
            arguments=dict(arguments or {}),
            observation=observation if isinstance(observation, dict) else {},
            details=details,
            claim=claim_for_intent(str(intent or "")),
            ok=bool(getattr(execution, "ok", False)),
            status=str(getattr(execution, "status", "") or ""),
            source_context=source_context,
        )
    except Exception:
        return


def _maybe_store_tool_receipt(
    execution: ToolIntentExecution,
    *,
    receipt_key: str,
    session_id: str,
    checkpoint_id: str | None,
    intent: str,
    arguments: dict[str, Any],
    idempotency_key: str,
    source_context: dict[str, Any] | None = None,
) -> None:
    # An execution record is written for EVERY call, before the receipt gate below. The two are
    # deliberately different things: a receipt is a replay cache keyed by idempotency (a key hit
    # returns the cached result instead of running the tool, a few lines above at :320), so it is
    # correctly limited to mutating intents. Widening it to reads would make the second "what is
    # in this folder" answer from a stale row while the folder changed underneath.
    #
    # A record is write-only and never replayed. It exists so the answer binder can check that the
    # reply only claims what the tools returned — which for a read-only listing was previously
    # impossible, because nothing kept the result past the turn.
    _record_execution(
        execution,
        session_id=session_id,
        intent=intent,
        arguments=arguments,
        source_context=source_context,
    )

    if not receipt_key or not checkpoint_id:
        return
    store_tool_receipt(
        receipt_key=receipt_key,
        session_id=session_id,
        checkpoint_id=str(checkpoint_id),
        tool_name=intent,
        idempotency_key=idempotency_key,
        arguments=arguments,
        execution=_execution_to_receipt(execution),
    )


def _execution_to_receipt(execution: ToolIntentExecution) -> dict[str, Any]:
    return _execution_to_receipt_impl(execution)


def _execution_from_receipt(receipt: dict[str, Any]) -> ToolIntentExecution | None:
    return _execution_from_receipt_impl(receipt)


def _execute_web_tool(
    intent: str,
    arguments: dict[str, Any],
    *,
    task_id: str,
    source_context: dict[str, Any] | None,
) -> ToolIntentExecution:
    return _execute_web_tool_impl(
        intent,
        arguments,
        task_id=task_id,
        source_context=source_context,
        allow_web_fallback_fn=policy_engine.allow_web_fallback,
        load_builtin_tools_fn=load_builtin_tools,
        planned_search_query_fn=WebAdapter.planned_search_query,
        call_tool_fn=call_tool,
        adaptive_research_fn=CuriosityRoamer().adaptive_research,
        unsupported_execution_for_intent_fn=_unsupported_execution_for_intent,
        tool_observation_fn=_tool_observation,
        audit_log_fn=audit_logger.log,
    )


def _execute_hive_tool(
    intent: str,
    arguments: dict[str, Any],
    *,
    hive_activity_tracker: HiveActivityTracker,
    public_hive_bridge: PublicHiveBridge | None,
) -> ToolIntentExecution:
    from core.voolbook_identity import get_profile, update_profile
    from network.signer import get_local_peer_id

    return _execute_hive_tool_impl(
        intent,
        arguments,
        hive_activity_tracker=hive_activity_tracker,
        public_hive_bridge=public_hive_bridge,
        unsupported_execution_for_intent_fn=_unsupported_execution_for_intent,
        capability_gap_for_intent_fn=capability_gap_for_intent,
        render_capability_truth_response_fn=render_capability_truth_response,
        research_topic_from_signal_fn=research_topic_from_signal,
        audit_log_fn=audit_logger.log,
        get_local_peer_id_fn=get_local_peer_id,
        get_profile_fn=get_profile,
        update_profile_fn=update_profile,
    )


def _execute_hive_list_available(
    hive_activity_tracker: HiveActivityTracker,
    arguments: dict[str, Any],
    *,
    public_hive_bridge: PublicHiveBridge | None,
) -> ToolIntentExecution:
    return _execute_hive_list_available_impl(
        hive_activity_tracker,
        arguments,
        public_hive_bridge=public_hive_bridge,
        capability_gap_for_intent_fn=capability_gap_for_intent,
        render_capability_truth_response_fn=render_capability_truth_response,
    )


def _failed_hive_execution(intent: str, result: dict[str, Any], fallback: str) -> ToolIntentExecution:
    return _failed_hive_execution_impl(intent, result, fallback)


def _execute_operator_tool(
    intent: str,
    arguments: dict[str, Any],
    *,
    task_id: str,
    session_id: str,
    source_context: dict[str, Any] | None = None,
) -> ToolIntentExecution:
    return _execute_operator_tool_impl(
        intent,
        arguments,
        task_id=task_id,
        session_id=session_id,
        dispatch_operator_action_fn=dispatch_operator_action,
        source_context=source_context,
    )


def _execute_payment_tool(
    intent: str,
    arguments: dict[str, Any],
    *,
    source_context: dict[str, Any] | None,
) -> ToolIntentExecution:
    return _execute_payment_tool_impl(intent, arguments, source_context=source_context)


def _build_operator_action_intent(operator_kind: str, arguments: dict[str, Any]) -> OperatorActionIntent:
    return _build_operator_action_intent_impl(operator_kind, arguments)


def _normalize_item(item: Any) -> dict[str, Any]:
    return _normalize_item_impl(item)
