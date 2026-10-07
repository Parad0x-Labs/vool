from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import math
import re
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from core.runtime_paths import active_vool_home

LOGGER = logging.getLogger(__name__)

_BLOCK_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")

# Query-side stopwords for the BM25 leg (indexing is untouched). These carry no
# discriminative information in an OR-term query: with them, an unknown-value
# question like "What is my passport number?" earned a lexical leg on "my"/"is"
# for every stored fact and injected unrelated content at the model boundary
# (measured). Standard IR practice; applies to query terms only.
_QUERY_STOPWORDS = frozenset({
    "a", "an", "and", "are", "but", "did", "do", "does", "for", "from",
    "had", "has", "have", "her", "his", "how", "is", "it", "its", "my",
    "not", "of", "on", "or", "our", "that", "the", "their", "them", "then",
    "there", "these", "they", "this", "to", "was", "we", "were", "what",
    "when", "where", "which", "who", "why", "will", "with", "you", "your",
})


@dataclass(frozen=True)
class MemoryNode:
    node_id: str
    content: str
    timestamp: float
    keywords: list[str]
    tags: list[str]
    context_description: str
    embedding: list[float]
    linked_node_ids: list[str]
    agent_id: str
    # Which vector space `embedding` lives in ('' = legacy row recorded before
    # backend stamping). Cosine is only meaningful within ONE space; search,
    # dedup and collapse gate on this (see _backends_comparable).
    embedding_backend: str = ""
    # Layer-1 source occurrence this index row derives from ('' = legacy row
    # recorded before the evidence contract, or an unlinked derivation).
    # Every row that can surface in an answer should resolve to its source.
    source_occurrence_id: str = ""

    def to_row(self) -> dict[str, Any]:
        row = asdict(self)
        row["keywords"] = json.dumps(self.keywords, ensure_ascii=False)
        row["tags"] = json.dumps(self.tags, ensure_ascii=False)
        row["embedding"] = json.dumps(self.embedding, separators=(",", ":"))
        row["linked_node_ids"] = json.dumps(self.linked_node_ids, ensure_ascii=False)
        return row


@dataclass(frozen=True)
class MemoryBlock:
    block_name: str
    content: str
    agent_id: str
    updated_at: float


@dataclass(frozen=True)
class SourceOccurrence:
    """Layer-1 source evidence: ONE occurrence of something that was said.

    Retention is NOT promotion: an occurrence records what a role said, as
    redacted at write time, inside one chat scope. It never becomes profile
    truth by being stored. Identical bodies stay distinct occurrences; the
    only identity collapse is the write-idempotency seam for an infra retry
    of the SAME finalized turn (same request lineage, role and body bytes).
    """

    occurrence_id: str
    chat_scope: str
    project_id: str | None
    role: str            # 'user' | 'assistant'
    speaker: str         # display name if known, else ''
    authority: str       # observed-user-statement | assistant-output | quoted | imported-historical
    recorded_at: float   # system capture time
    statement_at: float | None   # when the statement was made, if known
    event_at: float | None       # when the described event is/was effective
    body: str            # redacted original text, exact Unicode, unmodified after write
    source_kind: str     # 'live-turn' | 'historical-import'
    import_batch: str | None
    status: str          # 'active' | 'deleted' | 'revoked'
    request_id: str = ""
    # Body-integrity state measured against the digest recorded at the
    # authorized write ('occurrence_store'): 'verified' | 'mismatch' |
    # 'legacy-unverified' | 'cleared' | 'unverified' (manually constructed
    # object; no row evidence). Serving read APIs refuse 'mismatch'.
    body_integrity: str = "unverified"
    # Persisted capture order in this store, used only to disambiguate tied
    # capture/statement clocks. It is not an event date or a retrieval rank.
    source_sequence: int | None = None

    def to_row(self) -> dict[str, Any]:
        return asdict(self)


#: Authority values for layer-1 occurrences (CONTRACT mr29/1 §1.2).
OCCURRENCE_AUTHORITIES = frozenset({
    "observed-user-statement", "assistant-output", "quoted", "imported-historical",
})
OCCURRENCE_ROLES = frozenset({"user", "assistant"})


#: The one partition id. `memory_blocks` is keyed PRIMARY KEY (block_name, agent_id) and
#: `memory_nodes` is filtered by it, so a default that disagrees with the writer's id is not a
#: preference -- it is a different store. Measured on the live store 2026-08-18: every chat write
#: passed "vool_chat" explicitly while this class and three readers defaulted to a bare "vool",
#: leaving 132 nodes and 3 blocks on one side and 1 and 1 on the other. `block_read("user_profile")`
#: returned the operator's stored name under the writer's id and None under the readers'. Four
#: defaults for one id; this is the only one now, and callers may still pass an explicit id.
DEFAULT_AGENT_ID = "vool_chat"


