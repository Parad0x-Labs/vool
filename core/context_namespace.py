"""Persistent chat namespaces and explicit context-import grants.

The chat identifier is the privacy boundary.  A matching semantic score never
grants access on its own: context outside the current chat must have a live,
server-created import grant.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Literal

from storage.db import get_connection

NamespaceState = Literal["active", "archived", "deleted"]
GrantScope = Literal["chat", "project", "user_profile", "action_receipt"]

_log = logging.getLogger(__name__)

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


# A chat whose first turn is private (core.private_chat) gets no namespace row: the sidebar lists
# every namespace, so the row showed as an empty "New chat", and it kept the chat's id and its
# import grants on disk. Such a chat lives in this process only, keyed by chat id, holding what a
# row and its grants would hold (ids and times, never a word of the chat). Readers see it only while
# the chat is private; the first turn that is saved creates the row, carries its grants over and
# drops this. The bound evicts the oldest: an evicted chat that is still private gets a fresh entry
# with its default grants on its next turn (imports granted while private are gone, never widened).
_UNSAVED: dict[str, tuple[ContextNamespace, dict[tuple[str, str], ContextImportGrant]]] = {}
_UNSAVED_MAX = 512
_UNSAVED_LOCK = threading.Lock()


def _chat_is_private_now(clean_chat: str) -> bool:
    """True while ``clean_chat`` is private: its switch is on, or the bound request is a private turn
    of it. An unreadable switch answers True (core.private_chat.is_private fails closed)."""
    from core import private_chat

    if private_chat.is_private(clean_chat):
        return True
    if not private_chat.request_is_private():
        return False
    owner = private_chat.private_request_session()
    if not owner or owner == clean_chat:
        return True
    from core.chat_session_identity import canonical_chat_session_id

    return canonical_chat_session_id(owner) == canonical_chat_session_id(clean_chat)


def _unsaved_entry(
    clean_chat: str,
) -> tuple[ContextNamespace, dict[tuple[str, str], ContextImportGrant]] | None:
    """The in-process record of a private chat that has no row, while it is still private."""
    with _UNSAVED_LOCK:
        entry = _UNSAVED.get(clean_chat)
    if entry is None:
        return None
    # Kept when the chat is no longer private: the saved turn that follows creates the row and
    # carries these grants over (ensure_chat_namespace), whichever reader happened to ask first.
    return entry if _chat_is_private_now(clean_chat) else None


def _grant_record(
    clean_chat: str, scope: str, source_id: str, source_project_id: str, now: str
) -> ContextImportGrant:
    stable = hashlib.sha256(
        f"{clean_chat}\0{scope}\0{source_id}".encode()
    ).hexdigest()[:24]
    return ContextImportGrant(
        grant_id=f"grant-{stable}",
        chat_id=clean_chat,
        scope=scope,  # type: ignore[arg-type]
        source_id=source_id,
        source_project_id=source_project_id,
        created_at=now,
    )


def _ensure_unsaved_namespace(
    clean_chat: str,
    *,
    project_id: str,
    parent_chat_id: str,
    branch_turn: int | None,
    grant_confirmed_profile: bool,
    grant_current_receipts: bool,
    now: str,
) -> ContextNamespace:
    with _UNSAVED_LOCK:
        previous = _UNSAVED.pop(clean_chat, None)
        if previous is None:
            namespace = ContextNamespace(
                chat_id=clean_chat,
                project_id=project_id,
                parent_chat_id=parent_chat_id,
                branch_turn=branch_turn,
                created_at=now,
                updated_at=now,
            )
            grants: dict[tuple[str, str], ContextImportGrant] = {}
        else:
            namespace, grants = previous[0], dict(previous[1])
            if project_id and not namespace.project_id:
                namespace = replace(namespace, project_id=project_id, updated_at=now)
        wanted = []
        if grant_confirmed_profile:
            wanted.append(("user_profile", "profile:confirmed"))
        if grant_current_receipts:
            wanted.append(("action_receipt", f"chat:{clean_chat}"))
        for scope, source in wanted:
            if (scope, source) not in grants:
                grants[(scope, source)] = _grant_record(clean_chat, scope, source, "", now)
        _UNSAVED[clean_chat] = (namespace, grants)
        while len(_UNSAVED) > _UNSAVED_MAX:
            _UNSAVED.pop(next(iter(_UNSAVED)), None)
    return namespace


def ensure_context_namespace_schema() -> None:
    conn = get_connection()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS context_namespaces (
                chat_id TEXT PRIMARY KEY,
                canonical_id TEXT NOT NULL DEFAULT '',
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

            CREATE TABLE IF NOT EXISTS context_namespace_schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        # The canonical identity each chat_id folds to (core.chat_session_identity). New
        # tables get the column in-column; an existing install gets a guarded ALTER plus
        # a ONE-TIME marker-gated backfill, so erasure decisions can resolve a chat by
        # ANY handle spelling without scanning the table. After the first ensure the only
        # recurring cost is one indexed meta read.
        columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(context_namespaces)")
        }
        if "canonical_id" not in columns:
            conn.execute(
                "ALTER TABLE context_namespaces"
                " ADD COLUMN canonical_id TEXT NOT NULL DEFAULT ''"
            )
        # only after the column is guaranteed — on a pre-existing table the CREATE INDEX
        # would run before any ALTER if it lived in the script above, and crash
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_context_namespaces_canonical
                ON context_namespaces(canonical_id, lifecycle_state);
            """
        )
        backfilled = conn.execute(
            "SELECT value FROM context_namespace_schema_meta"
            " WHERE key = 'canonical_id_backfill'"
        ).fetchone()
        if backfilled is None:
            from core.chat_session_identity import canonical_chat_session_id

            stale = conn.execute(
                "SELECT chat_id FROM context_namespaces WHERE canonical_id = ''"
            ).fetchall()
            for row in stale:
                conn.execute(
                    "UPDATE context_namespaces SET canonical_id = ? WHERE chat_id = ?",
                    (
                        canonical_chat_session_id(str(row["chat_id"])),
                        str(row["chat_id"]),
                    ),
                )
            conn.execute(
                "INSERT OR REPLACE INTO context_namespace_schema_meta(key, value)"
                " VALUES ('canonical_id_backfill', '1')"
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
    from core.chat_session_identity import canonical_chat_session_id

    clean_chat = _clean_id(chat_id, field="chat_id")
    clean_project = str(project_id or "").strip()
    clean_parent = str(parent_chat_id or "").strip()
    now = _utcnow()
    ensure_context_namespace_schema()
    if _load_saved_namespace(clean_chat) is None and _chat_is_private_now(clean_chat):
        return _ensure_unsaved_namespace(
            clean_chat,
            project_id=clean_project,
            parent_chat_id=clean_parent,
            branch_turn=branch_turn,
            grant_confirmed_profile=grant_confirmed_profile,
            grant_current_receipts=grant_current_receipts,
            now=now,
        )
    with _UNSAVED_LOCK:
        unsaved = _UNSAVED.pop(clean_chat, None)
    if unsaved is not None and not clean_project:
        clean_project = unsaved[0].project_id
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO context_namespaces (
                chat_id, canonical_id, project_id, lifecycle_state, parent_chat_id,
                branch_turn, created_at, updated_at
            ) VALUES (?, ?, ?, 'active', ?, ?, ?, ?)
            """,
            (clean_chat, canonical_chat_session_id(clean_chat),
             clean_project, clean_parent, branch_turn, now, now),
        )
        # A row created by an older build carries no canonical_id yet; the one-time
        # backfill in the schema ensure covers the whole table, and this single-row
        # heal covers a row that a mixed-version process re-ensures as ''.
        conn.execute(
            """
            UPDATE context_namespaces
            SET canonical_id = ?
            WHERE chat_id = ? AND canonical_id = ''
            """,
            (canonical_chat_session_id(clean_chat), clean_chat),
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
    if namespace.lifecycle_state == "active" and unsaved is not None:
        # Imports granted while the chat was private and unsaved carry over to its row.
        for grant in unsaved[1].values():
            grant_context_import(
                clean_chat,
                scope=grant.scope,
                source_id=grant.source_id,
                source_project_id=grant.source_project_id,
            )
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
    """The chat's namespace: its row, or, while a chat that started private stays private, the
    in-process namespace that stands in for a row it never got."""
    clean_chat = str(chat_id or "").strip()
    if not clean_chat:
        return None
    saved = _load_saved_namespace(clean_chat)
    if saved is not None:
        return saved
    entry = _unsaved_entry(clean_chat)
    return entry[0] if entry is not None else None


def _load_saved_namespace(clean_chat: str) -> ContextNamespace | None:
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


def chat_namespace_ids_for_identity(chat_id: str) -> list[str]:
    """Every namespace ``chat_id`` key that names the SAME chat as ``chat_id``.

    The identity authority (``core.chat_session_identity``) folds any handle into one
    canonical id, so one chat is addressable by more than one spelling: the native handle
    a door ensured it under, and the canonical id that handle folds to. This resolves the
    concrete keys under which that chat's namespace row(s) are stored — the exact handle
    plus every row whose recorded ``canonical_id`` matches — through one indexed query, so
    a caller can consult the chat's lifecycle by ANY spelling without scanning the table.
    """
    handle = str(chat_id or "").strip()
    if not handle:
        return []
    from core.chat_session_identity import canonical_chat_session_id

    canonical = canonical_chat_session_id(handle)
    ensure_context_namespace_schema()
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT chat_id FROM context_namespaces
            WHERE chat_id = ? OR (canonical_id = ? AND canonical_id != '')
            ORDER BY chat_id
            """,
            (handle, canonical),
        ).fetchall()
    finally:
        conn.close()
    return [str(row["chat_id"]) for row in rows]


