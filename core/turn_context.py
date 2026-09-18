"""Turn context authority — C09 + C10 + the C13 context/privacy portion.

ONE authority wiring the existing evidence store (``core.context_pages``),
the A8 privacy gates (``core.finalization``) and the append-only layout laws
(``core.context_layout``) into real served turns. This module owns SCOPE,
LIFECYCLE and POLICY for turn context; ``core.context_pages`` keeps owning
storage and the residency law. It deliberately does NOT create another
memory store, context selector, or a raw ``page_in(hash)`` product surface:
every product-facing operation is addressed by scoped ``admission_id`` and
re-checked against the caller's principal/session/project scope.

Laws enforced here (OX-CONTEXT-RUNTIME Parts II–III + A8):

    CONTEXT IS COMPILED, NOT ACCUMULATED — admission is scoped, compile is
    deterministic, and what a turn sees is decided HERE, not by the caller.
    A FROZEN HEADER DOES NOT MUTATE WITHIN A GENERATION —
    ``assert_frozen_stable`` runs on every compile; the legal escape is
    :func:`bump_context_generation`, never a silent rewrite.
    GOVERNANCE HISTORY IS APPEND-ONLY — withhold/erase/supersede/archive
    append digest lines; ``assert_digest_append_only`` runs on every append.
    ERASED BYTES LEAVE EVERY CACHE WE REACH — erasure releases pins, marks
    the admission, and removes the stored bytes when the LAST admission
    referencing them lets go (content-addressed dedup means other scopes
    may still legitimately hold identical bytes).
    WITHHELD/ERASED CONTEXT IS NEVER COMPILED — every page re-passes the
    A8 availability gates at read time, fail-closed.
    A CACHE HIT IS MEASURED, NEVER INFERRED — tool-memo hit truth is
    stamped by the only component that mechanically knows it; a provider
    prompt-cache verdict is UNMEASURED here and is never claimed from
    layout-hash equality (an equal local hash says nothing about what a
    provider actually kept resident).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from core.context_layout import (
    assert_digest_append_only,
    assert_frozen_stable,
)
from core.context_pages import (
    ContextPage,
    PageNotFoundError,
    PinActiveError,
    admit_page,
    archive_closed_pages,
    erase_page,
    new_pin_id,
    page_in,
    pin_page,
    release_pin,
)

_log = logging.getLogger(__name__)

SCHEMA_VERSION = 1

# How many characters of one admitted page the served turn path may admit.
# The cap is enforced by the caller at admission with an EXPLICIT marker in
# the stored bytes — never a silent cut.
ADMISSION_CONTENT_CAP_CHARS = 8000

ORIGINS = ("explicit", "inferred")
ADMISSION_STATUSES = ("active", "superseded", "withheld", "erased")
# Origin a page must carry to be shared across sessions of one project.
_PROJECT_SHARED_ORIGINS = ("explicit",)

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_-]{2,}")
_STOPWORDS = frozenset(
    ["the", "and", "that", "this", "with", "what", "when", "where", "how", "why", "who", "you", "your", "our", "for", "from", "about", "into", "over", "under", "again", "then", "once", "here", "there", "all", "any", "both", "each", "few", "more", "most", "other", "some", "such", "only", "own", "same", "than", "too", "very", "can", "will", "just", "should", "now", "has", "have", "had", "was", "were", "are", "been", "being", "did", "does", "doing", "its", "not", "but", "they", "them", "their", "she", "he", "him", "his", "her", "hers", "ours", "yours", "theirs", "myself", "yourself", "himself", "herself", "itself", "themselves", "please", "tell", "give", "show", "explain", "help", "need", "want", "know", "think", "say", "like", "also", "get", "got", "make", "made", "take", "took", "come", "came", "look", "looked"]
)


class AdmissionNotFoundError(KeyError):
    """No admission with this id in the caller's scope."""


class AdmissionScopeError(PermissionError):
    """The admission exists but not inside the caller's principal scope."""


class AdmissionClosedError(RuntimeError):
    """The admission is withheld/erased and cannot serve context anymore."""


class AdmissionRefusedError(RuntimeError):
    """The A8 write fence refused these bytes; nothing was stored."""


# ── Scope and records ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TurnScope:
    """WHO a context page belongs to and at which turn/generation it arose."""

    principal: str
    session_id: str
    project_id: str = ""
    turn_id: str = ""
    generation: int = 0

    def with_generation(self, generation: int) -> TurnScope:
        return TurnScope(
            principal=self.principal,
            session_id=self.session_id,
            project_id=self.project_id,
            turn_id=self.turn_id,
            generation=int(generation),
        )


@dataclass
class ContextSelection:
    """One page selected into a compiled context block."""

    admission_id: str
    page_hash: str
    title: str
    origin: str
    source_kind: str
    pinned: bool
    bytes: int
    content: str
    generation: int = 0

    def render(self) -> str:
        stamp = (
            f"[page admission={self.admission_id[:12]} origin={self.origin} "
            f"source={self.source_kind} pinned={str(self.pinned).lower()}]"
        )
        return f"{stamp}\n{self.content}\n[/page]"


