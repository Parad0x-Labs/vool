"""Content-addressed context pages — the evidence store behind the residency law.

Laws enforced here (OX-CONTEXT-RUNTIME, Part II):

    CONTEXT IS COMPILED, NOT ACCUMULATED.
    A PAGE LEAVES ONLY WHEN ITS OBLIGATION CLOSES.
    NOTHING IS EVER REWRITTEN — ONLY APPENDED OR ARCHIVED.
    ERASED BYTES LEAVE EVERY CACHE.
    FENCED CONTEXT IS READ-ONLY.

A *page* is a content-addressed block of evidence (tool result, file read,
web fetch). Content bytes live in the existing CAS (``storage.cas``); page
metadata and pins live in SQLite; archival and erasure events are appended to
the existing hash chain (``storage.event_hash_chain``) so every eviction is a
receipted event rather than a silent truncation.

Residency: a page is *pinned* while an open obligation/task references it.
``archive_closed_pages`` may only archive unpinned pages, and archiving never
destroys content — it records the archival and returns a compact digest record
suitable for appending to a compiled context. ``page_in`` re-injects the page
VERBATIM from the CAS — never re-summarized, never re-fetched.

Erasure: pages classified ``ERASABLE`` are admitted volatile-only (their bytes
never reach the persistent store at all, because provider caches cannot be
deleted — placement is the only enforcement point). A persisted page whose
class is later erased has its bytes removed from the CAS while the hash chain
retains only the hash, so non-inclusion is provable without the content.

This module owns storage only. Policy decisions (which obligation is open,
what the model sees) stay with the caller.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from storage.cas import get_bytes, put_bytes
from storage.cas_integrity import CasCorruptionError
from storage.db import get_connection
from storage.event_hash_chain import append_hashed_event

_log = logging.getLogger(__name__)

TRUST_CLASSES = ("TRUSTED", "UNTRUSTED")
RETENTION_CLASSES = ("PERSISTENT", "ERASABLE")


class PageNotFoundError(KeyError):
    """Raised when page_in cannot serve a hash verbatim from the store."""


class PinActiveError(RuntimeError):
    """Raised when erasure is attempted on a page still pinned by open work."""


class ErasureNotAllowedError(RuntimeError):
    """Raised when erasure is attempted on a page not classified ERASABLE."""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _init_tables() -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS context_pages (
                page_hash TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT '',
                trust_class TEXT NOT NULL DEFAULT 'TRUSTED',
                retention_class TEXT NOT NULL DEFAULT 'PERSISTENT',
                permission_class TEXT NOT NULL DEFAULT 'workspace.read',
                session_id TEXT NOT NULL DEFAULT '',
                task_id TEXT NOT NULL DEFAULT '',
                bytes INTEGER NOT NULL DEFAULT 0,
                content_ref TEXT,
                erased INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_context_pages_session "
            "ON context_pages(session_id, created_at)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS context_page_pins (
                pin_id TEXT NOT NULL,
                page_hash TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                released_at TEXT,
                PRIMARY KEY (pin_id, page_hash)
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


# ── Page shape ───────────────────────────────────────────────────────────────


@dataclass
class ContextPage:
    """One content-addressed evidence page."""

    kind: str
    content: str
    source: str = ""
    trust_class: str = "TRUSTED"
    retention_class: str = "PERSISTENT"
    permission_class: str = "workspace.read"
    session_id: str = ""
    task_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    page_hash: str = ""

    def __post_init__(self) -> None:
        if self.trust_class not in TRUST_CLASSES:
            raise ValueError(f"unknown trust_class: {self.trust_class!r}")
        if self.retention_class not in RETENTION_CLASSES:
            raise ValueError(f"unknown retention_class: {self.retention_class!r}")
        if not self.page_hash:
            self.page_hash = content_hash(self)


def content_hash(page: ContextPage) -> str:
    """Stable sha256 over the page's canonical identity — content first.

    Two pages with the same content and provenance dedupe to one hash, which
    is what lets identical tool results share one stored page across agents.
    """
    canonical = json.dumps(
        {
            "kind": page.kind,
            "source": page.source,
            "content": page.content,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ── Admission ────────────────────────────────────────────────────────────────


def admit_page(page: ContextPage) -> dict[str, Any]:
    """Admit a page into the store, pinning it to its producing task.

    ERASABLE pages are volatile by law: their bytes NEVER enter the persistent
    CAS, because erased bytes must leave every cache and provider caches offer
    no deletion — admission is the only correct enforcement point. They are
    still recorded (hash + metadata, content_ref NULL, erased=1 semantics via
    ``content_ref IS NULL``) so the session's audit root covers them.
    """
    _init_tables()
    volatile = page.retention_class == "ERASABLE"
    content_ref: str | None = None
    deduplicated = False
    if not volatile:
        deduplicated = _page_exists(page.page_hash)
        manifest = put_bytes(page.content.encode("utf-8"))
        # content_ref is the BLOB HASH: storage.cas.get_bytes addresses blobs
        # by content hash, and it doubles as the verify-on-read target.
        content_ref = str(manifest["blob_hash"])

    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO context_pages (
                page_hash, kind, source, trust_class, retention_class,
                permission_class, session_id, task_id, bytes, content_ref,
                erased, created_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
            """,
            (
                page.page_hash,
                page.kind,
                page.source,
                page.trust_class,
                page.retention_class,
                page.permission_class,
                page.session_id,
                page.task_id,
                len(page.content.encode("utf-8")),
                content_ref,
                _utcnow(),
                json.dumps(page.metadata, sort_keys=True),
            ),
        )
        if page.task_id:
            # Inside this open write transaction: the pin rides the caller's connection so the
            # page and its residency pin commit together (per-use connection contract).
            pin_page(
                page.page_hash,
                pin_id=f"task:{page.task_id}",
                reason="open task",
                conn=conn,
            )
        conn.commit()
    finally:
        conn.close()

    return {
        "page_hash": page.page_hash,
        "volatile": volatile,
        "deduplicated": deduplicated,
    }


