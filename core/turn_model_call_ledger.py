"""How many provider calls this turn actually made, counted where they are made.

The number a turn reported as `model_calls` was not a count of anything. Two sources fed it and
neither could see a provider call:

* the deterministic result builder (`core.agent_runtime.fast_command_surface`) wrote the literal
  ``0``, on the assumption that a fast-path answer means nothing upstream had run;
* the model-lane builder (`core.agent_runtime.turn_reasoning`) wrote
  ``route_telemetry["model_calls"] or bool(model_execution.used_model)`` -- context-retrieval
  capsule telemetry, falling back to a BOOLEAN. Two calls read 1. A call that failed read 0.

Both are wrong in the same direction, and a live turn showed it: 136.41s wall, two
``model.call_completed`` events, and a terminal ``turn.trace_completed`` reporting
``model_calls: 0`` beside a proof saying ``fallback_reason: model_not_used``. The semantic receipt
already noticed and said so -- ``attempt_disagrees_with_self_report: true`` -- but the seam that saw
the calls (`core.semantic.reach`) is observation-only by construction and the runtime must never
read it back, so the disagreement had nowhere to go.

**The contract, exactly.**

``model_calls`` counts **provider invocation attempts entered while executing one turn**. One
increment each time the runtime enters a provider call:

* an adapter task method -- ``run_text_task``, ``run_structured_task``, or a streaming call --
  through ``MemoryFirstRouter._invoke_manifest``;
* a direct provider HTTP post that bypasses the adapter, i.e. the intent arbiter.

Counted:

* **retries** -- each attempt is its own call. The input was spent and the wall clock was paid.
* **failures** -- a provider that was called and raised, timed out, or returned nothing was still
  called. A count that dropped these would be the same lie in a smaller font.

Not counted:

* calls the runtime **declined to make** -- an open circuit breaker, a failed health probe, no
  manifest resolved, a cancelled request. No provider call was entered, so there is nothing to
  count. This is the same line ``core.semantic.reach.note_provider_call_attempt`` draws, and the two
  are deliberately kept in step so the receipt's cross-check compares like with like.

**Why a stamped id and not a ContextVar.** Provider execution crosses a shallow copy of the request
context (routing, retries) and the conductor dispatches onto a thread pool, where a ContextVar set
on the main thread is simply absent. The turn id is stamped INTO the context dict, so every copy and
every worker carries it, and the count lands in one place however the turn is executed.

Nested sub-turns inherit the outer id rather than opening a second ledger: one user message is one
turn, and a sub-turn's provider calls are that message's cost.

**Why the ledger also holds identity and served usage (2026-08-12).** It used to hold a bare
integer, and the footer that had to describe the turn read the *lane*, the *model id* and the
*tokens* from `core.memory_first_router.get_turn_usage()` -- which is backed by a
``threading.local()``. The conductor (`core/conductor/scheduler.py`) and the tool planner
(`core/agent_runtime/turn_planner.py`) both dispatch provider calls onto a ``ThreadPoolExecutor``,
so the served response's usage was recorded on a worker thread and read back on the API thread,
where the thread-local is a fresh empty object. Live, 2026-08-12: a conductor turn whose Activity
ledger showed a cloud Nemotron call rendered ``local | model | tokens unreported`` -- a lane, an
identity and a spend, none of them read from anything.

The count already crossed that boundary correctly, because it is keyed by an id stamped INTO the
context dict rather than by the thread. Everything the footer needs is now recorded the same way, at
the same seams, so a fact known anywhere in the turn is known at the end of it. Runtime events cross
threads by the same mechanism, which is why Activity had the truth all along.
"""
from __future__ import annotations

import contextlib
import threading
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

#: The context key holding this turn's ledger id. Underscore-prefixed: server-derived turn metadata,
#: never model-facing and never accepted from an API caller.
LEDGER_ID_KEY = "_turn_model_call_ledger_id"
#: The request/turn identity the ledger id was opened against, so a caller that reuses one context
#: dict for several turns gets a fresh count instead of a running total.
LEDGER_TURN_KEY = "_turn_model_call_ledger_turn"

