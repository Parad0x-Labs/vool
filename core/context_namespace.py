"""Persistent chat namespaces and explicit context-import grants.

The chat identifier is the privacy boundary.  A matching semantic score never
grants access on its own: context outside the current chat must have a live,
server-created import grant.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from storage.db import get_connection

NamespaceState = Literal["active", "archived", "deleted"]
GrantScope = Literal["chat", "project", "user_profile", "action_receipt"]

_NAMESPACE_STATES = frozenset({"active", "archived", "deleted"})
_GRANT_SCOPES = frozenset({"chat", "project", "user_profile", "action_receipt"})


@dataclass(frozen=True)
class ContextNamespace:
    chat_id: str
    project_id: str = ""
    lifecycle_state: NamespaceState = "active"
    parent_chat_id: str = ""
    branch_turn: int | None = None
    created_at: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class ContextImportGrant:
    grant_id: str
    chat_id: str
    scope: GrantScope
    source_id: str
    source_project_id: str = ""
    created_at: str = ""
    revoked_at: str = ""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_id(value: Any, *, field: str) -> str:
    cleaned = str(value or "").strip()
    if not cleaned:
        raise ValueError(f"{field} is required")
    if len(cleaned) > 240 or any(ord(char) < 32 for char in cleaned):
        raise ValueError(f"invalid {field}")
    return cleaned


def ensure_context_namespace_schema() -> None:
    conn = get_connection()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS context_namespaces (
                chat_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL DEFAULT '',
                lifecycle_state TEXT NOT NULL DEFAULT 'active',
                parent_chat_id TEXT NOT NULL DEFAULT '',
                branch_turn INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                CHECK (lifecycle_state IN ('active', 'archived', 'deleted'))
            );

            CREATE INDEX IF NOT EXISTS idx_context_namespaces_project_state
                ON context_namespaces(project_id, lifecycle_state);

            CREATE TABLE IF NOT EXISTS context_import_grants (
                grant_id TEXT PRIMARY KEY,
                chat_id TEXT NOT NULL,
                scope TEXT NOT NULL,
                source_id TEXT NOT NULL,
                source_project_id TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                revoked_at TEXT NOT NULL DEFAULT '',
                UNIQUE(chat_id, scope, source_id),
                FOREIGN KEY(chat_id) REFERENCES context_namespaces(chat_id),
                CHECK (scope IN ('chat', 'project', 'user_profile', 'action_receipt'))
            );

            CREATE INDEX IF NOT EXISTS idx_context_import_grants_chat_active
                ON context_import_grants(chat_id, revoked_at);
            """
        )
        conn.commit()
    finally:
        conn.close()


