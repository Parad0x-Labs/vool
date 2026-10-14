"""Run history answers to a chat forget.

The runtime database keeps a record of every turn beside the memory stores: the request as it
arrived (session events, checkpoints, attempts, the turn's obligation set), the answer it gave
(the A7 finalization, the finalized response), and what the turn produced on the way (candidate
knowledge, the context access report, notifications, tool receipts). Before this module a chat
"forget X" erased X from memory and left every one of those rows readable in the database file
(measured 2026-10-14 on a served daemon: after "Forget applied", the forgotten words were still in
runtime_session_events, runtime_checkpoints, runtime_attempts, obligation_sets, a7_finalizations and
runtime_sessions).

``forget_run_history`` erases them for one chat and one token, ``verify_run_history`` re-reads them
inside the final verification, and ``checkpoint_write_ahead_logs`` folds the write-ahead log so the
old page images of erased rows do not outlive the erasure in the ``-wal`` file.

Each table gets one of three actions:

* ``delete`` -- the row is history only; nothing refers to it by identity.
* ``redact`` -- the row is a state record other rows or a running turn address by identity, so it
  stays and every field (or JSON leaf) that carries the token is blanked.
* ``a7`` -- the finalization's own governed ERASE transition (the chat-delete law): the row stays,
  its content is cleared and its content hash salted.

Ownership follows the derivative-store law (``carriers._derivative_scope``): a row of this chat, or a
row whose owner cannot be resolved, answers to the forget; a row that resolves to another chat does
not. A table that cannot be read, decoded or rewritten is named incomplete and the forget is not
reported as applied.
"""
from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.memory.carriers import (
    _RUNTIME_MADE_RE,
    ROW_IDENTITIES,
    _without_nested_identities,
    carries,
)

_LOG = logging.getLogger(__name__)

_SESSION = "session"
_ATTEMPT = "attempt"
_REQUEST = "request"
_TASK = "task"
_NOTIFICATION = "notification"
_TOPIC = "topic"
_OBLIGATION = "obligation"

#: The prefix of every store this module records in an erasure result.
STORE_PREFIX = "run_history_"


@dataclass(frozen=True)
class _Table:
    name: str
    action: str
    owner: str
    #: Columns that identify, count, time or classify the row. Every other column is payload,
    #: including columns a later writer adds.
    structural: frozenset[str]
    #: For an owner other than ``session``: the columns whose values link the row to its owner.
    links: tuple[str, ...] = ()
    #: A payload column's digest column: a digest of erased words is blanked with them (the chat-delete
    #: checkpoint sweep does the same for final_response_hash).
    digests: tuple[tuple[str, str], ...] = ()


def _cols(*names: str) -> frozenset[str]:
    return frozenset(names)


