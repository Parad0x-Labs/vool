"""Step 10: exact retry -- a new execution generation against a persisted `runtime_attempts` row,
carrying forward valid results and rerunning only what is actually retryable.

Reconstructs `LiveDataSubtask`/`SubtaskOutcome` objects directly from persisted
`runtime_attempt_subtasks` rows rather than re-deriving anything from the original request text --
retrying is "run the same typed plan again, selectively," never "re-interpret the sentence and
hope the same entities come out." A subtask carried forward keeps its previous
result/timestamp/source untouched; only subtasks whose prior state was a genuinely transient
failure are re-executed.
"""

from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from core.live_data_plan import LiveDataPlan, LiveDataSubtask, SubtaskLifecycle, SubtaskOutcome

# Repair 7 (Mnemosyne review, 2026-08-06): a successful market/weather result was ALWAYS carried
# forward on retry, including a five-day-old one -- refresh_required/refresh_reason stayed at their
# defaults everywhere. TTLs are a stated product policy, not a guess: market prices are volatile
# enough that even a same-session immediate retry should refresh a quote past this age; weather
# conditions change more slowly. Both are read-only LIVE_DATA lookups -- the only operation types
# `plan_retry_generation` ever sees -- so "a successful externally consequential operation is never
# repeated automatically" holds structurally here: nothing mutating reaches this table at all.
_FRESHNESS_TTL_SECONDS_BY_OPERATION: dict[str, int] = {
    "market_quote": 15 * 60,
    "weather_lookup": 60 * 60,
}


