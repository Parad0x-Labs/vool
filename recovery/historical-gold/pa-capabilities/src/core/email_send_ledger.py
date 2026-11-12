"""The durable send-intent ledger for native email (B3, PA lane; revised 2026-09-09).

IDENTITY LAW (revised): a content hash alone is NOT action identity. A retry of
one approved action and an intentional second send of the same message are
different things and are treated differently:

* ``action_key`` — what the operator authorized: the operator approval id when
  the send executes from the approval lane, a caller-supplied idempotency key
  for programmatic sends, or ``""`` for un-keyed library use.
* ``send_key`` = sha256(action_key | account | sorted(recipients) | subject |
  body) — the row identity. Retries of the SAME action hit the same row.
* ``content_key`` = sha256(account | recipients | subject | body) — used only
  for the cross-action UNKNOWN gate.

Rules:
* Same send_key, row ``sent`` (within window) → ``duplicate_suppressed``: the
  re-approved or re-called SAME action does not re-send (crash between wire and
  outcome-commit, double-approve, turn-level retry).
* Same send_key, row ``unknown``/``attempting`` (within window) →
  ``send_unknown``: the outcome of THIS action is unobservable; a blind retry
  could double-send.
* DIFFERENT action_key, same content → allowed: a fresh authorization is a new
  action, even for an identical message — EXCEPT while any unresolved
  ``unknown`` row exists for the same content (within window): then refused
  with ``send_unknown`` until the user resolves what actually happened
  (``resolve_unknown``). A soft note (``recent_identical_sends``) is attached
  when the same content was sent recently under other actions — information,
  not a gate.

RESERVATION LAW (revised): no external execution without a durable intent row.
``begin_attempt`` FAILS CLOSED: if the ledger store is unavailable the send is
refused with the typed ``ledger_unavailable`` reason — it does not fail open.
The one honest exception runs the other way: if the reservation exists but the
OUTCOME recording fails after a successful wire exchange, the send stands (the
effect happened; hiding it would lie) and carries
``ledger_warning: outcome_recording_failed``; the row stays ``attempting``, so
a same-action retry is conservatively refused as unknown until resolved or the
window passes.

This ledger never promises exactly-once SMTP delivery — SMTP has no such
guarantee. It promises the weaker, honest thing: the runtime will not
blind-retry content whose outcome it could not observe, and will not execute a
send it could not durably reserve.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

#: A send recorded longer ago than this no longer suppresses a new attempt.
DEFAULT_DEDUP_WINDOW_SECONDS = 900

STATUS_ATTEMPTING = "attempting"
STATUS_SENT = "sent"
STATUS_FAILED = "failed"
STATUS_UNKNOWN = "unknown"

REASON_DUPLICATE = "duplicate_suppressed"
REASON_UNKNOWN = "send_unknown"
REASON_LEDGER_UNAVAILABLE = "ledger_unavailable"

#: Outcomes that block a new attempt of the SAME action inside the window.
_SAME_ACTION_BLOCKING = {STATUS_SENT, STATUS_UNKNOWN, STATUS_ATTEMPTING}


def _hash(parts: dict[str, Any]) -> str:
    canonical = json.dumps(parts, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def content_key_for(*, account: str, recipients: list[str], subject: str, body: str) -> str:
    """The pure-content key (no action): the cross-action UNKNOWN gate's lookup."""
    return _hash({
        "account": str(account or "default"),
        "recipients": sorted(str(r) for r in recipients or []),
        "subject": str(subject or ""),
        "body": str(body or ""),
    })


def send_key_for(
    *, account: str, recipients: list[str], subject: str, body: str, action_key: str = "",
) -> str:
    """The ACTION-bound row identity. Recipients are sorted so ordering cannot
    mint a second key for the same message."""
    return _hash({
        "action_key": str(action_key or ""),
        "account": str(account or "default"),
        "recipients": sorted(str(r) for r in recipients or []),
        "subject": str(subject or ""),
        "body": str(body or ""),
    })