#: Bounded so a long-lived daemon cannot grow this without limit. A turn reads its own count while
#: it is still executing, so eviction only ever discards turns that finished many turns ago.
_MAX_TRACKED_TURNS = 512


class ProviderCallAlreadyTerminalizedError(RuntimeError):
    """A late worker tried to close a call the enclosing turn already terminalized."""


# Compatibility alias for integrations that imported the pre-release name.
ProviderCallAlreadyTerminalized = ProviderCallAlreadyTerminalizedError


@dataclass
class ProviderCall:
    """One provider invocation attempt, named by the manifest that was about to be called.

    Recorded at call ENTRY, so the identity survives a call that then failed -- which is the case
    that most needs describing, because a failed cloud call spent the user's key and returned no
    usage to name itself with.
    """

    provider_id: str = ""
    model_id: str = ""
    cost_class: str = ""
    #: Runtime identity shared with ``model.call_started`` / terminal model events.  This lets the
    #: API close the exact started attempt if an enclosing deadline wins before the worker can
    #: return from provider I/O.
    model_call_id: str = ""
    #: Server-authored purpose of bounded internal generations (planner / conductor generation).
    #: Empty for ordinary answer calls and never inferred from model or provider identity.
    call_role: str = ""
    call_id: str = field(default_factory=lambda: f"provider-call-{uuid.uuid4().hex}")
    #: ``attempted`` until the invocation seam records its terminal outcome.  Kept explicit so a
    #: process interruption cannot be rewritten as either success or provider failure.
    outcome: str = "attempted"
    error_class: str = ""
    usage_details: dict[str, Any] | None = None
    verification_receipt: dict[str, Any] | None = None

    @property
    def lane(self) -> str:
        """``local`` / ``cloud`` / "" -- and "" is a real answer, not a prompt to pick one.

        An unrecognised or absent cost class is reported as the empty string and rendered as
        "unrecorded" downstream. Guessing a lane here is the defect this module was extended to fix.
        """
        lowered = str(self.cost_class or "").lower()
        if "cloud" in lowered:
            return "cloud"
        if "local" in lowered:
            return "local"
        return ""


@dataclass
class _TurnLedger:
    """Everything one turn recorded about what it invoked. Never inferred, only appended to."""

    calls: list[ProviderCall] = field(default_factory=list)
    #: The usage summary of the response that was actually SERVED, as
    #: `core.memory_first_router._record_response_usage` built it. Distinct from `calls`: several
    #: calls may be entered and at most one of them serves the answer.
    served_usage: dict[str, Any] | None = None
    #: Deterministic tool intents dispatched this turn, in execution order
    #: (`core.runtime_execution_tools.execute_runtime_tool`).
    tools: list[str] = field(default_factory=list)
    #: C19: the presentation selector's PRE-GATE record, written by the router
    #: seam that ran the election and read back where the turn is decorated.
    #: It travels here — keyed by the stamped id — because provider execution
    #: crosses shallow context copies, exactly like `served_usage`.
    presentation_selection: dict[str, Any] | None = None


_LOCK = threading.Lock()
_COUNTS: OrderedDict[str, _TurnLedger] = OrderedDict()


def _turn_fingerprint(source_context: dict[str, Any] | None) -> str:
    """The request identity this turn was opened under, or '' when the caller supplied none."""
    context = source_context or {}
    for name in ("request_id", "cancel_turn_id", "task_id"):
        value = str(context.get(name) or "").strip()
        if value:
            return value
    return ""