@dataclass
class TurnContextCompilation:
    """The compiled turn-context block plus its honest receipt."""

    principal: str = ""
    session_id: str = ""
    generation: int = 0
    items: list[ContextSelection] = field(default_factory=list)
    exclusions: list[dict[str, Any]] = field(default_factory=list)
    truncations: list[dict[str, Any]] = field(default_factory=list)
    frozen_hash: str = ""
    digest_text: str = ""
    layout_stable_within_generation: bool = True
    block_text: str = ""

    @property
    def selected(self) -> list[dict[str, Any]]:
        return [
            {
                "admission_id": item.admission_id,
                "page_hash": item.page_hash,
                "title": item.title,
                "origin": item.origin,
                "source_kind": item.source_kind,
                "pinned": item.pinned,
                "bytes": item.bytes,
                "generation": item.generation,
            }
            for item in self.items
        ]

    def receipt(self) -> dict[str, Any]:
        """Honest accounting of what entered the block and why.

        ``provider_cache`` is an explicit UNMEASURED marker: nothing here
        may claim a provider prompt-cache hit — an equal local layout hash
        is not evidence about what a provider kept resident. The only
        measured cache verdicts in this runtime are the tool-memo ones the
        tool results themselves carry.
        """
        return {
            "authority": "core.turn_context",
            "schema_version": SCHEMA_VERSION,
            "generation": self.generation,
            "selected": self.selected,
            "exclusions": list(self.exclusions),
            "truncations": list(self.truncations),
            "frozen_hash": self.frozen_hash,
            "layout_stable_within_generation": self.layout_stable_within_generation,
            "provider_cache": "unmeasured",
            "cache_hit_measured": False,
        }


# ── Storage ──────────────────────────────────────────────────────────────────


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connection():
    from storage.db import get_connection

    return get_connection()