def ensure_chat_namespace(
    chat_id: str,
    *,
    project_id: str | None = None,
    parent_chat_id: str | None = None,
    branch_turn: int | None = None,
    grant_confirmed_profile: bool = False,
    grant_current_receipts: bool = True,
) -> ContextNamespace:
    """Create a namespace once, without silently replacing its identity."""
    clean_chat = _clean_id(chat_id, field="chat_id")
    clean_project = str(project_id or "").strip()
    clean_parent = str(parent_chat_id or "").strip()
    now = _utcnow()
    ensure_context_namespace_schema()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO context_namespaces (
                chat_id, project_id, lifecycle_state, parent_chat_id,
                branch_turn, created_at, updated_at
            ) VALUES (?, ?, 'active', ?, ?, ?, ?)
            """,
            (clean_chat, clean_project, clean_parent, branch_turn, now, now),
        )
        if clean_project:
            conn.execute(
                """
                UPDATE context_namespaces
                SET project_id = CASE WHEN project_id = '' THEN ? ELSE project_id END,
                    updated_at = ?
                WHERE chat_id = ? AND lifecycle_state != 'deleted'
                """,
                (clean_project, now, clean_chat),
            )
        conn.commit()
    finally:
        conn.close()
    namespace = load_chat_namespace(clean_chat)
    if namespace is None:
        raise RuntimeError("context namespace was not created")
    if namespace.lifecycle_state == "active" and grant_confirmed_profile:
        grant_context_import(
            clean_chat,
            scope="user_profile",
            source_id="profile:confirmed",
        )
    if namespace.lifecycle_state == "active" and grant_current_receipts:
        grant_context_import(
            clean_chat,
            scope="action_receipt",
            source_id=f"chat:{clean_chat}",
        )
    refreshed = load_chat_namespace(clean_chat)
    if refreshed is None:
        raise RuntimeError("context namespace disappeared")
    return refreshed


def load_chat_namespace(chat_id: str) -> ContextNamespace | None:
    clean_chat = str(chat_id or "").strip()
    if not clean_chat:
        return None
    ensure_context_namespace_schema()
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT chat_id, project_id, lifecycle_state, parent_chat_id,
                   branch_turn, created_at, updated_at
            FROM context_namespaces
            WHERE chat_id = ?
            LIMIT 1
            """,
            (clean_chat,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    payload = dict(row)
    return ContextNamespace(
        chat_id=str(payload["chat_id"]),
        project_id=str(payload.get("project_id") or ""),
        lifecycle_state=str(payload.get("lifecycle_state") or "active"),  # type: ignore[arg-type]
        parent_chat_id=str(payload.get("parent_chat_id") or ""),
        branch_turn=int(payload["branch_turn"]) if payload.get("branch_turn") is not None else None,
        created_at=str(payload.get("created_at") or ""),
        updated_at=str(payload.get("updated_at") or ""),
    )


def authoritative_chat_workspace(chat_id: str) -> tuple[str, str]:
    """The SERVER-OWNED workspace root for a chat, or ("", reason).

    Authority order: the chat namespace's bound project (server-side sqlite, written only by the
    session-meta / project-binding lanes), then the persisted session meta's project, then the
    project store's registered root -- each refusing when the folder no longer exists. A client
    body may carry a workspace string, but it is never an input here: grants minted against a
    workspace resolve THAT workspace from this function, so a client, plugin or model cannot widen
    a grant by naming a different root, the home directory, or another chat's folder.

    reason is one of: "project" (with the root), "missing_chat", "deleted_chat", "unbound",
    "project_missing", "unreadable".
    """
    clean_chat = str(chat_id or "").strip()
    if not clean_chat:
        return "", "missing_chat"
    try:
        namespace = load_chat_namespace(clean_chat)
    except Exception:
        return "", "unreadable"
    project_id = ""
    if namespace is not None:
        if namespace.lifecycle_state != "active":
            return "", "deleted_chat" if namespace.lifecycle_state == "deleted" else str(namespace.lifecycle_state)
        project_id = str(namespace.project_id or "").strip()
    if not project_id:
        # A chat can pre-date its namespace row; session meta is the same server-owned pairing
        # the turn dispatch falls back to, so it is consulted the same way here.
        try:
            from core.persistent_memory import load_session_meta

            project_id = str(
                (load_session_meta().get(clean_chat) or {}).get("project_id") or ""
            ).strip()
        except Exception:
            return "", "unreadable"
    if not project_id:
        return "", "unbound"
    try:
        from core import project_store

        root = str(project_store.project_root(project_id) or "")
    except Exception:
        return "", "unreadable"
    if not root:
        return "", "project_missing"
    return root, "project"


def list_chat_namespaces(
    *,
    include_deleted: bool = False,
    limit: int | None = 200,
) -> tuple[ContextNamespace, ...]:
    ensure_context_namespace_schema()
    conn = get_connection()
    try:
        if include_deleted:
            rows = conn.execute(
                """
                SELECT chat_id, project_id, lifecycle_state, parent_chat_id,
                       branch_turn, created_at, updated_at
                FROM context_namespaces
                ORDER BY updated_at DESC, chat_id
                LIMIT ?
                """,
                (-1 if limit is None else max(1, int(limit)),),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT chat_id, project_id, lifecycle_state, parent_chat_id,
                       branch_turn, created_at, updated_at
                FROM context_namespaces
                WHERE lifecycle_state != 'deleted'
                ORDER BY updated_at DESC, chat_id
                LIMIT ?
                """,
                (-1 if limit is None else max(1, int(limit)),),
            ).fetchall()
    finally:
        conn.close()
    return tuple(
        ContextNamespace(
            chat_id=str(row["chat_id"]),
            project_id=str(row["project_id"] or ""),
            lifecycle_state=str(row["lifecycle_state"]),  # type: ignore[arg-type]
            parent_chat_id=str(row["parent_chat_id"] or ""),
            branch_turn=(
                int(row["branch_turn"])
                if row["branch_turn"] is not None
                else None
            ),
            created_at=str(row["created_at"] or ""),
            updated_at=str(row["updated_at"] or ""),
        )
        for row in rows
    )


def set_chat_namespace_state(chat_id: str, state: NamespaceState) -> ContextNamespace:
    clean_chat = _clean_id(chat_id, field="chat_id")
    clean_state = str(state or "").strip().lower()
    if clean_state not in _NAMESPACE_STATES:
        raise ValueError("invalid namespace state")
    namespace = load_chat_namespace(clean_chat)
    if namespace is None:
        raise ValueError("chat namespace does not exist")
    current_state = namespace.lifecycle_state
    if current_state == "deleted" and clean_state != "deleted":
        raise ValueError("deleted chat namespaces cannot be restored")
    if current_state == clean_state:
        return namespace
    allowed_transitions = {
        ("active", "archived"),
        ("active", "deleted"),
        ("archived", "active"),
        ("archived", "deleted"),
    }
    if (current_state, clean_state) not in allowed_transitions:
        raise ValueError(
            f"invalid namespace transition: {current_state}->{clean_state}"
        )
    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE context_namespaces
            SET lifecycle_state = ?, updated_at = ?
            WHERE chat_id = ?
            """,
            (clean_state, _utcnow(), clean_chat),
        )
        if clean_state == "deleted":
            conn.execute(
                """
                UPDATE context_import_grants
                SET revoked_at = ?
                WHERE chat_id = ? AND revoked_at = ''
                """,
                (_utcnow(), clean_chat),
            )
        conn.commit()
    finally:
        conn.close()
    namespace = load_chat_namespace(clean_chat)
    if namespace is None:
        raise RuntimeError("context namespace disappeared")
    return namespace


def set_chat_namespace_project(
    chat_id: str,
    project_id: str | None,
) -> ContextNamespace:
    clean_chat = _clean_id(chat_id, field="chat_id")
    clean_project = str(project_id or "").strip()
    if len(clean_project) > 240 or any(
        ord(char) < 32 for char in clean_project
    ):
        raise ValueError("invalid project_id")
    namespace = load_chat_namespace(clean_chat)
    if namespace is None or namespace.lifecycle_state != "active":
        raise ValueError("project assignment requires an active chat")
    if namespace.project_id == clean_project:
        return namespace
    now = _utcnow()
    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE context_namespaces
            SET project_id = ?, updated_at = ?
            WHERE chat_id = ? AND lifecycle_state = 'active'
            """,
            (clean_project, now, clean_chat),
        )
        conn.execute(
            """
            UPDATE context_import_grants
            SET revoked_at = ?
            WHERE chat_id = ? AND scope = 'project'
              AND revoked_at = ''
            """,
            (now, clean_chat),
        )
        conn.commit()
    finally:
        conn.close()
    updated = load_chat_namespace(clean_chat)
    if updated is None:
        raise RuntimeError("context namespace disappeared")
    return updated


def grant_context_import(
    chat_id: str,
    *,
    scope: GrantScope,
    source_id: str,
    source_project_id: str | None = None,
) -> ContextImportGrant:
    clean_chat = _clean_id(chat_id, field="chat_id")
    clean_scope = str(scope or "").strip().lower()
    if clean_scope not in _GRANT_SCOPES:
        raise ValueError("invalid context grant scope")
    clean_source = _clean_id(source_id, field="source_id")
    clean_project = str(source_project_id or "").strip()
    namespace = load_chat_namespace(clean_chat)
    if namespace is None:
        raise ValueError("chat namespace does not exist")
    if namespace.lifecycle_state != "active":
        raise ValueError("context imports require an active chat")
    now = _utcnow()
    stable = hashlib.sha256(
        f"{clean_chat}\0{clean_scope}\0{clean_source}".encode()
    ).hexdigest()[:24]
    grant_id = f"grant-{stable}"
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO context_import_grants (
                grant_id, chat_id, scope, source_id, source_project_id,
                created_at, revoked_at
            ) VALUES (?, ?, ?, ?, ?, ?, '')
            ON CONFLICT(chat_id, scope, source_id) DO UPDATE SET
                source_project_id = excluded.source_project_id,
                revoked_at = ''
            """,
            (
                grant_id,
                clean_chat,
                clean_scope,
                clean_source,
                clean_project,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return ContextImportGrant(
        grant_id=grant_id,
        chat_id=clean_chat,
        scope=clean_scope,  # type: ignore[arg-type]
        source_id=clean_source,
        source_project_id=clean_project,
        created_at=now,
    )


def revoke_context_import(chat_id: str, *, scope: GrantScope, source_id: str) -> bool:
    clean_chat = _clean_id(chat_id, field="chat_id")
    clean_scope = str(scope or "").strip().lower()
    clean_source = _clean_id(source_id, field="source_id")
    ensure_context_namespace_schema()
    conn = get_connection()
    try:
        cursor = conn.execute(
            """
            UPDATE context_import_grants
            SET revoked_at = ?
            WHERE chat_id = ? AND scope = ? AND source_id = ? AND revoked_at = ''
            """,
            (_utcnow(), clean_chat, clean_scope, clean_source),
        )
        conn.commit()
        return bool(cursor.rowcount)
    finally:
        conn.close()


def list_context_imports(chat_id: str) -> tuple[ContextImportGrant, ...]:
    clean_chat = str(chat_id or "").strip()
    if not clean_chat:
        return ()
    ensure_context_namespace_schema()
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT grant_id, chat_id, scope, source_id, source_project_id,
                   created_at, revoked_at
            FROM context_import_grants
            WHERE chat_id = ? AND revoked_at = ''
            ORDER BY created_at, grant_id
            """,
            (clean_chat,),
        ).fetchall()
    finally:
        conn.close()
    return tuple(
        ContextImportGrant(
            grant_id=str(row["grant_id"]),
            chat_id=str(row["chat_id"]),
            scope=str(row["scope"]),  # type: ignore[arg-type]
            source_id=str(row["source_id"]),
            source_project_id=str(row["source_project_id"] or ""),
            created_at=str(row["created_at"] or ""),
            revoked_at=str(row["revoked_at"] or ""),
        )
        for row in rows
    )


def clone_chat_namespace(
    source_chat_id: str,
    new_chat_id: str,
    *,
    through_turn: int | None = None,
) -> ContextNamespace:
    """Copy transcript rows into an independent namespace.

    Tool receipts, semantic nodes, summaries, grants, and capsule state are not
    copied.  The new namespace can reference them only through a later grant.
    """
    source = load_chat_namespace(_clean_id(source_chat_id, field="source_chat_id"))
    if source is None or source.lifecycle_state == "deleted":
        raise ValueError("source chat does not exist")
    clean_new = _clean_id(new_chat_id, field="new_chat_id")
    if load_chat_namespace(clean_new) is not None:
        raise ValueError("target chat already exists")
    branch_limit = None if through_turn is None else max(0, int(through_turn))
    namespace = ensure_chat_namespace(
        clean_new,
        project_id=source.project_id,
        parent_chat_id=source.chat_id,
        branch_turn=branch_limit,
    )

    from core.memory.files import (
        append_sequenced_jsonl,
        conversation_log_path,
        load_jsonl,
    )

    source_rows = [
        dict(row)
        for row in load_jsonl(conversation_log_path())
        if str(row.get("session_id") or "") == source.chat_id
    ]
    if branch_limit is not None:
        source_rows = source_rows[:branch_limit]
    for row in source_rows:
        source_event_id = str(row.get("event_id") or "").strip()
        row.pop("event_id", None)
        row.pop("event_sequence", None)
        row["session_id"] = clean_new
        row["project_id"] = source.project_id
        row["derived_from_chat_id"] = source.chat_id
        row["source_event_id"] = source_event_id
        row["derived_copy_id"] = f"copy-{uuid.uuid4().hex}"
        append_sequenced_jsonl(conversation_log_path(), row)
    return namespace


__all__ = [
    "ContextImportGrant",
    "ContextNamespace",
    "authoritative_chat_workspace",
    "clone_chat_namespace",
    "ensure_chat_namespace",
    "ensure_context_namespace_schema",
    "grant_context_import",
    "list_chat_namespaces",
    "list_context_imports",
    "load_chat_namespace",
    "revoke_context_import",
    "set_chat_namespace_project",
    "set_chat_namespace_state",
]