class VoolMemory:
    """SQLite-backed durable memory for named prompt blocks and episodic nodes."""

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS memory_blocks (
        block_name TEXT NOT NULL,
        agent_id TEXT NOT NULL,
        content TEXT NOT NULL,
        updated_at REAL NOT NULL,
        PRIMARY KEY (block_name, agent_id)
    );

    CREATE TABLE IF NOT EXISTS memory_nodes (
        node_id TEXT PRIMARY KEY,
        agent_id TEXT NOT NULL,
        content TEXT NOT NULL,
        timestamp REAL NOT NULL,
        keywords TEXT NOT NULL,
        tags TEXT NOT NULL,
        context_description TEXT NOT NULL,
        embedding TEXT NOT NULL,
        linked_node_ids TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_memory_blocks_agent_updated
        ON memory_blocks(agent_id, updated_at DESC);
    CREATE INDEX IF NOT EXISTS idx_memory_nodes_agent
        ON memory_nodes(agent_id);
    CREATE INDEX IF NOT EXISTS idx_memory_nodes_agent_ts
        ON memory_nodes(agent_id, timestamp DESC);

    -- Full-text index for hybrid (BM25 + semantic) retrieval. Kept in sync
    -- manually in node_store / invalidate (standalone, not external-content, so
    -- a corrupt sync degrades to "no BM25 leg" rather than breaking writes).
    CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
        node_id UNINDEXED, agent_id UNINDEXED, content, keywords,
        tokenize='porter unicode61'
    );

    -- Layer-1 source evidence (CONTRACT mr29/1 §1.2): every occurrence of
    -- what was said, per role, inside one chat scope. Retention is not
    -- promotion: admission gates live in layer 2 (memory_nodes) and can only
    -- decide INDEXING, never whether the source evidence survives. The body
    -- is the redacted original; spans elsewhere address it with Python
    -- character indices over exactly these bytes. Deleting/revoke clears the
    -- body and its derivative index row (source_fts) together.
    -- body_sha256 is the INTEGRITY ANCHOR (F16): recorded at the authorized
    -- write over the canonical redacted bytes; every serving read verifies
    -- sha256(body) == body_sha256 before the body may become evidence and
    -- REFUSES the row on mismatch (see _occurrence_body_integrity). It is
    -- never recomputed over current bytes to "repair" a damaged row.
    CREATE TABLE IF NOT EXISTS source_occurrences (
        occurrence_id TEXT PRIMARY KEY,
        agent_id TEXT NOT NULL,
        chat_scope TEXT NOT NULL,
        project_id TEXT,
        role TEXT NOT NULL,
        speaker TEXT NOT NULL DEFAULT '',
        authority TEXT NOT NULL,
        recorded_at REAL NOT NULL,
        statement_at REAL,
        event_at REAL,
        body TEXT NOT NULL,
        source_kind TEXT NOT NULL,
        import_batch TEXT,
        status TEXT NOT NULL DEFAULT 'active',
        request_id TEXT NOT NULL DEFAULT '',
        body_sha256 TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_source_occurrences_scope_time
        ON source_occurrences(agent_id, chat_scope, recorded_at DESC);
    CREATE INDEX IF NOT EXISTS idx_source_occurrences_batch
        ON source_occurrences(agent_id, import_batch);
    CREATE INDEX IF NOT EXISTS idx_source_occurrences_request
        ON source_occurrences(agent_id, request_id);

    -- Derivative FTS over occurrence bodies (rebuildable from layer 1).
    CREATE VIRTUAL TABLE IF NOT EXISTS source_fts USING fts5(
        occurrence_id UNINDEXED, body, tokenize='porter unicode61'
    );

    -- Derivative semantic index over occurrence bodies (rebuildable from
    -- layer 1; CONTRACT mr29/1 §1 pattern: indexing is a derivative, the
    -- body stays the authority). One row per (occurrence, backend): vector
    -- spaces are backend-specific and must never be compared across
    -- backends. body_sha256 guards staleness — a rewritten body invalidates
    -- its derivative until re-embedded. Deleting/revoke clears this
    -- derivative together with the body and its FTS row, so forgotten
    -- content cannot resurface through the semantic leg.
    CREATE TABLE IF NOT EXISTS source_embeddings (
        occurrence_id TEXT NOT NULL,
        agent_id TEXT NOT NULL,
        backend TEXT NOT NULL,
        dim INTEGER NOT NULL,
        vector TEXT NOT NULL,
        body_sha256 TEXT NOT NULL,
        embedded_at REAL NOT NULL,
        PRIMARY KEY (occurrence_id, backend)
    );
    CREATE INDEX IF NOT EXISTS idx_source_embeddings_agent_backend
        ON source_embeddings(agent_id, backend);
    """

    # Retrieval ranking. Hybrid search (node_search_hybrid) fuses two INDEPENDENT
    # candidate rankings with reciprocal-rank fusion instead of mixing scores:
    # BM25 keyword scores and cosine similarities live on incomparable scales (a
    # min-max-spanned BM25 value of 0.2 weighted against a raw cosine buried exact
    # keyword evidence — measured recall@10 50% vs 93% for the same corpus ranked
    # by BM25 alone). RRF consumes only ORDER, which is scale-free. Lexical
    # evidence is the stronger leg on this corpus, so it carries the larger
    # weight; the semantic leg contributes complementary candidates (paraphrase)
    # and stays bounded so a weak/noisy embedding space cannot bury exact-term
    # evidence. Effective importance (recency + frequency) breaks ties so the
    # LATEST value of a fact outranks the stale one it replaced.
    _RRF_K = 60
    _W_LEXICAL = 1.0
    _W_SEMANTIC = 0.35
    # A node earns a SEMANTIC rank only if its raw similarity clears this floor;
    # rank alone is not relevance — in a small store every comparable node holds
    # some rank, and without a floor weak matches ride the semantic leg into the
    # context (measured: unrelated facts injected for a question about something
    # never stored). 0.55 is set from measured separation in the installed
    # model's compressed similarity space: a true paraphrase match scores ~0.68
    # while unrelated question/fact pairs top out below 0.50.
    _SEMANTIC_FLOOR = 0.55

    @staticmethod
    def semantic_floor(backend: str) -> float:
        # Floors belong to vector spaces. On the corrected Nomic retrieval
        # representation, the retained positive/negative calibration brackets
        # 0.50; legacy folded and hash spaces retain their existing floor.
        return 0.50 if backend.endswith("#retrieval-mrl384-v1") else 0.55

    # Effective importance (recency/frequency) as a small additive term on the
    # fused scale: bounded so it reorders near-ties (stale vs latest value) and
    # brevity artifacts without letting access patterns override real
    # lexical/semantic evidence. 0.10 covers a one-rank difference on BOTH legs
    # (~0.017 normalized) for a realistic staleness gap.
    _W_EFFECTIVE_FUSED = 0.10
    # node_search (semantic-only) keeps its legacy cosine-scale contract.
    _W_EFFECTIVE = 0.30          # weight of effective importance in the legacy rank
    _HALF_LIFE_DAYS = 14.0
    _W_RECENCY = 0.7
    _W_FREQ = 0.3
    _FREQ_CAP = 50.0
    _DEDUP_SIM = 0.97          # cosine above which a near-identical write is a NOOP
    _DEDUP_OVERLAP = 0.85      # Jaccard token-overlap required alongside high cosine
                              # (strict: only true restatements NOOP, never updates)
    # 1-hop entity-graph expansion: after ranking, pull in nodes linked from the top
    # hits so directly-connected context surfaces even if it fell below min_score on
    # direct similarity. Bounded (one hop, capped fan-out + total) and dedup'd.
    _LINK_EXPANSION = True
    _LINK_FANOUT = 3           # max links followed per top hit
    _LINK_WEIGHT = 0.9         # a linked node rides in at this fraction of its linker's score
    _LINK_EXPANSION_MAX = 5    # cap on total linked nodes pulled into one search
    _COLLAPSE_SIM = 0.85       # retrieval-time: a near-RESTATEMENT (high cosine AND
                               # high token overlap, within one embedding space)
                               # collapses to the NEWEST copy so repeats do not
                               # occupy result slots. Distinct facts about the same
                               # entity always survive: cosine alone never merges,
                               # and supersession heuristics were removed — they
                               # fired on ubiquitous tokens ("now", "changed") and
                               # collapsed whole distinct sessions into one, which
                               # measured as the largest single recall loss. The
                               # latest value still ranks first via the recency term.

    def __init__(
        self,
        runtime_home: str | Path | None = None,
        agent_id: str = DEFAULT_AGENT_ID,
        db_path: str | Path | None = None,
    ) -> None:
        self._agent_id = str(agent_id or "").strip() or DEFAULT_AGENT_ID
        self._db_path = _resolve_db_path(runtime_home=runtime_home, db_path=db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        # Occurrence-body integrity telemetry (F16): every serving read that
        # refuses a damaged row counts here. `last_integrity_refusals` holds
        # the refusals of the most recent gated read call; the total is
        # cumulative for the instance's lifetime.
        self.integrity_refusals_total = 0
        self.last_integrity_refusals: list[dict[str, str]] = []
        self._conn = sqlite3.connect(str(self._db_path), timeout=30.0, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.create_function("vool_scope_matches", 3, _sqlite_scope_matches)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA busy_timeout=5000;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._conn.executescript(self.SCHEMA)
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        """Additively add ranking/temporal columns and backfill the FTS index.
        Idempotent: safe to run on every open, on fresh and existing DBs."""
        have = {row["name"] for row in self._conn.execute("PRAGMA table_info(memory_nodes)")}
        adds = {
            "base_importance": "REAL NOT NULL DEFAULT 0.5",
            "access_count": "INTEGER NOT NULL DEFAULT 0",
            "last_access": "REAL",
            "valid_from": "REAL",
            "valid_to": "REAL",  # NULL = live; non-NULL = invalidated/superseded
            # A8 pass-001 payload lineage: the governing request id of the turn
            # that produced this node ('' on legacy rows — never fabricated).
            "lineage_request_id": "TEXT NOT NULL DEFAULT ''",
            # Which vector space `embedding` lives in ('' = legacy row recorded
            # before backend stamping). Additive and idempotent; legacy rows keep
            # '' and stay comparable only with each other.
            "embedding_backend": "TEXT NOT NULL DEFAULT ''",
            # Layer-2 → layer-1 link: the source occurrence this index row
            # derives from ('' = legacy/unlinked; CONTRACT mr29/1 §1).
            "source_occurrence_id": "TEXT NOT NULL DEFAULT ''",
        }
        changed = False
        for col, decl in adds.items():
            if col not in have:
                self._conn.execute(f"ALTER TABLE memory_nodes ADD COLUMN {col} {decl}")
                changed = True
        if changed:
            # backfill temporal/access defaults for pre-existing rows
            self._conn.execute(
                "UPDATE memory_nodes SET valid_from = COALESCE(valid_from, timestamp), "
                "last_access = COALESCE(last_access, timestamp)"
            )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_memory_nodes_live "
            "ON memory_nodes(agent_id, valid_to)"
        )
        # backfill FTS for any node missing from it (e.g. DB created before
        # upgrade). Carry each node's OWN agent_id so a multi-agent DB opened by
        # one agent does not mis-attribute other agents' rows to the opener
        # (which would leak them into the opener's BM25 leg).
        try:
            missing = self._conn.execute(
                "SELECT node_id, agent_id, content, keywords FROM memory_nodes n "
                "WHERE NOT EXISTS (SELECT 1 FROM memory_fts f WHERE f.node_id = n.node_id)"
            ).fetchall()
            for row in missing:
                self._conn.execute(
                    "INSERT INTO memory_fts (node_id, agent_id, content, keywords) VALUES (?, ?, ?, ?)",
                    (row["node_id"], str(row["agent_id"]), str(row["content"]), str(row["keywords"])),
                )
        except sqlite3.Error:
            pass  # FTS is a best-effort leg; never block on it
        # Backfill the layer-1 derivative FTS for active occurrences missing
        # from it (e.g. a DB written before this index existed). Same
        # best-effort law as the memory_fts backfill above.
        try:
            missing_occ = self._conn.execute(
                "SELECT o.occurrence_id, o.body FROM source_occurrences o "
                "WHERE o.status = 'active' AND o.body != '' "
                "AND NOT EXISTS (SELECT 1 FROM source_fts f "
                "                WHERE f.occurrence_id = o.occurrence_id)"
            ).fetchall()
            for row in missing_occ:
                self._conn.execute(
                    "INSERT INTO source_fts (occurrence_id, body) VALUES (?, ?)",
                    (str(row["occurrence_id"]), str(row["body"])),
                )
        except sqlite3.Error:
            pass
        # Forget-law revocation ledger: one row per (token, chat) the user
        # asked to forget. Carriers that re-serve stored dialogue (transcript
        # assembly, dialogue context items) consult it so a value can never
        # re-enter a prompt from a surface the sweep could not reach — the
        # same belt-and-braces law the A8 availability gates apply to
        # WITHHELD/ERASED assistant payloads.
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_revocations (
                token TEXT NOT NULL,
                chat_id TEXT NOT NULL,
                revoked_at REAL NOT NULL,
                PRIMARY KEY (token, chat_id)
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_memory_revocations_chat "
            "ON memory_revocations(chat_id)"
        )
        self._conn.commit()

    @property
    def db_path(self) -> Path:
        return self._db_path

    @property
    def agent_id(self) -> str:
        return self._agent_id

    def block_read(self, block_name: str) -> str | None:
        name = _normalize_block_name(block_name)
        with self._lock:
            row = self._conn.execute(
                """
                SELECT content
                FROM memory_blocks
                WHERE block_name = ? AND agent_id = ?
                LIMIT 1
                """,
                (name, self._agent_id),
            ).fetchone()
        return str(row["content"]) if row else None

    def block_write(self, block_name: str, content: str) -> None:
        name = _normalize_block_name(block_name)
        text = str(content or "").strip()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO memory_blocks (block_name, agent_id, content, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(block_name, agent_id)
                DO UPDATE SET content = excluded.content, updated_at = excluded.updated_at
                """,
                (name, self._agent_id, text, time.time()),
            )
            self._conn.commit()

    def block_append(self, block_name: str, content: str, *, dedupe: bool = True) -> None:
        addition = str(content or "").strip()
        if not addition:
            return
        existing = self.block_read(block_name) or ""
        if dedupe and _line_exists(existing, addition):
            return
        separator = "\n" if existing and not existing.endswith("\n") else ""
        self.block_write(block_name, f"{existing}{separator}{addition}")

    def block_replace(self, block_name: str, old: str, new: str) -> bool:
        old_text = str(old or "")
        if not old_text:
            return False
        existing = self.block_read(block_name)
        if existing is None or old_text not in existing:
            return False
        self.block_write(block_name, existing.replace(old_text, str(new or "").strip(), 1))
        return True

    def block_delete(self, block_name: str) -> None:
        name = _normalize_block_name(block_name)
        with self._lock:
            self._conn.execute(
                "DELETE FROM memory_blocks WHERE block_name = ? AND agent_id = ?",
                (name, self._agent_id),
            )
            self._conn.commit()

    def blocks_for_prompt(self, block_names: list[str]) -> str:
        parts: list[str] = []
        seen: set[str] = set()
        for raw_name in block_names:
            name = _normalize_block_name(raw_name)
            if name in seen:
                continue
            seen.add(name)
            content = self.block_read(name)
            if content:
                parts.append(f"[{name}]\n{content.strip()}")
        return "\n\n".join(parts)

    def all_block_names(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT block_name
                FROM memory_blocks
                WHERE agent_id = ?
                ORDER BY updated_at DESC, block_name ASC
                """,
                (self._agent_id,),
            ).fetchall()
        return [str(row["block_name"]) for row in rows]

    def node_store(
        self,
        content: str,
        keywords: list[str],
        tags: list[str],
        context_description: str,
        embedding: list[float],
        linked_node_ids: list[str] | None = None,
        *,
        timestamp: float | None = None,
        importance: float | None = None,
        lineage_request_id: str = "",
        embedding_backend: str = "",
        source_occurrence_id: str = "",
    ) -> MemoryNode:
        text = str(content or "").strip()
        if not text:
            raise ValueError("memory node content is required")
        ts = float(timestamp if timestamp is not None else time.time())
        vec = [float(value) for value in list(embedding or [])]
        backend = str(embedding_backend or "").strip()
        score = 0.5 if importance is None else max(0.0, min(1.0, float(importance)))

        # Dedup-on-write: a near-identical LIVE restatement is a NOOP — bump its
        # access (recency/frequency reinforcement) instead of storing a duplicate.
        with self._lock:
            dup = self._find_near_duplicate(
                vec,
                text,
                tags=tags,
                context_description=context_description,
                embedding_backend=backend,
            )
            if dup is not None:
                self._conn.execute(
                    "UPDATE memory_nodes SET access_count = access_count + 1, "
                    "last_access = ?, base_importance = MAX(base_importance, ?) "
                    "WHERE node_id = ? AND agent_id = ?",
                    (ts, score, dup.node_id, self._agent_id),
                )
                self._conn.commit()
                return dup

        session_key = "|".join(sorted(_session_scopes(tags=tags, context_description=context_description)))
        node_id = hashlib.sha256(f"{self._agent_id}:{ts:.9f}:{session_key}:{text}".encode()).hexdigest()[:20]
        node = MemoryNode(
            node_id=node_id,
            content=text,
            timestamp=ts,
            keywords=[str(item).strip() for item in list(keywords or []) if str(item).strip()],
            tags=[str(item).strip() for item in list(tags or []) if str(item).strip()],
            context_description=str(context_description or "").strip(),
            embedding=vec,
            linked_node_ids=[str(item).strip() for item in list(linked_node_ids or []) if str(item).strip()],
            agent_id=self._agent_id,
            embedding_backend=backend,
            source_occurrence_id=str(source_occurrence_id or "").strip(),
        )
        row = node.to_row()
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO memory_nodes (
                    node_id, agent_id, content, timestamp, keywords, tags,
                    context_description, embedding, linked_node_ids,
                    base_importance, access_count, last_access, valid_from, valid_to,
                    lineage_request_id, embedding_backend, source_occurrence_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, NULL, ?, ?, ?)
                """,
                (
                    row["node_id"], row["agent_id"], row["content"], row["timestamp"],
                    row["keywords"], row["tags"], row["context_description"],
                    row["embedding"], row["linked_node_ids"],
                    score, ts, ts, str(lineage_request_id or ""), backend,
                    node.source_occurrence_id,
                ),
            )
            with contextlib.suppress(sqlite3.Error):  # FTS is a best-effort leg
                self._conn.execute(
                    "INSERT INTO memory_fts (node_id, agent_id, content, keywords) VALUES (?, ?, ?, ?)",
                    (node_id, self._agent_id, text, " ".join(node.keywords)),
                )
            self._conn.commit()
        return node

    def _find_near_duplicate(
        self,
        embedding: list[float],
        text: str,
        *,
        tags: list[str] | None = None,
        context_description: str = "",
        embedding_backend: str = "",
    ) -> MemoryNode | None:
        """Find a LIVE node that is a near-identical restatement of *text* (high
        cosine AND high token overlap, within one vector space). Used for
        dedup-on-write NOOP."""
        if not embedding:
            return None
        session_scope = _exact_stored_session_scope(
            tags=tags,
            context_description=context_description,
        )
        if session_scope is None:
            return None
        rows = self._conn.execute(
            """
            SELECT *
            FROM memory_nodes
            WHERE agent_id = ?
              AND valid_to IS NULL
              AND vool_scope_matches(tags, context_description, ?) = 1
            """,
            (self._agent_id, session_scope),
        ).fetchall()
        best: MemoryNode | None = None
        best_sim = 0.0
        new_tokens = _token_set(text)
        for row in rows:
            node = _row_to_node(row)
            if not _backends_comparable(embedding_backend, node.embedding_backend):
                continue
            sim = _cosine_similarity(embedding, node.embedding)
            if sim < self._DEDUP_SIM:
                continue
            if _token_overlap(new_tokens, _token_set(node.content)) < self._DEDUP_OVERLAP:
                continue
            if sim > best_sim:
                best_sim, best = sim, node
        return best

    def node_get(self, node_id: str) -> MemoryNode | None:
        normalized = str(node_id or "").strip()
        if not normalized:
            return None
        with self._lock:
            row = self._conn.execute(
                """
                SELECT *
                FROM memory_nodes
                WHERE node_id = ? AND agent_id = ?
                LIMIT 1
                """,
                (normalized, self._agent_id),
            ).fetchone()
        return _row_to_node(row) if row else None

    def node_update_links(self, node_id: str, linked_ids: list[str]) -> None:
        normalized = str(node_id or "").strip()
        if not normalized:
            return
        links = [str(item).strip() for item in list(linked_ids or []) if str(item).strip()]
        with self._lock:
            self._conn.execute(
                """
                UPDATE memory_nodes
                SET linked_node_ids = ?
                WHERE node_id = ? AND agent_id = ?
                """,
                (json.dumps(links, ensure_ascii=False), normalized, self._agent_id),
            )
            self._conn.commit()

    def node_search(
        self,
        query_embedding: list[float],
        *,
        top_k: int = 5,
        min_score: float = 0.6,
        session_id: str | None = None,
    ) -> list[tuple[MemoryNode, float]]:
        """Semantic + effective-importance hybrid over LIVE nodes. The recency
        term in effective importance makes the latest value of a fact outrank
        the stale one. Returned nodes get an access bump (reinforcement).
        Backward-compatible signature; min_score still filters on cosine."""
        return self._ranked_search(
            query_embedding,
            None,
            top_k=top_k,
            min_score=min_score,
            session_id=session_id,
        )

    def node_search_hybrid(
        self,
        query_text: str,
        query_embedding: list[float],
        *,
        top_k: int = 5,
        min_score: float = 0.0,
        session_id: str | None = None,
        query_embedding_backend: str = "",
        diversify: bool = False,
    ) -> list[tuple[MemoryNode, float]]:
        """Keyword (BM25) + semantic retrieval fused by reciprocal rank.

        The fusion consumes only the ORDER of each leg, never raw score
        magnitudes, so incomparable score scales cannot bury one leg. Returned
        scores are normalized fused strength in [0, 1] (1.0 = top of both legs),
        NOT cosine — min_score filters on this fused scale.

        ``query_embedding_backend`` names the vector space of *query_embedding*
        (see core.embedding_service.embed_stamped). Semantic comparison happens
        ONLY against nodes in the same space; every other node still competes
        through the keyword leg, so a hash-fallback query or a service outage
        degrades to strong lexical retrieval instead of noise or emptiness.
        Legacy callers that omit the stamp ('') keep their previous
        compare-with-legacy-rows behavior."""
        return self._ranked_search(
            query_embedding,
            query_text,
            top_k=top_k,
            min_score=min_score,
            session_id=session_id,
            query_embedding_backend=query_embedding_backend,
            diversify=diversify,
        )

    def session_nodes(
        self,
        *,
        session_id: str | None,
        limit: int = 1024,
    ) -> list[MemoryNode]:
        """Valid nodes in one chat's scope, newest-stored first, capped.

        Read-only companion to _ranked_search's candidate load (same scope and
        validity SQL authority) for bounded evaluator-side passes that rank on
        content the vector/keyword legs cannot express — e.g. stated-time
        neighborhoods for explicit historical questions. No scoring happens
        here; callers filter, rank, and are expected to cap what they use.
        """
        session_scope = _requested_session_scope(session_id)
        if session_id is not None and session_scope is None:
            return []
        with self._lock:
            if session_id is not None:
                rows = self._conn.execute(
                    """
                    SELECT *
                    FROM memory_nodes
                    WHERE agent_id = ?
                      AND valid_to IS NULL
                      AND vool_scope_matches(tags, context_description, ?) = 1
                    ORDER BY timestamp DESC
                    LIMIT ?
                    """,
                    (self._agent_id, session_scope, max(1, int(limit))),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """
                    SELECT *
                    FROM memory_nodes
                    WHERE agent_id = ? AND valid_to IS NULL
                    ORDER BY timestamp DESC
                    LIMIT ?
                    """,
                    (self._agent_id, max(1, int(limit))),
                ).fetchall()
        return [_row_to_node(row) for row in rows]

    def _ranked_search(
        self,
        query_embedding: list[float],
        query_text: str | None,
        *,
        top_k: int,
        min_score: float,
        session_id: str | None = None,
        query_embedding_backend: str = "",
        diversify: bool = False,
    ) -> list[tuple[MemoryNode, float]]:
        query = [float(value) for value in list(query_embedding or [])]
        if not query:
            return []
        session_scope = _requested_session_scope(session_id)
        if session_id is not None and session_scope is None:
            return []
        now = time.time()
        bm25 = (
            self._bm25_scores(query_text, session_id=session_id)
            if query_text
            else {}
        )
        with self._lock:
            if session_id is not None:
                rows = self._conn.execute(
                    """
                    SELECT *
                    FROM memory_nodes
                    WHERE agent_id = ?
                      AND valid_to IS NULL
                      AND vool_scope_matches(tags, context_description, ?) = 1
                    """,
                    (self._agent_id, session_scope),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """
                    SELECT *
                    FROM memory_nodes
                    WHERE agent_id = ? AND valid_to IS NULL
                    """,
                    (self._agent_id,),
                ).fetchall()
        if query_text:
            ranked = self._fused_rank(
                query,
                [row for row in rows],
                bm25,
                min_score=min_score,
                session_id=session_id,
                query_embedding_backend=query_embedding_backend,
            )
        else:
            ranked = self._legacy_semantic_rank(
                query,
                [row for row in rows],
                min_score=min_score,
                session_id=session_id,
                now=now,
            )
        limit = max(1, int(top_k))
        if diversify and query_text and query_embedding_backend.startswith("ollama:"):
            # RRF's lexical weight can otherwise bury EVERY semantic-only
            # hit regardless of its neural rank. Reserve at most two places
            # within the existing top-k; all candidates already passed scope,
            # validity, vector-space and fused-score gates.
            neural = sorted(
                (item for item in ranked
                 if item[0].embedding_backend == query_embedding_backend
                 and _cosine_similarity(query, item[0].embedding) >= self.semantic_floor(query_embedding_backend)),
                key=lambda item: _cosine_similarity(query, item[0].embedding),
                reverse=True,
            )[:min(2, limit // 2)]
            diversified = []
            def add(item):
                if item[2] not in {r[2] for r in diversified}:
                    diversified.append(item)
            for i, item in enumerate(ranked):
                add(item)
                if i < len(neural):
                    add(neural[i])
                if len(diversified) >= limit:
                    break
            top = diversified[:limit]
        else:
            top = ranked[:limit]
        merged = self._expand_one_hop(top, session_id=session_id) if (top and self._LINK_EXPANSION) else top
        if merged:
            self._bump_access(
                [nid for (_n, _s, nid) in merged],
                now,
                session_id=session_id,
            )
        return [(n, s) for (n, s, _nid) in merged]

    def _legacy_semantic_rank(
        self,
        query: list[float],
        rows: list[Any],
        *,
        min_score: float,
        session_id: str | None,
        now: float,
    ) -> list[tuple[MemoryNode, float, str]]:
        """Semantic-only ranking on the cosine scale (node_search contract:
        min_score filters on cosine; score = semantic + effective importance)."""
        scored: list[tuple[MemoryNode, float, str]] = []
        for row in rows:
            node = _row_to_node(row)
            if session_id is not None and not _node_matches_session(node, session_id):
                continue
            sem = _cosine_similarity(query, node.embedding)
            if sem < float(min_score):
                continue
            eff = _effective_importance(
                base=_row_float(row, "base_importance", 0.5),
                access_count=_row_int(row, "access_count", 0),
                # Recency is KNOWLEDGE time (the record's own timestamp),
                # never last_access: accessing a record must not launder an
                # old statement into looking freshly stated (measured: 12
                # targeted recalls of an older Monday claim flipped it above
                # the newer Friday correction). Access frequency stays a
                # bounded usefulness signal through the freq term only.
                age_days=max(0.0, (now - float(node.timestamp or 0.0)) / 86400.0),
                half_life=self._HALF_LIFE_DAYS,
                w_recency=self._W_RECENCY,
                w_freq=self._W_FREQ,
                freq_cap=self._FREQ_CAP,
            )
            scored.append((node, self._W_SEMANTIC * sem + self._W_EFFECTIVE * eff, node.node_id))
        collapsed = self._temporal_collapse(scored)
        collapsed.sort(key=lambda item: (item[1], item[0].timestamp, item[2]), reverse=True)
        return collapsed

    def _fused_rank(
        self,
        query: list[float],
        rows: list[Any],
        bm25: dict[str, float],
        *,
        min_score: float,
        session_id: str | None,
        query_embedding_backend: str = "",
    ) -> list[tuple[MemoryNode, float, str]]:
        """RRF fusion of the keyword and semantic legs. Semantic similarity is
        computed ONLY within one vector space; nodes outside the query's space
        are never cosine-compared — they compete on the keyword leg alone. A
        min_score above 0 gates on the normalized fused score."""
        q_backend = str(query_embedding_backend or "").strip()
        nodes: list[MemoryNode] = []
        node_row = {}
        for row in rows:
            node = _row_to_node(row)
            if session_id is not None and not _node_matches_session(node, session_id):
                continue
            nodes.append(node)
            node_row[node.node_id] = row

        semantic: dict[str, float] = {}
        if q_backend:
            for node in nodes:
                if node.embedding_backend != q_backend:
                    continue
                sem = _cosine_similarity(query, node.embedding)
                if sem >= self.semantic_floor(query_embedding_backend):
                    semantic[node.node_id] = sem
        else:
            # unstamped query: legacy behavior — compare with legacy rows only
            for node in nodes:
                if node.embedding_backend:
                    continue
                sem = _cosine_similarity(query, node.embedding)
                if sem >= self.semantic_floor(query_embedding_backend):
                    semantic[node.node_id] = sem

        lexical = {nid: v for nid, v in (bm25 or {}).items() if v > 0.0}
        sem_rank = _rank_map(semantic)
        lex_rank = _rank_map(lexical)
        max_fused = (self._W_LEXICAL + self._W_SEMANTIC) / (self._RRF_K + 1)

        scored: list[tuple[MemoryNode, float, str]] = []
        now = time.time()
        for node in nodes:
            raw = 0.0
            rank = sem_rank.get(node.node_id)
            if rank is not None:
                raw += self._W_SEMANTIC / (self._RRF_K + rank)
            rank = lex_rank.get(node.node_id)
            if rank is not None:
                raw += self._W_LEXICAL / (self._RRF_K + rank)
            if raw <= 0.0:
                continue
            eff = _effective_importance(
                base=_row_float(node_row[node.node_id], "base_importance", 0.5),
                access_count=_row_int(node_row[node.node_id], "access_count", 0),
                # Recency is KNOWLEDGE time (the record's own timestamp),
                # never last_access: accessing a record must not launder an
                # old statement into looking freshly stated (measured: 12
                # targeted recalls of an older Monday claim flipped it above
                # the newer Friday correction). Access frequency stays a
                # bounded usefulness signal through the freq term only.
                age_days=max(0.0, (now - float(node.timestamp or 0.0)) / 86400.0),
                half_life=self._HALF_LIFE_DAYS,
                w_recency=self._W_RECENCY,
                w_freq=self._W_FREQ,
                freq_cap=self._FREQ_CAP,
            )
            fused = raw / max_fused + self._W_EFFECTIVE_FUSED * eff
            if min_score > 0 and fused < float(min_score):
                continue
            scored.append((node, fused, node.node_id))

        collapsed = self._temporal_collapse(scored)
        collapsed.sort(
            key=lambda item: (item[1], item[0].timestamp, item[2]),
            reverse=True,
        )
        return collapsed

    def _expand_one_hop(
        self,
        top: list[tuple[MemoryNode, float, str]],
        *,
        session_id: str | None = None,
    ) -> list[tuple[MemoryNode, float, str]]:
        """Pull nodes linked from the top hits into the result, downweighted.

        One hop only (links of `top` are followed, not links of the pulled nodes),
        with a capped fan-out + total and a seen-set, so cycles and result-set
        explosion are impossible. A linked node rides in at ``_LINK_WEIGHT`` of its
        linker's score, so it surfaces just below the hit that connected it even if
        its own direct similarity fell below ``min_score``.
        """
        in_result = {nid for (_n, _s, nid) in top}
        extras: list[tuple[MemoryNode, float, str]] = []
        for node, score, _nid in top:
            for linked_id in list(node.linked_node_ids or [])[: self._LINK_FANOUT]:
                if linked_id in in_result:
                    continue
                linked = self._node_get_for_search(
                    linked_id,
                    session_id=session_id,
                )
                if linked is None:
                    continue
                in_result.add(linked_id)
                extras.append((linked, score * self._LINK_WEIGHT, linked_id))
                if len(extras) >= self._LINK_EXPANSION_MAX:
                    break
            if len(extras) >= self._LINK_EXPANSION_MAX:
                break
        if not extras:
            return top
        return sorted(top + extras, key=lambda item: item[1], reverse=True)

    def _temporal_collapse(
        self, scored: list[tuple[MemoryNode, float, str]]
    ) -> list[tuple[MemoryNode, float, str]]:
        """Collapse a near-RESTATEMENT to the NEWEST copy: walk newest-first; a node
        merges into an already-chosen newer representative only when BOTH cosine >=
        COLLAPSE_SIM AND Jaccard token-overlap >= DEDUP_OVERLAP within the SAME
        vector space. That is a true restatement (the same words re-said), so one
        copy is enough.

        Embedding cosine alone is NOT enough: two distinct, never-superseded facts
        about the same entity sit close in embedding space (cosine ~0.9) yet must
        both survive. The previous extra branch — a supersession MARKER word plus
        any shared token — is gone: marker words like "now" or "changed" are
        ubiquitous in real conversations and any shared token satisfied the branch,
        so distinct same-topic sessions collapsed into one (measured as the largest
        single recall loss). The latest value of a fact still outranks the stale
        one it replaced via the recency term in the ranking, and both remain
        visible for provenance."""
        order = sorted(scored, key=lambda it: it[0].timestamp, reverse=True)
        reps: list[list] = []  # [node, score, node_id, token_set]
        for node, score, nid in order:
            node_tokens = _token_set(node.content)
            merged = False
            for rep in reps:
                if not _backends_comparable(node.embedding_backend, rep[0].embedding_backend):
                    continue
                if _cosine_similarity(node.embedding, rep[0].embedding) < self._COLLAPSE_SIM:
                    continue
                if _token_overlap(node_tokens, rep[3]) < self._DEDUP_OVERLAP:
                    continue
                rep[1] = max(rep[1], score)  # inherit the older phrasing's relevance
                merged = True
                break
            if not merged:
                reps.append([node, score, nid, node_tokens])
        return [(r[0], r[1], r[2]) for r in reps]

    def _bm25_scores(
        self,
        query_text: str,
        *,
        session_id: str | None = None,
    ) -> dict[str, float]:
        """Normalized BM25 (0..1, higher=better) per node for the query terms."""
        import re as _re
        terms = [
            t
            for t in _re.findall(r"[a-zA-Z0-9_]{2,}", str(query_text or "").lower())
            if t not in _QUERY_STOPWORDS
        ]
        if not terms:
            return {}
        session_scope = _requested_session_scope(session_id)
        if session_id is not None and session_scope is None:
            return {}
        match = " OR ".join(dict.fromkeys(terms))
        try:
            with self._lock:
                if session_id is not None:
                    rows = self._conn.execute(
                        """
                        SELECT node_id, content, keywords
                        FROM memory_nodes
                        WHERE agent_id = ?
                          AND valid_to IS NULL
                          AND vool_scope_matches(
                              tags,
                              context_description,
                              ?
                          ) = 1
                        """,
                        (self._agent_id, session_scope),
                    ).fetchall()
                    return _scoped_bm25_scores(rows, terms)
                else:
                    rows = self._conn.execute(
                        """
                        SELECT memory_fts.node_id, bm25(memory_fts) AS rank
                        FROM memory_fts
                        JOIN memory_nodes AS node
                          ON node.node_id = memory_fts.node_id
                         AND node.agent_id = memory_fts.agent_id
                        WHERE memory_fts.agent_id = ?
                          AND node.valid_to IS NULL
                          AND memory_fts MATCH ?
                        ORDER BY rank
                        LIMIT 50
                        """,
                        (self._agent_id, match),
                    ).fetchall()
        except sqlite3.Error:
            return {}
        # bm25() is lower=better (typically negative); map to 0..1 higher=better,
        # RELATIVE TO THE BEST match. A min-max span (worst -> 0.0) silently
        # stripped the lexical leg from every match but the best — with two
        # matching documents the weaker one scored exactly 0.0 and lost the leg
        # entirely (measured on value-update pairs). raw/best preserves order and
        # keeps every genuine match positive.
        ranks = [(str(r["node_id"]), float(r["rank"])) for r in rows]
        if not ranks:
            return {}
        best = min(r for _id, r in ranks)
        if best == 0.0:
            return {nid: 1.0 for nid, _ in ranks}
        return {nid: min(1.0, best / r) if r else 1.0 for nid, r in ranks}

    def _node_get_for_search(
        self,
        node_id: str,
        *,
        session_id: str | None,
    ) -> MemoryNode | None:
        normalized = str(node_id or "").strip()
        if not normalized:
            return None
        if session_id is None:
            with self._lock:
                row = self._conn.execute(
                    """
                    SELECT *
                    FROM memory_nodes
                    WHERE node_id = ?
                      AND agent_id = ?
                      AND valid_to IS NULL
                    LIMIT 1
                    """,
                    (normalized, self._agent_id),
                ).fetchone()
            return _row_to_node(row) if row else None
        session_scope = _requested_session_scope(session_id)
        if session_scope is None:
            return None
        with self._lock:
            row = self._conn.execute(
                """
                SELECT *
                FROM memory_nodes
                WHERE node_id = ?
                  AND agent_id = ?
                  AND valid_to IS NULL
                  AND vool_scope_matches(tags, context_description, ?) = 1
                LIMIT 1
                """,
                (normalized, self._agent_id, session_scope),
            ).fetchone()
        return _row_to_node(row) if row else None

    def _bump_access(
        self,
        node_ids: list[str],
        now: float,
        *,
        session_id: str | None = None,
    ) -> None:
        if not node_ids:
            return
        with self._lock:
            if session_id is None:
                self._conn.executemany(
                    "UPDATE memory_nodes SET access_count = access_count + 1, last_access = ? "
                    "WHERE node_id = ? AND agent_id = ? AND valid_to IS NULL",
                    [(now, nid, self._agent_id) for nid in node_ids],
                )
            else:
                session_scope = _requested_session_scope(session_id)
                if session_scope is None:
                    return
                self._conn.executemany(
                    """
                    UPDATE memory_nodes
                    SET access_count = access_count + 1, last_access = ?
                    WHERE node_id = ?
                      AND agent_id = ?
                      AND valid_to IS NULL
                      AND vool_scope_matches(tags, context_description, ?) = 1
                    """,
                    [
                        (now, nid, self._agent_id, session_scope)
                        for nid in node_ids
                    ],
                )
            self._conn.commit()

    def node_invalidate(self, node_id: str, *, at: float | None = None) -> None:
        """Bi-temporal soft-delete: mark a node no longer valid (excluded from
        retrieval) without losing the historical row."""
        normalized = str(node_id or "").strip()
        if not normalized:
            return
        with self._lock:
            self._conn.execute(
                "UPDATE memory_nodes SET valid_to = ? WHERE node_id = ? AND agent_id = ? AND valid_to IS NULL",
                (float(at if at is not None else time.time()), normalized, self._agent_id),
            )
            self._conn.commit()

    def node_invalidate_matching(
        self,
        text: str,
        *,
        session_id: str | None = None,
        at: float | None = None,
    ) -> int:
        """Forget law: HARD-delete live nodes carrying ``text`` in one chat,
        re-admitting the token-free clauses of the same statements.

        Forgetting is a mutation, so it must use the same provenance boundary
        as retrieval. A missing or invalid session scope matches nothing;
        callers cannot accidentally erase another chat's semantic memory.

        The rows (and their FTS legs) are removed, not end-dated: a soft
        ``valid_to`` invalidation still leaves the payload bytes readable in
        the table, and every raw-store reader — not just the retrieval paths
        that filter on validity — could still surface a value the user asked
        to forget (sealed acceptance F14-06/07/10/11: forgotten tokens
        remained present on reachable stores). The durable
        ``memory_revocations`` ledger keeps the audit trail of WHAT was
        forgotten, so hard-deleting the payload loses no accounting.

        Value-scoped preservation (sealed F14-07/12): a compound statement
        ("morning code 5501 and evening code 6602") mints ONE node, so
        deleting the 5501-carrier would also destroy the sibling 6602 the
        user did NOT ask to forget. Before each delete, the token-free
        clauses of the statement are re-admitted as a salvage node under the
        same scope/provenance — forgetting one value must not erase the
        rest of the user's own statement.
        """
        needle = _normalize_forget_token(text)
        if not needle:
            return 0
        expected_scope = (
            _requested_session_scope(session_id)
            if session_id is not None
            else None
        )
        if session_id is not None and expected_scope is None:
            return 0
        # SQL prefilter on the needle's first word, then the separator-
        # insensitive match decides (a hyphenated variant shares the first
        # word but defeats plain substring matching — F14-07).
        first_word = needle.split(" ", 1)[0]
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT *
                FROM memory_nodes
                WHERE agent_id = ?
                  AND valid_to IS NULL
                  AND instr(lower(content), ?) > 0
                """,
                (self._agent_id, first_word),
            ).fetchall()
            doomed: list[Any] = []
            for row in rows:
                if not text_carries_forget_token(str(row["content"] or ""), needle):
                    continue
                if expected_scope is not None:
                    node = _row_to_node(row)
                    if not _node_matches_session(node, session_id or ""):
                        continue
                doomed.append(row)
            if not doomed:
                return 0
            node_ids = [str(row["node_id"]) for row in doomed]
            salvages = [
                (
                    _forget_salvage_content(str(row["content"] or ""), needle),
                    row,
                )
                for row in doomed
            ]
            self._conn.executemany(
                "DELETE FROM memory_nodes WHERE node_id = ? AND agent_id = ?",
                [(node_id, self._agent_id) for node_id in node_ids],
            )
            with contextlib.suppress(sqlite3.Error):  # FTS is a best-effort leg
                self._conn.executemany(
                    "DELETE FROM memory_fts WHERE node_id = ?",
                    [(node_id,) for node_id in node_ids],
                )
            self._conn.commit()
        # Salvage runs AFTER the delete commits, outside the sweep statement:
        # each re-admission is its own write (dedup/FTS inside node_store),
        # and a salvage failure must never resurrect the forgotten value.
        for salvage_text, row in salvages:
            if not salvage_text:
                continue
            try:
                self._store_forget_salvage(salvage_text, row, needle)
            except Exception:
                LOGGER.warning("forget salvage re-admission failed")
        return len(node_ids)

    def _store_forget_salvage(self, salvage_text: str, dead_row: Any,
                              needle: str) -> None:
        """Re-admit one token-free remainder of a deleted forget-law node.

        Provenance is inherited from the dead node (scope tags, context
        description, importance); keywords are the dead node's keywords that
        do not carry the forgotten token. Marked ``forget-salvage`` so the
        lineage stays inspectable. No source-occurrence link: the occurrence
        layer clears whole bodies by its own settled law.
        """
        from core.embedding_service import embed_stamped

        dead_tags = [
            str(tag or "").strip()
            for tag in _json_list(dead_row["tags"])
            if str(tag or "").strip()
        ]
        keywords = [
            str(kw or "").strip()
            for kw in _json_list(dead_row["keywords"])
            if str(kw or "").strip()
            and not text_carries_forget_token(str(kw or ""), needle)
        ]
        vec, backend = embed_stamped(salvage_text)
        self.node_store(
            content=salvage_text,
            keywords=keywords,
            tags=[*dead_tags, "forget-salvage"],
            context_description=str(dead_row["context_description"] or ""),
            embedding=vec,
            embedding_backend=backend,
            importance=float(dead_row["base_importance"] or 0.5),
            timestamp=float(dead_row["timestamp"] or time.time()),
        )

    def node_delete_by_lineage(self, request_id: str) -> int:
        """A8 erasure traversal: HARD-delete every node whose lineage_request_id
        matches (rows + FTS leg). Unlike invalidation, the bytes are removed —
        an erased payload's semantic recall must not survive. Idempotent."""
        rid = str(request_id or "").strip()
        if not rid:
            return 0
        with self._lock:
            rows = self._conn.execute(
                "SELECT node_id FROM memory_nodes WHERE lineage_request_id = ?",
                (rid,),
            ).fetchall()
            node_ids = [str(row["node_id"]) for row in rows]
            if not node_ids:
                return 0
            self._conn.executemany(
                "DELETE FROM memory_nodes WHERE node_id = ?",
                [(node_id,) for node_id in node_ids],
            )
            with contextlib.suppress(sqlite3.Error):  # FTS is a best-effort leg
                self._conn.executemany(
                    "DELETE FROM memory_fts WHERE node_id = ?",
                    [(node_id,) for node_id in node_ids],
                )
            self._conn.commit()
        return len(node_ids)

    def node_delete_for_session(self, session_id: str) -> int:
        """Chat-wide deletion traversal: HARD-delete every node whose stored
        session scope is EXACTLY this chat (rows + FTS leg).

        ``delete_conversation_session`` removes the whole chat — transcript,
        jsonl memory, meta, namespace — so the semantic nodes minted from
        that chat's statements (via store_turn/append_conversation_event)
        must not survive it: a deleted chat's facts staying readable in
        memory_nodes is a deletion-sweep gap, not a soft archival question
        (sealed acceptance F14-04). The exact-scope rule from retrieval
        provenance doubles as the preservation boundary: a node carrying
        MORE than one session scope is left for its other owner. The
        ``memory_revocations`` path is not needed here — the namespace
        transition to ``deleted`` is the non-resurrection authority for a
        whole-chat wipe.
        """
        expected = _requested_session_scope(session_id)
        if expected is None:
            return 0
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT node_id, tags, context_description
                FROM memory_nodes
                WHERE agent_id = ? AND instr(tags, ?) > 0
                """,
                (self._agent_id, expected),
            ).fetchall()
            doomed: list[str] = []
            for row in rows:
                tags = [
                    str(tag or "").strip()
                    for tag in _json_list(row["tags"])
                    if str(tag or "").strip()
                ]
                if _exact_stored_session_scope(
                    tags=tags,
                    context_description=str(row["context_description"] or ""),
                ) != expected:
                    continue
                doomed.append(str(row["node_id"]))
            if not doomed:
                return 0
            self._conn.executemany(
                "DELETE FROM memory_nodes WHERE node_id = ? AND agent_id = ?",
                [(node_id, self._agent_id) for node_id in doomed],
            )
            with contextlib.suppress(sqlite3.Error):  # FTS is a best-effort leg
                self._conn.executemany(
                    "DELETE FROM memory_fts WHERE node_id = ?",
                    [(node_id,) for node_id in doomed],
                )
            self._conn.commit()
        return len(doomed)

    def occurrence_delete_for_chat(self, chat_scope: str) -> int:
        """Chat-wide deletion traversal: HARD-delete every source occurrence
        of one chat (body, FTS and embedding legs together).

        Mirrors node_delete_for_session at layer 1. All statuses are swept —
        a previously token-forgotten row (body already blanked) still belongs
        to a chat that no longer exists and must not survive its deletion.
        """
        scope = str(chat_scope or "").strip()
        if not scope:
            return 0
        with self._lock:
            rows = self._conn.execute(
                "SELECT occurrence_id FROM source_occurrences "
                "WHERE agent_id = ? AND chat_scope = ?",
                (self._agent_id, scope),
            ).fetchall()
            ids = [str(row["occurrence_id"]) for row in rows]
            if not ids:
                return 0
            marks = ", ".join("?" for _ in ids)
            self._conn.execute(
                f"DELETE FROM source_fts WHERE occurrence_id IN ({marks})", ids
            )
            self._conn.execute(
                f"DELETE FROM source_embeddings WHERE occurrence_id IN ({marks})",
                ids,
            )
            self._conn.execute(
                f"DELETE FROM source_occurrences WHERE occurrence_id IN ({marks})",
                ids,
            )
            self._conn.commit()
        return len(ids)

    def node_delete_unlineaged_by_content(
        self, content_hash: str, *, plaintext: str = ""
    ) -> int:
        """A8 erasure traversal legacy leg (pass-002, CE-A; PASS003 digest
        repair): HARD-delete nodes with NO lineage stamp whose content equals
        OR CONTAINS the erased payload. The digest comparison runs through
        core.finalization's ONE canonical matcher — bare hex, ``sha256:``
        prefixed, and keyed-tombstone representations are all recognized (the
        PASS002 failure was a bare-hex-vs-prefixed mismatch that deleted
        nothing while reporting completion). Lineage is never fabricated —
        only rows that never carried one are touched."""
        clean = str(content_hash or "").strip()
        if not clean:
            return 0
        from core.finalization import _text_matches_erasure

        governed_text = str(plaintext or "")
        with self._lock:
            rows = self._conn.execute(
                "SELECT node_id FROM memory_nodes "
                "WHERE (lineage_request_id IS NULL OR lineage_request_id = '')"
            ).fetchall()
            doomed: list[str] = []
            for row in rows:
                node = self._conn.execute(
                    "SELECT content FROM memory_nodes WHERE node_id = ?",
                    (str(row["node_id"]),),
                ).fetchone()
                if node is None:
                    continue
                if _text_matches_erasure(
                        str(node["content"] or ""), clean, governed_text, min_fragment=24
                    ):
                    doomed.append(str(row["node_id"]))
            if not doomed:
                return 0
            self._conn.executemany(
                "DELETE FROM memory_nodes WHERE node_id = ?",
                [(node_id,) for node_id in doomed],
            )
            with contextlib.suppress(sqlite3.Error):  # FTS is a best-effort leg
                self._conn.executemany(
                    "DELETE FROM memory_fts WHERE node_id = ?",
                    [(node_id,) for node_id in doomed],
                )
            self._conn.commit()
        return len(doomed)

    def purge_governed_lines(self, *, content_hash: str, plaintext: str = "") -> int:
        """PASS003 (NCE-D): remove governed LINES from every memory_block so
        erased bytes cannot re-enter prompts via blocks_for_prompt. Lines are
        matched through the same canonical matcher as the node legs. A block
        left empty by the purge is deleted outright."""
        clean = str(content_hash or "").strip()
        if not clean and not str(plaintext or ""):
            return 0
        from core.finalization import _text_matches_erasure

        governed_text = str(plaintext or "")
        removed = 0
        with self._lock:
            rows = self._conn.execute(
                "SELECT block_name, agent_id, content FROM memory_blocks"
            ).fetchall()
            for row in rows:
                content = str(row["content"] or "")
                if not content.strip():
                    continue
                lines = content.split("\n")
                kept = [
                    line
                    for line in lines
                    if not (
                        _text_matches_erasure(
                            line, clean, governed_text, min_fragment=24
                        )
                        # a whole-block copy of the payload survives no more
                        # than a single-line copy would
                        or _text_matches_erasure(content, clean, "", min_fragment=24)
                    )
                ]
                if len(kept) == len(lines):
                    continue
                removed += len(lines) - len(kept)
                block_text = "\n".join(kept)
                if not block_text.strip():
                    self._conn.execute(
                        "DELETE FROM memory_blocks WHERE block_name = ? AND agent_id = ?",
                        (str(row["block_name"]), str(row["agent_id"])),
                    )
                else:
                    self._conn.execute(
                        "UPDATE memory_blocks SET content = ? WHERE block_name = ? AND agent_id = ?",
                        (block_text, str(row["block_name"]), str(row["agent_id"])),
                    )
            self._conn.commit()
        return removed

    def prune(self, max_nodes: int) -> int:
        """Budgeted prune: keep the highest effective-importance LIVE nodes,
        invalidate the rest. Returns the count invalidated. Pure-Python so it
        works on any sqlite build (no SQL math functions required)."""
        now = time.time()
        with self._lock:
            rows = self._conn.execute(
                "SELECT node_id, base_importance, access_count, last_access, timestamp "
                "FROM memory_nodes WHERE agent_id = ? AND valid_to IS NULL",
                (self._agent_id,),
            ).fetchall()
        if len(rows) <= max_nodes:
            return 0
        ranked = sorted(
            rows,
            key=lambda r: _effective_importance(
                base=_row_float(r, "base_importance", 0.5),
                access_count=_row_int(r, "access_count", 0),
                age_days=max(0.0, (now - _row_float(r, "last_access", _row_float(r, "timestamp", now))) / 86400.0),
                half_life=self._HALF_LIFE_DAYS, w_recency=self._W_RECENCY,
                w_freq=self._W_FREQ, freq_cap=self._FREQ_CAP,
            ),
            reverse=True,
        )
        to_drop = [str(r["node_id"]) for r in ranked[max_nodes:]]
        for nid in to_drop:
            self.node_invalidate(nid, at=now)
        return len(to_drop)

    def recent_nodes(self, limit: int = 20) -> list[MemoryNode]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT *
                FROM memory_nodes
                WHERE agent_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (self._agent_id, max(1, int(limit))),
            ).fetchall()
        return [_row_to_node(row) for row in rows]

    def node_count(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS count FROM memory_nodes WHERE agent_id = ?",
                (self._agent_id,),
            ).fetchone()
        return int(row["count"] if row else 0)

    # ── layer-1 source occurrences (CONTRACT mr29/1 §1) ─────────────────────

    def occurrence_store(
        self,
        *,
        chat_scope: str,
        role: str,
        body: str,
        authority: str,
        project_id: str | None = None,
        speaker: str = "",
        recorded_at: float | None = None,
        statement_at: float | None = None,
        event_at: float | None = None,
        source_kind: str = "live-turn",
        import_batch: str | None = None,
        request_id: str = "",
    ) -> SourceOccurrence:
        """Persist ONE source occurrence of what was said (verbatim, redacted).

        No admission gate applies here: retention of source evidence is
        unconditional within its chat scope. Semantic admission (layer 2)
        decides separately whether an INDEX row is worth deriving. The body
        must already be redacted by the write seam (one redaction, at write).

        Idempotency: a retry of the same finalized turn (same request_id,
        role and body bytes) re-reads the existing occurrence instead of
        minting a duplicate. Distinct utterances that merely share bytes
        (different request lineage, or no lineage at all) stay distinct.
        """
        import uuid as _uuid

        scope = str(chat_scope or "").strip()
        if not scope:
            raise ValueError("occurrence chat_scope is required")
        clean_role = str(role or "").strip().lower()
        if clean_role not in OCCURRENCE_ROLES:
            raise ValueError(f"invalid occurrence role: {role!r}")
        clean_authority = str(authority or "").strip()
        if clean_authority not in OCCURRENCE_AUTHORITIES:
            raise ValueError(f"invalid occurrence authority: {authority!r}")
        text = str(body or "")
        if not text.strip():
            raise ValueError("occurrence body is required")
        ts = float(recorded_at if recorded_at is not None else time.time())
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        rid = str(request_id or "")
        with self._lock:
            if rid:
                row = self._conn.execute(
                    "SELECT rowid AS source_sequence, * FROM source_occurrences WHERE agent_id = ? "
                    "AND request_id = ? AND role = ? AND body_sha256 = ? "
                    "AND status = 'active'",
                    (self._agent_id, rid, clean_role, digest),
                ).fetchone()
                if row is not None:
                    return _row_to_occurrence(row)
            occurrence_id = str(_uuid.uuid4())
            self._conn.execute(
                """
                INSERT INTO source_occurrences (
                    occurrence_id, agent_id, chat_scope, project_id, role,
                    speaker, authority, recorded_at, statement_at, event_at,
                    body, source_kind, import_batch, status, request_id,
                    body_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
                """,
                (
                    occurrence_id, self._agent_id, scope, project_id, clean_role,
                    str(speaker or ""), clean_authority, ts,
                    float(statement_at) if statement_at is not None else None,
                    float(event_at) if event_at is not None else None,
                    text, str(source_kind or "live-turn"),
                    import_batch, rid, digest,
                ),
            )
            self._conn.execute(
                "INSERT INTO source_fts (occurrence_id, body) VALUES (?, ?)",
                (occurrence_id, text),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT rowid AS source_sequence, * FROM source_occurrences WHERE occurrence_id = ?",
                (occurrence_id,),
            ).fetchone()
        return _row_to_occurrence(row)

    def occurrence_get(self, occurrence_id: str) -> SourceOccurrence | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT rowid AS source_sequence, * FROM source_occurrences WHERE occurrence_id = ? AND agent_id = ?",
                (str(occurrence_id or ""), self._agent_id),
            ).fetchone()
        return _row_to_occurrence(row) if row is not None else None

    def _reset_integrity_refusals(self) -> None:
        """Begin a new gated read call: telemetry reflects THIS call."""
        with self._lock:
            self.last_integrity_refusals = []

    def _serving_occurrence(self, row: sqlite3.Row) -> SourceOccurrence | None:
        """Convert a row for SERVING, refusing a damaged body (F16 integrity).

        The digest recorded at the authorized write is the integrity anchor:
        a body whose current bytes no longer match it must never reach a
        prompt, capsule, materialization or derivative index. Refusing here
        is shared by every serving path — lexical, semantic, neighbors and
        the embedding backfill — so no lane can relay damaged bytes. A
        refusal is OBSERVABLE as a refusal (counted + named in telemetry),
        never mistakable for a retrieval miss. Legacy rows without a
        recorded digest stay servable ('legacy-unverified') so legacy recall
        is not silently dropped.
        """
        occurrence = _row_to_occurrence(row)
        if occurrence.body_integrity == "mismatch":
            with self._lock:
                self.integrity_refusals_total += 1
                self.last_integrity_refusals.append({
                    "occurrence_id": occurrence.occurrence_id,
                    "reason": "body_sha256_mismatch",
                })
            return None
        return occurrence

    def occurrence_search(
        self,
        query_text: str,
        *,
        chat_scope: str,
        limit: int = 8,
        roles: tuple[str, ...] | list[str] | None = None,
    ) -> list[tuple[SourceOccurrence, float]]:
        """BM25 lexical search over ACTIVE occurrence bodies in one chat scope.

        A pure read: unlike node retrieval, searching evidence never bumps any
        access counter, so repeated evaluation of one store cannot contaminate
        a later comparison arm. The semantic (embedding) leg for occurrences
        is deliberately not computed here — layer-2 admission decides which
        occurrences deserve an embedding derivative; this leg guarantees every
        retained occurrence is findable by its own words regardless.
        """
        terms = [
            term
            for term in re.findall(r"[a-zA-Z0-9_]{2,}", str(query_text or "").lower())
            if term not in _QUERY_STOPWORDS
        ]
        if not terms:
            return []
        scope = str(chat_scope or "").strip()
        if not scope:
            return []
        match = " OR ".join(dict.fromkeys(terms))
        role_filter = ""
        wanted_roles = tuple(
            r for r in (str(item or "").strip().lower() for item in list(roles or [])) if r
        )
        if wanted_roles:
            role_filter = " AND o.role IN (" + ", ".join("?" for _ in wanted_roles) + ")"
        try:
            with self._lock:
                # placeholder order is fixed by the SQL text: scope, agent,
                # MATCH, role filter values, LIMIT — roles must not bind
                # ahead of the MATCH string.
                rows = self._conn.execute(
                    f"""
                    SELECT o.rowid AS source_sequence, o.*, bm25(source_fts) AS rank
                    FROM source_fts
                    JOIN source_occurrences AS o
                      ON o.occurrence_id = source_fts.occurrence_id
                    WHERE o.chat_scope = ?
                      AND o.agent_id = ?
                      AND o.status = 'active'
                      AND o.body != ''
                      AND source_fts MATCH ?{role_filter}
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (scope, self._agent_id, match, *wanted_roles, max(1, int(limit))),
                ).fetchall()
        except sqlite3.Error:
            return []
        out: list[tuple[SourceOccurrence, float]] = []
        self._reset_integrity_refusals()
        for row in rows:
            # bm25() is lower=better (typically negative); map to 0..1
            # higher=better exactly like the memory_fts leg.
            raw = float(row["rank"] or 0.0)
            score = 1.0 / (1.0 + max(0.0, -raw)) if raw < 0 else 0.0
            occurrence = self._serving_occurrence(row)
            if occurrence is None:
                # damaged body refused by the integrity gate (counted in
                # telemetry); serving fewer results is the honest outcome
                continue
            out.append((occurrence, score))
        return out

    def occurrence_embedding_upsert(
        self,
        occurrence_id: str,
        *,
        backend: str,
        vector: list[float],
        body_sha256: str,
    ) -> bool:
        """Store one semantic derivative of a retained occurrence body.

        Idempotent per (occurrence_id, backend). The body itself remains the
        authority at layer 1; this row only makes the occurrence findable by
        meaning. Caller embeds (storage layer stays free of model calls);
        body_sha256 records which body bytes the vector describes so a
        rewritten body is detected as stale by occurrence_embeddings_missing.

        Integrity law (F16): a derivative may only describe VERIFIED
        AUTHORIZED bytes. The call is refused when the occurrence's current
        body disagrees with its ingest-time digest (damaged — never embed
        corrupted bytes) or when the caller's digest is not the digest the
        authorized write recorded (never legitimize a checksum recomputed
        over already-damaged data at read time).
        """
        occ = str(occurrence_id or "")
        be = str(backend or "").strip()
        if not occ or not be:
            return False
        vec = [float(v) for v in list(vector or [])]
        if not vec:
            return False
        payload = json.dumps(vec)
        with self._lock:
            anchor = self._conn.execute(
                "SELECT body, body_sha256 FROM source_occurrences "
                "WHERE occurrence_id = ? AND agent_id = ?",
                (occ, self._agent_id),
            ).fetchone()
            if anchor is None or _occurrence_body_integrity(anchor) != "verified":
                return False
            if str(body_sha256 or "") != str(anchor["body_sha256"]):
                return False
            self._conn.execute(
                """
                INSERT INTO source_embeddings (
                    occurrence_id, agent_id, backend, dim, vector,
                    body_sha256, embedded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(occurrence_id, backend) DO UPDATE SET
                    dim = excluded.dim,
                    vector = excluded.vector,
                    body_sha256 = excluded.body_sha256,
                    embedded_at = excluded.embedded_at
                """,
                (occ, self._agent_id, be, len(vec), payload,
                 str(body_sha256 or ""), time.time()),
            )
            self._conn.commit()
        return True

    def occurrence_embeddings_missing(
        self,
        *,
        chat_scope: str,
        backend: str,
        limit: int = 32,
    ) -> list[tuple[str, str]]:
        """Active occurrences in scope whose CURRENT body has no embedding.

        Stale derivatives (body_sha256 mismatch — the body was rewritten)
        count as missing so they get re-embedded. Bounded by *limit*; the
        caller decides the embedding budget.

        A DAMAGED body (current bytes disagree with the digest recorded at
        the authorized write) is never returned: embedding it would mint a
        derivative of corrupted bytes and invite the caller to recompute a
        digest over already-damaged data — refused and counted in the
        integrity telemetry instead.
        """
        scope = str(chat_scope or "").strip()
        be = str(backend or "").strip()
        if not scope or not be:
            return []
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT o.occurrence_id, o.body, o.body_sha256
                FROM source_occurrences AS o
                LEFT JOIN source_embeddings AS e
                  ON e.occurrence_id = o.occurrence_id AND e.backend = ?
                WHERE o.agent_id = ? AND o.chat_scope = ?
                  AND o.status = 'active' AND o.body != ''
                  AND (e.occurrence_id IS NULL OR e.body_sha256 != o.body_sha256)
                ORDER BY o.recorded_at DESC
                LIMIT ?
                """,
                (be, self._agent_id, scope, max(1, int(limit))),
            ).fetchall()
        self._reset_integrity_refusals()
        out: list[tuple[str, str]] = []
        for row in rows:
            if _occurrence_body_integrity(row) == "mismatch":
                with self._lock:
                    self.integrity_refusals_total += 1
                    self.last_integrity_refusals.append({
                        "occurrence_id": str(row["occurrence_id"]),
                        "reason": "body_sha256_mismatch_backfill_refused",
                    })
                continue
            out.append((str(row["occurrence_id"]), str(row["body"])))
        return out

    def occurrence_stated_between(
        self,
        *,
        chat_scope: str,
        start: float,
        end: float,
        limit: int = 64,
    ) -> list[SourceOccurrence]:
        """ACTIVE occurrences in one chat scope whose source-supported
        statement time lies in ``[start, end]`` (unix seconds, inclusive).

        Pure read, ordered nearest-first to the window's midpoint, then in
        capture order. Rows with no statement time are never returned: the
        system write time is not a statement time. Every row passes the same
        serving integrity gate as the other occurrence reads.
        """
        scope = str(chat_scope or "").strip()
        if not scope:
            return []
        lo, hi = float(start), float(end)
        if hi < lo:
            return []
        middle = (lo + hi) / 2.0
        with self._lock:
            rows = self._conn.execute(
                "SELECT rowid AS source_sequence, * FROM source_occurrences "
                "WHERE agent_id = ? AND chat_scope = ? AND status = 'active' "
                "AND body != '' AND statement_at IS NOT NULL "
                "AND statement_at >= ? AND statement_at <= ? "
                "ORDER BY abs(statement_at - ?) ASC, recorded_at ASC, rowid ASC LIMIT ?",
                (self._agent_id, scope, lo, hi, middle, max(1, int(limit))),
            ).fetchall()
        self._reset_integrity_refusals()
        return [
            occurrence
            for occurrence in (self._serving_occurrence(row) for row in rows)
            if occurrence is not None
        ]

    def occurrence_active_count(self, *, chat_scope: str) -> int:
        """Active, non-empty occurrences in one chat scope (pure read)."""
        scope = str(chat_scope or "").strip()
        if not scope:
            return 0
        with self._lock:
            row = self._conn.execute(
                "SELECT count(*) FROM source_occurrences "
                "WHERE agent_id = ? AND chat_scope = ? "
                "AND status = 'active' AND body != ''",
                (self._agent_id, scope),
            ).fetchone()
        return int(row[0] if row else 0)

    def occurrence_search_semantic(
        self,
        query_vector: list[float],
        *,
        chat_scope: str,
        backend: str,
        floor: float | None = None,
        limit: int = 8,
    ) -> list[tuple[SourceOccurrence, float]]:
        """Cosine search over embedded ACTIVE occurrences in one chat scope.

        Pure read. Backend-consistent only: rows embedded by a different
        backend are invisible — vector spaces are not comparable across
        backends, and pretending otherwise would silently mix geometries.
        """
        scope = str(chat_scope or "").strip()
        be = str(backend or "").strip()
        q_vec = [float(v) for v in list(query_vector or [])]
        if not scope or not be or not q_vec:
            return []
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT o.rowid AS source_sequence, o.*, e.vector
                FROM source_embeddings AS e
                JOIN source_occurrences AS o
                  ON o.occurrence_id = e.occurrence_id
                WHERE e.agent_id = ? AND e.backend = ?
                  AND o.agent_id = ? AND o.chat_scope = ?
                  AND o.status = 'active' AND o.body != ''
                """,
                (self._agent_id, be, self._agent_id, scope),
            ).fetchall()
        scored: list[tuple[float, Any]] = []
        for row in rows:
            try:
                vec = json.loads(str(row["vector"] or "[]"))
            except (ValueError, TypeError):
                continue
            if not vec or len(vec) != len(q_vec):
                continue
            sim = _cosine_similarity(q_vec, vec)
            if floor is None or sim >= float(floor):
                scored.append((sim, row))
        scored.sort(key=lambda pair: (-pair[0], str(pair[1]["occurrence_id"])))
        out: list[tuple[SourceOccurrence, float]] = []
        self._reset_integrity_refusals()
        for sim, row in scored[: max(1, int(limit))]:
            occurrence = self._serving_occurrence(row)
            if occurrence is None:
                # damaged body refused by the integrity gate even though a
                # (stale-content) embedding matched; never relay the bytes
                continue
            out.append((occurrence, float(sim)))
        return out

    def occurrence_neighbors(
        self,
        occurrence: SourceOccurrence,
        *,
        before: int = 1,
        after: int = 1,
    ) -> list[SourceOccurrence]:
        """Adjacent occurrences in the same chat, for referent resolution.

        Materializing a span may need its parent/neighbor clauses ("it",
        "that plan") to be meaningful. Neighbors are ordered as spoken.
        """
        with self._lock:
            # Tied recorded_at (frozen benchmark clock, back-to-back writes in
            # one tick) must still order as spoken. rowid is the write order,
            # so the timestamp boundary is rowid-aware at the tie; strictly
            # ordered clocks behave exactly as before.
            anchor_rowid = "SELECT rowid FROM source_occurrences WHERE occurrence_id = ?"
            rows_before = self._conn.execute(
                "SELECT rowid AS source_sequence, * FROM source_occurrences WHERE agent_id = ? AND chat_scope = ? "
                "AND status = 'active' "
                "AND (recorded_at < ? OR (recorded_at = ? AND rowid < (" + anchor_rowid + "))) "
                "ORDER BY recorded_at DESC, rowid DESC LIMIT ?",
                (self._agent_id, occurrence.chat_scope, occurrence.recorded_at,
                 occurrence.recorded_at, occurrence.occurrence_id, max(0, int(before))),
            ).fetchall()
            rows_after = self._conn.execute(
                "SELECT rowid AS source_sequence, * FROM source_occurrences WHERE agent_id = ? AND chat_scope = ? "
                "AND status = 'active' "
                "AND (recorded_at > ? OR (recorded_at = ? AND rowid > (" + anchor_rowid + "))) "
                "ORDER BY recorded_at ASC, rowid ASC LIMIT ?",
                (self._agent_id, occurrence.chat_scope, occurrence.recorded_at,
                 occurrence.recorded_at, occurrence.occurrence_id, max(0, int(after))),
            ).fetchall()
        self._reset_integrity_refusals()
        ordered = [
            occurrence
            for occurrence in (self._serving_occurrence(r) for r in reversed(rows_before))
            if occurrence is not None
        ]
        ordered += [
            occurrence
            for occurrence in (self._serving_occurrence(r) for r in rows_after)
            if occurrence is not None
        ]
        return ordered

    def occurrence_delete(
        self,
        *,
        occurrence_id: str | None = None,
        chat_scope: str | None = None,
        import_batch: str | None = None,
        revoked: bool = False,
    ) -> int:
        """Delete (or revoke) source bodies AND their derivative index rows.

        Deletion covers the body and every derivative that can re-serve it:
        the FTS row is removed with the body, and the tombstone keeps only
        the occurrence identity (body cleared, status 'deleted'/'revoked').
        Returns the number of occurrences affected.
        """
        clauses = ["agent_id = ?", "status = 'active'"]
        params: list[Any] = [self._agent_id]
        if occurrence_id is not None:
            clauses.append("occurrence_id = ?")
            params.append(str(occurrence_id))
        if chat_scope is not None:
            clauses.append("chat_scope = ?")
            params.append(str(chat_scope))
        if import_batch is not None:
            clauses.append("import_batch = ?")
            params.append(str(import_batch))
        new_status = "revoked" if revoked else "deleted"
        where = " AND ".join(clauses)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT occurrence_id FROM source_occurrences WHERE {where}",
                params,
            ).fetchall()
            ids = [str(row["occurrence_id"]) for row in rows]
            if not ids:
                return 0
            marks = ", ".join("?" for _ in ids)
            self._conn.execute(
                f"DELETE FROM source_fts WHERE occurrence_id IN ({marks})", ids
            )
            # Semantic derivatives die with the body: forgotten content must
            # not resurface through the embedding leg (CONTRACT mr29/1 §1.2
            # derivative law).
            self._conn.execute(
                f"DELETE FROM source_embeddings WHERE occurrence_id IN ({marks})", ids
            )
            self._conn.execute(
                f"UPDATE source_occurrences SET body = '', body_sha256 = '', "
                f"status = ? WHERE occurrence_id IN ({marks})",
                [new_status, *ids],
            )
            self._conn.commit()
        return len(ids)

    def occurrence_invalidate_matching(
        self,
        token: str,
        *,
        chat_scope: str,
    ) -> int:
        """Forget law for layer 1: remove *token* from occurrences of this chat.

        Mirrors node_invalidate_matching so a forget command retires BOTH
        layers — otherwise the semantic ledger would forget a value the
        source archive still recalls verbatim.

        Value-scoped preservation (sealed acceptance F14-02): a body whose
        ONLY carrier is the assistant turn ("Kiln load logged: 11 stoneware
        pieces, cone 9 schedule, password-protected run with code SALT-4.")
        loses its sibling facts when the whole body clears. When the body has
        token-free clauses, they are RETAINED as the new body (hash and FTS
        resynced, stale embeddings dropped for the search-time backfill) —
        the token bytes still vanish from every reachable surface. A body
        with no token-free remainder clears whole (status='deleted') as
        before.
        """
        needle = _normalize_forget_token(token)
        if not needle:
            return 0
        import hashlib as _hashlib

        with self._lock:
            rows = self._conn.execute(
                "SELECT occurrence_id, body FROM source_occurrences "
                "WHERE agent_id = ? AND chat_scope = ? AND status = 'active' "
                "AND body != ''",
                (self._agent_id, str(chat_scope or "").strip()),
            ).fetchall()
            matched = [
                (str(row["occurrence_id"]), str(row["body"] or ""))
                for row in rows
                if text_carries_forget_token(str(row["body"] or ""), needle)
            ]
            if not matched:
                return 0
            cleared: list[str] = []
            salvaged: list[tuple[str, str]] = []
            for occ_id, body in matched:
                remainder = _forget_salvage_content(
                    body, needle, clause_join=", ")
                if remainder:
                    salvaged.append((occ_id, remainder))
                else:
                    cleared.append(occ_id)
            if cleared:
                marks = ", ".join("?" for _ in cleared)
                self._conn.execute(
                    f"DELETE FROM source_fts WHERE occurrence_id IN ({marks})",
                    cleared,
                )
                # Semantic derivatives die with the body: forgotten content
                # must not resurface through the embedding leg (CONTRACT
                # mr29/1 §1.2 derivative law).
                self._conn.execute(
                    f"DELETE FROM source_embeddings WHERE occurrence_id IN "
                    f"({marks})",
                    cleared,
                )
                self._conn.execute(
                    f"UPDATE source_occurrences SET body = '', body_sha256 = '', "
                    f"status = 'deleted' WHERE occurrence_id IN ({marks})",
                    cleared,
                )
            for occ_id, remainder in salvaged:
                # Authorized forget-write: the integrity anchor is recomputed
                # over the retained bytes (the F16 anchor law binds hashes to
                # authorized writes, not to row immutability).
                self._conn.execute(
                    "DELETE FROM source_fts WHERE occurrence_id = ?", (occ_id,)
                )
                with contextlib.suppress(sqlite3.Error):
                    self._conn.execute(
                        "INSERT INTO source_fts (occurrence_id, body) "
                        "VALUES (?, ?)",
                        (occ_id, remainder),
                    )
                # Stale vectors fail the body_sha256 staleness guard anyway;
                # dropping them lets the search-time backfill re-embed the
                # retained clauses.
                self._conn.execute(
                    "DELETE FROM source_embeddings WHERE occurrence_id = ?",
                    (occ_id,),
                )
                self._conn.execute(
                    "UPDATE source_occurrences SET body = ?, body_sha256 = ? "
                    "WHERE occurrence_id = ?",
                    (
                        remainder,
                        _hashlib.sha256(remainder.encode("utf-8")).hexdigest(),
                        occ_id,
                    ),
                )
            self._conn.commit()
            self._forget_derived_receipts(needle, chat_scope=str(chat_scope or "").strip())
        return len(matched)

    def _forget_derived_receipts(self, needle: str, *, chat_scope: str) -> None:
        """The memory kernel's receipts are derived from source bodies: the forget law covers them too.

        Every receipt of this chat whose stored text (head sentence, typed facts, change lines) carries the
        forgotten token is dropped and, when its source body still serves (a clause-salvaged remainder),
        rebuilt from that body -- so a later statement's change line can no longer name the forgotten value
        either. Without this, the forgotten value stayed at rest in ``memory_receipts`` and could re-enter the
        reader's packet (measured on the v14.x port, 2026-10-07). A store without the receipts table is
        untouched.
        """
        with self._lock:
            try:
                rows = self._conn.execute(
                    "SELECT occurrence_id, head_text, facts_json, changes_json, withdraws_json FROM memory_receipts "
                    "WHERE agent_id = ? AND chat_scope = ?",
                    (self._agent_id, chat_scope),
                ).fetchall()
            except sqlite3.Error:
                return
            stale = [
                str(row[0]) for row in rows
                if any(text_carries_forget_token(str(value or ""), needle) for value in tuple(row)[1:])
            ]
            if not stale:
                return
            marks = ", ".join("?" for _ in stale)
            self._conn.execute(f"DELETE FROM memory_receipts WHERE occurrence_id IN ({marks})", stale)
            survivors = self._conn.execute(
                f"SELECT occurrence_id, role, body, statement_at FROM source_occurrences "
                f"WHERE occurrence_id IN ({marks}) AND status = 'active' AND body != '' "
                f"ORDER BY COALESCE(statement_at, 0), rowid",
                stale,
            ).fetchall()
            self._conn.commit()
            if not survivors:
                return
            try:
                from types import SimpleNamespace

                from core.memory_receipts import write_receipt
            except Exception:
                return
            for row in survivors:
                occurrence = SimpleNamespace(
                    occurrence_id=str(row["occurrence_id"]), role=str(row["role"] or "user"),
                    statement_at=row["statement_at"], body=str(row["body"] or ""),
                )
                write_receipt(self, occurrence, chat_scope=chat_scope)

    def record_revocation(self, token: str, *, chat_id: str) -> str | None:
        """Record a durable forget-law revocation for one chat scope.

        The ledger is the non-resurrection authority: prompt carriers filter
        on it, so a forgotten value cannot re-enter a prompt through a store
        the point-in-time sweep missed (or through later reconstruction).
        Chat-scoped by design — forgetting a token here never revokes it in a
        foreign chat (F14-12 preservation control).
        """
        # Stored in canonical form: the carriers gate with the same
        # separator-insensitive normalization, so a hyphenated variant of a
        # revoked token cannot slip past the gate (F14-07).
        needle = _normalize_forget_token(token)
        scope = str(chat_id or "").strip()
        if not needle or not scope:
            return None
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO memory_revocations (token, chat_id, revoked_at)
                VALUES (?, ?, ?)
                ON CONFLICT(token, chat_id) DO NOTHING
                """,
                (needle, scope, time.time()),
            )
            self._conn.commit()
        return needle

    def revoked_tokens(self, chat_id: str) -> tuple[str, ...]:
        """Active forget-law revocations for one chat scope, casefolded."""
        scope = str(chat_id or "").strip()
        if not scope:
            return ()
        try:
            with self._lock:
                rows = self._conn.execute(
                    "SELECT token FROM memory_revocations WHERE chat_id = ? "
                    "ORDER BY revoked_at, token",
                    (scope,),
                ).fetchall()
        except sqlite3.Error:
            return ()
        return tuple(str(row["token"]) for row in rows)

    def occurrence_delete_by_lineage(self, request_id: str) -> int:
        """A8 erasure traversal hook: remove occurrences of one request id."""
        rid = str(request_id or "").strip()
        if not rid:
            return 0
        return self._occurrence_delete_where("request_id = ?", (rid,))

    def _occurrence_delete_where(self, where: str, params: list[Any]) -> int:
        with self._lock:
            rows = self._conn.execute(
                f"SELECT occurrence_id FROM source_occurrences "
                f"WHERE agent_id = ? AND status = 'active' AND {where}",
                [self._agent_id, *params],
            ).fetchall()
            ids = [str(row["occurrence_id"]) for row in rows]
            if not ids:
                return 0
            marks = ", ".join("?" for _ in ids)
            self._conn.execute(
                f"DELETE FROM source_fts WHERE occurrence_id IN ({marks})", ids
            )
            # Semantic derivatives die with the body: forgotten content must
            # not resurface through the embedding leg (CONTRACT mr29/1 §1.2
            # derivative law).
            self._conn.execute(
                f"DELETE FROM source_embeddings WHERE occurrence_id IN ({marks})", ids
            )
            self._conn.execute(
                f"UPDATE source_occurrences SET body = '', body_sha256 = '', "
                f"status = 'deleted' WHERE occurrence_id IN ({marks})",
                ids,
            )
            self._conn.commit()
        return len(ids)

    def occurrence_ids_for_batch(
        self,
        import_batch: str,
        *,
        chat_scope: str | None = None,
    ) -> list[str]:
        """Active occurrence ids of one import batch (optionally one scope)."""
        batch = str(import_batch or "").strip()
        if not batch:
            return []
        sql = ("SELECT occurrence_id FROM source_occurrences "
               "WHERE agent_id = ? AND import_batch = ?")
        params: list[Any] = [self._agent_id, batch]
        if chat_scope is not None:
            sql += " AND chat_scope = ?"
            params.append(str(chat_scope))
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [str(row["occurrence_id"]) for row in rows]

    def node_invalidate_by_occurrence(
        self,
        occurrence_ids: list[str],
    ) -> int:
        """Invalidate semantic nodes derived from the given occurrences.

        The deletion law: a derivative surviving its source's deletion is a
        defect. Nodes linked via source_occurrence_id are retired (valid_to
        set); every BM25 path already filters valid_to IS NULL, so the
        keyword leg cannot resurface revoked evidence.
        """
        wanted = [str(item).strip() for item in list(occurrence_ids or []) if str(item).strip()]
        if not wanted:
            return 0
        with self._lock:
            rows = self._conn.execute(
                "SELECT node_id FROM memory_nodes WHERE agent_id = ? "
                "AND source_occurrence_id IN (" + ", ".join("?" for _ in wanted) + ")",
                [self._agent_id, *wanted],
            ).fetchall()
            node_ids = [str(row["node_id"]) for row in rows]
            for node_id in node_ids:
                self.node_invalidate(node_id)
        return len(node_ids)

    def occurrence_count(self, *, chat_scope: str | None = None) -> int:
        sql = "SELECT COUNT(*) AS count FROM source_occurrences WHERE agent_id = ? AND status = 'active'"
        params: list[Any] = [self._agent_id]
        if chat_scope is not None:
            sql += " AND chat_scope = ?"
            params.append(str(chat_scope))
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
        return int(row["count"] if row else 0)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> VoolMemory:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.close()
        return False


def _resolve_db_path(*, runtime_home: str | Path | None, db_path: str | Path | None) -> Path:
    if db_path is not None:
        return Path(db_path).expanduser().resolve()
    home = Path(runtime_home).expanduser().resolve() if runtime_home is not None else active_vool_home()
    memory_dir = (home / "data" / "memory").resolve()
    canonical = memory_dir / "vool_memory.db"
    legacy = memory_dir / "nulla_memory.db"
    if not canonical.exists() and legacy.exists():
        return legacy.resolve()
    return canonical.resolve()


def _normalize_block_name(block_name: str) -> str:
    name = str(block_name or "").strip().lower()
    if not _BLOCK_NAME_RE.match(name):
        raise ValueError(f"invalid memory block name: {block_name!r}")
    return name


def _session_scopes(*, tags: list[str] | None = None, context_description: str = "") -> set[str]:
    scopes: set[str] = set()
    for tag in list(tags or []):
        text = str(tag or "").strip()
        if text.startswith("session:"):
            value = text.removeprefix("session:").strip()
            if value:
                scopes.add(value)
    match = re.search(r"\bsession=([^\s]+)", str(context_description or ""))
    if match:
        value = match.group(1).strip()
        if value:
            scopes.add(value)
    return scopes


_FORGET_SEPARATOR_RE = re.compile(r"[\s\-_]+")


_FORGET_SALVAGE_SUBJECT_START_RE = re.compile(
    r"^(?:the|a|an|my|our|their|his|her|its|this|that|these|those|each|"
    r"every|some|any|all|no)\b",
    re.IGNORECASE,
)


def _forget_salvage_content(
    content: str, needle: str, *, clause_join: str = " and "
) -> str:
    """Token-free remainder of a statement, or "" when nothing survives.

    Clause granularity is conservative: a sentence that does not carry the
    token keeps verbatim; a sentence that does is split on coordinating
    joins ("and"/"but"/"also", commas, semicolons) and only its token-free
    clauses survive. Two further conservative laws:

    * A kept clause must be SELF-CONTAINED — it must start with a determiner
      or a capitalized word, i.e. carry its own subject. A bare predicate
      fragment ("numbered P-77 by coincidence." after its subject clause
      was forgotten) asserts nothing identifiable, and it measurably SHADOWS
      the intact record that answers the question (sealed F10-07 regression:
      the fragment outranked "The prompter's copy number is P-77.").
    * A clause-level miss (the token and its sibling share one clause)
      drops the clause — the forgotten value's non-resurrection outranks
      sibling retention on ambiguity.
    """
    text = str(content or "").strip()
    if not text:
        return ""
    # A needle that spans clause separators ("drawer 14, index B/7") cannot
    # be matched at clause granularity — no single clause carries it whole,
    # so the token-carrying clause itself survived the salvage and kept
    # serving a fragment of the forgotten value (measured, F16-06: "lives in
    # drawer 14" still served after forgetting "drawer 14, index B/7"). A
    # clause carrying ANY distinctive PART of such a needle is a
    # token-carrying clause and is dropped.
    needle_parts = [
        part.strip()
        for part in re.split(r"\s*[,;]\s*", str(needle or ""))
        if len(part.strip()) >= 4
    ]

    def _carries(chunk: str) -> bool:
        if text_carries_forget_token(chunk, needle):
            return True
        return any(
            text_carries_forget_token(chunk, part) for part in needle_parts
        )

    sentences = re.split(r"(?<=[.!?;])\s+", text)
    kept: list[str] = []
    for sentence in sentences:
        if not _carries(sentence):
            kept.append(sentence)
            continue
        clauses = re.split(
            r"\s*[,;]\s+|\s+(?:and|but|also)\s+", sentence, flags=re.IGNORECASE
        )
        clean = []
        for clause in clauses:
            stripped = clause.strip(" ,;")
            if not stripped:
                continue
            if _carries(stripped):
                continue
            first = re.match(r"[A-Za-z]+", stripped)
            if not first:
                continue
            word = first.group(0)
            if not (word[:1].isupper()
                    or _FORGET_SALVAGE_SUBJECT_START_RE.match(word)):
                continue
            clean.append(stripped)
        if clean:
            kept.append(clause_join.join(clean))
    salvage = " ".join(kept).strip()
    if salvage == text:
        # Nothing was actually removed (token matched nothing at clause
        # granularity) — no salvage needed.
        return ""
    return salvage


def _normalize_forget_token(token: str) -> str:
    """Canonical form for forget-law token matching (mirrors the helper in
    core.context_retrieval; kept local to avoid an import cycle).

    Separators (whitespace, hyphen, underscore) and case are not meaning:
    a hyphenated copy of a forgotten token is the same token (measured:
    F14-07's "thermal-pool" survived every plain-substring layer and the
    neural evidence leg re-served the deleted value).
    """
    return " ".join(_FORGET_SEPARATOR_RE.split(str(token or "").casefold())).strip()


def text_carries_forget_token(text: str, needle: str) -> bool:
    """Separator/case-insensitive carry test for one forget-law needle."""
    normalized = _normalize_forget_token(needle)
    if not normalized:
        return False
    return normalized in _normalize_forget_token(text)


def _requested_session_scope(session_id: str | None) -> str | None:
    if session_id is None:
        return None
    normalized = str(session_id or "").strip()
    if not normalized:
        return None
    return f"v2:{hashlib.sha256(normalized.encode('utf-8')).hexdigest()}"


def _exact_stored_session_scope(
    *,
    tags: list[str] | None,
    context_description: str,
) -> str | None:
    scopes = _session_scopes(
        tags=tags,
        context_description=context_description,
    )
    if not scopes:
        return ""
    if len(scopes) != 1:
        return None
    scope = next(iter(scopes))
    if re.fullmatch(r"v2:[0-9a-f]{64}", scope) is None:
        return None
    exact_tag = f"session:{scope}"
    normalized_tags = {
        str(tag or "").strip()
        for tag in list(tags or [])
        if str(tag or "").strip()
    }
    return scope if exact_tag in normalized_tags else None


def _sqlite_scope_matches(
    raw_tags: Any,
    context_description: Any,
    expected_scope: Any,
) -> int:
    tags = [
        str(tag or "").strip()
        for tag in _json_list(raw_tags)
        if str(tag or "").strip()
    ]
    stored_scope = _exact_stored_session_scope(
        tags=tags,
        context_description=str(context_description or ""),
    )
    expected = str(expected_scope or "").strip()
    if stored_scope is None:
        return 0
    return int(stored_scope == expected)


def _node_matches_session(node: MemoryNode, session_id: str) -> bool:
    """Require provenance for scoped retrieval; unscoped legacy nodes are denied."""
    expected = _requested_session_scope(session_id)
    if expected is None:
        return True
    return _exact_stored_session_scope(
        tags=node.tags,
        context_description=node.context_description,
    ) == expected


def _line_exists(existing: str, addition: str) -> bool:
    normalized_addition = " ".join(str(addition or "").split()).lower()
    return any(" ".join(line.split()).lower() == normalized_addition for line in str(existing or "").splitlines())


def _backends_comparable(a: str, b: str) -> bool:
    """True when two embedding stamps name the SAME vector space.

    Equal dimensions do not make spaces interchangeable: a hash-fallback vector
    and a neural vector of the same width are not comparable, and comparing them
    as if they were measured as garbage similarity. '' marks legacy rows recorded
    before backend stamping; those stay comparable only with each other (their
    historical self-consistent behavior) and never with stamped vectors. Re-embed
    (or migrate) to clear legacy rows."""
    return str(a or "").strip() == str(b or "").strip()


def _rank_map(scores: dict[str, float]) -> dict[str, int]:
    """Dense rank (0 = best) over a score dict; deterministic ties by node id."""
    ordered = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    return {nid: rank for rank, (nid, _value) in enumerate(ordered)}


def _row_to_node(row: sqlite3.Row) -> MemoryNode:
    return MemoryNode(
        node_id=str(row["node_id"]),
        content=str(row["content"]),
        timestamp=float(row["timestamp"]),
        keywords=_json_list(row["keywords"]),
        tags=_json_list(row["tags"]),
        context_description=str(row["context_description"]),
        embedding=[float(value) for value in _json_list(row["embedding"])],
        linked_node_ids=_json_list(row["linked_node_ids"]),
        agent_id=str(row["agent_id"]),
        embedding_backend=str(row["embedding_backend"] if "embedding_backend" in row.keys() else ""),
        source_occurrence_id=str(row["source_occurrence_id"] if "source_occurrence_id" in row.keys() else ""),
    )


def _row_to_occurrence(row: sqlite3.Row) -> SourceOccurrence:
    keys = row.keys()
    def _opt_time(name: str) -> float | None:
        value = row[name] if name in row.keys() else None
        return float(value) if value is not None else None

    def _opt_str(name: str) -> str | None:
        value = row[name] if name in row.keys() else None
        return None if value is None else str(value)

    return SourceOccurrence(
        occurrence_id=str(row["occurrence_id"]),
        chat_scope=str(row["chat_scope"]),
        project_id=_opt_str("project_id"),
        role=str(row["role"]),
        speaker=str(row["speaker"] if "speaker" in row.keys() else ""),
        authority=str(row["authority"]),
        recorded_at=float(row["recorded_at"]),
        statement_at=_opt_time("statement_at"),
        event_at=_opt_time("event_at"),
        body=str(row["body"]),
        source_kind=str(row["source_kind"]),
        import_batch=_opt_str("import_batch"),
        status=str(row["status"]),
        request_id=str(row["request_id"] if "request_id" in row.keys() else ""),
        body_integrity=_occurrence_body_integrity(row),
        source_sequence=(
            int(row["source_sequence"])
            if "source_sequence" in keys and row["source_sequence"] is not None else None
        ),
    )


#: A digest written by `occurrence_store` is always 64 lowercase hex chars.
_BODY_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


def _occurrence_body_integrity(row: sqlite3.Row) -> str:
    """Classify a layer-1 row's body against its ingest-time digest.

    The digest in ``body_sha256`` is established at the authorized write
    (``occurrence_store``, over the canonical redacted bytes) and cleared
    together with the body by delete/revoke. This function NEVER mutates
    anything and never re-records a digest over current bytes: it only
    compares sha256(current body) against the digest the authorized write
    recorded, which is exactly the damage model a digest stored beside
    mutable content can detect (not a malicious writer who changes both).

    States:
      'verified'          digest matches — safe to serve as evidence
      'mismatch'          digest recorded but body differs — DAMAGED; serving
                          read APIs must refuse it (never serve, never embed)
      'legacy-unverified' non-empty body with no recorded digest (row written
                          before digest discipline or by an external editor):
                          still served so legacy recall is not silently
                          dropped, but explicitly flagged, never 'verified'
      'cleared'           empty body (delete/revoke tombstone; status governs)
    """
    keys = row.keys()
    if "body_sha256" not in keys:
        return "legacy-unverified"
    digest = str(row["body_sha256"] or "")
    body = str(row["body"] or "")
    if not body:
        return "cleared"
    if not _BODY_DIGEST_RE.match(digest):
        return "legacy-unverified"
    actual = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return "verified" if actual == digest else "mismatch"


def _json_list(raw: Any) -> list[Any]:
    try:
        data = json.loads(str(raw or "[]"))
    except Exception:
        return []
    return list(data) if isinstance(data, list) else []


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    # Delegate to the shared cosine so a dimension mismatch is projected-and-compared
    # (and logged once) instead of silently scoring 0.0 — which previously hid total
    # recall failure when the embedding backend changed dimension between store/query.
    from core.embedding_service import cosine_similarity as _shared_cosine
    return _shared_cosine(a, b)


def _token_set(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]{3,}", str(text or "").lower()))


def _token_overlap(a: set[str], b: set[str]) -> float:
    """Jaccard overlap (symmetric): |a∩b| / |a∪b|. Symmetric so a value-changing
    UPDATE ('deadline July 15' -> 'moved deadline to Aug 1') scores below the
    dedup gate and is NOT swallowed as a duplicate — temporal collapse at
    retrieval handles that case instead."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _scoped_bm25_scores(
    rows: list[sqlite3.Row],
    query_terms: list[str],
) -> dict[str, float]:
    """Compute BM25 using only rows already authorized by the storage query.

    Both document tokens and query terms are PORTER-STEMMED — the same stemming
    authority as this store's FTS5 ``tokenize='porter unicode61'`` index (see
    core/porter_stem.py; equivalence pinned by
    tests/test_porter_stem_equivalence.py). The scoped leg previously matched
    exact tokens only, so a query term that differed morphologically from the
    stored word ("park" vs "parking", "guests" vs "guest") scored NOTHING while
    the unscoped FTS5 leg would have matched it (measured 2026-09-27: a stored
    "Parking: the north lot." fact was unreachable for "Where do guests park?".
    with both retrieval legs)."""
    from core.porter_stem import stem, recall_words

    documents: list[tuple[str, list[str]]] = []
    for row in rows:
        text = f"{row['content']!s} {row['keywords']!s}" 
        tokens = [stem(token) for token in recall_words(text)]
        documents.append((str(row["node_id"]), tokens))
    if not documents:
        return {}

    terms = list(
        dict.fromkeys(stem(str(term).lower()) for term in query_terms if term)
    )
    document_count = len(documents)
    average_length = (
        sum(len(tokens) for _node_id, tokens in documents) / document_count
    ) or 1.0
    document_frequency = {
        term: sum(1 for _node_id, tokens in documents if term in tokens)
        for term in terms
    }
    # Admission vs weighting, separated (the previous fraction cutoff
    # max(2, N/4) conflated them and destroyed discriminative terms):
    #
    # ADMISSION: any term that matches at least one scoped record (df > 0)
    # earns the lexical leg. A subject present in EVERY record of its scope
    # ("helios" in a 6-session store; an exact project name like "aurora"
    # over its 3 records) is a genuine keyword match, not a stopword merely
    # because its scope contains only that project's records — deleting it
    # (the old rule, and an earlier anchor-required revision) left
    # subject-only queries with NO lexical path, and records below the
    # semantic floor became unreachable by hybrid search.
    #
    # WEIGHTING is IDF's job and needs no corpus-side deletion: a df == N
    # term carries IDF ~ log(1 + 0.5/N) ≈ 0, so it adds near-zero score
    # difference and cannot order anything by itself — it only admits the
    # records that genuinely contain it.
    #
    # Generic/stopword-mass queries (the fabrication vector the old cutoff
    # guarded: "my"/"is" earning all-fact legs) are stopped query-side by
    # _QUERY_STOPWORDS; a query left with NO matching term (df == 0) still
    # earns no leg and injects nothing.
    terms = [t for t in terms if document_frequency[t] > 0]
    if not terms:
        return {}
    k1 = 1.2
    b = 0.75
    raw_scores: dict[str, float] = {}
    for node_id, tokens in documents:
        token_count = len(tokens)
        score = 0.0
        for term in terms:
            frequency = tokens.count(term)
            if frequency <= 0:
                continue
            frequency_in_documents = document_frequency[term]
            inverse_document_frequency = math.log(
                1.0
                + (
                    document_count
                    - frequency_in_documents
                    + 0.5
                )
                / (frequency_in_documents + 0.5)
            )
            denominator = frequency + k1 * (
                1.0 - b + b * token_count / average_length
            )
            score += inverse_document_frequency * (
                frequency * (k1 + 1.0) / denominator
            )
        if score > 0.0:
            raw_scores[node_id] = score
    if not raw_scores:
        return {}
    best = max(raw_scores.values())
    return {node_id: score / best for node_id, score in raw_scores.items()}


def _effective_importance(
    *,
    base: float,
    access_count: int,
    age_days: float,
    half_life: float,
    w_recency: float,
    w_freq: float,
    freq_cap: float,
) -> float:
    """Agent-memory-standard effective importance: base * (W_R*recency + W_F*freq).
    recency = exp(-ln2/half_life * age_days); freq = log1p(access)/log1p(cap)."""
    import math
    recency = math.exp(-(math.log(2.0) / max(1e-6, half_life)) * max(0.0, age_days))
    freq = math.log1p(max(0, access_count)) / math.log1p(max(1.0, freq_cap))
    return max(0.0, min(1.0, float(base))) * (w_recency * recency + w_freq * freq)


def _row_float(row: Any, key: str, default: float) -> float:
    try:
        val = row[key]
        return float(val) if val is not None else float(default)
    except (KeyError, IndexError, TypeError, ValueError):
        return float(default)


def _row_int(row: Any, key: str, default: int) -> int:
    try:
        val = row[key]
        return int(val) if val is not None else int(default)
    except (KeyError, IndexError, TypeError, ValueError):
        return int(default)


__all__ = ["MemoryBlock", "MemoryNode", "VoolMemory"]