def begin_turn(source_context: dict[str, Any] | None) -> str:
    """Open a ledger for this turn and stamp its id into the context. Returns the id.

    Idempotent for one turn: a nested sub-turn re-entering the runtime with a context that already
    carries an id keeps counting into the same ledger. A caller that reuses one dict across several
    user turns gets a new ledger, because the stamped request identity no longer matches.
    """
    if not isinstance(source_context, dict):
        return ""
    fingerprint = _turn_fingerprint(source_context)
    existing = str(source_context.get(LEDGER_ID_KEY) or "").strip()
    if existing and str(source_context.get(LEDGER_TURN_KEY) or "") == fingerprint:
        return existing
    ledger_id = f"tmc-{uuid.uuid4().hex}"
    source_context[LEDGER_ID_KEY] = ledger_id
    source_context[LEDGER_TURN_KEY] = fingerprint
    with _LOCK:
        _COUNTS[ledger_id] = _TurnLedger()
        _COUNTS.move_to_end(ledger_id)
        while len(_COUNTS) > _MAX_TRACKED_TURNS:
            _COUNTS.popitem(last=False)
    return ledger_id


def _ledger(source_context: dict[str, Any] | None) -> _TurnLedger | None:
    """This turn's ledger, or None when the caller never opened one. Callers hold `_LOCK`."""
    ledger_id = str((source_context or {}).get(LEDGER_ID_KEY) or "").strip()
    if not ledger_id:
        return None
    ledger = _COUNTS.get(ledger_id)
    if ledger is not None:
        _COUNTS.move_to_end(ledger_id)
    return ledger


def record_provider_call(
    source_context: dict[str, Any] | None,
    *,
    provider_id: str = "",
    model_id: str = "",
    cost_class: str = "",
    model_call_id: str = "",
    call_role: str = "",
) -> str:
    """One provider call was entered. Returns its opaque id, or ``""``; never raises.

    Fail-soft for the same reason the REACH seam is: accounting must not be able to end a turn that
    was otherwise going to work. A call on a turn with no ledger (a caller that never reached
    `begin_turn`) is dropped rather than invented -- an unattributable increment would corrupt some
    other turn's count.

    The identity keywords are optional and each defaults to "" rather than to a plausible value: a
    seam that does not know which provider it is about to reach records that it does not know, and
    the footer says "unrecorded" instead of picking the commoner lane.
    """
    try:
        with _LOCK:
            ledger = _ledger(source_context)
            if ledger is None:
                return ""
            call = ProviderCall(
                provider_id=str(provider_id or ""),
                model_id=str(model_id or ""),
                cost_class=str(cost_class or ""),
                model_call_id=str(model_call_id or ""),
                call_role=str(call_role or ""),
            )
            ledger.calls.append(call)
        # M3: the same instant, recorded on the turn's grounding lifecycle -- which evidence set
        # this call's own prompt context carried. Here because this is the ONE seam every
        # provider invocation passes through at ENTRY, so a call made before anything was bound
        # records the absence rather than being invisible, and "the answer was written before the
        # evidence existed" becomes a readable fact instead of an inference about timing.
        #
        # Outside `_LOCK`: two independent ledgers, and holding one while taking the other is how
        # a deadlock is built.
        try:
            from core.grounding_lifecycle import record_synthesis_call

            record_synthesis_call(
                source_context, model_call_id=str(model_call_id or ""), call_role=str(call_role or "")
            )
        except Exception:
            pass
        return call.call_id
    except Exception:
        return ""


def _file_provider_fault(source_context: dict[str, Any] | None, call: ProviderCall, error_class: str) -> None:
    """File the typed fault one failed provider call produced. Best-effort, never raises.

    The call ledger is the owning boundary for provider-call outcomes -- every invocation
    in the runtime closes here -- so the mapping happens HERE, once, keyed by the call's
    own id. The security plane is never involved: a provider failure is availability.
    """
    try:
        from core.faults.mapping import fault_code_for_provider_error_class
        from core.faults.recorder import identity_from_context, record_fault
        from core.faults.records import FaultRecord

        turn_key, session_id = identity_from_context(source_context)
        record_fault(
            FaultRecord.for_code(
                fault_code_for_provider_error_class(error_class),
                authority="core.turn_model_call_ledger",
                turn_key=turn_key,
                session_id=session_id,
                dedupe=call.call_id,
                evidence_refs=tuple(ref for ref in (call.model_call_id,) if ref),
                context={
                    "provider_id": call.provider_id,
                    "model_id": call.model_id,
                    "error_class": error_class,
                    "call_role": call.call_role,
                    "status": "failed",
                },
            )
        )
    except Exception:
        # Accounting must not be able to end a turn that was otherwise going to work.
        pass