def _parse_iso(value: str) -> datetime | None:
    """A timezone-AWARE datetime, or None for anything that isn't one -- missing, malformed, or
    genuinely parseable but timezone-NAIVE (Final Repair 4, Mnemosyne final review: a naive
    `completed_at` -- no `+00:00`/`Z` offset -- parses successfully via `datetime.fromisoformat`
    without raising, so a bare `except ValueError` here let it through as a naive datetime; the
    TypeError happened later, outside any try/except, when the caller subtracted it from an
    aware `now`, permanently blocking retry planning for that whole attempt chain. Never
    reinterprets a naive timestamp as local machine time -- treated as unknown age instead,
    exactly like a missing or malformed one, per the conservative existing policy."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def evaluate_subtask_freshness(row: dict[str, Any], *, now_iso: str) -> tuple[bool, str]:
    """(refresh_required, refresh_reason) for a SUCCEEDED subtask row, evaluated against `now_iso`
    -- pure, no I/O, so a test asserts any point on the freshness curve with a controlled timestamp
    rather than waiting in real time. Compares against `completed_at` (the machine-stamped ISO
    execution-completion time), never `retrieved_at`/`observed_at` (display strings sourced
    verbatim from the external provider's own format -- "02:35 PM", "2026-08-06 07:30 UTC" -- not
    reliably parseable). An operation with no defined TTL, or a row with no parseable completion
    time, is never treated as stale: unknown age is not evidence of staleness, so it is carried
    forward exactly as before this repair, not refreshed on a guess."""
    operation = str(row.get("operation") or "")
    ttl_seconds = _FRESHNESS_TTL_SECONDS_BY_OPERATION.get(operation)
    if ttl_seconds is None:
        return False, ""
    completed_at = _parse_iso(str(row.get("completed_at") or ""))
    now = _parse_iso(now_iso)
    if completed_at is None or now is None:
        return False, ""
    age_seconds = (now - completed_at).total_seconds()
    if age_seconds <= ttl_seconds:
        return False, ""
    return True, (
        f"result is {int(age_seconds // 60)} minutes old, exceeding the "
        f"{ttl_seconds // 60}-minute freshness window for {operation}"
    )

_TERMINAL_ATTEMPT_STATES = {
    "PARTIAL_SUCCESS", "SUCCEEDED", "FAILED_TOOL", "FAILED_PROVIDER",
    "FAILED_SYNTHESIS", "FAILED_VALIDATION", "CANCELLED", "ABANDONED",
}
# Repair 3 (Mnemosyne review): a caller that loses the DB idempotency race (see
# core.runtime_continuity.create_runtime_attempt) must render the SAME logical result the winner
# produces, not a partial snapshot taken mid-execution. Bounded so a caller can never block
# forever on a winner that itself crashed before finishing -- it renders honestly from whatever is
# persisted once the bound is hit, never fabricating a result the winner never produced.
_REPLAY_WAIT_TOTAL_SECONDS = 8.0
_REPLAY_WAIT_POLL_SECONDS = 0.1
# A same-process lock loser waits far less than a cross-process replay: if it is racing the SAME
# logical retry, the winner's single INSERT lands in milliseconds; if it is a genuinely different
# retry (a distinct trigger turn) queued behind a slow one, this bound is how long "already in
# progress" takes to report rather than blocking for the full replay window.
_SAME_PROCESS_LOCK_WAIT_SECONDS = 2.0

# Step 10 idempotency: `create_runtime_attempt`'s own atomic check-and-insert (see
# core.runtime_continuity) closes the race for the DATABASE ROW, but two concurrent calls can
# still both pass that check-and-get-the-same-row-back and then both proceed to run the SAME
# rerun subtasks (duplicate network calls, duplicate writes racing on result content, though not
# duplicate ROWS). A per-parent-attempt lock, held for the FULL duration of one retry generation's
# execution, is what actually guarantees "duplicate execution" never happens, not just "duplicate
# row." Measured directly: without this lock, two threads calling `execute_attempt_retry` for the
# same parent at nearly the same instant both created a fresh generation before either one's write
# was visible to the other.
_RETRY_LOCKS_GUARD = threading.Lock()
_RETRY_LOCKS: dict[str, threading.Lock] = {}


def _retry_lock_for(parent_attempt_id: str) -> threading.Lock:
    with _RETRY_LOCKS_GUARD:
        lock = _RETRY_LOCKS.get(parent_attempt_id)
        if lock is None:
            lock = threading.Lock()
            _RETRY_LOCKS[parent_attempt_id] = lock
        return lock

_REQUIRED_FIELDS_BY_OPERATION: dict[str, tuple[str, ...]] = {
    "market_quote": ("price", "currency", "change_24h_pct", "source", "retrieved_at"),
    "weather_lookup": ("condition", "source", "observed_at"),
    "unsupported_market_entity": (),
}
_TOOL_BY_OPERATION: dict[str, str] = {
    "market_quote": "market_prices",
    "unsupported_market_entity": "market_prices",
    "weather_lookup": "weather",
}

# Lifecycle states whose subtasks are eligible for automatic rerun -- Step 10 correction: only a
# genuinely TRANSIENT failure is retried automatically. UNSUPPORTED_ENTITY, WAITING_APPROVAL, and
# CANCELLED are carried forward unchanged; a fresh SUCCEEDED result is carried forward, never
# re-fetched, unless a caller explicitly marks it stale (no numeric staleness policy is defined
# yet -- see the module docstring in attempt_followup.py's retry wiring for the disclosed gap).
# "interrupted" (Repair 6, Mnemosyne review) is a subtask a restart-abandon sweep found still
# PLANNED/RUNNING and force-transitioned to FAILED -- it never produced a result, so it must be
# rerun exactly like a transient failure, never carried forward as if it were completed work.
_AUTO_RERUN_STATES = {"FAILED"}
_AUTO_RERUN_FAILURE_CLASSES = {"transient", "interrupted", ""}


def _subtask_from_row(row: dict[str, Any], *, plan_id: str) -> LiveDataSubtask:
    operation = str(row.get("operation") or "")
    entity_key = str(row.get("entity_key") or "")
    arguments = dict(row.get("arguments") or {})
    return LiveDataSubtask(
        subtask_id=f"{plan_id}:{operation}:{'_'.join(entity_key.split()) or uuid.uuid4().hex[:8]}",
        entity=entity_key.title() if entity_key else str(row.get("subtask_id") or "unknown"),
        operation=operation,
        arguments=arguments,
        required_result_fields=_REQUIRED_FIELDS_BY_OPERATION.get(operation, ()),
        tool=_TOOL_BY_OPERATION.get(operation, "market_prices"),
        tool_intent="web.research",
    )


def _carried_forward_outcome(row: dict[str, Any], *, plan_id: str) -> SubtaskOutcome:
    """A SubtaskOutcome reconstructed from a PRIOR generation's persisted row -- never re-fetched,
    its result/timestamps are exactly what was already stored."""
    subtask = _subtask_from_row(row, plan_id=plan_id)
    state_name = str(row.get("lifecycle_state") or "FAILED")
    try:
        state = SubtaskLifecycle[state_name]
    except KeyError:
        state = SubtaskLifecycle.FAILED
    return SubtaskOutcome(
        subtask=subtask,
        state=state,
        result=dict(row.get("result_summary") or {}) or None,
        failure_reason=str(row.get("failure_reason") or ""),
        approval_decision=str(row.get("approval_state") or ""),
    )


def plan_retry_generation(
    parent_subtasks: list[dict[str, Any]],
    *,
    plan_id: str,
    now_iso: str = "",
    same_canonical_turn: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Splits the parent generation's subtask rows into (carry_forward_rows, rerun_rows) -- pure,
    no I/O, directly testable against every lifecycle/failure_class combination.

    Repair 7: a SUCCEEDED row whose result has exceeded its operation's freshness window
    (`evaluate_subtask_freshness`) is treated as needing a refresh -- moved into `rerun`, not
    carried forward unconditionally as before. `now_iso` defaults to `""`, which skips freshness
    evaluation entirely (every row's prior behavior is preserved byte-for-byte) -- the one
    production caller (`_execute_attempt_retry_locked`) always supplies the real current time; a
    stale row is tagged with `_refresh_reason` for the caller to persist as
    `refresh_required`/`refresh_reason` rather than a misleading "transient failure" rerun_reason.

    Evidence isolation (P1): a SUCCEEDED row may be reused across a TURN boundary only when a
    freshness policy still permits it. `same_canonical_turn` is False for a retry triggered by a
    later turn than the one that produced the parent's results; a SUCCEEDED row whose operation
    has NO defined TTL then has nothing that permits its reuse (unknown age is no licence), so it
    is retrieved again. Deterministic outcomes (unsupported entity, awaiting approval, cancelled)
    are planning facts, not time-based evidence, and still carry. The carried row keeps its full
    identity either way -- `carried_forward_from_attempt_id`/`_generation` and the original
    timestamps are persisted by the caller, which is what makes the reuse auditable as retained
    history rather than a fresh observation.
    """
    carry_forward: list[dict[str, Any]] = []
    rerun: list[dict[str, Any]] = []
    for row in parent_subtasks:
        state = str(row.get("lifecycle_state") or "")
        failure_class = str(row.get("failure_class") or "")
        if state in _AUTO_RERUN_STATES and failure_class in _AUTO_RERUN_FAILURE_CLASSES:
            rerun.append(row)
            continue
        if state == "SUCCEEDED":
            if not same_canonical_turn and (
                str(row.get("operation") or "") not in _FRESHNESS_TTL_SECONDS_BY_OPERATION
            ):
                unpermitted_row = dict(row)
                unpermitted_row["_refresh_reason"] = (
                    "result reuse across turns is not permitted without a freshness policy"
                )
                rerun.append(unpermitted_row)
                continue
            if now_iso:
                refresh_required, refresh_reason = evaluate_subtask_freshness(row, now_iso=now_iso)
                if refresh_required:
                    stale_row = dict(row)
                    stale_row["_refresh_reason"] = refresh_reason
                    rerun.append(stale_row)
                    continue
        carry_forward.append(row)
    return carry_forward, rerun


