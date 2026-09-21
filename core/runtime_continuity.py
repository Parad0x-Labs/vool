from __future__ import annotations

import contextlib
import contextvars
import hashlib
import json
import logging
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from core.runtime_execution_history import build_runtime_execution_history
from storage.db import DEFAULT_DB_PATH, get_connection

_log = logging.getLogger(__name__)

_DB_PATH_OVERRIDE: str | Path | None = None
_LOCK = threading.RLock()
_RESUMABLE_STATUSES = {"running", "interrupted", "pending_approval"}
# A7 W1 monotone terminal law: a checkpoint in one of these statuses may not
# silently re-enter execution or have its finalized answer replaced.
_CHECKPOINT_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


class CheckpointTransitionRefused(RuntimeError):
    """A monotone-terminal violation was refused at the durable choke point.

    Raised for: completed/cancelled checkpoints re-entering execution, non-
    retryable failed checkpoints resuming, and different-content rewrites of an
    already-finalized terminal answer. The durable row is left untouched.
    """

_MUTATING_TOOL_INTENTS = {
    "workspace.ensure_directory",
    "workspace.write_file",
    "workspace.replace_in_file",
    "workspace.apply_unified_diff",
    "workspace.rollback_last_change",
    "workspace.run_formatter",
    "sandbox.run_command",
    "hive.research_topic",
    "hive.create_topic",
    "hive.claim_task",
    "hive.post_progress",
    "hive.submit_result",
    "operator.cleanup_temp_files",
    "operator.move_path",
    "operator.schedule_calendar_event",
}


@dataclass
class _CheckpointRestorationScope:
    """One turn's durable checkpoint view and evidence-work ledger.

    A resume used to read the same checkpoint four times. Each read independently restored up to
    64 source-context items and 64 loop-state items, so a correctly bounded final collection still
    cost 512 JSON item decodes. The cache is deliberately context-local rather than process-global:
    concurrent turns never share mutable checkpoint truth, while every consumer inside one turn
    sees the same bounded representation.
    """

    cache: dict[str, dict[str, Any] | None] = field(default_factory=dict)
    evidence_work: int = 0
    durable_reads: int = 0

    @property
    def remaining_evidence_work(self) -> int:
        from core.agent_runtime.request_authority import TURN_EVIDENCE_ITEMS_MAX

        return max(0, TURN_EVIDENCE_ITEMS_MAX - self.evidence_work)


_CHECKPOINT_RESTORATION_SCOPE: contextvars.ContextVar[
    _CheckpointRestorationScope | None
] = contextvars.ContextVar("checkpoint_restoration_scope", default=None)


@contextlib.contextmanager
def runtime_checkpoint_restoration_scope():
    """Reuse one bounded checkpoint restoration view for one effective runtime turn."""

    existing = _CHECKPOINT_RESTORATION_SCOPE.get()
    if existing is not None:
        yield existing
        return
    scope = _CheckpointRestorationScope()
    token = _CHECKPOINT_RESTORATION_SCOPE.set(scope)
    try:
        yield scope
    finally:
        _CHECKPOINT_RESTORATION_SCOPE.reset(token)


def configure_runtime_continuity_db_path(db_path: str | Path | None) -> None:
    global _DB_PATH_OVERRIDE
    _DB_PATH_OVERRIDE = None if db_path is None else str(Path(db_path).expanduser().resolve())


# Every persisted store this module writes. A table left off this tuple is a store that
# survives the per-test reset for the whole pytest session, so one test's state answers for the
# next one -- which is order-dependence, and it is invisible until the order happens to change.
#
# `machine_read_memory` was exactly that. It was added after this list and never registered, so a
# disk read performed by any earlier test stayed readable through `_recall_machine_read`, whose
# cache-miss path reads this table. Measured: a test that ran "disk space on C:" for session "s1"
# made a LATER test's bare "ok what about D?" for the same session re-run machine.disk_usage
# instead of declining -- the precise thing
# tests/test_machine_followup_routing.py::test_bare_drive_letter_without_a_prior_read_is_not_hijacked
# exists to forbid. That test escaped only because it hand-picked an unused session id.
#
# Clearing the in-process cache is not enough on its own: `reset_machine_followup_state()` drops
# only the OrderedDict, and the recall path then re-seeds it from this table.
#
# tests/test_the_machine_read_lane_starts_each_test_clean.py holds this tuple to the module's
# write sites, so a new store cannot be added without being reset.
_RESET_TABLES = (
    "runtime_tool_receipts",
    "runtime_checkpoints",
    "runtime_session_events",
    "runtime_sessions",
    "hive_idempotency_keys",
    "message_queue",
    "machine_read_memory",
    "live_data_obligation_memory",
    "runtime_attempts",
    "runtime_attempt_subtasks",
    "runtime_attempt_approvals",
    "runtime_followup_resolutions",
    "runtime_unresolved_effects",
    "unresolved_effect_resolutions",
)


def reset_runtime_continuity_state() -> None:
    # In-process stores must go too, not just the tables. `_FINDINGS` (active_finding) and
    # `_CAPSULES` (audit_session) are module-level dicts that outlive a test, and only the three
    # test modules that created them cleared them by hand -- so any other test touching the audit
    # lane inherited a stale finding or capsule. That is the same defect class as the
    # `machine_read_memory` leak fixed in cf20052: a per-test reset that covers the database and
    # not the memory beside it. Imported here rather than at module scope to keep this module free
    # of agent_runtime imports.
    for module_name, clear_name in (
        ("core.agent_runtime.active_finding", "clear_active_findings"),
        ("core.agent_runtime.audit_session", "clear_audit_capsules"),
    ):
        try:
            module = __import__(module_name, fromlist=[clear_name])
            getattr(module, clear_name)()
        except Exception:
            continue
    with _LOCK:
        conn = _conn()
        try:
            for table in _RESET_TABLES:
                try:
                    conn.execute(f"DELETE FROM {table}")
                except Exception:
                    continue
            conn.commit()
        finally:
            conn.close()