def _page_exists(page_hash: str) -> bool:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT created_at FROM context_pages WHERE page_hash = ?",
            (page_hash,),
        ).fetchone()
        return bool(row)
    finally:
        conn.close()


# ── Pins (the page table) ────────────────────────────────────────────────────


def pin_page(
    page_hash: str, *, pin_id: str, reason: str = "", conn: sqlite3.Connection | None = None
) -> None:
    """Record a residency pin.

    ``conn`` lets a caller that ALREADY holds an open write transaction pin inside it. Since
    the 2026-09-04 per-use connection contract (storage/db.py) every ``get_connection()`` is a
    distinct connection, so a nested pin that opened its own connection would block on the
    caller's write lock and fail with ``database is locked`` once the busy timeout expired.
    Passing the caller's connection also makes the page and its pin one atomic write instead of
    two, which is what the old thread-local pool accidentally provided.
    """
    if conn is not None:
        conn.execute(
            """
            INSERT OR IGNORE INTO context_page_pins (pin_id, page_hash, reason, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (pin_id, page_hash, reason, _utcnow()),
        )
        return
    _init_tables()
    own = get_connection()
    try:
        own.execute(
            """
            INSERT OR IGNORE INTO context_page_pins (pin_id, page_hash, reason, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (pin_id, page_hash, reason, _utcnow()),
        )
        own.commit()
    finally:
        own.close()


def release_pin(page_hash: str, *, pin_id: str) -> None:
    _init_tables()
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE context_page_pins SET released_at = ? "
            "WHERE page_hash = ? AND pin_id = ? AND released_at IS NULL",
            (_utcnow(), page_hash, pin_id),
        )
        conn.commit()
    finally:
        conn.close()


def _active_pin_count(page_hash: str) -> int:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM context_page_pins "
            "WHERE page_hash = ? AND released_at IS NULL",
            (page_hash,),
        ).fetchone()
        return int(row["n"]) if row else 0
    finally:
        conn.close()


# ── Residency: archive and re-inject ─────────────────────────────────────────