class RetryAlreadyInProgressError(Exception):
    """Raised when a retry against this exact parent is already executing in another thread --
    the caller should report "already in progress," not start a second concurrent execution."""


def execute_attempt_retry(
    parent_attempt: dict[str, Any],
    parent_subtasks: list[dict[str, Any]],
    *,
    session_id: str,
    checkpoint_id: str,
    trigger_user_turn_id: str = "",
    resolution_intent: str = "",
    source_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Runs one retry generation. Returns {"attempt": <new attempt dict>, "rendered": <str>,
    "carried_forward_count": int, "rerun_count": int, "idempotent_replay": bool}, or raises
    `RetryAlreadyInProgressError`.

    Idempotency is enforced in two layers. The process-local per-parent-attempt lock below is a
    within-process optimization only -- it avoids two threads in THIS process both entering the
    machinery for the same parent. It is NOT the authoritative guarantee: Mnemosyne reproduced two
    separate OS processes both passing this lock (each in its own process, so the lock never saw
    the other) and both executing the retry. The authoritative guarantee is
    `core.runtime_continuity.create_runtime_attempt`'s real database uniqueness constraint on
    (parent_attempt_id, trigger_user_turn_id, resolution_intent), enforced by SQLite across every
    connection to this database file regardless of process. `trigger_user_turn_id` must be the
    real canonical turn id of the turn that asked for this retry -- an empty one raises inside
    `create_runtime_attempt` rather than silently creating an unkeyed, undeduplicated retry.
    """
    from core.runtime_continuity import compute_retry_idempotency_key, find_attempt_by_retry_idempotency_key

    parent_id_for_lock = str(parent_attempt.get("attempt_id") or "")
    # The idempotency identity is scoped to the retry chain's ROOT, not the immediate parent, so
    # two concurrent retries of the same logical retry that resolved DIFFERENT immediate parents
    # (thread B picked up thread A's freshly-created child as "the latest unresolved attempt")
    # still compute the SAME key and the database constraint in `create_runtime_attempt` collapses
    # them -- must match the key that function writes (see its comment). The process-local lock
    # below stays keyed on the immediate parent: it is only a within-process optimization, and the
    # root-scoped DB key is the authoritative cross-process guarantee.
    root_id_for_key = str(parent_attempt.get("root_attempt_id") or parent_attempt.get("attempt_id") or "")
    # A caller that will lose the DB race anyway (a replay of an already-terminal retry) does not
    # need the process-local lock at all -- skip straight to rendering the existing result so a
    # slow winner elsewhere can never make a pure replay request block or raise
    # RetryAlreadyInProgressError for work it isn't going to do.
    probe_key = compute_retry_idempotency_key(root_id_for_key, trigger_user_turn_id, resolution_intent)
    existing = find_attempt_by_retry_idempotency_key(probe_key) if probe_key else None
    if existing is not None:
        return _render_idempotent_replay(existing, parent_attempt=parent_attempt)

    lock = _retry_lock_for(parent_id_for_lock)
    if not lock.acquire(blocking=False):
        # Another thread in THIS process already holds the lock for this exact parent. If it is
        # racing on the SAME (trigger turn, intent) -- the identical logical retry -- its row will
        # appear within milliseconds; wait briefly and render that result instead of erroring, so a
        # same-process race behaves the same as the cross-process race the DB constraint already
        # handles. If it is a DIFFERENT retry against the same parent (a distinct trigger turn),
        # this key will never appear and the short bound below falls through to the "already in
        # progress" report, same as before this repair.
        winner = _wait_for_retry_row(probe_key, deadline_seconds=_SAME_PROCESS_LOCK_WAIT_SECONDS) if probe_key else None
        if winner is not None:
            return _render_idempotent_replay(winner, parent_attempt=parent_attempt)
        raise RetryAlreadyInProgressError(f"a retry against {parent_id_for_lock} is already executing")
    try:
        return _execute_attempt_retry_locked(
            parent_attempt, parent_subtasks, session_id=session_id, checkpoint_id=checkpoint_id,
            trigger_user_turn_id=trigger_user_turn_id, resolution_intent=resolution_intent,
            source_context=source_context,
        )
    finally:
        lock.release()


def _wait_for_retry_row(retry_idempotency_key: str, *, deadline_seconds: float) -> dict[str, Any] | None:
    from core.runtime_continuity import find_attempt_by_retry_idempotency_key

    deadline = time.monotonic() + deadline_seconds
    while time.monotonic() < deadline:
        row = find_attempt_by_retry_idempotency_key(retry_idempotency_key)
        if row is not None:
            return row
        time.sleep(_REPLAY_WAIT_POLL_SECONDS)
    return find_attempt_by_retry_idempotency_key(retry_idempotency_key)


def _render_idempotent_replay(existing_attempt: dict[str, Any], *, parent_attempt: dict[str, Any]) -> dict[str, Any]:
    """Repair 3: render the SAME logical result an already-existing (in-flight or terminal) retry
    attempt produced, without starting a second execution. Waits briefly for the winner to reach a
    terminal state so a fast-losing replay does not render a mid-flight snapshot; if the wait bound
    is hit, it still renders honestly from whatever is currently persisted -- never fabricated."""
    from core.agent_runtime.live_data_render import render_live_data_answer
    from core.runtime_continuity import find_attempt_by_retry_idempotency_key, list_runtime_attempt_subtasks

    attempt_id = str(existing_attempt.get("attempt_id") or "")
    generation = int(existing_attempt.get("execution_generation") or 1)
    plan_id = str(existing_attempt.get("plan_id") or "")
    deadline = time.monotonic() + _REPLAY_WAIT_TOTAL_SECONDS
    current = existing_attempt
    while str(current.get("lifecycle_state") or "") not in _TERMINAL_ATTEMPT_STATES and time.monotonic() < deadline:
        time.sleep(_REPLAY_WAIT_POLL_SECONDS)
        refreshed = find_attempt_by_retry_idempotency_key(str(current.get("retry_idempotency_key") or ""))
        if refreshed is None:
            break
        current = refreshed

    subtasks = list_runtime_attempt_subtasks(attempt_id, execution_generation=generation)
    outcomes = [_carried_forward_outcome(row, plan_id=plan_id) for row in subtasks]
    merged_plan = LiveDataPlan(
        plan_id=plan_id, attempt_id=attempt_id,
        original_request=str(parent_attempt.get("original_request_snapshot") or ""),
        subtasks=tuple(o.subtask for o in outcomes),
    )
    rendered = render_live_data_answer(merged_plan, outcomes) if outcomes else (
        "The retry this request matches has not produced a result yet. Please try again shortly."
    )
    return {
        "attempt": current, "rendered": rendered,
        "carried_forward_count": len(subtasks), "rerun_count": 0,
        "plan_id": plan_id, "idempotent_replay": True,
    }


class _RetryApprovalDecision:
    """Minimal shim matching the shape `run_live_data_plan` expects from
    `core.mode_permission_policy.decide_tool_call`'s return value (`.allowed`/`.reason`/`.effect`)
    -- used only to carry a PERSISTED approval override, never to replace the real policy gate."""

    __slots__ = ("allowed", "effect", "reason")

    def __init__(self, *, allowed: bool, reason: str = "", effect: str = "") -> None:
        self.allowed = allowed
        self.reason = reason
        self.effect = effect


def _resolve_retry_approval_decisions(
    rerun_plan: LiveDataPlan, rerun_rows: list[dict[str, Any]], *,
    source_context: dict[str, Any] | None, parent_attempt_id: str,
) -> dict[str, Any]:
    """Repair 8 (Mnemosyne review): the retry path used to call `run_live_data_plan(...,
    approval_decisions=None)` -- no approval evaluation at all. This calls the SAME real live gate
    a first execution uses (`evaluate_approval_policy`), then, for any subtask that gate does NOT
    allow, consults a PERSISTED durable approval (`runtime_attempt_approvals`, keyed to the
    PARENT's own subtask id -- rerun subtasks mint a fresh id per generation, so the persisted
    approval was necessarily recorded against the parent's) via the same digest-scoped validity
    check `is_approval_valid` already proves: changed arguments/plan invalidate it, an expired
    approval is not reused, and a daemon restart -- which loses nothing but also grants nothing --
    can never turn an in-memory approval into a durable one, because there IS no in-memory approval
    here at all; only what was actually recorded is ever consulted. A live "allowed" decision is
    never downgraded by a stale persisted denial -- only a live "not allowed" can be UPGRADED by a
    valid persisted allow."""
    from core.attempt_approval import (
        CURRENT_APPROVAL_POLICY_VERSION,
        compute_action_digest,
        compute_plan_digest,
        is_approval_valid,
    )
    from core.live_data_plan import evaluate_approval_policy
    from core.runtime_continuity import latest_attempt_approval_for_subtask

    live_decisions = evaluate_approval_policy(rerun_plan, source_context=source_context)
    action_digests = {
        task.subtask_id: compute_action_digest(task.tool_intent, task.arguments) for task in rerun_plan.subtasks
    }
    plan_digest = compute_plan_digest(list(action_digests.values()))

    resolved: dict[str, Any] = {}
    for task, row in zip(rerun_plan.subtasks, rerun_rows, strict=True):
        decision = live_decisions.get(task.subtask_id)
        if decision is None or getattr(decision, "allowed", True):
            resolved[task.subtask_id] = decision
            continue
        original_subtask_id = str(row.get("subtask_id") or "")
        approval = latest_attempt_approval_for_subtask(parent_attempt_id, original_subtask_id)
        valid, reason = is_approval_valid(
            approval, action_digest=action_digests[task.subtask_id], plan_digest=plan_digest,
            policy_version=CURRENT_APPROVAL_POLICY_VERSION,
        )
        resolved[task.subtask_id] = _RetryApprovalDecision(allowed=True, reason=reason) if valid else decision
    return resolved


def _refused_slot_rerun_plan(
    parent_attempt: dict[str, Any],
    *,
    plan_id: str,
    attempt_id: str,
    source_context: dict[str, Any] | None,
) -> tuple[tuple[Any, ...], list[dict[str, Any]], tuple[str, ...]]:
    """(subtasks, persistence rows, slot texts with no lane) for a retry driven by the ledger.

    AUD-20260829-003 (C10): "retry that exact failed request" must re-run THAT slot. The parent
    turn of the defect this closes has no typed subtasks at all -- it was served by a fast path --
    so the only record of what went unanswered is the demand ledger, and that is what is re-planned
    here, one recorded slot at a time through the ordinary `build_live_data_plan`.

    A slot the planner still cannot turn into a lookup comes back in the third element rather than
    being dropped. That is the honest outcome for `water tempperature in baltc sea`: no lane reads
    it, and saying so is the answer. Nothing in this function can produce a value.
    """
    from core.live_data_plan import build_live_data_plan
    from core.refused_slot_register import refused_slots_for_attempt

    try:
        slots = refused_slots_for_attempt(parent_attempt)
    except Exception:
        return (), [], ()
    if not slots:
        return (), [], ()
    subtasks: list[Any] = []
    rows: list[dict[str, Any]] = []
    unresolved: list[str] = []
    seen_subtask_ids: set[str] = set()
    for slot in slots:
        try:
            plan = build_live_data_plan(
                slot.text,
                plan_id=plan_id,
                attempt_id=attempt_id,
                source_context=dict(source_context or {}),
            )
        except Exception:
            plan = None
        planned = tuple(getattr(plan, "subtasks", ()) or ()) if plan is not None else ()
        if not planned:
            unresolved.append(slot.text)
            continue
        for subtask in planned:
            subtask_id = str(getattr(subtask, "subtask_id", "") or "")
            if subtask_id in seen_subtask_ids:
                continue
            seen_subtask_ids.add(subtask_id)
            subtasks.append(subtask)
            rows.append(
                {
                    "subtask_id": subtask_id,
                    "entity_type": str(getattr(subtask, "entity_type", "") or ""),
                    "entity_key": str(getattr(subtask, "entity", "") or ""),
                    "failure_class": "unanswered slot",
                    "result_version": 0,
                    # Which recorded slot this subtask was planned FOR. Without it a subtask
                    # that comes back with no outcome cannot be traced to the slot it was
                    # supposed to answer, and the slot disappears from the reply.
                    "_slot_text": slot.text,
                }
            )
    return tuple(subtasks), rows, tuple(unresolved)


def _execute_attempt_retry_locked(
    parent_attempt: dict[str, Any],
    parent_subtasks: list[dict[str, Any]],
    *,
    session_id: str,
    checkpoint_id: str,
    trigger_user_turn_id: str = "",
    resolution_intent: str = "",
    source_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from core.agent_runtime.live_data_render import render_live_data_answer
    from core.agent_runtime.live_data_runner import run_live_data_plan
    from core.runtime_continuity import (
        AttemptRole,
        create_runtime_attempt,
        finalize_runtime_attempt,
        upsert_runtime_attempt_subtask,
    )

    plan_id = f"livedata-retry-{uuid.uuid4().hex[:12]}"
    now_iso = datetime.now(timezone.utc).isoformat()
    # Same canonical turn = the retry was triggered by the same turn that produced the parent's
    # results (the idempotent replay of one turn's own retry, or a same-turn re-entry). Anything
    # else is a LATER turn asking to reuse an earlier turn's evidence, and `plan_retry_generation`
    # then demands a freshness policy before carrying a SUCCEEDED result forward. The chain
    # binding -- same bound evidence set -- is structural here: the rows are read from the parent
    # attempt of this retry chain, never from session membership.
    parent_trigger_turn = str(parent_attempt.get("trigger_user_turn_id") or "")
    same_canonical_turn = bool(trigger_user_turn_id) and trigger_user_turn_id == parent_trigger_turn
    carry_forward_rows, rerun_rows = plan_retry_generation(
        parent_subtasks, plan_id=plan_id, now_iso=now_iso, same_canonical_turn=same_canonical_turn
    )

    new_attempt = create_runtime_attempt(
        session_id=session_id,
        checkpoint_id=checkpoint_id,
        original_request=str(parent_attempt.get("original_request_snapshot") or ""),
        answer_mode=str(parent_attempt.get("answer_mode") or ""),
        plan_id=plan_id,
        origin_user_turn_id=str(parent_attempt.get("origin_user_turn_id") or ""),
        trigger_user_turn_id=trigger_user_turn_id,
        # Final Repair 3 (Mnemosyne final review, 2026-08-07): generation 1 carried this field,
        # but every retry generation lost it back to empty string -- create_runtime_attempt was
        # simply never passed it here. Propagated from the parent so it survives the whole chain.
        origin_conversation_event_id=str(parent_attempt.get("origin_conversation_event_id") or ""),
        root_attempt_id=str(parent_attempt.get("root_attempt_id") or parent_attempt.get("attempt_id") or ""),
        parent_attempt_id=str(parent_attempt.get("attempt_id") or ""),
        # R-3 (H1 §2): generation is allocated by the L0 fence CAS inside the
        # mint unit when a fence row governs this execution; the caller no
        # longer computes parent+1.
        resolution_intent=resolution_intent,
        # ARCH-TRUTH-R1c: a retry is a new generation of the SAME chain (root propagated
        # above), and says so in its role — so a later follow-up finds the retry rather
        # than the turn-door row of whatever turn asked for it.
        role=AttemptRole.RETRY.value,
    )
    if new_attempt.get("idempotent_replay"):
        # We lost the DB race between the probe at the top of execute_attempt_retry and this
        # INSERT (another thread/process won in between) -- render the winner's result instead of
        # duplicating its work.
        return _render_idempotent_replay(new_attempt, parent_attempt=parent_attempt)
    attempt_id = str(new_attempt.get("attempt_id") or "")
    new_generation = int(new_attempt.get("execution_generation") or 1)
    parent_id = str(parent_attempt.get("attempt_id") or "")
    parent_generation = int(parent_attempt.get("execution_generation") or 1)

    carried_outcomes: list[SubtaskOutcome] = []
    for row in carry_forward_rows:
        outcome = _carried_forward_outcome(row, plan_id=plan_id)
        carried_outcomes.append(outcome)
        upsert_runtime_attempt_subtask(
            attempt_id=attempt_id, subtask_id=outcome.subtask.subtask_id, execution_generation=new_generation,
            plan_id=plan_id,
            operation=outcome.subtask.operation, entity_type=str(row.get("entity_type") or ""),
            entity_key=str(row.get("entity_key") or ""), arguments=dict(outcome.subtask.arguments),
            lifecycle_state=outcome.state.name, result_summary=outcome.result or {},
            failure_class=str(row.get("failure_class") or ""), failure_reason=outcome.failure_reason,
            approval_state=outcome.approval_decision,
            retryable=bool(row.get("retryable")), retry_reason=str(row.get("retry_reason") or ""),
            carried_forward_from_attempt_id=parent_id, carried_forward_from_generation=parent_generation,
            previous_result_version=int(row.get("result_version") or 0),
            queued_at=row.get("queued_at"), started_at=row.get("started_at"),
            completed_at=row.get("completed_at"), retrieved_at=row.get("retrieved_at"),
        )

    rerun_subtasks = [_subtask_from_row(row, plan_id=plan_id) for row in rerun_rows]
    # AUD-20260829-003 (C10). A turn served by a closed-contract fast path persists NO typed
    # subtasks, so a retry of it had nothing to carry forward and nothing to re-run -- it minted an
    # empty generation and rendered nothing, and the follow-up went to the model, which invented a
    # value for the very slot the turn had disclosed as unanswered. The demand ledger DOES hold
    # what was asked and not delivered, so that is what a retry re-runs: the recorded slot text,
    # through the ordinary live-data planner. Not a fresh search over the follow-up phrase -- the
    # incident `core.attempt_followup`'s docstring records -- and not a search over the whole
    # original request either: one plan per recorded slot.
    unresolved_slot_texts: tuple[str, ...] = ()
    if not rerun_subtasks and not carry_forward_rows:
        slot_subtasks, slot_rows, unresolved_slot_texts = _refused_slot_rerun_plan(
            parent_attempt, plan_id=plan_id, attempt_id=attempt_id, source_context=source_context
        )
        if slot_subtasks:
            rerun_subtasks = list(slot_subtasks)
            rerun_rows = list(rerun_rows) + list(slot_rows)
    fresh_outcomes: list[SubtaskOutcome] = []
    if rerun_subtasks:
        rerun_plan = LiveDataPlan(
            plan_id=plan_id, attempt_id=attempt_id,
            original_request=str(parent_attempt.get("original_request_snapshot") or ""),
            subtasks=tuple(rerun_subtasks),
        )
        retry_approval_decisions = _resolve_retry_approval_decisions(
            rerun_plan, rerun_rows, source_context=source_context, parent_attempt_id=parent_id,
        )
        fresh_outcomes = list(run_live_data_plan(rerun_plan, approval_decisions=retry_approval_decisions))
        # A retry that re-runs subtasks really reaches the network, exactly as the first attempt
        # did, and must receipt it for the same reason `apps.vool_agent` receipts the first
        # attempt: a lane that fetches while reporting nothing leaves the turn's accounting false
        # and leaves every evidence-sufficiency reader blind to work that really happened. Measured
        # once the attribution exemption came out -- a retry that re-fetched Atlantisxyzabc123
        # successfully had its rendered answer replaced with "I didn't run any live lookup on this
        # turn", because nothing on the turn said otherwise.
        from core.live_data_retrieval_receipts import publish_live_data_retrieval_receipts

        publish_live_data_retrieval_receipts(
            source_context, fresh_outcomes, plan_id=plan_id, attempt_id=rerun_plan.attempt_id
        )
        # NOT strict: `run_live_data_plan` can produce fewer outcomes than `rerun_rows` (a subtask
        # whose approval was denied is excluded from execution entirely, not given a "denied"
        # outcome) -- proven by a real sabotage test that this length divergence genuinely occurs,
        # not just in theory. `strict=True` here would turn that expected case into a crash.
        for outcome, row in zip(fresh_outcomes, rerun_rows, strict=False):
            state_name = outcome.state.name
            failure_class = "transient" if state_name == "FAILED" else ("unsupported" if state_name == "UNSUPPORTED_ENTITY" else "")
            # Repair 7: a row `plan_retry_generation` moved into rerun for STALENESS (not a prior
            # failure) carries its own `_refresh_reason` -- persist it as refresh_required/
            # refresh_reason and use an honest rerun_reason instead of the misleading
            # "transient failure" default a SUCCEEDED row's empty failure_class would fall back to.
            stale_reason = str(row.get("_refresh_reason") or "")
            if stale_reason:
                rerun_reason = f"refreshed: {stale_reason}"
            else:
                prior_failure_class = str(row.get("failure_class") or "transient failure")
                rerun_reason = f"{prior_failure_class} in generation {parent_generation}"
            upsert_runtime_attempt_subtask(
                attempt_id=attempt_id, subtask_id=outcome.subtask.subtask_id, execution_generation=new_generation,
                plan_id=plan_id,
                operation=outcome.subtask.operation, entity_type=str(row.get("entity_type") or ""),
                entity_key=str(row.get("entity_key") or ""), arguments=dict(outcome.subtask.arguments),
                lifecycle_state=state_name, result_summary=outcome.result or {},
                failure_class=failure_class, failure_reason=outcome.failure_reason,
                approval_state=outcome.approval_decision,
                retryable=state_name == "FAILED", retry_reason="transient tool failure" if state_name == "FAILED" else "",
                refresh_required=bool(stale_reason), refresh_reason=stale_reason,
                rerun_reason=rerun_reason,
                previous_result_version=int(row.get("result_version") or 0),
                queued_at=outcome.queued_at_iso or None, started_at=outcome.started_at_iso or None,
                completed_at=outcome.completed_at_iso or None,
                retrieved_at=str((outcome.result or {}).get("retrieved_at") or (outcome.result or {}).get("observed_at") or "") or None,
            )

    all_outcomes = carried_outcomes + fresh_outcomes
    merged_plan = LiveDataPlan(
        plan_id=plan_id, attempt_id=attempt_id,
        original_request=str(parent_attempt.get("original_request_snapshot") or ""),
        subtasks=tuple(o.subtask for o in all_outcomes),
    )
    rendered = render_live_data_answer(merged_plan, all_outcomes)
    # EVERY recorded slot the retry did not answer is disclosed -- not only the ones no lane
    # could plan.
    #
    # Measured: a retry that re-planned all three recorded slots and got zero outcomes back
    # rendered the EMPTY STRING. The merged plan is built from outcomes, so with no outcomes it
    # has no subtasks, and the renderer -- which does synthesize a row for a planned subtask
    # with no outcome -- was handed nothing to synthesize from. The unresolved branch could not
    # cover it either: those slots WERE plannable, so the list was empty. A retry that silently
    # returns nothing is the C7 silent drop on the C10 path, and it is what let the follow-up
    # go to the model and invent a value for the slot.
    #
    # A slot is answered here only if a subtask planned for it produced a usable outcome. The
    # rest are served as the LEDGER holds them: its text, its recorded reason. No quantity, no
    # synthesis, nothing that could be filled in downstream.
    answered_slot_texts = {
        str(row.get("_slot_text") or "")
        for outcome, row in zip(fresh_outcomes, rerun_rows, strict=False)
        if outcome.ok and row.get("_slot_text")
    }
    unanswered_slot_texts = tuple(
        dict.fromkeys(
            [
                text
                for text in (str(row.get("_slot_text") or "") for row in rerun_rows)
                if text and text not in answered_slot_texts
            ]
            + list(unresolved_slot_texts)
        )
    )
    if unanswered_slot_texts:
        # Rows are ledger text, carry no quantity, and use the disclosure shape the sweep
        # already serves.
        from core.conductor.obligation_ledger import RSS_REASON_NO_LANE

        rows = "\n".join(f"* {text} — {RSS_REASON_NO_LANE}" for text in unanswered_slot_texts)
        head = (
            "Re-ran the recorded request. This part still has no answering lane, so I hold no "
            "value for it and will not state one:"
            if len(unanswered_slot_texts) == 1
            else "Re-ran the recorded request. These parts still have no answering lane, so I "
            "hold no values for them and will not state any:"
        )
        rendered = f"{rendered.strip()}\n\n{head}\n{rows}".strip()

    finalized = finalize_runtime_attempt(attempt_id, execution_generation=new_generation)
    return {
        "attempt": finalized or new_attempt,
        "rendered": rendered,
        "carried_forward_count": len(carry_forward_rows),
        "rerun_count": len(rerun_rows),
        "plan_id": plan_id,
        "idempotent_replay": False,
    }
