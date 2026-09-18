"""The operator approval store: pending actions, their atomic claim, and the durable lifecycle of
the effects approved through it.

Plain operator actions (drafts, cleanups, moves) use create / load / mark exactly as before.
Approved external effects (provider calendar operations) additionally keep ONE durable operation
record inside ``result_json`` under ``"operation"``; the law is core.operator.effect_lifecycle:

* ``phase``    -- the evidence rung reached (unsent / dispatching / accepted / verified /
  recorded). No write lowers it, except a definitive refusal of the write itself.
* ``owner``    -- the live claim: a random token, the fence it was issued under, the state it
  entered from and the claiming pid (provenance only); ``None`` while nobody holds it.
* ``fence``    -- grows by one on every claim. A write from a worker whose token/fence no longer
  match is refused, so a stale worker can never alter the winner's state.
* ``evidence`` -- identities and verification facts (provider id, correlation, last
  verification), merged and never erased by a later empty value.
* ``history``  -- a bounded trail of transitions, for diagnosis.

Liveness of an owner is proven only by its OS lock under ``<store file>.operation-locks/``: the
winning claim holds it for the whole attempt, and recovery may touch an ``executing`` row only
after taking it. Process-local state here is a handle table (like an fd table) for claims THIS
process holds; it is never evidence about another process. A store with no database file cannot
prove ownership across processes, so claims and recovery there fail closed.
"""
from __future__ import annotations

import contextlib
import json
import secrets
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from core.operator.effect_lifecycle import (
    PHASE_DISPATCHING,
    PHASE_RECORDED,
    PHASE_UNSENT,
    PHASE_VERIFIED,
    PHASES,
    RESUMABLE_STATUSES,
    OwnerLock,
    higher_phase,
    owner_lock_path,
    owner_provenance,
    phase_rank,
    resting_status,
)

OPERATION_KEY = "operation"
#: The revision-4 name for the resumable states, kept for its callers.
RESUMABLE_STATES = RESUMABLE_STATUSES
_TERMINAL_STATUSES = frozenset({"executed", "superseded", "cancelled"})
_STATUS_RANK = {"pending_approval": 0, "outcome_unproven": 1, "effect_unrecorded": 2, "executed": 3}
_HISTORY_LIMIT = 16
#: Identity fields a later write may never erase with an empty value.
_IDENTITY_KEYS = ("uid", "provider_uid", "intent_uid", "calendar_id", "etag", "href", "correlation_id")


class OperationOwnershipError(RuntimeError):
    """This worker no longer owns the operation; nothing was written."""


class OperationStateError(RuntimeError):
    """The operation already reached a state this write may not change; nothing was written."""


def create_pending_action(
    *,
    session_id: str,
    task_id: str,
    action_kind: str,
    scope: dict[str, Any],
    now_fn: Callable[[], str],
    get_connection_fn: Callable[[], Any],
) -> str:
    now = now_fn()
    action_id = str(uuid.uuid4())
    conn = get_connection_fn()
    try:
        conn.execute(
            """
            UPDATE operator_action_requests
            SET status = 'superseded', updated_at = ?
            WHERE session_id = ? AND action_kind = ? AND status = 'pending_approval'
            """,
            (now, session_id, action_kind),
        )
        conn.execute(
            """
            INSERT INTO operator_action_requests (
                action_id, session_id, task_id, action_kind, scope_json,
                result_json, status, created_at, updated_at, executed_at
            ) VALUES (?, ?, ?, ?, ?, '{}', 'pending_approval', ?, ?, NULL)
            """,
            (action_id, session_id, task_id, action_kind, json.dumps(scope, sort_keys=True), now, now),
        )
        conn.commit()
        return action_id
    finally:
        conn.close()