def _init_tables() -> None:
    conn = _connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS turn_context_admissions (
                admission_id TEXT PRIMARY KEY,
                page_hash TEXT NOT NULL,
                principal TEXT NOT NULL,
                session_id TEXT NOT NULL,
                project_id TEXT NOT NULL DEFAULT '',
                turn_id TEXT NOT NULL DEFAULT '',
                generation INTEGER NOT NULL DEFAULT 0,
                origin TEXT NOT NULL DEFAULT 'inferred',
                source_kind TEXT NOT NULL DEFAULT 'chat_exchange',
                title TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'active',
                bytes INTEGER NOT NULL DEFAULT 0,
                request_id TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_turn_context_scope "
            "ON turn_context_admissions(principal, session_id, status, created_at)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS turn_context_generations (
                principal TEXT NOT NULL,
                session_id TEXT NOT NULL,
                generation INTEGER NOT NULL DEFAULT 1,
                frozen_hash TEXT NOT NULL DEFAULT '',
                digest_text TEXT NOT NULL DEFAULT '',
                last_frontier_hash TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL,
                PRIMARY KEY (principal, session_id)
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


# ── Frozen header / digest laws ──────────────────────────────────────────────


def frozen_header(principal: str, session_id: str) -> str:
    """The stable prefix of every compiled block in a generation.

    Deterministic by law: no clock, no counters, no model identity. A
    mutation here is the #1 silent cache buster, so :func:`compile_turn_context`
    verifies stability against the generation row and raises loudly; the
    legal escape is :func:`bump_context_generation`.
    """
    return (
        f"[turn-context v{SCHEMA_VERSION} "
        f"principal={principal} session={session_id}]"
    )


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _append_digest_line(conn, principal: str, session_id: str, line: str) -> None:
    """Append one governance line to the session digest, append-only."""
    row = conn.execute(
        "SELECT digest_text FROM turn_context_generations "
        "WHERE principal = ? AND session_id = ?",
        (principal, session_id),
    ).fetchone()
    previous = str(row["digest_text"]) if row else ""
    stamp = _utcnow()
    new_digest = f"{previous}{line} @ {stamp}\n" if previous else f"{line} @ {stamp}\n"
    # The layout law, enforced on REAL state: the previous digest must
    # survive byte-for-byte as a prefix of the new one.
    assert_digest_append_only({}, previous, new_digest)
    conn.execute(
        """
        INSERT INTO turn_context_generations (
            principal, session_id, generation, frozen_hash, digest_text,
            last_frontier_hash, updated_at
        ) VALUES (?, ?, 1, '', ?, '', ?)
        ON CONFLICT(principal, session_id) DO UPDATE SET
            digest_text = excluded.digest_text, updated_at = excluded.updated_at
        """,
        (principal, session_id, new_digest, stamp),
    )


def _digest_line(event: str, admission_id: str, detail: str = "") -> str:
    short = admission_id[:12] if admission_id else "-"
    # Digest lines are audit lines, never content: no page text, no payload.
    base = f"{event} admission={short}"
    return f"{base} {detail}".rstrip()


def note_governance_event(
    principal: str,
    session_id: str,
    *,
    event: str,
    admission_id: str = "",
    detail: str = "",
) -> None:
    """Record one governance event in the session's append-only digest."""
    _init_tables()
    conn = _connection()
    try:
        _append_digest_line(
            conn, principal, session_id, _digest_line(event, admission_id, detail)
        )
        conn.commit()
    finally:
        conn.close()


# ── Admission ────────────────────────────────────────────────────────────────


def resolve_principal(source_context: dict[str, Any] | None) -> str:
    """Principal for a request: the trusted owner, else the channel identity.

    Mirrors the served door's own rule (``core.web.api.service``): loopback
    owner requests are ``owner_local``; everything else is bound to its
    channel so foreign surfaces can never read owner-scoped context.
    """
    from core.request_trust import request_is_owner_local

    context = source_context if isinstance(source_context, dict) else {}
    if request_is_owner_local(context):
        return "owner_local"
    host = str(context.get("client_host") or context.get("channel") or "").strip()
    return f"channel:{host or 'unknown'}"


def admit_turn_context(
    content: str,
    *,
    scope: TurnScope,
    source_kind: str = "chat_exchange",
    title: str = "",
    origin: str = "inferred",
    retention_class: str = "PERSISTENT",
    trust_class: str = "TRUSTED",
    request_id: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Admit one bounded page of turn context under a full scope identity.

    PERSISTENT retention (the default) is what makes context survive a
    restart: bytes go to the CAS, the scoped admission row to SQLite, and
    the residency pin (``task:<turn_id>``) to the pin table. ERASABLE
    pages stay volatile-only by the storage law. The A8 write fence is
    consulted BEFORE any byte persists: text the authority has erased is
    refused at the door, typed, and stored nowhere.
    """
    if origin not in ORIGINS:
        raise ValueError(f"unknown origin: {origin!r}")
    if not str(scope.principal or "").strip() or not str(scope.session_id or "").strip():
        raise ValueError("admission requires a principal and a session scope")
    text = str(content or "")
    if not text.strip():
        raise ValueError("refusing to admit empty context")

    from core.finalization import writer_may_persist_text

    if not writer_may_persist_text(text):
        raise AdmissionRefusedError(
            "A8 write fence refused admission: the payload is governed-erased"
        )

    page = ContextPage(
        kind="turn_context",
        content=text,
        source=f"turn-context:{source_kind}",
        trust_class=trust_class,
        retention_class=retention_class,
        permission_class="workspace.read",
        session_id=scope.session_id,
        # Storage-level residency pin: an open turn holds its page.
        task_id=scope.turn_id or "",
        metadata=dict(metadata or {}),
    )
    stored = admit_page(page)

    _init_tables()
    generation = scope.generation or current_generation(scope.principal, scope.session_id)
    admission_id = uuid.uuid4().hex
    conn = _connection()
    try:
        conn.execute(
            """
            INSERT INTO turn_context_admissions (
                admission_id, page_hash, principal, session_id, project_id,
                turn_id, generation, origin, source_kind, title, status,
                bytes, request_id, metadata_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?)
            """,
            (
                admission_id,
                page.page_hash,
                scope.principal,
                scope.session_id,
                scope.project_id or "",
                scope.turn_id or "",
                int(generation),
                origin,
                source_kind,
                title or source_kind,
                len(text.encode("utf-8")),
                request_id or "",
                json.dumps(dict(metadata or {}), sort_keys=True),
                _utcnow(),
                _utcnow(),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    _log.info(
        "admitted turn context %s session=%s turn=%s origin=%s bytes=%d",
        admission_id[:12],
        scope.session_id,
        scope.turn_id or "-",
        origin,
        len(text.encode("utf-8")),
    )
    return {
        "admission_id": admission_id,
        "page_hash": page.page_hash,
        "pinned": bool(scope.turn_id),
        "volatile": bool(stored.get("volatile")),
        "deduplicated": bool(stored.get("deduplicated")),
        "generation": int(generation),
    }


def _admission_row(admission_id: str, principal: str) -> dict[str, Any] | None:
    _init_tables()
    conn = _connection()
    try:
        row = conn.execute(
            "SELECT * FROM turn_context_admissions WHERE admission_id = ?",
            (admission_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    record = dict(row)
    if str(record["principal"]) != principal:
        raise AdmissionScopeError(
            f"admission {admission_id[:12]} is not in principal {principal!r}'s scope"
        )
    record["metadata"] = json.loads(str(record.get("metadata_json") or "{}"))
    return record


def _turn_pin_id(turn_id: str) -> str:
    return f"task:{turn_id}"


def _release_known_pins(record: dict[str, Any]) -> list[str]:
    """Release every pin THIS authority knows the page holds."""
    page_hash = str(record["page_hash"])
    released: list[str] = []
    turn_id = str(record["turn_id"] or "")
    if turn_id:
        release_pin(page_hash, pin_id=_turn_pin_id(turn_id))
        released.append(_turn_pin_id(turn_id))
    manual_pins = list((record.get("metadata") or {}).get("manual_pins") or [])
    for pin_id in manual_pins:
        release_pin(page_hash, pin_id=str(pin_id))
        released.append(str(pin_id))
    return released


# ── Generation state ─────────────────────────────────────────────────────────


def close_turn_scope(principal: str, session_id: str, turn_id: str) -> int:
    """Close one turn's obligations: release its residency pins.

    A completed turn is a closed obligation — "a page leaves only when its
    obligation closes". Pages stay resident (restart persistence is storage,
    not pins); closing only ends the pin's exemption from relevance and
    budget. Explicit operator pins are unaffected.
    """
    clean = str(turn_id or "").strip()
    if not clean:
        return 0
    _init_tables()
    conn = _connection()
    try:
        rows = conn.execute(
            "SELECT admission_id, page_hash, metadata_json FROM turn_context_admissions "
            "WHERE principal = ? AND session_id = ? AND turn_id = ? AND status = 'active'",
            (principal, session_id, clean),
        ).fetchall()
    finally:
        conn.close()
    released = 0
    for row in rows:
        release_pin(str(row["page_hash"]), pin_id=_turn_pin_id(clean))
        released += 1
    if released:
        _log.info(
            "closed turn scope %s/%s: %d pin(s) released",
            session_id,
            clean[:12],
            released,
        )
    return released


def current_generation(principal: str, session_id: str) -> int:
    _init_tables()
    conn = _connection()
    try:
        row = conn.execute(
            "SELECT generation FROM turn_context_generations "
            "WHERE principal = ? AND session_id = ?",
            (principal, session_id),
        ).fetchone()
        return int(row["generation"]) if row else 1
    finally:
        conn.close()


def bump_context_generation(
    principal: str,
    session_id: str,
    *,
    reason: str = "",
    actor: str = "operator",
) -> int:
    """Start a new context generation for one session.

    The legal escape hatch for a frozen-zone change (model switch with a
    different stable prefix, policy change, explicit operator reset): the
    generation counter moves, the frozen guard re-arms, and the event is
    receipted in the digest.
    """
    _init_tables()
    generation = current_generation(principal, session_id) + 1
    conn = _connection()
    try:
        conn.execute(
            """
            INSERT INTO turn_context_generations (
                principal, session_id, generation, frozen_hash, digest_text,
                last_frontier_hash, updated_at
            ) VALUES (?, ?, ?, '', '', '', ?)
            ON CONFLICT(principal, session_id) DO UPDATE SET
                generation = excluded.generation,
                frozen_hash = '',
                last_frontier_hash = '',
                updated_at = excluded.updated_at
            """,
            (principal, session_id, generation, _utcnow()),
        )
        _append_digest_line(
            conn,
            principal,
            session_id,
            _digest_line("generation_bumped", "", f"generation={generation} reason={reason or 'unspecified'} actor={actor}"),
        )
        conn.commit()
    finally:
        conn.close()
    return generation


# ── Relevance (deterministic, explainable) ───────────────────────────────────


def _tokens(text: str) -> set[str]:
    return {
        token for token in _TOKEN_RE.findall(str(text or "").lower())
        if token not in _STOPWORDS
    }


def relevance_score(query_text: str, *, title: str, content: str, source_kind: str) -> float:
    """Deterministic token-overlap relevance in [0, 1].

    Mechanical and explainable by design: the share of distinct query terms
    (stopwords removed) that appear in the page's title/source/content. No
    model, no embedding, no clock — the same inputs always score the same,
    and every exclusion can name its score.
    """
    query_terms = _tokens(query_text)
    if not query_terms:
        return 0.0
    haystack = _tokens(f"{title} {source_kind} {content}")
    if not haystack:
        return 0.0
    overlap = sum(1 for term in query_terms if term in haystack)
    return overlap / len(query_terms)


RELEVANCE_THRESHOLD = 0.1


# ── A8 availability gates ────────────────────────────────────────────────────


def _a8_exclusion(record: dict[str, Any], content: str | None) -> str | None:
    """The A8 availability verdict for one admission, or None when servable.

    Two mechanical gates, most restrictive wins: the page's bound request
    lineage and the page text itself. An unreadable governance store is
    fail-closed (excluded), matching the served transcript reader's law.
    Only a positively ABSENT store resolves ungoverned.
    """
    from core.finalization import (
        governance_store_ready,
        payload_availability_for_request_id,
        payload_availability_for_text,
    )

    if not governance_store_ready():
        return None
    verdicts: list[str] = []
    try:
        request_id = str(record.get("request_id") or "")
        if request_id:
            verdict = payload_availability_for_request_id(request_id)
            if verdict is not None:
                verdicts.append(verdict)
        if content is not None:
            verdict = payload_availability_for_text(content)
            if verdict is not None:
                verdicts.append(verdict)
    except Exception:
        return "governance_unavailable"
    if "ERASED" in verdicts:
        return "ERASED"
    if "WITHHELD" in verdicts:
        return "WITHHELD"
    return None


# ── Compile ──────────────────────────────────────────────────────────────────


def compile_turn_context(
    principal: str,
    session_id: str,
    *,
    project_id: str = "",
    query_text: str = "",
    budget_chars: int = 4000,
    source_context: dict[str, Any] | None = None,
) -> TurnContextCompilation:
    """Compile the scoped turn-context block for one turn, or an empty one.

    Deterministic given (admitted pages, scope, query, budget). Selection
    order: pinned pages first (residency law — an open obligation holds its
    page), then unpinned pages newest-first. Overflow is EXPLICIT: pages
    that do not fit are named in ``truncations``, never silently dropped.
    Pinned pages are never truncated away for budget; if they alone exceed
    the budget the receipt says so.
    """
    _init_tables()
    compilation = TurnContextCompilation(
        principal=principal, session_id=session_id
    )
    if not str(principal).strip() or not str(session_id).strip():
        return compilation

    conn = _connection()
    try:
        gen_row = conn.execute(
            "SELECT generation, frozen_hash, digest_text, last_frontier_hash "
            "FROM turn_context_generations WHERE principal = ? AND session_id = ?",
            (principal, session_id),
        ).fetchone()
        # Scope union: every live page of THIS session, plus pages the
        # operator explicitly granted to THIS project (cross-session project
        # sharing is opt-in by origin — inferred pages never cross sessions).
        # WITHHELD pages stay in the candidate set so the receipt can name
        # them as governed exclusions; erased pages are gone by law.
        rows = conn.execute(
            "SELECT * FROM turn_context_admissions "
            "WHERE principal = ? AND status IN ('active', 'withheld') AND "
            "(session_id = ? OR (project_id != '' AND project_id = ? "
            " AND origin = 'explicit')) "
            "ORDER BY created_at DESC",
            (principal, session_id, project_id or ""),
        ).fetchall()
    finally:
        conn.close()

    generation = int(gen_row["generation"]) if gen_row else 1
    previous_frozen = str(gen_row["frozen_hash"]) if gen_row else ""
    digest_text = str(gen_row["digest_text"]) if gen_row else ""
    previous_frontier = str(gen_row["last_frontier_hash"]) if gen_row else ""
    compilation.generation = generation
    compilation.digest_text = digest_text

    header = frozen_header(principal, session_id)
    current_frozen = _sha256_text(header)
    compilation.frozen_hash = current_frozen
    if previous_frozen:
        # The frozen guard, on real state: within one generation the stable
        # prefix may not change. A changed hash here means something mutated
        # the header mid-generation (a timestamp, a counter, a version bump
        # without bump_context_generation) — raise, never serve.
        assert_frozen_stable(
            {"frozen_hash": previous_frozen}, {"frozen_hash": current_frozen}
        )

    # Scope-ordered candidates: same session always; same-project pages
    # shared only when they carry an explicitly-granted origin (the query
    # already bounds that union; a mismatch here is defensive bookkeeping).
    candidates: list[dict[str, Any]] = []
    for row in rows:
        record = dict(row)
        record["metadata"] = json.loads(str(record.get("metadata_json") or "{}"))
        same_session = str(record["session_id"]) == str(session_id)
        if not same_session and str(record["origin"]) not in _PROJECT_SHARED_ORIGINS:
            compilation.exclusions.append(
                _exclusion(record, "cross_session_origin_not_explicit")
            )
            continue
        candidates.append(record)

    # Gate + read every candidate (A8 first, then relevance for unpinned).
    selections: list[tuple[dict[str, Any], ContextSelection]] = []
    for record in candidates:
        if str(record["status"]) == "withheld":
            compilation.exclusions.append(
                _exclusion(record, "withheld_by_privacy_action")
            )
            continue
        content = _read_page_bytes(record, compilation)
        if content is None:
            continue
        verdict = _a8_exclusion(record, content)
        if verdict is not None:
            compilation.exclusions.append(
                _exclusion(record, f"a8_availability_{str(verdict).lower()}")
            )
            continue
        pinned = (
            _active_pin_count(str(record["page_hash"])) > 0
            and str(record["session_id"]) == str(session_id)
        )
        score = relevance_score(
            query_text,
            title=str(record["title"] or ""),
            content=content,
            source_kind=str(record["source_kind"] or ""),
        )
        if not pinned and score < RELEVANCE_THRESHOLD:
            compilation.exclusions.append(
                _exclusion(record, f"irrelevant(score={score:.2f})")
            )
            continue
        selections.append(
            (
                record,
                ContextSelection(
                    admission_id=str(record["admission_id"]),
                    page_hash=str(record["page_hash"]),
                    title=str(record["title"] or ""),
                    origin=str(record["origin"] or "inferred"),
                    source_kind=str(record["source_kind"] or ""),
                    pinned=pinned,
                    bytes=int(record["bytes"] or 0),
                    content=content,
                    generation=int(record["generation"] or 0),
                ),
            )
        )

    # Residency-first ordering: pinned (oldest first), then unpinned newest-first.
    selections.sort(
        key=lambda pair: (
            0 if pair[1].pinned else 1,
            pair[0]["created_at"] if pair[1].pinned else _negate_created(pair[0]["created_at"]),
        )
    )

    budget = max(int(budget_chars), 0)
    used = 0
    exceeded_by_pinned = False
    for _record, selection in selections:
        cost = len(selection.render().encode("utf-8"))
        if used and used + cost > budget and not selection.pinned:
            compilation.truncations.append(
                {
                    "admission_id": selection.admission_id,
                    "page_hash": selection.page_hash,
                    "title": selection.title,
                    "bytes": selection.bytes,
                    "reason": "budget_overflow",
                }
            )
            continue
        if used + cost > budget and selection.pinned:
            exceeded_by_pinned = True
        compilation.items.append(selection)
        used += cost
    if exceeded_by_pinned:
        compilation.truncations.append(
            {
                "admission_id": "",
                "page_hash": "",
                "title": "",
                "bytes": used,
                "reason": "budget_exceeded_by_pinned_pages",
            }
        )

    if compilation.items:
        body = "\n".join(selection.render() for selection in compilation.items)
        compilation.block_text = f"{header}\n{body}"
    else:
        compilation.block_text = ""
        body = ""

    frontier_hash = _sha256_text(body)
    compilation.layout_stable_within_generation = (
        not previous_frontier or previous_frontier == frontier_hash
    )
    conn = _connection()
    try:
        conn.execute(
            """
            INSERT INTO turn_context_generations (
                principal, session_id, generation, frozen_hash, digest_text,
                last_frontier_hash, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(principal, session_id) DO UPDATE SET
                frozen_hash = excluded.frozen_hash,
                last_frontier_hash = excluded.last_frontier_hash,
                updated_at = excluded.updated_at
            """,
            (
                principal,
                session_id,
                generation,
                current_frozen,
                digest_text,
                frontier_hash,
                _utcnow(),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return compilation


def _negate_created(created_at: str) -> str:
    # ISO-8601 UTC timestamps sort lexicographically; reversing recency
    # without a numeric column keeps the SQL schema untouched.
    return "".join(chr(0x10FFFF - ord(ch)) for ch in str(created_at))


def _exclusion(record: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "admission_id": str(record["admission_id"]),
        "page_hash": str(record["page_hash"]),
        "title": str(record["title"] or ""),
        "reason": reason,
    }


def _read_page_bytes(record: dict[str, Any], compilation: TurnContextCompilation) -> str | None:
    """Read one page verbatim through the storage law, or record why not."""
    try:
        return page_in(str(record["page_hash"]))
    except PageNotFoundError as exc:
        compilation.exclusions.append(
            _exclusion(record, f"page_unavailable({exc})"[:120])
        )
        return None


def _active_pin_count(page_hash: str) -> int:
    from core.context_pages import _active_pin_count

    return _active_pin_count(page_hash)


# ── Lifecycle: pin / archive / recall ────────────────────────────────────────


def pin_admission(
    principal: str, admission_id: str, *, reason: str = ""
) -> dict[str, Any]:
    """Hold a page against archival with a manual (operator) pin."""
    record = _require_admission(principal, admission_id)
    if str(record["status"]) != "active":
        raise AdmissionClosedError(
            f"admission {admission_id[:12]} is {record['status']}; only active pages pin"
        )
    pin_id = new_pin_id()
    pin_page(str(record["page_hash"]), pin_id=pin_id, reason=reason or "operator pin")
    metadata = dict(record.get("metadata") or {})
    manual_pins = [str(p) for p in metadata.get("manual_pins") or []]
    manual_pins.append(pin_id)
    metadata["manual_pins"] = manual_pins
    _update_admission(admission_id, metadata_json=json.dumps(metadata, sort_keys=True))
    return {"admission_id": admission_id, "pin_id": pin_id}


def release_admission_pin(
    principal: str, admission_id: str, *, pin_id: str
) -> dict[str, Any]:
    record = _require_admission(principal, admission_id)
    release_pin(str(record["page_hash"]), pin_id=str(pin_id))
    metadata = dict(record.get("metadata") or {})
    manual_pins = [str(p) for p in metadata.get("manual_pins") or []]
    if str(pin_id) in manual_pins:
        manual_pins.remove(str(pin_id))
    metadata["manual_pins"] = manual_pins
    _update_admission(admission_id, metadata_json=json.dumps(metadata, sort_keys=True))
    return {"admission_id": admission_id, "pin_id": str(pin_id), "released": True}


def archive_scope_pages(principal: str, session_id: str) -> list[dict[str, Any]]:
    """Archive every unpinned page of one scoped session, with receipts.

    Residency changes; content never does. Each archived page appends a
    digest line, so the session's history records the archival without
    holding what was archived.
    """
    _require_session(principal, session_id)
    records = archive_closed_pages(session_id=session_id)
    for record in records:
        note_governance_event(
            principal,
            session_id,
            event="archived",
            admission_id="",
            detail=f"page={str(record.get('page_hash', ''))[:12]}",
        )
    return records


def recall_page(principal: str, admission_id: str) -> dict[str, Any]:
    """Recall one scoped page VERBATIM (archived or resident), A8-gated.

    This is the product recall surface: addressed by admission id inside the
    caller's principal scope — never a raw hash-addressed read.
    """
    record = _require_admission(principal, admission_id)
    status = str(record["status"])
    if status in ("erased", "withheld"):
        raise AdmissionClosedError(
            f"admission {admission_id[:12]} is {status}; recall refused"
        )
    try:
        content = page_in(str(record["page_hash"]))
    except PageNotFoundError as exc:
        raise AdmissionClosedError(
            f"admission {admission_id[:12]} bytes are not servable: {exc}"
        ) from exc
    verdict = _a8_exclusion(record, content)
    if verdict is not None:
        raise AdmissionClosedError(
            f"admission {admission_id[:12]} is {verdict} by privacy policy"
        )
    return {
        "admission_id": admission_id,
        "page_hash": str(record["page_hash"]),
        "content": content,
        "origin": str(record["origin"]),
        "source_kind": str(record["source_kind"]),
        "title": str(record["title"] or ""),
        "status": status,
        "bytes": int(record["bytes"] or 0),
        "created_at": str(record["created_at"]),
    }


# ── Lifecycle: withhold / erase / supersede (C13 controls) ──────────────────


def withhold_admission(
    principal: str,
    admission_id: str,
    *,
    reason: str = "",
    actor: str = "operator",
) -> dict[str, Any]:
    """WITHHOLD: monotone, reversible-by-nobody — the page stops serving."""
    record = _require_admission(principal, admission_id)
    status = str(record["status"])
    if status == "erased":
        raise AdmissionClosedError("admission already erased")
    if status == "withheld":
        return {"admission_id": admission_id, "status": "withheld", "idempotent": True}
    _set_status(admission_id, "withheld")
    _release_known_pins(record)
    note_governance_event(
        principal,
        str(record["session_id"]),
        event="withheld",
        admission_id=admission_id,
        detail=f"reason={reason or 'unspecified'} actor={actor}",
    )
    return {"admission_id": admission_id, "status": "withheld"}


def erase_admission(
    principal: str,
    admission_id: str,
    *,
    reason: str = "",
    actor: str = "operator",
) -> dict[str, Any]:
    """FORGET: erased everywhere this authority reaches, provably.

    Releases the page's pins, marks the admission erased, and — when the
    LAST admission referencing the content lets go — removes the stored
    bytes through the governed erasure path. Identical bytes admitted by
    another scope are that scope's lawful property and survive until its
    own erasure.
    """
    record = _require_admission(principal, admission_id)
    if str(record["status"]) == "erased":
        return {"admission_id": admission_id, "status": "erased", "idempotent": True}
    session_id = str(record["session_id"])
    _release_known_pins(record)
    _set_status(admission_id, "erased")
    page_hash = str(record["page_hash"])
    remaining = _live_admissions_for_page(page_hash)
    bytes_erased = False
    erasure_receipt = ""
    if not remaining:
        result = erase_page(
            page_hash,
            governance_actor=actor,
            governance_reason=reason or "operator forget",
        )
        erasure_receipt = str(result.get("erasure_receipt") or "")
        bytes_erased = True
    note_governance_event(
        principal,
        session_id,
        event="erased",
        admission_id=admission_id,
        detail=f"reason={reason or 'unspecified'} actor={actor} bytes_erased={str(bytes_erased).lower()}",
    )
    return {
        "admission_id": admission_id,
        "status": "erased",
        "bytes_erased": bytes_erased,
        "erasure_receipt": erasure_receipt,
    }


def supersede_admission(
    principal: str,
    admission_id: str,
    *,
    content: str,
    reason: str = "",
    actor: str = "operator",
    title: str = "",
) -> dict[str, Any]:
    """EDIT: append-only correction — a new explicit page replaces the old.

    NOTHING IS EVER REWRITTEN: the old admission is marked superseded and
    unpinned (it stops serving), the correction is a NEW admission with
    explicit origin, and the digest records the supersession.
    """
    record = _require_admission(principal, admission_id)
    if str(record["status"]) != "active":
        raise AdmissionClosedError(
            f"admission {admission_id[:12]} is {record['status']}; only active pages supersede"
        )
    _set_status(admission_id, "superseded")
    _release_known_pins(record)
    scope = TurnScope(
        principal=principal,
        session_id=str(record["session_id"]),
        project_id=str(record["project_id"] or ""),
        turn_id=str(record["turn_id"] or ""),
    )
    admitted = admit_turn_context(
        content,
        scope=scope,
        source_kind=str(record["source_kind"] or "chat_exchange"),
        title=title or str(record["title"] or ""),
        origin="explicit",
        request_id=str(record["request_id"] or ""),
        metadata={"supersedes": admission_id},
    )
    # The producing turn already closed when it committed; the storage-level
    # task pin the replacement just inherited must not exempt the correction
    # from relevance and budget. Its turn lineage stays on the admission row.
    if scope.turn_id:
        close_turn_scope(principal, scope.session_id, scope.turn_id)
    note_governance_event(
        principal,
        scope.session_id,
        event="superseded",
        admission_id=admission_id,
        detail=f"replacement={admitted['admission_id'][:12]} actor={actor} reason={reason or 'unspecified'}",
    )
    return {
        "admission_id": admission_id,
        "status": "superseded",
        "replacement_admission_id": admitted["admission_id"],
        "replacement_page_hash": admitted["page_hash"],
    }


# ── Inspect (C13: privacy-safe by construction) ──────────────────────────────


def inspect_admissions(
    principal: str,
    *,
    session_id: str = "",
    include_content: bool = False,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Scoped, provenance-carrying inspection records.

    Content bytes are opt-in and owner-scoped by the caller; the record
    shape itself never leaks another principal's rows — the SQL is
    principal-bounded, so isolation is structural, not a filter.
    """
    _init_tables()
    clauses = ["principal = ?"]
    args: list[Any] = [principal]
    if session_id:
        clauses.append("session_id = ?")
        args.append(session_id)
    conn = _connection()
    try:
        rows = conn.execute(
            f"SELECT * FROM turn_context_admissions WHERE {' AND '.join(clauses)} "
            f"ORDER BY created_at DESC LIMIT ?",
            [*args, max(int(limit), 0)],
        ).fetchall()
    finally:
        conn.close()
    records: list[dict[str, Any]] = []
    for row in rows:
        record = dict(row)
        metadata = json.loads(str(record.get("metadata_json") or "{}"))
        entry: dict[str, Any] = {
            "admission_id": str(record["admission_id"]),
            "page_hash": str(record["page_hash"]),
            "session_id": str(record["session_id"]),
            "project_id": str(record["project_id"] or ""),
            "turn_id": str(record["turn_id"] or ""),
            "generation": int(record["generation"] or 0),
            "origin": str(record["origin"]),
            "source_kind": str(record["source_kind"]),
            "title": str(record["title"] or ""),
            "status": str(record["status"]),
            "bytes": int(record["bytes"] or 0),
            "created_at": str(record["created_at"]),
            "pinned": _active_pin_count(str(record["page_hash"])) > 0,
        }
        if include_content:
            try:
                entry["content"] = page_in(str(record["page_hash"]))
            except PageNotFoundError:
                entry["content"] = None
                entry["content_state"] = "erased_or_volatile"
        entry["metadata"] = metadata
        records.append(entry)
    return records


def _require_admission(principal: str, admission_id: str) -> dict[str, Any]:
    record = _admission_row(str(admission_id), str(principal))
    if record is None:
        raise AdmissionNotFoundError(admission_id)
    return record


def _require_session(principal: str, session_id: str) -> None:
    if not str(principal).strip() or not str(session_id).strip():
        raise ValueError("a principal and session scope are required")


def _update_admission(admission_id: str, **columns: Any) -> None:
    if not columns:
        return
    _init_tables()
    assignments = ", ".join(f"{name} = ?" for name in columns)
    conn = _connection()
    try:
        conn.execute(
            f"UPDATE turn_context_admissions SET {assignments}, updated_at = ? "
            f"WHERE admission_id = ?",
            [*columns.values(), _utcnow(), admission_id],
        )
        conn.commit()
    finally:
        conn.close()


def _set_status(admission_id: str, status: str) -> None:
    if status not in ADMISSION_STATUSES:
        raise ValueError(f"unknown admission status: {status!r}")
    _update_admission(admission_id, status=status)


def _live_admissions_for_page(page_hash: str) -> list[str]:
    _init_tables()
    conn = _connection()
    try:
        rows = conn.execute(
            "SELECT admission_id FROM turn_context_admissions "
            "WHERE page_hash = ? AND status != 'erased'",
            (page_hash,),
        ).fetchall()
        return [str(row["admission_id"]) for row in rows]
    finally:
        conn.close()


__all__ = [
    "ADMISSION_CONTENT_CAP_CHARS",
    "ORIGINS",
    "RELEVANCE_THRESHOLD",
    "SCHEMA_VERSION",
    "AdmissionClosedError",
    "AdmissionNotFoundError",
    "AdmissionRefusedError",
    "AdmissionScopeError",
    "ContextSelection",
    "PageNotFoundError",
    "PinActiveError",
    "TurnContextCompilation",
    "TurnScope",
    "admit_turn_context",
    "archive_scope_pages",
    "bump_context_generation",
    "close_turn_scope",
    "compile_turn_context",
    "current_generation",
    "erase_admission",
    "frozen_header",
    "inspect_admissions",
    "note_governance_event",
    "pin_admission",
    "recall_page",
    "release_admission_pin",
    "release_pin",
    "relevance_score",
    "resolve_principal",
    "supersede_admission",
    "withhold_admission",
]
