"""A0 canonical invocation ledger + L0 execution fence (K-01 / K-02).

Two logical authorities, physically merged in one module by permission of the
canonical schema contract (D1):

- **A0 owns request truth** — ``invocation_requests``: accept-once UNIQUE
  external identity, INSERT-or-SELECT, frozen bindings (principal, session,
  privacy scope, raw digest). Never advances fences.
- **L0 owns execution truth** — ``executions``: generation CAS, epoch-in-
  predicate refuse, MAX_GENERATION ceiling, root budget drawdown
  (decrement-not-reset), terminal transitions. Never accepts requests.

Ownership never overlaps: A0 functions below write only invocation_requests;
L0 functions write only executions.

Laws enforced mechanically:

- ACCEPT-ONCE: UNIQUE(external_kind, external_value); same identity redelivered
  resolves to the SAME req:, never a second admission.
- BIND-BEFORE-WORK: no UPDATE path exists for accepted bindings.
- DEFAULT-DENY PRINCIPAL: a missing/unprovable principal is REFUSED — it never
  becomes owner_local.
- FENCE-OR-REFUSE: every L0 transition is a conditional UPDATE whose rowcount
  is the lease; rowcount 0 raises :class:`FenceRefused`.
- EPOCH REFUSE: runtime_epoch is stamped AND compared in every predicate —
  a writer from a dead process incarnation is refused (stored-but-unread
  epoch is failure, D3).
- ROOT-BUDGET-INHERITS: budget charges decrement the root row; retries never
  reset it; MAX_GENERATION caps the retry chain.
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any

from storage.db import get_connection

_LOCK = threading.Lock()


class InvocationConflict(ValueError):
    """Same external identity delivered with DIFFERENT bytes: refuse + alarm."""


class PrincipalDenied(PermissionError):
    """Missing/unprovable principal context: DEFAULT DENY (never OWNER)."""


class FenceRefused(RuntimeError):
    """FENCE-OR-REFUSE: conditional write matched zero rows."""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Runtime epoch (D3): stamped by L0 at boot, COMPARED in authoritative
# predicates. Reuses the existing process-incarnation identity — ONE question,
# ONE owner: there is exactly one process incarnation id per runtime.
# ---------------------------------------------------------------------------

_EPOCH_CACHE: str = ""


def current_runtime_epoch() -> str:
    global _EPOCH_CACHE
    with _LOCK:
        if not _EPOCH_CACHE:
            from core.runtime_continuity import _process_instance_id

            _EPOCH_CACHE = _process_instance_id()
        return _EPOCH_CACHE


def reset_runtime_epoch_for_tests() -> None:
    global _EPOCH_CACHE
    with _LOCK:
        _EPOCH_CACHE = ""


# ---------------------------------------------------------------------------
# A0 — invocation_requests
# ---------------------------------------------------------------------------

_VALID_PRINCIPALS = ("owner_local",)


def validate_principal(principal: str) -> str:
    """The ONE principal vocabulary validator (reused by A8 replay scope).

    A8 must refuse replay without a principal (fail-closed) but must not grow
    a second permission authority — so it reuses this ledger validator.
    """
    return _validate_principal(principal)


def _validate_principal(principal: str) -> str:
    clean = str(principal or "").strip()
    if not clean:
        # Missing context MUST NOT become OWNER: default deny.
        raise PrincipalDenied("principal context absent: refusing invocation")
    if clean.startswith("channel:"):
        return clean
    if clean in _VALID_PRINCIPALS:
        return clean
    raise PrincipalDenied(f"unrecognized principal class: {clean[:60]!r}")


def accept_invocation(
    *,
    external_kind: str,
    external_value: str,
    principal: str,
    session_binding: str = "",
    privacy_local_only: bool = True,
    raw_digest: str = "",
) -> dict[str, Any]:
    """Accept-once door. Returns {'request_id', 'outcome'} where outcome is
    ACCEPTED_FIRST | ACCEPTED_IDENTICAL. Raises InvocationConflict when the
    same external identity arrives with different frozen bytes/digest."""
    kind = str(external_kind or "").strip()
    value = str(external_value or "").strip()
    if not kind or not value:
        raise ValueError("external_kind and external_value are required")
    validated_principal = _validate_principal(principal)
    digest = str(raw_digest or "")
    conn = get_connection()
    try:
        try:
            cursor = conn.execute(
                """
                INSERT INTO invocation_requests (
                    request_id, external_kind, external_value, principal,
                    session_binding, privacy_local_only, raw_digest,
                    accepted_at, state
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ACCEPTED')
                """,
                (
                    f"req:{kind}:{value}",
                    kind,
                    value,
                    validated_principal,
                    str(session_binding or ""),
                    1 if privacy_local_only else 0,
                    digest,
                    _utcnow(),
                ),
            )
            conn.commit()
            if cursor.rowcount == 1:
                return {
                    "request_id": cursor.lastrowid and f"req:{kind}:{value}",
                    "outcome": "ACCEPTED_FIRST",
                }
        except sqlite3.IntegrityError:
            conn.rollback()
        row = conn.execute(
            "SELECT * FROM invocation_requests WHERE external_kind = ? AND external_value = ?",
            (kind, value),
        ).fetchone()
        if row is None:
            raise RuntimeError("accept-once race lost with no visible row")
        stored_digest = str(row["raw_digest"] or "")
        if stored_digest != digest:
            raise InvocationConflict(
                f"external identity {kind}:{value[:80]} redelivered with different bytes"
            )
        return {"request_id": str(row["request_id"]), "outcome": "ACCEPTED_IDENTICAL"}
    finally:
        conn.close()


def get_invocation(request_id: str) -> dict[str, Any] | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM invocation_requests WHERE request_id = ?", (str(request_id or ""),)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# L0 — executions fence
# ---------------------------------------------------------------------------

DEFAULT_MAX_GENERATION = 5

EXECUTION_TERMINAL_STATES = (
    "COMPLETED",
    "FAILED",
    "CANCELLED",
    "SUPERSEDED",
    "DEADLINE_EXCEEDED",
    "BUDGET_EXHAUSTED",
)


def open_execution(
    *,
    request_id: str,
    root_attempt_id: str,
    runtime_epoch: str | None = None,
    max_generation: int = DEFAULT_MAX_GENERATION,
    budget_remaining_calls: int | None = None,
    budget_remaining_effects: int | None = None,
) -> dict[str, Any]:
    """Open (or idempotently reopen-read) the execution fence for a request.

    execution_id ≡ promoted retry-chain root_attempt_id (aliased, D2 — no new
    entropy). Generation starts at 0 and only advances via :func:`bump_generation`.
    """
    req = get_invocation(request_id)
    if req is None:
        raise ValueError(f"unknown request_id: {request_id!r} (A0 must accept first)")
    ex_id = str(root_attempt_id or "").strip()
    if not ex_id:
        raise ValueError("root_attempt_id required: execution_id aliases it")
    epoch = runtime_epoch or current_runtime_epoch()
    conn = get_connection()
    try:
        try:
            conn.execute(
                """
                INSERT INTO executions (
                    execution_id, request_id, generation, runtime_epoch,
                    max_generation, budget_remaining_calls,
                    budget_remaining_effects, state
                ) VALUES (?, ?, 0, ?, ?, ?, ?, 'ACTIVE')
                """,
                (
                    ex_id,
                    request_id,
                    epoch,
                    int(max_generation),
                    budget_remaining_calls,
                    budget_remaining_effects,
                ),
            )
            conn.commit()
            outcome = "OPENED_FIRST"
        except sqlite3.IntegrityError:
            conn.rollback()
            outcome = "OPENED_IDENTICAL"
        row = conn.execute(
            "SELECT * FROM executions WHERE execution_id = ?", (ex_id,)
        ).fetchone()
        result = dict(row)
        result["outcome"] = outcome
        return result
    finally:
        conn.close()


def get_execution(execution_id: str) -> dict[str, Any] | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM executions WHERE execution_id = ?", (str(execution_id or ""),)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def restamp_dead_epoch(execution_id: str, *, runtime_epoch: str | None = None) -> bool:
    """A9 RC-6 epoch ADOPTION for a provably dead writer's fence row.

    The epoch predicate exists to kill writes from a dead process — but the boot
    recovery sweep (`mark_stale_runtime_attempts_abandoned`) deliberately marks the
    dead process's attempts ABANDONED+retryable so a later explicit "retry" can bind
    to them. Those two policies collided: adoption of a pre-restart chain could never
    mint (MintRefused forever), while the sweep advertised retryability.

    This is the narrow, single transition that reconciles them: re-stamp an ACTIVE
    execution row onto the CURRENT writer's epoch. The CALLER owns the policy gate —
    it may only be invoked when the chain was abandoned by restart recovery (checked
    in `create_runtime_attempt`); this function itself refuses nothing else and never
    touches a terminal or already-current-epoch row. All CAS predicates stay intact
    afterwards.
    """
    new_epoch = runtime_epoch or current_runtime_epoch()
    conn = get_connection()
    try:
        cursor = conn.execute(
            """
            UPDATE executions SET runtime_epoch = ?
            WHERE execution_id = ? AND state = 'ACTIVE'
              AND runtime_epoch IS NOT NULL AND runtime_epoch != ?
            """,
            (str(new_epoch), str(execution_id or ""), str(new_epoch)),
        )
        conn.commit()
        return cursor.rowcount == 1
    finally:
        conn.close()


def rearm_terminal_chain(execution_id: str, *, runtime_epoch: str | None = None) -> bool:
    """R-3/A9 companion to `restamp_dead_epoch`: re-arm a TERMINAL chain for an
    explicit user retry.

    A chain's fence row goes terminal when its turn completes (the turn door
    terminalizes its own fence). An explicit user retry of that chain is a new
    user-authorized generation on the SAME chain -- but `bump_generation`
    requires ACTIVE, so every retry of a finished chain minted MintRefused and
    the follow-up served one fixed failure string (AUD-20260829-003 C10/R1,
    deterministic on every prompt).

    The CALLER owns the policy gate exactly as with epoch adoption: re-arm only
    for an explicit user retry/REPEAT resolution with no live attempt anywhere
    on the chain. This transition does not bypass the ceiling, does not touch
    the generation, and re-stamps the epoch so a cross-restart retry lands on
    the live writer. Every CAS predicate stays intact afterwards.
    """
    new_epoch = runtime_epoch or current_runtime_epoch()
    conn = get_connection()
    try:
        cursor = conn.execute(
            """
            UPDATE executions SET state = 'ACTIVE', runtime_epoch = ?
            WHERE execution_id = ?
              AND state IN ('COMPLETED', 'FAILED', 'CANCELLED')
              AND generation < max_generation
            """,
            (new_epoch, str(execution_id or "")),
        )
        conn.commit()
        return cursor.rowcount == 1
    finally:
        conn.close()


def bump_generation(execution_id: str, *, runtime_epoch: str | None = None) -> int:
    """Monotone generation CAS. Rowcount IS the lease; refusal means a stale
    writer or an exhausted chain (MAX_GENERATION ceiling).

    F-03 (independent-proof repair): allocation and authoritative persistence
    are ONE statement (UPDATE ... RETURNING) — the previous commit-then-SELECT
    read the generation after committing, so under WAL contention two writers
    could both observe (and persist onto minted attempts) the same skewed
    value. There is no read-after-commit window anymore."""
    epoch = runtime_epoch or current_runtime_epoch()
    conn = get_connection()
    try:
        cursor = conn.execute(
            """
            UPDATE executions SET generation = generation + 1
            WHERE execution_id = ? AND runtime_epoch = ?
              AND state = 'ACTIVE' AND generation < max_generation
            RETURNING generation
            """,
            (str(execution_id or ""), epoch),
        )
        row = cursor.fetchone()
        if cursor.rowcount != 1 or row is None:
            conn.rollback()
            raise FenceRefused(
                f"generation bump refused for {execution_id!r} (epoch/state/ceiling)"
            )
        conn.commit()
        return int(row["generation"])
    finally:
        conn.close()


def require_generation(execution_id: str, generation: int, *, runtime_epoch: str | None = None) -> None:
    """Fence check for an outcome-bearing durable write: presents
    (execution_id, generation) + epoch; raises FenceRefused unless the fence
    row matches EXACTLY. Callers fold this into their UPDATE predicates or
    verify before commit.

    Staleness is generation/epoch drift, never the row's terminal state: the
    turn door terminalizes its own fence (R-3/A9 RC-3) BEFORE post-turn
    writers present the sealed tuple — delivery truth (R-7/A-6) marks the
    transport handoff of bytes that generation already owns. A terminal row
    cannot bump a new generation (bump requires ACTIVE), so a matching tuple
    on one is unambiguously its owner; a retry always carries a different
    generation and stays refused."""
    epoch = runtime_epoch or current_runtime_epoch()
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT 1 FROM executions
            WHERE execution_id = ? AND generation = ? AND runtime_epoch = ?
            """,
            (str(execution_id or ""), int(generation), epoch),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise FenceRefused(
            f"fence mismatch: execution={execution_id!r} generation={generation}"
        )


