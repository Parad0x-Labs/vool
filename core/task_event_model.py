"""Typed task-event model for the live execution UI.

Maps raw runtime-bus events (see ``core/runtime_task_events.py`` /
``core/runtime_continuity.py``) into a small, typed, user-safe event shape that
the /chat status card and execution panel consume. Design rules:

* **Allowlist only.** Unknown event types map to ``None`` and are dropped, so no
  unexpected text -- and no model chain-of-thought -- can reach the UI channel.
* **The final answer is not a task event.** ``model_output_chunk`` (the answer
  deltas) maps to ``None``; it stays in the normal answer stream.
* **Safe summaries only.** We copy the already-curated ``summary``/``message``
  fields (tool names, one-line step summaries, lane/provider ids), clipped to a
  single short line -- never raw reasoning.
* **A failure states its cause.** Failure-shaped events carry a ``diagnostics``
  block (``reason``, ``error_kind``, ``provider_health_recorded`` and a compact
  prompt-budget summary), key-allowlisted and sanitised on the way out.

The conversation card and the side panel both render from this one shape, so
they cannot disagree (that is the whole point of a single typed source).
"""

from __future__ import annotations

import re
from typing import Any

# --- Section-2 execution stages -------------------------------------------------
STAGE_UNDERSTANDING = "Understanding"
STAGE_PLANNING = "Planning"
STAGE_SEARCHING = "Searching"
STAGE_READING = "Reading"
STAGE_EDITING = "Editing"
STAGE_RUNNING = "Running"
STAGE_TESTING = "Testing"
STAGE_VERIFYING = "Verifying"
STAGE_WAITING_PERMISSION = "Waiting for permission"
STAGE_REPAIRING = "Repairing"
STAGE_COMPLETED = "Completed"
STAGE_FAILED = "Failed safely"
STAGE_CANCELLED = "Cancelled"

_SAFE_SUMMARY_MAX = 200
_DIAGNOSTIC_KIND_MAX = 64

# Reasons are not authored for a reader: the router's general failure branch emits
# ``reason=str(exc)`` (core/memory_first_router.py), and an adapter's exception carries whatever
# the raising code interpolated -- ``response.raise_for_status()`` alone puts the provider URL,
# query string included, in the text. Same rule and same reason as core/error_surface.py: keep the
# part that names the failure, drop the part that names the operator's machine or a credential.
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
# The lookbehind is load-bearing: without it the path rule bites into a slashed model id --
# "openrouter/anthropic/claude-x" contains "/anthropic/claude-x", which reads as a path and would
# have been rewritten to "openrouterclaude-x", corrupting the one identifier a reader needs.
_POSIX_PATH_RE = re.compile(r"(?<![\w/])(?:~|/[^/\s\"']+)(?:/[^/\s\"']+)+/?")
_WINDOWS_PATH_RE = re.compile(r"[A-Za-z]:\\(?:[^\\\s\"']+\\)*[^\\\s\"']*")

# tool-name substrings -> stage. Order matters (first match wins).
_TOOL_STAGE_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("search", "research", "web", "lookup", "browse", "find", "grep", "index"), STAGE_SEARCHING),
    (("read", "fetch", "open", "inspect", "view", "cat", "load"), STAGE_READING),
    (("edit", "write", "patch", "apply", "create_file", "modify", "format"), STAGE_EDITING),
    (("test", "pytest", "verify", "assert"), STAGE_TESTING),
    (("run", "exec", "start", "server", "preview", "build", "shell", "command", "install"), STAGE_RUNNING),
)


def _clip(text: str, limit: int = _SAFE_SUMMARY_MAX) -> str:
    single = " ".join(str(text or "").split())
    return single if len(single) <= limit else single[: limit - 1].rstrip() + "…"


def _to_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def stage_for_tool(tool_name: str) -> str:
    lowered = str(tool_name or "").lower()
    for needles, stage in _TOOL_STAGE_RULES:
        if any(needle in lowered for needle in needles):
            return stage
    return STAGE_RUNNING


