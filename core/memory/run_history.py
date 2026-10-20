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
* ``a7`` -- the finalization's own governed erasure (``erase_finalization_payload``, the chat-delete
  law): the row stays, its content is cleared, its content hash salted, and the A8 derivative sweep
  (useful outputs, pins, vault bundles, mirror snapshots, ...) runs to completion or the forget is
  not complete.
* ``artifact`` -- an archived artifact: the packed file and its manifest row are both removed.
* ``queue`` -- a queued message: one that already ran is redacted, one still waiting is kept.
* ``goal`` -- the chat's explicit /goal record (``core.goal_workflow``): redacted like a state record, and a
  goal that is not finished and whose working text (objective, acceptance, plan steps, execution binding)
  carried the words is stopped and its worker fenced, so it never runs on blanked text. A step's settled
  reason and answer excerpt, the last reason and superseded revisions are record only: blanked, and the
  goal goes on.

A row that carried the words also loses every nested digest (``request_sha256``,
``original_request_hash``, any ``*_hash``/``*_sha256``/``*_digest`` leaf): an unsalted digest of a short
request ("my locker code is 4417") gives the words back by trial.

The person's pinned messages (``message_pins.json`` and its ``.bak``) of this chat that carry the words
are removed with the run history.

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
    fold_forget_text,
    text_carries,
)

_LOG = logging.getLogger(__name__)

_SESSION = "session"
_ATTEMPT = "attempt"
_REQUEST = "request"
_TASK = "task"
_NOTIFICATION = "notification"
_TOPIC = "topic"
_OBLIGATION = "obligation"
#: A row of this chat; a row with no chat is this chat's only when its topic is.
_SESSION_TOPIC = "session_topic"

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
    # Research the runtime packed for a chat (core/autonomous_topic_research.py): the manifest names the
    # topic in its title and summary, and the packed file holds the whole packet.
    _Table("artifact_manifests", "artifact", _SESSION_TOPIC, _cols(
        "artifact_id", "source_kind", "topic_id", "claim_id", "candidate_id", "session_id", "file_path",
        "storage_backend", "content_sha256", "raw_bytes", "compressed_bytes", "compression_ratio", "created_at",
        "updated_at"), links=("topic_id",)),
    # The chat's hive watch state: id lists and counters, plus the last interaction's payload.
    _Table("session_hive_watch_state", "redact", _SESSION, _cols(
        "session_id", "watched_topic_ids_json", "seen_post_ids_json", "pending_topic_ids_json",
        "seen_curiosity_topic_ids_json", "seen_curiosity_run_ids_json", "seen_agent_ids_json",
        "last_active_agents", "snooze_until", "last_prompted_at", "interaction_mode", "smalltalk_repeat_count",
        "updated_at")),
    # The chat's explicit /goal record (core/goal_workflow.py): objective, acceptance, plan and step results.
    _Table("chat_goals", "goal", _SESSION, _cols(
        "goal_id", "session_id", "project_id", "state", "revision", "current_step_id", "created_at", "updated_at",
        "ended_at", "started_at", "paused_at", "paused_seconds", "created_turn_id", "model", "worker_epoch")),
    # An agent-team member started from a chat: its record is re-adopted by id after a restart, so it stays.
    _Table("agents", "redact", _SESSION, _cols(
        "agent_id", "team_id", "key", "importance", "effective_importance", "depth", "parent_id", "state",
        "run_token", "pid", "create_time", "launched_at", "ended_at", "exit_code", "progress_done",
        "progress_planned", "running_seconds", "session_id")),
)

_GOAL_IDENTITIES = ROW_IDENTITIES | next(spec.structural for spec in _INVENTORY if spec.name == "chat_goals")

