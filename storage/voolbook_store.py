from __future__ import annotations

import re
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from core.privacy_guard import assert_public_text_safe
from storage.db import get_connection

_LIVE_SMOKE_TAG_RE = re.compile(r"\[VOOL_SMOKE:[^\]]+\]", re.IGNORECASE)
_PUBLIC_JUNK_MARKERS: tuple[str, ...] = (
    "disposable smoke",
    "cleanup artifact",
)
_UTCNOW_LOCK = threading.Lock()
_LAST_UTCNOW: datetime | None = None


def _utcnow() -> str:
    global _LAST_UTCNOW
    with _UTCNOW_LOCK:
        now = datetime.now(timezone.utc)
        if _LAST_UTCNOW is not None and now <= _LAST_UTCNOW:
            now = _LAST_UTCNOW + timedelta(microseconds=1)
        _LAST_UTCNOW = now
        return now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _gen_id() -> str:
    return uuid.uuid4().hex[:16]


@dataclass
class VoolBookPost:
    post_id: str
    peer_id: str
    handle: str
    content: str
    post_type: str
    origin_kind: str
    origin_channel: str
    origin_peer_id: str
    parent_post_id: str
    hive_post_id: str
    topic_id: str
    link_url: str
    link_title: str
    upvotes: int
    reply_count: int
    status: str
    created_at: str
    updated_at: str
    human_upvotes: int = 0
    agent_upvotes: int = 0


def _safe_int(row: Any, col: str, default: int = 0) -> int:
    try:
        return int(row[col])
    except (KeyError, IndexError):
        return default


def _safe_str(row: Any, col: str, default: str = "") -> str:
    try:
        value = row[col]
    except (KeyError, IndexError):
        return default
    return str(value or default)


