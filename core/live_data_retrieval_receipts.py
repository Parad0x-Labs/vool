"""Typed receipts for the live-data plan lane's remote fetches.

`core.retrieval_observability` receipts adaptive research and the reasoning fallback; `core.fresh_data.fx`
receipts direct FX. The live-data typed plan was the remaining lane that really reaches the network --
proven live on 2026-08-13: a Local Only turn answered "Berlin: Clear, 23 C ... Source: wttr.in" while its
terminal trace reported `web_calls: 0` and zero receipts of every family. Only the `live_data_plan_*`
events recorded that anything had been fetched at all.

That is a truthfulness defect in current-turn accounting on its own, and it also weakens a gate: the
release harness treats a non-zero `web_calls` and any non-empty receipt list as evidence of prohibited
retrieval, so a lane that fetches while reporting zero can never trip it.

This module converts already-executed subtask outcomes into the same receipt shape the other lanes
publish. It reports what the plan did; it does not decide whether the plan was allowed to run -- that
authority stays upstream in `evaluate_approval_policy` and the retrieval constraints.

P1 (2026-09-01) — receipt coverage is DERIVED, and the lifecycle is typed
-----------------------------------------------------------------------
The remote-operation set used to be a manual frozenset. It drifted twice: its first version listed
three operation names that never existed (invented names fail silently -- they simply never match),
and then `water_temperature` landed in the runner's dispatch WITHOUT being added here, so real remote
work (open-meteo geocoding + the marine API) shipped with zero retrieval receipts, zero Activity tool
steps, and no operation-level lifecycle record. Manual lists drift every time a new tool lands.

Coverage is now derived from the typed capability metadata an operation must declare anyway --
`OperationCapability.effect_class == "remote_fetch"` (`core.conductor.capabilities`), the same law
`core.conductor.evidence` already applies to observation effects. A capability registered tomorrow
with that declaration is receipted with no edit to this module. The reverse direction is enforced by
`unclassified_dispatched_operations`: an operation the runner dispatches with no declared effect
class is reported, so the drift that produced the water-temperature gap makes CI red instead of
shipping silently.

Beside the preserved retrieval-receipt family, every dispatched remote operation now also leaves a
TYPED LIFECYCLE receipt (`vool.live_data_operation_receipt.v1`): started / succeeded / failed /
refused / cancelled, naming the request, turn, attempt, provider and operation it belongs to. The
retrieval family reports what was RETRIEVED (a refused subtask keeps the shape it always had); the
lifecycle family reports what was ATTEMPTED -- a refusal is a first-class fact there, never a silent
absence. Neither family ever dresses a failed or refused operation as a successful observation: the
lifecycle receipts carry their own structural failure signals (`ok`, `reason`, `source_count`-free
shape read by `core.observation_evidence` exactly like the retrieval family's).
"""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime, timezone
from typing import Any

#: The transport-class declaration a live-data operation must carry for its remote work to be
#: receipted. Derived, never hand-listed -- see the module docstring.
from core.conductor.capabilities import REMOTE_FETCH_EFFECT_CLASS
from core.runtime_task_events import emit_runtime_event


def remote_fetch_operations() -> frozenset[str]:
    """Every registered capability whose declared effect class is a remote fetch.

    Derived from the registry's own metadata (`OperationCapability.effect_class`), so a newly
    registered remote capability is covered the moment it declares the class, and a declassified
    one stops being receipted the moment it does not. Registration is idempotent
    (`register_slice_1_operations`, the same call `core.conductor.planner` makes at import), so
    this derivation is self-sufficient in a process that never imported the planner.
    """
    import core.conductor.operations as _operations
    from core.conductor.registry import known_operations

    _operations.register_slice_1_operations()
    return frozenset(
        spec.name
        for spec in known_operations()
        if str(getattr(getattr(spec, "capability", None), "effect_class", "") or "")
        == REMOTE_FETCH_EFFECT_CLASS
    )


_DISPATCH_BRANCH_RE = re.compile(r"subtask\.operation\s*==\s*\"([a-z0-9_]+)\"")


