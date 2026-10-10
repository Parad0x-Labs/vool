from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.memory.revocation import fenced_writer
from storage.db import get_connection
from storage.migrations import run_migrations

_WORD_RE = re.compile(r"[a-z0-9][a-z0-9_\-']+", re.IGNORECASE)
_STOPWORDS = {
    "about",
    "after",
    "again",
    "and",
    "from",
    "have",
    "just",
    "that",
    "their",
    "them",
    "there",
    "they",
    "this",
    "those",
    "with",
    "your",
}


def _init_db() -> None:
    run_migrations()


def _authority_home_for_store() -> Path | None:
    """The home owning the configured store, when its path names one.

    Re-derived from the store path itself so another process configuring the
    same database discovers the same persisted barrier instead of silently
    consulting the caller's runtime home.
    """
    from core.memory.revocation import _carrier_authority_home
    from storage.db import active_default_db_path

    return _carrier_authority_home(active_default_db_path())


def _sniffed_scope(payload: Any) -> str:
    """The chat a sniffed row's serialized payload declares, '' when unattributed."""

    scope = ""
    if isinstance(payload, dict):
        scope = str(payload.get("origin_chat_id") or payload.get("session_id")
                    or payload.get("chat_id") or payload.get("chat_scope")
                    or payload.get("origin_session_id") or "")
    if scope:
        return scope
    if isinstance(payload, dict):
        for value in payload.values():
            if isinstance(value, dict):
                scope = _sniffed_scope(value)
                if scope:
                    return scope
    return ""


def _sniffed_owned(row: Any, *, token: str, chat_id: str) -> bool:
    """This operation owns a sniffed copy it can attribute to its chat, or an
    unattributed one — the same scope discipline the final verification and
    every file carrier apply (another chat's attributed row is not ours)."""
    from core.memory.carriers import carries, row_payload

    payload = row_payload(row)
    scope = _sniffed_scope(payload)
    if scope not in {"", chat_id}:
        return False
    return carries(payload, token)


def _forget_sniffed_copies(token: str, chat_id: str, erasures: dict[str, Any] | None) -> None:
    """Owned erasure of the swarm learning dataset (explicit forget context).

    The store's serialized prompt/result payloads are retained memory: rows
    this operation owns (its declared chat scope, or an unattributed copy)
    are removed inside the forget's admitted context and the removal is
    reread; anything unreadable or unremovable stays and the store is named
    incomplete, keeping the whole operation pending.
    """
    from core.memory.entries import _record_erasure_store

    removed = 0
    try:
        conn = get_connection()
        try:
            rows = conn.execute("SELECT * FROM sniffed_context").fetchall()
            for row in rows:
                if not _sniffed_owned(row, token=token, chat_id=chat_id):
                    continue
                conn.execute("DELETE FROM sniffed_context WHERE id = ?", (row["id"],))
                removed += 1
            conn.commit()
        finally:
            conn.close()
    except Exception:
        _record_erasure_store(erasures, "swarm_sniffed_context", "incomplete")
        return
    retained = _sniffed_retained_copies(token, chat_id)
    _record_erasure_store(erasures, "swarm_sniffed_context", "incomplete" if retained else "verified", removed)


def _sniffed_retained_copies(token: str, chat_id: str) -> bool:
    """True when a sniffed copy this operation owns still carries the token."""
    try:
        conn = get_connection()
        try:
            rows = conn.execute("SELECT * FROM sniffed_context").fetchall()
        finally:
            conn.close()
    except Exception:
        return True
    return any(_sniffed_owned(row, token=token, chat_id=chat_id) for row in rows)


@fenced_writer(store_home=lambda values: _authority_home_for_store())
def save_sniffed_context(parent_peer_id: str, prompt_data: Any, result_data: Any) -> None:
    """
    Saves a copy of the incoming prompt and outgoing result into the node's local dataset.
    This builds free intelligence for the helper node in exchange for its compute.
    """
    _init_db()
    conn = get_connection()
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        conn.execute(
            """
            INSERT INTO sniffed_context (parent_peer_id, prompt_json, result_json, timestamp)
            VALUES (?, ?, ?, ?)
            """,
            (parent_peer_id, json.dumps(prompt_data, sort_keys=True), json.dumps(result_data, sort_keys=True), now_iso),
        )
        conn.commit()
    finally:
        conn.close()


def get_recent_contexts(limit: int = 50) -> list[dict[str, Any]]:
    """Retrieves the recent learning dataset snippets, served through the
    shared pending-forget authority like every other retained-memory read."""
    from core.memory.revocation import authorize_payload

    _init_db()
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM sniffed_context ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
        home = _authority_home_for_store()
        return [dict(r) for r in rows if authorize_payload(dict(r), None, home)]
    finally:
        conn.close()


def search_recent_contexts(query_text: str, *, limit: int = 3, search_window: int = 120) -> list[dict[str, Any]]:
    tokens = set(_keyword_tokens(query_text))
    if not tokens:
        return []
    ranked: list[tuple[float, dict[str, Any]]] = []
    for row in get_recent_contexts(limit=max(limit, search_window)):
        prompt_preview = _json_preview(row.get("prompt_json"))
        result_preview = _json_preview(row.get("result_json"))
        joined = f"{prompt_preview} {result_preview}".strip()
        if not joined:
            continue
        item_tokens = set(_keyword_tokens(joined))
        overlap = len(tokens & item_tokens)
        if overlap <= 0:
            continue
        score = overlap / max(1, len(tokens))
        ranked.append(
            (
                score,
                {
                    "parent_peer_id": row.get("parent_peer_id"),
                    "prompt_preview": prompt_preview[:220],
                    "result_preview": result_preview[:220],
                    "timestamp": row.get("timestamp"),
                    "learning_value": float(row.get("learning_value") or 0.0),
                    "score": round(score, 4),
                },
            )
        )
    ranked.sort(key=lambda item: (item[0], str(item[1].get("timestamp") or "")), reverse=True)
    return [row for _, row in ranked[: max(1, int(limit))]]


def _json_preview(value: Any) -> str:
    try:
        parsed = json.loads(str(value or ""))
    except Exception:
        return str(value or "").strip()
    if isinstance(parsed, dict):
        for key in ("summary", "prompt", "query", "task_summary", "result", "output_text", "response"):
            text = str(parsed.get(key) or "").strip()
            if text:
                return text
        return json.dumps(parsed, ensure_ascii=False, sort_keys=True)
    if isinstance(parsed, list):
        return " ".join(str(item) for item in parsed[:6])
    return str(parsed).strip()


def _keyword_tokens(text: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in _WORD_RE.findall(str(text or "").lower()):
        token = raw.strip("'")
        if len(token) < 3 or token in _STOPWORDS or token in seen:
            continue
        seen.add(token)
        out.append(token)
        if len(out) >= 18:
            break
    return out
