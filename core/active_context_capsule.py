"""Versioned, source-backed chat capsules.

Capsules are a compact shadow representation of a chat. They never replace the
raw transcript, never ingest assistant guesses, and are not injected into model
prompts until the shadow evaluation gate is explicitly promoted.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from core.context_namespace import load_chat_namespace
from core.runtime_continuity import load_tool_receipt
from core.secret_redaction import redact_secrets
from storage.db import get_connection

CapsuleMode = Literal["shadow", "active", "rejected"]
ClaimSource = Literal["transcript_user", "verified_action", "receipt"]

_CONSTRAINT_RE = re.compile(
    r"\b(?:must|never|only|do not|don't|cannot|can't|required|constraint)\b",
    re.IGNORECASE,
)
_DECISION_RE = re.compile(
    r"\b(?:we decided|i decided|we will|i will use|use .+ instead|"
    r"switch(?:ed)? to|from now on)\b",
    re.IGNORECASE,
)
_ACTION_OUTCOME_BY_TYPE = {
    "tool_executed": "succeeded",
    "tool_failed": "failed",
    "tool_preview": "pending_approval",
}


@dataclass(frozen=True)
class CapsuleClaim:
    text: str
    source_type: ClaimSource
    source_id: str
    content_hash: str


@dataclass(frozen=True)
class ActiveContextCapsule:
    version_id: str
    version_number: int
    chat_id: str
    project_id: str
    previous_version_id: str
    mode: CapsuleMode
    objective: CapsuleClaim | None
    decisions: tuple[CapsuleClaim, ...]
    constraints: tuple[CapsuleClaim, ...]
    system_state: tuple[CapsuleClaim, ...]
    unresolved_work: tuple[CapsuleClaim, ...]
    verified_actions: tuple[CapsuleClaim, ...]
    receipt_references: tuple[str, ...]
    transcript_head_hash: str
    payload_hash: str
    record_hash: str
    created_at: str


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _content_hash(text: str) -> str:
    return hashlib.sha256(str(text or "").encode()).hexdigest()


def _clean_text(text: Any, *, limit: int = 600) -> str:
    redacted = redact_secrets(" ".join(str(text or "").split())).strip()
    return redacted[: max(1, int(limit))]


def _claim(
    text: Any,
    *,
    source_type: ClaimSource,
    source_id: str,
) -> CapsuleClaim | None:
    clean = _clean_text(text)
    clean_source = str(source_id or "").strip()
    if not clean or not clean_source:
        return None
    return CapsuleClaim(
        text=clean,
        source_type=source_type,
        source_id=clean_source,
        content_hash=_content_hash(clean),
    )


def ensure_active_capsule_schema() -> None:
    conn = get_connection()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS context_capsule_versions (
                version_id TEXT PRIMARY KEY,
                version_number INTEGER NOT NULL DEFAULT 1,
                chat_id TEXT NOT NULL,
                project_id TEXT NOT NULL DEFAULT '',
                previous_version_id TEXT NOT NULL DEFAULT '',
                mode TEXT NOT NULL,
                capsule_json TEXT NOT NULL,
                transcript_head_hash TEXT NOT NULL DEFAULT '',
                payload_hash TEXT NOT NULL,
                record_hash TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY(chat_id) REFERENCES context_namespaces(chat_id),
                CHECK (mode IN ('shadow', 'active', 'rejected')),
                UNIQUE(chat_id, version_number)
            );

            CREATE INDEX IF NOT EXISTS idx_context_capsule_versions_chat
                ON context_capsule_versions(chat_id, created_at, version_id);

            CREATE TABLE IF NOT EXISTS context_capsule_heads (
                chat_id TEXT PRIMARY KEY,
                version_id TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(chat_id) REFERENCES context_namespaces(chat_id),
                FOREIGN KEY(version_id)
                    REFERENCES context_capsule_versions(version_id)
            );
            """
        )
        columns = {
            str(row["name"])
            for row in conn.execute(
                "PRAGMA table_info(context_capsule_versions)"
            ).fetchall()
        }
        record_hash_added = False
        if "version_number" not in columns:
            conn.execute(
                "ALTER TABLE context_capsule_versions "
                "ADD COLUMN version_number INTEGER NOT NULL DEFAULT 1"
            )
        if "transcript_head_hash" not in columns:
            conn.execute(
                "ALTER TABLE context_capsule_versions "
                "ADD COLUMN transcript_head_hash TEXT NOT NULL DEFAULT ''"
            )
        if "record_hash" not in columns:
            conn.execute(
                "ALTER TABLE context_capsule_versions "
                "ADD COLUMN record_hash TEXT NOT NULL DEFAULT ''"
            )
            record_hash_added = True
        if record_hash_added:
            _backfill_legacy_capsule_record_hashes(conn)
        elif conn.execute(
            """
            SELECT 1
            FROM context_capsule_versions
            WHERE record_hash = ''
            LIMIT 1
            """
        ).fetchone() is not None:
            raise ValueError("context capsule record hash is missing")
        conn.executescript(
            """
            CREATE TRIGGER IF NOT EXISTS context_capsule_versions_no_update
            BEFORE UPDATE ON context_capsule_versions
            BEGIN
                SELECT RAISE(ABORT, 'context capsule versions are immutable');
            END;

            CREATE TRIGGER IF NOT EXISTS context_capsule_versions_no_delete
            BEFORE DELETE ON context_capsule_versions
            BEGIN
                SELECT RAISE(ABORT, 'context capsule versions are immutable');
            END;
            """
        )
        conn.commit()
    finally:
        conn.close()


