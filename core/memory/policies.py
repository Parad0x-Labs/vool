from __future__ import annotations

import contextlib
import json
import re
from typing import Any

from core.memory.files import utcnow
from core.privacy_guard import (
    normalize_share_scope,
    parse_restricted_terms,
    share_scope_label,
    tokenize_restricted_terms,
)
from storage.db import get_connection

_PRIVACY_STATUS_RE = re.compile(
    r"^(?:/privacy|show privacy|show share scope|show memory scope|what(?:'s| is) (?:the )?(?:privacy|share|memory) (?:scope|mode|setting)|is this (?:private|private vault|public|public commons|hive mind|shared pack))\??$",
    re.IGNORECASE,
)
_SCOPE_EXCEPT_RE = re.compile(r"\b(?:except|but keep|but preserve)\b(.+)$", re.IGNORECASE)
_HIVE_TASK_QUERY_HINT_RE = re.compile(
    r"\b(?:tasks?|queue|work|available|open|help|research|researches)\b",
    re.IGNORECASE,
)


def ensure_session_policy_table() -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS session_memory_policies (
                session_id TEXT PRIMARY KEY,
                share_scope TEXT NOT NULL DEFAULT 'local_only',
                restricted_terms_json TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def session_memory_policy(session_id: str | None) -> dict[str, Any]:
    fallback_scope = _default_share_scope()
    normalized_session_id = str(session_id or "").strip()
    if not normalized_session_id:
        return {
            "session_id": "",
            "share_scope": fallback_scope,
            "realm_label": share_scope_label(fallback_scope),
            "restricted_terms": [],
            "updated_at": "",
        }
    ensure_session_policy_table()
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT session_id, share_scope, restricted_terms_json, updated_at
            FROM session_memory_policies
            WHERE session_id = ?
            LIMIT 1
            """,
            (normalized_session_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return {
            "session_id": normalized_session_id,
            "share_scope": fallback_scope,
            "realm_label": share_scope_label(fallback_scope),
            "restricted_terms": [],
            "updated_at": "",
        }
    data = dict(row)
    try:
        restricted_terms = json.loads(data.get("restricted_terms_json") or "[]")
    except json.JSONDecodeError:
        restricted_terms = []
    return {
        "session_id": normalized_session_id,
        "share_scope": normalize_share_scope(str(data.get("share_scope") or "local_only")),
        "realm_label": share_scope_label(str(data.get("share_scope") or "local_only")),
        "restricted_terms": tokenize_restricted_terms(list(restricted_terms or [])),
        "updated_at": str(data.get("updated_at") or ""),
    }


def describe_session_memory_policy(session_id: str | None) -> str:
    policy = session_memory_policy(session_id)
    scope = policy["share_scope"]
    restricted_terms = list(policy.get("restricted_terms") or [])
    if scope == "hive_mind":
        base = "Session sharing: SHARED PACK. Generalized learned procedures may sync to the mesh after secret screening. Raw chat, memory extracts, and session summaries stay in the PRIVATE VAULT."
    elif scope == "public_knowledge":
        base = "Session sharing: HIVE/PUBLIC COMMONS. Generalized learned procedures may be published as public claims after secret screening. Raw chat, memory extracts, and session summaries stay in the PRIVATE VAULT."
    else:
        base = "Session sharing: PRIVATE VAULT. Chat history, extracted memory, summaries, and learned procedures stay on this node unless you explicitly reclassify the session."
    if restricted_terms:
        base += " Protected exceptions: " + ", ".join(restricted_terms[:6]) + "."
    return base


def set_session_memory_policy(
    session_id: str,
    *,
    share_scope: str,
    restricted_terms: list[str] | None = None,
) -> dict[str, Any]:
    normalized_session_id = str(session_id or "").strip()
    if not normalized_session_id:
        raise ValueError("session_id is required to set memory scope")
    normalized_scope = normalize_share_scope(share_scope)
    normalized_terms = tokenize_restricted_terms(restricted_terms)

    ensure_session_policy_table()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO session_memory_policies (
                session_id, share_scope, restricted_terms_json, updated_at
            ) VALUES (?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                share_scope = excluded.share_scope,
                restricted_terms_json = excluded.restricted_terms_json,
                updated_at = excluded.updated_at
            """,
            (
                normalized_session_id,
                normalized_scope,
                json.dumps(normalized_terms, sort_keys=True),
                utcnow(),
            ),
        )
        if _table_exists(conn, "local_tasks"):
            with contextlib.suppress(Exception):
                conn.execute(
                    """
                    UPDATE local_tasks
                    SET share_scope = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE session_id = ?
                    """,
                    (normalized_scope, normalized_session_id),
                )
        if _table_exists(conn, "learning_shards"):
            with contextlib.suppress(Exception):
                conn.execute(
                    """
                    UPDATE learning_shards
                    SET share_scope = ?,
                        restricted_terms_json = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE origin_session_id = ?
                      AND source_type = 'local_generated'
                    """,
                    (
                        normalized_scope,
                        json.dumps(normalized_terms, sort_keys=True),
                        normalized_session_id,
                    ),
                )
        conn.commit()
    finally:
        conn.close()

    stats = _reconcile_session_learning_scope(
        normalized_session_id,
        requested_scope=normalized_scope,
        restricted_terms=normalized_terms,
    )
    return {
        "session_id": normalized_session_id,
        "share_scope": normalized_scope,
        "realm_label": share_scope_label(normalized_scope),
        "restricted_terms": normalized_terms,
        **stats,
    }