def load_pending_action(
    *,
    session_id: str,
    action_kind: str,
    action_id: str | None = None,
    get_connection_fn: Callable[[], Any],
) -> dict[str, Any] | None:
    conn = get_connection_fn()
    try:
        if action_id:
            row = conn.execute(
                """
                SELECT *
                FROM operator_action_requests
                WHERE action_id = ? AND session_id = ? AND action_kind = ? AND status = 'pending_approval'
                LIMIT 1
                """,
                (action_id, session_id, action_kind),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT *
                FROM operator_action_requests
                WHERE session_id = ? AND action_kind = ? AND status = 'pending_approval'
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (session_id, action_kind),
            ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ---------------------------------------------------------------------------------------------
# The durable operation record
# ---------------------------------------------------------------------------------------------


def _read_result(raw: Any) -> dict[str, Any]:
    text = str(raw or "{}")
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return {"unreadable_result_json": text[:400]}
    return value if isinstance(value, dict) else {"unreadable_result_json": text[:400]}


def operation_record(result: dict[str, Any]) -> dict[str, Any]:
    record = result.get(OPERATION_KEY) if isinstance(result, dict) else None
    return dict(record) if isinstance(record, dict) else {}


def durable_phase(status: str, result: dict[str, Any]) -> str:
    """The evidence rung a row has reached.

    Rows written before the operation record existed are inferred conservatively from their
    status: an ``executing`` or ``outcome_unproven`` row is assumed to have crossed the boundary.
    """
    inferred = {
        "executed": PHASE_RECORDED,
        "effect_unrecorded": PHASE_VERIFIED,
        "outcome_unproven": PHASE_DISPATCHING,
    }.get(str(status), PHASE_UNSENT)
    recorded = operation_record(result).get("phase")
    if recorded in PHASES:
        return higher_phase(recorded, inferred)
    if str(status) == "executing":
        return PHASE_DISPATCHING
    return inferred


def operation_evidence(row: dict[str, Any] | None) -> dict[str, Any]:
    """The evidence a stored row carries (plus its revision-4 top-level provider id)."""
    if not row:
        return {}
    result = _read_result(row.get("result_json"))
    evidence = dict(operation_record(result).get("evidence") or {})
    if not evidence.get("provider_uid") and result.get("provider_uid"):
        evidence["provider_uid"] = str(result["provider_uid"])
    evidence["phase"] = durable_phase(str(row.get("status") or ""), result)
    return evidence


def _merge_result(existing: dict[str, Any], incoming: dict[str, Any] | None) -> dict[str, Any]:
    merged = dict(existing)
    for key, value in dict(incoming or {}).items():
        if key == OPERATION_KEY:
            continue
        if key in _IDENTITY_KEYS and value in ("", None) and existing.get(key) not in ("", None):
            continue
        merged[key] = value
    return merged


def _merge_evidence(existing: dict[str, Any], incoming: dict[str, Any] | None) -> dict[str, Any]:
    merged = dict(existing or {})
    for key, value in dict(incoming or {}).items():
        if value in ("", None) and merged.get(key) not in ("", None):
            continue
        merged[key] = value
    return merged


def _identity_evidence(result: dict[str, Any] | None, *, receipt: bool) -> dict[str, Any]:
    source = dict(result or {})
    evidence = {key: source[key] for key in ("provider_uid", "intent_uid", "calendar_id", "correlation_id", "accepted",
                                             "identity_mismatch", "last_verification") if source.get(key)}
    if receipt and source.get("uid"):
        evidence.setdefault("provider_uid", source["uid"])
    return evidence


def _mirror(evidence: dict[str, Any]) -> dict[str, Any]:
    """Top-level copies revision-4 readers look for (``provider_uid``)."""
    return {"provider_uid": evidence["provider_uid"]} if evidence.get("provider_uid") else {}


def _note_history(record: dict[str, Any], event: str, now: str, note: str = "") -> None:
    history = list(record.get("history") or [])
    entry: dict[str, Any] = {"at": now, "event": event}
    if note:
        entry["note"] = str(note)[:200]
    history.append(entry)
    record["history"] = history[-_HISTORY_LIMIT:]


def _write_row(action_id: str, *, now_fn: Callable[[], str], get_connection_fn: Callable[[], Any], decide: Callable[..., Any]) -> Any:
    """Read, decide and write in ONE ``BEGIN IMMEDIATE`` transaction.

    Writers serialize across threads and processes, so a decision is always taken on the row as
    it is at the moment of writing. ``decide(status, result, now)`` returns None (write nothing)
    or ``(new_status, new_result, value)``; exceptions it raises roll everything back.
    """
    now = now_fn()
    conn = get_connection_fn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT status, result_json FROM operator_action_requests WHERE action_id = ?", (action_id,)).fetchone()
        if row is None:
            conn.rollback()
            return None
        status = str(row[0])
        decision = decide(status, _read_result(row[1]), now)
        if decision is None:
            conn.rollback()
            return None
        new_status, new_result, value = decision
        conn.execute(
            "UPDATE operator_action_requests SET status = ?, result_json = ?, updated_at = ?, "
            "executed_at = CASE WHEN ? = 'executed' THEN ? ELSE executed_at END "
            "WHERE action_id = ? AND status = ?",
            (new_status, json.dumps(new_result, sort_keys=True), now, new_status, now, action_id, status),
        )
        conn.commit()
        return value
    except BaseException:
        with contextlib.suppress(Exception):
            conn.rollback()
        raise
    finally:
        conn.close()


def _store_file(get_connection_fn: Callable[[], Any]) -> str:
    conn = get_connection_fn()
    try:
        rows = conn.execute("PRAGMA database_list").fetchall()
    finally:
        conn.close()
    for row in rows:
        if str(row[1]) == "main":
            return str(row[2] or "")
    return ""


def _owner_lock(store_file: str, action_id: str) -> OwnerLock:
    return OwnerLock(owner_lock_path(f"{store_file}.operation-locks", action_id))


# ---------------------------------------------------------------------------------------------
# Ownership: claim, record, release, recover
# ---------------------------------------------------------------------------------------------


@dataclass
class OperationClaim:
    """This process's handle on an operation it won: the OS owner lock plus the durable token.

    Held only by the winning worker, for the duration of its attempt. It says nothing about any
    other process; liveness elsewhere is decided by the OS lock alone.
    """

    action_id: str
    token: str
    fence: int
    entry_status: str
    store_file: str
    now_fn: Callable[[], str]
    get_connection_fn: Callable[[], Any]
    lock: OwnerLock = field(repr=False)

    def owns(self, result: dict[str, Any]) -> bool:
        owner = operation_record(result).get("owner")
        return isinstance(owner, dict) and owner.get("token") == self.token and int(owner.get("fence") or 0) == self.fence

    def load(self) -> dict[str, Any] | None:
        conn = self.get_connection_fn()
        try:
            row = conn.execute("SELECT * FROM operator_action_requests WHERE action_id = ?", (self.action_id,)).fetchone()
            return dict(row) if row is not None else None
        finally:
            conn.close()

    def record(self, *, phase: str, evidence: dict[str, Any] | None = None, note: str = "") -> bool:
        """Advance this attempt's durable evidence; the row stays ``executing``.

        False when this worker no longer owns the operation -- the caller must not cross the
        effect boundary. Storage failures raise; they are not ownership answers.
        """

        def decide(status: str, result: dict[str, Any], now: str):
            if status != "executing" or not self.owns(result):
                raise OperationOwnershipError(f"{self.action_id}: ownership lost before recording {phase}")
            record = operation_record(result)
            record["phase"] = higher_phase(record.get("phase"), phase)
            record["evidence"] = _merge_evidence(record.get("evidence") or {}, evidence)
            _note_history(record, f"phase:{record['phase']}", now, note)
            merged = _merge_result(result, _mirror(record["evidence"]))
            merged[OPERATION_KEY] = record
            return "executing", merged, True

        try:
            return bool(_write_row(self.action_id, now_fn=self.now_fn, get_connection_fn=self.get_connection_fn, decide=decide))
        except OperationOwnershipError:
            return False

    def release(self, *, note: str, floor_status: str = "", proven_not_applied: bool = False,
                evidence: dict[str, Any] | None = None) -> str:
        """End this attempt WITHOUT a receipt; returns the resting status.

        The resting state comes from durable evidence (``resting_status``): an attempt that never
        crossed returns to its entry state, one that crossed rests ``outcome_unproven``, a
        verified effect rests ``effect_unrecorded``. ``floor_status`` can only RAISE that result.
        ``proven_not_applied`` is the one way down: positive evidence the write was not applied
        (nothing was sent, or the provider definitively refused the write itself). It is ignored
        once the provider has accepted anything.
        """

        def decide(status: str, result: dict[str, Any], now: str):
            if status != "executing" or not self.owns(result):
                raise OperationOwnershipError(f"{self.action_id}: ownership lost before release")
            record = operation_record(result)
            phase = durable_phase(status, result)
            refused = proven_not_applied and phase_rank(phase) <= phase_rank(PHASE_DISPATCHING)
            if refused:
                phase = PHASE_UNSENT
            record["phase"] = phase
            record["evidence"] = _merge_evidence(record.get("evidence") or {}, evidence)
            owner = record.get("owner") if isinstance(record.get("owner"), dict) else {}
            target = resting_status(phase, entry_status=str(owner.get("entry_status") or self.entry_status))
            if not refused and _STATUS_RANK.get(floor_status, -1) > _STATUS_RANK.get(target, -1):
                target = floor_status
            record["owner"] = None
            _note_history(record, f"released:{target}" + (":proven_not_applied" if refused else ""), now, note)
            merged = _merge_result(result, _mirror(record["evidence"]))
            merged[OPERATION_KEY] = record
            return target, merged, target

        try:
            return str(_write_row(self.action_id, now_fn=self.now_fn, get_connection_fn=self.get_connection_fn, decide=decide) or "")
        finally:
            self.relinquish()

    def relinquish(self) -> None:
        """Drop the OS lock and this process's handle. Writes nothing: whatever the row durably
        says is what recovery will act on."""
        with _HELD_LOCK:
            if _HELD.get(self.action_id) is self:
                del _HELD[self.action_id]
        self.lock.release()


_HELD: dict[str, OperationClaim] = {}
_HELD_LOCK = threading.Lock()


def held_claim(action_id: str | None) -> OperationClaim | None:
    """The claim THIS process holds on the operation, if any."""
    with _HELD_LOCK:
        return _HELD.get(str(action_id or ""))


def relinquish_claim(action_id: str | None) -> None:
    claim = held_claim(action_id)
    if claim is not None:
        claim.relinquish()


def claim_pending_action(
    action_id: str,
    *,
    now_fn: Callable[[], str],
    get_connection_fn: Callable[[], Any],
) -> bool:
    """Atomically claim an approved operation for ONE live worker.

    Exactly one caller moves a resumable row (``pending_approval``, ``outcome_unproven``,
    ``effect_unrecorded``) to ``executing``; the winner holds the operation's OS owner lock and a
    fresh token and fence until it records a receipt or releases. Every other caller -- a
    duplicate approval, a replay, another process -- gets False and must diagnose the row
    instead of executing. A crash between claim and completion leaves ``executing`` with its
    recorded phase; recovery acts on it only once the dead owner's lock can be taken.
    """
    key = str(action_id)
    if held_claim(key) is not None:
        return False
    store_file = _store_file(get_connection_fn)
    if not store_file:
        return False
    lock = _owner_lock(store_file, key)
    if not lock.try_acquire():
        return False
    token = secrets.token_hex(16)
    won: dict[str, Any] = {}

    def decide(status: str, result: dict[str, Any], now: str):
        if status not in RESUMABLE_STATUSES:
            return None
        record = operation_record(result)
        fence = int(record.get("fence") or 0) + 1
        record["phase"] = durable_phase(status, result)
        record["fence"] = fence
        record["owner"] = {"token": token, "fence": fence, "entry_status": status, "claimed_at": now, **owner_provenance()}
        _note_history(record, f"claimed_from:{status}", now)
        merged = dict(result)
        merged[OPERATION_KEY] = record
        won.update(fence=fence, entry_status=status)
        return "executing", merged, True

    try:
        claimed = bool(_write_row(key, now_fn=now_fn, get_connection_fn=get_connection_fn, decide=decide))
    except BaseException:
        lock.release()
        raise
    if not claimed:
        lock.release()
        return False
    claim = OperationClaim(action_id=key, token=token, fence=int(won["fence"]), entry_status=str(won["entry_status"]),
                           store_file=store_file, now_fn=now_fn, get_connection_fn=get_connection_fn, lock=lock)
    with _HELD_LOCK:
        _HELD[key] = claim
    return True


def requeue_interrupted_action(
    action_id: str,
    *,
    entry_state: str,
    note: str,
    now_fn: Callable[[], str],
    get_connection_fn: Callable[[], Any],
) -> str | bool:
    """Recover an ``executing`` row ONLY when its owner is provably gone.

    The proof is the OS owner lock: while any live worker (this process or another) holds it,
    nothing is touched and False is returned. Once it can be taken, the previous owner has
    exited, and the row rests where its durable evidence puts it -- never-dispatched returns to
    the state it was claimed from, anything that crossed becomes ``outcome_unproven``, a verified
    effect becomes ``effect_unrecorded``. A row without an owner record (written before it
    existed) is treated as crossed, at least ``entry_state``. Returns the resting status. The
    fence is kept, so the dead owner's token can never write again.
    """
    key = str(action_id)
    if held_claim(key) is not None:
        return False
    store_file = _store_file(get_connection_fn)
    if not store_file:
        return False
    lock = _owner_lock(store_file, key)
    if not lock.try_acquire():
        return False

    def decide(status: str, result: dict[str, Any], now: str):
        if status != "executing":
            return None
        record = operation_record(result)
        owner = record.get("owner") if isinstance(record.get("owner"), dict) else {}
        phase = durable_phase(status, result)
        if owner:
            target = resting_status(phase, entry_status=str(owner.get("entry_status") or ""))
        else:
            target = resting_status(phase, entry_status="outcome_unproven")
            if _STATUS_RANK.get(str(entry_state), -1) > _STATUS_RANK.get(target, -1):
                target = str(entry_state)
        record["phase"] = phase
        record["owner"] = None
        _note_history(record, f"owner_gone:{target}", now, note)
        merged = dict(result)
        attempts = list(merged.get("interrupted_attempts") or [])
        attempts.append(str(note or "interrupted")[:200])
        merged["interrupted_attempts"] = attempts[-8:]
        merged[OPERATION_KEY] = record
        return target, merged, target

    try:
        return _write_row(key, now_fn=now_fn, get_connection_fn=get_connection_fn, decide=decide) or False
    finally:
        lock.release()


def _same_receipt(existing: dict[str, Any], incoming: dict[str, Any]) -> bool:
    uid = str(existing.get("uid") or "")
    return bool(uid) and uid == str((incoming or {}).get("uid") or "")


def _mark(action_id: str, *, result: dict[str, Any], now_fn: Callable[[], str], get_connection_fn: Callable[[], Any],
          phase: str, event: str) -> None:
    key = str(action_id)
    outcome: dict[str, str] = {}

    def decide(status: str, existing: dict[str, Any], now: str):
        if status in _TERMINAL_STATUSES:
            if status == "executed" and phase == PHASE_RECORDED and _same_receipt(existing, result):
                return None
            raise OperationStateError(f"{key}: already {status}; a late {event} write was refused")
        record = operation_record(existing)
        owner = record.get("owner") if isinstance(record.get("owner"), dict) else None
        claim = held_claim(key)
        if claim is not None and not claim.owns(existing):
            # This process believes it owns the operation, but the store no longer records that
            # claim (it was recovered or re-owned elsewhere): the write would alter state this
            # worker does not own, whatever the row's status is now.
            raise OperationOwnershipError(f"{key}: the store no longer records this worker's claim; its {event} write was refused")
        if status == "executing" and owner and claim is None:
            raise OperationOwnershipError(f"{key}: this worker does not own the operation; its {event} write was refused")
        merged = _merge_result(existing, result)
        managed = bool(record) or status == "executing" or phase != PHASE_RECORDED
        if not managed:
            outcome["status"] = "executed"
            return "executed", merged, True
        new_phase = higher_phase(durable_phase(status, existing), phase)
        record["phase"] = new_phase
        record["owner"] = None
        record["evidence"] = _merge_evidence(record.get("evidence") or {}, _identity_evidence(result, receipt=phase == PHASE_RECORDED))
        _note_history(record, event, now)
        merged = _merge_result(merged, _mirror(record["evidence"]))
        merged[OPERATION_KEY] = record
        new_status = resting_status(new_phase, entry_status="outcome_unproven")
        outcome["status"] = new_status
        return new_status, merged, True

    _write_row(key, now_fn=now_fn, get_connection_fn=get_connection_fn, decide=decide)
    if outcome.get("status") and outcome["status"] != "executing":
        relinquish_claim(key)


def mark_action_effect_unrecorded(
    action_id: str,
    *,
    result: dict[str, Any],
    now_fn: Callable[[], str],
    get_connection_fn: Callable[[], Any],
) -> None:
    """The effect verifiably happened but its receipt could not be persisted.

    Verify-only from here: recovery reads the recorded identity and re-marks executed; if the
    effect is gone it reports that and never re-creates it under this approval.
    """
    _mark(action_id, result=result, now_fn=now_fn, get_connection_fn=get_connection_fn, phase=PHASE_VERIFIED, event="effect_unrecorded")


def mark_action_outcome_unproven(
    action_id: str,
    *,
    result: dict[str, Any],
    now_fn: Callable[[], str],
    get_connection_fn: Callable[[], Any],
) -> None:
    """Record that the effect MAY have happened (the request crossed and its outcome is unproven).

    Not pending (a re-approval must not blindly re-fire) and not executed (no success claim);
    resumption goes through the owning executor's verify-then-reconcile path. Evidence already
    recorded (an accepted provider id, a verification) is kept: a verified effect stays
    ``effect_unrecorded``, and a completed operation refuses the write outright.
    """
    _mark(action_id, result=result, now_fn=now_fn, get_connection_fn=get_connection_fn, phase=PHASE_DISPATCHING, event="outcome_unproven")


def mark_action_executed(
    action_id: str,
    *,
    result: dict[str, Any],
    now_fn: Callable[[], str],
    get_connection_fn: Callable[[], Any],
) -> None:
    """Persist the receipt. Claimed rows accept it only from their owner; earlier identities
    and evidence are merged, never replaced; an already-executed row accepts only its own receipt."""
    _mark(action_id, result=result, now_fn=now_fn, get_connection_fn=get_connection_fn, phase=PHASE_RECORDED, event="executed")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()