def _claim_from_dict(payload: Any) -> CapsuleClaim | None:
    if not isinstance(payload, dict):
        return None
    try:
        return CapsuleClaim(
            text=str(payload["text"]),
            source_type=str(payload["source_type"]),  # type: ignore[arg-type]
            source_id=str(payload["source_id"]),
            content_hash=str(payload["content_hash"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _claims_from_list(payload: Any) -> tuple[CapsuleClaim, ...]:
    claims: list[CapsuleClaim] = []
    for item in list(payload or []):
        claim = _claim_from_dict(item)
        if claim is not None:
            claims.append(claim)
    return tuple(claims)


def _stable_json_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return _content_hash(encoded)


def _capsule_record_hash(
    *,
    version_id: str,
    version_number: int,
    chat_id: str,
    project_id: str,
    previous_version_id: str,
    mode: CapsuleMode,
    capsule_json: str,
    transcript_head_hash: str,
    payload_hash: str,
    created_at: str,
) -> str:
    return _stable_json_hash(
        {
            "schema": "context_capsule_record_v2",
            "version_id": str(version_id),
            "version_number": int(version_number),
            "chat_id": str(chat_id),
            "project_id": str(project_id),
            "previous_version_id": str(previous_version_id),
            "mode": str(mode),
            "capsule_json": str(capsule_json),
            "transcript_head_hash": str(transcript_head_hash),
            "payload_hash": str(payload_hash),
            "created_at": str(created_at),
        }
    )


def _backfill_legacy_capsule_record_hashes(conn: Any) -> None:
    rows = conn.execute(
        """
        SELECT *
        FROM context_capsule_versions
        ORDER BY chat_id, version_number, created_at, version_id
        """
    ).fetchall()
    by_chat: dict[str, list[Any]] = {}
    for row in rows:
        by_chat.setdefault(str(row["chat_id"]), []).append(row)

    updates: list[tuple[str, str]] = []
    for chat_id, chat_rows in by_chat.items():
        previous_version_id = ""
        for expected_version, row in enumerate(chat_rows, start=1):
            encoded = str(row["capsule_json"])
            mode = str(row["mode"])
            row_previous = str(row["previous_version_id"] or "")
            if (
                _content_hash(encoded) != str(row["payload_hash"])
                or mode not in {"shadow", "active", "rejected"}
                or int(row["version_number"] or 1) != expected_version
                or row_previous != previous_version_id
            ):
                raise ValueError("legacy context capsule chain is corrupt")
            try:
                payload = json.loads(encoded)
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                raise ValueError(
                    "legacy context capsule payload is corrupt"
                ) from exc
            if not isinstance(payload, dict):
                raise ValueError("legacy context capsule payload is corrupt")
            updates.append(
                (
                    _capsule_record_hash(
                        version_id=str(row["version_id"]),
                        version_number=expected_version,
                        chat_id=chat_id,
                        project_id=str(row["project_id"] or ""),
                        previous_version_id=row_previous,
                        mode=mode,  # type: ignore[arg-type]
                        capsule_json=encoded,
                        transcript_head_hash=str(
                            row["transcript_head_hash"] or ""
                        ),
                        payload_hash=str(row["payload_hash"]),
                        created_at=str(row["created_at"]),
                    ),
                    str(row["version_id"]),
                )
            )
            previous_version_id = str(row["version_id"])
        head = conn.execute(
            """
            SELECT version_id
            FROM context_capsule_heads
            WHERE chat_id = ?
            LIMIT 1
            """,
            (chat_id,),
        ).fetchone()
        if (
            head is None
            or not chat_rows
            or str(head["version_id"]) != str(chat_rows[-1]["version_id"])
        ):
            raise ValueError("legacy context capsule head is corrupt")

    if not updates:
        return
    conn.execute(
        "DROP TRIGGER IF EXISTS context_capsule_versions_no_update"
    )
    conn.executemany(
        """
        UPDATE context_capsule_versions
        SET record_hash = ?
        WHERE version_id = ?
        """,
        updates,
    )


def _capsule_from_row(row: Any) -> ActiveContextCapsule | None:
    if row is None:
        return None
    encoded = str(row["capsule_json"])
    if _content_hash(encoded) != str(row["payload_hash"]):
        return None
    mode = str(row["mode"])
    if mode not in {"shadow", "active", "rejected"}:
        return None
    record_hash = str(row["record_hash"] or "")
    if not record_hash or record_hash != _capsule_record_hash(
        version_id=str(row["version_id"]),
        version_number=int(row["version_number"] or 1),
        chat_id=str(row["chat_id"]),
        project_id=str(row["project_id"] or ""),
        previous_version_id=str(row["previous_version_id"] or ""),
        mode=mode,  # type: ignore[arg-type]
        capsule_json=encoded,
        transcript_head_hash=str(row["transcript_head_hash"] or ""),
        payload_hash=str(row["payload_hash"]),
        created_at=str(row["created_at"]),
    ):
        return None
    try:
        payload = json.loads(encoded)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    return ActiveContextCapsule(
        version_id=str(row["version_id"]),
        version_number=int(row["version_number"] or 1),
        chat_id=str(row["chat_id"]),
        project_id=str(row["project_id"] or ""),
        previous_version_id=str(row["previous_version_id"] or ""),
        mode=mode,  # type: ignore[arg-type]
        objective=_claim_from_dict(payload.get("objective")),
        decisions=_claims_from_list(payload.get("decisions")),
        constraints=_claims_from_list(payload.get("constraints")),
        system_state=_claims_from_list(payload.get("system_state")),
        unresolved_work=_claims_from_list(payload.get("unresolved_work")),
        verified_actions=_claims_from_list(payload.get("verified_actions")),
        receipt_references=tuple(
            str(item)
            for item in list(payload.get("receipt_references") or [])
            if str(item).strip()
        ),
        transcript_head_hash=str(row["transcript_head_hash"] or ""),
        payload_hash=str(row["payload_hash"]),
        record_hash=record_hash,
        created_at=str(row["created_at"]),
    )


def _validated_capsule_chain(
    conn: Any,
    chat_id: str,
    *,
    require_head: bool = True,
) -> tuple[tuple[ActiveContextCapsule, ...], bool]:
    rows = conn.execute(
        """
        SELECT *
        FROM context_capsule_versions
        WHERE chat_id = ?
        ORDER BY version_number, created_at, version_id
        """,
        (chat_id,),
    ).fetchall()
    capsules: list[ActiveContextCapsule] = []
    previous: ActiveContextCapsule | None = None
    for expected_version, row in enumerate(rows, start=1):
        capsule = _capsule_from_row(row)
        if (
            capsule is None
            or capsule.chat_id != chat_id
            or capsule.version_number != expected_version
            or (
                expected_version == 1
                and bool(capsule.previous_version_id)
            )
            or (
                expected_version > 1
                and (
                    previous is None
                    or capsule.previous_version_id != previous.version_id
                    or previous.chat_id != capsule.chat_id
                    or previous.version_number + 1 != capsule.version_number
                )
            )
        ):
            return (), False
        capsules.append(capsule)
        previous = capsule

    head_row = conn.execute(
        """
        SELECT version_id
        FROM context_capsule_heads
        WHERE chat_id = ?
        LIMIT 1
        """,
        (chat_id,),
    ).fetchone()
    if not require_head:
        return tuple(capsules), True
    if not capsules:
        return ((), head_row is None)
    if head_row is None or str(head_row["version_id"]) != capsules[-1].version_id:
        return (), False
    return tuple(capsules), True


def load_latest_capsule(chat_id: str) -> ActiveContextCapsule | None:
    clean_chat = str(chat_id or "").strip()
    if not clean_chat:
        return None
    ensure_active_capsule_schema()
    conn = get_connection()
    try:
        capsules, valid = _validated_capsule_chain(conn, clean_chat)
    finally:
        conn.close()
    if not valid or not capsules:
        return None
    return capsules[-1]


def load_active_capsule(chat_id: str) -> ActiveContextCapsule | None:
    capsule = load_latest_capsule(chat_id)
    if capsule is None or capsule.mode != "active":
        return None
    return capsule


def list_capsule_versions(
    chat_id: str,
    *,
    limit: int = 100,
) -> tuple[ActiveContextCapsule, ...]:
    clean_chat = str(chat_id or "").strip()
    if not clean_chat:
        return ()
    ensure_active_capsule_schema()
    conn = get_connection()
    try:
        capsules, valid = _validated_capsule_chain(conn, clean_chat)
    finally:
        conn.close()
    if not valid:
        return ()
    return capsules[: max(1, int(limit))]


def _unique_claims(
    claims: list[CapsuleClaim],
    *,
    limit: int,
) -> tuple[CapsuleClaim, ...]:
    selected: list[CapsuleClaim] = []
    seen: set[str] = set()
    for claim in reversed(claims):
        if claim.content_hash in seen:
            continue
        seen.add(claim.content_hash)
        selected.append(claim)
        if len(selected) >= max(1, int(limit)):
            break
    selected.reverse()
    return tuple(selected)


def _verified_action_record(
    receipt_id: str,
    *,
    chat_id: str,
    project_id: str,
) -> dict[str, Any] | None:
    clean_receipt_id = str(receipt_id or "").strip()
    if not clean_receipt_id:
        return None
    receipt = load_tool_receipt(clean_receipt_id)
    if (
        not isinstance(receipt, dict)
        or str(receipt.get("receipt_key") or "").strip() != clean_receipt_id
        or str(receipt.get("session_id") or "").strip() != chat_id
    ):
        return None
    execution = receipt.get("execution")
    action_record = (
        dict(execution.get("action_record") or {})
        if isinstance(execution, dict)
        else {}
    )
    origin = action_record.get("origin")
    parameters = action_record.get("parameters")
    result = action_record.get("result")
    action_type = str(action_record.get("action_type") or "").strip()
    if (
        action_record.get("schema") != "tool_action_receipt_v1"
        or str(action_record.get("receipt_id") or "").strip()
        != clean_receipt_id
        or not str(action_record.get("action_id") or "").strip()
        or action_type not in _ACTION_OUTCOME_BY_TYPE
        or not str(action_record.get("tool_name") or "").strip()
        or not isinstance(origin, dict)
        or str(origin.get("chat_id") or "").strip() != chat_id
        or str(origin.get("project_id") or "").strip() != project_id
        or not isinstance(parameters, dict)
        or not isinstance(result, dict)
        or not str(result.get("summary") or "").strip()
        or str(result.get("outcome") or "").strip()
        != _ACTION_OUTCOME_BY_TYPE[action_type]
    ):
        return None
    try:
        datetime.fromisoformat(str(action_record.get("occurred_at") or ""))
    except (TypeError, ValueError):
        return None
    if (
        str(receipt.get("tool_name") or "").strip()
        != str(action_record["tool_name"]).strip()
        or str(receipt.get("idempotency_key") or "").strip()
        != str(action_record["action_id"]).strip()
        or _stable_json_hash(receipt.get("arguments") or {})
        != _stable_json_hash(parameters)
        or str(action_record.get("parameters_hash") or "")
        != _stable_json_hash(parameters)
        or str(action_record.get("result_hash") or "")
        != _stable_json_hash(result)
    ):
        return None
    hashed_record = {
        key: value
        for key, value in action_record.items()
        if key not in {"occurred_at", "record_hash"}
    }
    if str(action_record.get("record_hash") or "") != _stable_json_hash(
        hashed_record
    ):
        return None
    return action_record


def create_shadow_capsule(
    *,
    chat_id: str,
    project_id: str = "",
    user_text: str,
    verified_actions: list[dict[str, Any]] | None = None,
    receipt_references: list[str] | None = None,
    request_id: str = "",
) -> ActiveContextCapsule:
    namespace = load_chat_namespace(chat_id)
    if namespace is None:
        raise ValueError("chat namespace does not exist")
    if namespace.lifecycle_state != "active":
        raise ValueError("capsules require an active chat")
    requested_project = str(project_id or "").strip()
    if requested_project and requested_project != namespace.project_id:
        raise ValueError("capsule project does not match chat namespace")
    source_id = "turn:" + _content_hash(user_text)[:24]
    objective = _claim(
        user_text,
        source_type="transcript_user",
        source_id=source_id,
    )
    new_decisions: list[CapsuleClaim] = []
    new_constraints: list[CapsuleClaim] = []
    for sentence in re.split(r"(?<=[.!?;])\s+|\n+", str(user_text or "")):
        if _DECISION_RE.search(sentence):
            claim = _claim(
                sentence,
                source_type="transcript_user",
                source_id=source_id,
            )
            if claim is not None:
                new_decisions.append(claim)
        if _CONSTRAINT_RE.search(sentence):
            claim = _claim(
                sentence,
                source_type="transcript_user",
                source_id=source_id,
            )
            if claim is not None:
                new_constraints.append(claim)

    ensure_active_capsule_schema()
    conn = get_connection()
    try:
        # Serialize the read/build/write sequence. Loading the prior head before
        # this transaction lets concurrent finalized turns fork the version
        # chain even when their numeric versions remain unique.
        conn.execute("BEGIN IMMEDIATE")
        namespace_row = conn.execute(
            """
            SELECT project_id, lifecycle_state
            FROM context_namespaces
            WHERE chat_id = ?
            LIMIT 1
            """,
            (namespace.chat_id,),
        ).fetchone()
        if (
            namespace_row is None
            or str(namespace_row["lifecycle_state"]) != "active"
        ):
            raise ValueError("capsules require an active chat")
        capsules, chain_valid = _validated_capsule_chain(
            conn,
            namespace.chat_id,
        )
        if not chain_valid:
            raise ValueError("context capsule chain is corrupt")
        previous = capsules[-1] if capsules else None
        effective_project_id = str(namespace_row["project_id"] or "")

        requested_action_receipts = {
            str(action.get("receipt_id") or "").strip()
            for action in list(verified_actions or [])
            if isinstance(action, dict)
            and bool(action.get("verified"))
            and str(action.get("receipt_id") or "").strip()
        }
        requested_receipts = {
            str(item).strip()
            for item in list(receipt_references or [])
            if str(item).strip()
        } | requested_action_receipts
        valid_actions = {
            receipt_id: action_record
            for receipt_id in sorted(requested_receipts)
            if (
                action_record := _verified_action_record(
                    receipt_id,
                    chat_id=namespace.chat_id,
                    project_id=effective_project_id,
                )
            )
            is not None
        }
        receipt_ids = sorted(valid_actions)
        action_claims = [
            claim
            for receipt_id in sorted(requested_action_receipts)
            if receipt_id in valid_actions
            if (
                claim := _claim(
                    dict(valid_actions[receipt_id].get("result") or {}).get(
                        "summary"
                    ),
                    source_type="verified_action",
                    source_id=receipt_id,
                )
            )
            is not None
        ]

        decisions = [
            *(previous.decisions if previous else ()),
            *new_decisions,
        ]
        constraints = [
            *(previous.constraints if previous else ()),
            *new_constraints,
        ]
        created_at = _utcnow()
        version_id = f"capsule-{uuid.uuid4().hex}"
        payload = {
            # A8 pass-001 payload lineage ('' on legacy lanes — never
            # fabricated). Shadow capsules are immutable and not served into
            # prompts today; the stamp makes future traversal deterministic.
            "request_lineage": str(request_id or "").strip(),
            "objective": asdict(objective) if objective else None,
            "decisions": [
                asdict(claim)
                for claim in _unique_claims(decisions, limit=12)
            ],
            "constraints": [
                asdict(claim)
                for claim in _unique_claims(constraints, limit=12)
            ],
            "system_state": [],
            "unresolved_work": [asdict(objective)] if objective else [],
            "verified_actions": [
                asdict(claim)
                for claim in _unique_claims(action_claims, limit=12)
            ],
            "receipt_references": sorted(set(receipt_ids)),
        }
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        payload_hash = _content_hash(encoded)
        transcript_head_hash = _content_hash(
            "\0".join(
                [
                    previous.transcript_head_hash if previous else "",
                    source_id,
                ]
            )
        )
        version_number = (previous.version_number + 1) if previous else 1
        previous_version_id = previous.version_id if previous else ""
        record_hash = _capsule_record_hash(
            version_id=version_id,
            version_number=version_number,
            chat_id=namespace.chat_id,
            project_id=effective_project_id,
            previous_version_id=previous_version_id,
            mode="shadow",
            capsule_json=encoded,
            transcript_head_hash=transcript_head_hash,
            payload_hash=payload_hash,
            created_at=created_at,
        )
        capsule = ActiveContextCapsule(
            version_id=version_id,
            version_number=version_number,
            chat_id=namespace.chat_id,
            project_id=effective_project_id,
            previous_version_id=previous_version_id,
            mode="shadow",
            objective=objective,
            decisions=_claims_from_list(payload["decisions"]),
            constraints=_claims_from_list(payload["constraints"]),
            system_state=(),
            unresolved_work=_claims_from_list(payload["unresolved_work"]),
            verified_actions=_claims_from_list(payload["verified_actions"]),
            receipt_references=tuple(payload["receipt_references"]),
            transcript_head_hash=transcript_head_hash,
            payload_hash=payload_hash,
            record_hash=record_hash,
            created_at=created_at,
        )
        conn.execute(
            """
            INSERT INTO context_capsule_versions (
                version_id, version_number, chat_id, project_id,
                previous_version_id, mode, capsule_json,
                transcript_head_hash, payload_hash, record_hash, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                capsule.version_id,
                capsule.version_number,
                capsule.chat_id,
                capsule.project_id,
                capsule.previous_version_id,
                capsule.mode,
                encoded,
                capsule.transcript_head_hash,
                capsule.payload_hash,
                capsule.record_hash,
                capsule.created_at,
            ),
        )
        conn.execute(
            """
            INSERT INTO context_capsule_heads (
                chat_id, version_id, updated_at
            ) VALUES (?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                version_id = excluded.version_id,
                updated_at = excluded.updated_at
            """,
            (
                capsule.chat_id,
                capsule.version_id,
                capsule.created_at,
            ),
        )
        written_chain, written_chain_valid = _validated_capsule_chain(
            conn,
            capsule.chat_id,
        )
        if (
            not written_chain_valid
            or not written_chain
            or written_chain[-1] != capsule
        ):
            raise ValueError("context capsule chain failed write validation")
        conn.commit()
    finally:
        conn.close()
    return capsule


def rebuild_shadow_capsule(chat_id: str) -> ActiveContextCapsule | None:
    namespace = load_chat_namespace(chat_id)
    if namespace is None or namespace.lifecycle_state != "active":
        return None
    from core.memory.files import conversation_log_path, load_jsonl

    rows = [
        row
        for row in load_jsonl(conversation_log_path())
        if str(row.get("session_id") or "") == namespace.chat_id
    ]
    if not rows:
        return None
    return create_shadow_capsule(
        chat_id=namespace.chat_id,
        project_id=namespace.project_id,
        user_text=str(rows[-1].get("user") or ""),
    )


__all__ = [
    "ActiveContextCapsule",
    "CapsuleClaim",
    "create_shadow_capsule",
    "ensure_active_capsule_schema",
    "list_capsule_versions",
    "load_active_capsule",
    "load_latest_capsule",
    "rebuild_shadow_capsule",
]