def _model_info(event: dict[str, Any]) -> dict[str, Any] | None:
    # Identity authority is intentionally one-way and never inferred across tiers:
    #
    # * provider_id/model_id (or model_name) are generic RECORDED identity only.
    # * requested_* exists only when request provenance supplied those exact fields.
    # * planned_* is policy output and remains separately labelled as policy.
    # * selected_* exists only when router/manifest-selection provenance supplied it.
    # * actual_adapter_* exists only when adapter execution proof supplied it.
    #
    # Presentation may prefer actual > selected > requested/policy > recorded > unknown,
    # but that preference must never manufacture or relabel a stronger evidence field.
    lane_proof_raw = event.get("lane_proof")
    lane_proof = lane_proof_raw if isinstance(lane_proof_raw, dict) else {}
    lane = str(event.get("lane") or "").strip()
    lane_type = str(event.get("lane_type") or "").strip()
    cost_class = str(event.get("cost_class") or "").strip()
    provider = str(event.get("provider_id") or "").strip()
    model_id = str(event.get("model_id") or event.get("model_name") or "").strip()
    requested_provider = str(event.get("requested_provider_id") or lane_proof.get("requested_provider_id") or "").strip()
    requested_model = str(
        event.get("requested_model_id")
        or event.get("requested_model")
        or lane_proof.get("requested_model_id")
        or lane_proof.get("requested_model")
        or ""
    ).strip()
    policy_provider = str(event.get("planned_provider_id") or lane_proof.get("planned_provider_id") or "").strip()
    policy_model = str(event.get("planned_model_id") or lane_proof.get("planned_model_id") or "").strip()
    selected_provider = str(event.get("selected_provider_id") or lane_proof.get("selected_provider_id") or "").strip()
    selected_model = str(
        event.get("selected_model_id")
        or event.get("selected_model")
        or lane_proof.get("selected_model_id")
        or lane_proof.get("selected_model")
        or ""
    ).strip()
    actual_provider = str(
        event.get("actual_adapter_provider_id") or lane_proof.get("actual_adapter_provider_id") or ""
    ).strip()
    actual_model = str(
        event.get("actual_adapter_model_id") or lane_proof.get("actual_adapter_model_id") or ""
    ).strip()
    locality = str(event.get("locality") or lane_proof.get("locality") or "").strip().lower()
    model_short = model_id.split("/")[-1].strip() if model_id else ""
    if not any(
        (
            lane,
            lane_type,
            cost_class,
            provider,
            model_short,
            requested_provider,
            requested_model,
            policy_provider,
            policy_model,
            selected_provider,
            selected_model,
            actual_provider,
            actual_model,
            locality,
        )
    ):
        return None
    blob = f"{lane} {lane_type} {cost_class}".lower()
    # Cloud is a locality, not a billing verdict: verified-free OpenRouter lanes report
    # ``free_cloud`` and must never light the paid indicator.
    is_paid = "paid" in blob
    result = {
        "lane": lane or None,
        "paid": is_paid,
        "provider_id": provider or None,
        "model_id": model_short or None,
    }
    projected_identity = {
        "requested_provider_id": requested_provider,
        "requested_model_id": requested_model,
        "policy_provider_id": policy_provider,
        "policy_model_id": policy_model,
        "selected_provider_id": selected_provider,
        "selected_model_id": selected_model,
        "actual_adapter_provider_id": actual_provider,
        "actual_adapter_model_id": actual_model,
        "locality": locality,
    }
    result.update({key: value for key, value in projected_identity.items() if value})
    for key in ("model_call_id", "response_id"):
        value = str(event.get(key) or "").strip()
        if value:
            result[key] = value
    return result