_INVENTORY: tuple[_Table, ...] = (
    _Table("runtime_session_events", "delete", _SESSION, _cols("session_id", "seq", "event_type", "created_at")),
    _Table("runtime_sessions", "redact", _SESSION, _cols(
        "session_id", "started_at", "updated_at", "event_count", "last_event_type", "task_class", "status",
        "last_checkpoint_id")),
    _Table("runtime_checkpoints", "redact", _SESSION, _cols(
        "checkpoint_id", "session_id", "task_id", "task_class", "status", "step_count", "last_tool_name",
        "final_response_hash", "resume_count", "created_at", "updated_at", "completed_at",
        "resumed_from_checkpoint_id"), digests=(("final_response", "final_response_hash"),)),
    _Table("runtime_attempts", "redact", _SESSION, _cols(
        "attempt_id", "session_id", "checkpoint_id", "origin_user_turn_id", "trigger_user_turn_id",
        "origin_conversation_event_id", "root_attempt_id", "parent_attempt_id", "execution_generation",
        "attempt_role", "execution_slot", "original_request_hash", "original_request_bytes",
        "original_request_truncated", "answer_mode", "plan_id", "lifecycle_state", "retryable",
        "retry_from_stage", "refresh_required", "created_daemon_sha", "last_updated_daemon_sha",
        "process_instance_id", "retry_idempotency_key", "created_at", "updated_at", "completed_at",
        "execution_id"), digests=(("original_request_snapshot", "original_request_hash"),)),
    _Table("runtime_attempt_subtasks", "redact", _ATTEMPT, _cols(
        "attempt_id", "subtask_id", "execution_generation", "plan_id", "operation", "entity_type",
        "lifecycle_state", "failure_class", "approval_state", "retryable", "refresh_required",
        "carried_forward_from_attempt_id", "carried_forward_from_generation", "previous_result_version",
        "queued_at", "started_at", "completed_at", "retrieved_at", "result_version", "updated_at"),
        links=("attempt_id",)),
    _Table("runtime_tool_receipts", "redact", _SESSION, _cols(
        "receipt_key", "session_id", "checkpoint_id", "tool_name", "idempotency_key", "created_at", "updated_at")),
    _Table("runtime_followup_resolutions", "redact", _SESSION, _cols(
        "resolution_id", "session_id", "follow_up_turn_id", "resolved_attempt_id", "resolution_intent",
        "resolution_confidence", "fallback_used", "created_at")),
    _Table("message_queue", "queue", _SESSION, _cols(
        "queue_item_id", "session_id", "seq", "status", "idempotency_key", "turn_id", "lease_owner",
        "created_at", "updated_at", "completed_at")),
    _Table("live_data_obligation_memory", "delete", _SESSION, _cols(
        "session_id", "operation", "source_turn_id", "updated_at")),
    _Table("machine_read_memory", "delete", _SESSION, _cols("session_id", "source_turn_id", "updated_at")),
    _Table("active_mission_slots", "delete", _SESSION, _cols(
        "slot_id", "session_id", "slot_name", "source_turn_id", "slot_confidence", "scope", "created_at",
        "superseded_at", "superseded_by_turn_id")),
    _Table("execution_facts", "delete", _SESSION, _cols(
        "fact_id", "turn_key", "session_id", "kind", "ok", "status", "created_at")),
    _Table("fault_records", "delete", _SESSION, _cols(
        "fault_id", "code", "schema_version", "category", "severity", "retry", "lifecycle", "authority",
        "turn_key", "attempt_id", "effect_id", "session_id", "dedupe", "created_at", "updated_at")),
    _Table("security_events", "redact", _SESSION, _cols(
        "event_id", "sec_code", "schema_version", "severity", "state", "source", "resource_class", "fault_id",
        "turn_key", "session_id", "observed_at", "acknowledged_at", "acknowledged_by", "resolved_at")),
    _Table("operator_action_requests", "redact", _SESSION, _cols(
        "action_id", "session_id", "task_id", "action_kind", "status", "created_at", "updated_at", "executed_at")),
    _Table("curiosity_topics", "redact", _SESSION, _cols(
        "topic_id", "session_id", "originating_task_id", "trace_id", "topic_kind", "priority", "status",
        "created_at", "updated_at", "last_run_at", "candidate_id")),
    _Table("curiosity_runs", "redact", _TOPIC, _cols(
        "run_id", "topic_id", "task_id", "trace_id", "candidate_id", "outcome", "created_at"), links=("topic_id",)),
    _Table("turn_context_generations", "redact", _SESSION, _cols(
        "principal", "session_id", "generation", "frozen_hash", "last_frontier_hash", "updated_at")),
    _Table("obligation_sets", "redact", _OBLIGATION, _cols("set_id", "version", "status", "closure_hash", "created_at")),
    _Table("a7_finalizations", "a7", _REQUEST, _cols(
        "finalization_id", "semantic_result_id", "turn_id", "content_hash", "status", "delivery_status",
        "created_at", "payload_ref", "availability", "request_id", "terminal_reason", "delivery_evidence_class"),
        links=("request_id",)),
    _Table("finalized_responses", "delete", _REQUEST, _cols(
        "parent_task_id", "status_marker", "confidence_score", "content_hash", "anchored_signature", "created_at",
        "finalization_id", "request_id"), links=("request_id", "parent_task_id")),
    _Table("candidate_knowledge_lane", "delete", _TASK, _cols(
        "candidate_id", "task_hash", "task_id", "trace_id", "task_class", "task_kind", "output_mode",
        "provider_name", "model_name", "confidence", "trust_score", "validation_state", "promotion_state",
        "review_state", "created_at", "expires_at", "invalidated_at"), links=("task_id", "trace_id")),
    _Table("context_access_log", "delete", _TASK, _cols(
        "log_id", "task_id", "trace_id", "total_context_budget", "bootstrap_tokens_used", "relevant_tokens_used",
        "cold_tokens_used", "retrieval_confidence", "swarm_metadata_consulted", "cold_archive_opened",
        "created_at"), links=("task_id", "trace_id")),
    _Table("notification_items", "delete", _SESSION, _cols(
        "seq", "notification_id", "dedupe_key", "source_kind", "schedule_id", "event_key", "session_id",
        "created_at", "read_at", "dismissed_at", "snoozed_until", "superseded_at", "last_action",
        "last_action_at")),
    _Table("notification_deliveries", "redact", _NOTIFICATION, _cols(
        "delivery_id", "notification_id", "channel", "state", "external_id", "created_at", "updated_at"),
        links=("notification_id",)),
    _Table("native_notification_requests", "redact", _NOTIFICATION, _cols(
        "identifier", "kind", "notification_id", "schedule_id", "fire_generation", "deliver_at_utc", "state",
        "hand_count", "handed_at", "submitted_at", "listed_at", "acknowledged_at", "last_action", "created_at",
        "updated_at"), links=("notification_id",)),
)