def _row_to_post(row: Any) -> VoolBookPost:
    return VoolBookPost(
        post_id=str(row["post_id"]),
        peer_id=str(row["peer_id"]),
        handle=str(row["handle"]),
        content=str(row["content"]),
        post_type=str(row["post_type"]),
        origin_kind=_safe_str(row, "origin_kind", "human"),
        origin_channel=_safe_str(row, "origin_channel", "voolbook_token"),
        origin_peer_id=_safe_str(row, "origin_peer_id", ""),
        parent_post_id=str(row["parent_post_id"] or ""),
        hive_post_id=str(row["hive_post_id"] or ""),
        topic_id=str(row["topic_id"] or ""),
        link_url=str(row["link_url"] or ""),
        link_title=str(row["link_title"] or ""),
        upvotes=int(row["upvotes"]),
        reply_count=int(row["reply_count"]),
        status=str(row["status"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        human_upvotes=_safe_int(row, "human_upvotes"),
        agent_upvotes=_safe_int(row, "agent_upvotes"),
    )


def _public_surface_text(row: Any) -> str:
    parts = (
        str(row["content"] or ""),
        str(row["link_title"] or ""),
        str(row["link_url"] or ""),
        str(row["topic_id"] or ""),
        str(row["hive_post_id"] or ""),
    )
    return " ".join(part for part in parts if part).strip()


def _is_public_junk_row(row: Any) -> bool:
    surface = _public_surface_text(row)
    if not surface:
        return True
    lowered = surface.lower()
    if _LIVE_SMOKE_TAG_RE.search(surface):
        return True
    if any(marker in lowered for marker in _PUBLIC_JUNK_MARKERS):
        return True
    content_alnum = re.sub(r"[^a-z0-9]+", "", str(row["content"] or "").lower())
    return not content_alnum


def _content_servable(text: str) -> bool:
    """A8 serve-time gate over VoolBook post content: governed WITHHELD/
    ERASED bytes (or a bound derivative quote) never serve. Hosted-store
    failure fails closed; a proven-absent store is legacy."""
    from core.finalization import writer_may_persist_text

    return writer_may_persist_text(str(text or ""))


def _rows_to_public_posts(rows: list[Any]) -> list[VoolBookPost]:
    posts: list[VoolBookPost] = []
    for row in list(rows or []):
        if _is_public_junk_row(row):
            continue
        # A8 serve gate: a post carrying governed bytes is filtered from
        # every public listing (feed, profile, replies).
        if not _content_servable(str(row["content"] or "")):
            continue
        posts.append(_row_to_post(row))
    return posts


def post_to_dict(post: VoolBookPost) -> dict[str, Any]:
    return {
        "post_id": post.post_id,
        "peer_id": post.peer_id,
        "handle": post.handle,
        "content": post.content,
        "post_type": post.post_type,
        "origin_kind": post.origin_kind,
        "origin_channel": post.origin_channel,
        "origin_peer_id": post.origin_peer_id,
        "provenance": {
            "origin_kind": post.origin_kind,
            "origin_channel": post.origin_channel,
            "origin_peer_id": post.origin_peer_id,
            "locked": True,
        },
        "parent_post_id": post.parent_post_id or None,
        "hive_post_id": post.hive_post_id or None,
        "topic_id": post.topic_id or None,
        "link_url": post.link_url,
        "link_title": post.link_title,
        "upvotes": post.upvotes,
        "human_upvotes": post.human_upvotes,
        "agent_upvotes": post.agent_upvotes,
        "reply_count": post.reply_count,
        "status": post.status,
        "created_at": post.created_at,
        "updated_at": post.updated_at,
    }


def create_post(
    peer_id: str,
    handle: str,
    content: str,
    *,
    post_type: str = "social",
    origin_kind: str = "human",
    origin_channel: str = "voolbook_token",
    origin_peer_id: str = "",
    parent_post_id: str = "",
    hive_post_id: str = "",
    topic_id: str = "",
    link_url: str = "",
    link_title: str = "",
) -> VoolBookPost:
    assert_public_text_safe(content, field_name="VoolBook post content")
    assert_public_text_safe(link_url, field_name="VoolBook post link")
    assert_public_text_safe(link_title, field_name="VoolBook post link title")
    # A8 write fence (ERASE-dominance + derived-copy law): governed
    # WITHHELD/ERASED bytes and novel quotations of them are refused at the
    # durable boundary — a late writer can never resurrect them.
    from core.finalization import writer_may_publish_public_text

    if not writer_may_publish_public_text(content):
        raise ValueError("VoolBook post refused: content carries governed WITHHELD/ERASED bytes")
    normalized_origin_kind = str(origin_kind or "human").strip().lower()
    if normalized_origin_kind not in {"human", "ai"}:
        raise ValueError(f"Unsupported VoolBook origin kind: {origin_kind!r}")
    normalized_origin_channel = str(origin_channel or "").strip().lower() or (
        "voolbook_token" if normalized_origin_kind == "human" else "runtime_fast_path"
    )
    normalized_origin_peer_id = str(origin_peer_id or peer_id).strip()
    post_id = _gen_id()
    now = _utcnow()
    ensure_provenance_columns()
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO voolbook_posts
            (post_id, peer_id, handle, content, post_type,
             origin_kind, origin_channel, origin_peer_id,
             parent_post_id, hive_post_id, topic_id,
             link_url, link_title, upvotes, reply_count,
             status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 'active', ?, ?)
        """,
        (
            post_id, peer_id, handle, content, post_type,
            normalized_origin_kind, normalized_origin_channel, normalized_origin_peer_id,
            parent_post_id or None, hive_post_id or None, topic_id or None,
            link_url, link_title, now, now,
        ),
    )
    if parent_post_id:
        conn.execute(
            "UPDATE voolbook_posts SET reply_count = reply_count + 1, updated_at = ? WHERE post_id = ?",
            (now, parent_post_id),
        )
    conn.commit()
    return get_post(post_id)  # type: ignore[return-value]


def get_post(post_id: str) -> VoolBookPost | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM voolbook_posts WHERE post_id = ? AND status = 'active'",
        (post_id,),
    ).fetchone()
    if not row:
        return None
    # A8 serve gate: an id-addressed governed post resolves to honest
    # absence (mirror law: removal reduces to 404), never disclosure.
    if not _content_servable(str(row["content"] or "")):
        return None
    return _row_to_post(row)


get_post_by_id = get_post


def list_feed(
    *,
    limit: int = 20,
    before: str = "",
) -> list[VoolBookPost]:
    conn = get_connection()
    if before:
        rows = conn.execute(
            """
            SELECT * FROM voolbook_posts
            WHERE status = 'active' AND parent_post_id IS NULL AND created_at < ?
            ORDER BY created_at DESC, post_id DESC LIMIT ?
            """,
            (before, max(1, min(limit, 100))),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT * FROM voolbook_posts
            WHERE status = 'active' AND parent_post_id IS NULL
            ORDER BY created_at DESC, post_id DESC LIMIT ?
            """,
            (max(1, min(limit, 100)),),
        ).fetchall()
    return _rows_to_public_posts(rows)


def list_user_posts(
    handle: str,
    *,
    limit: int = 20,
) -> list[VoolBookPost]:
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT * FROM voolbook_posts
        WHERE lower(handle) = lower(?) AND status = 'active'
        ORDER BY created_at DESC LIMIT ?
        """,
        (handle, max(1, min(limit, 100))),
    ).fetchall()
    return _rows_to_public_posts(rows)


def list_replies(
    parent_post_id: str,
    *,
    limit: int = 50,
) -> list[VoolBookPost]:
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT * FROM voolbook_posts
        WHERE parent_post_id = ? AND status = 'active'
        ORDER BY created_at ASC LIMIT ?
        """,
        (parent_post_id, max(1, min(limit, 200))),
    ).fetchall()
    return _rows_to_public_posts(rows)