def _cost_info(event: dict[str, Any]) -> dict[str, Any] | None:
    cost_class = str(event.get("cost_class") or "").strip()
    usd_actual = _to_float(event.get("usd_actual"))
    output_tokens = _to_int(event.get("output_tokens"))
    prompt_tokens = _to_int(event.get("prompt_tokens"))
    if not (cost_class or usd_actual is not None or output_tokens is not None or prompt_tokens is not None):
        return None
    result = {
        "cost_class": cost_class or None,
        "paid": "paid" in cost_class.lower(),
        "usd_actual": usd_actual,
        "output_tokens": output_tokens,
        "prompt_tokens": prompt_tokens,
    }
    for key in ("model_call_id", "response_id"):
        value = str(event.get(key) or "").strip()
        if value:
            result[key] = value
    return result


def _basename(match: re.Match[str], separator: str) -> str:
    tail = match.group(0).rstrip(separator).rsplit(separator, 1)[-1]
    return tail or "<path>"


def _safe_diagnostic_text(value: Any, *, limit: int) -> str:
    """One sanitised line of failure text: secrets masked, URLs and absolute paths removed."""
    from core.secret_redaction import redact_secrets

    text = redact_secrets(str(value or ""))
    # The lane already ships ``provider_id``, so the endpoint URL adds no diagnosis -- only the
    # chance of a credential riding in its query string.
    text = _URL_RE.sub("<url>", text)
    text = _POSIX_PATH_RE.sub(lambda match: _basename(match, "/"), text)
    text = _WINDOWS_PATH_RE.sub(lambda match: _basename(match, "\\"), text)
    return _clip(text, limit)


# ``prompt_budget`` telemetry is 13+ keys wide and the rejecting adapter adds the raw exception
# text under ``error`` (adapters/openai_compatible_adapter.py), so this copies by KEY ALLOWLIST,
# never by merge: a merge would put that free text -- and every key added later -- on the wire to
# the browser. What survives is what a person needs to act on a rejection: the window, what was
# left for the prompt, what the prompt still needed, and what was already thrown away trying.
_PROMPT_BUDGET_INT_FIELDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("num_ctx",), "num_ctx"),
    (("available_prompt_tokens",), "available_prompt_tokens"),
    # ``estimated_prompt_tokens_after`` is the ledger's name for the size the prompt still needed
    # once every shed had run; read against available_prompt_tokens it IS the rejection, so the UI
    # field says that out loud. ``final_input_tokens`` is the same number under the gateway's name.
    (("estimated_prompt_tokens_after", "final_input_tokens"), "required_prompt_tokens"),
    (("dropped_history_messages",), "dropped_history_messages"),
    (("dropped_retrieved_messages",), "dropped_retrieved_messages"),
    (("dropped_context_summaries",), "dropped_context_summaries"),
)
_PROMPT_BUDGET_BOOL_FIELDS: tuple[str, ...] = ("dropped_memory", "system_prompt_preserved")


def _prompt_budget_summary(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict) or not value:
        return None
    summary: dict[str, Any] = {}
    status = _clip(str(value.get("status") or "").strip(), 32)
    if status:
        summary["status"] = status
    for sources, name in _PROMPT_BUDGET_INT_FIELDS:
        for source in sources:
            number = _to_int(value.get(source))
            if number is not None:
                summary[name] = number
                break
    for name in _PROMPT_BUDGET_BOOL_FIELDS:
        if value.get(name) is not None:
            summary[name] = bool(value[name])
    return summary or None