def parse_session_scope_command(text: str) -> dict[str, Any] | None:
    cleaned = " ".join(str(text or "").split()).strip()
    if not cleaned:
        return None
    lowered = cleaned.lower().strip(" .!?")
    if _PRIVACY_STATUS_RE.match(cleaned):
        return {"action": "show"}

    restricted_terms: list[str] = []
    except_match = _SCOPE_EXCEPT_RE.search(cleaned)
    if except_match:
        restricted_terms = parse_restricted_terms(except_match.group(1))
        lowered = cleaned[: except_match.start()].strip().lower().strip(" .!?")

    if lowered in {"private", "private vault", "local only", "keep this private", "this conversation is private", "store this locally", "only local", "locked"}:
        return {"action": "set", "share_scope": "local_only", "restricted_terms": restricted_terms}
    if looks_like_hive_task_query(lowered):
        return None
    if lowered in {"hive mind", "shared pack", "friend-swarm", "friend swarm", "mesh-share"} or any(
        phrase in lowered
        for phrase in (
            "share with hive",
            "share this with hive",
            "set shared pack",
            "set hive mind",
            "switch to shared pack",
            "switch to hive mind",
            "use shared pack",
            "use hive mind",
            "this is hive mind",
            "this is shared pack",
        )
    ):
        return {"action": "set", "share_scope": "hive_mind", "restricted_terms": restricted_terms}
    if lowered in {"public knowledge", "public commons", "hive/public commons"} or any(
        phrase in lowered
        for phrase in (
            "make this public",
            "this is public knowledge",
            "publish this knowledge",
            "set public commons",
            "switch to public commons",
            "use public commons",
        )
    ):
        return {"action": "set", "share_scope": "public_knowledge", "restricted_terms": restricted_terms}
    if any(phrase in lowered for phrase in ("private forever", "keep this local", "keep this private")):
        return {"action": "set", "share_scope": "local_only", "restricted_terms": restricted_terms}
    return None


def looks_like_hive_task_query(lowered: str) -> bool:
    text = str(lowered or "").strip().lower()
    if "hive mind" not in text and "shared pack" not in text and "public commons" not in text:
        return False
    if not _HIVE_TASK_QUERY_HINT_RE.search(text):
        return False
    return any(
        word in text
        for word in (
            "task",
            "tasks",
            "queue",
            "available",
            "open",
            "help with",
            "help",
            "work on",
            "work",
            "research",
        )
    )


def _table_exists(conn: Any, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
        (table_name,),
    ).fetchone()
    return bool(row)


def _default_share_scope() -> str:
    try:
        from core import policy_engine
        return str(policy_engine.get("shards.default_share_scope", "local_only"))
    except Exception:
        return "local_only"


def _reconcile_session_learning_scope(
    session_id: str,
    *,
    requested_scope: str,
    restricted_terms: list[str],
) -> dict[str, int]:
    from core.knowledge_registry import register_local_shard, withdraw_local_shard

    conn = get_connection()
    try:
        if not _table_exists(conn, "learning_shards"):
            return {"updated_shards": 0, "registered_shards": 0, "blocked_shards": 0}
        try:
            rows = conn.execute(
                """
                SELECT shard_id, share_scope
                FROM learning_shards
                WHERE origin_session_id = ?
                  AND source_type = 'local_generated'
                ORDER BY updated_at DESC
                """,
                (session_id,),
            ).fetchall()
        except Exception:
            return {"updated_shards": 0, "registered_shards": 0, "blocked_shards": 0}
    finally:
        conn.close()

    updated = 0
    registered = 0
    blocked = 0
    normalized_scope = normalize_share_scope(requested_scope)
    for row in rows:
        shard_id = str(row["shard_id"])
        updated += 1
        if normalized_scope == "local_only":
            withdraw_local_shard(shard_id)
            continue
        manifest = register_local_shard(shard_id, restricted_terms=restricted_terms)
        if manifest:
            registered += 1
            continue
        blocked += 1
        conn = get_connection()
        try:
            if _table_exists(conn, "learning_shards"):
                conn.execute(
                    """
                    UPDATE learning_shards
                    SET share_scope = 'local_only',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE shard_id = ?
                    """,
                    (shard_id,),
                )
                conn.commit()
        finally:
            conn.close()
        withdraw_local_shard(shard_id)
    return {"updated_shards": updated, "registered_shards": registered, "blocked_shards": blocked}