def set_execution_terminal(
    execution_id: str,
    terminal_state: str,
    *,
    runtime_epoch: str | None = None,
    expect_generation: int | None = None,
) -> bool:
    """Terminal transition, ACTIVE -> terminal only, epoch-in-predicate.
    Returns False (never raises) when the fence refused: callers log discard."""
    if terminal_state not in EXECUTION_TERMINAL_STATES:
        raise ValueError(f"not an execution terminal state: {terminal_state!r}")
    epoch = runtime_epoch or current_runtime_epoch()
    sql = "UPDATE executions SET state = ? WHERE execution_id = ? AND state = 'ACTIVE' AND runtime_epoch = ?"
    params: list[Any] = [terminal_state, str(execution_id or ""), epoch]
    if expect_generation is not None:
        sql += " AND generation = ?"
        params.append(int(expect_generation))
    conn = get_connection()
    try:
        cursor = conn.execute(sql, tuple(params))
        conn.commit()
        return cursor.rowcount == 1
    finally:
        conn.close()


def charge_root_budget(
    execution_id: str,
    *,
    calls: int = 0,
    effects: int = 0,
    runtime_epoch: str | None = None,
) -> bool:
    """ROOT-BUDGET-INHERITS: decrement-not-reset on the ROOT row. Retries and
    new generations draw down the same custody; insufficient budget refuses."""
    epoch = runtime_epoch or current_runtime_epoch()
    conn = get_connection()
    try:
        cursor = conn.execute(
            """
            UPDATE executions
            SET budget_remaining_calls = budget_remaining_calls - ?,
                budget_remaining_effects = budget_remaining_effects - ?
            WHERE execution_id = ? AND runtime_epoch = ? AND state = 'ACTIVE'
              AND budget_remaining_calls >= ? AND budget_remaining_effects >= ?
            """,
            (int(calls), int(effects), str(execution_id or ""), epoch, int(calls), int(effects)),
        )
        conn.commit()
        return cursor.rowcount == 1
    finally:
        conn.close()
