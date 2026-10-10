"""Shared payload traversal and final, read-only verification of memory carriers.

The serialized writer row is the payload schema. Unknown fields are payload,
including nested keys; only explicitly named storage identities are excluded.
"""
from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

_SEPARATORS = re.compile(r"[\s\-_]+")

# These identify an owner/row, rather than a remembered value. Do not replace
# this list with suffix rules: a future field named answer_id can carry prose.
ROW_IDENTITIES = frozenset({
    "record_id", "event_id", "event_sequence", "request_id", "session_id",
    "chat_id", "chat_scope", "origin_chat_id", "origin_session_id", "project_id",
    "origin_project_id", "agent_id", "node_id", "occurrence_id", "receipt_id",
    "source_id", "heuristic_id", "created_at", "updated_at", "recorded_at", "ts",
    "last_confirmed_at", "erased_at", "erasure_salt", "erasure_digest", "content_hash",
    "body_sha256", "lineage_request_id", "superseded_record_id", "source_occurrence_id",
    "turn_id", "parent_turn_id", "archive_id", "topic_id", "previous_version_id",
    "version_id", "payload_hash", "record_hash", "transcript_head_hash", "page_hash",
    "content_ref", "admission_id", "principal", "pin_id", "_erasure_rowid",
})
NODE_IDENTITIES = ROW_IDENTITIES | frozenset({
    "timestamp", "embedding", "linked_node_ids", "base_importance", "access_count",
    "last_access", "valid_from", "valid_to", "embedding_backend",
})
SOURCE_IDENTITIES = ROW_IDENTITIES | frozenset({"source_sequence", "statement_at", "event_at"})
RECEIPT_IDENTITIES = SOURCE_IDENTITIES | frozenset({
    "writer", "model", "cost_calls", "cost_tokens",
})


def payload_strings(value: Any) -> tuple[str, ...]:
    """Every decoded leaf and mapping key, rejecting opaque/unreadable shapes."""
    if value is None:
        return ()
    if isinstance(value, bytes):
        return (value.decode("utf-8", errors="strict"),)
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Mapping):
        return tuple(part for key, item in value.items()
                     for part in (*payload_strings(key), *payload_strings(item)))
    if isinstance(value, (list, tuple)):
        return tuple(part for item in value for part in payload_strings(item))
    if isinstance(value, (int, float, bool)):
        return (str(value),)
    raise ValueError("memory carrier has an opaque payload")


def carries(value: Any, token: str) -> bool:
    needle = " ".join(_SEPARATORS.split(str(token).casefold())).strip()
    # Inspect the whole shape even after finding a match: malformed later
    # fields are still an unverified carrier, not an ignorable parse error.
    parts = payload_strings(value)
    return bool(needle) and any(needle in " ".join(_SEPARATORS.split(part.casefold())) for part in parts)


def row_payload(
    row: Mapping[str, Any] | Any, *, structural_fields: Sequence[str] = (),
    json_fields: Sequence[str] = (),
) -> dict[str, Any]:
    structural = frozenset(structural_fields)
    structured = frozenset(json_fields)
    payload = {}
    for key, value in dict(row).items():
        if key in structural:
            continue
        if key in structured or key.endswith("_json"):
            if value is not None and isinstance(value, (str, bytes, bytearray)):
                value = json.loads(value)
        payload[key] = value
    return payload


def payload_columns(schema: Sequence[Any], *, structural_fields: Sequence[str] = ()) -> list[str]:
    """Derive the closed payload field set from the current writer schema."""
    excluded = frozenset(structural_fields)
    return [str(column[1]) for column in schema if str(column[1]) not in excluded]


def schema_json_fields(schema: Sequence[Any]) -> tuple[str, ...]:
    return tuple(str(column[1]) for column in schema
                 if "JSON" in str(column[2]).upper() or str(column[1]).endswith("_json"))


def session_metadata_carriers() -> tuple[Path, ...]:
    """The metadata writer's document and every recovery/staging copy of it.

    This is deliberately a filename-family inventory, not a list of known
    suffixes: a writer adding another backup or temporary suffix adds a
    carrier to both the sweep and its final verification automatically.
    """
    from core.memory.files import chat_session_meta_path

    path = chat_session_meta_path()
    try:
        copies = sorted(item for item in path.parent.iterdir()
                        if item.name.startswith(path.name + "."))
    except FileNotFoundError:
        copies = []
    return (path, *copies)