#: Session-scoped tables whose words are not run history and are not this operation's to erase:
#: objects the person made and manages themselves (a scheduled reminder, the chat's own list of
#: restricted terms), and stores another forget step already owns and verifies.
_NOT_RUN_HISTORY = frozenset({
    "reminder_requests", "session_memory_policies",
    "context_pages", "turn_context_admissions", "context_capsule_heads",
})

_SCOPE_COLUMNS = ("session_id", "chat_id", "origin_session_id", "chat_scope")

#: A store a retry of the forget cannot clear: a chat-scoped table no erasure step knows. The reply
#: does not ask for a retry over it.
UNCLASSIFIED_PREFIX = "unclassified_"

#: A nested digest of a row's words (``request_sha256``, ``original_request_hash``, ``text_digest``).
_DIGEST_KEY_RE = re.compile(r"(?:^|_)(?:sha1|sha256|sha512|md5|hash|digest|checksum)$")

#: A number the runtime counted or timed (``{"elapsed_ms": 12}``, ``step_count``, ``tokens_used``): a bare
#: JSON number under such a key is bookkeeping, not the person's words, so "forget 12" leaves it.
_BOOKKEEPING_KEY_RE = re.compile(
    r"(?:^|_)(?:ms|us|ns|sec|secs|seconds|elapsed|duration|latency|count|total|seq|sequence|step|steps|index|idx"
    r"|attempt|attempts|retries|bytes|size|tokens|used|version|generation|depth|pid|exit_code|port|offset|limit"
    r"|budget|priority|timestamp|epoch|confidence|score|ratio|rate)$")
#: An ed25519 signature as the runtime stores it: base64 of 64 bytes, padded, or url-safe unpadded.
_SIGNATURE_RE = re.compile(r"[A-Za-z0-9+/]{86}==|[A-Za-z0-9_-]{86}")
#: Tables whose ``signature`` is one the runtime signed itself (the provider gateway's call records). A ``signature``
#: elsewhere may be one the person pasted (a transaction signature in a tool's arguments) and is read as words.
_RUNTIME_SIGNED_TABLES = frozenset({"provider_invocation_manifests"})
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


def _bookkeeping_number(key: Any, value: Any) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and bool(_BOOKKEEPING_KEY_RE.search(str(key).casefold())))


def _without_bookkeeping(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _without_bookkeeping(item) for key, item in value.items() if not _bookkeeping_number(key, item)}
    if isinstance(value, list):
        return [_without_bookkeeping(item) for item in value]
    return value


def _payload(row: Mapping[str, Any], structural: frozenset[str], json_columns: frozenset[str]) -> dict[str, Any]:
    """The row's words: structural columns, nested runtime-made identities and counters left out."""
    identities = ROW_IDENTITIES | structural
    values = {}
    for key, value in dict(row).items():
        if key in identities:
            continue
        if key in json_columns:
            value = _decoded(value)
        elif isinstance(value, (bytes, bytearray, memoryview)):
            raise ValueError("run history field has an opaque representation")
        values[key] = _without_bookkeeping(_without_nested_identities(value, identities))
    return values


def _runtime_made(value: Any) -> bool:
    return value is None or isinstance(value, (int, float)) or (
        isinstance(value, str) and bool(_RUNTIME_MADE_RE.fullmatch(value)))


def _redacted(value: Any, token: str, structural: frozenset[str]) -> Any:
    """``value`` with every leaf that carries ``token`` blanked; shape and identities stay."""
    if isinstance(value, Mapping):
        kept = {}
        for key, item in value.items():
            if (key in structural and _runtime_made(item)) or _bookkeeping_number(key, item):
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