#: Session-scoped tables whose words are not run history and are not this operation's to erase:
#: objects the person made and manages themselves (a scheduled reminder, the chat's own list of
#: restricted terms), and stores another forget step already owns and verifies.
_NOT_RUN_HISTORY = frozenset({
    "reminder_requests", "session_memory_policies",
    "context_pages", "turn_context_admissions", "context_capsule_heads",
})

_SCOPE_COLUMNS = ("session_id", "chat_id", "origin_session_id", "chat_scope")
_PENDING_QUEUE_STATES = frozenset({"pending"})
_OBLIGATION_ATTEMPT_RE = re.compile(r"^ob:([^:]+):")


def _quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _decoded(value: Any) -> Any:
    """A JSON column's value; text that is not JSON is matched as the text it is."""
    if isinstance(value, (bytes, bytearray, memoryview)):
        raise ValueError("run history field has an opaque representation")
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return json.loads(value)
    except ValueError:
        return value


def _json_columns(schema: Iterable[Any]) -> frozenset[str]:
    return frozenset(str(column[1]) for column in schema
                     if "JSON" in str(column[2]).upper() or str(column[1]).endswith("_json"))


def _payload(row: Mapping[str, Any], structural: frozenset[str], json_columns: frozenset[str]) -> dict[str, Any]:
    """The row's words: structural columns, and nested runtime-made identities, left out."""
    identities = ROW_IDENTITIES | structural
    values = {}
    for key, value in dict(row).items():
        if key in identities:
            continue
        if key in json_columns:
            value = _decoded(value)
        elif isinstance(value, (bytes, bytearray, memoryview)):
            raise ValueError("run history field has an opaque representation")
        values[key] = _without_nested_identities(value, identities)
    return values


def _runtime_made(value: Any) -> bool:
    return value is None or isinstance(value, (int, float)) or (
        isinstance(value, str) and bool(_RUNTIME_MADE_RE.fullmatch(value)))