def _diagnostics_info(event: dict[str, Any]) -> dict[str, Any] | None:
    """The cause of a failure, in the shape the failure card can render.

    An audit turn lost three provider lanes at once and showed the user three failures with no
    cause: the router had emitted reason/error_kind/prompt_budget on every one, the ledger kept
    them, and this projection -- a hand-written output dict -- simply never read them, so tracing
    it took hours. Fields are read here or they do not exist for the UI.
    """
    result: dict[str, Any] = {}
    # `reason` is one family's name for the cause, not the runtime's. Reading it alone left the
    # families that name it differently with NO diagnostics block at all -- `model_lane_failed`
    # carries `error`/`fallback_reason`, `model_routing_failed` carries `rejection_reason`, and a
    # retrieval receipt carries `failure_reason`. That is the operator-reported row: a local lane
    # died on a read timeout and the card showed a bare "Model call failed" over a payload that
    # already held the timeout string. First key present wins; all of them are sanitised the same
    # way, so widening the read does not widen what can leak.
    reason = ""
    for field in ("reason", "error", "fallback_reason", "failure_reason", "rejection_reason"):
        reason = _safe_diagnostic_text(event.get(field), limit=_SAFE_SUMMARY_MAX)
        if reason:
            break
    if reason:
        result["reason"] = reason
    error_kind = _safe_diagnostic_text(event.get("error_kind"), limit=_DIAGNOSTIC_KIND_MAX)
    if error_kind:
        result["error_kind"] = error_kind
    # The TYPED class, which is the one token separating a timeout from a refusal from a malformed
    # tool call -- and the exception type behind a cause the runtime could not classify at all.
    for source, name in (("error_class", "error_class"), ("failure_class", "error_class"),
                         ("exception_class", "exception_class")):
        if name in result:
            continue
        text = _safe_diagnostic_text(event.get(source), limit=_DIAGNOSTIC_KIND_MAX)
        if text:
            result[name] = text
    if event.get("retryable") is not None:
        result["retryable"] = bool(event["retryable"])
    # False is the interesting value here (a prompt-shape rejection deliberately leaves provider
    # health untouched so a healthy provider's circuit stays closed), so presence, not truth, gates.
    if event.get("provider_health_recorded") is not None:
        result["provider_health_recorded"] = bool(event["provider_health_recorded"])
    prompt_budget = _prompt_budget_summary(event.get("prompt_budget"))
    if prompt_budget is not None:
        result["prompt_budget"] = prompt_budget
    return result or None