def _without_digests(value: Any) -> Any:
    """``value`` with every nested digest leaf blanked."""
    if isinstance(value, Mapping):
        return {key: "" if (isinstance(item, str) and item and _DIGEST_KEY_RE.search(str(key).casefold()))
                else _without_digests(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_without_digests(item) for item in value]
    return value


# ── ownership ────────────────────────────────────────────────────────────────


class _Owners:
    """Which attempts, requests, tasks, notifications and topics belong to this chat or another."""

    def __init__(self, conn: Any, tables: set[str], chat_id: str, *, resolve_links: bool = True) -> None:
        self.chat_id = chat_id
        self.own: dict[str, set[str]] = {kind: set() for kind in (_ATTEMPT, _REQUEST, _TASK, _NOTIFICATION, _TOPIC)}
        self.foreign: dict[str, set[str]] = {kind: set() for kind in self.own}
        if not resolve_links:
            return  # only session-owned tables will be asked about

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

    def resolves_to_this_chat(self, spec: _Table, row: Mapping[str, Any]) -> bool:
        """Owned because it names this chat, not only because it names no chat at all."""
        if spec.owner == _SESSION:
            return str(row["session_id"] or "").strip() == self.chat_id
        if spec.owner == _SESSION_TOPIC:
            return self.owns(spec, row)
        if spec.owner == _OBLIGATION:
            owner = self.obligation_owner(row["snapshot_json"])
            if owner is not None:
                return owner
            return bool({str(_safe_json(row["snapshot_json"]).get("request_id") or "").strip()} & self.own[_REQUEST])
        names = set(row.keys())
        links = {str(row[column] or "").strip() for column in spec.links if column in names} - {""}
        kinds = (_REQUEST, _TASK) if spec.owner == _REQUEST else (spec.owner,)
        return any(links & self.own[kind] for kind in kinds)

    def owns(self, spec: _Table, row: Mapping[str, Any]) -> bool:
        if spec.owner == _SESSION:
            return self.session_owned(row["session_id"])
        if spec.owner == _SESSION_TOPIC:
            session = str(row["session_id"] or "").strip()
            if session:
                return session == self.chat_id
            topic = str(row["topic_id"] or "").strip()
            return bool(topic) and topic in self.own[_TOPIC]
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
    query = f"SELECT rowid AS _erasure_rowid, * FROM {_quoted(spec.name)}"
    params: tuple[Any, ...] = ()
    if spec.owner == _SESSION:
        # Narrowed in SQL, decided by ``owns`` below: this chat's rows and rows with no chat.
        query += " WHERE session_id IS NULL OR session_id = ? OR TRIM(session_id, ' \t\r\n') IN ('', ?)"
        params = (owners.chat_id, owners.chat_id)
    rows = conn.execute(query, params).fetchall()
    return [row for row in rows if owners.owns(spec, row)], _json_columns(schema)


def _archived_payload(row: Mapping[str, Any]) -> Any:
    """An artifact's packed file, decoded; an archive that is present but unreadable fails closed."""
    path = Path(str(row["file_path"] or "")).expanduser()
    if not str(row["file_path"] or "").strip() or not path.is_file():
        return None
    from core.liquefy_bridge import load_packed_bytes

    raw = load_packed_bytes(payload=path.read_bytes(), storage_backend=str(row["storage_backend"] or "local_archive"))
    return json.loads(raw.decode("utf-8"))


def _carrying(conn: Any, spec: _Table, owners: _Owners, token: str) -> tuple[list[Any], frozenset[str]]:
    rows, json_columns = _owned_rows(conn, spec, owners)
    # Decode every owned row before writing: a malformed or opaque row is an unverified carrier.
    payloads = []
    for row in rows:
        payload = _payload(row, spec.structural, json_columns)
        if spec.action == "artifact":
            payload["_archive"] = _without_bookkeeping(_archived_payload(row))
        payloads.append((row, payload))
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
        # The row carried the words: a nested digest of them goes too (finding: the checkpoint kept
        # ``_request_provenance.request_sha256`` and ``original_request_hash`` of an erased request).
        for column in names:
            if column not in json_columns or column in structural:
                continue
            current = updates.get(column, row[column])
            decoded = _decoded(current)
            if isinstance(decoded, (Mapping, list)):
                cleared = _without_digests(decoded)
                if cleared != decoded:
                    updates[column] = json.dumps(cleared)
        assignments = ", ".join(f"{_quoted(column)} = ?" for column in updates)
        conn.execute(f"UPDATE {_quoted(spec.name)} SET {assignments} WHERE rowid = ?",
                     (*updates.values(), row["_erasure_rowid"]))


_FORGET_ACTOR = "memory_forget"


def _erase_finalizations(rows: Iterable[Any]) -> None:
    """The A8 erasure of each finalization: transition and derivative sweep, both complete."""
    from core.finalization import erase_finalization_payload

    for row in rows:
        result = erase_finalization_payload(str(row["finalization_id"]), reason="chat forget",
                                            governance_actor=_FORGET_ACTOR)
        if not result.get("transitioned") or not result.get("sweep_complete"):
            raise RuntimeError("finalization erasure did not complete its derivative sweep")


def _unswept_finalizations(conn: Any, spec: _Table, owners: _Owners) -> list[Any]:
    """This chat's finalizations a forget erased whose derivative sweep has not completed (a retry
    finds no words left in the row itself, so it resumes the sweep from here)."""
    from core.finalization import AVAILABILITY_ERASED, erasure_sweep_status

    tables = _tables(conn)
    if "a7_governance_events" not in tables:
        return []
    erased_by_forget = {str(row[0]) for row in conn.execute(
        "SELECT DISTINCT finalization_id FROM a7_governance_events WHERE actor = ?", (_FORGET_ACTOR,))}
    if not erased_by_forget:
        return []
    rows, _ = _owned_rows(conn, spec, owners)
    return [row for row in rows if str(row["availability"] or "") == AVAILABILITY_ERASED
            and str(row["finalization_id"]) in erased_by_forget
            and not erasure_sweep_status(str(row["finalization_id"])).get("sweep_complete")]


def _without_command(value: Any, command: str) -> Any:
    """``value`` with every copy of the forget command's own text taken out of its strings."""
    if isinstance(value, Mapping):
        return {key: _without_command(item, command) for key, item in value.items()}
    if isinstance(value, list):
        return [_without_command(item, command) for item in value]
    if isinstance(value, str):
        return fold_forget_text(value).replace(command, " ")
    return value


def _erase_table(conn: Any, spec: _Table, owners: _Owners, token: str, command: str = "") -> tuple[int, bool]:
    """Returns (rows erased that held the words outside the forget command itself, whether a carrying
    row had to be left in place).

    The forget turn's own records (its request, its checkpoint) quote the command and are erased with
    the rest, but they are not something the chat had told before: counting them would answer "Forget
    applied" to a forget that found nothing.
    """
    matched, json_columns = _carrying(conn, spec, owners, token)
    folded_command = fold_forget_text(command).strip()
    # Counted: rows that name this chat (a row with no chat is erased under the derivative law, but it is
    # not something this chat was told) and that hold the words outside the forget command itself.
    counted = {row["_erasure_rowid"] for row in matched
               if owners.resolves_to_this_chat(spec, row)
               and (not folded_command or carries(_without_command(
                   _payload(row, spec.structural, json_columns), folded_command), token))}
    held: set[Any] = set()
    if spec.action == "a7":
        unswept = _unswept_finalizations(conn, spec, owners)
        conn.commit()
        _erase_finalizations([*matched, *unswept])
        if _unswept_finalizations(conn, spec, owners):
            raise RuntimeError("finalization derivative sweep is incomplete")
    elif spec.action == "goal":
        from core.goal_workflow import forget_goal_row, stop_goals_after_forget

        # A goal row is rewritten whole from what was read, so it is read again under the write lock: a
        # worker's save that committed after the first read is erased, not overwritten by the older row.
        conn.execute("BEGIN IMMEDIATE")
        matched, _ = _carrying(conn, spec, owners, token)
        stopped = [forget_goal_row(conn, row, token) for row in matched]
        conn.commit()
        stop_goals_after_forget([goal for goal in stopped if goal is not None])
    elif spec.action == "artifact":
        for row in matched:
            path = Path(str(row["file_path"] or "")).expanduser()
            if str(row["file_path"] or "").strip() and path.is_file():
                path.unlink()
            conn.execute(f"DELETE FROM {_quoted(spec.name)} WHERE rowid = ?", (row["_erasure_rowid"],))
        conn.commit()
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
    return len(counted - held), bool(held)


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


def forget_run_history(token: str, *, chat_id: str, erasures: dict[str, Any], command: str = "") -> None:
    """Erase ``token`` from this chat's run history in the runtime database and verify each table.

    ``command`` is the forget request's own text; rows that carry the words only inside it are erased
    but not counted as removed."""
    from core.memory.entries import _record_erasure_store

    try:
        databases = _runtime_databases()
    except Exception as exc:
        _LOG.warning("forget: run history databases could not be resolved: %s", type(exc).__name__)
        _record_erasure_store(erasures, "run_history", "incomplete")
        return
    _erase_databases(databases, _INVENTORY, token, chat_id=chat_id, erasures=erasures, command=command)
    _forget_pins(token, chat_id=chat_id, erasures=erasures)
    failed = any(name.startswith(STORE_PREFIX) and result["status"] == "incomplete"
                 for name, result in erasures.get("stores", {}).items())
    _record_erasure_store(erasures, "run_history", "incomplete" if failed else "verified")


def _erase_databases(databases: list[str], inventory: list[_Table] | tuple[_Table, ...], token: str, *, chat_id: str,
                     erasures: dict[str, Any], command: str = "") -> None:
    from core.memory.entries import _record_erasure_store
    from storage.db import get_connection

    for database in databases:
        conn = None
        try:
            conn = get_connection(database)
            tables = _tables(conn)
            owners = _Owners(conn, tables, chat_id,
                             resolve_links=any(spec.owner != _SESSION for spec in inventory))
            for spec in inventory:
                if spec.name not in tables:
                    continue
                store = STORE_PREFIX + spec.name
                try:
                    removed, held = _erase_table(conn, spec, owners, token, command)
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


# ── pinned messages ──────────────────────────────────────────────────────────

_PIN_BOOKKEEPING = frozenset({"id", "role", "ts", "pinned_at", "finalization_id"})


def _pin_files() -> list[Path]:
    """The pin store, its last-good backup and an interrupted save's temporary file."""
    from core.message_pins import pins_path

    path = pins_path()
    return [path, path.with_name(path.name + ".bak"), path.with_name(path.name + ".tmp")]


def _pins_owned(key: Any, chat_id: str) -> bool:
    from core.chat_session_identity import canonical_chat_session_id

    text = str(key or "").strip()
    return bool(text) and (text == chat_id or canonical_chat_session_id(text) == chat_id)


def _pin_carries(pin: Any, token: str) -> bool:
    if not isinstance(pin, Mapping):
        return carries(pin, token)
    return carries({key: value for key, value in pin.items() if key not in _PIN_BOOKKEEPING}, token)


def _read_pin_store(path: Path) -> dict[str, Any] | None:
    """A pin file's contents; None when the file is absent. Unreadable is an unverified carrier."""
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8") or "{}")
    if not isinstance(data, dict):
        raise ValueError("pin store is not a mapping")
    return data


def _pins_carrying(token: str, chat_id: str) -> int:
    count = 0
    for path in _pin_files():
        store = _read_pin_store(path)
        for key, pins in (store or {}).items():
            if _pins_owned(key, chat_id):
                count += sum(1 for pin in (pins if isinstance(pins, list) else [pins]) if _pin_carries(pin, token))
    return count


def _forget_pins(token: str, *, chat_id: str, erasures: dict[str, Any]) -> None:
    """Remove this chat's pinned messages that carry the words, from the store and its copies.

    A pin is the person's own snapshot of one message; the chat-delete law drops every pin of the chat
    and the A8 sweep drops an erased answer's pin. A pinned user message carrying forgotten words was
    still served after the forget (review of 9b78dac).
    """
    import os

    from core.memory.entries import _record_erasure_store
    from core.message_pins import _PINS_LOCK

    store_name = STORE_PREFIX + "message_pins"
    removed = 0
    try:
        with _PINS_LOCK:
            for index, path in enumerate(_pin_files()):
                store = _read_pin_store(path)
                if store is None:
                    continue
                changed = False
                for key in list(store):
                    pins = store[key]
                    if not _pins_owned(key, chat_id):
                        continue
                    items = pins if isinstance(pins, list) else [pins]
                    kept = [pin for pin in items if not _pin_carries(pin, token)]
                    if len(kept) != len(items):
                        if index == 0:
                            removed += len(items) - len(kept)
                        store[key] = kept
                        changed = True
                if changed:
                    payload = json.dumps({key: value for key, value in store.items() if value}, ensure_ascii=False)
                    staging = path.with_name(path.name + ".forget")
                    with staging.open("w", encoding="utf-8") as handle:
                        handle.write(payload)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(staging, path)
            if _pins_carrying(token, chat_id):
                raise RuntimeError("pinned messages could not be erased and verified")
        _record_erasure_store(erasures, store_name, "verified", removed)
    except Exception as exc:
        _LOG.warning("forget: %s could not be erased: %s", store_name, type(exc).__name__)
        _record_erasure_store(erasures, store_name, "incomplete")


# ── words written back after a forget ────────────────────────────────────────


def forgotten_tokens(chat_id: str) -> tuple[str, ...] | None:
    """The words this chat (or every chat) forgot, from the forget authority's ledger.

    Read without the authority's lock: the ledger is replaced whole, so a read sees one version.
    ``()`` when this home never forgot anything; None when the ledger exists and cannot be read, which a
    caller treats as "may carry forgotten words".
    """
    from core.memory import revocation

    scope = str(chat_id or "").strip()
    try:
        directory = revocation._directory()
        if not (directory / "ledger.json").exists():
            return ()
        data = revocation._read(directory)
    except Exception as exc:
        _LOG.warning("forgotten words could not be read: %s", type(exc).__name__)
        return None
    return tuple(sorted({row["token"] for row in data["operations"].values()
                         if row.get("kind", "forget") == "forget" and row["chat"] in ("", scope)}))


def carries_forgotten_words(chat_id: str, texts: Iterable[Any]) -> bool:
    """Whether a run-history write for this chat would put forgotten words back (unknown is yes)."""
    tokens = forgotten_tokens(chat_id)
    if tokens is None:
        return True
    if not tokens:
        return False
    # Ids, stamps and digests the runtime made are not words (a request id's hex never "is" a forgotten 12).
    values = [text for text in texts if text and not _runtime_made(text)]
    return any(text_carries(text, token) for text in values for token in tokens)


def goal_words_carry(value: Any, token: str) -> bool:
    """Whether part of a chat goal (a field, the plan, the binding) carries ``token``, read the way the forget
    sweep reads a ``chat_goals`` row: runtime-made identities and counters are not words."""
    return carries(_without_bookkeeping(_without_nested_identities(value, _GOAL_IDENTITIES)), token)


def goal_words_redacted(value: Any, token: str) -> Any:
    """Part of a chat goal with every leaf that carries ``token`` blanked, as the forget sweep blanks it."""
    return _redacted(value, token, _GOAL_IDENTITIES)


def resweep_forgotten_run_history(chat_id: str) -> int:
    """Erase this chat's forgotten words from the run history a turn wrote after the forget.

    A turn that was running when the forget came finalizes later: its checkpoint update merges the state
    and the final answer it still held, and they would put the words back (review of 9b78dac:
    ``finalize_runtime_checkpoint`` after "Forget applied" restored the word to ``final_response``). Run
    when a turn finalizes; covers this chat's rows only and never raises into the turn.
    """
    scope = str(chat_id or "").strip()
    if not scope:
        return 0
    tokens = forgotten_tokens(scope)
    if not tokens:
        if tokens is None:
            _LOG.warning("forgotten words could not be re-swept: the forget ledger is unreadable")
        return 0
    inventory = [spec for spec in _INVENTORY if spec.owner == _SESSION and spec.action in {"delete", "redact"}]
    removed = 0
    try:
        databases = _runtime_databases()
    except Exception as exc:
        _LOG.warning("forgotten words could not be re-swept: %s", type(exc).__name__)
        return 0
    for token in tokens:
        erasures: dict[str, Any] = {}
        _erase_databases(databases, inventory, token, chat_id=scope, erasures=erasures)
        stores = erasures.get("stores", {})
        removed += sum(int(result.get("removed") or 0) for result in stores.values())
        failed = sorted(name for name, result in stores.items() if result["status"] == "incomplete")
        if failed:
            _LOG.warning("forgotten words could not be re-swept from %s", ", ".join(failed))
    return removed


# ── verification ─────────────────────────────────────────────────────────────


def _without_signatures(value: Any) -> Any:
    """A runtime signature (base64 under ``signature``) is random letters and digits, not the person's words.

    Measured on CI after the 0.7.2 merge: a provider call's record keeps its base64 signature in two places, and
    "Forget 20." on the 20th ended "could not be verified ... provider invocation manifests" because one signature
    held "...kt20Uj..."; a two-digit number stands alone in about one signature in 1,250 and a three-letter word in
    one in 330, and every model call adds a record. Only that table's own signatures are left out; a ``signature``
    that holds anything but an ed25519 signature is still read.
    """
    if isinstance(value, Mapping):
        return {key: _without_signatures(item) for key, item in value.items()
                if not (key == "signature" and isinstance(item, str) and _SIGNATURE_RE.fullmatch(item))}
    if isinstance(value, list):
        return [_without_signatures(item) for item in value]
    return value


def _unclassified_payload(row: Mapping[str, Any], table: str = "") -> list[Any]:
    """A session-scoped table nobody classified: its words, without ids, stamps, counters and the runtime's signatures."""
    signed = table in _RUNTIME_SIGNED_TABLES
    words: list[Any] = []
    for key, value in (_without_signatures(dict(row)) if signed else dict(row)).items():
        if key in ROW_IDENTITIES or _runtime_made(value):
            continue
        if isinstance(value, (bytes, bytearray, memoryview)):
            raise ValueError("unclassified run history field has an opaque representation")
        if key.endswith("_json"):
            value = _decoded(value)
            value = _without_signatures(value) if signed else value
        words.append(_without_bookkeeping(_without_nested_identities(value, ROW_IDENTITIES)))
    return words


def verify_run_history(conn: Any, tables: set[str], chat_id: str, token: str, verify: Any) -> None:
    """Re-read every run-history table, and every other chat-scoped table no store owns.

    A chat-scoped table that is not classified anywhere is read too: if this chat's rows carry the
    forgotten words, the forget is reported incomplete rather than claimed while they stay.
    ``conn`` is the runtime default database; a separate runtime continuity file is read as well.
    """
    from storage.db import active_default_db_path, get_connection

    _verify_database(conn, tables, chat_id, token, verify)
    verify(STORE_PREFIX + "message_pins", lambda: bool(_pins_carrying(token, chat_id)))
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
            return bool(matched) or (spec.action == "a7" and bool(_unswept_finalizations(conn, spec, owners)))

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
            return any(carries(_unclassified_payload(row, table), token) for row in rows)

        verify(UNCLASSIFIED_PREFIX + table, unclassified)


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
