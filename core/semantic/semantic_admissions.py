"""K-04 durable A2 admission referent.

Admission precedes reference (REFERENT-BEFORE-REFERENCE): a thin insert-once
``semantic_admissions`` row must exist before any downstream authority (A7)
cites the admitted ``sr:`` id. The volatile ContextVar admission record stays
the in-process truth; THIS row is the durable referent that makes citations
verifiable.

Six-column thin representation per CANONICAL_SCHEMA_CONTRACT (D5):
``(sr_id PK, request_id, admitted_at, source_class, obligation_set_version,
accepted)``. Insert-once: a second admission of the same id is a no-op, never
an overwrite. Forward-only: legacy stores receive NO backfilled rows
(MIGRATION_CONTRACT law 4).
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

from storage.db import get_connection

_CURRENT_REQUEST_ID: ContextVar[str] = ContextVar("invocation_request_id", default="")

# R-4 (H-2): the L0 fence tuple of the current turn, bound by the turn door
# (_r3_open_turn_execution). Outcome-bearing writers consult this and present
# `(execution_id, generation, runtime_epoch)`; a writer that cannot present the
# current tuple must discard (FENCE-OR-REFUSE).
_EXECUTION_IDENTITY: ContextVar[dict | None] = ContextVar("execution_identity", default=None)


def set_execution_context(identity: dict) -> Any:
    return _EXECUTION_IDENTITY.set(dict(identity or {}))


def current_execution_identity() -> dict | None:
    value = _EXECUTION_IDENTITY.get()
    return dict(value) if isinstance(value, dict) else None


def clear_execution_context() -> None:
    """Turn-scope hygiene: a bound fence tuple must never leak into the next
    turn on the same thread/context."""
    _EXECUTION_IDENTITY.set(None)


def enforce_fence_if_onboarded() -> None:
    """Fail-closed fence check for onboarded lanes: when an execution identity
    is bound, the CURRENT fence tuple must match (H1 INV-1). Unbound (legacy)
    lanes are not yet onboarded and skip."""
    identity = current_execution_identity()
    if not identity:
        return
    from core.invocation.ledger import require_generation

    require_generation(
        str(identity.get("execution_id") or ""),
        int(identity.get("generation") or 0),
        runtime_epoch=str(identity.get("runtime_epoch") or ""),
    )


def set_request_context(request_id: str) -> Any:
    """Bind the A0 request id for the current execution context (ingress law:
    <turn_id> and admission rows are fed FROM req:, never invented)."""
    return _CURRENT_REQUEST_ID.set(str(request_id or "").strip())


def current_request_id() -> str:
    return _CURRENT_REQUEST_ID.get()


@contextmanager
def bound_request_context(request_id: str):
    """OWNING lifecycle for a request-id binding (P1 request-context isolation).

    Binds ``request_id`` for the duration of the scope and restores the
    PREVIOUS value — via the ContextVar token, so normal exit, exception
    unwinding, and nesting all restore exactly — never a cleared default that
    would clobber an outer scope. ContextVar semantics keep concurrent
    threads/tasks isolated. The A0 door (``core.web.api.service.dispatch_post``)
    and the interior fallback (``agent.run_once``) bind with this same token
    discipline inline; every OTHER binder MUST go through this seam. A raw
    ``set_request_context`` without a token reset leaks the id into every later
    turn on the same context — the test-order poisoning this seam exists to
    make unrepresentable (R5 request-context leak, 2026-09-01)."""
    token = set_request_context(request_id)
    try:
        yield current_request_id()
    finally:
        _CURRENT_REQUEST_ID.reset(token)


@contextmanager
def bound_execution_context(identity: dict):
    """OWNING lifecycle for a turn fence-tuple binding, same discipline as
    :func:`bound_request_context`: the PREVIOUS binding (including an outer
    scope's) is restored on normal exit and on exception. Unlike
    :func:`clear_execution_context` — a top-of-turn sweep to the unbound
    default, correct only where no outer scope owns a binding — the scoped form
    never destroys a fence it does not own."""
    token = set_execution_context(identity)
    try:
        yield current_execution_identity()
    finally:
        _EXECUTION_IDENTITY.reset(token)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def record_admission(
    semantic_result_id: str,
    *,
    source_class: str,
    obligation_set_version: str = "",
    accepted: bool = True,
    request_id: str | None = None,
) -> str:
    """Insert-once durable referent. Returns ACCEPTED_FIRST | IDENTICAL."""
    clean_sr = str(semantic_result_id or "").strip()
    if not clean_sr:
        raise ValueError("semantic_result_id required")
    req = str(request_id if request_id is not None else current_request_id()).strip()
    conn = get_connection()
    try:
        try:
            conn.execute(
                """
                INSERT INTO semantic_admissions (
                    sr_id, request_id, admitted_at, source_class,
                    obligation_set_version, accepted
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    clean_sr,
                    req,
                    _utcnow(),
                    str(source_class or "unknown"),
                    str(obligation_set_version or ""),
                    1 if accepted else 0,
                ),
            )
            conn.commit()
            return "ACCEPTED_FIRST"
        except sqlite3.IntegrityError:
            conn.rollback()
        # Insert-once conflict: classify honestly per durable-cas (A-2) —
        # identical stored row ⇒ ACCEPTED_IDENTICAL; a different stored row
        # under the same id is a REJECTED_DIFFERENT refusal, never a silent
        # 'IDENTICAL'. Any non-conflict storage failure propagates.
        stored = get_admission(clean_sr)
        if stored is None:
            raise RuntimeError(
                f"admission conflict for {clean_sr} with no visible stored row"
            )
        same = (
            str(stored.get("request_id") or "") == req
            and int(stored.get("accepted") or 0) == (1 if accepted else 0)
            # F-04/M11 repair: a different source_class under the same sr id
            # is a DIFFERENT claim and must be classified honestly as
            # REJECTED_DIFFERENT, never laundered into ACCEPTED_IDENTICAL.
            # (obligation_set_version rides along as correlation metadata; it
            # is not part of admission identity.)
            and str(stored.get("source_class") or "") == str(source_class or "")
        )
        return "ACCEPTED_IDENTICAL" if same else "REJECTED_DIFFERENT"
    finally:
        conn.close()


def get_admission(semantic_result_id: str) -> dict[str, Any] | None:
    clean = str(semantic_result_id or "").strip()
    if not clean:
        return None
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM semantic_admissions WHERE sr_id = ?", (clean,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def admission_exists(semantic_result_id: str) -> bool:
    """Referential-closure check consumed by A7 before binding a citation.

    A-3: only an ACCEPTED admission is a referent — a rejected duplicate
    candidate's sr id is not admissible truth."""
    clean = str(semantic_result_id or "").strip()
    if not clean:
        return False
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT 1 FROM semantic_admissions WHERE sr_id = ? AND accepted = 1 LIMIT 1",
            (clean,),
        ).fetchone()
        return row is not None
    finally:
        conn.close()