def _redacted(value: Any, token: str, structural: frozenset[str]) -> Any:
    """``value`` with every leaf that carries ``token`` blanked; shape and identities stay."""
    if isinstance(value, Mapping):
        kept = {}
        for key, item in value.items():
            if key in structural and _runtime_made(item):
                kept[key] = item
            elif not carries(key, token):
                kept[key] = _redacted(item, token, structural)
        return kept
    if isinstance(value, (list, tuple)):
        return [_redacted(item, token, structural) for item in value]
    if isinstance(value, str):
        return "" if carries(value, token) else value
    if isinstance(value, (bool, int, float)):
        return None if carries(value, token) else value
    return value


# ── ownership ────────────────────────────────────────────────────────────────


class _Owners:
    """Which attempts, requests, tasks, notifications and topics belong to this chat or another."""

    def __init__(self, conn: Any, tables: set[str], chat_id: str) -> None:
        self.chat_id = chat_id
        self.own: dict[str, set[str]] = {kind: set() for kind in (_ATTEMPT, _REQUEST, _TASK, _NOTIFICATION, _TOPIC)}
        self.foreign: dict[str, set[str]] = {kind: set() for kind in self.own}

        def bind(kind: str, session: Any, value: Any) -> None:
            text = str(value or "").strip()
            if text:
                (self.own if self.session_owned(session) else self.foreign)[kind].add(text)

        def select(table: str, *columns: str) -> list[Any]:
            if table not in tables:
                return []
            names = {str(column[1]) for column in conn.execute(f"PRAGMA table_info({_quoted(table)})")}
            if not set(columns) <= names:
                return []
            return conn.execute(f"SELECT {', '.join(_quoted(c) for c in columns)} FROM {_quoted(table)}").fetchall()

        for session, attempt in select("runtime_attempts", "session_id", "attempt_id"):
            bind(_ATTEMPT, session, attempt)
        for session, task, context in select("runtime_checkpoints", "session_id", "task_id", "source_context_json"):
            bind(_TASK, session, task)
            for request in _request_ids(context):
                bind(_REQUEST, session, request)
        for session, task in select("local_tasks", "session_id", "task_id"):
            bind(_TASK, session, task)
        for session, details in select("runtime_session_events", "session_id", "details_json"):
            for request in _request_ids(details):
                bind(_REQUEST, session, request)
            for task in _keyed_values(details, ("task_id", "trace_id")):
                bind(_TASK, session, task)
        for session, request in select("invocation_requests", "session_binding", "request_id"):
            if str(session or "").strip():
                bind(_REQUEST, session, request)
        for session, notification in select("notification_items", "session_id", "notification_id"):
            bind(_NOTIFICATION, session, notification)
        for session, topic in select("curiosity_topics", "session_id", "topic_id"):
            bind(_TOPIC, session, topic)
        for (snapshot,) in select("obligation_sets", "snapshot_json"):
            owner = self.obligation_owner(snapshot)
            if owner is not None:
                request = str(_safe_json(snapshot).get("request_id") or "").strip()
                if request:
                    (self.own if owner else self.foreign)[_REQUEST].add(request)

    def session_owned(self, session: Any) -> bool:
        return str(session or "").strip() in {"", self.chat_id}

    def linked(self, kind: str, values: Iterable[Any]) -> bool:
        """A linked row is owned when a link is this chat's, or when no link resolves at all."""
        links = {str(value or "").strip() for value in values} - {""}
        if links & self.own[kind]:
            return True
        return not links & self.foreign[kind]

    def obligation_owner(self, snapshot: Any) -> bool | None:
        """True/False for this chat/another chat, None when the set names no attempt of either."""
        data = _safe_json(snapshot)
        attempts = {match.group(1) for item in data.get("obligations") or [] if isinstance(item, Mapping)
                    for match in [_OBLIGATION_ATTEMPT_RE.match(str(item.get("obligation_id") or ""))] if match}
        if attempts & self.own[_ATTEMPT]:
            return True
        if attempts & self.foreign[_ATTEMPT]:
            return False
        return None

    def owns(self, spec: _Table, row: Mapping[str, Any]) -> bool:
        if spec.owner == _SESSION:
            return self.session_owned(row["session_id"])
        if spec.owner == _OBLIGATION:
            owner = self.obligation_owner(row["snapshot_json"])
            if owner is not None:
                return owner
            return self.linked(_REQUEST, [_safe_json(row["snapshot_json"]).get("request_id")])
        kind = {_ATTEMPT: _ATTEMPT, _REQUEST: _REQUEST, _TASK: _TASK,
                _NOTIFICATION: _NOTIFICATION, _TOPIC: _TOPIC}[spec.owner]
        names = set(row.keys())  # a sqlite3.Row's `in` tests values, not column names
        values = [row[column] for column in spec.links if column in names]
        if spec.owner == _REQUEST:
            # A finalized response names its turn by request id or by the task it answered.
            return self.linked(_REQUEST, values) and self.linked(_TASK, values)
        return self.linked(kind, values)