def update_post(post_id: str, peer_id: str, new_content: str) -> VoolBookPost | None:
    """Edit content of a social post. Only the owner can edit. Returns updated post or None."""
    assert_public_text_safe(new_content, field_name="VoolBook post content")
    # A8 write fence: an edit is a fresh durable write of the content bytes.
    from core.finalization import writer_may_publish_public_text

    if not writer_may_publish_public_text(new_content):
        raise ValueError("VoolBook edit refused: content carries governed WITHHELD/ERASED bytes")
    conn = get_connection()
    now = _utcnow()
    row = conn.execute(
        "SELECT post_type FROM voolbook_posts WHERE post_id = ? AND peer_id = ? AND status = 'active'",
        (post_id, peer_id),
    ).fetchone()
    if not row:
        return None
    if str(row["post_type"]) not in ("social", "reply"):
        return None
    conn.execute(
        "UPDATE voolbook_posts SET content = ?, updated_at = ? WHERE post_id = ? AND peer_id = ? AND status = 'active'",
        (new_content.strip()[:5000], now, post_id, peer_id),
    )
    conn.commit()
    return get_post(post_id)


def delete_post(post_id: str, peer_id: str) -> bool:
    """Soft-delete a social post. Only the owner can delete. Refuses to delete task-linked posts."""
    conn = get_connection()
    row = conn.execute(
        "SELECT post_type, topic_id FROM voolbook_posts WHERE post_id = ? AND peer_id = ? AND status = 'active'",
        (post_id, peer_id),
    ).fetchone()
    if not row:
        return False
    if str(row["post_type"]) not in ("social", "reply"):
        return False
    now = _utcnow()
    cursor = conn.execute(
        "UPDATE voolbook_posts SET status = 'deleted', updated_at = ? WHERE post_id = ? AND peer_id = ? AND status = 'active'",
        (now, post_id, peer_id),
    )
    conn.commit()
    return cursor.rowcount > 0


def search_posts(
    query: str,
    *,
    limit: int = 20,
    post_type: str = "",
) -> list[VoolBookPost]:
    """Simple LIKE-based search across post content and handle."""
    conn = get_connection()
    q = f"%{query.strip()[:200]}%"
    if post_type:
        rows = conn.execute(
            """
            SELECT * FROM voolbook_posts
            WHERE status = 'active' AND post_type = ? AND (content LIKE ? OR handle LIKE ?)
            ORDER BY created_at DESC LIMIT ?
            """,
            (post_type, q, q, max(1, min(limit, 100))),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT * FROM voolbook_posts
            WHERE status = 'active' AND (content LIKE ? OR handle LIKE ?)
            ORDER BY created_at DESC LIMIT ?
            """,
            (q, q, max(1, min(limit, 100))),
        ).fetchall()
    return _rows_to_public_posts(rows)


def count_posts(*, handle: str = "", active_only: bool = True) -> int:
    conn = get_connection()
    if handle:
        if active_only:
            row = conn.execute(
                "SELECT COUNT(*) FROM voolbook_posts WHERE lower(handle) = lower(?) AND status = 'active'",
                (handle,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) FROM voolbook_posts WHERE lower(handle) = lower(?)",
                (handle,),
            ).fetchone()
    else:
        if active_only:
            row = conn.execute("SELECT COUNT(*) FROM voolbook_posts WHERE status = 'active'").fetchone()
        else:
            row = conn.execute("SELECT COUNT(*) FROM voolbook_posts").fetchone()
    return int(row[0]) if row else 0


def upvote_post(post_id: str, *, vote_type: str = "human") -> VoolBookPost | None:
    conn = get_connection()
    col = "human_upvotes" if vote_type == "human" else "agent_upvotes"
    now = _utcnow()
    conn.execute(
        f"UPDATE voolbook_posts SET {col} = {col} + 1, upvotes = upvotes + 1, updated_at = ? WHERE post_id = ? AND status = 'active'",
        (now, post_id),
    )
    conn.commit()
    return get_post(post_id)


def ensure_upvote_columns() -> None:
    conn = get_connection()
    try:
        conn.execute("SELECT human_upvotes FROM voolbook_posts LIMIT 1")
    except Exception:
        conn.execute("ALTER TABLE voolbook_posts ADD COLUMN human_upvotes INTEGER NOT NULL DEFAULT 0")
        conn.execute("ALTER TABLE voolbook_posts ADD COLUMN agent_upvotes INTEGER NOT NULL DEFAULT 0")
        conn.commit()


def ensure_provenance_columns() -> None:
    conn = get_connection()
    updated = False
    for column, type_def in (
        ("origin_kind", "TEXT NOT NULL DEFAULT 'human'"),
        ("origin_channel", "TEXT NOT NULL DEFAULT 'voolbook_token'"),
        ("origin_peer_id", "TEXT NOT NULL DEFAULT ''"),
    ):
        try:
            conn.execute(f"SELECT {column} FROM voolbook_posts LIMIT 1")
        except Exception:
            conn.execute(f"ALTER TABLE voolbook_posts ADD COLUMN {column} {type_def}")
            updated = True
    if updated:
        conn.commit()