def chat_namespace_deleted_for_identity(chat_id: str) -> bool:
    """True when a namespace naming THIS chat identity is lifecycle-``deleted``.

    Every candidate spelling resolves through :func:`load_chat_namespace`, so the durable
    lifecycle state — never a parallel cache — is what answers. Storage faults are NOT
    absorbed here: "the erasure authority cannot be read" is the caller's fail-closed
    decision to make, not a silent ``False`` ("not deleted") from this owner.
    """
    for candidate in chat_namespace_ids_for_identity(chat_id):
        namespace = load_chat_namespace(candidate)
        if namespace is not None and namespace.lifecycle_state == "deleted":
            return True
    return False


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
    namespace = _load_saved_namespace(clean_chat)
    if namespace is None:
        unsaved = _unsaved_entry(clean_chat)
        if clean_state == "deleted":
            # Nothing of it was saved, so deleting it is forgetting it: no tombstone row either, and
            # nothing left for a later saved turn to carry over.
            with _UNSAVED_LOCK:
                _UNSAVED.pop(clean_chat, None)
            if unsaved is not None:
                return replace(unsaved[0], lifecycle_state="deleted", updated_at=_utcnow())
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


#: Session-meta fields a chat can have before its first message is saved without the person having
#: done anything to it beyond opening it in a project.
_UNUSED_META_KEYS = frozenset({"project_id"})