def _safe_json(value: Any) -> dict[str, Any]:
    try:
        data = json.loads(value) if isinstance(value, str) and value.strip() else {}
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _keyed_values(value: Any, keys: tuple[str, ...]) -> set[str]:
    found: set[str] = set()

    def walk(item: Any) -> None:
        if isinstance(item, Mapping):
            for key, nested in item.items():
                if key in keys and isinstance(nested, str) and nested.strip():
                    found.add(nested.strip())
                else:
                    walk(nested)
        elif isinstance(item, list):
            for nested in item:
                walk(nested)

    walk(_safe_json(value) if isinstance(value, str) else value)
    return found


def _request_ids(value: Any) -> set[str]:
    return _keyed_values(value, ("request_id",))


# ── erasure ──────────────────────────────────────────────────────────────────


def _owned_rows(conn: Any, spec: _Table, owners: _Owners) -> tuple[list[Any], frozenset[str]]:
    schema = conn.execute(f"PRAGMA table_info({_quoted(spec.name)})").fetchall()
    rows = conn.execute(f"SELECT rowid AS _erasure_rowid, * FROM {_quoted(spec.name)}").fetchall()
    return [row for row in rows if owners.owns(spec, row)], _json_columns(schema)


def _carrying(conn: Any, spec: _Table, owners: _Owners, token: str) -> tuple[list[Any], frozenset[str]]:
    rows, json_columns = _owned_rows(conn, spec, owners)
    # Decode every owned row before writing: a malformed or opaque row is an unverified carrier.
    payloads = [(row, _payload(row, spec.structural, json_columns)) for row in rows]
    return [row for row, payload in payloads if carries(payload, token)], json_columns


def _redact_row(conn: Any, spec: _Table, row: Any, json_columns: frozenset[str], token: str) -> None:
    structural = ROW_IDENTITIES | spec.structural
    updates: dict[str, Any] = {}
    names = list(row.keys())
    for column in names:
        if column in structural:
            continue
        value = row[column]
        if column in json_columns:
            decoded = _decoded(value)
            if isinstance(decoded, str):
                if carries(decoded, token):
                    updates[column] = ""
            elif decoded is not None and carries(_without_nested_identities(decoded, structural), token):
                updates[column] = json.dumps(_redacted(decoded, token, structural))
        elif isinstance(value, str) and carries(value, token):
            updates[column] = ""
        elif isinstance(value, (bool, int, float)) and carries(value, token):
            updates[column] = None
    for column, digest in spec.digests:
        if column in updates and digest in names:
            updates[digest] = ""
    if updates:
        assignments = ", ".join(f"{_quoted(column)} = ?" for column in updates)
        conn.execute(f"UPDATE {_quoted(spec.name)} SET {assignments} WHERE rowid = ?",
                     (*updates.values(), row["_erasure_rowid"]))


def _erase_finalizations(rows: list[Any]) -> None:
    from core.finalization import AVAILABILITY_ERASED, set_availability

    for row in rows:
        if not set_availability(str(row["finalization_id"]), AVAILABILITY_ERASED,
                                reason="chat forget", governance_actor="memory_forget"):
            raise RuntimeError("finalization erase transition was refused")