def unclassified_dispatched_operations(dispatch_source: str) -> list[str]:
    """Operations a dispatch source branches on that declare no effect class.

    This is the completeness invariant's detector: parse the runner's real `_run_one` source (the
    same binding-by-source the previous manual-set test used), and report every dispatched
    operation that is either unregistered or registered without an `effect_class`. A remote
    operation added to the runner without capability metadata appears here, which is what makes
    the drift CI-red instead of silent.
    """
    names: list[str] = []
    for name in dict.fromkeys(_DISPATCH_BRANCH_RE.findall(str(dispatch_source or ""))):
        spec = None
        try:
            import core.conductor.operations as _operations
            from core.conductor.registry import operation_spec

            _operations.register_slice_1_operations()
            spec = operation_spec(name)
        except Exception:
            spec = None
        effect_class = str(getattr(getattr(spec, "capability", None), "effect_class", "") or "")
        if not effect_class:
            names.append(name)
    return names


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _operation(outcome: Any) -> str:
    subtask = getattr(outcome, "subtask", None)
    return str(getattr(subtask, "operation", "") or "").strip().lower()


def _requested(outcome: Any) -> str:
    subtask = getattr(outcome, "subtask", None)
    arguments = dict(getattr(subtask, "arguments", None) or {})
    for key in ("location", "asset_name", "asset_key", "place", "requested_text"):
        value = str(arguments.get(key) or "").strip()
        if value:
            return value
    return ""


def receipt_for_outcome(outcome: Any, *, plan_id: str) -> dict[str, Any] | None:
    """One receipt for one executed remote subtask, or None when it never left the machine."""

    operation = _operation(outcome)
    if operation not in remote_fetch_operations():
        return None
    state = str(getattr(getattr(outcome, "state", None), "value", "") or "").strip().lower()
    if state in {"skipped", "blocked", "pending", "planned"}:
        # Refused before the network, so nothing was retrieved and nothing may be reported as such.
        return None
    failure = str(getattr(outcome, "failure_reason", "") or "")
    if failure.startswith("rejected implausible location before fetch"):
        return None
    requested = _requested(outcome)
    ok = bool(getattr(outcome, "ok", False))
    # The source this retrieval actually read, from the result's own `source_url` (runtime-owned,
    # never parsed from prose), and the plan node it served -- so a consumer counting unique
    # sources can name this one and join it to the node's completion row instead of counting the
    # same lookup twice.
    result = getattr(outcome, "result", None)
    source_host = ""
    if ok and isinstance(result, dict):
        try:
            from urllib.parse import urlparse

            source_host = str(urlparse(str(result.get("source_url") or "")).hostname or "")
        except Exception:
            source_host = ""
    subtask = getattr(outcome, "subtask", None)
    return {
        "schema": "vool.web_retrieval_receipt.v1",
        "retrieval_id": f"web-retrieval-{uuid.uuid4().hex}",
        "kind": f"live_data_{operation}",
        "action": operation,
        "task_id": str(plan_id or "")[:160],
        "subtask_id": str(getattr(subtask, "subtask_id", "") or "")[:160],
        # The subject is hashed for the same reason the other lanes hash their query: a receipt is
        # provenance, not a second copy of the user's text.
        "query_hash": hashlib.sha256(requested.encode("utf-8")).hexdigest(),
        "status": "available" if ok else ("failed" if failure else "unavailable"),
        "source_count": 1 if ok else 0,
        "source_domains": [source_host] if source_host else [],
        "started_at": _utc_now(),
        "completed_at": _utc_now(),
        "failure_class": "" if ok else failure[:120],
    }


#: Outcome states that were never dispatched as remote work: no fetch was attempted, refused or
#: otherwise, so the lifecycle channel reports nothing for them (it reports ATTEMPTS, not plans).
_NEVER_DISPATCHED_STATES = frozenset(
    {"planned", "pending", "approved", "skipped", "blocked", "unsupported_entity"}
)