def _row_public(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    try:
        details = json.loads(str(row.get("last_details_json") or "{}"))
    except Exception:
        details = {}
    return {
        "send_key": str(row.get("send_key") or ""),
        "content_key": str(row.get("content_key") or ""),
        "action_key": str(row.get("action_key") or ""),
        "status": str(row.get("status") or ""),
        "attempts": int(row.get("attempts") or 0),
        "updated_at": str(row.get("updated_at") or ""),
        "details": details if isinstance(details, dict) else {},
    }


def _within_window(updated_at: str, now: datetime, window_seconds: int) -> bool:
    try:
        then = datetime.fromisoformat(str(updated_at))
    except (TypeError, ValueError):
        return False
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return (now - then) < timedelta(seconds=max(0, int(window_seconds)))


def lookup(
    send_key: str,
    *,
    get_connection_fn: Callable[[], Any] | None = None,
) -> dict[str, Any] | None:
    if get_connection_fn is None:
        from storage.db import get_connection as get_connection_fn  # type: ignore[no-redef]
    try:
        conn = get_connection_fn()
        try:
            row = conn.execute(
                "SELECT * FROM email_send_ledger WHERE send_key = ?", (send_key,)
            ).fetchone()
            return _row_public(dict(row) if row else None)
        finally:
            conn.close()
    except Exception:
        return None  # a read is advisory; the authoritative gate is begin_attempt


def begin_attempt(
    *,
    send_key: str,
    account: str,
    recipients: list[str],
    subject: str,
    action_key: str = "",
    content_key: str = "",
    dedup_window_seconds: int = DEFAULT_DEDUP_WINDOW_SECONDS,
    now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    get_connection_fn: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Open (or re-open) the durable reservation for this ACTION's content, or
    refuse with a typed reason. FAILS CLOSED: if the ledger store cannot be
    read or written, returns ``{"blocked": True, "reason": "ledger_unavailable"}``
    — the send must not proceed without its durable reservation.

    Returns ``{"blocked": False, "row": <row|None>, "recent_identical_sends": int}``
    when a new SMTP attempt may proceed."""
    if get_connection_fn is None:
        from storage.db import get_connection as get_connection_fn  # type: ignore[no-redef]
    now = now_fn()
    now_iso = now.isoformat()
    action_key = str(action_key or "")
    content_key = str(content_key or "")
    # No content_key: the caller declined the cross-action gate (degenerate
    # library use); same-action rules still apply. The send path always passes it.
    conn = None
    try:
        conn = get_connection_fn()
        row = conn.execute(
            "SELECT * FROM email_send_ledger WHERE send_key = ?", (send_key,)
        ).fetchone()
        row_dict = dict(row) if row is not None else None
        if row_dict is not None:
            status = str(row_dict.get("status") or "")
            in_window = _within_window(str(row_dict.get("updated_at") or ""), now, dedup_window_seconds)
            if status in _SAME_ACTION_BLOCKING and in_window:
                return {
                    "blocked": True,
                    "reason": REASON_DUPLICATE if status == STATUS_SENT else REASON_UNKNOWN,
                    "row": _row_public(row_dict),
                }
        # Cross-action gate: an unresolved unknown for the SAME CONTENT (any
        # action) blocks every new action until the user resolves it.
        if content_key:
            others = conn.execute(
                "SELECT * FROM email_send_ledger WHERE content_key = ? AND status = ?",
                (content_key, STATUS_UNKNOWN),
            ).fetchall()
            for other in others:
                other = dict(other)
                if _within_window(str(other.get("updated_at") or ""), now, dedup_window_seconds):
                    return {
                        "blocked": True, "reason": REASON_UNKNOWN,
                        "row": _row_public(other),
                        "content_gate": True,
                    }
        # Information, not a gate: how many OTHER actions sent this content recently.
        recent_identical = 0
        if content_key:
            recent_identical = sum(
                1 for other in conn.execute(
                    "SELECT * FROM email_send_ledger WHERE content_key = ? AND status = ?",
                    (content_key, STATUS_SENT),
                ).fetchall()
                if _within_window(str(dict(other).get("updated_at") or ""), now, dedup_window_seconds)
            )
        if row_dict is None:
            conn.execute(
                """
                INSERT INTO email_send_ledger (
                    send_key, content_key, action_key, account, recipients_json,
                    subject, status, attempts, last_details_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, '{}', ?, ?)
                """,
                (send_key, content_key, action_key, str(account or "default"),
                 json.dumps(sorted(str(r) for r in recipients or [])),
                 str(subject or ""), STATUS_ATTEMPTING, now_iso, now_iso),
            )
        else:
            conn.execute(
                """
                UPDATE email_send_ledger
                SET status = ?, action_key = ?, attempts = attempts + 1, updated_at = ?
                WHERE send_key = ?
                """,
                (STATUS_ATTEMPTING, action_key, now_iso, send_key),
            )
        conn.commit()
    except Exception:
        # FAIL CLOSED: no durable reservation, no external execution. Never
        # raise into the send path; the typed reason carries the refusal.
        return {"blocked": True, "reason": REASON_LEDGER_UNAVAILABLE, "row": None}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    return {"blocked": False, "reason": "", "row": None, "recent_identical_sends": recent_identical}


def record_outcome(
    send_key: str,
    *,
    status: str,
    details: dict[str, Any] | None = None,
    now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    get_connection_fn: Callable[[], Any] | None = None,
) -> bool:
    """Resolve the open attempt. Returns True when the outcome was durably
    recorded. ``False`` means the row keeps its prior status: an ``attempting``
    row stays attempting (a later same-action retry reads it as unknown — the
    conservative direction), a failure here never invents an outcome."""
    if status not in {STATUS_SENT, STATUS_FAILED, STATUS_UNKNOWN}:
        raise ValueError(f"unsupported send-ledger status: {status!r}")
    if get_connection_fn is None:
        from storage.db import get_connection as get_connection_fn  # type: ignore[no-redef]
    now_iso = now_fn().isoformat()
    try:
        conn = get_connection_fn()
        try:
            conn.execute(
                """
                UPDATE email_send_ledger
                SET status = ?, last_details_json = ?, updated_at = ?
                WHERE send_key = ?
                """,
                (status, json.dumps(details or {}, sort_keys=True), now_iso, send_key),
            )
            conn.commit()
        finally:
            conn.close()
        return True
    except Exception:
        return False


def resolve_unknown(
    send_key: str,
    *,
    resolved_status: str,
    note: str = "",
    now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    get_connection_fn: Callable[[], Any] | None = None,
) -> bool:
    """The user-checked path out of an UNKNOWN row: they looked at the outbox
    and decided what actually happened. Only an ``unknown`` row may be resolved
    this way; returns True when the row was resolved."""
    if resolved_status not in {STATUS_SENT, STATUS_FAILED}:
        raise ValueError("an unknown outcome may only be resolved to 'sent' or 'failed'")
    if get_connection_fn is None:
        from storage.db import get_connection as get_connection_fn  # type: ignore[no-redef]
    now_iso = now_fn().isoformat()
    try:
        conn = get_connection_fn()
        try:
            cur = conn.execute(
                """
                UPDATE email_send_ledger
                SET status = ?, last_details_json = ?, updated_at = ?
                WHERE send_key = ? AND status = ?
                """,
                (
                    resolved_status,
                    json.dumps({"resolved_by": "user", "note": str(note or "")}, sort_keys=True),
                    now_iso,
                    send_key,
                    STATUS_UNKNOWN,
                ),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()
    except Exception:
        return False


__all__ = [
    "DEFAULT_DEDUP_WINDOW_SECONDS",
    "REASON_DUPLICATE",
    "REASON_LEDGER_UNAVAILABLE",
    "REASON_UNKNOWN",
    "STATUS_ATTEMPTING",
    "STATUS_FAILED",
    "STATUS_SENT",
    "STATUS_UNKNOWN",
    "begin_attempt",
    "content_key_for",
    "lookup",
    "record_outcome",
    "resolve_unknown",
    "send_key_for",
]