def _erase_table(conn: Any, spec: _Table, owners: _Owners, token: str) -> tuple[int, bool]:
    """Returns (rows erased, whether a carrying row had to be left in place)."""
    matched, json_columns = _carrying(conn, spec, owners, token)
    held: set[Any] = set()
    if spec.action == "a7":
        conn.commit()
        _erase_finalizations(matched)
    else:
        for row in matched:
            if spec.action == "delete":
                conn.execute(f"DELETE FROM {_quoted(spec.name)} WHERE rowid = ?", (row["_erasure_rowid"],))
            elif spec.action == "queue" and str(row["status"] or "") in _PENDING_QUEUE_STATES:
                # A queued message that has not run yet is the person's own unsent words: dropping or
                # blanking it would lose a message. It stays, and the forget says it is not complete.
                held.add(row["_erasure_rowid"])
            else:
                _redact_row(conn, spec, row, json_columns, token)
        conn.commit()
    retained, _ = _carrying(conn, spec, owners, token)
    if any(row["_erasure_rowid"] not in held for row in retained):
        raise RuntimeError("run history could not be erased and verified")
    return len(matched) - len(retained), bool(held)


def _runtime_databases() -> list[str]:
    """Every database file a run-history writer uses: the runtime default, and the runtime
    continuity store when it was pointed at a file of its own."""
    from storage.db import _resolve_db_path, active_default_db_path

    paths = [str(active_default_db_path())]
    from core.runtime_continuity import _runtime_db_path

    continuity = str(_resolve_db_path(_runtime_db_path()))
    if continuity not in paths and Path(continuity).exists():
        paths.append(continuity)
    return paths