def record_provider_call_outcome(
    source_context: dict[str, Any] | None,
    call_id: str,
    *,
    outcome: str,
    error_class: str = "",
    usage_details: dict[str, Any] | None = None,
    verification_receipt: dict[str, Any] | None = None,
) -> bool:
    """Close one exact invocation attempt as ``completed`` or ``failed``.

    The call id comes from :func:`record_provider_call`, so concurrent provider arms never mark the
    wrong list entry merely because they completed out of order.  Unknown ids and invalid outcomes
    fail soft: accounting cannot be allowed to break an otherwise valid answer.
    """

    if outcome not in {"completed", "failed"}:
        return False
    try:
        with _LOCK:
            ledger = _ledger(source_context)
            if ledger is None:
                return False
            for call in ledger.calls:
                if call.call_id != str(call_id or ""):
                    continue
                # Terminal means terminal.  In particular, an abandoned worker that finally
                # returns after the enclosing turn recorded a timeout cannot rewrite failure as
                # success or produce a second, contradictory receipt.
                if call.outcome != "attempted":
                    return False
                from core.provider_verification import build_verification_receipt
                call.verification_receipt = verification_receipt or build_verification_receipt(
                    call_id=call.call_id, request_id=str((source_context or {}).get("request_id") or ""),
                    requested_model=str((source_context or {}).get("requested_model") or call.model_id),
                    selected_model=call.model_id, provider_id=call.provider_id, outcome=outcome,
                )
                call.usage_details = dict(usage_details) if usage_details else None
                call.outcome = outcome
                call.error_class = str(error_class or "") if outcome == "failed" else ""
                if outcome == "failed":
                    _file_provider_fault(source_context, call, call.error_class)
                break
            else:
                return False
        _emit_verification_receipt(source_context, call)
        return True
    except Exception:
        return False
    return False


def _emit_verification_receipt(source_context, call):
    # Emit after releasing the accounting lock. Telemetry cannot fail a completed answer.
    try:
        from core.runtime_task_events import emit_runtime_event
        receipt = dict(call.verification_receipt)
        emit_runtime_event(source_context, event_type="model_verification_receipt",
                           message="Model identity receipt: " + receipt["verification"]["status"],
                           details={"verification_receipt": receipt, "provider_id": call.provider_id,
                                    "model_id": call.model_id, "model_call_id": call.model_call_id})
    except Exception:
        pass


def fail_pending_provider_calls(
    source_context: dict[str, Any] | None,
    *,
    error_class: str,
) -> list[dict[str, str]]:
    """Atomically terminalize every provider attempt still owned by this turn.

    Called immediately before the terminal turn trace.  Returning the exact identities that were
    changed lets the runtime emit one matching ``model.call_failed`` receipt per started call,
    before it emits ``turn.trace_completed``.  Already-completed or already-failed calls are left
    untouched, so racing cleanup is single-assignment rather than last-writer-wins.
    """

    closed: list[dict[str, str]] = []
    receipt_calls = []
    try:
        with _LOCK:
            ledger = _ledger(source_context)
            if ledger is None:
                return []
            for call in ledger.calls:
                if call.outcome != "attempted":
                    continue
                from core.provider_verification import build_verification_receipt
                call.verification_receipt = build_verification_receipt(
                    call_id=call.call_id, request_id=str((source_context or {}).get("request_id") or ""),
                    requested_model=str((source_context or {}).get("requested_model") or call.model_id),
                    selected_model=call.model_id, provider_id=call.provider_id, outcome="failed",
                )
                receipt_calls.append(call)
                call.outcome = "failed"
                call.error_class = str(error_class or "")
                _file_provider_fault(source_context, call, call.error_class)
                closed.append(
                    {
                        "call_id": call.call_id,
                        "model_call_id": call.model_call_id,
                        "provider_id": call.provider_id,
                        "model_id": call.model_id,
                        "cost_class": call.cost_class,
                        "call_role": call.call_role,
                    }
                )
    except Exception:
        return []
    for call in receipt_calls:
        _emit_verification_receipt(source_context, call)
    return closed