def _read_session_metadata(path: Path) -> dict[str, dict[str, Any]]:
    """Decode the complete writer document without dropping unknown fields."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8") or "{}")
    except FileNotFoundError:
        return {}
    if not isinstance(payload, dict) or any(not isinstance(row, dict) for row in payload.values()):
        raise ValueError("session metadata has an invalid owner or payload shape")
    return payload


def read_session_metadata(path: Path) -> dict[str, dict[str, Any]]:
    """Public metadata reads withhold withdrawn and pending rows."""
    from core.memory.revocation import _carrier_authority_home, authorize_payload

    return {chat: row for chat, row in _read_session_metadata(path).items()
            if authorize_payload(row, chat, _carrier_authority_home(path))}


def session_metadata_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    """All fields actually emitted by any metadata writer are carriers.

    Only the outer document key identifies the owning chat. Names such as
    ``project_id`` inside its row can still carry a user-provided value.
    """
    return dict(row)


def prune_payload(value: Any, token: str) -> Any:
    """Remove matching leaves/keys while preserving unrelated nested values."""
    if isinstance(value, Mapping):
        return {key: prune_payload(item, token) for key, item in value.items()
                if not carries(key, token)
                and (isinstance(item, (Mapping, list, tuple)) or not carries(item, token))}
    if isinstance(value, (list, tuple)):
        return [prune_payload(item, token) for item in value
                if isinstance(item, (Mapping, list, tuple)) or not carries(item, token)]
    return None if carries(value, token) else value


def _quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _scope(row: Mapping[str, Any]) -> str:
    return str(row.get("origin_chat_id") or row.get("session_id") or row.get("chat_id")
               or row.get("chat_scope") or row.get("origin_session_id") or "")


def verify_forget_stores(
    chat_id: str, token: str, *, owner_memories: Mapping[str, Any], erasures: dict[str, Any],
) -> None:
    """Re-read every carrier after the last sweep; never repair a late copy.

    Caller holds the common writer fence until the operation is completed.
    Each failed/unreadable carrier remains named incomplete in ``erasures``.
    """
    from core.memory.entries import _load_erasure_rows, _record_erasure_store
    from core.memory.files import (
        conversation_log_path,
        memory_entries_path,
        memory_path,
        operator_dense_profile_path,
        session_summaries_path,
        user_heuristics_path,
    )

    def verify(name: str, reader: Any) -> None:
        try:
            retained = bool(reader())
            _record_erasure_store(erasures, name, "incomplete" if retained else "verified")
        except Exception:
            _record_erasure_store(erasures, name, "incomplete")

    def file_rows(path: Path) -> bool:
        rows = _load_erasure_rows(path)
        return any(carries(row_payload(row, structural_fields=ROW_IDENTITIES), token)
                   for row in rows if _scope(row) in {"", chat_id})

    for name, path in (
        ("structured_entries", memory_entries_path()),
        ("structured_summaries", session_summaries_path()),
        ("session_summaries", session_summaries_path()),
        ("conversation_log", conversation_log_path()),
        ("user_heuristics", user_heuristics_path()),
    ):
        verify(name, lambda path=path: file_rows(path))

    def text_file(path: Path, *, structured: bool = False) -> bool:
        try:
            value = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return False
        return carries(json.loads(value) if structured else value, token)

    def mirror() -> bool:
        from core.memory.entries import _text_is_erased, sanitize_fact

        # A foreign chat's bound mirror line is not this operation's copy.
        owners = {str(row.get("record_id") or ""): _scope(row)
                  for row in _load_erasure_rows(memory_entries_path())}
        try:
            lines = memory_path().read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return False
        for line in lines:
            if not carries(line, token):
                continue
            match = re.search(r"<!--\s*mem:([A-Za-z0-9_.:-]+)\s*-->\s*$", line)
            if match and owners.get(match.group(1), "") not in {"", chat_id}:
                continue
            if not match:
                # The writer preserves unrelated manual lines by exact
                # erasure identity. Verify that same payload and identity;
                # a substring of a different fact is not the erased row.
                text = re.sub(r"^\s*-\s*(?:\[\d{4}-\d{2}-\d{2}\]\s*)?", "", line)
                if not _text_is_erased(sanitize_fact(text)):
                    continue
            return True
        return False

    verify("memory_mirror", mirror)
    verify("operator_dense_profile", lambda: text_file(operator_dense_profile_path(), structured=True))

    def session_metadata() -> bool:
        return any(carries(session_metadata_payload(_read_session_metadata(path).get(chat_id, {})), token)
                   for path in session_metadata_carriers())

    verify("chat_session_metadata", session_metadata)

    from core.vool_memory import _node_forget_payload, _node_matches_session, _row_to_node

    for owner, memory in owner_memories.items():
        prefix = owner + "_"

        def semantic(mem: Any = memory) -> bool:
            rows = mem._conn.execute("SELECT * FROM memory_nodes WHERE agent_id = ?", (mem.agent_id,)).fetchall()
            json_fields = schema_json_fields(mem._conn.execute("PRAGMA table_info(memory_nodes)").fetchall())
            return any(_node_matches_session(_row_to_node(row), chat_id)
                       and carries(_node_forget_payload(row, json_fields=json_fields), token) for row in rows)

        verify(prefix + "semantic_nodes", semantic)

        def sources(mem: Any = memory) -> bool:
            rows = mem._conn.execute(
                "SELECT * FROM source_occurrences WHERE agent_id = ? AND chat_scope = ?",
                (mem.agent_id, chat_id),
            ).fetchall()
            json_fields = schema_json_fields(mem._conn.execute("PRAGMA table_info(source_occurrences)").fetchall())
            return any(carries(row_payload(row, structural_fields=SOURCE_IDENTITIES, json_fields=json_fields), token) for row in rows)

        verify(prefix + "source_occurrences", sources)

        def receipts(mem: Any = memory) -> bool:
            from core.memory_receipts import receipt_payload_for_erasure

            if not mem._conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'memory_receipts'").fetchone():
                return False
            rows = mem._conn.execute(
                "SELECT * FROM memory_receipts WHERE agent_id = ? AND chat_scope = ?", (mem.agent_id, chat_id),
            ).fetchall()
            json_fields = schema_json_fields(mem._conn.execute("PRAGMA table_info(memory_receipts)").fetchall())
            for row in rows:
                source = mem._conn.execute(
                    "SELECT body FROM source_occurrences WHERE occurrence_id = ? AND agent_id = ?",
                    (row["occurrence_id"], mem.agent_id),
                ).fetchone()
                if carries(receipt_payload_for_erasure(row, source_body=source[0] if source else None,
                                                      json_fields=json_fields), token):
                    return True
            return False

        verify(prefix + "memory_receipts", receipts)

        def nodes_index(mem: Any = memory) -> bool:
            rows = mem._conn.execute(
                "SELECT f.content AS indexed_content, f.keywords AS indexed_keywords, n.* "
                "FROM memory_fts f LEFT JOIN memory_nodes n ON n.node_id = f.node_id WHERE f.agent_id = ?",
                (mem.agent_id,),
            ).fetchall()
            return any(carries([row["indexed_content"], row["indexed_keywords"]], token)
                       and (row["node_id"] is None or _node_matches_session(_row_to_node(row), chat_id)) for row in rows)

        verify(prefix + "memory_fts", nodes_index)

        def source_index(mem: Any = memory) -> bool:
            rows = mem._conn.execute(
                "SELECT f.body, o.chat_scope FROM source_fts f LEFT JOIN source_occurrences o "
                "ON o.occurrence_id = f.occurrence_id WHERE o.chat_scope = ? OR o.occurrence_id IS NULL",
                (chat_id,),
            ).fetchall()
            return any(carries(row["body"], token) for row in rows)

        verify(prefix + "source_fts", source_index)

        def vectors(mem: Any = memory) -> bool:
            return mem._conn.execute(
                "SELECT 1 FROM source_embeddings e LEFT JOIN source_occurrences o "
                "ON o.occurrence_id = e.occurrence_id WHERE e.agent_id = ? AND "
                "(o.occurrence_id IS NULL OR (o.chat_scope = ? AND "
                "(o.body = '' OR e.body_sha256 != o.body_sha256))) LIMIT 1",
                (mem.agent_id, chat_id),
            ).fetchone() is not None

        verify(prefix + "source_embeddings", vectors)
        verify(prefix + "memory_blocks", lambda mem=memory: any(
            carries(row_payload(row, structural_fields=ROW_IDENTITIES), token)
            for row in mem._conn.execute("SELECT * FROM memory_blocks WHERE agent_id = ?", (mem.agent_id,)).fetchall()
        ))

    from storage.db import get_connection

    conn = None
    try:
        conn = get_connection()
        tables = {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        targets = {"learning_shards", "local_tasks", "response_feedback", "context_capsule_versions"}
        targets.update(name for name in tables if name.startswith("dialogue_")
                       or any(part in name for part in ("packet", "cache", "snapshot")))
        for table in sorted(targets & tables):
            def sql_rows(table: str = table) -> bool:
                schema = conn.execute(f"PRAGMA table_info({_quoted(table)})").fetchall()
                names = {str(column[1]) for column in schema}
                scope = next((name for name in ("session_id", "chat_id", "origin_session_id", "chat_scope") if name in names), None)
                suffix = (f" WHERE {_quoted(scope)} = ? OR {_quoted(scope)} IS NULL OR {_quoted(scope)} = ''"
                          if scope else "")
                params = (chat_id,) if scope else ()
                structured = [str(column[1]) for column in schema if "JSON" in str(column[2]).upper()]
                rows = conn.execute(f"SELECT * FROM {_quoted(table)}{suffix}", params).fetchall()
                if any(isinstance(value, (bytes, bytearray, memoryview)) and value
                       for row in rows for column, value in dict(row).items() if column not in structured):
                    raise ValueError("derivative payload has an opaque representation")
                return any(carries(row_payload(row, structural_fields=ROW_IDENTITIES, json_fields=structured), token) for row in rows)

            verify(table, sql_rows)
        _verify_pages(conn, tables, chat_id, token, verify)

        # The swarm learning dataset is a retained store in the same database:
        # its full serialized payload answers to this operation like every
        # other carrier, by the same owned-scope discipline its erasure uses.
        from storage.swarm_memory import _sniffed_retained_copies

        verify("swarm_sniffed_context", lambda: _sniffed_retained_copies(token, chat_id))
    except Exception:
        _record_erasure_store(erasures, "final_derivative_verification", "incomplete")
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                _record_erasure_store(erasures, "final_derivative_connection", "incomplete")

    from core.evidence_kernel.receipts import _ledger_dir, _receipt_payload_for_erasure, _sha256

    def ledgers() -> bool:
        root = _ledger_dir()
        try:
            kinds = list(root.iterdir())
        except FileNotFoundError:
            return False
        return any(carries(_receipt_payload_for_erasure(row), token)
                   for kind in kinds if kind.is_dir()
                   for row in _load_erasure_rows(kind / (_sha256(chat_id)[:24] + ".jsonl")))

    verify("packet_receipt_ledgers", ledgers)
    from core.runtime_paths import data_path

    roots = [("bootstrap_snapshots", data_path("bootstrap")), ("relay_mirror_snapshots", Path("relay_mirror"))]
    configured = str(os.environ.get("VOOL_MIRROR_DATA_DIR") or "").strip()
    if configured:
        roots.append(("configured_mirror_snapshots", Path(configured)))
    for name, root in roots:
        def snapshots(root: Path = root) -> bool:
            try:
                paths = list(root.iterdir())
            except FileNotFoundError:
                return False
            return any(carries(json.loads(path.read_text(encoding="utf-8")), token)
                       for path in paths if path.suffix.lower() == ".json")

        verify(name, snapshots)


def _verify_pages(conn: Any, tables: set[str], chat_id: str, token: str, verify: Any) -> None:
    from storage.cas import get_bytes

    if "context_pages" not in tables:
        return

    def pages() -> bool:
        owned = "session_id = ?"
        params = [chat_id]
        if "turn_context_admissions" in tables:
            owned += " OR page_hash IN (SELECT page_hash FROM turn_context_admissions WHERE session_id = ? AND source_kind = 'chat_exchange')"
            params.append(chat_id)
        rows = conn.execute(
            "SELECT * FROM context_pages WHERE kind = 'turn_context' AND source = 'turn-context:chat_exchange' "
            "AND (" + owned + ")", params,
        ).fetchall()
        for row in rows:
            if carries(row_payload(row, structural_fields=ROW_IDENTITIES), token):
                return True
            ref = str(row["content_ref"] or "")
            data = get_bytes(ref) if ref else None
            if ref and data is None and not row["erased"]:
                raise ValueError("context content is unavailable for verification")
            if data is not None and carries(data, token):
                return True
        if "turn_context_admissions" in tables:
            rows = conn.execute(
                "SELECT * FROM turn_context_admissions WHERE session_id = ? AND source_kind = 'chat_exchange'", (chat_id,),
            ).fetchall()
            if any(carries(row_payload(row, structural_fields=ROW_IDENTITIES), token) for row in rows):
                return True
        return False

    verify("context_pages", pages)