def _gate_payload_tree(value: Any, depth: int = 0) -> Any:
    """A8 PASS003 (CE09) write-side gate for details-style trees: every
    embedded string whose governed payload is WITHHELD/ERASED is suppressed
    before persistence; a store outage fails closed (value suppressed)."""
    if depth > 8:
        return value
    from core.finalization import writer_may_persist_text

    def _gated(text: str) -> str:
        try:
            return text if writer_may_persist_text(text) else ""
        except Exception:
            return ""

    if isinstance(value, str):
        return _gated(value)
    if isinstance(value, dict):
        return {k: _gate_payload_tree(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [_gate_payload_tree(item, depth + 1) for item in value]
    return value


def _iter_tree_strings(value: Any, depth: int = 0):
    """Yield every plain string embedded in an arbitrary JSON-ish tree."""
    if depth > 8:
        return
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_tree_strings(item, depth + 1)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_tree_strings(item, depth + 1)


def _servable_details_value(value: Any, depth: int = 0) -> Any:
    """A8 PASS003 (CE09): serve-side availability gate for details_json trees.
    Every embedded string carrying governed WITHHELD/ERASED bytes is
    suppressed; a store outage fails closed by propagating."""
    if depth > 8:
        return value
    from core.finalization import writer_may_persist_text

    if isinstance(value, str):
        return value if writer_may_persist_text(value) else ""
    if isinstance(value, dict):
        return {k: _servable_details_value(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [_servable_details_value(item, depth + 1) for item in value]
    return value


def _scrub_persisted(value: Any) -> Any:
    """Recursively mask high-confidence secrets in anything about to be PERSISTED.

    A working redactor was already applied to runtime_checkpoints.request_text and the conversation
    log, but not here -- so a key pasted into a chat turn, or a .env body passed to
    workspace.write_file, landed verbatim in runtime_session_events / tool receipts AND was served
    back in cleartext over GET /api/runtime/events. Scrub at the write boundary so every persisted
    row is covered regardless of which caller produced it.
    """
    from core.secret_redaction import redact_secrets

    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, dict):
        return {key: _scrub_persisted(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub_persisted(item) for item in value]
    return value


def _next_session_seq(conn: Any, session_id: str, recorded_event_count: int) -> int:
    """Final Repair 2 (Mnemosyne final review, 2026-08-07): a session already damaged by the
    Repair-1-era event_count bug (event_count left permanently understating MAX(seq)) stayed
    permanently broken, because the next seq was derived from `runtime_sessions.event_count`
    alone -- every later call recomputed the SAME already-taken seq and hit the
    `(session_id, seq)` primary key. Self-heals by taking the greater of the recorded
    `event_count` and the actual `MAX(seq)` persisted in `runtime_session_events`: a session that
    was never desynced sees no change (the two already agree); a damaged one converges on its
    first write after this fix lands, without any separate repair pass or manual intervention."""
    max_persisted_row = conn.execute(
        "SELECT MAX(seq) AS max_seq FROM runtime_session_events WHERE session_id = ?", (session_id,),
    ).fetchone()
    max_persisted_seq = int(max_persisted_row["max_seq"] or 0) if max_persisted_row else 0
    return max(int(recorded_event_count or 0), max_persisted_seq) + 1


def append_runtime_event(
    *,
    session_id: str,
    event_type: str,
    message: str,
    details: dict[str, Any] | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    clean_session_id = str(session_id or "").strip()
    if not clean_session_id:
        raise ValueError("session_id is required")
    clean_event_type = str(event_type or "status").strip() or "status"
    clean_message = _scrub_persisted(str(message or "").strip())
    # A8 pass-002 writer gate: an event that carries governed bytes whose
    # payload transitioned WITHHELD/ERASED mid-flight is not durably appended
    # (ERASE dominates the append path).
    from core.finalization import writer_may_persist_text

    if not writer_may_persist_text(clean_message):
        clean_message = ""
    clean_details = _scrub_persisted(dict(details or {}))
    # A8 PASS003 (CE09): details_json is a payload-bearing surface too — the
    # same unavailable-verdict veto applies to every embedded string, not
    # just the message column.
    clean_details = _gate_payload_tree(clean_details)
    timestamp = str(created_at or _utcnow())
    with _LOCK:
        conn = _conn()
        try:
            existing = conn.execute(
                "SELECT event_count, started_at, request_preview, task_class, status, last_checkpoint_id FROM runtime_sessions WHERE session_id = ? LIMIT 1",
                (clean_session_id,),
            ).fetchone()
            seq = _next_session_seq(conn, clean_session_id, int(existing["event_count"] if existing else 0))
            request_preview = _clean_request_preview(clean_details.get("request_preview") or (existing["request_preview"] if existing else ""))
            task_class = str(clean_details.get("task_class") or (existing["task_class"] if existing else "")).strip()
            checkpoint_id = str(clean_details.get("checkpoint_id") or (existing["last_checkpoint_id"] if existing else "")).strip()
            # A8 PASS003 ERASE-dominance: durable-boundary re-check INSIDE the
            # runtime write transaction — a verdict that raced an in-flight
            # erase is re-derived here; sqlite serialization makes check+commit
            # atomic against the erase's own transaction.
            def _durable_veto(value: Any) -> bool:
                try:
                    return not writer_may_persist_text(str(value or ""))
                except Exception:
                    return True

            governed_strings: list[str] = [clean_message, *list(_iter_tree_strings(clean_details))]
            if any(
                text and _durable_veto(text) for text in governed_strings
            ):
                clean_message = ""
                clean_details = {}
                request_preview = _clean_request_preview("")
            conn.execute(
                """
                INSERT INTO runtime_session_events (
                    session_id, seq, event_type, message, details_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    clean_session_id,
                    seq,
                    clean_event_type,
                    clean_message,
                    _json_dumps(clean_details),
                    timestamp,
                ),
            )
            _upsert_runtime_session(
                conn,
                session_id=clean_session_id,
                request_preview=request_preview,
                task_class=task_class,
                status=_event_status(
                    clean_event_type,
                    previous_status=str(existing["status"] if existing else ""),
                ),
                last_event_type=clean_event_type,
                last_message=clean_message,
                event_count=seq,
                started_at=str(existing["started_at"] if existing else timestamp),
                updated_at=timestamp,
                checkpoint_id=checkpoint_id or None,
            )
            conn.commit()
        finally:
            conn.close()
    stored = {
        "session_id": clean_session_id,
        "seq": seq,
        "event_type": clean_event_type,
        "message": clean_message,
        "created_at": timestamp,
    }
    stored.update(clean_details)
    return stored


def list_runtime_sessions(*, limit: int = 20, include_execution_history: bool = True) -> list[dict[str, Any]]:
    from core.finalization import availability_read_scope

    with availability_read_scope():
        return _list_runtime_sessions(limit=limit, include_execution_history=include_execution_history)


def _list_runtime_sessions(*, limit: int, include_execution_history: bool = True) -> list[dict[str, Any]]:
    bounded_limit = max(1, min(int(limit), 100))
    with _LOCK:
        conn = _conn()
        try:
            rows = conn.execute(
                """
                SELECT session_id, started_at, updated_at, event_count, last_event_type,
                       last_message, request_preview, task_class, status, last_checkpoint_id
                FROM runtime_sessions
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (bounded_limit,),
            ).fetchall()
            session_rows = [dict(row) for row in rows]
            # Status polling does not resume a task. Read only fields consumed by
            # execution-history projection, never restore source_context/state.
            ids = [str(row["session_id"]) for row in session_rows]
            slots = ",".join("?" for _ in ids)
            checkpoints = {}
            events_by_session = {sid: [] for sid in ids}
            receipts_by_session = {sid: [] for sid in ids}
            if ids:
                for cp in conn.execute(
                    f"SELECT c.checkpoint_id, c.status, c.step_count, c.resume_count, "
                    f"c.last_tool_name, c.pending_intent_json FROM runtime_checkpoints c "
                    f"JOIN runtime_sessions s ON s.last_checkpoint_id = c.checkpoint_id "
                    f"WHERE s.session_id IN ({slots})", ids,
                ).fetchall():
                    item = dict(cp)
                    item["pending_intent"] = _json_loads(item.pop("pending_intent_json"), fallback={})
                    checkpoints[item["checkpoint_id"]] = item
                if include_execution_history:
                    for event in conn.execute(
                        f"SELECT * FROM (SELECT e.*, ROW_NUMBER() OVER "
                        f"(PARTITION BY session_id ORDER BY seq ASC) AS page_rank "
                        f"FROM runtime_session_events e WHERE session_id IN ({slots})) "
                        f"WHERE page_rank <= 200 ORDER BY session_id, seq", ids,
                    ).fetchall():
                        events_by_session[event["session_id"]].append(dict(event))
                    for receipt in conn.execute(
                        f"SELECT * FROM (SELECT r.*, ROW_NUMBER() OVER "
                        f"(PARTITION BY session_id ORDER BY updated_at DESC) AS page_rank "
                        f"FROM runtime_tool_receipts r WHERE session_id IN ({slots})) "
                        f"WHERE page_rank <= 64 ORDER BY session_id, updated_at DESC", ids,
                    ).fetchall():
                        item = dict(receipt)
                        item.pop("page_rank")
                        item["arguments"] = _json_loads(item.pop("arguments_json"), fallback={})
                        item["execution"] = _json_loads(item.pop("execution_json"), fallback={})
                        receipts_by_session[item["session_id"]].append(item)
        finally:
            conn.close()
    # A8 PASS003 (NCE-A): denormalized session summary columns are served
    # surfaces too — governed WITHHELD/ERASED bytes never disclose through
    # last_message/request_preview; a privacy-store outage fails closed.
    for session_row in session_rows:
        for _summary_col in ("last_message", "request_preview"):
            raw_preview = str(session_row.get(_summary_col) or "")
            if not raw_preview.strip():
                continue
            session_row[_summary_col] = _servable_event_message(raw_preview)
    live_checkpoints = _live_checkpoint_ids()
    for row in session_rows:
        session_id = str(row.get("session_id") or "")
        checkpoint_id = str(row.get("last_checkpoint_id") or "")
        checkpoint = checkpoints.get(checkpoint_id)
        # A turn whose worker is executing RIGHT NOW is not something to resume -- it is something
        # already happening. `_RESUMABLE_STATUSES` includes 'running' because a `running` row
        # normally means a process died holding it; while the worker is demonstrably alive that
        # reading is simply wrong, and acting on it would run the same work twice.
        row["worker_live"] = bool(checkpoint_id and checkpoint_id in live_checkpoints)
        if checkpoint:
            row["resume_available"] = bool(
                checkpoint.get("status") in _RESUMABLE_STATUSES and not row["worker_live"]
            )
            row["checkpoint_status"] = str(checkpoint.get("status") or "")
            row["checkpoint_step_count"] = int(checkpoint.get("step_count") or 0)
        else:
            row["resume_available"] = False
            row["checkpoint_status"] = ""
            row["checkpoint_step_count"] = 0
        if not include_execution_history:
            continue
        events = [_session_event_for_serve(event) for event in events_by_session[session_id]]
        receipts = receipts_by_session[session_id]
        row["execution_history"] = build_runtime_execution_history(
            session=row,
            checkpoint=checkpoint,
            events=events,
            receipts=receipts,
        )
    return session_rows


def list_runtime_session_events(
    session_id: str,
    *,
    after_seq: int = 0,
    limit: int = 100,
) -> list[dict[str, Any]]:
    clean_session_id = str(session_id or "").strip()
    if not clean_session_id:
        return []
    bounded_limit = max(1, min(int(limit), 200))
    clean_after = max(0, int(after_seq))
    with _LOCK:
        conn = _conn()
        try:
            rows = conn.execute(
                """
                SELECT session_id, seq, event_type, message, details_json, created_at
                FROM runtime_session_events
                WHERE session_id = ? AND seq > ?
                ORDER BY seq ASC
                LIMIT ?
                """,
                (clean_session_id, clean_after, bounded_limit),
            ).fetchall()
        finally:
            conn.close()
    return _serve_event_rows(rows)


def _serve_event_rows(rows) -> list[dict[str, Any]]:
    """Map stored rows to servable events under ONE governance read scope.

    Every event runs the A8 serve gate on its message and on each string in its details, and
    each check opened its own SQLite connection: 60 events cost 181 connections (measured
    2026-09-06). The Proof Chip, execution-truth and grounding reads all list events this way,
    so the scope belongs here, not in any one caller. Verdicts stay per-check and uncached.
    """
    from core.finalization import availability_read_scope

    with availability_read_scope():
        return [_session_event_for_serve(row) for row in rows]


def _session_event_for_serve(row) -> dict[str, Any]:
    item = {
        "session_id": str(row["session_id"]),
        "seq": int(row["seq"]),
        "event_type": str(row["event_type"]),
        # A8 pass-002 serve gate (T13): a stored runtime event never
        # discloses WITHHELD/ERASED governed bytes; store failure fails
        # closed (message suppressed).
        "message": _servable_event_message(str(row["message"])),
        "created_at": str(row["created_at"]),
    }
    item.update(
        _servable_details_value(_json_loads(row["details_json"], fallback={}))
    )
    return item


def list_recent_runtime_session_events(
    session_id: str,
    *,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Return the newest bounded event window in chronological order."""
    clean_session_id = str(session_id or "").strip()
    if not clean_session_id:
        return []
    bounded_limit = max(1, min(int(limit), 200))
    with _LOCK:
        conn = _conn()
        try:
            rows = conn.execute(
                """
                SELECT session_id, seq, event_type, message, details_json, created_at
                FROM runtime_session_events
                WHERE session_id = ?
                ORDER BY seq DESC
                LIMIT ?
                """,
                (clean_session_id, bounded_limit),
            ).fetchall()
        finally:
            conn.close()
    return _serve_event_rows(list(reversed(rows)))


def list_runtime_tool_receipts(
    session_id: str,
    *,
    limit: int = 64,
) -> list[dict[str, Any]]:
    clean_session_id = str(session_id or "").strip()
    if not clean_session_id:
        return []
    bounded_limit = max(1, min(int(limit), 200))
    with _LOCK:
        conn = _conn()
        try:
            rows = conn.execute(
                """
                SELECT receipt_key, session_id, checkpoint_id, tool_name, idempotency_key,
                       arguments_json, execution_json, created_at, updated_at
                FROM runtime_tool_receipts
                WHERE session_id = ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (clean_session_id, bounded_limit),
            ).fetchall()
        finally:
            conn.close()
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["arguments"] = _json_loads(item.pop("arguments_json"), fallback={})
        item["execution"] = _json_loads(item.pop("execution_json"), fallback={})
        out.append(item)
    return out


def create_runtime_checkpoint(
    *,
    session_id: str,
    request_text: str,
    source_context: dict[str, Any] | None = None,
    task_id: str = "",
    task_class: str = "",
) -> dict[str, Any]:
    from core.secret_redaction import redact_secrets

    checkpoint_id = f"runtime-{uuid.uuid4().hex}"
    timestamp = _utcnow()
    clean_session_id = str(session_id or "").strip()
    # The stored request text is a plaintext parallel to the redacted conversation log; scrub
    # high-confidence secrets so a pasted key does not persist verbatim in the checkpoint row.
    request_text = redact_secrets(str(request_text or ""))
    state = _stable_checkpoint_state({
        "executed_steps": [],
        "seen_tool_payloads": [],
        "loop_source_context": _stable_source_context(source_context),
        "pending_tool_payload": None,
        "last_tool_payload": None,
        "last_tool_response": None,
    })
    from core.runtime_task_outcome import normalize_runtime_task_outcome

    retry_of = str((source_context or {}).get("runtime_retry_of") or "").strip()
    retry_origin = str(
        (source_context or {}).get("runtime_retry_origin_checkpoint_id") or retry_of
    ).strip()
    initial_outcome = normalize_runtime_task_outcome(
        {
            "fulfillment_status": "fulfilled",
            "origin_task_id": str(
                (source_context or {}).get("runtime_retry_origin_task_id") or task_id or ""
            ).strip(),
            "origin_checkpoint_id": retry_origin or checkpoint_id,
        },
        task_id=task_id,
        checkpoint_id=checkpoint_id,
        request_text=request_text,
    ).to_dict()
    with _LOCK:
        conn = _conn()
        try:
            conn.execute(
                """
                INSERT INTO runtime_checkpoints (
                    checkpoint_id, session_id, task_id, task_class, request_text, source_context_json,
                    status, step_count, last_tool_name, pending_intent_json, state_json, final_response,
                    failure_text, outcome_json, resume_count, created_at, updated_at, completed_at,
                    resumed_from_checkpoint_id
                ) VALUES (?, ?, ?, ?, ?, ?, 'running', 0, '', '{}', ?, '', '', ?, 0, ?, ?, NULL, ?)
                """,
                (
                    checkpoint_id,
                    clean_session_id,
                    str(task_id or "").strip(),
                    str(task_class or "").strip(),
                    str(request_text or ""),
                    _json_dumps(_stable_source_context(source_context)),
                    _json_dumps(state),
                    _json_dumps(initial_outcome),
                    timestamp,
                    timestamp,
                    retry_of or None,
                ),
            )
            _upsert_runtime_session(
                conn,
                session_id=clean_session_id,
                request_preview=_preview_text(request_text),
                task_class=str(task_class or "").strip(),
                status="running",
                last_event_type="task_received",
                last_message="Task checkpoint created.",
                event_count=_session_event_count(conn, clean_session_id),
                started_at=_session_started_at(conn, clean_session_id, timestamp),
                updated_at=timestamp,
                checkpoint_id=checkpoint_id,
            )
            conn.commit()
        finally:
            conn.close()
    return get_runtime_checkpoint(checkpoint_id) or {}


def get_runtime_checkpoint(checkpoint_id: str) -> dict[str, Any] | None:
    clean_checkpoint_id = str(checkpoint_id or "").strip()
    if not clean_checkpoint_id:
        return None
    restoration_scope = _CHECKPOINT_RESTORATION_SCOPE.get()
    if restoration_scope is not None and clean_checkpoint_id in restoration_scope.cache:
        cached = restoration_scope.cache[clean_checkpoint_id]
        return dict(cached) if cached is not None else None
    with _LOCK:
        conn = _conn()
        try:
            row = conn.execute(
                """
                SELECT checkpoint_id, session_id, task_id, task_class, request_text, source_context_json,
                       status, step_count, last_tool_name, pending_intent_json, state_json, final_response,
                       final_response_hash,
                       failure_text, outcome_json, resume_count, created_at, updated_at, completed_at,
                       resumed_from_checkpoint_id
                FROM runtime_checkpoints
                WHERE checkpoint_id = ?
                LIMIT 1
                """,
                (clean_checkpoint_id,),
            ).fetchone()
            if row:
                evidence_limit = (
                    restoration_scope.remaining_evidence_work
                    if restoration_scope is not None
                    else _checkpoint_evidence_limit()
                )
                source_context, state, evidence_work = _restore_runtime_checkpoint_json_view(
                    conn,
                    checkpoint_id=clean_checkpoint_id,
                    evidence_limit=evidence_limit,
                )
        finally:
            conn.close()
    if not row:
        if restoration_scope is not None:
            restoration_scope.cache[clean_checkpoint_id] = None
        return None
    data = dict(row)
    data.pop("source_context_json")
    data["source_context"] = source_context
    data["pending_intent"] = _json_loads(data.pop("pending_intent_json"), fallback={})
    data.pop("state_json")
    data["state"] = state
    from core.runtime_task_outcome import normalize_runtime_task_outcome

    data["outcome"] = normalize_runtime_task_outcome(
        _json_loads(data.pop("outcome_json"), fallback={}),
        transport_status=str(data.get("status") or ""),
        failure_text=str(data.get("failure_text") or ""),
        task_id=str(data.get("task_id") or ""),
        checkpoint_id=str(data.get("checkpoint_id") or ""),
        request_text=str(data.get("request_text") or ""),
    ).to_dict()
    if restoration_scope is not None:
        restoration_scope.evidence_work += evidence_work
        restoration_scope.durable_reads += 1
        restoration_scope.cache[clean_checkpoint_id] = data
    return dict(data)


def latest_resumable_checkpoint(session_id: str) -> dict[str, Any] | None:
    clean_session_id = str(session_id or "").strip()
    if not clean_session_id:
        return None
    with _LOCK:
        conn = _conn()
        try:
            row = conn.execute(
                """
                SELECT checkpoint_id
                FROM runtime_checkpoints
                WHERE session_id = ? AND status IN ('running', 'interrupted', 'pending_approval')
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (clean_session_id,),
            ).fetchone()
        finally:
            conn.close()
    if not row:
        return None
    return get_runtime_checkpoint(str(row["checkpoint_id"]))


def latest_unfulfilled_checkpoint(session_id: str) -> dict[str, Any] | None:
    """Return the immediately preceding terminal task only when it remains retryable/unfulfilled.

    Deliberately do not scan backwards for *any* historical failure: if a newer terminal task was
    fulfilled, retrying an older failure would resurrect an unrelated request. Legacy rows infer
    fulfillment from ``status``/``failure_text`` in ``get_runtime_checkpoint``.
    """
    clean_session_id = str(session_id or "").strip()
    if not clean_session_id:
        return None
    with _LOCK:
        conn = _conn()
        try:
            row = conn.execute(
                """
                SELECT checkpoint_id
                FROM runtime_checkpoints
                WHERE session_id = ?
                  AND status NOT IN ('running', 'interrupted', 'pending_approval')
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (clean_session_id,),
            ).fetchone()
        finally:
            conn.close()
    if not row:
        return None
    checkpoint = get_runtime_checkpoint(str(row["checkpoint_id"]))
    outcome = dict((checkpoint or {}).get("outcome") or {})
    if str(outcome.get("fulfillment_status") or "") not in {
        "partially_fulfilled",
        "blocked",
        "failed",
    }:
        return None
    if not bool(outcome.get("retryable")):
        return None
    return checkpoint


def latest_failed_checkpoint(session_id: str) -> dict[str, Any] | None:
    """Backward-compatible name for the task-aware unfulfilled checkpoint selector."""

    return latest_unfulfilled_checkpoint(session_id)


def resume_runtime_checkpoint(
    checkpoint_id: str,
    *,
    source_context: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    current = get_runtime_checkpoint(checkpoint_id)
    if not current:
        return None
    from core.agent_runtime.request_authority import compose_evidence_origins

    stored_source_context = _stable_source_context(current.get("source_context"))
    incoming_source_context = _stable_source_context(source_context)
    stored_evidence = stored_source_context.get("external_evidence")
    incoming_evidence = incoming_source_context.get("external_evidence")
    merged_source_context = dict(stored_source_context)
    merged_source_context.update(incoming_source_context)
    if "external_evidence" in stored_source_context or "external_evidence" in incoming_source_context:
        merged_source_context["external_evidence"] = compose_evidence_origins(
            stored_evidence,
            incoming_evidence,
        )
    state = dict(current.get("state") or {})
    state["loop_source_context"] = _merge_loop_source_context(
        state.get("loop_source_context"),
        incoming_source_context,
    )
    try:
        updated = update_runtime_checkpoint(
            checkpoint_id,
            status="running",
            source_context=merged_source_context,
            state=state,
            resume_count=int(current.get("resume_count") or 0) + 1,
        )
    except CheckpointTransitionRefused:
        # A7 W1: terminal checkpoints (completed/cancelled, non-retryable failed)
        # may not silently reopen. Honest not-resumable signal to the caller.
        _log.info("resume_runtime_checkpoint refused terminal checkpoint %s", checkpoint_id)
        return None
    return updated


def _iter_source_context_strings(value, depth: int = 0):
    """Yield every plain string embedded in a checkpoint source_context tree
    (attachment/source material can carry governed payload bytes)."""
    if depth > 6:
        return
    if isinstance(value, str):
        if value.strip():
            yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_source_context_strings(item, depth + 1)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_source_context_strings(item, depth + 1)


def _servable_event_message(message: str) -> str:
    """A8 pass-002 serve gate: stored event messages must not disclose
    WITHHELD/ERASED governed bytes. Fail-closed on store failure."""
    clean = str(message or "")
    if not clean.strip():
        return clean
    from core.finalization import writer_may_persist_text

    if writer_may_persist_text(clean):
        return clean
    return ""


def update_runtime_checkpoint(
    checkpoint_id: str,
    *,
    task_id: str | None = None,
    task_class: str | None = None,
    source_context: dict[str, Any] | None = None,
    status: str | None = None,
    step_count: int | None = None,
    last_tool_name: str | None = None,
    pending_intent: dict[str, Any] | None = None,
    state: dict[str, Any] | None = None,
    final_response: str | None = None,
    failure_text: str | None = None,
    outcome: dict[str, Any] | None = None,
    resume_count: int | None = None,
) -> dict[str, Any] | None:
    current = get_runtime_checkpoint(checkpoint_id)
    if not current:
        return None
    merged_state = _stable_checkpoint_state(current.get("state"))
    if state:
        merged_state.update(_stable_checkpoint_state(state))
    merged_state = _stable_checkpoint_state(merged_state)
    merged_source_context = dict(current.get("source_context") or {})
    if source_context is not None:
        merged_source_context = _stable_source_context(source_context)
    pending_value = current.get("pending_intent") or {}
    if pending_intent is not None:
        pending_value = dict(pending_intent)
    next_status = str(status or current.get("status") or "running").strip() or "running"
    next_task_id = str(task_id if task_id is not None else current.get("task_id") or "").strip()
    next_task_class = str(task_class if task_class is not None else current.get("task_class") or "").strip()
    next_step_count = int(step_count if step_count is not None else current.get("step_count") or len(list(merged_state.get("executed_steps") or [])))
    next_last_tool_name = str(last_tool_name if last_tool_name is not None else current.get("last_tool_name") or "").strip()
    next_resume_count = int(resume_count if resume_count is not None else current.get("resume_count") or 0)
    from core.runtime_task_outcome import normalize_runtime_task_outcome

    next_failure_text = str(
        failure_text if failure_text is not None else current.get("failure_text") or ""
    )
    outcome_seed = outcome if outcome is not None else current.get("outcome")
    if outcome is None and (
        next_status in {"failed", "cancelled"}
        or (failure_text is not None and bool(next_failure_text.strip()))
    ):
        current_outcome = dict(current.get("outcome") or {})
        outcome_seed = {
            "origin_task_id": current_outcome.get("origin_task_id"),
            "origin_checkpoint_id": current_outcome.get("origin_checkpoint_id"),
            "original_request_hash": current_outcome.get("original_request_hash"),
        }
    next_outcome = _scrub_persisted(
        normalize_runtime_task_outcome(
            outcome_seed,
            transport_status=next_status,
            failure_text=next_failure_text,
            task_id=next_task_id,
            checkpoint_id=str(current.get("checkpoint_id") or checkpoint_id),
            request_text=str(current.get("request_text") or ""),
        ).to_dict()
    )
    updated_at = _utcnow()
    completed_at = updated_at if next_status in {"completed", "failed", "cancelled"} else None
    # A7 W1 monotone terminal guard — decided BEFORE any write so a refused
    # transition leaves the durable row untouched.
    current_status = str(current.get("status") or "running")
    if current_status in _CHECKPOINT_TERMINAL_STATUSES and next_status != current_status:
        outcome_retryable = bool((current.get("outcome") or {}).get("retryable"))
        allowed = (
            (current_status == "failed" and next_status == "completed")
            or (current_status == "failed" and next_status == "running" and outcome_retryable)
        )
        if not allowed:
            raise CheckpointTransitionRefused(
                f"checkpoint {checkpoint_id} is terminal ({current_status}); "
                f"transition to {next_status!r} refused"
            )
    existing_final_response = str(current.get("final_response") or "")
    next_final_response = str(final_response if final_response is not None else existing_final_response)
    stored_final_hash = str(current.get("final_response_hash") or "")
    # A8 pass-002 ERASE-dominance (CE-B/T11): a stale writer that survived an
    # erase must never durably restore governed payload bytes. Availability
    # is consulted at THIS durable boundary — WITHHELD/ERASED verdicts veto
    # the write; a store failure fails closed (refusal, never disclosure).
    if next_final_response.strip():
        from core.finalization import writer_may_persist_text

        if not writer_may_persist_text(next_final_response):
            raise CheckpointTransitionRefused(
                f"checkpoint {checkpoint_id}: governed payload is WITHHELD/ERASED; "
                "stale-writer resurrection refused"
            )
        for evidence_value in _iter_source_context_strings(merged_source_context):
            if not writer_may_persist_text(evidence_value):
                raise CheckpointTransitionRefused(
                    f"checkpoint {checkpoint_id}: source_context carries "
                    "WITHHELD/ERASED payload; write refused"
                )
    if (
        current_status in _CHECKPOINT_TERMINAL_STATUSES
        and existing_final_response
        and next_final_response
        and next_final_response != existing_final_response
    ):
        raise CheckpointTransitionRefused(
            f"checkpoint {checkpoint_id} already carries finalized answer content; "
            "different-content rewrite refused"
        )
    if next_status in _CHECKPOINT_TERMINAL_STATUSES and next_final_response:
        final_hash = "sha256:" + hashlib.sha256(next_final_response.encode("utf-8")).hexdigest()
        if stored_final_hash and final_hash != stored_final_hash:
            raise CheckpointTransitionRefused(
                f"checkpoint {checkpoint_id} finalized-answer identity mismatch refused"
            )
    else:
        # A8 PASS003 (CE11): stamp the payload identity whenever governed
        # text is durably retained, not only on terminal transitions — a
        # recoverable (interrupted/running/pending_approval) checkpoint must
        # carry its final_response_hash so the erasure sweep's equality leg
        # can find it after any status.
        final_hash = (
            "sha256:" + hashlib.sha256(next_final_response.encode("utf-8")).hexdigest()
            if next_final_response
            else stored_final_hash
        )
    with _LOCK:
        conn = _conn()
        try:
            # A8 PASS003 ERASE-dominance (SHADOW-C): the pass-002 gate above is
            # necessary but not sufficient — an erase can win between that
            # early check and this durable UPDATE. Re-derive the verdict here,
            # inside the runtime write transaction; sqlite serialization makes
            # check+commit atomic against the erase transaction. Fail closed:
            # an outage refuses the write rather than risking resurrection.
            from core.finalization import writer_may_persist_text as _wpt

            def _durable_veto(text: str) -> bool:
                try:
                    return not _wpt(str(text or ""))
                except Exception:
                    return True

            if next_final_response.strip() and _durable_veto(next_final_response):
                raise CheckpointTransitionRefused(
                    f"checkpoint {checkpoint_id}: governed payload is WITHHELD/"
                    "ERASED at the durable boundary; stale-writer resurrection "
                    "refused"
                )
            if any(
                str(value or "").strip() and _durable_veto(str(value))
                for value in _iter_source_context_strings(merged_source_context)
            ):
                raise CheckpointTransitionRefused(
                    f"checkpoint {checkpoint_id}: source_context carries "
                    "WITHHELD/ERASED payload at the durable boundary; write refused"
                )
            conn.execute(
                """
                UPDATE runtime_checkpoints
                SET task_id = ?,
                    task_class = ?,
                    source_context_json = ?,
                    status = ?,
                    step_count = ?,
                    last_tool_name = ?,
                    pending_intent_json = ?,
                    state_json = ?,
                    final_response = ?,
                    final_response_hash = ?,
                    failure_text = ?,
                    outcome_json = ?,
                    resume_count = ?,
                    updated_at = ?,
                    completed_at = ?
                WHERE checkpoint_id = ?
                """,
                (
                    next_task_id,
                    next_task_class,
                    _json_dumps(merged_source_context),
                    next_status,
                    next_step_count,
                    next_last_tool_name,
                    _json_dumps(pending_value),
                    _json_dumps(merged_state),
                    next_final_response,
                    final_hash,
                    next_failure_text,
                    _json_dumps(next_outcome),
                    next_resume_count,
                    updated_at,
                    completed_at,
                    checkpoint_id,
                ),
            )
            _upsert_runtime_session(
                conn,
                session_id=str(current.get("session_id") or ""),
                request_preview=_preview_text(str(current.get("request_text") or "")),
                task_class=next_task_class,
                status=next_status,
                last_event_type=None,
                last_message=None,
                event_count=_session_event_count(conn, str(current.get("session_id") or "")),
                started_at=_session_started_at(conn, str(current.get("session_id") or ""), str(current.get("created_at") or updated_at)),
                updated_at=updated_at,
                checkpoint_id=checkpoint_id,
            )
            conn.commit()
        finally:
            conn.close()
    updated = dict(current)
    updated.update(
        {
            "task_id": next_task_id,
            "task_class": next_task_class,
            "source_context": merged_source_context,
            "status": next_status,
            "step_count": next_step_count,
            "last_tool_name": next_last_tool_name,
            "pending_intent": pending_value,
            "state": merged_state,
            "final_response": next_final_response,
            "final_response_hash": final_hash,
            "failure_text": str(
                failure_text
                if failure_text is not None
                else current.get("failure_text") or ""
            ),
            "outcome": next_outcome,
            "resume_count": next_resume_count,
            "updated_at": updated_at,
            "completed_at": completed_at,
        }
    )
    restoration_scope = _CHECKPOINT_RESTORATION_SCOPE.get()
    if restoration_scope is not None:
        restoration_scope.cache[str(checkpoint_id)] = updated
    return dict(updated)


def record_runtime_tool_progress(
    checkpoint_id: str,
    *,
    executed_steps: list[dict[str, Any]],
    loop_source_context: dict[str, Any] | None,
    seen_tool_payloads: set[str] | list[str],
    pending_tool_payload: dict[str, Any] | None,
    last_tool_payload: dict[str, Any] | None,
    last_tool_response: dict[str, Any] | None,
    last_tool_name: str | None = None,
    task_class: str | None = None,
    status: str | None = None,
    pending_batch_calls: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    # A7 monotone terminals keep their final truth: tool progress arriving after the
    # checkpoint reached a terminal status carries no state meaning (the turn is over), so
    # it is discarded rather than refused -- a late recorder must not crash the caller's
    # flow, and the durable row must not move. Downgrade and resume attempts elsewhere
    # still refuse; this is the no-op reading of the same monotone law.
    current = get_runtime_checkpoint(checkpoint_id)
    if current is not None and str(current.get("status") or "running") in _CHECKPOINT_TERMINAL_STATUSES:
        return current
    state = {
        "executed_steps": [dict(step) for step in list(executed_steps or [])],
        "seen_tool_payloads": sorted({str(item) for item in list(seen_tool_payloads or []) if str(item)}),
        "loop_source_context": _stable_source_context(loop_source_context),
        "pending_tool_payload": dict(pending_tool_payload) if isinstance(pending_tool_payload, dict) else None,
        # The rest of the paused reply's batch, stored beside the pending head so a turn resumed
        # after an approval knows the whole plan the operator was shown -- not just its first call.
        "pending_batch_calls": [
            dict(item) for item in list(pending_batch_calls or []) if isinstance(item, dict)
        ],
        "last_tool_payload": dict(last_tool_payload) if isinstance(last_tool_payload, dict) else None,
        "last_tool_response": dict(last_tool_response) if isinstance(last_tool_response, dict) else None,
    }
    return update_runtime_checkpoint(
        checkpoint_id,
        state=state,
        pending_intent=state["pending_tool_payload"] or {},
        step_count=len(state["executed_steps"]),
        last_tool_name=str(last_tool_name or ""),
        task_class=task_class,
        status=status or "running",
    )


def finalize_runtime_checkpoint(
    checkpoint_id: str,
    *,
    status: str,
    final_response: str = "",
    failure_text: str = "",
    outcome: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    from core.secret_redaction import redact_secrets

    clean_status = str(status or "completed").strip() or "completed"
    # Terminal turns wipe their transient tool state: update_runtime_checkpoint MERGES
    # state, so without this a completed row keeps pending_tool_payload /
    # last_tool_response forever -- stale data that context loaders (which do not all
    # filter by status) could resurface into a later turn's prompt. Interrupted and
    # pending_approval checkpoints keep state -- resuming them is the feature.
    cleared_state: dict[str, Any] | None = None
    if clean_status in {"completed", "failed", "cancelled"}:
        cleared_state = {
            "executed_steps": [],
            "seen_tool_payloads": [],
            "pending_tool_payload": None,
            "pending_batch_calls": [],
            "last_tool_payload": None,
            "last_tool_response": None,
        }
    # Scrub secrets an assistant turn may have echoed back before persisting the final text.
    from core.runtime_task_outcome import normalize_runtime_task_outcome

    current = get_runtime_checkpoint(checkpoint_id) or {}
    normalized_outcome = normalize_runtime_task_outcome(
        outcome,
        transport_status=clean_status,
        failure_text=failure_text,
        task_id=str(current.get("task_id") or ""),
        checkpoint_id=str(current.get("checkpoint_id") or checkpoint_id),
        request_text=str(current.get("request_text") or ""),
    )
    normalized_failure_text = str(failure_text or "")
    if normalized_outcome.is_unfulfilled and not normalized_failure_text:
        codes = ", ".join(normalized_outcome.failure_codes) or "task_unfulfilled"
        normalized_failure_text = f"{normalized_outcome.failure_stage or 'fulfillment'}: {codes}"
    return update_runtime_checkpoint(
        checkpoint_id,
        status=clean_status,
        pending_intent={},
        state=cleared_state,
        final_response=redact_secrets(str(final_response or "")),
        failure_text=redact_secrets(normalized_failure_text),
        outcome=normalized_outcome.to_dict(),
    )


# ============================================================================
# Runtime attempts: generic execution-attempt persistence (Step 7).
# ============================================================================
# A "runtime attempt" is one execution of a typed, multi-subtask plan against a request --
# LIVE_DATA today, any future typed-plan answer_mode later (workspace tool tasks, provider/tool-
# call tasks, multipart agent tasks, grounded research). Distinct from a `runtime_checkpoint` (one
# per TURN, opaque to a plan's internal structure) and from the visible conversation log (one per
# turn's user/assistant text): an attempt is the structured record a "why did that fail?" or
# "retry" follow-up needs to bind to, which neither of the other two stores can serve on their own.
#
# Concurrency: each subtask is its OWN row (`runtime_attempt_subtasks`, primary key includes
# subtask_id), so concurrent workers completing different subtasks in the same attempt write to
# different rows and never race on a shared read-modify-write. The parent attempt's lifecycle is
# reconciled from the full set of subtask rows by a pure function (`reconcile_attempt_lifecycle`),
# called once after every worker has returned -- never computed by patching a shared blob mid-flight.
#
# A retry creates a NEW attempt row linked via parent_attempt_id/root_attempt_id rather than
# mutating the prior attempt in place, so the full retry chain for one original request stays
# inspectable and no attempt row is ever overwritten out from under a caller still reading it.


class AttemptLifecycle(str, Enum):
    RECEIVED = "RECEIVED"
    PLANNED = "PLANNED"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    RUNNING = "RUNNING"
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
    SUCCEEDED = "SUCCEEDED"
    FAILED_TOOL = "FAILED_TOOL"
    FAILED_PROVIDER = "FAILED_PROVIDER"
    FAILED_SYNTHESIS = "FAILED_SYNTHESIS"
    FAILED_VALIDATION = "FAILED_VALIDATION"
    PENDING_RECONCILIATION = "PENDING_RECONCILIATION"
    CANCELLED = "CANCELLED"
    ABANDONED = "ABANDONED"
    # H-1/INV-3: supersession is a durable row transition written ONLY by the
    # successor's mint unit — never by sweeps, never by readers.
    SUPERSEDED = "SUPERSEDED"


class AttemptRole(str, Enum):
    """ARCH-TRUTH-R1c: what one attempt row IS inside its turn's chain.

    One external turn is one chain: the turn door mints the ROOT, the lane that answers
    and any planned sub-request hang off it, and a retry is a new generation of the same
    chain. The role is what lets a reader ask "which attempt of this turn actually
    answered" STRUCTURALLY, instead of inferring it from whichever row happened to be
    written to last -- the inference that made a referential follow-up bind to the turn
    door's bookkeeping row and report that nothing was recorded.
    """

    TURN_ROOT = "turn_root"
    ANSWER = "answer"
    PLANNER_TASK = "planner_task"
    RETRY = "retry"


#: Roles whose row carries what the turn actually did -- the antecedent a referential
#: follow-up ("why did that fail?", "which cities did I ask for?") must bind to. The turn
#: root is deliberately absent: it is the chain's identity and the turn's outcome, not its
#: work. Legacy rows (role '') are ranked WITH these, so a database written before the
#: role column resolves exactly as it did before.
_ANSWER_BEARING_ROLES = (
    AttemptRole.ANSWER.value,
    AttemptRole.PLANNER_TASK.value,
    AttemptRole.RETRY.value,
)

#: SQL rank: answer-bearing and untyped-legacy rows first, the turn root last. This is the
#: structural half of follow-up resolution -- recency only orders rows of equal rank, so
#: reordering `updated_at` cannot move a bookkeeping row ahead of the row that answered.
_ROLE_RANK_SQL = (
    f"(CASE WHEN attempt_role = '{AttemptRole.TURN_ROOT.value}' THEN 1 ELSE 0 END) ASC"
)


_TERMINAL_ATTEMPT_STATES = {
    AttemptLifecycle.PARTIAL_SUCCESS.value,
    AttemptLifecycle.SUCCEEDED.value,
    AttemptLifecycle.FAILED_TOOL.value,
    AttemptLifecycle.FAILED_PROVIDER.value,
    AttemptLifecycle.FAILED_SYNTHESIS.value,
    AttemptLifecycle.FAILED_VALIDATION.value,
    AttemptLifecycle.PENDING_RECONCILIATION.value,
    AttemptLifecycle.CANCELLED.value,
    AttemptLifecycle.ABANDONED.value,
    AttemptLifecycle.SUPERSEDED.value,
}

# Generous but bounded: large enough to hold any real pasted user request without truncation in
# the overwhelming common case, small enough to bound worst-case row size. When exceeded, the
# canonical source of truth for an "exact retry" is the linked dialogue-turn record
# (origin_conversation_event_id / origin_user_turn_id), not this snapshot -- see
# original_request_truncated below.
ORIGINAL_REQUEST_SNAPSHOT_LIMIT_BYTES = 32_768

_process_instance_id_cache: str = ""


def _process_instance_id() -> str:
    """One stable id per process lifetime -- distinct from the git commit SHA (which identifies
    the CODE) and from checkpoint_id/attempt_id (which identify a REQUEST). Lets a restart-recovery
    sweep record which process actually performed the abandonment transition."""
    global _process_instance_id_cache
    if not _process_instance_id_cache:
        _process_instance_id_cache = f"proc-{uuid.uuid4().hex[:16]}"
    return _process_instance_id_cache


_daemon_sha_cache: str | None = None


def _daemon_commit_sha() -> str:
    """The running process's own git commit, cached once per process -- resolving it is a
    subprocess call (`git rev-parse HEAD`), too expensive to repeat on every attempt write."""
    global _daemon_sha_cache
    if _daemon_sha_cache is None:
        try:
            from core.runtime_paths import PROJECT_ROOT
            from core.web.api.runtime import git_checkout_state

            state = git_checkout_state(PROJECT_ROOT)
            _daemon_sha_cache = str(state.get("commit") or "") if state.get("valid") else ""
        except Exception:
            _daemon_sha_cache = ""
    return _daemon_sha_cache


def _request_hash(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8", errors="replace")).hexdigest()


def reconcile_attempt_lifecycle(
    subtask_states: list[str],
    *,
    plan_valid: bool = True,
    provider_available: bool = True,
    synthesis_ok: bool = True,
) -> tuple[str, str]:
    """The one runtime-owned function that derives a parent attempt's lifecycle from its
    subtasks' persisted states -- (lifecycle_state, terminal_reason). Pure: no I/O, so every
    combination the reconciliation rules require is directly unit-testable. The visible renderer
    never sets lifecycle status from prose; this is the only place that decision is made.

    `provider_available`/`synthesis_ok` default True because a LIVE_DATA plan never calls a model
    provider or a synthesis pass -- they exist for future attempt types (grounded research,
    provider/tool-call tasks) that route through the same reconciliation function.
    """
    if not plan_valid:
        return AttemptLifecycle.FAILED_VALIDATION.value, "plan could not be constructed or validated"
    if not provider_available:
        return AttemptLifecycle.FAILED_PROVIDER.value, "provider unavailable before execution"
    if not subtask_states:
        return AttemptLifecycle.FAILED_VALIDATION.value, "no subtasks were planned"
    if any(state == "WAITING_APPROVAL" for state in subtask_states):
        return AttemptLifecycle.WAITING_APPROVAL.value, "one or more subtasks require approval"
    # A6 law: an effect whose physical outcome could not be proven is UNKNOWN, never
    # FAILED_TOOL -- the attempt must not advertise a retryable failure over an effect
    # that may have landed. PENDING_RECONCILIATION is non-retryable by policy below.
    if any(state == "PENDING_RECONCILIATION" for state in subtask_states):
        return (
            AttemptLifecycle.PENDING_RECONCILIATION.value,
            "one or more effects have unknown outcome; reconciliation required before retry",
        )
    succeeded = [state for state in subtask_states if state == "SUCCEEDED"]
    failed_or_unsupported = [state for state in subtask_states if state in ("FAILED", "UNSUPPORTED_ENTITY")]
    total = len(subtask_states)
    if len(succeeded) == total:
        if not synthesis_ok:
            return AttemptLifecycle.FAILED_SYNTHESIS.value, "tool results succeeded but synthesis failed"
        return AttemptLifecycle.SUCCEEDED.value, ""
    if succeeded and failed_or_unsupported:
        return AttemptLifecycle.PARTIAL_SUCCESS.value, f"{len(succeeded)}/{total} subtasks succeeded"
    if failed_or_unsupported and not succeeded:
        return AttemptLifecycle.FAILED_TOOL.value, "all executable subtasks failed"
    return (
        AttemptLifecycle.FAILED_VALIDATION.value,
        "subtask states did not resolve to a known terminal shape",
    )


# Default retry guidance keyed by TERMINAL attempt lifecycle -- Step 7 correction #6. Governs the
# ATTEMPT-level summary written by `finalize_runtime_attempt`. Per-subtask retryability (e.g. an
# UNSUPPORTED_ENTITY subtask staying non-retryable unless capabilities changed or the user
# corrected the request) is set independently on each `runtime_attempt_subtasks` row by the
# caller, since only the caller knows what changed between generations.
_ATTEMPT_RETRY_POLICY: dict[str, tuple[bool, str]] = {
    AttemptLifecycle.FAILED_TOOL.value: (True, "transient tool failure"),
    AttemptLifecycle.FAILED_PROVIDER.value: (True, "transient provider failure"),
    AttemptLifecycle.FAILED_SYNTHESIS.value: (True, "synthesis failed; tool results may be reusable"),
    AttemptLifecycle.FAILED_VALIDATION.value: (False, "invalid stage must be identified before retry"),
    AttemptLifecycle.PENDING_RECONCILIATION.value: (False, "unknown effect outcome; reconcile before any retry"),
    AttemptLifecycle.PARTIAL_SUCCESS.value: (True, "some subtasks still need to be retried"),
    AttemptLifecycle.WAITING_APPROVAL.value: (False, "resumable, not automatically executable"),
    AttemptLifecycle.CANCELLED.value: (False, "not automatically retryable unless explicitly requested"),
    AttemptLifecycle.SUCCEEDED.value: (False, "carry forward; refresh only under an explicit staleness policy"),
    AttemptLifecycle.ABANDONED.value: (True, "attempt was interrupted, not user-cancelled"),
    # H-1: the successor already exists — a superseded row is never retried.
    AttemptLifecycle.SUPERSEDED.value: (False, "superseded by a newer generation of this execution"),
}


class MintRefused(RuntimeError):
    """H-1/INV-3: a successor mint was refused (blocking predecessor state or
    unreconciled effects). `reason_code` names the blocking class."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


_MintRefused = MintRefused


class AttemptClaimRefused(RuntimeError):
    """H-1/INV-2: the claim CAS lost (rowcount 0 or uniqueness violation).
    The executor MUST abort; this refusal is never swallowed."""


_EXECUTING_ATTEMPT_STATES = ("PLANNED", "RUNNING")
_CLAIMABLE_ATTEMPT_STATES = ("RECEIVED", "PLANNED", "WAITING_APPROVAL")


def claim_runtime_attempt(attempt_id: str, *, to_state: str = "RUNNING") -> dict[str, Any]:
    """INV-2 EXECUTING EXCLUSIVITY claim, as a real CAS.

    RECEIVED→PLANNED/RUNNING and WAITING_APPROVAL→RUNNING resume are
    conditional UPDATEs on `(attempt_id, lifecycle_state IN claimable)`;
    rowcount 0 or an IntegrityError from ux_one_live_attempt raises
    :class:`AttemptClaimRefused` — the caller must abort the executor.
    """
    if to_state not in _EXECUTING_ATTEMPT_STATES:
        raise ValueError(f"claim target must be an executing state, got {to_state!r}")
    with _LOCK:
        conn = _conn()
        try:
            try:
                cursor = conn.execute(
                    """
                    UPDATE runtime_attempts SET lifecycle_state = ?, updated_at = ?
                    WHERE attempt_id = ? AND lifecycle_state IN (?, ?, ?)
                    """,
                    (
                        to_state,
                        _utcnow(),
                        str(attempt_id),
                        *_CLAIMABLE_ATTEMPT_STATES,
                    ),
                )
                conn.commit()
                won = int(cursor.rowcount or 0) == 1
            except sqlite3.IntegrityError as exc:
                conn.rollback()
                raise AttemptClaimRefused(
                    f"claim refused for {attempt_id}: another executor holds the"
                    " PLANNED/RUNNING slot for this execution"
                ) from exc
        finally:
            conn.close()
    if not won:
        raise AttemptClaimRefused(
            f"claim refused for {attempt_id}: row is not in a claimable state"
        )
    return {"attempt_id": str(attempt_id), "claimed_state": to_state}


def _default_retry_guidance(lifecycle_state: str) -> tuple[bool, str]:
    return _ATTEMPT_RETRY_POLICY.get(lifecycle_state, (False, ""))


def compute_retry_idempotency_key(parent_attempt_id: str, trigger_user_turn_id: str, resolution_intent: str) -> str:
    """The durable identity a retry-created attempt is deduplicated on (Repair 2/3, Mnemosyne
    review): (parent_attempt_id, trigger_user_turn_id, resolution_intent). Two retry requests that
    agree on all three ARE the same logical retry, whether they land microseconds apart on two
    threads, land on two separate daemon processes sharing this database, or are the exact same
    HTTP request replayed minutes later after the first response was lost -- deliberately NOT
    scoped by lifecycle state, so it keeps matching after the child reaches a terminal state
    (Repair 3: sequential replay must dedupe past completion, not just while in flight). A
    genuinely NEW user retry turn has a different trigger_user_turn_id and is free to create a new
    generation."""
    payload = "|".join((
        str(parent_attempt_id or "").strip(),
        str(trigger_user_turn_id or "").strip(),
        str(resolution_intent or "").strip(),
    ))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def create_runtime_attempt(
    *,
    session_id: str,
    original_request: str,
    checkpoint_id: str = "",
    origin_user_turn_id: str = "",
    trigger_user_turn_id: str = "",
    origin_conversation_event_id: str = "",
    answer_mode: str = "",
    plan_id: str = "",
    root_attempt_id: str = "",
    parent_attempt_id: str = "",
    execution_generation: int = 1,
    resolution_intent: str = "",
    client_turn_id: str = "",
    role: str = "",
    execution_slot: str = "",
) -> dict[str, Any]:
    """A new attempt row, state RECEIVED. A first execution leaves root_attempt_id/parent_attempt_id
    to default (root_attempt_id becomes this attempt's own id); a retry passes the ORIGINAL
    attempt's root_attempt_id and the immediately-prior attempt's id as parent_attempt_id, so the
    chain stays linked (Step 7 correction #3) -- this function never mutates a prior attempt row.

    Repair 2/3 (Mnemosyne review): a retry (parent_attempt_id set) REQUIRES a non-empty
    trigger_user_turn_id -- an empty trigger turn cannot serve as a durable idempotency identity,
    so this fails loudly (ValueError) rather than silently creating an ambiguous generation. The
    retry is then inserted via `INSERT ... ON CONFLICT(retry_idempotency_key) DO NOTHING`, a real
    database uniqueness constraint enforced by SQLite across EVERY connection touching this
    database file -- in-process, cross-thread, AND cross-process, unlike a process-local
    `threading.Lock` (see `core.agent_runtime.attempt_retry`), which only ever protected one
    process and was proven insufficient by a live two-process reproduction. The returned dict's
    `idempotent_replay` key tells the caller whether IT won the insert (False -- proceed to
    execute) or a concurrent/prior caller already owns this exact retry (True -- the caller must
    render from the EXISTING row's persisted state, never re-execute).
    """
    from core.secret_redaction import redact_secrets

    attempt_id = f"attempt-{uuid.uuid4().hex}"
    timestamp = _utcnow()
    clean_session_id = str(session_id or "").strip()
    redacted = redact_secrets(str(original_request or ""))
    raw_bytes = len(redacted.encode("utf-8", errors="replace"))
    truncated = raw_bytes > ORIGINAL_REQUEST_SNAPSHOT_LIMIT_BYTES
    snapshot = redacted
    if truncated:
        # UTF-8-safe truncation boundary. The hash below is of the FULL redacted text, not the
        # truncated snapshot, so identity can still be verified against the canonical dialogue-
        # turn record even when the snapshot itself was cut -- never retry a truncated
        # approximation as if it were the exact request (Step 7 correction #2).
        snapshot = redacted.encode("utf-8", errors="replace")[:ORIGINAL_REQUEST_SNAPSHOT_LIMIT_BYTES].decode(
            "utf-8", errors="ignore"
        )
    request_hash = _request_hash(redacted)
    clean_parent_id = str(parent_attempt_id or "").strip()
    # Validate against the RAW input, before any fallback blending: `resolved_trigger_turn` below
    # falls back to `origin_user_turn_id` when `trigger_user_turn_id` is empty -- correct for a
    # FIRST attempt (only one of the two was supplied, so treating them as the same turn is
    # reasonable), but for a RETRY this fallback would let an empty trigger silently inherit the
    # PARENT's own origin turn (always non-empty once Repair 4 wired it) and sail straight past
    # this guard -- found live via Repair 9's own required mutation ("remove origin/trigger wiring
    # -> idempotency tests fail"), which stayed green until this check moved to the raw value.
    raw_trigger_turn = str(trigger_user_turn_id or "").strip()
    if clean_parent_id and not raw_trigger_turn:
        raise ValueError(
            "trigger_user_turn_id is required to create a retry attempt (parent_attempt_id is "
            "set) -- an empty trigger turn cannot be used as a durable idempotency identity"
        )
    resolved_origin_turn = str(origin_user_turn_id or trigger_user_turn_id or "").strip()
    resolved_trigger_turn = str(trigger_user_turn_id or resolved_origin_turn).strip()
    resolved_root = str(root_attempt_id or "").strip() or attempt_id
    # ARCH-TRUTH-R1c: the role is a closed vocabulary. An unknown one is a caller defect,
    # not something to store and let a reader interpret later.
    resolved_role = str(role or "").strip()
    if resolved_role and resolved_role not in {member.value for member in AttemptRole}:
        raise ValueError(f"unknown attempt role: {resolved_role!r}")
    # ARCH-TRUTH-R1d: the executing slot within the chain. '' is the chain's ordinary slot
    # (root, single answer, retry generation); a planned sub-request carries its own stable
    # task key, which is what lets a wave of distinct tasks be live at once while the same
    # task still cannot execute twice.
    resolved_slot = str(execution_slot or "").strip()
    daemon_sha = _daemon_commit_sha()
    process_id = _process_instance_id()
    # Dedup identity is scoped to the ROOT of the retry chain, not the immediate parent. Two
    # concurrent retries of the SAME logical retry (same trigger turn + intent) can resolve
    # DIFFERENT immediate parents: under contention, thread A creates a fresh RECEIVED retry child
    # and thread B, resolving "the latest unresolved-or-partial attempt in session," picks up that
    # child instead of the original -- a parent-scoped key then differs between the two and the
    # `ON CONFLICT (retry_idempotency_key)` insert cannot collapse them, producing two children.
    # Every attempt in one retry chain shares `root_attempt_id`, so keying on the root makes the
    # two racers agree regardless of which immediate parent each resolved. A genuinely new retry
    # turn still gets a distinct key via its distinct `trigger_user_turn_id`, so it is still free to
    # create a new generation (see `test_a_genuinely_new_retry_turn_is_allowed_to_create_a_new_generation`).
    retry_idempotency_key = (
        compute_retry_idempotency_key(resolved_root, resolved_trigger_turn, resolution_intent)
        if clean_parent_id else ""
    )
    # R-3 (H1 §2): generation is ALLOCATED by the L0 fence CAS — never caller
    # arithmetic. When a fence row governs this execution, two distinct
    # concurrent retries serialize on bump_generation and receive DISTINCT
    # generations; MAX_GENERATION ceiling + budget custody bind at the same
    # CAS. Without a fence row (legacy chain), the caller-supplied value stays
    # the fallback so historical lanes keep their numbering.
    resolved_execution_generation = int(execution_generation)

    def _chain_was_boot_swept_retryable(root_attempt_id: str) -> bool:
        """A9 RC-6 policy gate for epoch adoption: the ONLY chains whose fence may be
        re-stamped onto a new process's epoch are those the startup recovery sweep
        itself marked ABANDONED+retryable — i.e. the stored epoch's writer is provably
        gone (the sweep runs at boot, older_than_seconds=0). Live or non-abandoned
        chains keep the strict epoch refusal.

        A9 pass-002 (CE-4): LIVE authoritative execution state beats stale
        historical retryability. A boot-swept ABANDONED ancestor stops being an
        adoption entitlement the moment the chain contains a LIVE (non-terminal)
        execution minted after the abandonment — e.g. the RUNNING child of an
        already-performed legitimate adoption. Once re-emerged, the chain keeps
        the strict epoch refusal: stale history must never re-stamp a fence
        over a live writer."""
        clean_root = str(root_attempt_id or "").strip()
        if not clean_root:
            return False
        try:
            with _LOCK:
                conn = _conn()
                try:
                    swept = conn.execute(
                        """
                        SELECT 1 FROM runtime_attempts
                        WHERE root_attempt_id = ?
                          AND lifecycle_state = 'ABANDONED' AND retryable = 1
                        LIMIT 1
                        """,
                        (clean_root,),
                    ).fetchone()
                    if swept is None:
                        return False
                    # Liveness check on the SAME chain: any RECEIVED/PLANNED/RUNNING
                    # execution outranks the historical entitlement.
                    live_conflict = conn.execute(
                        """
                        SELECT 1 FROM runtime_attempts
                        WHERE root_attempt_id = ?
                          AND lifecycle_state IN (
                              'RECEIVED', 'PLANNED', 'RUNNING'
                          )
                        LIMIT 1
                        """,
                        (clean_root,),
                    ).fetchone()
                    return live_conflict is None
                finally:
                    conn.close()
        except Exception:
            return False

    def _chain_retry_rearm_eligible(
        root_id: str, parent_id: str, intent: str
    ) -> bool:
        """C10/R1 (AUD-20260829-003): an explicit user retry/REPEAT may re-arm a
        TERMINAL chain's fence row. Eligible only when (a) the resolution is an
        explicit retry intent, (b) the immediate parent attempt is itself
        terminal, and (c) no attempt anywhere on the chain is live. The ledger's
        `rearm_terminal_chain` owns the row-level predicate (terminal state,
        ceiling) — this gate is the policy half, mirroring the epoch-adoption
        split above.

        ARCH-TRUTH-R1c: reads the attempt rows through THIS module's own connection,
        like every other runtime_attempts query here and like its epoch-adoption sibling
        above. It used to open `get_connection()` — the ledger/default store — so wherever
        the continuity store is configured separately the lookup ran against a database
        holding no attempts, the parent read as missing, and every explicit retry of a
        terminal chain was refused. Harmless while the answering attempt had its own
        unfenced root; load-bearing now that a turn's answer shares the turn root's fence,
        which is terminal by the time a later turn asks to retry it."""
        from core.attempt_followup import REPEAT_ORIGINAL_REQUEST, RETRY_ATTEMPT

        if str(intent or "") not in (RETRY_ATTEMPT, REPEAT_ORIGINAL_REQUEST):
            return False
        try:
            conn = _conn()
            try:
                parent = conn.execute(
                    "SELECT lifecycle_state FROM runtime_attempts WHERE attempt_id = ?",
                    (str(parent_id or ""),),
                ).fetchone()
                if parent is None or parent[0] not in _TERMINAL_ATTEMPT_STATES:
                    return False
                live = conn.execute(
                    """
                    SELECT 1 FROM runtime_attempts
                    WHERE root_attempt_id = ?
                      AND lifecycle_state IN ('RECEIVED', 'PLANNED', 'RUNNING')
                    LIMIT 1
                    """,
                    (str(root_id or ""),),
                ).fetchone()
                return live is None
            finally:
                conn.close()
        except Exception:
            return False

    def _next_unfenced_chain_generation(root_id: str) -> int:
        """One past the highest generation this chain has durably recorded.

        The unfenced counterpart of the CAS: the same monotone rule, read from the
        table that stores the counter because the executions row that would allocate
        it does not exist for this chain.
        """
        conn = _conn()
        try:
            row = conn.execute(
                "SELECT MAX(execution_generation) FROM runtime_attempts WHERE root_attempt_id = ?",
                (str(root_id or ""),),
            ).fetchone()
        finally:
            conn.close()
        highest = int(row[0]) if row is not None and row[0] is not None else 0
        return highest + 1

    if clean_parent_id:
        from core.invocation.ledger import FenceRefused, bump_generation, get_execution

        if get_execution(resolved_root) is not None:
            try:
                resolved_execution_generation = bump_generation(resolved_root) + 1
            except FenceRefused as exc:
                # A9 RC-6: a fence refusal on epoch alone — for a chain the restart
                # recovery sweep itself marked ABANDONED+retryable — is adoption, not
                # trespass. Re-stamp onto the current writer's epoch exactly once and
                # retry the CAS; every other refusal (ceiling, terminal row, live
                # foreign epoch) still refuses.
                adopted = False
                if _chain_was_boot_swept_retryable(resolved_root):
                    from core.invocation.ledger import restamp_dead_epoch

                    adopted = restamp_dead_epoch(resolved_root)
                    if adopted:
                        try:
                            resolved_execution_generation = bump_generation(resolved_root) + 1
                        except FenceRefused:
                            adopted = False
                if not adopted:
                    # C10/R1: a refusal on STATE (the chain's fence went terminal
                    # when its turn completed) for an EXPLICIT user retry is a
                    # re-arm, not trespass: the user authorized a new generation
                    # on the same chain. Ceiling and live-writer refusals still
                    # refuse -- `rearm_terminal_chain` will not touch them.
                    re_armed = False
                    if _chain_retry_rearm_eligible(
                        resolved_root, clean_parent_id, resolution_intent
                    ):
                        from core.invocation.ledger import rearm_terminal_chain

                        re_armed = rearm_terminal_chain(resolved_root)
                        if re_armed:
                            try:
                                resolved_execution_generation = (
                                    bump_generation(resolved_root) + 1
                                )
                            except FenceRefused:
                                re_armed = False
                    if not re_armed:
                        raise MintRefused(
                            "FENCE_REFUSED",
                            f"generation allocation refused for {resolved_root}: {exc}",
                        ) from exc
        else:
            # No fence row governs this chain: a chain rooted before L0 existed, or a
            # lane with no A0 request to open one. R-3 deleted the caller's parent+1
            # arithmetic and this parameter defaults to 1, so the fallback handed every
            # retry of an unfenced chain its PARENT's generation -- two rows of one chain
            # at generation 1, a chain counter that never advances, and an "earlier
            # attempt" indistinguishable from the current one. Allocate from the chain's
            # own durable rows instead: the same monotone rule the CAS enforces, read
            # from the table that stores the counter. An explicit caller value already
            # ahead of the chain still wins, so a lane that does supply its own
            # numbering keeps it.
            resolved_execution_generation = max(
                int(execution_generation), _next_unfenced_chain_generation(resolved_root)
            )
    # R-3: ensure the L0 fence row exists for this execution (idempotent on the
    # retry lane). Best-effort here because run_once's turn door is the
    # authoritative open; legacy lanes without an A0 request stay unfenced.
    try:
        from core.invocation.ledger import open_execution as _r3_open_exec
        from core.semantic.semantic_admissions import current_request_id as _r3_cur_req

        _r3_req = _r3_cur_req()
        if _r3_req and get_execution(resolved_root) is None:
            _r3_open_exec(request_id=_r3_req, root_attempt_id=resolved_root)
    except Exception:
        pass
    insert_sql = """
        INSERT INTO runtime_attempts (
            attempt_id, session_id, checkpoint_id,
            origin_user_turn_id, trigger_user_turn_id, origin_conversation_event_id,
            root_attempt_id, parent_attempt_id, execution_generation,
            original_request_snapshot, original_request_hash, original_request_bytes,
            original_request_truncated,
            answer_mode, plan_id, lifecycle_state, terminal_reason,
            retryable, retry_reason, retry_from_stage, refresh_required, refresh_reason,
            retry_idempotency_key,
            created_daemon_sha, last_updated_daemon_sha, process_instance_id,
            execution_id, attempt_role, execution_slot,
            created_at, updated_at, completed_at
        ) VALUES (
            ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?,
            ?,
            ?, ?, ?, '',
            0, '', '', 0, '',
            ?,
            ?, ?, ?,
            ?, ?, ?,
            ?, ?, NULL
        )
    """
    insert_params = (
        attempt_id, clean_session_id, str(checkpoint_id or "").strip(),
        resolved_origin_turn, resolved_trigger_turn, str(origin_conversation_event_id or "").strip(),
        resolved_root, str(parent_attempt_id or "").strip(), resolved_execution_generation,
        snapshot, request_hash, raw_bytes,
        int(truncated),
        str(answer_mode or "").strip(), str(plan_id or "").strip(), AttemptLifecycle.RECEIVED.value,
        retry_idempotency_key,
        daemon_sha, daemon_sha, process_id,
        # L0 fence identity (K-02/D2): execution_id aliases the retry-chain root.
        resolved_root, resolved_role, resolved_slot,
        timestamp, timestamp,
    )
    with _LOCK:
        conn = _conn()
        try:
            won_the_insert = False
            row = None
            if retry_idempotency_key:
                # SQLite requires the ON CONFLICT target to restate the partial index's WHERE
                # clause verbatim -- "ON CONFLICT (retry_idempotency_key) DO NOTHING" alone does not
                # match a partial unique index and raises OperationalError at execute time.
                cursor = conn.execute(
                    f"{insert_sql} ON CONFLICT (retry_idempotency_key) WHERE retry_idempotency_key != '' DO NOTHING",
                    insert_params,
                )
                won_the_insert = int(cursor.rowcount or 0) == 1
                row = conn.execute(
                    f"SELECT {_RUNTIME_ATTEMPT_COLUMNS} FROM runtime_attempts WHERE retry_idempotency_key = ? LIMIT 1",
                    (retry_idempotency_key,),
                ).fetchone()
            else:
                conn.execute(insert_sql, insert_params)
                won_the_insert = True
                row = conn.execute(
                    f"SELECT {_RUNTIME_ATTEMPT_COLUMNS} FROM runtime_attempts WHERE attempt_id = ? LIMIT 1",
                    (attempt_id,),
                ).fetchone()
            # H-1/INV-3 SUPERSESSION-IS-A-TRANSITION: only the mint WINNER
            # supersedes; it terminalizes every non-terminal predecessor of the
            # same execution to SUPERSEDED inside this same transaction —
            # refusing when any predecessor is PENDING_RECONCILIATION or the
            # execution carries unreconciled UNKNOWN/DISPATCHED effects.
            if won_the_insert and clean_parent_id:
                ex_id = resolved_root
                blocking = conn.execute(
                    """
                    SELECT COUNT(*) AS c FROM runtime_attempts
                    WHERE execution_id = ? AND lifecycle_state IN ('PENDING_RECONCILIATION')
                    """,
                    (ex_id,),
                ).fetchone()
                if int(blocking["c"] or 0) > 0:
                    raise _MintRefused(
                        "PENDING_RECONCILIATION",
                        f"[PENDING_RECONCILIATION] execution {ex_id} has an unreconciled attempt; retry refused",
                    )
                unknown_effects = conn.execute(
                    """
                    SELECT COUNT(*) AS c FROM runtime_unresolved_effects
                    WHERE state IN ('unknown', 'dispatched')
                      AND attempt_id IN (
                          SELECT attempt_id FROM runtime_attempts
                          WHERE execution_id = ? AND attempt_id != ?
                      )
                    """,
                    (ex_id, attempt_id),
                ).fetchone()
                if int(unknown_effects["c"] or 0) > 0:
                    raise _MintRefused(
                        "UNRESOLVED_EFFECTS",
                        f"[UNRESOLVED_EFFECTS] execution {ex_id} has unreconciled UNKNOWN/DISPATCHED effects; mint refused",
                    )
                conn.execute(
                    """
                    UPDATE runtime_attempts SET lifecycle_state = 'SUPERSEDED',
                        terminal_reason = 'superseded_by_successor_mint', updated_at = ?
                    WHERE execution_id = ? AND attempt_id != ?
                      AND lifecycle_state NOT IN
                          ('SUCCEEDED','PARTIAL_SUCCESS','FAILED_TOOL','FAILED_PROVIDER',
                           'FAILED_SYNTHESIS','FAILED_VALIDATION','PENDING_RECONCILIATION',
                           'CANCELLED','ABANDONED','SUPERSEDED')
                    """,
                    (_utcnow(), ex_id, attempt_id),
                )
            conn.commit()
        finally:
            conn.close()
    result = _attempt_row_to_dict(row) if row else {}
    result["idempotent_replay"] = not won_the_insert
    # An event only for the row this call ACTUALLY created -- a replayed/concurrent caller that
    # lost the insert race did not create anything, and must not spam a second "created" event for
    # an attempt that already exists.
    if won_the_insert and clean_session_id:
        # The event this row emits is grouped by the chat page strictly on `client_turn_id`
        # (an id the CLIENT sent with the turn -- a different id space from the dialogue-turn
        # ids stored on the row above). Emitted only when the caller supplied one: inventing a
        # tag here would misfile retry/sweep events under a turn they do not belong to.
        clean_client_turn_id = str(client_turn_id or "").strip()
        with contextlib.suppress(Exception):
            append_runtime_event(
                session_id=clean_session_id,
                event_type="runtime_attempt_created",
                message=f"Runtime attempt {attempt_id} created ({answer_mode or 'unknown mode'}).",
                details={
                    "attempt_id": str(result.get("attempt_id") or attempt_id), "checkpoint_id": str(checkpoint_id or "").strip(),
                    "answer_mode": str(answer_mode or ""), "root_attempt_id": resolved_root,
                    # The generation this row was ACTUALLY minted with, not the caller's
                    # request for one: the fence CAS (or, unfenced, the chain's own counter)
                    # allocates it above, so echoing the raw parameter published a generation
                    # that disagreed with the durable row on every retry.
                    "parent_attempt_id": str(parent_attempt_id or ""), "execution_generation": int(resolved_execution_generation),
                    # Same value under the canonical field name, so a reader keying on
                    # `turn_key` sees these rows too. Still only when the caller supplied a
                    # turn: an invented key would misfile a sweep event, exactly as above.
                    **({"client_turn_id": clean_client_turn_id, "turn_key": clean_client_turn_id} if clean_client_turn_id else {}),
                },
            )
    return result


_RUNTIME_ATTEMPT_COLUMNS = (
    "attempt_id, session_id, checkpoint_id, "
    "origin_user_turn_id, trigger_user_turn_id, origin_conversation_event_id, "
    "root_attempt_id, parent_attempt_id, execution_generation, "
    "original_request_snapshot, original_request_hash, original_request_bytes, original_request_truncated, "
    "answer_mode, plan_id, lifecycle_state, terminal_reason, "
    "retryable, retry_reason, retry_from_stage, refresh_required, refresh_reason, "
    "retry_idempotency_key, "
    "created_daemon_sha, last_updated_daemon_sha, process_instance_id, "
    "execution_id, attempt_role, execution_slot, "
    "created_at, updated_at, completed_at"
)


def _attempt_row_to_dict(row: Any) -> dict[str, Any]:
    data = dict(row)
    data["retryable"] = bool(data.get("retryable"))
    data["refresh_required"] = bool(data.get("refresh_required"))
    data["original_request_truncated"] = bool(data.get("original_request_truncated"))
    return data


def get_runtime_attempt(attempt_id: str) -> dict[str, Any] | None:
    clean_attempt_id = str(attempt_id or "").strip()
    if not clean_attempt_id:
        return None
    with _LOCK:
        conn = _conn()
        try:
            row = conn.execute(
                f"SELECT {_RUNTIME_ATTEMPT_COLUMNS} FROM runtime_attempts WHERE attempt_id = ? LIMIT 1",
                (clean_attempt_id,),
            ).fetchone()
        finally:
            conn.close()
    return _attempt_row_to_dict(row) if row else None


def latest_runtime_attempt(
    session_id: str, *, exclude_attempt_id: str = ""
) -> dict[str, Any] | None:
    """The most recent attempt for a session, regardless of terminal/non-terminal state -- the
    read side a "why did that fail?"/"retry" resolver (Step 9) will query. Not used for anything
    in Step 7 itself beyond the persistence-checkpoint verification.

    A9 pass-002 (CE-5): `exclude_attempt_id` removes the CURRENT follow-up turn's own
    freshly-minted attempt from newest-row resolution, so a referential phrase can never
    bind to itself merely by being newest.

    ARCH-TRUTH-R1c: "most recent" is ranked by ROLE first and recency second. The turn
    door's bookkeeping row is closed LAST in every turn, so pure recency made it the
    answer to "the latest attempt in this session" -- and it carries no subtasks, so a
    referential follow-up bound to it reported that nothing was recorded. Ranking the
    turn root last makes the selection structural: recency only orders rows that are
    equally eligible, and legacy rows (role '') rank WITH the answer-bearing ones, so a
    database written before the role column resolves exactly as it did before."""
    clean_session_id = str(session_id or "").strip()
    if not clean_session_id:
        return None
    excluded = str(exclude_attempt_id or "").strip()
    with _LOCK:
        conn = _conn()
        try:
            row = conn.execute(
                f"""
                SELECT {_RUNTIME_ATTEMPT_COLUMNS} FROM runtime_attempts
                WHERE session_id = ? AND (? = '' OR attempt_id != ?)
                ORDER BY {_ROLE_RANK_SQL}, updated_at DESC
                LIMIT 1
                """,
                (clean_session_id, excluded, excluded),
            ).fetchone()
        finally:
            conn.close()
    return _attempt_row_to_dict(row) if row else None


def recent_runtime_attempts(
    session_id: str, *, exclude_attempt_id: str = "", limit: int = 8
) -> tuple[dict[str, Any], ...]:
    """The session's most recent attempts, newest first.

    AUD-20260829-003 (C9/C12): `latest_unresolved_or_partial_attempt` answers one question --
    "is there an attempt whose LIFECYCLE says something is outstanding" -- and that is the wrong
    grain for a turn that served some of what was asked and dropped the rest. Such a turn is
    SUCCEEDED at the attempt level while its obligation set carries slots the finalization sweep
    drove to `unanswered`; measured in `runtime_followup_resolutions`, every "why did that fail?"
    on such a turn resolved to `no unresolved or partially failed attempt in session` and fell
    through to generation.

    This accessor exists so a caller can ask the PER-SLOT record instead: walk the session's
    recent attempts and consult each one's demand obligations. It carries no policy of its own --
    it is a bounded, ordered read, and `core.refused_slot_register` owns what the rows mean.
    """
    clean_session_id = str(session_id or "").strip()
    if not clean_session_id:
        return ()
    excluded = str(exclude_attempt_id or "").strip()
    bounded = max(1, min(int(limit or 1), 64))
    with _LOCK:
        conn = _conn()
        try:
            rows = conn.execute(
                f"""
                SELECT {_RUNTIME_ATTEMPT_COLUMNS} FROM runtime_attempts
                WHERE session_id = ? AND (? = '' OR attempt_id != ?)
                ORDER BY {_ROLE_RANK_SQL}, updated_at DESC
                LIMIT ?
                """,
                (clean_session_id, excluded, excluded, bounded),
            ).fetchall()
        finally:
            conn.close()
    return tuple(_attempt_row_to_dict(row) for row in rows or ())


# Attempts a follow-up resolver should treat as "still has something to explain/retry" --
# excludes only the two states where there is nothing left to act on: a fully successful attempt
# (nothing failed) and a user-cancelled one (explicitly closed).
_UNRESOLVED_OR_PARTIAL_STATES = (
    AttemptLifecycle.RECEIVED.value,
    AttemptLifecycle.PLANNED.value,
    AttemptLifecycle.RUNNING.value,
    AttemptLifecycle.PARTIAL_SUCCESS.value,
    AttemptLifecycle.FAILED_TOOL.value,
    AttemptLifecycle.FAILED_PROVIDER.value,
    AttemptLifecycle.FAILED_SYNTHESIS.value,
    AttemptLifecycle.FAILED_VALIDATION.value,
    AttemptLifecycle.WAITING_APPROVAL.value,
    AttemptLifecycle.ABANDONED.value,
)


def latest_unresolved_or_partial_attempt(
    session_id: str, *, exclude_attempt_id: str = ""
) -> dict[str, Any] | None:
    """Resolution precedence tier 3 (Step 9): the most recent attempt in the session that still
    has something to explain or retry -- i.e. not a clean SUCCEEDED and not user-CANCELLED. This
    is what "why did that fail?"/"retry" bind to; it deliberately does NOT fall back to a fully
    successful attempt, so a genuinely unrelated question after a clean success does not get
    hijacked into re-explaining a request that worked.

    A9 pass-002 (CE-5): the CURRENT follow-up turn's own attempt (always RUNNING when tier 3
    runs, because the turn door claims it first) is excluded via `exclude_attempt_id` — the
    follow-up phrase itself must never shadow the prior failed execution it refers to.

    ARCH-TRUTH-R1c: ranked by ROLE first (see `latest_runtime_attempt`) — a prior turn's
    bookkeeping root must not stand in for the answering attempt that holds the failure."""
    clean_session_id = str(session_id or "").strip()
    if not clean_session_id:
        return None
    excluded = str(exclude_attempt_id or "").strip()
    placeholders = ",".join("?" for _ in _UNRESOLVED_OR_PARTIAL_STATES)
    with _LOCK:
        conn = _conn()
        try:
            row = conn.execute(
                f"""
                SELECT {_RUNTIME_ATTEMPT_COLUMNS} FROM runtime_attempts
                WHERE session_id = ? AND lifecycle_state IN ({placeholders})
                  AND (? = '' OR attempt_id != ?)
                ORDER BY {_ROLE_RANK_SQL}, updated_at DESC
                LIMIT 1
                """,
                (clean_session_id, *_UNRESOLVED_OR_PARTIAL_STATES, excluded, excluded),
            ).fetchone()
        finally:
            conn.close()
    return _attempt_row_to_dict(row) if row else None


def get_runtime_attempt_by_id_fragment(session_id: str, fragment: str) -> dict[str, Any] | None:
    """Resolution precedence tier 1 (Step 9): an explicitly-referenced attempt id, when the user's
    text names one directly (rare in normal phrasing, but the highest-precedence match when
    present). Scoped to the session so a copy-pasted id from a DIFFERENT session can't be used to
    read another session's attempt."""
    clean_session_id = str(session_id or "").strip()
    clean_fragment = str(fragment or "").strip()
    if not clean_session_id or not clean_fragment:
        return None
    with _LOCK:
        conn = _conn()
        try:
            row = conn.execute(
                f"""
                SELECT {_RUNTIME_ATTEMPT_COLUMNS} FROM runtime_attempts
                WHERE session_id = ? AND attempt_id LIKE ?
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (clean_session_id, f"%{clean_fragment}%"),
            ).fetchone()
        finally:
            conn.close()
    return _attempt_row_to_dict(row) if row else None


def find_child_attempt(parent_attempt_id: str) -> dict[str, Any] | None:
    """The most recent attempt whose parent_attempt_id is this one, if any -- the idempotency
    check a retry dispatcher runs BEFORE calling `execute_attempt_retry` (Step 10 correction): a
    repeated retry request for the same parent while a child is already RECEIVED/PLANNED/RUNNING
    must resume/reference that child rather than start a second concurrent execution. Once the
    child reaches a terminal state, a genuinely NEW retry request is free to create another one."""
    clean_parent_id = str(parent_attempt_id or "").strip()
    if not clean_parent_id:
        return None
    with _LOCK:
        conn = _conn()
        try:
            row = conn.execute(
                f"""
                SELECT {_RUNTIME_ATTEMPT_COLUMNS} FROM runtime_attempts
                WHERE parent_attempt_id = ?
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (clean_parent_id,),
            ).fetchone()
        finally:
            conn.close()
    return _attempt_row_to_dict(row) if row else None


def find_attempt_by_retry_idempotency_key(retry_idempotency_key: str) -> dict[str, Any] | None:
    """The durable, terminal-surviving idempotency lookup (Repair 2/3, Mnemosyne review): the
    attempt row, if any, already created for this exact (parent_attempt_id, trigger_user_turn_id,
    resolution_intent) triple -- regardless of whether that row is still in flight or has long
    since reached a terminal state. This is the public read side of the same uniqueness constraint
    `create_runtime_attempt` writes through; callers outside this module (the retry dispatcher)
    should use this rather than reaching into private row-access helpers."""
    clean_key = str(retry_idempotency_key or "").strip()
    if not clean_key:
        return None
    with _LOCK:
        conn = _conn()
        try:
            row = conn.execute(
                f"SELECT {_RUNTIME_ATTEMPT_COLUMNS} FROM runtime_attempts WHERE retry_idempotency_key = ? LIMIT 1",
                (clean_key,),
            ).fetchone()
        finally:
            conn.close()
    return _attempt_row_to_dict(row) if row else None


def update_runtime_attempt(
    attempt_id: str,
    *,
    plan_id: str | None = None,
    lifecycle_state: str | None = None,
    terminal_reason: str | None = None,
    retryable: bool | None = None,
    retry_reason: str | None = None,
    retry_from_stage: str | None = None,
    refresh_required: bool | None = None,
    refresh_reason: str | None = None,
    completed: bool = False,
    client_turn_id: str = "",
) -> dict[str, Any] | None:
    current = get_runtime_attempt(attempt_id)
    if not current:
        return None
    updated_at = _utcnow()
    current_state = str(current.get("lifecycle_state") or "")
    next_state = str(lifecycle_state if lifecycle_state is not None else current_state or AttemptLifecycle.RECEIVED.value)
    # H-1 TERMINAL ABSORBS: a terminal row refuses further lifecycle writes —
    # no writer may resurrect it. The refusal is RETURNED, never swallowed.
    if (
        lifecycle_state is not None
        and current_state in _TERMINAL_ATTEMPT_STATES
        and next_state != current_state
    ):
        return {
            "attempt_id": str(attempt_id),
            "transition_refused": "terminal_absorbs",
            "lifecycle_state": current_state,
            "refused_state": next_state,
        }
    completed_at = updated_at if (completed or next_state in _TERMINAL_ATTEMPT_STATES) else current.get("completed_at")
    with _LOCK:
        conn = _conn()
        try:
            terminal_placeholders = ", ".join("?" for _ in _TERMINAL_ATTEMPT_STATES)
            conn.execute(
                f"""
                UPDATE runtime_attempts
                SET plan_id = ?,
                    lifecycle_state = ?,
                    terminal_reason = ?,
                    retryable = ?,
                    retry_reason = ?,
                    retry_from_stage = ?,
                    refresh_required = ?,
                    refresh_reason = ?,
                    last_updated_daemon_sha = ?,
                    updated_at = ?,
                    completed_at = ?
                WHERE attempt_id = ?
                  AND lifecycle_state NOT IN ({terminal_placeholders})
                """,
                (
                    str(plan_id if plan_id is not None else current.get("plan_id") or ""),
                    next_state,
                    str(terminal_reason if terminal_reason is not None else current.get("terminal_reason") or ""),
                    int(bool(retryable if retryable is not None else current.get("retryable"))),
                    str(retry_reason if retry_reason is not None else current.get("retry_reason") or ""),
                    str(retry_from_stage if retry_from_stage is not None else current.get("retry_from_stage") or ""),
                    int(bool(refresh_required if refresh_required is not None else current.get("refresh_required"))),
                    str(refresh_reason if refresh_reason is not None else current.get("refresh_reason") or ""),
                    _daemon_commit_sha(),
                    updated_at,
                    completed_at,
                    str(attempt_id),
                    *_TERMINAL_ATTEMPT_STATES,
                ),
            )
            conn.commit()
        finally:
            conn.close()
    session_id = str(current.get("session_id") or "")
    if session_id and next_state != current.get("lifecycle_state"):
        is_terminal = next_state in _TERMINAL_ATTEMPT_STATES
        # Tagged only when the caller supplied the client turn id (same contract as
        # `create_runtime_attempt`): the retry dispatcher and the restart sweep call this with no
        # turn in scope, and their events must stay untagged rather than inherit a wrong turn.
        clean_client_turn_id = str(client_turn_id or "").strip()
        with contextlib.suppress(Exception):
            append_runtime_event(
                session_id=session_id,
                event_type="runtime_attempt_completed" if is_terminal else "runtime_attempt_updated",
                message=f"Runtime attempt {attempt_id} -> {next_state}.",
                details={
                    "attempt_id": str(attempt_id), "lifecycle_state": next_state,
                    "terminal_reason": str(terminal_reason or current.get("terminal_reason") or ""),
                    "retryable": bool(retryable if retryable is not None else current.get("retryable")),
                    # Same value under the canonical field name, so a reader keying on
                    # `turn_key` sees these rows too. Still only when the caller supplied a
                    # turn: an invented key would misfile a sweep event, exactly as above.
                    **({"client_turn_id": clean_client_turn_id, "turn_key": clean_client_turn_id} if clean_client_turn_id else {}),
                },
            )
    return get_runtime_attempt(attempt_id)


def demote_runtime_attempt_if_succeeded(
    attempt_id: str,
    *,
    terminal_reason: str,
) -> dict[str, Any] | None:
    """SUCCEEDED -> PARTIAL_SUCCESS, as one CAS, when the demand census proves it.

    M3B root cause D (measured live 2026-08-30): `reconcile_attempt_lifecycle` derives
    the parent state from its SUBTASK rows, so a turn whose every dispatched subtask
    succeeded finalized SUCCEEDED even while most of the user's demands were never
    minted into the plan at all -- Incident 3 served London weather only and certified
    full success. The canonical completeness denominator is the OBLIGATION census, and
    the one seam every finalized answer passes through (`_rss_closure_sweep`) is where
    that census is terminal -- so that is where this demotion is invoked from.

    This is a deliberate, narrow exception to H-1's terminal-absorbs refusal, and it is
    NOT a resurrection path: the transition is monotonic (SUCCEEDED -> PARTIAL_SUCCESS
    only), fires only when the row still reads SUCCEEDED at the moment of the write
    (a concurrent terminal writer wins the race and this no-ops), and its reason is a
    census count, never prose. Every other terminal state is untouchable here.
    """
    attempt_id = str(attempt_id or "").strip()
    reason = str(terminal_reason or "").strip() or "demand obligations unanswered"
    if not attempt_id:
        return None
    retryable, retry_reason = _default_retry_guidance(AttemptLifecycle.PARTIAL_SUCCESS.value)
    updated_at = _utcnow()
    fired = False
    session_id = ""
    with _LOCK:
        conn = _conn()
        try:
            cursor = conn.execute(
                """
                UPDATE runtime_attempts
                SET lifecycle_state = ?,
                    terminal_reason = ?,
                    retryable = ?,
                    retry_reason = ?,
                    last_updated_daemon_sha = ?,
                    updated_at = ?,
                    completed_at = ?
                WHERE attempt_id = ?
                  AND lifecycle_state = 'SUCCEEDED'
                """,
                (
                    AttemptLifecycle.PARTIAL_SUCCESS.value,
                    reason,
                    int(retryable),
                    retry_reason,
                    _daemon_commit_sha(),
                    updated_at,
                    updated_at,
                    attempt_id,
                ),
            )
            fired = bool(cursor.rowcount)
            if fired:
                row = conn.execute(
                    "SELECT session_id FROM runtime_attempts WHERE attempt_id = ?",
                    (attempt_id,),
                ).fetchone()
                session_id = str(row["session_id"]) if row is not None else ""
            conn.commit()
        finally:
            conn.close()
    if fired and session_id:
        with contextlib.suppress(Exception):
            append_runtime_event(
                session_id=session_id,
                event_type="runtime_attempt_updated",
                message=f"Runtime attempt {attempt_id} -> PARTIAL_SUCCESS (demand census).",
                details={
                    "attempt_id": attempt_id,
                    "lifecycle_state": AttemptLifecycle.PARTIAL_SUCCESS.value,
                    "terminal_reason": reason,
                    "retryable": bool(retryable),
                    "demoted_by": "demand_census",
                },
            )
    current = get_runtime_attempt(attempt_id)
    if isinstance(current, dict):
        current["demotion_fired"] = fired
    return current


_RUNTIME_ATTEMPT_SUBTASK_COLUMNS = (
    "attempt_id, subtask_id, execution_generation, plan_id, operation, entity_type, entity_key, "
    "arguments_json, lifecycle_state, result_summary_json, failure_class, failure_reason, approval_state, "
    "retryable, retry_reason, refresh_required, refresh_reason, "
    "carried_forward_from_attempt_id, carried_forward_from_generation, rerun_reason, previous_result_version, "
    "queued_at, started_at, completed_at, retrieved_at, result_version, updated_at"
)


def _subtask_row_to_dict(row: Any) -> dict[str, Any]:
    data = dict(row)
    data["arguments"] = _json_loads(data.pop("arguments_json", "{}"), fallback={})
    data["result_summary"] = _json_loads(data.pop("result_summary_json", "{}"), fallback={})
    data["retryable"] = bool(data.get("retryable"))
    data["refresh_required"] = bool(data.get("refresh_required"))
    return data


def upsert_runtime_attempt_subtask(
    *,
    attempt_id: str,
    subtask_id: str,
    execution_generation: int = 1,
    plan_id: str = "",
    operation: str = "",
    entity_type: str = "",
    entity_key: str = "",
    arguments: dict[str, Any] | None = None,
    lifecycle_state: str = "PLANNED",
    result_summary: dict[str, Any] | None = None,
    failure_class: str = "",
    failure_reason: str = "",
    approval_state: str = "",
    retryable: bool = False,
    retry_reason: str = "",
    refresh_required: bool = False,
    refresh_reason: str = "",
    carried_forward_from_attempt_id: str = "",
    carried_forward_from_generation: int = 0,
    rerun_reason: str = "",
    previous_result_version: int = 0,
    queued_at: str | None = None,
    started_at: str | None = None,
    completed_at: str | None = None,
    retrieved_at: str | None = None,
) -> dict[str, Any]:
    """Create-or-update ONE subtask row, keyed by (attempt_id, subtask_id, execution_generation).

    Every subtask transition updates only this row -- never a shared blob -- so concurrent workers
    completing different subtasks in the same attempt never race (Step 7 correction #1). Safe to
    call once to plan the subtask and again (any number of times) to record its outcome; a
    concurrent write to a DIFFERENT subtask_id is always a different row and never contends.

    `carried_forward_from_attempt_id`/`carried_forward_from_generation` (Step 10) mark a subtask
    whose result came from a PRIOR execution generation, never re-fetched; `rerun_reason` marks the
    opposite -- a subtask that WAS re-executed this generation, and why. A row should set at most
    one of the two, never both.
    """
    from core.secret_redaction import redact_secrets

    timestamp = _utcnow()
    clean_arguments = _scrub_persisted(dict(arguments or {}))
    clean_result_summary = _scrub_persisted(dict(result_summary or {}))
    clean_failure_reason = redact_secrets(str(failure_reason or ""))[:1000]
    with _LOCK:
        conn = _conn()
        try:
            existing = conn.execute(
                "SELECT result_version FROM runtime_attempt_subtasks WHERE attempt_id = ? AND subtask_id = ? AND execution_generation = ?",
                (str(attempt_id), str(subtask_id), int(execution_generation)),
            ).fetchone()
            next_version = int(existing["result_version"]) + 1 if existing else 1
            conn.execute(
                """
                INSERT INTO runtime_attempt_subtasks (
                    attempt_id, subtask_id, execution_generation, plan_id, operation, entity_type, entity_key,
                    arguments_json, lifecycle_state, result_summary_json, failure_class, failure_reason, approval_state,
                    retryable, retry_reason, refresh_required, refresh_reason,
                    carried_forward_from_attempt_id, carried_forward_from_generation, rerun_reason, previous_result_version,
                    queued_at, started_at, completed_at, retrieved_at, result_version, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (attempt_id, subtask_id, execution_generation) DO UPDATE SET
                    plan_id = excluded.plan_id,
                    operation = excluded.operation,
                    entity_type = excluded.entity_type,
                    entity_key = excluded.entity_key,
                    arguments_json = excluded.arguments_json,
                    lifecycle_state = excluded.lifecycle_state,
                    result_summary_json = excluded.result_summary_json,
                    failure_class = excluded.failure_class,
                    failure_reason = excluded.failure_reason,
                    approval_state = excluded.approval_state,
                    retryable = excluded.retryable,
                    retry_reason = excluded.retry_reason,
                    refresh_required = excluded.refresh_required,
                    refresh_reason = excluded.refresh_reason,
                    carried_forward_from_attempt_id = excluded.carried_forward_from_attempt_id,
                    carried_forward_from_generation = excluded.carried_forward_from_generation,
                    rerun_reason = excluded.rerun_reason,
                    previous_result_version = excluded.previous_result_version,
                    queued_at = COALESCE(excluded.queued_at, runtime_attempt_subtasks.queued_at),
                    started_at = COALESCE(excluded.started_at, runtime_attempt_subtasks.started_at),
                    completed_at = COALESCE(excluded.completed_at, runtime_attempt_subtasks.completed_at),
                    retrieved_at = COALESCE(excluded.retrieved_at, runtime_attempt_subtasks.retrieved_at),
                    result_version = excluded.result_version,
                    updated_at = excluded.updated_at
                """,
                (
                    str(attempt_id), str(subtask_id), int(execution_generation), str(plan_id or ""),
                    str(operation or ""), str(entity_type or ""), str(entity_key or ""),
                    _json_dumps(clean_arguments), str(lifecycle_state or "PLANNED"), _json_dumps(clean_result_summary),
                    str(failure_class or ""), clean_failure_reason, str(approval_state or ""),
                    int(bool(retryable)), str(retry_reason or "")[:500], int(bool(refresh_required)), str(refresh_reason or "")[:500],
                    str(carried_forward_from_attempt_id or ""), int(carried_forward_from_generation or 0),
                    str(rerun_reason or "")[:500], int(previous_result_version or 0),
                    queued_at, started_at, completed_at, retrieved_at, next_version, timestamp,
                ),
            )
            conn.commit()
        finally:
            conn.close()
    with _LOCK:
        conn = _conn()
        try:
            row = conn.execute(
                f"SELECT {_RUNTIME_ATTEMPT_SUBTASK_COLUMNS} FROM runtime_attempt_subtasks WHERE attempt_id = ? AND subtask_id = ? AND execution_generation = ?",
                (str(attempt_id), str(subtask_id), int(execution_generation)),
            ).fetchone()
        finally:
            conn.close()
    return _subtask_row_to_dict(row) if row else {}


def list_runtime_attempt_subtasks(attempt_id: str, *, execution_generation: int | None = None) -> list[dict[str, Any]]:
    clean_attempt_id = str(attempt_id or "").strip()
    if not clean_attempt_id:
        return []
    with _LOCK:
        conn = _conn()
        try:
            if execution_generation is None:
                rows = conn.execute(
                    f"SELECT {_RUNTIME_ATTEMPT_SUBTASK_COLUMNS} FROM runtime_attempt_subtasks WHERE attempt_id = ? ORDER BY subtask_id",
                    (clean_attempt_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    f"SELECT {_RUNTIME_ATTEMPT_SUBTASK_COLUMNS} FROM runtime_attempt_subtasks WHERE attempt_id = ? AND execution_generation = ? ORDER BY subtask_id",
                    (clean_attempt_id, int(execution_generation)),
                ).fetchall()
        finally:
            conn.close()
    return [_subtask_row_to_dict(row) for row in rows]


def chain_answer_bearing_attempts(root_attempt_id: str) -> list[dict[str, Any]]:
    """Every answer-bearing attempt on ONE chain, oldest first (ARCH-TRUTH-R1d).

    A turn answered by a plan has several children, one per planned request, and the turn
    is what a later follow-up refers to -- not whichever child a newest-row read happened
    to land on. This is the chain-level read that makes "what did I ask for?" answerable
    from the whole turn. The turn root is excluded for the same reason it ranks last in
    resolution: it is the chain's identity and the turn's outcome, not its work. Untyped
    legacy rows (role '') are included, so a database written before the role column reads
    exactly as it did.
    """
    clean_root = str(root_attempt_id or "").strip()
    if not clean_root:
        return []
    with _LOCK:
        conn = _conn()
        try:
            rows = conn.execute(
                f"""
                SELECT {_RUNTIME_ATTEMPT_COLUMNS} FROM runtime_attempts
                WHERE root_attempt_id = ? AND attempt_role != ?
                ORDER BY created_at ASC, attempt_id ASC
                """,
                (clean_root, AttemptRole.TURN_ROOT.value),
            ).fetchall()
        finally:
            conn.close()
    return [_attempt_row_to_dict(row) for row in rows or ()]


def list_chain_answer_subtasks(root_attempt_id: str) -> list[dict[str, Any]]:
    """The subtasks of every answer-bearing attempt on one chain, deterministically ordered.

    Each attempt contributes the subtasks of ITS OWN generation, so a retried child
    contributes the generation it actually ran rather than every generation it ever had.
    Order is the chain's own: attempts oldest first, subtasks by id within each -- the same
    answer on every read, which is what makes a recall reproducible.
    """
    aggregated: list[dict[str, Any]] = []
    for attempt in chain_answer_bearing_attempts(root_attempt_id):
        aggregated.extend(
            list_runtime_attempt_subtasks(
                str(attempt.get("attempt_id") or ""),
                execution_generation=int(attempt.get("execution_generation") or 1),
            )
        )
    return aggregated


def finalize_runtime_attempt(
    attempt_id: str,
    *,
    execution_generation: int = 1,
    plan_valid: bool = True,
    provider_available: bool = True,
    synthesis_ok: bool = True,
    client_turn_id: str = "",
) -> dict[str, Any] | None:
    """Reconcile the parent attempt's lifecycle from its persisted subtask rows and write the
    result -- the ONLY function that finalizes an attempt's lifecycle (Step 7 correction #8). Reads
    are a single query against `runtime_attempt_subtasks`, run after every worker has already
    returned (the caller runs this once, single-threaded, after its executor pool has closed), so
    there is no race between "reading subtask states" and "a subtask still being written."
    """
    subtasks = list_runtime_attempt_subtasks(attempt_id, execution_generation=execution_generation)
    subtask_states = [str(item.get("lifecycle_state") or "") for item in subtasks]
    lifecycle_state, terminal_reason = reconcile_attempt_lifecycle(
        subtask_states,
        plan_valid=plan_valid,
        provider_available=provider_available,
        synthesis_ok=synthesis_ok,
    )
    retryable, retry_reason = _default_retry_guidance(lifecycle_state)
    retry_from_stage = ""
    if lifecycle_state in (AttemptLifecycle.PARTIAL_SUCCESS.value, AttemptLifecycle.FAILED_TOOL.value):
        failing = [item for item in subtasks if item.get("lifecycle_state") in ("FAILED", "UNSUPPORTED_ENTITY")]
        retry_from_stage = ",".join(sorted({str(item.get("subtask_id") or "") for item in failing}))
    return update_runtime_attempt(
        attempt_id,
        lifecycle_state=lifecycle_state,
        terminal_reason=terminal_reason,
        retryable=retryable,
        retry_reason=retry_reason,
        retry_from_stage=retry_from_stage,
        completed=True,
        client_turn_id=client_turn_id,
    )


def mark_stale_runtime_attempts_abandoned(*, older_than_seconds: float = 0.0) -> int:
    """Close out attempts left non-terminal (RECEIVED/PLANNED/RUNNING) -- the attempt-layer
    counterpart to `mark_stale_runtime_checkpoints_interrupted`. `older_than_seconds=0` is the
    startup rule: the process that owned those rows is gone by definition. A restart-generated
    transition emits its own `runtime_attempt_abandoned` Activity event (Step 7 correction #9) so
    the transition itself is visible, not just its silent effect on the row.

    Repair 1 (Mnemosyne review, 2026-08-06): the original version inserted the Activity event using
    `runtime_sessions.event_count + 1` as `seq` but never wrote that new `seq` back to
    `runtime_sessions.event_count` -- the exact thing `mark_stale_runtime_checkpoints_interrupted`
    already does correctly via `_upsert_runtime_session(..., event_count=seq, ...)`. Two visible
    breaks followed: (a) `event_count` permanently understated `MAX(seq)`, so the NEXT
    `append_runtime_event` for that session recomputed the SAME `seq` and hit the
    `(session_id, seq)` primary key -- the session was left permanently 500ing; (b) two stale
    attempts in ONE session both read the same stale `event_count` and tried to insert the same
    `seq` within this very sweep, raising immediately and aborting the whole batch before its one
    trailing `conn.commit()` -- so neither attempt's ABANDONED transition was ever persisted,
    leaving both RUNNING forever. Fixed by adopting the checkpoint sweep's own pattern (write
    `event_count` back every time) plus two corrections the checkpoint sweep does not need at
    startup scale: a per-session local sequence cache (this sweep is the only writer touching these
    rows while it runs, single-threaded, under `_LOCK`, so caching avoids re-reading a value this
    same loop already advanced past) and a per-attempt try/except + per-attempt commit, so the
    attempt's lifecycle transition and its Activity event land in the SAME committed unit, and one
    malformed/failing row can never roll back an already-committed sibling or block the next
    session's row from being processed.

    Repair 6 (Mnemosyne review): the parent transitioning to ABANDONED left its own PLANNED/RUNNING
    subtask rows exactly as they were -- still PLANNED/RUNNING forever, with no result and no
    failure reason. Two consequences: the failure explanation omitted the interrupted subtask
    entirely (the renderer only looks at FAILED/UNSUPPORTED_ENTITY/SUCCEEDED rows), and a retry
    carried the untouched PLANNED/RUNNING row forward unchanged instead of rerunning it (carry-
    forward is for genuinely completed work, not work that never ran). Each such subtask is now
    transitioned to FAILED / failure_class=interrupted / retryable in the SAME per-attempt
    transaction as the parent's own ABANDONED transition, and the attempt's `retry_from_stage`
    names it -- so both the explanation and the retry path see it correctly.
    """
    cutoff = ""
    if older_than_seconds > 0:
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=float(older_than_seconds))).isoformat()
    nonterminal_states = (
        AttemptLifecycle.RECEIVED.value,
        AttemptLifecycle.PLANNED.value,
        AttemptLifecycle.RUNNING.value,
    )
    placeholders = ",".join("?" for _ in nonterminal_states)
    reason = (
        "Runtime stopped before the attempt finished."
        if not cutoff
        else f"Attempt made no progress for {int(older_than_seconds // 60)} minutes and was closed out."
    )
    process_id = _process_instance_id()
    with _LOCK:
        conn = _conn()
        try:
            try:
                rows = conn.execute(
                    f"""
                    SELECT attempt_id, session_id, execution_generation FROM runtime_attempts
                    WHERE lifecycle_state IN ({placeholders}) AND (? = '' OR updated_at < ?)
                    ORDER BY updated_at ASC
                    """,
                    (*nonterminal_states, cutoff, cutoff),
                ).fetchall()
            except Exception:
                # A sweep failure must never be the reason startup crashes -- durably reported via
                # the process log (already configured by setup_logging() before this runs), not a
                # raised exception that repeats on every restart.
                _log.error("runtime_attempt_sweep: failed to read stale attempts", exc_info=True)
                return 0
            count = 0
            session_seq_cache: dict[str, int] = {}
            session_started_cache: dict[str, str] = {}
            session_checkpoint_cache: dict[str, str] = {}
            for row in rows:
                attempt_id = str(row["attempt_id"])
                session_id = str(row["session_id"] or "")
                execution_generation = int(row["execution_generation"] or 1)
                try:
                    timestamp = _utcnow()
                    # Repair 6: interrupted subtasks first, so their ids are known before the
                    # parent's own UPDATE names them in retry_from_stage.
                    interrupted_rows = conn.execute(
                        """
                        SELECT subtask_id FROM runtime_attempt_subtasks
                        WHERE attempt_id = ? AND execution_generation = ? AND lifecycle_state IN ('PLANNED', 'RUNNING')
                        """,
                        (attempt_id, execution_generation),
                    ).fetchall()
                    interrupted_subtask_ids = sorted({str(r["subtask_id"]) for r in interrupted_rows})
                    if interrupted_subtask_ids:
                        conn.execute(
                            """
                            UPDATE runtime_attempt_subtasks
                            SET lifecycle_state = 'FAILED', failure_class = 'interrupted',
                                failure_reason = 'Runtime stopped before this subtask finished (interrupted by restart) -- no successful result was recorded.',
                                retryable = 1, retry_reason = 'interrupted by restart, will rerun on retry',
                                updated_at = ?
                            WHERE attempt_id = ? AND execution_generation = ? AND lifecycle_state IN ('PLANNED', 'RUNNING')
                            """,
                            (timestamp, attempt_id, execution_generation),
                        )
                    conn.execute(
                        """
                        UPDATE runtime_attempts
                        SET lifecycle_state = ?, terminal_reason = ?, retryable = 1, retry_reason = ?,
                            retry_from_stage = ?,
                            last_updated_daemon_sha = ?, updated_at = ?, completed_at = ?
                        WHERE attempt_id = ?
                        """,
                        (
                            AttemptLifecycle.ABANDONED.value, reason,
                            "attempt was interrupted, not user-cancelled",
                            ",".join(interrupted_subtask_ids),
                            _daemon_commit_sha(), timestamp, timestamp, attempt_id,
                        ),
                    )
                    if session_id:
                        if session_id not in session_seq_cache:
                            existing = conn.execute(
                                "SELECT event_count, started_at, last_checkpoint_id FROM runtime_sessions WHERE session_id = ? LIMIT 1",
                                (session_id,),
                            ).fetchone()
                            # Final Repair 2: self-heal a session already desynced by the earlier
                            # event_count bug -- seed the cache from max(event_count, MAX(persisted
                            # seq)), not event_count alone, so a damaged session's restart event
                            # does not recompute an already-taken seq.
                            session_seq_cache[session_id] = _next_session_seq(
                                conn, session_id, int(existing["event_count"] if existing else 0)
                            ) - 1
                            session_started_cache[session_id] = str(existing["started_at"] if existing else timestamp)
                            session_checkpoint_cache[session_id] = str(existing["last_checkpoint_id"] if existing and existing["last_checkpoint_id"] else "")
                        seq = session_seq_cache[session_id] + 1
                        details = {"attempt_id": attempt_id, "process_instance_id": process_id, "reason": reason}
                        conn.execute(
                            """
                            INSERT INTO runtime_session_events (
                                session_id, seq, event_type, message, details_json, created_at
                            ) VALUES (?, ?, 'runtime_attempt_abandoned', ?, ?, ?)
                            """,
                            (session_id, seq, reason, _json_dumps(details), timestamp),
                        )
                        # Repair 1: write event_count back in the SAME transaction as the event it
                        # counts -- the established mark_stale_runtime_checkpoints_interrupted
                        # pattern this sweep was missing.
                        _upsert_runtime_session(
                            conn,
                            session_id=session_id,
                            request_preview="",
                            task_class="",
                            status="",
                            last_event_type="runtime_attempt_abandoned",
                            last_message=reason,
                            event_count=seq,
                            started_at=session_started_cache[session_id],
                            updated_at=timestamp,
                            checkpoint_id=session_checkpoint_cache[session_id] or None,
                        )
                        session_seq_cache[session_id] = seq
                    # Attempt transition + Activity event committed as one unit, per attempt -- a
                    # later row's failure can never undo an earlier row's already-committed work.
                    conn.commit()
                    count += 1
                except Exception:
                    _log.error(
                        "runtime_attempt_sweep: failed to abandon attempt %s (session %s)",
                        attempt_id, session_id, exc_info=True,
                    )
                    with contextlib.suppress(Exception):
                        conn.rollback()
                    continue
            return count
        finally:
            conn.close()


def record_attempt_approval(
    *,
    attempt_id: str,
    action_digest: str,
    plan_digest: str,
    policy_version: str,
    requested_scope: str,
    decision: str,
    subtask_id: str = "",
    deciding_turn_id: str = "",
    expires_at: str | None = None,
) -> dict[str, Any]:
    """One durable approval decision, valid ONLY for the exact matching action/plan digest (Step 7
    correction #7). The in-memory maps in core.mode_permission_policy are not authoritative after
    a restart; a changed plan, changed arguments, or a changed policy version must never silently
    reuse an approval recorded against a different digest -- callers must recompute and compare
    digests before treating a prior approval as still valid, this function only records the
    decision, it does not itself decide validity.
    """
    approval_id = f"approval-{uuid.uuid4().hex}"
    timestamp = _utcnow()
    with _LOCK:
        conn = _conn()
        try:
            conn.execute(
                """
                INSERT INTO runtime_attempt_approvals (
                    approval_id, attempt_id, subtask_id, action_digest, plan_digest, policy_version,
                    requested_scope, decision, deciding_turn_id, created_at, decided_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    approval_id, str(attempt_id), str(subtask_id or ""), str(action_digest or ""),
                    str(plan_digest or ""), str(policy_version or ""), str(requested_scope or ""),
                    str(decision or ""), str(deciding_turn_id or ""), timestamp, timestamp, expires_at,
                ),
            )
            conn.commit()
        finally:
            conn.close()
    return {
        "approval_id": approval_id, "attempt_id": str(attempt_id), "subtask_id": str(subtask_id or ""),
        "action_digest": str(action_digest or ""), "plan_digest": str(plan_digest or ""),
        "policy_version": str(policy_version or ""), "requested_scope": str(requested_scope or ""),
        "decision": str(decision or ""), "deciding_turn_id": str(deciding_turn_id or ""),
        "created_at": timestamp, "decided_at": timestamp, "expires_at": expires_at,
    }


def latest_attempt_approval_for_subtask(attempt_id: str, subtask_id: str) -> dict[str, Any] | None:
    """Repair 8 (Mnemosyne review): the read side of the approval table a retry needs to consult
    -- the most recent decision recorded against this exact (attempt_id, subtask_id), or None. A
    restart never silently grants: this returns exactly what was durably recorded, nothing inferred
    from absence. Whether that decision is still USABLE (matching digest, not expired, not a
    denial) is `is_approval_valid`'s job, not this function's -- this only reads."""
    clean_attempt_id = str(attempt_id or "").strip()
    clean_subtask_id = str(subtask_id or "").strip()
    if not clean_attempt_id or not clean_subtask_id:
        return None
    with _LOCK:
        conn = _conn()
        try:
            row = conn.execute(
                """
                SELECT approval_id, attempt_id, subtask_id, action_digest, plan_digest, policy_version,
                       requested_scope, decision, deciding_turn_id, created_at, decided_at, expires_at
                FROM runtime_attempt_approvals
                WHERE attempt_id = ? AND subtask_id = ?
                ORDER BY decided_at DESC, created_at DESC
                LIMIT 1
                """,
                (clean_attempt_id, clean_subtask_id),
            ).fetchone()
        finally:
            conn.close()
    return dict(row) if row else None


def record_followup_resolution(
    *,
    session_id: str,
    follow_up_turn_id: str = "",
    resolved_attempt_id: str = "",
    resolution_intent: str = "",
    resolution_reason: str = "",
    resolution_confidence: float = 0.0,
    fallback_used: bool = False,
) -> dict[str, Any]:
    """One durable row per resolved (or explicitly unresolved -- `resolved_attempt_id=""`) short
    follow-up (Step 9). Also emits `runtime_follow_up_resolved`/`runtime_follow_up_unresolved` into
    the same Activity ledger every other runtime event lands in.
    """
    resolution_id = f"followup-{uuid.uuid4().hex}"
    timestamp = _utcnow()
    clean_session_id = str(session_id or "").strip()
    with _LOCK:
        conn = _conn()
        try:
            conn.execute(
                """
                INSERT INTO runtime_followup_resolutions (
                    resolution_id, session_id, follow_up_turn_id, resolved_attempt_id,
                    resolution_intent, resolution_reason, resolution_confidence, fallback_used, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    resolution_id, clean_session_id, str(follow_up_turn_id or ""), str(resolved_attempt_id or ""),
                    str(resolution_intent or ""), str(resolution_reason or ""), float(resolution_confidence),
                    int(bool(fallback_used)), timestamp,
                ),
            )
            conn.commit()
        finally:
            conn.close()
    if clean_session_id:
        try:
            event_type = "runtime_follow_up_resolved" if resolved_attempt_id else "runtime_follow_up_unresolved"
            append_runtime_event(
                session_id=clean_session_id,
                event_type=event_type,
                message=f"Follow-up resolved as {resolution_intent or 'unknown'}: {resolution_reason}",
                details={
                    "resolution_id": resolution_id, "resolved_attempt_id": str(resolved_attempt_id or ""),
                    "resolution_intent": str(resolution_intent or ""), "fallback_used": bool(fallback_used),
                },
            )
        except Exception:
            pass
    return {
        "resolution_id": resolution_id, "session_id": clean_session_id,
        "follow_up_turn_id": str(follow_up_turn_id or ""), "resolved_attempt_id": str(resolved_attempt_id or ""),
        "resolution_intent": str(resolution_intent or ""), "resolution_reason": str(resolution_reason or ""),
        "resolution_confidence": float(resolution_confidence), "fallback_used": bool(fallback_used),
        "created_at": timestamp,
    }


_QUEUE_ACTIVE_STATUSES = ("pending", "in_flight")


def _queue_row_to_dict(row: Any) -> dict[str, Any]:
    data = dict(row)
    data["payload"] = _json_loads(data.pop("payload_json", "{}"), fallback={})
    return data


def get_queue_item(queue_item_id: str) -> dict[str, Any] | None:
    clean = str(queue_item_id or "").strip()
    if not clean:
        return None
    with _LOCK:
        conn = _conn()
        try:
            row = conn.execute("SELECT * FROM message_queue WHERE queue_item_id = ? LIMIT 1", (clean,)).fetchone()
        finally:
            conn.close()
    return _queue_row_to_dict(row) if row else None


def enqueue_message(*, session_id: str, payload: dict[str, Any], idempotency_key: str = "") -> dict[str, Any]:
    """Append a message to a session's queue. Idempotent per (session_id, idempotency_key): a
    repeat submit (double-click, reconnect replay) returns the EXISTING item, never a duplicate.
    The queued text is secret-scrubbed before it is persisted."""
    from core.secret_redaction import redact_secrets

    clean_session = str(session_id or "").strip()
    idem = str(idempotency_key or "").strip()
    safe_payload = dict(payload or {})
    if "text" in safe_payload:
        safe_payload["text"] = redact_secrets(str(safe_payload.get("text") or ""))
    with _LOCK:
        conn = _conn()
        try:
            if idem:
                existing = conn.execute(
                    "SELECT * FROM message_queue WHERE session_id = ? AND idempotency_key = ? LIMIT 1",
                    (clean_session, idem),
                ).fetchone()
                if existing:
                    return _queue_row_to_dict(existing)
            seq_row = conn.execute("SELECT COALESCE(MAX(seq), 0) AS m FROM message_queue WHERE session_id = ?", (clean_session,)).fetchone()
            seq = int(seq_row["m"] if seq_row else 0) + 1
            queue_item_id = f"msg-{uuid.uuid4().hex}"
            now = _utcnow()
            try:
                conn.execute(
                    "INSERT INTO message_queue "
                    "(queue_item_id, session_id, seq, status, payload_json, idempotency_key, turn_id, lease_owner, created_at, updated_at, completed_at) "
                    "VALUES (?, ?, ?, 'pending', ?, ?, '', '', ?, ?, NULL)",
                    (queue_item_id, clean_session, seq, _json_dumps(safe_payload), idem, now, now),
                )
                conn.commit()
            except Exception:
                # Unique-index backstop: a concurrent enqueue with the same idem key won the race.
                conn.rollback()
                if idem:
                    existing = conn.execute("SELECT * FROM message_queue WHERE session_id = ? AND idempotency_key = ? LIMIT 1", (clean_session, idem)).fetchone()
                    if existing:
                        return _queue_row_to_dict(existing)
                raise
            return _queue_row_to_dict(conn.execute("SELECT * FROM message_queue WHERE queue_item_id = ?", (queue_item_id,)).fetchone())
        finally:
            conn.close()


def list_queued_messages(session_id: str, *, statuses: tuple[str, ...] = _QUEUE_ACTIVE_STATUSES) -> list[dict[str, Any]]:
    clean = str(session_id or "").strip()
    if not clean or not statuses:
        return []
    placeholders = ",".join("?" for _ in statuses)
    with _LOCK:
        conn = _conn()
        try:
            rows = conn.execute(
                f"SELECT * FROM message_queue WHERE session_id = ? AND status IN ({placeholders}) ORDER BY seq ASC",
                (clean, *statuses),
            ).fetchall()
        finally:
            conn.close()
    return [_queue_row_to_dict(row) for row in rows]


def claim_next_message(session_id: str, *, lease_owner: str = "", turn_id: str = "", expected_queue_item_id: str = "") -> dict[str, Any] | None:
    """Atomically claim the oldest pending message for a session (pending -> in_flight). The
    compare-and-swap UPDATE (WHERE status='pending') guarantees exactly-once dispatch: two
    racing claimers (double pump, two tabs, reconnect) cannot both win the same item."""
    clean = str(session_id or "").strip()
    if not clean:
        return None
    with _LOCK:
        conn = _conn()
        try:
            row = conn.execute(
                "SELECT queue_item_id FROM message_queue WHERE session_id = ? AND status = 'pending' ORDER BY seq ASC LIMIT 1",
                (clean,),
            ).fetchone()
            if not row:
                return None
            qid = str(row["queue_item_id"])
            if expected_queue_item_id and qid != expected_queue_item_id:
                return None
            now = _utcnow()
            cur = conn.execute(
                "UPDATE message_queue SET status = 'in_flight', lease_owner = ?, turn_id = ?, updated_at = ? WHERE queue_item_id = ? AND status = 'pending'",
                (str(lease_owner or ""), str(turn_id or ""), now, qid),
            )
            conn.commit()
            if cur.rowcount != 1:
                return None  # lost the race; another claimer took it
            return _queue_row_to_dict(conn.execute("SELECT * FROM message_queue WHERE queue_item_id = ?", (qid,)).fetchone())
        finally:
            conn.close()


def complete_queue_item(queue_item_id: str, *, status: str = "completed") -> dict[str, Any] | None:
    clean = str(queue_item_id or "").strip()
    valid = status if status in {"completed", "failed", "cancelled"} else "completed"
    with _LOCK:
        conn = _conn()
        try:
            now = _utcnow()
            conn.execute("UPDATE message_queue SET status = ?, updated_at = ?, completed_at = ? WHERE queue_item_id = ?", (valid, now, now, clean))
            conn.commit()
        finally:
            conn.close()
    return get_queue_item(clean)


def cancel_queue_item(queue_item_id: str, *, session_id: str = "") -> bool:
    """Cancel a queued message. Only a still-PENDING item can be cancelled -- an in-flight turn
    keeps running. Returns True if this call cancelled it."""
    clean = str(queue_item_id or "").strip()
    now = _utcnow()
    with _LOCK:
        conn = _conn()
        try:
            if session_id:
                cur = conn.execute(
                    "UPDATE message_queue SET status = 'cancelled', updated_at = ?, completed_at = ? WHERE queue_item_id = ? AND session_id = ? AND status = 'pending'",
                    (now, now, clean, str(session_id)),
                )
            else:
                cur = conn.execute(
                    "UPDATE message_queue SET status = 'cancelled', updated_at = ?, completed_at = ? WHERE queue_item_id = ? AND status = 'pending'",
                    (now, now, clean),
                )
            conn.commit()
            return int(cur.rowcount or 0) == 1
        finally:
            conn.close()


def recover_stale_queue_items() -> int:
    """Startup recovery: an in_flight message is from an interrupted turn (crash/kill). Reset it
    to pending so it is not lost. A clean reconnect leaves pending items untouched, so nothing is
    re-run twice; only a genuinely interrupted turn is retried. Returns the count reset."""
    with _LOCK:
        conn = _conn()
        try:
            cur = conn.execute("UPDATE message_queue SET status = 'pending', lease_owner = '', updated_at = ? WHERE status = 'in_flight'", (_utcnow(),))
            conn.commit()
            return int(cur.rowcount or 0)
        finally:
            conn.close()


def remember_machine_read(session_id: str, *, kind: str, drive: str, source_turn_id: str = "") -> None:
    """Persist the last grounded machine read for a session so an elliptical follow-up survives a
    server restart. Upsert keyed by session_id. Best-effort: never raises into the turn path."""
    normalized = str(session_id or "").strip()
    if not normalized:
        return
    try:
        with _LOCK:
            conn = _conn()
            try:
                conn.execute(
                    """
                    INSERT INTO machine_read_memory (session_id, entity_type, kind, drive, source_turn_id, updated_at)
                    VALUES (?, 'drive', ?, ?, ?, ?)
                    ON CONFLICT(session_id) DO UPDATE SET
                        kind = excluded.kind,
                        drive = excluded.drive,
                        source_turn_id = excluded.source_turn_id,
                        updated_at = excluded.updated_at
                    """,
                    (normalized, str(kind or ""), str(drive or ""), str(source_turn_id or ""), _utcnow()),
                )
                conn.commit()
            finally:
                conn.close()
    except Exception:
        return


def recall_machine_read(session_id: str) -> dict[str, str] | None:
    """The last grounded machine read for a session (kind/drive/source_turn_id), or None. Reads the
    persisted row so a follow-up works across a restart. Best-effort: returns None on any error."""
    normalized = str(session_id or "").strip()
    if not normalized:
        return None
    try:
        with _LOCK:
            conn = _conn()
            try:
                row = conn.execute(
                    "SELECT kind, drive, source_turn_id FROM machine_read_memory WHERE session_id = ? LIMIT 1",
                    (normalized,),
                ).fetchone()
            finally:
                conn.close()
    except Exception:
        return None
    if not row:
        return None
    return {
        "kind": str(row["kind"] or ""),
        "drive": str(row["drive"] or ""),
        "source_turn_id": str(row["source_turn_id"] or ""),
    }


#: How many entities one obligation may accumulate. A conversation that has walked through more
#: cities than this has stopped being a follow-up chain, and an aggregate over all of them would be
#: a worse answer than the most recent binding.
_OBLIGATION_SLOT_LIMIT = 8
#: How many raw user texts to remember as "already absorbed by this obligation". Only needs to cover
#: the walk-back window the resolver uses.
_OBLIGATION_ABSORBED_LIMIT = 12


def remember_live_data_obligation(
    session_id: str,
    *,
    operation: str,
    slots: list[str] | tuple[str, ...],
    request_text: str,
    absorbed_text: str = "",
    source_turn_id: str = "",
) -> None:
    """Record the live-data obligation this turn just fulfilled, keyed by session.

    Slots ACCUMULATE while the operation stays the same -- that is what lets "which one is warmer?"
    reach both cities rather than only the one the last turn named. A different operation replaces
    the record outright: a conversation that moved from weather to prices has a new obligation, and
    carrying the old slots forward would be exactly the stale-intent replay this record exists to
    prevent.

    Upsert keyed by session_id, best-effort, never raises into the turn path -- the same contract as
    `remember_machine_read`, whose failure mode (a follow-up that silently loses its referent) is
    strictly better than a turn that dies recording state about itself.
    """
    normalized = str(session_id or "").strip()
    operation_name = str(operation or "").strip()
    if not normalized or not operation_name:
        return
    incoming = [str(slot).strip() for slot in (slots or ()) if str(slot).strip()]
    if not incoming:
        return
    try:
        existing = recall_live_data_obligation(normalized) or {}
        kept_slots: list[str] = []
        kept_absorbed: list[str] = []
        if str(existing.get("operation") or "") == operation_name:
            kept_slots = [str(item) for item in (existing.get("slots") or [])]
            kept_absorbed = [str(item) for item in (existing.get("absorbed") or [])]
        merged: list[str] = []
        for slot in [*kept_slots, *incoming]:
            if slot.casefold() not in {item.casefold() for item in merged}:
                merged.append(slot)
        merged = merged[-_OBLIGATION_SLOT_LIMIT:]
        absorbed = [*kept_absorbed]
        normalized_absorbed = " ".join(str(absorbed_text or "").split()).strip()
        if normalized_absorbed and normalized_absorbed not in absorbed:
            absorbed.append(normalized_absorbed)
        absorbed = absorbed[-_OBLIGATION_ABSORBED_LIMIT:]
        with _LOCK:
            conn = _conn()
            try:
                conn.execute(
                    """
                    INSERT INTO live_data_obligation_memory
                        (session_id, operation, slots_json, request_text, absorbed_json,
                         source_turn_id, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(session_id) DO UPDATE SET
                        operation = excluded.operation,
                        slots_json = excluded.slots_json,
                        request_text = excluded.request_text,
                        absorbed_json = excluded.absorbed_json,
                        source_turn_id = excluded.source_turn_id,
                        updated_at = excluded.updated_at
                    """,
                    (
                        normalized,
                        operation_name,
                        json.dumps(merged),
                        str(request_text or "").strip(),
                        json.dumps(absorbed),
                        str(source_turn_id or ""),
                        _utcnow(),
                    ),
                )
                conn.commit()
            finally:
                conn.close()
    except Exception:
        return


def recall_live_data_obligation(session_id: str) -> dict[str, Any] | None:
    """The live-data obligation on the table for this session, or None.

    Reads the persisted row so a follow-up survives a restart, exactly like `recall_machine_read`.
    Best-effort: returns None on any error.
    """
    normalized = str(session_id or "").strip()
    if not normalized:
        return None
    try:
        with _LOCK:
            conn = _conn()
            try:
                row = conn.execute(
                    "SELECT operation, slots_json, request_text, absorbed_json, source_turn_id "
                    "FROM live_data_obligation_memory WHERE session_id = ? LIMIT 1",
                    (normalized,),
                ).fetchone()
            finally:
                conn.close()
    except Exception:
        return None
    if not row:
        return None

    def _load(raw: Any) -> list[str]:
        try:
            parsed = json.loads(str(raw or "[]"))
        except Exception:
            return []
        return [str(item) for item in parsed if str(item).strip()] if isinstance(parsed, list) else []

    return {
        "operation": str(row["operation"] or ""),
        "slots": _load(row["slots_json"]),
        "request_text": str(row["request_text"] or ""),
        "absorbed": _load(row["absorbed_json"]),
        "source_turn_id": str(row["source_turn_id"] or ""),
    }


# How long a checkpoint may sit at `running` without being touched before it is treated as dead.
# Only used by the in-process sweep; startup keeps its own rule (see below).
STALE_CHECKPOINT_SECONDS = 900.0
# The sweep is one indexed SELECT, but a turn should not pay for it on every message.
_SWEEP_INTERVAL_SECONDS = 60.0
_last_sweep_monotonic = 0.0


def sweep_stale_checkpoints_if_due(*, now_monotonic: float | None = None) -> int:
    """Run the deadline sweep at most once a minute. Safe to call at the start of every turn.

    Startup was the ONLY place the sweep ran, so a checkpoint that wedged after startup stayed
    `running` until the process died. Calling this per turn means a wedged task clears on the
    operator's next message rather than on their next restart.
    """

    global _last_sweep_monotonic
    import time as _time

    now = float(now_monotonic if now_monotonic is not None else _time.monotonic())
    if _last_sweep_monotonic and (now - _last_sweep_monotonic) < _SWEEP_INTERVAL_SECONDS:
        return 0
    _last_sweep_monotonic = now
    try:
        return mark_stale_runtime_checkpoints_interrupted(
            older_than_seconds=STALE_CHECKPOINT_SECONDS
        )
    except Exception:
        # A housekeeping sweep must never be the reason a turn fails.
        return 0


def _live_checkpoint_ids() -> frozenset[str]:
    """Checkpoints with a worker executing right now, from the live-turn registry.

    Fails OPEN (empty set) if the registry cannot be read: the sweep's job is housekeeping, and a
    housekeeping helper must never be the reason a turn fails. The cost of failing open is the old
    behaviour for that one sweep, not a crash.
    """
    try:
        from core.live_turns import live_checkpoint_ids

        return live_checkpoint_ids()
    except Exception:
        return frozenset()


def mark_stale_runtime_checkpoints_interrupted(*, older_than_seconds: float = 0.0) -> int:
    """Close out checkpoints left at `running`.

    `older_than_seconds=0` is the startup rule and means "every one of them": the process that owned
    those rows is gone by definition, so none of them can still be progressing.

    A positive value is the IN-PROCESS rule, and it is what this function was missing. It had no
    time predicate at all and ran once, at process start (`apps/vool_agent.py`), so a task that
    wedged after startup — a build stuck in a 300s generation, a tool loop that never returned —
    left a `running` row with zero steps that survived for the entire life of the process. Nothing
    swept it, and the operator saw an in-flight task that would never move again. The deadline is
    checked against `updated_at`, so a checkpoint that is genuinely making progress keeps resetting
    it and is never touched.
    """

    cutoff = ""
    if older_than_seconds > 0:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(seconds=float(older_than_seconds))
        ).isoformat()

    # Checkpoints whose worker is STILL EXECUTING in this process. Quiet is not the same as gone: a
    # turn can spend well past the deadline inside one model call or one long tool without writing
    # progress, and closing it out would both lie about it and offer the operator a Resume that runs
    # the work a second time alongside the copy still going. `older_than_seconds=0` is the STARTUP
    # rule -- the process that owned those rows is gone by definition, so nothing can be live and
    # this set is empty then anyway. Raising the deadline would not fix this; it would only move it.
    live_checkpoints = _live_checkpoint_ids()

    with _LOCK:
        conn = _conn()
        try:
            # ISO-8601 UTC strings from `_utcnow()` sort lexicographically in timestamp order, so a
            # string comparison is a time comparison here.
            rows = conn.execute(
                """
                SELECT checkpoint_id, session_id, request_text, task_class
                FROM runtime_checkpoints
                WHERE status = 'running' AND (? = '' OR updated_at < ?)
                ORDER BY updated_at ASC
                """,
                (cutoff, cutoff),
            ).fetchall()
            count = 0
            for row in rows:
                checkpoint_id = str(row["checkpoint_id"])
                if checkpoint_id in live_checkpoints:
                    continue
                timestamp = _utcnow()
                conn.execute(
                    """
                    UPDATE runtime_checkpoints
                    SET status = 'interrupted',
                        failure_text = ?,
                        updated_at = ?
                    WHERE checkpoint_id = ?
                    """,
                    (
                        "Runtime stopped before the task finished."
                        if not cutoff
                        else (
                            "Task made no progress for "
                            f"{int(older_than_seconds // 60)} minutes and was closed out."
                        ),
                        timestamp,
                        checkpoint_id,
                    ),
                )
                session_id = str(row["session_id"] or "")
                existing = conn.execute(
                    "SELECT event_count, started_at, request_preview, last_checkpoint_id FROM runtime_sessions WHERE session_id = ? LIMIT 1",
                    (session_id,),
                ).fetchone()
                seq = int(existing["event_count"] if existing else 0) + 1
                details = {
                    "checkpoint_id": checkpoint_id,
                    "request_preview": _preview_text(str(row["request_text"] or "")),
                    "task_class": str(row["task_class"] or "").strip(),
                    "resume_available": True,
                }
                conn.execute(
                    """
                    INSERT INTO runtime_session_events (
                        session_id, seq, event_type, message, details_json, created_at
                    ) VALUES (?, ?, 'task_interrupted', ?, ?, ?)
                    """,
                    (
                        session_id,
                        seq,
                        "Previous runtime stopped before completion. Resume is available.",
                        _json_dumps(details),
                        timestamp,
                    ),
                )
                _upsert_runtime_session(
                    conn,
                    session_id=session_id,
                    request_preview=details["request_preview"],
                    task_class=details["task_class"],
                    status="interrupted",
                    last_event_type="task_interrupted",
                    last_message="Previous runtime stopped before completion. Resume is available.",
                    event_count=seq,
                    started_at=str(existing["started_at"] if existing else timestamp),
                    updated_at=timestamp,
                    checkpoint_id=str(existing["last_checkpoint_id"] if existing and existing["last_checkpoint_id"] else checkpoint_id),
                )
                count += 1
            conn.commit()
            return count
        finally:
            conn.close()


def build_tool_receipt_key(
    *,
    checkpoint_id: str,
    step_index: int,
    intent: str,
    arguments: dict[str, Any] | None,
) -> str:
    payload = {
        "checkpoint_id": str(checkpoint_id or "").strip(),
        "step_index": max(0, int(step_index)),
        "intent": str(intent or "").strip(),
        "arguments": _normalize_for_hash(arguments or {}),
    }
    digest = hashlib.sha256(_json_dumps(payload).encode("utf-8")).hexdigest()
    return f"receipt-{digest}"


def is_mutating_tool_intent(intent: str) -> bool:
    return str(intent or "").strip() in _MUTATING_TOOL_INTENTS


def load_tool_receipt(receipt_key: str) -> dict[str, Any] | None:
    clean_key = str(receipt_key or "").strip()
    if not clean_key:
        return None
    with _LOCK:
        conn = _conn()
        try:
            row = conn.execute(
                """
                SELECT receipt_key, session_id, checkpoint_id, tool_name, idempotency_key,
                       arguments_json, execution_json, created_at, updated_at
                FROM runtime_tool_receipts
                WHERE receipt_key = ?
                LIMIT 1
                """,
                (clean_key,),
            ).fetchone()
        finally:
            conn.close()
    if not row:
        return None
    data = dict(row)
    data["arguments"] = _json_loads(data.pop("arguments_json"), fallback={})
    data["execution"] = _json_loads(data.pop("execution_json"), fallback={})
    return data


def store_tool_receipt(
    *,
    receipt_key: str,
    session_id: str,
    checkpoint_id: str,
    tool_name: str,
    idempotency_key: str,
    arguments: dict[str, Any] | None,
    execution: dict[str, Any] | None,
) -> dict[str, Any]:
    from core.agent_runtime.orchestrator import redact_tool_arguments

    clean_receipt_key = str(receipt_key or "").strip()
    if not clean_receipt_key:
        raise ValueError("receipt_key is required")
    safe_arguments = _scrub_persisted(redact_tool_arguments(dict(arguments or {})))
    safe_execution = _scrub_persisted(redact_tool_arguments(dict(execution or {})))
    if not isinstance(safe_arguments, dict):
        safe_arguments = {}
    if not isinstance(safe_execution, dict):
        safe_execution = {}
    timestamp = _utcnow()
    payload = {
        "receipt_key": clean_receipt_key,
        "session_id": str(session_id or "").strip(),
        "checkpoint_id": str(checkpoint_id or "").strip(),
        "tool_name": str(tool_name or "").strip(),
        "idempotency_key": str(idempotency_key or "").strip(),
        "arguments": safe_arguments,
        "execution": safe_execution,
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    with _LOCK:
        conn = _conn()
        try:
            conn.execute(
                """
                INSERT INTO runtime_tool_receipts (
                    receipt_key, session_id, checkpoint_id, tool_name, idempotency_key,
                    arguments_json, execution_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(receipt_key) DO UPDATE SET
                    execution_json = excluded.execution_json,
                    updated_at = excluded.updated_at
                """,
                (
                    clean_receipt_key,
                    payload["session_id"],
                    payload["checkpoint_id"],
                    payload["tool_name"],
                    payload["idempotency_key"],
                    _json_dumps(payload["arguments"]),
                    _json_dumps(payload["execution"]),
                    timestamp,
                    timestamp,
                ),
            )
            conn.commit()
        finally:
            conn.close()
    return payload


# ─────────────────────────────────────────────────────────────────────────────
# A6 effect reconciliation store (assembly W001).
#
# Owns the cross-turn truth state of one LOGICAL external effect: did an
# authorized effect actually happen? UNKNOWN is a durable first-class state and
# never collapses into FAILED. Identity is checkpoint-free by construction --
# `compute_logical_effect_id` hashes ONLY the intent and its canonically
# normalized typed arguments, so a fresh user turn retrying the same real-world
# mutation derives the SAME logical effect id (and a per-attempt
# effect_instance_id distinguishes retry from intentional repeat).
# ─────────────────────────────────────────────────────────────────────────────

_UNRESOLVED_ACTIVE_STATES = ("prepared", "dispatched", "unknown")
_UNRESOLVED_TERMINAL_APPLIED = "applied"
_UNRESOLVED_TERMINAL_FAILED = "failed_safe_to_retry"
_UNRESOLVED_STATE_UNKNOWN = "unknown"
_UNRESOLVED_STATE_PREPARED = "prepared"
_UNRESOLVED_STATE_DISPATCHED = "dispatched"
_UNRESOLVED_SUPERSEDED = "superseded"
_UNRESOLVED_EXPIRED_PRE_DISPATCH = "expired_pre_dispatch"

# How long a prepared/dispatched lease may sit before restart-style recovery
# reclassifies it. Generous: any real dispatch classifies within seconds; the
# sweep exists for crashed processes, not slow ones.
_UNRESOLVED_LEASE_SECONDS = 900

_VALID_RESOLUTION_SOURCES = frozenset({"mechanical", "provider", "user"})
_VALID_RESOLUTIONS = frozenset(
    {"CONFIRMED_APPLIED", "CONFIRMED_FAILED_SAFE_TO_RETRY", "SUPERSEDED_NEW_INSTANCE"}
)


def compute_logical_effect_id(*, intent: str, arguments: dict[str, Any] | None) -> str:
    """Checkpoint-independent identity of one intended real-world mutation.

    `build_tool_receipt_key` minus checkpoint_id/step_index: same intended
    mutation -> same id across retries, turns and checkpoints. Prompt text,
    model wording, turn/checkpoint/attempt ids are deliberately NOT inputs.
    """
    payload = {
        "intent": str(intent or "").strip(),
        "arguments": _normalize_for_hash(dict(arguments or {})),
    }
    digest = hashlib.sha256(_json_dumps(payload).encode("utf-8")).hexdigest()
    return f"lef-{digest}"


def _unresolved_row_to_dict(row: Any) -> dict[str, Any]:
    data = dict(row)
    data["expected_evidence"] = _json_loads(data.pop("expected_evidence_json", "{}"), fallback={})
    return data


def find_active_unresolved_effect(logical_effect_id: str) -> dict[str, Any] | None:
    """The active (prepared/dispatched/unknown) row for this logical effect, if any."""
    clean = str(logical_effect_id or "").strip()
    if not clean:
        return None
    placeholders = ", ".join("?" for _ in _UNRESOLVED_ACTIVE_STATES)
    with _LOCK:
        conn = _conn()
        try:
            row = conn.execute(
                f"""
                SELECT * FROM runtime_unresolved_effects
                WHERE logical_effect_id = ? AND state IN ({placeholders})
                ORDER BY created_at DESC LIMIT 1
                """,
                (clean, *_UNRESOLVED_ACTIVE_STATES),
            ).fetchone()
        finally:
            conn.close()
    return _unresolved_row_to_dict(row) if row else None


def list_active_unresolved_effects(limit: int = 100) -> list[dict[str, Any]]:
    """Every active (prepared/dispatched/unknown) unresolved effect, newest first.

    The A6 surface (NIA-006): until now the store only answered id-keyed lookups,
    so an UNKNOWN in a send-message or payment intent blocked forever with the
    reason buried in receipts — nothing could even LIST the stuck rows.
    """
    cap = max(1, min(int(limit), 500))
    placeholders = ", ".join("?" for _ in _UNRESOLVED_ACTIVE_STATES)
    with _LOCK:
        conn = _conn()
        try:
            rows = conn.execute(
                f"""
                SELECT * FROM runtime_unresolved_effects
                WHERE state IN ({placeholders})
                ORDER BY updated_at DESC LIMIT ?
                """,
                (*_UNRESOLVED_ACTIVE_STATES, cap),
            ).fetchall()
        finally:
            conn.close()
    return [_unresolved_row_to_dict(row) for row in rows]


def get_unresolved_effect(logical_effect_id: str) -> dict[str, Any] | None:
    """Latest row for this logical effect in ANY state (active or terminal)."""
    clean = str(logical_effect_id or "").strip()
    if not clean:
        return None
    with _LOCK:
        conn = _conn()
        try:
            row = conn.execute(
                """
                SELECT * FROM runtime_unresolved_effects
                WHERE logical_effect_id = ?
                ORDER BY created_at DESC, updated_at DESC LIMIT 1
                """,
                (clean,),
            ).fetchone()
        finally:
            conn.close()
    return _unresolved_row_to_dict(row) if row else None


def list_unresolved_effect_resolutions(logical_effect_id: str) -> list[dict[str, Any]]:
    clean = str(logical_effect_id or "").strip()
    if not clean:
        return []
    with _LOCK:
        conn = _conn()
        try:
            rows = conn.execute(
                """
                SELECT * FROM unresolved_effect_resolutions
                WHERE logical_effect_id = ? ORDER BY created_at ASC, resolution_id ASC
                """,
                (clean,),
            ).fetchall()
        finally:
            conn.close()
    return [dict(row) for row in rows]


def _parse_iso_or_zero(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value or ""))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except Exception:
        return datetime(1970, 1, 1, tzinfo=timezone.utc)


def sweep_stale_unresolved_effects(*, max_age_seconds: int = _UNRESOLVED_LEASE_SECONDS) -> int:
    """Lazy crash-window recovery. Runs inside reserve's locked transaction.

    PREPARED rows whose lease expired never began dispatch -- no physical effect
    was possible (crash window C2), so they are released as expired_pre_dispatch
    and a fresh identical intent may mint a new instance. DISPATCHED rows whose
    lease expired crossed the dispatch boundary before dying (C3/C4): whether the
    physical effect landed is UNPROVABLE, so they become durable UNKNOWN and keep
    blocking until reconciled. Misclassifying a never-executed dispatch as
    UNKNOWN is acceptable; the reverse is forbidden.
    """
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(seconds=max(1, int(max_age_seconds)))).isoformat()
    changed = 0
    with _LOCK:
        conn = _conn()
        try:
            stale_prepared = conn.execute(
                """
                SELECT logical_effect_id FROM runtime_unresolved_effects
                WHERE state = ? AND created_at < ?
                """,
                (_UNRESOLVED_STATE_PREPARED, cutoff),
            ).fetchall()
            stale_dispatched = conn.execute(
                """
                SELECT logical_effect_id FROM runtime_unresolved_effects
                WHERE state = ? AND COALESCE(dispatched_at, created_at) < ?
                """,
                (_UNRESOLVED_STATE_DISPATCHED, cutoff),
            ).fetchall()
            timestamp = _utcnow()
            for row in stale_prepared:
                cursor = conn.execute(
                    """
                    UPDATE runtime_unresolved_effects
                    SET state = ?, reason = ?, updated_at = ?
                    WHERE logical_effect_id = ? AND state = ?
                    """,
                    (
                        _UNRESOLVED_EXPIRED_PRE_DISPATCH,
                        "lease_expiry_no_dispatch_began",
                        timestamp,
                        row["logical_effect_id"],
                        _UNRESOLVED_STATE_PREPARED,
                    ),
                )
                changed += cursor.rowcount
            for row in stale_dispatched:
                cursor = conn.execute(
                    """
                    UPDATE runtime_unresolved_effects
                    SET state = ?, reason = ?, updated_at = ?
                    WHERE logical_effect_id = ? AND state = ?
                    """,
                    (
                        _UNRESOLVED_STATE_UNKNOWN,
                        "crash_recovery_dispatch_lease_expired",
                        timestamp,
                        row["logical_effect_id"],
                        _UNRESOLVED_STATE_DISPATCHED,
                    ),
                )
                changed += cursor.rowcount
            if changed:
                conn.commit()
        finally:
            conn.close()
    return changed


def reserve_logical_effect(
    *,
    intent: str,
    arguments: dict[str, Any] | None,
    resource_identity: str = "",
    expected_evidence: dict[str, Any] | None = None,
    session_id: str = "",
    checkpoint_id: str = "",
    turn_id: str = "",
    receipt_key: str = "",
    semantic_result_id: str = "",
    attempt_id: str = "",
    reconcilability: str = "unknown",
) -> dict[str, Any]:
    """Durably reserve a logical effect BEFORE any physical side effect.

    BEGIN IMMEDIATE + active-row check + insert, backed by the partial unique
    index `ux_unresolved_active_effect`: at most ONE active instance per
    logical effect may exist, across threads and processes. Returns
    {"outcome": "reserved", ...} or {"outcome": "blocked", "row": ...}.
    """
    clean_intent = str(intent or "").strip()
    leid = compute_logical_effect_id(intent=clean_intent, arguments=arguments)
    timestamp = _utcnow()
    instance_id = f"eff-{uuid.uuid4().hex}"
    evidence_payload: dict[str, Any]
    if isinstance(expected_evidence, dict):
        evidence_payload = expected_evidence
    else:
        evidence_payload = {}
    with _LOCK:
        conn = _conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            # Lazy crash-window recovery first, so a crashed process's stale
            # leases are classified before this reservation decides.
            cutoff = (
                datetime.now(timezone.utc) - timedelta(seconds=_UNRESOLVED_LEASE_SECONDS)
            ).isoformat()
            conn.execute(
                """
                UPDATE runtime_unresolved_effects
                SET state = ?, reason = ?, updated_at = ?
                WHERE state = ? AND created_at < ?
                """,
                (
                    _UNRESOLVED_EXPIRED_PRE_DISPATCH,
                    "lease_expiry_no_dispatch_began",
                    timestamp,
                    _UNRESOLVED_STATE_PREPARED,
                    cutoff,
                ),
            )
            conn.execute(
                """
                UPDATE runtime_unresolved_effects
                SET state = ?, reason = ?, updated_at = ?
                WHERE state = ? AND COALESCE(dispatched_at, created_at) < ?
                """,
                (
                    _UNRESOLVED_STATE_UNKNOWN,
                    "crash_recovery_dispatch_lease_expired",
                    timestamp,
                    _UNRESOLVED_STATE_DISPATCHED,
                    cutoff,
                ),
            )
            placeholders = ", ".join("?" for _ in _UNRESOLVED_ACTIVE_STATES)
            existing = conn.execute(
                f"""
                SELECT * FROM runtime_unresolved_effects
                WHERE logical_effect_id = ? AND state IN ({placeholders})
                LIMIT 1
                """,
                (leid, *_UNRESOLVED_ACTIVE_STATES),
            ).fetchone()
            if existing is not None:
                conn.rollback()
                return {"outcome": "blocked", "logical_effect_id": leid, "row": _unresolved_row_to_dict(existing)}
            conn.execute(
                """
                INSERT INTO runtime_unresolved_effects (
                    logical_effect_id, effect_instance_id, tool_name,
                    resource_identity, expected_evidence_json, state,
                    attempt_id, turn_id, checkpoint_id, receipt_key,
                    semantic_result_id, reconcilability, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    leid,
                    instance_id,
                    clean_intent,
                    str(resource_identity or ""),
                    _json_dumps(evidence_payload),
                    _UNRESOLVED_STATE_PREPARED,
                    str(attempt_id or ""),
                    str(turn_id or ""),
                    str(checkpoint_id or ""),
                    str(receipt_key or ""),
                    str(semantic_result_id or ""),
                    str(reconcilability or "unknown"),
                    timestamp,
                    timestamp,
                ),
            )
            conn.commit()
        except Exception:
            with contextlib.suppress(Exception):
                conn.rollback()
            raise
        finally:
            conn.close()
    return {
        "outcome": "reserved",
        "logical_effect_id": leid,
        "effect_instance_id": instance_id,
        "state": _UNRESOLVED_STATE_PREPARED,
    }


def mark_effect_dispatched(
    *,
    logical_effect_id: str,
    effect_instance_id: str,
    claimed_by: str = "",
    execution_identity: dict | None = None,
) -> bool:
    """CAS claim PREPARED -> DISPATCHED. Only the claimant may physically mutate.

    R-4 (H-2): when the caller carries an execution identity (onboarded lane),
    the fence tuple is presented here — a stale generation/epoch cannot claim
    dispatch, and False (rowcount 0) must be treated as never-dispatched-by-it.
    """
    if execution_identity:
        from core.invocation.ledger import require_generation

        require_generation(
            str(execution_identity.get("execution_id") or ""),
            int(execution_identity.get("generation") or 0),
            runtime_epoch=str(execution_identity.get("runtime_epoch") or ""),
        )
    timestamp = _utcnow()
    with _LOCK:
        conn = _conn()
        try:
            cursor = conn.execute(
                """
                UPDATE runtime_unresolved_effects
                SET state = ?, claimed_by = ?, claimed_at = ?, dispatched_at = ?, updated_at = ?
                WHERE logical_effect_id = ? AND effect_instance_id = ? AND state = ?
                """,
                (
                    _UNRESOLVED_STATE_DISPATCHED,
                    str(claimed_by or ""),
                    timestamp,
                    timestamp,
                    timestamp,
                    str(logical_effect_id or ""),
                    str(effect_instance_id or ""),
                    _UNRESOLVED_STATE_PREPARED,
                ),
            )
            conn.commit()
        finally:
            conn.close()
    return cursor.rowcount == 1


def classify_effect_outcome(
    *,
    logical_effect_id: str,
    effect_instance_id: str,
    outcome: str,
    reason: str = "",
    detail: str = "",
) -> bool:
    """Write the proven outcome of a claimed dispatch.

    outcome: applied | failed_safe_to_retry | unknown. Anything unprovable MUST
    be passed as unknown -- it keeps the row active and blocking.
    """
    allowed = {_UNRESOLVED_TERMINAL_APPLIED, _UNRESOLVED_TERMINAL_FAILED, _UNRESOLVED_STATE_UNKNOWN}
    normalized = str(outcome or "").strip().lower()
    if normalized not in allowed:
        raise ValueError(f"unsupported effect outcome classification: {outcome!r}")
    timestamp = _utcnow()
    with _LOCK:
        conn = _conn()
        try:
            cursor = conn.execute(
                """
                UPDATE runtime_unresolved_effects
                SET state = ?, reason = ?, detail = ?, claimed_by = NULL, updated_at = ?
                WHERE logical_effect_id = ? AND effect_instance_id = ? AND state = ?
                """,
                (
                    normalized,
                    str(reason or ""),
                    str(detail or "")[:2000],
                    timestamp,
                    str(logical_effect_id or ""),
                    str(effect_instance_id or ""),
                    _UNRESOLVED_STATE_DISPATCHED,
                ),
            )
            conn.commit()
        finally:
            conn.close()
    return cursor.rowcount == 1


def logical_effect_row_states(logical_effect_id: str) -> list[dict[str, Any]]:
    """The durable row states one logical effect has recorded, oldest first.

    A READ of the one ledger -- for callers that must reconcile session-journal state against
    effect truth after a crash, a restart, or a failed session write. Each row carries its
    state (prepared/dispatched/unknown/applied/failed_safe_to_retry/expired_pre_dispatch/
    superseded), the cancellation or classification reason, the instance id and timestamps.
    No rows means the effect was never reserved durably."""
    clean = str(logical_effect_id or "").strip()
    if not clean:
        return []
    with _LOCK:
        conn = _conn()
        try:
            rows = conn.execute(
                """
                SELECT effect_instance_id, state, reason, claimed_by, created_at, updated_at
                FROM runtime_unresolved_effects
                WHERE logical_effect_id = ?
                ORDER BY created_at, effect_instance_id
                """,
                (clean,),
            ).fetchall()
        finally:
            conn.close()
    return [
        {
            "effect_instance_id": str(row[0] or ""),
            "state": str(row[1] or ""),
            "reason": str(row[2] or ""),
            "claimed_by": str(row[3] or ""),
            "created_at": str(row[4] or ""),
            "updated_at": str(row[5] or ""),
        }
        for row in rows
    ]


def cancel_effect_reservation(
    *, logical_effect_id: str, effect_instance_id: str, reason: str = ""
) -> bool:
    """Release a PREPARED reservation that never crossed into dispatch."""
    timestamp = _utcnow()
    with _LOCK:
        conn = _conn()
        try:
            cursor = conn.execute(
                """
                UPDATE runtime_unresolved_effects
                SET state = ?, reason = ?, updated_at = ?
                WHERE logical_effect_id = ? AND effect_instance_id = ? AND state = ?
                """,
                (
                    _UNRESOLVED_EXPIRED_PRE_DISPATCH,
                    str(reason or "cancelled_pre_dispatch"),
                    timestamp,
                    str(logical_effect_id or ""),
                    str(effect_instance_id or ""),
                    _UNRESOLVED_STATE_PREPARED,
                ),
            )
            conn.commit()
        finally:
            conn.close()
    return cursor.rowcount == 1


def resolve_unresolved_effect(
    *,
    logical_effect_id: str,
    resolution: str,
    source: str,
    evidence: str = "",
    resolved_by: str = "",
) -> dict[str, Any]:
    """Typed resolution of an unresolved effect; appends an immutable history event.

    source must be mechanical|provider|user. The model is NOT an effect-truth
    source and can never write a resolution through this function.
    """
    clean_resolution = str(resolution or "").strip().upper()
    clean_source = str(source or "").strip().lower()
    if clean_source == "model":
        raise ValueError("the model is not an effect-truth source; resolution refused")
    if clean_source not in _VALID_RESOLUTION_SOURCES:
        raise ValueError(f"unsupported resolution source: {source!r}")
    if clean_resolution not in _VALID_RESOLUTIONS:
        raise ValueError(f"unsupported resolution: {resolution!r}")
    target_state = {
        "CONFIRMED_APPLIED": _UNRESOLVED_TERMINAL_APPLIED,
        "CONFIRMED_FAILED_SAFE_TO_RETRY": _UNRESOLVED_TERMINAL_FAILED,
        "SUPERSEDED_NEW_INSTANCE": _UNRESOLVED_SUPERSEDED,
    }[clean_resolution]
    timestamp = _utcnow()
    resolution_id = f"res-{uuid.uuid4().hex}"
    with _LOCK:
        conn = _conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute(
                """
                SELECT effect_instance_id, state FROM runtime_unresolved_effects
                WHERE logical_effect_id = ?
                ORDER BY created_at DESC, updated_at DESC LIMIT 1
                """,
                (str(logical_effect_id or ""),),
            ).fetchone()
            if current is None:
                conn.rollback()
                return {"outcome": "no_active_effect"}
            placeholders = ", ".join("?" for _ in _UNRESOLVED_ACTIVE_STATES)
            active = conn.execute(
                f"""
                SELECT 1 FROM runtime_unresolved_effects
                WHERE logical_effect_id = ? AND state IN ({placeholders}) LIMIT 1
                """,
                (str(logical_effect_id or ""), *_UNRESOLVED_ACTIVE_STATES),
            ).fetchone()
            if active is None:
                conn.rollback()
                return {"outcome": "already_resolved", "state": current["state"]}
            conn.execute(
                """
                INSERT INTO unresolved_effect_resolutions (
                    resolution_id, logical_effect_id, effect_instance_id,
                    resolution, source, evidence, resolved_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    resolution_id,
                    str(logical_effect_id or ""),
                    str(current["effect_instance_id"] or ""),
                    clean_resolution,
                    clean_source,
                    str(evidence or "")[:2000],
                    str(resolved_by or ""),
                    timestamp,
                ),
            )
            conn.execute(
                """
                UPDATE runtime_unresolved_effects
                SET state = ?, reason = ?, detail = ?, updated_at = ?
                WHERE logical_effect_id = ?
                  AND state IN ('prepared', 'dispatched', 'unknown')
                """,
                (
                    target_state,
                    f"resolved_{clean_resolution.lower()}",
                    f"source={clean_source}; evidence={str(evidence or '')[:1500]}",
                    timestamp,
                    str(logical_effect_id or ""),
                ),
            )
            conn.commit()
        except Exception:
            with contextlib.suppress(Exception):
                conn.rollback()
            raise
        finally:
            conn.close()
    return {
        "outcome": "resolved",
        "resolution_id": resolution_id,
        "resolution": clean_resolution,
        "source": clean_source,
        "state": target_state,
    }


def store_hive_idempotent_result(
    *,
    idempotency_key: str,
    operation_kind: str,
    response_payload: dict[str, Any],
) -> None:
    clean_key = str(idempotency_key or "").strip()
    if not clean_key:
        return
    timestamp = _utcnow()
    with _LOCK:
        conn = _conn()
        try:
            conn.execute(
                """
                INSERT INTO hive_idempotency_keys (
                    idempotency_key, operation_kind, response_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(idempotency_key) DO UPDATE SET
                    response_json = excluded.response_json,
                    updated_at = excluded.updated_at
                """,
                (
                    clean_key,
                    str(operation_kind or "").strip(),
                    _json_dumps(response_payload),
                    timestamp,
                    timestamp,
                ),
            )
            conn.commit()
        finally:
            conn.close()


def load_hive_idempotent_result(idempotency_key: str) -> dict[str, Any] | None:
    clean_key = str(idempotency_key or "").strip()
    if not clean_key:
        return None
    with _LOCK:
        conn = _conn()
        try:
            row = conn.execute(
                """
                SELECT response_json
                FROM hive_idempotency_keys
                WHERE idempotency_key = ?
                LIMIT 1
                """,
                (clean_key,),
            ).fetchone()
        finally:
            conn.close()
    if not row:
        return None
    return _json_loads(row["response_json"], fallback={})


def _runtime_db_path() -> str | Path:
    return _DB_PATH_OVERRIDE or DEFAULT_DB_PATH


_MIGRATED_PATHS: set[str] = set()


def _ensure_migrated(path: str | Path) -> None:
    """Create the runtime-continuity schema on a fresh/empty runtime home before any
    query. Without this, a fresh agent start() crashes with
    'no such table: runtime_checkpoints'. Idempotent (migrations use IF NOT EXISTS)
    and run once per db path per process."""
    key = str(path)
    if key in _MIGRATED_PATHS:
        return
    _MIGRATED_PATHS.add(key)
    try:
        from storage.migrations import run_migrations

        run_migrations(path)
    except Exception:
        _MIGRATED_PATHS.discard(key)


def _conn():
    path = _runtime_db_path()
    _ensure_migrated(path)
    return get_connection(path)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, default=str)


def _json_loads(raw: Any, *, fallback: Any) -> Any:
    try:
        loaded = json.loads(str(raw or ""))
    except Exception:
        return fallback
    if isinstance(fallback, dict):
        return loaded if isinstance(loaded, dict) else fallback
    if isinstance(fallback, list):
        return loaded if isinstance(loaded, list) else fallback
    if isinstance(fallback, str):
        return loaded if isinstance(loaded, str) else fallback
    return loaded


_CHECKPOINT_JSON_COLUMNS = frozenset({"source_context_json", "state_json"})


def _checkpoint_evidence_limit() -> int:
    from core.agent_runtime.request_authority import TURN_EVIDENCE_ITEMS_MAX

    return TURN_EVIDENCE_ITEMS_MAX


def _decode_checkpoint_evidence_row(value: Any, value_type: str) -> Any:
    """Decode one row already admitted by SQLite's bounded ``json_each`` query.

    Objects and arrays are returned by SQLite as JSON text; scalar values are already native.
    Keeping this as one explicit seam lets hostile tests count actual item materializations. The
    caller's SQL ``LIMIT`` is the boundary: this function must never be invoked for item 65.
    """
    if value_type in {"object", "array"}:
        return _json_loads(value, fallback={} if value_type == "object" else [])
    if value_type == "true":
        return True
    if value_type == "false":
        return False
    if value_type == "null":
        return None
    return value


def _set_checkpoint_evidence_path(
    payload: dict[str, Any],
    *,
    evidence_path: str,
    items: list[Any],
) -> None:
    if evidence_path == "$.external_evidence":
        payload["external_evidence"] = items
        return
    if evidence_path == "$.loop_source_context.external_evidence":
        loop_context = payload.get("loop_source_context")
        if not isinstance(loop_context, dict):
            loop_context = {}
            payload["loop_source_context"] = loop_context
        loop_context["external_evidence"] = items
        return
    raise ValueError(f"unsupported checkpoint evidence path: {evidence_path}")


def _load_bounded_checkpoint_json_object(
    conn: Any,
    *,
    checkpoint_id: str,
    json_column: str,
    evidence_path: str,
    evidence_limit: int | None = None,
) -> dict[str, Any]:
    """Restore checkpoint JSON without ever materializing its full evidence array in Python.

    ``json_remove`` reconstructs the ordinary object with the evidence collection absent. A
    separate ``json_each ... LIMIT 64`` query admits only the first bounded rows into the runtime.
    That ordering is load-bearing: applying ``bounded_evidence_items`` after ``json.loads`` would
    produce the right final length while still allocating and walking all 50,000 legacy items.

    JSON1 is part of every supported CPython SQLite build. If a malformed legacy value or an
    unsupported SQLite build rejects the projection, fail closed to an empty object rather than
    falling back to the unbounded whole-object decoder this boundary exists to eliminate.
    """
    if json_column not in _CHECKPOINT_JSON_COLUMNS:
        raise ValueError(f"unsupported checkpoint JSON column: {json_column}")
    bounded_limit = max(
        0,
        min(
            _checkpoint_evidence_limit(),
            _checkpoint_evidence_limit()
            if evidence_limit is None
            else int(evidence_limit),
        ),
    )
    try:
        projection = conn.execute(
            f"""
            SELECT
                CASE
                    WHEN json_valid({json_column})
                    THEN json_remove({json_column}, ?)
                    ELSE '{{}}'
                END AS bounded_json,
                CASE
                    WHEN json_valid({json_column})
                    THEN json_type({json_column}, ?)
                    ELSE NULL
                END AS evidence_type
            FROM runtime_checkpoints
            WHERE checkpoint_id = ?
            LIMIT 1
            """,
            (evidence_path, evidence_path, checkpoint_id),
        ).fetchone()
    except Exception:
        return {}
    if not projection:
        return {}
    payload = _json_loads(projection["bounded_json"], fallback={})
    if not isinstance(payload, dict):
        payload = {}
    evidence_type = str(projection["evidence_type"] or "")
    if evidence_type != "array":
        if evidence_type:
            _set_checkpoint_evidence_path(payload, evidence_path=evidence_path, items=[])
        return payload
    try:
        rows = conn.execute(
            f"""
            SELECT evidence.value AS evidence_value, evidence.type AS evidence_value_type
            FROM runtime_checkpoints AS checkpoint,
                 json_each(checkpoint.{json_column}, ?) AS evidence
            WHERE checkpoint.checkpoint_id = ?
            LIMIT ?
            """,
            (evidence_path, checkpoint_id, bounded_limit),
        ).fetchall()
    except Exception:
        rows = []
    items = [
        _decode_checkpoint_evidence_row(row["evidence_value"], str(row["evidence_value_type"] or ""))
        for row in rows
    ]
    _set_checkpoint_evidence_path(payload, evidence_path=evidence_path, items=items)
    return payload


def _restore_runtime_checkpoint_json_view(
    conn: Any,
    *,
    checkpoint_id: str,
    evidence_limit: int,
) -> tuple[dict[str, Any], dict[str, Any], int]:
    """Restore both persisted representations under one shared evidence allowance.

    ``source_context_json`` is the existing turn-level continuity authority. When it explicitly
    carries evidence, loop state reuses that same bounded list; the duplicate state representation
    is projected without decoding its evidence. If source context has no evidence field, loop state
    may spend the allowance and its bounded result becomes the shared restored view. Conflicting
    arrays are therefore deterministic and can never be silently unioned under separate budgets.
    """

    source_context = _load_bounded_checkpoint_json_object(
        conn,
        checkpoint_id=checkpoint_id,
        json_column="source_context_json",
        evidence_path="$.external_evidence",
        evidence_limit=evidence_limit,
    )
    source_has_evidence = "external_evidence" in source_context
    source_evidence = source_context.get("external_evidence")
    source_items = source_evidence if isinstance(source_evidence, list) else []
    remaining = max(0, int(evidence_limit) - len(source_items))
    state = _load_bounded_checkpoint_json_object(
        conn,
        checkpoint_id=checkpoint_id,
        json_column="state_json",
        evidence_path="$.loop_source_context.external_evidence",
        evidence_limit=0 if source_has_evidence else remaining,
    )
    loop_context = state.get("loop_source_context")
    if not isinstance(loop_context, dict):
        loop_context = {}
        state["loop_source_context"] = loop_context
    state_evidence = loop_context.get("external_evidence")
    state_items = state_evidence if isinstance(state_evidence, list) else []
    if source_has_evidence:
        canonical_evidence = source_items
        evidence_work = len(source_items)
    elif "external_evidence" in loop_context:
        canonical_evidence = state_items
        evidence_work = len(state_items)
        source_context["external_evidence"] = canonical_evidence
    else:
        canonical_evidence = None
        evidence_work = 0
    if canonical_evidence is not None:
        loop_context["external_evidence"] = canonical_evidence
    return source_context, state, evidence_work


def _preview_text(text: str, *, limit: int = 180) -> str:
    compact = " ".join(str(text or "").split()).strip()
    if len(compact) <= limit:
        return compact
    return compact[: max(1, limit - 3)].rstrip() + "..."


def _clean_request_preview(value: Any) -> str:
    return _preview_text(str(value or ""))


def _stable_source_context(source_context: dict[str, Any] | None) -> dict[str, Any]:
    from core.agent_runtime.request_authority import bounded_evidence_items
    from core.secret_redaction import redact_secrets
    from core.turn_contract import TURN_REQUEST_KEY

    stable = dict(source_context or {})
    # ARCH-TRUTH-R1: the typed turn contract is LIVE-TURN state, never checkpoint
    # payload. Strip it at this one persistence boundary (every checkpoint
    # create/merge/update path funnels through here) so no stringified contract
    # ever lands in stored JSON — the turn that resumes re-mints its own
    # canonical request at its own ingress, before its inner runtime begins.
    stable.pop(TURN_REQUEST_KEY, None)
    if "external_evidence" in stable:
        stable["external_evidence"] = bounded_evidence_items(
            stable.get("external_evidence")
        )
    history = []
    for item in list(stable.get("conversation_history") or [])[-12:]:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        content = str(item.get("content") or "").strip()
        if role not in {"system", "user", "assistant"} or not content:
            continue
        # Redact high-confidence secrets before the history is persisted to the checkpoint
        # store (this is a plaintext parallel to the redacted conversation log).
        history.append({"role": role, "content": redact_secrets(content)[:4000]})
    stable["conversation_history"] = history
    return stable


def _stable_checkpoint_state(state: dict[str, Any] | None) -> dict[str, Any]:
    stable = dict(state or {})
    if "loop_source_context" in stable:
        stable["loop_source_context"] = _stable_source_context(
            stable.get("loop_source_context")
            if isinstance(stable.get("loop_source_context"), dict)
            else {}
        )
    return stable


def _merge_loop_source_context(existing: Any, incoming: dict[str, Any]) -> dict[str, Any]:
    from core.agent_runtime.request_authority import compose_evidence_origins

    merged = _stable_source_context(existing if isinstance(existing, dict) else {})
    stable_incoming = _stable_source_context(incoming)
    stored_evidence = merged.get("external_evidence")
    incoming_evidence = stable_incoming.get("external_evidence")
    merged.update(stable_incoming)
    if "external_evidence" in merged or "external_evidence" in stable_incoming:
        merged["external_evidence"] = compose_evidence_origins(
            stored_evidence,
            incoming_evidence,
        )
    history = []
    for item in list(merged.get("conversation_history") or [])[-12:]:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        content = str(item.get("content") or "").strip()
        if role not in {"system", "user", "assistant"} or not content:
            continue
        history.append({"role": role, "content": content[:4000]})
    merged["conversation_history"] = history
    return merged


def _normalize_for_hash(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _normalize_for_hash(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_normalize_for_hash(item) for item in value]
    if isinstance(value, tuple):
        return [_normalize_for_hash(item) for item in value]
    return value


def _event_status(event_type: str, *, previous_status: str = "") -> str:
    lowered = str(event_type or "").strip().lower()
    # Preference and approval receipts belong in the durable timeline, but they are not task state
    # transitions. With no prior task they describe an idle session; only a real nonterminal task
    # event below may establish `running`. Preserving a prior status also prevents a later mode
    # change from rewriting a completed Activity-history card.
    if lowered in {"mode_changed", "bypass_activated", "bypass_revoked", "permission_approved"}:
        return str(previous_status or "idle").strip().lower() or "idle"
    if lowered == "permission_denied":
        return "failed"
    if lowered in {
        "tool_failed",
        "task_failed",
        "task_envelope_failed",
        "task_envelope_step_failed",
        "task_envelope_merge_failed",
        "task_envelope_capacity_blocked",
        "task_envelope_missing_receipts",
        "task_envelope_missing_subtasks",
        "task_envelope_dependency_blocked",
        "task_envelope_dependency_failed",
        "task_envelope_restore_failed",
        "task_envelope_rollback_failed",
    }:
        return "failed"
    if lowered in {"task_interrupted"}:
        return "interrupted"
    if lowered in {"tool_preview", "task_pending_approval"}:
        return "pending_approval"
    if lowered in {"task_completed", "tool_loop_completed", "task_envelope_completed", "task_envelope_merge_completed"}:
        return "completed"
    if lowered in {
        "task_received",
        "task_resumed",
        "task_classified",
        "tool_selected",
        "tool_started",
        "tool_loop_resumed",
        "tool_executed",
        "tool_synthesizing",
        "task_envelope_started",
        "task_envelope_children_scheduled",
        "task_envelope_step_completed",
        "task_envelope_restore_completed",
        "task_envelope_rollback_completed",
    }:
        return "running"
    # Informational/forward-compatible events preserve lifecycle state. Unknown telemetry must not
    # be able to create a running task merely because it was appended first.
    return str(previous_status or "idle").strip().lower() or "idle"


def _session_event_count(conn: Any, session_id: str) -> int:
    row = conn.execute(
        "SELECT event_count FROM runtime_sessions WHERE session_id = ? LIMIT 1",
        (session_id,),
    ).fetchone()
    return int(row["event_count"]) if row else 0


def _session_started_at(conn: Any, session_id: str, fallback: str) -> str:
    row = conn.execute(
        "SELECT started_at FROM runtime_sessions WHERE session_id = ? LIMIT 1",
        (session_id,),
    ).fetchone()
    return str(row["started_at"]) if row and row["started_at"] else fallback


def _upsert_runtime_session(
    conn: Any,
    *,
    session_id: str,
    request_preview: str,
    task_class: str,
    status: str,
    last_event_type: str | None,
    last_message: str | None,
    event_count: int,
    started_at: str,
    updated_at: str,
    checkpoint_id: str | None,
) -> None:
    existing = conn.execute(
        """
        SELECT request_preview, task_class, status, last_event_type, last_message, event_count, started_at, last_checkpoint_id
        FROM runtime_sessions
        WHERE session_id = ?
        LIMIT 1
        """,
        (session_id,),
    ).fetchone()
    conn.execute(
        """
        INSERT INTO runtime_sessions (
            session_id, started_at, updated_at, event_count, last_event_type,
            last_message, request_preview, task_class, status, last_checkpoint_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(session_id) DO UPDATE SET
            started_at = excluded.started_at,
            updated_at = excluded.updated_at,
            event_count = excluded.event_count,
            last_event_type = excluded.last_event_type,
            last_message = excluded.last_message,
            request_preview = excluded.request_preview,
            task_class = excluded.task_class,
            status = excluded.status,
            last_checkpoint_id = excluded.last_checkpoint_id
        """,
        (
            session_id,
            started_at,
            updated_at,
            event_count,
            last_event_type if last_event_type is not None else str(existing["last_event_type"] if existing else ""),
            last_message if last_message is not None else str(existing["last_message"] if existing else ""),
            request_preview or str(existing["request_preview"] if existing else ""),
            task_class or str(existing["task_class"] if existing else ""),
            status or str(existing["status"] if existing else "idle"),
            checkpoint_id or str(existing["last_checkpoint_id"] if existing else ""),
        ),
    )


def price_wait_sessions() -> list[str]:
    """Owner queue inventory, no task content: lets the open app resume waiting chats."""
    with _LOCK:
        conn = _conn()
        try:
            rows = conn.execute("SELECT DISTINCT session_id FROM message_queue WHERE status='pending' AND json_extract(payload_json, '$.price_wait') IS NOT NULL").fetchall()
            return [str(row["session_id"]) for row in rows]
        finally:
            conn.close()
