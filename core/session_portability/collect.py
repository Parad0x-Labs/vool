"""The collector: read ONE session's portable truth out of a VOOL home, through the owning
readers of each store — never by scraping the whole home.

Everything collected passes the export redaction pass. Attachment BYTES are the operator's own
authorized files and ship verbatim; every name, label and metadata field around them is scrubbed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.session_portability.redaction import RedactionReport, redact_record
from core.session_portability.schema import (
    IMPORTED_EVIDENCE_FRESHNESS_POLICY,
    SCOPE_NOTE,
    ExportCounts,
)


class SessionNotFound(LookupError):
    pass


class OverBound(LookupError):
    def __init__(self, bound: str, limit: int) -> None:
        super().__init__(f"session exceeds the {bound} bound ({limit})")
        self.bound = bound
        self.limit = limit


def _bounds() -> dict[str, int]:
    from core.session_portability import schema

    return dict(schema.BOUNDS)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def _db_rows(sql: str, params: tuple) -> list[dict[str, Any]]:
    from storage.db import get_connection

    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.row_factory = sqlite_row_factory
        rows = cursor.execute(sql, params).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def sqlite_row_factory(cursor, row):
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


def collect_session(session_id: str) -> tuple[dict[str, Any], dict[str, bytes], ExportCounts, int]:
    """Collect one session. Returns (payload, attachment_bytes, counts, redaction_count).

    Raises SessionNotFound when the home holds nothing for the id, OverBound when the session
    exceeds a cumulative pack bound.
    """
    from core.memory.entries import list_conversation_sessions, load_session_meta
    from core.memory.files import (
        conversation_log_path,
        session_summaries_path,
    )
    from core.operator_profile import export_profile

    bounds = _bounds()
    private_roots = _private_roots()
    report = RedactionReport()

    turns = [
        row
        for row in _load_jsonl(conversation_log_path())
        if str(row.get("session_id") or "") == session_id
    ]
    turns.sort(key=lambda row: (int(row.get("event_sequence") or 0), str(row.get("ts") or "")))
    if len(turns) > bounds["turns"]:
        raise OverBound("turns", bounds["turns"])

    session_header = next(
        (
            row
            for row in list_conversation_sessions(limit=1_000_000)
            if row.get("session_id") == session_id
        ),
        None,
    )
    meta_entry = (load_session_meta() or {}).get(session_id) or {}
    namespace = _namespace_row(session_id)
    if not turns and not session_header and not namespace:
        raise SessionNotFound(session_id)

    dialogue_session = _db_rows(
        "SELECT * FROM dialogue_sessions WHERE session_id = ?", (session_id,)
    )
    dialogue_turns = _db_rows(
        "SELECT turn_id, session_id, raw_input, normalized_input, reconstructed_input, "
        "speaker_role, topic_hints_json, reference_targets_json, understanding_confidence, "
        "quality_flags_json, created_at, request_id FROM dialogue_turns WHERE session_id = ? "
        "ORDER BY created_at",
        (session_id,),
    )
    if len(dialogue_turns) > bounds["dialogue_turns"]:
        raise OverBound("dialogue_turns", bounds["dialogue_turns"])

    summaries = [
        row
        for row in _load_jsonl(session_summaries_path())
        if str(row.get("session_id") or "") == session_id
    ][: bounds["summaries"]]

    # Demand/obligation terminal states, keyed by the turns' governing request ids.
    obligation_sets: list[dict[str, Any]] = []
    seen_sets: set[tuple[str, str]] = set()
    for row in turns:
        request_id = str(row.get("request_id") or "")
        if not request_id:
            continue
        for set_id, version in _obligation_sets_for_request(request_id):
            if (set_id, version) in seen_sets:
                continue
            seen_sets.add((set_id, version))
            snapshot = _db_rows(
                "SELECT set_id, version, status, snapshot_json, closure_hash, created_at "
                "FROM obligation_sets WHERE set_id = ? AND version = ?",
                (set_id, version),
            )
            for db_row in snapshot:
                try:
                    parsed = json.loads(db_row.pop("snapshot_json", "{}"))
                except (ValueError, TypeError):
                    parsed = {}
                merged = dict(parsed)
                merged.update(
                    {k: v for k, v in db_row.items() if k not in ("snapshot_json",)}
                )
                obligation_sets.append(merged)
    obligation_sets = obligation_sets[: bounds["obligation_sets"]]

    # Receipt trail. JSON columns are parsed here so the payload is clean typed JSON.
    tool_receipts = []
    for row in _db_rows(
        "SELECT receipt_key, session_id, checkpoint_id, tool_name, idempotency_key, "
        "arguments_json, execution_json, created_at, updated_at "
        "FROM runtime_tool_receipts WHERE session_id = ? ORDER BY updated_at",
        (session_id,),
    ):
        tool_receipts.append(
            {
                "receipt_key": row.get("receipt_key"),
                "session_id": row.get("session_id"),
                "checkpoint_id": row.get("checkpoint_id"),
                "tool_name": row.get("tool_name"),
                "idempotency_key": row.get("idempotency_key"),
                "arguments": _loads(row.get("arguments_json")),
                "execution": _loads(row.get("execution_json")),
                "created_at": row.get("created_at"),
                "updated_at": row.get("updated_at"),
            }
        )
    if len(tool_receipts) > bounds["tool_receipts"]:
        raise OverBound("tool_receipts", bounds["tool_receipts"])
    session_events = []
    for row in _db_rows(
        "SELECT session_id, seq, event_type, message, details_json, created_at "
        "FROM runtime_session_events WHERE session_id = ? ORDER BY seq",
        (session_id,),
    ):
        session_events.append(
            {
                "session_id": row.get("session_id"),
                "seq": row.get("seq"),
                "event_type": row.get("event_type"),
                "message": row.get("message"),
                "details": _loads(row.get("details_json")),
                "created_at": row.get("created_at"),
            }
        )
    if len(session_events) > bounds["session_events"]:
        raise OverBound("session_events", bounds["session_events"])

    model_provider = _model_provider_projection(session_events)[: bounds["model_provider_records"]]

    # Finalizations bound to the session's canonical turn ids.
    turn_ids = [str(row.get("turn_id") or "") for row in dialogue_turns if row.get("turn_id")]
    finalizations: list[dict[str, Any]] = []
    if turn_ids:
        marks = ",".join("?" for _ in turn_ids[: bounds["finalizations"]])
        finalizations = _db_rows(
            "SELECT finalization_id, turn_id, content_hash, status, delivery_status, created_at "
            f"FROM a7_finalizations WHERE turn_id IN ({marks})",
            tuple(turn_ids[: bounds["finalizations"]]),
        )

    # Attachments: metadata for every one the session owns; bytes only for authorized
    # (staged/bound) states. Released attachments are receipts by design — their bytes were
    # already destroyed by the owning authority.
    attachment_metadata, embedded, attachment_bytes, evidence_refs = _collect_attachments(
        session_id, bounds, report
    )

    profile_payload = export_profile("owner_local")
    profile_items = [
        item
        for item in profile_payload.get("items", [])
        if str(item.get("source_session_id") or "") == session_id
    ][: bounds["profile_items"]]

    payload: dict[str, Any] = {
        "schema": "vool.session_bundle",
        "schema_version": 1,
        "exported_at": _utcnow(),
        "scope_note": SCOPE_NOTE,
        "session": dict(session_header or {"session_id": session_id}),
        "session_meta": dict(meta_entry),
        "namespace": dict(namespace or {}),
        "dialogue_session": dialogue_session[0] if dialogue_session else {},
        "dialogue_turns": dialogue_turns,
        "turns": turns,
        "summaries": summaries,
        "obligations": obligation_sets,
        "receipts": {"tool_receipts": tool_receipts, "session_events": session_events},
        "model_provider": model_provider,
        "finalizations": finalizations,
        "attachment_metadata": attachment_metadata,
        "evidence": {
            "references": evidence_refs,
            "embedded": embedded,
            "freshness_policy": IMPORTED_EVIDENCE_FRESHNESS_POLICY,
        },
        "profile_refs": {"format": "vool.operator_profile.v1", "items": profile_items},
    }

    # Redaction is applied to the WHOLE payload at once: one pass, one report, no section missed.
    # The session id is restored afterwards verbatim: it is the collision key at import time and
    # carries no private content.
    payload = redact_record(payload, report, private_roots)
    payload.setdefault("session", {})
    payload["session"]["session_id"] = session_id

    counts = ExportCounts(
        turns=len(payload.get("turns") or []),
        summaries=len(payload.get("summaries") or []),
        obligation_sets=len(payload.get("obligations") or []),
        tool_receipts=len(payload["receipts"]["tool_receipts"]),
        session_events=len(payload["receipts"]["session_events"]),
        evidence_refs=len(payload["evidence"]["references"]),
        embedded_files=len(payload["evidence"]["embedded"]),
        profile_items=len(payload["profile_refs"]["items"]),
        finalizations=len(payload.get("finalizations") or []),
        model_provider_records=len(payload.get("model_provider") or []),
    )
    return payload, attachment_bytes, counts, report.total


def _private_roots() -> list[Path]:
    from core.runtime_paths import active_data_dir, active_vool_home, active_workspace_dir

    roots: list[Path] = []
    try:
        roots.append(Path(active_vool_home()))
        roots.append(Path(active_data_dir()))
        roots.append(Path(active_workspace_dir()))
    except Exception:
        pass
    return [root for root in roots if str(root) not in ("", "/")]


def _namespace_row(session_id: str) -> dict[str, Any] | None:
    rows = _db_rows(
        "SELECT chat_id, project_id, lifecycle_state, parent_chat_id, branch_turn, created_at, "
        "updated_at FROM context_namespaces WHERE chat_id = ?",
        (session_id,),
    )
    return rows[0] if rows else None


def _obligation_sets_for_request(request_id: str) -> list[tuple[str, str]]:
    from core.conductor.obligation_ledger import set_for_request

    try:
        found = set_for_request(request_id)
    except Exception:
        return []
    if not found:
        return []
    set_id, version = found
    return [(str(set_id), str(version))]


def _model_provider_projection(session_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in session_events:
        event_type = str(row.get("event_type") or "")
        if event_type != "turn.trace_completed" and not event_type.startswith("model."):
            continue
        details = row.get("details") if isinstance(row.get("details"), dict) else {}
        record = {
            "ts": row.get("created_at") or "",
            "event_type": event_type,
            "model": str(details.get("model") or ""),
            "provider_id": str(details.get("provider_id") or ""),
            "route": str(details.get("route") or ""),
            "request_id": str(details.get("request_id") or ""),
        }
        if any(record[k] for k in ("model", "provider_id", "route")):
            out.append(record)
    return out


def _collect_attachments(
    session_id: str, bounds: dict[str, int], report: RedactionReport
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, bytes], list[dict[str, Any]]]:
    from core.runtime_paths import active_data_dir

    stage_dir = Path(active_data_dir()) / "chat_attachments"
    metadata: list[dict[str, Any]] = []
    embedded: list[dict[str, Any]] = []
    evidence_refs: list[dict[str, Any]] = []
    attachment_bytes: dict[str, bytes] = {}
    total_bytes = 0
    if not stage_dir.is_dir():
        return metadata, embedded, attachment_bytes, evidence_refs
    for manifest_path in sorted(stage_dir.glob("*.json")):
        try:
            record = json.loads(manifest_path.read_text())
        except (OSError, ValueError):
            continue
        if str(record.get("session_id") or "") != session_id:
            continue
        record = redact_record(record, report, _private_roots())
        metadata.append(record)
        state = str(record.get("state") or "")
        sha = str(record.get("sha256") or "")
        if state in ("staged", "bound") and sha:
            bytes_path = stage_dir / f"{record.get('id')}.bin"
            if bytes_path.is_file():
                data = bytes_path.read_bytes()
                if len(data) + total_bytes > bounds["embedded_total_bytes"]:
                    raise OverBound("embedded_total_bytes", bounds["embedded_total_bytes"])
                if len(embedded) + 1 > bounds["embedded_files"]:
                    raise OverBound("embedded_files", bounds["embedded_files"])
                total_bytes += len(data)
                member = f"attachments/{sha}"
                attachment_bytes[member] = data
                embedded.append(
                    {
                        "attachment_id": record.get("id") or "",
                        "name": record.get("name") or "",
                        "kind": record.get("kind") or "",
                        "media_type": record.get("media_type") or "",
                        "size_bytes": len(data),
                        "sha256": sha,
                        "path": member,
                        "turn_id": record.get("turn_id") or "",
                    }
                )
                evidence_refs.append(
                    {
                        "kind": "chat_attachment",
                        "attachment_id": record.get("id") or "",
                        "sha256": sha,
                        "state": state,
                    }
                )
        elif sha:
            # Released/spent attachment: the receipt travels, the bytes do not exist any more.
            evidence_refs.append(
                {
                    "kind": "chat_attachment_receipt",
                    "attachment_id": record.get("id") or "",
                    "sha256": sha,
                    "state": state,
                }
            )
    return metadata, embedded, attachment_bytes, evidence_refs


def _loads(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return {}
    return value if value is not None else {}


def _utcnow() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
