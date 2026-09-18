from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

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
    """Retrieves the recent learning dataset snippets."""
    _init_db()
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM sniffed_context ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
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