# raw event_type -> (typed type, stage or None, status or None). stage=None means
# "do not change the card's current stage" (e.g. a model swap mid-work).
_DIRECT_MAP: dict[str, tuple[str, str | None, str | None]] = {
    "usepod_price_paused": ("task.price_paused", "Waiting for price", "paused"),
    "usepod_price_resumed": ("task.price_resumed", "Resuming", "running"),
    "task_classified": ("task.stage_changed", STAGE_UNDERSTANDING, "running"),
    "task_received": ("task.started", STAGE_UNDERSTANDING, "running"),
    "model_routing_started": ("task.stage_changed", STAGE_PLANNING, "running"),
    "model.selection_started": ("model.selection_started", None, "running"),
    "model.selected": ("model.selected", None, "running"),
    "model.switch_started": ("model.switch_started", None, "running"),
    "model.switch_completed": ("model.switch_completed", None, "completed"),
    "model.escalation_evaluated": ("model.escalation_evaluated", None, "completed"),
    "model.escalation_proposed": ("model.escalation_proposed", STAGE_WAITING_PERMISSION, "pending_approval"),
    "model.escalation_approved": ("model.escalation_approved", None, "completed"),
    "model.escalation_denied": ("model.escalation_denied", None, "completed"),
    "model.paid_started": ("model.paid_started", None, "running"),
    "model.cost_updated": ("cloud.cost_updated", None, None),
    "model.paid_completed": ("model.paid_completed", None, "completed"),
    "model.paid_failed": ("model.paid_failed", STAGE_REPAIRING, "failed"),
    "model.returning_local": ("model.returning_local", None, "running"),
    "model.local_resumed": ("model.local_resumed", None, "completed"),
    "model.call_started": ("model.call_started", None, "running"),
    "model.call_completed": ("model.call_completed", None, "completed"),
    "model.call_failed": ("model.call_failed", STAGE_REPAIRING, "failed"),
    "workflow_planner_step": ("plan.step_started", STAGE_PLANNING, "running"),
    "workflow_planner_stop": ("plan.step_completed", STAGE_PLANNING, "running"),
    "tool_selected": ("tool.started", None, "running"),
    "tool_executed": ("tool.completed", None, "completed"),
    "tool_failed": ("tool.failed", STAGE_REPAIRING, "failed"),
    "tool_repeat_blocked": ("task.stage_changed", STAGE_REPAIRING, "running"),
    "tool_fallback_to_research": ("task.stage_changed", STAGE_SEARCHING, "running"),
    "tool_synthesizing": ("task.stage_changed", STAGE_VERIFYING, "running"),
    "tool_loop_completed": ("task.stage_changed", STAGE_VERIFYING, "running"),
    "tool_preview": ("permission.required", STAGE_WAITING_PERMISSION, "pending_approval"),
    "task_pending_approval": ("permission.required", STAGE_WAITING_PERMISSION, "pending_approval"),
    "model_lane_selected": ("model.changed", None, "running"),
    "model_lane_started": ("model.changed", None, "running"),
    "model_lane_completed": ("model.changed", None, "running"),
    "model_lane_failed": ("task.stage_changed", STAGE_REPAIRING, "running"),
    "model_lane_contract_failed": ("task.stage_changed", STAGE_REPAIRING, "running"),
    "model_usage": ("cloud.cost_updated", None, None),
    "model_lane_verifier_started": ("verification.started", STAGE_VERIFYING, "running"),
    "model_lane_verifier_completed": ("verification.completed", STAGE_VERIFYING, "completed"),
    # A flagged verdict means the reviewer executed successfully. Its negative verdict belongs in
    # review_state, not in the generic execution status that other consumers may treat as a task
    # failure. Infrastructure outcomes retain their own execution-health status below.
    "model_lane_verifier_flagged": ("verification.completed", STAGE_VERIFYING, "completed"),
    "model_lane_verifier_failed": ("verification.completed", STAGE_VERIFYING, "failed"),
    "model_lane_verifier_blocked": ("verification.completed", STAGE_VERIFYING, "blocked"),
    "model_lane_verifier_degraded": ("verification.completed", STAGE_VERIFYING, "degraded"),
    # Emitted by the chat-stream tail only, in the real commit-assembly window between the last
    # answer byte and the terminal response-commit frame. Stage stays None so it can never read as
    # a new execution stage; the card keeps its current stage while the answer ships.
    "task_finalizing": ("task.finalizing", None, "running"),
    "task_completed": ("task.completed", STAGE_COMPLETED, "completed"),
    "task_failed": ("task.failed", STAGE_FAILED, "failed"),
    "task_interrupted": ("task.cancelled", STAGE_CANCELLED, "cancelled"),
    "task_cancelled": ("task.cancelled", STAGE_CANCELLED, "cancelled"),
    "task_restored": ("task.restored", STAGE_WAITING_PERMISSION, "restored"),
    "mode_changed": ("mode.changed", None, "completed"),
    "bypass_activated": ("mode.changed", None, "completed"),
    "bypass_revoked": ("mode.changed", None, "completed"),
    "permission_approved": ("permission.resolved", None, "completed"),
    "permission_denied": ("permission.resolved", None, "failed"),
    # Finding D, 2026-08-04: the visible step counter was driven exclusively by the deterministic
    # evidence-collection events above (list_tree, manifest reads, ...) and had NO entry for the
    # MODEL-DRIVEN ledger `_emit_call_ledger` (`core/agent_runtime/stepped_audit.py`) emits for its
    # own nomination/challenge/prove/synthesize calls -- so a real audit that hit a genuine 180s
    # model timeout on its nomination call showed a counter that had already frozen before that
    # call even started. The counter was 100% real for what it counted; it was structurally blind
    # to whether the model-driven verification step ran, succeeded, timed out, or failed. Default
    # status here is the common case (`build_task_event` below refines it from the ledger row's own
    # `result` field, since one event type covers both a successful and a failed call).
    "audit_step": ("tool.completed", None, "completed"),
    "audit_budget_refused": ("tool.failed", STAGE_REPAIRING, "failed"),
    # Lane node lifecycle (live_data + conductor runners): the REAL fetch/execution rows, emitted
    # live around the actual work. Dropping them here is why a turn that genuinely fetched weather
    # or market data reached the client as task.started -> task.completed with nothing between --
    # the companion's typed activity language had no events to narrate and sat on generic status
    # while real work ran (measured live 2026-08-29, audited 2026-08-31). The served classifier
    # has a dedicated arm reading these frames through the SAME tool-rule table by the node's
    # declared operation. The completion row's own lifecycle state decides success or failure --
    # resolved in build_task_event below; the map alone cannot express it.
    "agent_node_started": ("agent_node_started", None, "running"),
    "agent_node_completed": ("agent_node_completed", None, None),
}