def record_served_usage(
    source_context: dict[str, Any] | None, summary: dict[str, Any] | None, *, authored: bool = True
) -> None:
    """The usage of the response that was SERVED this turn, recorded where every thread can read it.

    `authored=False` meters a response whose bytes are NOT the answer -- a tool-intent call, whose
    output is a tool choice the runtime then executes and renders. Its tokens are billed and
    surfaced like any other; the M3 authorship claim below is not raised for it, because raising
    it is raise-only and would make the gate read the runtime's rendered tool result as prose.

    The same numbers also go to `core.memory_first_router.record_turn_usage`, which is a
    thread-local and therefore invisible to the API thread whenever the call ran on a conductor or
    planner worker. This is the copy that survives that hop; the thread-local stays as the fast
    path for the single-threaded case and as the shape every existing caller already reads.
    """
    try:
        if not isinstance(summary, dict):
            return
        with _LOCK:
            ledger = _ledger(source_context)
            if ledger is None:
                return
            ledger.served_usage = dict(summary)
        # M3 AUTHORSHIP. This names the response that was actually SERVED, not a call that was
        # merely attempted -- and that distinction is load-bearing, not pedantic. Measured on
        # the isolated daemon: a browser turn's cloud call FAILED (`empty_synthesis`), the
        # runtime composed its own typed failure notice, and marking the turn model-authored at
        # call ENTRY made the gate treat that notice as model bytes and refuse it. A failed call
        # produces no bytes; only a served response does. Outside `_LOCK`: two independent
        # ledgers, and holding one while taking the other is how a deadlock is built.
        if authored:
            with contextlib.suppress(Exception):
                from core.grounding_lifecycle import record_model_authorship

                record_model_authorship(source_context)
        if not authored:
            return
        # AUTHOR ELIGIBILITY. The same instant, asked of the same served response: was the model
        # that WROTE these bytes certified to take the final-answer role? Recorded here rather
        # than at call entry for the reason immediately above -- a call that was entered and
        # failed produced no bytes, and refusing a runtime-composed failure notice on the
        # strength of an attempted call is a measured, previously-paid-for mistake.
        with contextlib.suppress(Exception):
            _record_author_eligibility(source_context, dict(summary))
    except Exception:
        return


def _served_manifest(provider_id: str) -> Any:
    """The registered manifest behind a served provider id, or None.

    A read, never a construction: the authority's verdict is about a configured model identity,
    and an id that resolves to no manifest is an identity this runtime cannot answer for.
    """

    try:
        from core.model_registry import ModelRegistry

        wanted = str(provider_id or "").strip()
        if not wanted:
            return None
        for manifest in ModelRegistry().list_manifests():
            if str(getattr(manifest, "provider_id", "") or "") == wanted:
                return manifest
    except Exception:
        return None
    return None


def _turn_has_runtime_support(source_context: dict[str, Any] | None) -> bool:
    """Did anything other than the model's weights back this turn's answer?

    A deterministic tool ran, or the turn's grounding lifecycle holds bound evidence or typed
    observations. Either way the model is RENDERING something the runtime minted, which is not
    the same act as originating an answer -- and it is the distinction that keeps an uncertified
    local model a useful worker instead of a switched-off one.
    """

    try:
        with _LOCK:
            ledger = _ledger(source_context)
            if ledger is not None and ledger.tools:
                return True
    except Exception:
        return False
    try:
        from core.grounding_lifecycle import lifecycle_for_context

        record = lifecycle_for_context(source_context)
        if record is None:
            return False
        return bool(
            getattr(record, "bound_notes", ())
            or getattr(record, "typed_observations", ())
            or getattr(record, "retrieved_notes", ())
        )
    except Exception:
        return False