def _tables(conn: Any) -> set[str]:
    return {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def forget_run_history(token: str, *, chat_id: str, erasures: dict[str, Any]) -> None:
    """Erase ``token`` from this chat's run history in the runtime database and verify each table."""
    from core.memory.entries import _record_erasure_store
    from storage.db import get_connection

    try:
        databases = _runtime_databases()
    except Exception as exc:
        _LOG.warning("forget: run history databases could not be resolved: %s", type(exc).__name__)
        _record_erasure_store(erasures, "run_history", "incomplete")
        return
    for database in databases:
        conn = None
        try:
            conn = get_connection(database)
            tables = _tables(conn)
            owners = _Owners(conn, tables, chat_id)
            for spec in _INVENTORY:
                if spec.name not in tables:
                    continue
                store = STORE_PREFIX + spec.name
                try:
                    removed, held = _erase_table(conn, spec, owners, token)
                    _record_erasure_store(erasures, store, "incomplete" if held else "verified", removed)
                except Exception as exc:
                    conn.rollback()
                    _LOG.warning("forget: %s could not be erased: %s", store, type(exc).__name__)
                    _record_erasure_store(erasures, store, "incomplete")
        except Exception as exc:
            _LOG.warning("forget: run history could not be erased: %s", type(exc).__name__)
            _record_erasure_store(erasures, "run_history_database", "incomplete")
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    _record_erasure_store(erasures, "run_history_connection", "incomplete")
    failed = any(name.startswith(STORE_PREFIX) and result["status"] == "incomplete"
                 for name, result in erasures.get("stores", {}).items())
    _record_erasure_store(erasures, "run_history", "incomplete" if failed else "verified")


# ── verification ─────────────────────────────────────────────────────────────


def _unclassified_payload(row: Mapping[str, Any]) -> list[Any]:
    """A session-scoped table nobody classified: its words, without ids, stamps and counters."""
    words: list[Any] = []
    for key, value in dict(row).items():
        if key in ROW_IDENTITIES or _runtime_made(value):
            continue
        if isinstance(value, (bytes, bytearray, memoryview)):
            raise ValueError("unclassified run history field has an opaque representation")
        if key.endswith("_json"):
            value = _decoded(value)
        words.append(_without_nested_identities(value, ROW_IDENTITIES))
    return words


def verify_run_history(conn: Any, tables: set[str], chat_id: str, token: str, verify: Any) -> None:
    """Re-read every run-history table, and every other chat-scoped table no store owns.

    A chat-scoped table that is not classified anywhere is read too: if this chat's rows carry the
    forgotten words, the forget is reported incomplete rather than claimed while they stay.
    ``conn`` is the runtime default database; a separate runtime continuity file is read as well.
    """
    from storage.db import active_default_db_path, get_connection

    _verify_database(conn, tables, chat_id, token, verify)
    default = str(active_default_db_path())
    for database in _runtime_databases():
        if database == default:
            continue
        other = get_connection(database)
        try:
            _verify_database(other, _tables(other), chat_id, token, verify)
        finally:
            other.close()


def _verify_database(conn: Any, tables: set[str], chat_id: str, token: str, verify: Any) -> None:
    from core.memory.carriers import _derivative_tables

    owners = _Owners(conn, tables, chat_id)
    for spec in _INVENTORY:
        if spec.name not in tables:
            continue

        def table_rows(spec: _Table = spec) -> bool:
            matched, _ = _carrying(conn, spec, owners, token)
            return bool(matched)

        verify(STORE_PREFIX + spec.name, table_rows)

    classified = {spec.name for spec in _INVENTORY} | _NOT_RUN_HISTORY | _derivative_tables(sorted(tables))
    for table in sorted(tables - classified):
        if table.startswith("sqlite_"):
            continue
        names = {str(column[1]) for column in conn.execute(f"PRAGMA table_info({_quoted(table)})")}
        scope = next((name for name in _SCOPE_COLUMNS if name in names), None)
        if scope is None:
            continue

        def unclassified(table: str = table, scope: str = scope) -> bool:
            rows = conn.execute(
                f"SELECT * FROM {_quoted(table)} WHERE {_quoted(scope)} = ? OR {_quoted(scope)} IS NULL "
                f"OR {_quoted(scope)} = ''", (chat_id,),
            ).fetchall()
            return any(carries(_unclassified_payload(row), token) for row in rows)

        verify("unclassified_" + table, unclassified)


def checkpoint_write_ahead_logs(erasures: dict[str, Any], *, memories: Iterable[Any] = ()) -> None:
    """Fold each erased database's write-ahead log into the file and truncate it.

    Until a checkpoint the ``-wal`` file still holds the page images written before the erasure;
    a TRUNCATE checkpoint copies the current pages back and empties it. A checkpoint that cannot
    complete (another connection keeps reading an older snapshot) leaves the forget incomplete.
    """
    from core.memory.entries import _record_erasure_store
    from storage.db import get_connection

    def fold(conn: Any) -> bool:
        for attempt in range(5):
            mode = conn.execute("PRAGMA journal_mode").fetchone()
            if str(mode[0]).lower() != "wal":
                return True
            busy, frames, _done = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if not busy and frames in (0, -1):
                return True
            time.sleep(0.05 * (attempt + 1))
        return False

    folded = True
    try:
        databases = _runtime_databases()
    except Exception as exc:
        _LOG.warning("forget: runtime databases could not be resolved: %s", type(exc).__name__)
        databases, folded = [], False
    for database in databases:
        conn = None
        try:
            conn = get_connection(database)
            folded = fold(conn) and folded
        except Exception as exc:
            _LOG.warning("forget: runtime write-ahead log could not be folded: %s", type(exc).__name__)
            folded = False
        finally:
            if conn is not None:
                conn.close()
    for memory in memories:
        try:
            memory._conn.commit()
            folded = fold(memory._conn) and folded
        except Exception as exc:
            _LOG.warning("forget: memory write-ahead log could not be folded: %s", type(exc).__name__)
            folded = False
    _record_erasure_store(erasures, "write_ahead_log", "verified" if folded else "incomplete")