def release_unused_chat_row(chat_id: str) -> bool:
    """A chat that turns private before anything of it was saved gives up its row.

    A chat opened inside a project gets its row (and the project binding in the session meta) before
    its first message. If that message turns private chat on (core.private_chat), the row would list
    it as an empty chat under the project and keep its id for good. When the row is all there is (an
    active row with no parent, no transcript row, nothing set in the session meta but the project,
    not pinned), the chat moves to this process, as a chat that started private does: the same
    project and imports, nothing on disk. The rows are deleted (zeroed: ``secure_delete``) and the
    meta entry is dropped. The first saved turn creates the row again, in the same project.

    Must be called once the chat is private. Returns whether the row was released. Never raises.
    """
    clean_chat = str(chat_id or "").strip()
    try:
        if not clean_chat or not _chat_is_private_now(clean_chat):
            return False
        namespace = _load_saved_namespace(clean_chat)
        if namespace is None or namespace.lifecycle_state != "active" or namespace.parent_chat_id:
            return False
        from core.chat_housekeeping import pinned_chat_ids
        from core.memory.entries import _drop_session_meta, _load_session_meta_raw, recent_conversation_events

        meta = _load_session_meta_raw().get(clean_chat)
        if meta is not None and not set(meta) <= _UNUSED_META_KEYS:
            return False
        if recent_conversation_events(clean_chat, limit=1, include_artifacts=True) or clean_chat in pinned_chat_ids():
            return False
        grants = {(g.scope, g.source_id): replace(g) for g in list_context_imports(clean_chat)}
        # Only what opening the chat gave it: the defaults and its own project. An import the person
        # granted would not survive a restart in this process, so such a chat keeps its row.
        own = {("user_profile", "profile:confirmed"), ("action_receipt", f"chat:{clean_chat}")}
        if namespace.project_id:
            own.add(("project", f"project:{namespace.project_id}"))
        if not set(grants) <= own:
            return False
        with _UNSAVED_LOCK:
            _UNSAVED.pop(clean_chat, None)
            _UNSAVED[clean_chat] = (namespace, grants)
            while len(_UNSAVED) > _UNSAVED_MAX:
                _UNSAVED.pop(next(iter(_UNSAVED)), None)
        conn = get_connection()
        try:
            conn.execute("DELETE FROM context_import_grants WHERE chat_id = ?", (clean_chat,))
            conn.execute("DELETE FROM context_namespaces WHERE chat_id = ?", (clean_chat,))
            conn.commit()
        finally:
            conn.close()
        if meta is not None:
            _drop_session_meta(clean_chat)
        return True
    except Exception:
        _log.warning("a private chat's unused row could not be released", exc_info=True)
        return False


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
    namespace = _load_saved_namespace(clean_chat)
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
    if _load_saved_namespace(clean_chat) is None:
        grant = _grant_record(clean_chat, clean_scope, clean_source, clean_project, now)
        with _UNSAVED_LOCK:
            entry = _UNSAVED.get(clean_chat)
            if entry is not None:
                entry[1][(clean_scope, clean_source)] = grant
                return grant
        raise ValueError("chat namespace does not exist")
    grant_id = _grant_record(clean_chat, clean_scope, clean_source, clean_project, now).grant_id
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
    with _UNSAVED_LOCK:
        entry = _UNSAVED.get(clean_chat)
        dropped = entry is not None and entry[1].pop((clean_scope, clean_source), None) is not None
    if dropped and _load_saved_namespace(clean_chat) is None:
        return True
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
    if clean_chat in _UNSAVED and _load_saved_namespace(clean_chat) is None:
        entry = _unsaved_entry(clean_chat)
        return tuple(entry[1].values()) if entry is not None else ()
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
    source = _load_saved_namespace(_clean_id(source_chat_id, field="source_chat_id"))
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