def _operation_lifecycle(state: str, failure: str) -> str:
    """The typed terminal vocabulary for one dispatched remote operation outcome.

    `refused` is reserved for work that was stopped before any network attempt: an approval
    denial, or the weather lane's explicit implausible-location guard. Everything else that ended
    badly is `failed` -- the attempt really ran. `started` is the honest state of an operation
    still in flight; an unrecognized state is `unknown`, never rounded up to a terminal.
    """
    if state == "waiting_approval":
        return "refused"
    if state == "cancelled":
        return "cancelled"
    if state == "running":
        return "started"
    if state == "succeeded":
        return "succeeded"
    if state == "failed":
        if failure.startswith("rejected implausible location before fetch"):
            return "refused"
        return "failed"
    return "unknown"


def operation_receipt_for_outcome(
    outcome: Any,
    *,
    plan_id: str,
    attempt_id: str = "",
    turn_id: str = "",
    request_id: str = "",
) -> dict[str, Any] | None:
    """The typed lifecycle receipt for one dispatched remote operation.

    Covers the full vocabulary the retrieval family cannot express: a refusal or cancellation is
    recorded as itself, never silently absent and never dressed as a retrieval. Identity fields
    (operation, plan, attempt, turn, request) name WHAT was attempted and for whom; `provider` is
    the source the fetch itself observed, empty when none was observed -- never guessed.
    """
    operation = _operation(outcome)
    if operation not in remote_fetch_operations():
        return None
    state = str(getattr(getattr(outcome, "state", None), "value", "") or "").strip().lower()
    if state in _NEVER_DISPATCHED_STATES:
        return None
    failure = str(getattr(outcome, "failure_reason", "") or "")
    lifecycle = _operation_lifecycle(state, failure)
    subtask = getattr(outcome, "subtask", None)
    ok = bool(getattr(outcome, "ok", False))
    result = dict(getattr(outcome, "result", None) or {}) if ok else {}
    return {
        "schema": "vool.live_data_operation_receipt.v1",
        "operation": operation,
        "effect_class": REMOTE_FETCH_EFFECT_CLASS,
        "lifecycle": lifecycle,
        "ok": ok,
        "reason": "" if ok else failure[:160],
        "plan_id": str(plan_id or "")[:160],
        "attempt_id": str(attempt_id or "")[:160],
        "subtask_id": str(getattr(subtask, "subtask_id", "") or "")[:160],
        "turn_id": str(turn_id or ""),
        "request_id": str(request_id or ""),
        # The provider actually observed on success. Empty on failure/refusal: the outcome does
        # not name who was unreachable, and a receipt never guesses.
        "provider": str(result.get("source") or "")[:120],
        "started_at": str(getattr(outcome, "started_at_iso", "") or ""),
        "completed_at": str(getattr(outcome, "completed_at_iso", "") or ""),
    }


def _turn_identity(source_context: Any) -> tuple[str, str]:
    """(turn_id, request_id) read from the turn's own context, never invented.

    Reads the canonical `TurnRequest` when the turn minted one, then the context's plain keys --
    the same resolution order the effect ledger applies. Empty strings are honest: a context with
    no turn identity gets receipts that say so, not receipts that borrow another turn's.
    """
    context = source_context if isinstance(source_context, dict) else {}
    turn_id = ""
    request_id = ""
    try:
        from core.turn_contract import TURN_REQUEST_KEY

        request = context.get(TURN_REQUEST_KEY)
    except Exception:
        request = None
    if request is not None:
        turn_id = str(getattr(request, "turn_id", "") or "")
        request_id = str(getattr(request, "request_id", "") or "")
    turn_id = turn_id or str(context.get("turn_id") or "")
    request_id = request_id or str(context.get("request_id") or "")
    return turn_id, request_id