def _turn_support_rows(source_context: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The rows this turn actually minted, in the shape `core.claim_support` reads.

    `runtime_tool_observations` is what the tool lanes already record, and
    `core.grounding_lifecycle` already knows how to project one into a support row -- reused
    rather than reshaped here, so a lane that changes its record shape changes it in one place.
    """

    context = source_context if isinstance(source_context, dict) else {}
    rows: list[dict[str, Any]] = []
    try:
        from core.grounding_lifecycle import _as_support_row

        for entry in list(context.get("runtime_tool_observations") or []):
            if isinstance(entry, dict):
                rows.append(_as_support_row(entry))
    except Exception:
        return []
    if rows:
        return rows
    try:
        from core.grounding_lifecycle import lifecycle_for_context

        record = lifecycle_for_context(context)
        if record is None:
            return []
        return [dict(row) for row in getattr(record, "typed_observations", ()) if isinstance(row, dict)]
    except Exception:
        return []


def _record_author_eligibility(
    source_context: dict[str, Any] | None, summary: dict[str, Any]
) -> None:
    """Ask the one authority whether the model that just served may author, and record it."""

    from core.final_answer_authorship import (
        decide_final_answer_author,
        record_authorship_decision,
    )

    context = source_context if isinstance(source_context, dict) else {}
    provider_id = str(summary.get("provider_id") or "").strip()
    manifest = _served_manifest(provider_id)
    role = str(context.get("model_call_role") or "").strip()
    request_text = ""
    for key in ("turn_request", "user_input", "effective_input", "request_text"):
        value = context.get(key)
        if isinstance(value, str) and value.strip():
            request_text = value
            break
        text = str(getattr(value, "text", "") or "").strip()
        if text:
            request_text = text
            break
    local_only = None
    try:
        from core.auto_local_only_mode import turn_is_local_only

        local_only = bool(turn_is_local_only(context))
    except Exception:
        local_only = None
    candidates: list[Any] = []
    try:
        from core.model_registry import ModelRegistry

        candidates = list(ModelRegistry().list_manifests(enabled_only=True))
    except Exception:
        candidates = []
    decision = decide_final_answer_author(
        request_text=request_text,
        author_role=role,
        requested_manifest=manifest,
        requested_model=provider_id,
        candidates=candidates,
        local_only=local_only,
        # After the fact. The bytes exist; whether some OTHER model could have been routed to is
        # not a property of the model that wrote them. See `decide_final_answer_author`.
        allow_escalation=False,
    )
    record_authorship_decision(
        context,
        decision,
        model_authored=True,
        supported_by_runtime=_turn_has_runtime_support(context),
        served_model=provider_id,
        support_rows=_turn_support_rows(context),
        request_text=request_text,
    )


def record_tool_execution(source_context: dict[str, Any] | None, intent: str) -> None:
    """A deterministic runtime tool was dispatched this turn, named by its intent.

    What this exists for: a workspace read reports ``route_reason="workspace_runtime_fast_path"``,
    the name of the LANE that claimed the message. The tool it ran is `workspace.read_file`, and
    that is the name the user recognises and the one Activity already shows. Recorded here so the
    footer can name the operation rather than the family that dispatched it.
    """
    try:
        name = str(intent or "").strip()
        if not name:
            return
        with _LOCK:
            ledger = _ledger(source_context)
            if ledger is None:
                return
            if name not in ledger.tools:
                ledger.tools.append(name)
    except Exception:
        return


def turn_model_calls(source_context: dict[str, Any] | None) -> int:
    """Provider calls entered so far on this turn. 0 when nothing was counted for it."""
    try:
        with _LOCK:
            ledger = _ledger(source_context)
            return len(ledger.calls) if ledger is not None else 0
    except Exception:
        return 0


def turn_served_usage(source_context: dict[str, Any] | None) -> dict[str, Any] | None:
    """The served response's usage summary for this turn, or None when none was recorded."""
    try:
        with _LOCK:
            ledger = _ledger(source_context)
            if ledger is None or not isinstance(ledger.served_usage, dict):
                return None
            return dict(ledger.served_usage)
    except Exception:
        return None


def turn_call_accounting(source_context: dict[str, Any] | None) -> dict[str, Any]:
    """A plain-dict summary of what this turn invoked, safe to attach to a turn result.

    Every field is a reading. `lanes` and `models` hold only what a manifest actually named, in
    first-seen order and de-duplicated; a call whose lane was not recorded contributes nothing to
    `lanes` rather than a guess. An empty accounting block ({}) means the turn opened no ledger --
    which is different from a ledger that recorded zero calls, and the two must not be collapsed.
    """
    try:
        with _LOCK:
            ledger = _ledger(source_context)
            if ledger is None:
                return {}
            calls = list(ledger.calls)
            served = dict(ledger.served_usage) if isinstance(ledger.served_usage, dict) else {}
            tools = list(ledger.tools)
    except Exception:
        return {}
    lanes: list[str] = []
    providers: list[str] = []
    models: list[str] = []
    completed_calls = 0
    failed_calls = 0
    pending_calls = 0
    failed_error_classes: list[str] = []
    for call in calls:
        if call.provider_id and call.provider_id not in providers:
            providers.append(call.provider_id)
        if call.lane and call.lane not in lanes:
            lanes.append(call.lane)
        if call.model_id and call.model_id not in models:
            models.append(call.model_id)
        if call.outcome == "completed":
            completed_calls += 1
        elif call.outcome == "failed":
            failed_calls += 1
            if call.error_class and call.error_class not in failed_error_classes:
                failed_error_classes.append(call.error_class)
        else:
            pending_calls += 1
    return {
        "calls": len(calls),
        "completed_calls": completed_calls,
        "failed_calls": failed_calls,
        "pending_calls": pending_calls,
        "failed_error_classes": failed_error_classes,
        "providers": providers,
        "lanes": lanes,
        "models": models,
        "tools": tools,
        "served_usage": served,
        "verification_receipts": [dict(call.verification_receipt) for call in calls if call.verification_receipt],
        # One row per invocation, owned by this turn. Duplicate terminal events cannot add rows.
        "usage_details": [dict(call.usage_details) if call.usage_details else {
            "cost_state": "free" if call.cost_class in {"free_local", "free_cloud"} else "unreported"
        } for call in calls] if any(call.usage_details for call in calls) else [],
    }


def reset_for_tests() -> None:
    """Drop every tracked turn. Test hook only."""
    with _LOCK:
        _COUNTS.clear()


def record_presentation_selection(
    source_context: dict[str, Any] | None,
    record: dict[str, Any] | None,
) -> None:
    """C19: file the presentation selector's record for THIS turn.

    Written by the router seam that ran the election (and re-written when the
    derived repair updates it). Keyed by the stamped ledger id, so the record
    survives the shallow context copies and thread hops that a bare dict write
    on a worker thread would not.
    """
    if not isinstance(record, dict) or not record.get("schema"):
        return
    try:
        with _LOCK:
            ledger = _ledger(source_context)
            if ledger is not None:
                ledger.presentation_selection = dict(record)
    except Exception:
        return


def turn_presentation_selection(source_context: dict[str, Any] | None) -> dict[str, Any] | None:
    """This turn's filed selection record, or None when no selector ran."""
    try:
        with _LOCK:
            ledger = _ledger(source_context)
            record = ledger.presentation_selection if ledger is not None else None
    except Exception:
        return None
    return dict(record) if isinstance(record, dict) and record else None


__all__ = [
    "LEDGER_ID_KEY",
    "LEDGER_TURN_KEY",
    "ProviderCall",
    "ProviderCallAlreadyTerminalized",
    "ProviderCallAlreadyTerminalizedError",
    "begin_turn",
    "fail_pending_provider_calls",
    "record_presentation_selection",
    "record_provider_call",
    "record_provider_call_outcome",
    "record_served_usage",
    "record_tool_execution",
    "reset_for_tests",
    "turn_call_accounting",
    "turn_model_calls",
    "turn_presentation_selection",
    "turn_served_usage",
]