_REVIEW_EXECUTION_STATE_BY_EVENT: dict[str, str] = {
    "model_lane_verifier_started": "running",
    "model_lane_verifier_completed": "passed",
    "model_lane_verifier_flagged": "flagged",
    "model_lane_verifier_blocked": "blocked",
    "model_lane_verifier_degraded": "degraded",
    "model_lane_verifier_failed": "runtime_failed",
}

_DEFAULT_SUMMARY: dict[str, str] = {
    "task.started": "Understanding the request",
    "task.stage_changed": "",
    "tool.started": "Working",
    "verification.started": "Verifying the result",
    "permission.required": "Waiting for your approval",
    "task.completed": "Complete",
    "task.failed": "Stopped safely",
    "task.cancelled": "Cancelled",
}


def build_task_event(event: dict[str, Any]) -> dict[str, Any] | None:
    """Map one raw runtime-bus event to a typed, user-safe UI event.

    Returns ``None`` for the answer stream (``model_output_chunk``) and for any
    event type not on the allowlist -- dropping is the safe default.
    """
    event_type = str(event.get("event_type") or "").strip()
    if not event_type or event_type == "model_output_chunk":
        return None
    mapped = _DIRECT_MAP.get(event_type)
    if mapped is None:
        return None
    typed, stage, status = mapped

    # `audit_step` covers both a successful and a failed model call under one event type -- the
    # ledger row's own `result` field (see `AuditCall.as_dict`, "answered" vs. "error:..." vs.
    # "rejected:...") is the ground truth for which one this was, not a guess made here.
    if event_type == "audit_step":
        result_text = str(event.get("result") or "").strip().lower()
        if result_text.startswith("error:") or result_text.startswith("rejected:"):
            typed, status, stage = "tool.failed", "failed", STAGE_REPAIRING

    # `agent_node_completed` covers a node's whole terminal truth under one event type -- the
    # row's own lifecycle `state` (and `failure_reason`/`ok` when a lane omits `state`) is the
    # ground truth for which one it was. A failed, cancelled or never-attempted node must never
    # be upgraded to a successful completion here.
    if event_type == "agent_node_completed":
        state = str(event.get("state") or "").strip().lower()
        node_failure = str(event.get("failure_reason") or "").strip()
        if state == "succeeded" and not node_failure:
            status = "completed"
        elif state in {"failed", "unresolved"} or node_failure:
            status = "failed"
        elif state in {"cancelled", "skipped", "dependency_failed"}:
            status = "cancelled"
        elif not state:
            # No lifecycle vocabulary on the row: fall back to the boolean, else make no claim.
            status = "completed" if event.get("ok") is True else None
        else:
            status = None  # unknown state vocabulary: make no claim rather than invent one

    tool = str(event.get("tool_name") or "").strip() or None
    if tool is None and event_type in {"audit_step", "audit_budget_refused"}:
        purpose = str(event.get("purpose") or "").strip()
        tool = f"audit.{purpose}" if purpose else "audit.step"
    if tool is None and typed in {"agent_node_started", "agent_node_completed"}:
        # The node's declared operation is the work name the classifier reads through the SAME
        # tool-rule table. It is an operation code, not free text: clipped, never projected.
        tool = _clip(str(event.get("operation") or "").strip(), 120) or None
    if typed in {"tool.started", "tool.completed", "tool.failed",
                 "agent_node_started", "agent_node_completed"} and tool and stage is None:
        stage = stage_for_tool(tool)

    summary = _clip(str(event.get("summary") or event.get("message") or "").strip())
    if not summary:
        summary = _DEFAULT_SUMMARY.get(typed, "")
        if not summary and tool:
            summary = f"{tool}"

    out: dict[str, Any] = {
        "type": typed,
        "raw_type": event_type,
        "stage": stage,
        "summary": summary,
        "tool": tool,
        "status": status,
    }
    review_state = _REVIEW_EXECUTION_STATE_BY_EVENT.get(event_type)
    if review_state is not None:
        # This is the authoritative causal dimension for reviewer execution. In particular,
        # blocked/degraded/runtime_failed mean no reviewer verdict was established.
        out["review_state"] = review_state

    model = _model_info(event)
    if model is not None:
        out["model"] = model
    cost = _cost_info(event)
    if cost is not None:
        out["cost"] = cost

    # Failure-shaped means: the mapping says this ended failed, OR it parks the card on
    # "Repairing" (model_lane_failed, model_lane_contract_failed and tool_repeat_blocked stay
    # `running` because the router expects to recover -- the user still just watched a lane die and
    # is owed the cause). Non-failures are left byte-identical: a lane that succeeds has no cause
    # to report, and every extra field on the hot path is a field that can leak.
    if status == "failed" or stage == STAGE_REPAIRING or review_state in {"blocked", "degraded"}:
        diagnostics = _diagnostics_info(event)
        if diagnostics is not None:
            out["diagnostics"] = diagnostics

    for key in ("path", "file_path", "target_path"):
        path = str(event.get(key) or "").strip()
        if path:
            out["path"] = path
            break

    current = _to_int(event.get("current"))
    total = _to_int(event.get("total"))
    if current is not None or total is not None:
        out["measurable"] = {"current": current, "total": total}

    seq = _to_int(event.get("seq"))
    if seq is not None:
        out["seq"] = seq
    if typed in {"mode.changed", "task.restored"}:
        active_mode = str(event.get("active_mode") or event.get("operating_mode") or "").strip()
        if active_mode:
            out["active_mode"] = active_mode
        expires_at = _to_float(event.get("expires_at"))
        if expires_at is not None:
            out["expires_at"] = expires_at
    if typed == "permission.required":
        from core.secret_redaction import redact_secrets

        request = dict(event.get("approval_request") or {})
        if request:
            out["approval"] = {
                "approval_id": str(request.get("approval_id") or "")[:128],
                "task_id": str(request.get("task_id") or "")[:128],
                "intent": str(request.get("intent") or "")[:160],
                "action": _clip(str(request.get("action") or "")),
                "affected_resources": [
                    _clip(str(item), limit=240)
                    for item in list(request.get("affected_resources") or [])[:12]
                    if str(item).strip()
                ],
                "expected_side_effects": _clip(str(request.get("expected_side_effects") or "")),
                "reversible": bool(request.get("reversible", False)),
                "scope_options": [
                    str(item)
                    for item in list(request.get("scope_options") or [])
                    if str(item) in {"once", "task", "project", "request"}
                ],
                # The concrete files a "request" grant would cover, so the button names a list the
                # operator can read rather than a number they have to trust.
                "planned_actions": [
                    {
                        "intent": str(item.get("intent") or "")[:160],
                        "target": _clip(str(item.get("target") or ""), limit=240),
                    }
                    for item in list(request.get("planned_actions") or [])[:32]
                    if isinstance(item, dict)
                ],
                "planned_action_count": max(0, int(request.get("planned_action_count") or 0)),
                # The same shape for what the grant REFUSES out of the same plan. Dropping it here
                # would leave the surface unable to say a delete or a command was excluded, which is
                # the one thing an operator needs before widening a click from one action to N.
                "excluded_actions": [
                    {
                        "intent": str(item.get("intent") or "")[:160],
                        "target": _clip(str(item.get("target") or ""), limit=240),
                    }
                    for item in list(request.get("excluded_actions") or [])[:32]
                    if isinstance(item, dict)
                ],
                "planned_action_label": (
                    str(request.get("planned_action_label") or "planned changes")[:64]
                ),
                "diff_preview": redact_secrets(str(request.get("diff_preview") or ""))[:6000],
            }
    return out