def publish_live_data_retrieval_receipts(
    source_context: dict[str, Any] | None,
    outcomes: Any,
    *,
    plan_id: str,
    attempt_id: str = "",
) -> list[dict[str, Any]]:
    """Record every remote subtask of one executed plan on the turn, and return the receipts.

    Publishes both families from the same derived coverage: the retrieval receipts (what was
    retrieved -- preserved shape) and the operation lifecycle receipts (what was attempted,
    refusals and cancellations included). `attempt_id` is the plan's owning runtime attempt, so a
    retry's receipts name the attempt that really ran them.
    """

    receipts = [
        receipt
        for receipt in (receipt_for_outcome(outcome, plan_id=plan_id) for outcome in list(outcomes or []))
        if receipt is not None
    ]
    turn_id, request_id = _turn_identity(source_context)
    operation_receipts = [
        receipt
        for receipt in (
            operation_receipt_for_outcome(
                outcome, plan_id=plan_id, attempt_id=attempt_id, turn_id=turn_id, request_id=request_id
            )
            for outcome in list(outcomes or [])
        )
        if receipt is not None
    ]
    if isinstance(source_context, dict) and operation_receipts:
        stored_lifecycle = source_context.get("live_data_operation_receipts")
        if not isinstance(stored_lifecycle, list):
            stored_lifecycle = []
            source_context["live_data_operation_receipts"] = stored_lifecycle
        stored_lifecycle.extend(operation_receipts)
        for receipt in operation_receipts:
            emit_runtime_event(
                source_context,
                event_type="live_data_operation_outcome",
                message=(
                    f"Live-data operation {receipt['operation']}: {receipt['lifecycle']}"
                ),
                details=dict(receipt),
            )
    if not receipts:
        return []
    if isinstance(source_context, dict):
        stored = source_context.get("web_retrieval_receipts")
        if not isinstance(stored, list):
            stored = []
            source_context["web_retrieval_receipts"] = stored
        stored.extend(receipts)
        # `web_calls` is deliberately NOT written here. It is owned by the remote-fetch scope, which
        # counts each call at the HTTP boundary itself (`note_remote_fetch_attempt`); the weather
        # path was simply not reporting itself there, and that is where it is fixed. Incrementing a
        # second counter from this layer would double-count and re-create the split-truth problem
        # these receipts exist to close.
    emit_runtime_event(
        source_context,
        event_type="web_retrieval_completed",
        message=f"Completed {len(receipts)} live-data retrieval(s).",
        details={"plan_id": str(plan_id or ""), "receipts": [dict(item) for item in receipts]},
    )
    _emit_activity_steps(source_context, receipts)
    return receipts


def _emit_activity_steps(
    source_context: dict[str, Any] | None, receipts: list[dict[str, Any]]
) -> None:
    """Record each executed retrieval as a typed Activity step.

    Activity decides "No tool ran -- answered directly" from `tool_selected`/`tool_executed`, which
    the LLM tool-call loop emits. This lane emits neither, so a turn that really fetched wttr.in was
    shown to the user as a plain answer with no tool (reproduced in the served UI on 2026-08-13,
    while the same turn's own provenance line read `tool | live_data_typed_plan`).

    `core.agent_runtime.fast_paths_utility._emit_workspace_tool_events` fixed the identical
    contradiction for the workspace fast path the same way: the lane that executed records itself,
    so the truth lives in the ledger every surface already reads rather than in a second inference
    engine per surface. These steps are derived from the SAME receipts as everything else here, so
    Activity cannot disagree with the receipt or the counter.
    """

    for receipt in receipts:
        action = str(receipt.get("action") or "lookup")
        tool_name = f"live_data.{action}"
        ok = str(receipt.get("status") or "") == "available"
        emit_runtime_event(
            source_context,
            event_type="tool_selected",
            message=f"Running {tool_name}",
            details={"tool_name": tool_name, "summary": f"Running {tool_name}"},
        )
        emit_runtime_event(
            source_context,
            event_type="tool_executed" if ok else "tool_failed",
            # The subject is intentionally absent: the receipt hashes it, and an Activity row is
            # provenance, not a second copy of what the user asked.
            message=f"Finished {tool_name}: {receipt.get('status')}",
            details={
                "tool_name": tool_name,
                "summary": f"Finished {tool_name}: {receipt.get('status')}",
            },
        )


__all__ = [
    "operation_receipt_for_outcome",
    "publish_live_data_retrieval_receipts",
    "receipt_for_outcome",
    "remote_fetch_operations",
    "unclassified_dispatched_operations",
]