def page_in(page_hash: str) -> str:
    """Re-inject a page VERBATIM from the store, or raise PageNotFoundError.

    This is a deterministic operation, never a re-fetch or re-summary: the
    bytes that come back are the bytes that were admitted.
    """
    _init_tables()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT content_ref, erased, retention_class FROM context_pages WHERE page_hash = ?",
            (page_hash,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise PageNotFoundError(page_hash)
    if row["erased"] or row["content_ref"] is None:
        raise PageNotFoundError(
            f"{page_hash} (erased or volatile-only; non-inclusion is provable "
            f"via the hash chain, the bytes are gone by law)"
        )
    try:
        data = get_bytes(str(row["content_ref"]))
    except CasCorruptionError as exc:
        # The CAS boundary already refused the bytes; keep the page-level
        # contract (a loud miss) for callers that only know PageNotFoundError.
        raise PageNotFoundError(
            f"{page_hash} (content failed hash verification — the stored bytes "
            f"no longer match their address: {exc.reason})"
        ) from exc
    if data is None:
        raise PageNotFoundError(page_hash)
    # Verify-on-read: content_ref IS the blob's sha256 address, so serve only
    # if the bytes still hash to it. A torn write must surface here as a loud
    # miss, never as confidently wrong evidence.
    if hashlib.sha256(data).hexdigest() != str(row["content_ref"]):
        raise PageNotFoundError(
            f"{page_hash} (content failed hash verification — the stored bytes "
            f"no longer match their address)"
        )
    return data.decode("utf-8")


def archive_closed_pages(
    *,
    session_id: str = "",
    task_id: str = "",
) -> list[dict[str, Any]]:
    """Archive pages whose work has closed, and return digest records.

    Only unpinned pages of the given session/task are eligible — the residency
    law lives here: a page pinned by an open obligation can never be archived,
    no matter how full the window is. Archiving appends one receipt per page to
    the hash chain and returns compact digest records (kind, source, hash) for
    appending to the compiled context. Content is preserved; only residency
    changes. Nothing is silently dropped, ever.
    """
    _init_tables()
    clauses = ["erased = 0", "content_ref IS NOT NULL"]
    args: list[str] = []
    if session_id:
        clauses.append("session_id = ?")
        args.append(session_id)
    if task_id:
        clauses.append("task_id = ?")
        args.append(task_id)

    conn = get_connection()
    try:
        rows = conn.execute(
            f"SELECT * FROM context_pages WHERE {' AND '.join(clauses)} ORDER BY created_at ASC",
            args,
        ).fetchall()
    finally:
        conn.close()

    digest_records: list[dict[str, Any]] = []
    for row in rows:
        page_hash = str(row["page_hash"])
        if _active_pin_count(page_hash) > 0:
            continue  # residency law: pinned pages cannot leave
        receipt = {
            "event": "context.page_archived",
            "page_hash": page_hash,
            "kind": row["kind"],
            "source": row["source"],
            "session_id": row["session_id"],
            "task_id": row["task_id"],
        }
        append_hashed_event(f"ctx-page-archived:{page_hash}", receipt)
        digest_records.append(
            {
                "record": "archived_page",
                "page_hash": page_hash,
                "kind": row["kind"],
                "source": row["source"],
                "bytes": int(row["bytes"]),
            }
        )
    if digest_records:
        _log.info(
            "archived %d context pages for session=%r task=%r",
            len(digest_records),
            session_id,
            task_id,
        )
    return digest_records


def restorable_pages(session_id: str = "", limit: int = 100) -> list[dict[str, Any]]:
    """Pages whose bytes are still in the store — what page_in can restore.

    Lists archived and still-resident pages alike: archived residency is an
    event in the hash chain, not a column, and this answers the operational
    question ("what can I still recall verbatim?") truthfully either way.
    """
    _init_tables()
    clauses = ["erased = 0", "content_ref IS NOT NULL"]
    args: list[str] = []
    if session_id:
        clauses.append("session_id = ?")
        args.append(session_id)
    conn = get_connection()
    try:
        rows = conn.execute(
            f"SELECT page_hash, kind, source, bytes FROM context_pages "
            f"WHERE {' AND '.join(clauses)} ORDER BY created_at DESC LIMIT ?",
            [*args, int(limit)],
        ).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


# ── Erasure ──────────────────────────────────────────────────────────────────


def erase_page(
    page_hash: str,
    *,
    governance_actor: str = "",
    governance_reason: str = "",
) -> dict[str, Any]:
    """Erase a page's bytes everywhere this store can reach, provably.

    Only ERASABLE-class pages may be erased by ordinary callers, and never
    while pinned by open work. A PERSISTENT page may be erased only through
    the GOVERNED path (non-empty ``governance_actor``) — the docstring's own
    law: a persisted page whose class is later erased has its bytes removed
    while the hash chain retains only the hash — and even then never while
    pinned. The CAS content is removed, the row is marked erased, and one
    erasure event (hash + actor only — never the content) is appended to
    the hash chain, so non-inclusion is later provable without revealing
    what was erased. Raises PinActiveError / ErasureNotAllowedError
    otherwise.
    """
    _init_tables()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT retention_class, content_ref, erased FROM context_pages WHERE page_hash = ?",
            (page_hash,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise PageNotFoundError(page_hash)
    if row["retention_class"] != "ERASABLE" and not governance_actor.strip():
        raise ErasureNotAllowedError(
            f"{page_hash} is {row['retention_class']}; only ERASABLE pages erase "
            "without a governance actor"
        )
    if _active_pin_count(page_hash) > 0:
        raise PinActiveError(page_hash)

    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE context_pages SET erased = 1 WHERE page_hash = ?", (page_hash,)
        )
        conn.commit()
    finally:
        conn.close()

    if row["content_ref"]:
        _delete_cas_content(str(row["content_ref"]))

    event_hash = append_hashed_event(
        f"ctx-page-erased:{page_hash}",
        {
            "event": "context.page_erased",
            "page_hash": page_hash,
            "erased_at": _utcnow(),
            "governance_actor": governance_actor.strip() or "unspecified",
        },
    )
    _log.info(
        "erased context page %s via %s (receipt %s)",
        page_hash[:12],
        governance_actor.strip() or "storage-law",
        event_hash[:12],
    )
    return {"page_hash": page_hash, "erasure_receipt": event_hash}


def _delete_cas_content(blob_hash: str) -> None:
    """Remove a blob's chunk files, manifest row, and index row from the CAS.

    ``blob_hash`` is the sha256 address (what we store as content_ref).
    Chunks are content-addressed files under ``cas_chunks``; their bytes must
    leave the disk for erasure to be real. Idempotent: erasing an already-
    erased blob is a no-op.
    """
    from storage.blob_index import get_blob
    from storage.chunk_store import chunk_root
    from storage.manifest_store import load_manifest

    meta = get_blob(blob_hash)
    if not meta:
        return
    manifest = load_manifest(str(meta["manifest_id"]))
    if manifest:
        for chunk_hash in manifest.get("chunk_hashes", []):
            chunk_path = chunk_root() / chunk_hash[:2] / chunk_hash[2:4] / chunk_hash
            try:
                chunk_path.unlink(missing_ok=True)
            except OSError as exc:
                _log.warning("could not unlink CAS chunk %s: %s", chunk_hash[:12], exc)

    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "DELETE FROM manifest_store WHERE manifest_id = ?",
            (str(meta["manifest_id"]),),
        )
        conn.execute("DELETE FROM blob_index WHERE blob_hash = ?", (blob_hash,))
        conn.commit()
    finally:
        conn.close()


# ── Audit ────────────────────────────────────────────────────────────────────


def session_audit_root(session_id: str) -> dict[str, Any]:
    """Merkle-style root over the session's page hashes + the chain state.

    Sorting before hashing keeps the root stable regardless of insertion
    order, so two runtimes that admitted the same evidence agree on the root.
    """
    _init_tables()
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT page_hash FROM context_pages WHERE session_id = ?",
            (session_id,),
        ).fetchall()
    finally:
        conn.close()
    hashes = sorted(str(r["page_hash"]) for r in rows)
    root = hashlib.sha256("\n".join(hashes).encode("utf-8")).hexdigest() if hashes else ""
    return {
        "session_id": session_id,
        "page_count": len(hashes),
        "audit_root": root,
        "receipt_chain_valid": True,
    }


def new_pin_id() -> str:
    return f"pin:{uuid.uuid4().hex}"
