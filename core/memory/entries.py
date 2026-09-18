from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.context_namespace import (
    ensure_chat_namespace,
    list_context_imports,
    load_chat_namespace,
)
from core.context_scope import ContextAccessPolicy
from core.memory.files import (
    MAX_MEMORY_INDEX_BYTES,
    chat_session_meta_path,
    conversation_log_path,
    default_memory_template,
    ensure_memory_files,
    load_jsonl,
    memory_entries_path,
    memory_path,
    rewrite_jsonl,
    session_summaries_path,
    today_iso,
    trim_jsonl_file,
    user_heuristics_path,
    utcnow,
    write_text_atomic,
)
from core.memory.policies import ensure_session_policy_table, session_memory_policy
from core.privacy_guard import keyword_tokens, normalize_share_scope
from core.secret_redaction import contains_secret


def _current_request_lineage() -> str:
    """A8 payload lineage: the A0 request id bound to the current dispatch, if
    any ('' on legacy/off-HTTP lanes — never fabricated)."""
    try:
        from core.semantic.semantic_admissions import current_request_id

        return str(current_request_id() or "")
    except Exception:
        return ""

_STOPWORDS = {
    "about",
    "after",
    "again",
    "also",
    "always",
    "another",
    "because",
    "before",
    "being",
    "between",
    "brief",
    "build",
    "change",
    "clear",
    "could",
    "direct",
    "every",
    "explain",
    "from",
    "have",
    "help",
    "honest",
    "keep",
    "like",
    "make",
    "need",
    "please",
    "project",
    "reply",
    "response",
    "responses",
    "run",
    "should",
    "something",
    "still",
    "that",
    "their",
    "them",
    "there",
    "these",
    "they",
    "thing",
    "this",
    "those",
    "through",
    "use",
    "want",
    "with",
    "write",
    "would",
    "your",
}
_HEURISTIC_ALWAYS_INCLUDE = {"response_style", "autonomy_preference", "source_preference", "preferred_stack"}
_MEMORY_ENTRY_LOCK = threading.Lock()

# ---------------------------------------------------------------------------
# P0 erasure law — ONE canonical memory authority.
#
# ``memory_entries.jsonl`` is the authority; ``MEMORY.md`` is a generated
# projection of it (the mirror), refreshed only through
# ``_rewrite_memory_mirror_locked`` with every mirror line BOUND to the
# record id of the governed row it projects. Forgetting a fact converts its
# row into a durable tombstone inside the SAME file in ONE atomic rewrite, so
# a crash can never acknowledge an erasure whose tombstone did not land — or
# land a tombstone whose row survived. Tombstones carry a per-erasure random
# salt and a salted digest of the erased text (never the text, never an
# unsalted digest): a stale, hand-edited or restored mirror line with the
# same text is dropped at the legacy ingest boundary and can never serve.
# ---------------------------------------------------------------------------

_ERASED_STATUS = "erased"
_LEARNED_KNOWLEDGE_MARKER = "## Learned Knowledge"
_MIRROR_BIND_RE = re.compile(r"\s*<!--\s*mem:([A-Za-z0-9_.:-]+)\s*-->\s*$")


class MemoryErasureError(RuntimeError):
    """A forget could not be verified complete on every model-visible path.

    Raised instead of acknowledging success: the caller must not report the
    entry removed while any read path (canonical rows, legacy mirror ingest,
    combined view, model-visible excerpt, or the mirror's own binding) can
    still yield the erased text.
    """


def _erasure_digest_text(text: str) -> str:
    """The canonical preimage for erasure digests: sanitized, case-folded text."""
    return sanitize_fact(text).casefold()


def _salted_erasure_digest(salt: str, text: str) -> str:
    return hashlib.sha256(
        f"{salt}:{_erasure_digest_text(text)}".encode("utf-8")
    ).hexdigest()


def _text_is_erased(text: str) -> bool:
    """True when this exact text has a durable erasure tombstone."""
    normalized = _erasure_digest_text(text)
    if not normalized:
        return False
    for row in load_jsonl(memory_entries_path()):
        if str(row.get("status") or "") != _ERASED_STATUS:
            continue
        salt = str(row.get("erasure_salt") or "")
        digest = str(row.get("erasure_digest") or "")
        if not salt or not digest:
            continue
        candidate = hashlib.sha256(
            f"{salt}:{normalized}".encode("utf-8")
        ).hexdigest()
        if hmac.compare_digest(candidate, digest):
            return True
    return False


def _row_is_active_memory(row: dict[str, Any]) -> bool:
    return (
        str(row.get("status") or "").strip().lower() == "active"
        and bool(str(row.get("record_id") or "").strip())
        and bool(str(row.get("text") or "").strip())
    )


def _erase_rows_locked(record_ids: list[str]) -> list[str]:
    """Identity-bound durable erasure. Caller holds ``_MEMORY_ENTRY_LOCK``.

    Each selected row becomes a tombstone in ONE atomic rewrite of the
    canonical store — the removal and its erasure evidence are the same
    write, so no crash window exists between them. Returns the erased texts
    (in memory only, for verification before any success is returned).
    """
    wanted = {str(rid or "").strip() for rid in record_ids if str(rid or "").strip()}
    if not wanted:
        return []
    rows = load_jsonl(memory_entries_path())
    out: list[dict[str, Any]] = []
    erased_texts: list[str] = []
    for row in rows:
        record_id = str(row.get("record_id") or "").strip()
        if record_id and record_id in wanted and str(row.get("status") or "") != _ERASED_STATUS:
            # A tombstone is not a row: forgetting an id that is already erased removes nothing, so the
            # caller's "True when a row was removed" stays true to the store (measured 2026-09-07: a
            # second forget of the same id reported success and re-salted the tombstone).
            text = str(row.get("text") or "")
            salt = secrets.token_hex(16)
            out.append(
                {
                    "record_id": record_id,
                    "created_at": str(row.get("created_at") or ""),
                    "status": _ERASED_STATUS,
                    "erased_at": utcnow(),
                    # Salted digest of the erased text: verifiable against a
                    # candidate line, never reversible, never precomputable,
                    # and never an unsalted confirmation oracle. The row's
                    # provenance content_hash (an UNSALTED digest) is dropped
                    # with the rest of the payload.
                    "erasure_salt": salt,
                    "erasure_digest": _salted_erasure_digest(salt, text),
                    "scope": str(row.get("scope") or ""),
                }
            )
            erased_texts.append(text)
        else:
            out.append(row)
    if erased_texts:
        rewrite_jsonl(memory_entries_path(), out)
    return erased_texts


def _rewrite_memory_mirror_locked() -> None:
    """Regenerate the MEMORY.md projection from the canonical store.

    Caller holds ``_MEMORY_ENTRY_LOCK``. The Learned Knowledge section is
    rebuilt from ACTIVE governed rows, each line bound to its record id —
    the mirror can never contain a memory that is not exactly some governed
    row. Unbound (legacy / hand-written) lines are preserved verbatim unless
    their text carries a durable erasure tombstone or duplicates a governed
    row: manual input stays on disk for the human, but a forgotten fact
    cannot come back through it.
    """
    path = memory_path()
    if not path.exists():
        path.write_text(default_memory_template(), encoding="utf-8")
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    marker_index = next(
        (i for i, line in enumerate(lines) if line.strip() == _LEARNED_KNOWLEDGE_MARKER),
        None,
    )
    if marker_index is None:
        preamble, section = lines, []
    else:
        preamble, section = lines[: marker_index + 1], lines[marker_index + 1 :]

    active_rows = [
        row for row in load_jsonl(memory_entries_path()) if _row_is_active_memory(row)
    ]
    active_texts = {
        _erasure_digest_text(str(row.get("text") or "")) for row in active_rows
    }
    bound: list[str] = []
    for row in active_rows:
        text = sanitize_fact(str(row.get("text") or ""))
        record_id = str(row.get("record_id") or "").strip()
        if not text or not record_id:
            continue
        created = str(row.get("created_at") or "")
        date = created[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", created) else today_iso()
        bound.append(f"- [{date}] {text} <!--mem:{record_id}-->")

    preserved: list[str] = []
    for line in section:
        stripped = line.strip()
        if not stripped or stripped == "<!-- New memories append below -->":
            continue
        if not stripped.startswith("- "):
            # Section comments and any hand-written notes that are not memory
            # bullets ride through untouched (the append marker is re-emitted
            # exactly once above, so it never accumulates).
            preserved.append(stripped)
            continue
        text = stripped[2:].strip()
        if _MIRROR_BIND_RE.search(text):
            # Bound lines regenerate from the store above; a bound line found
            # in the file is never trusted as content (its row may be gone).
            continue
        created_at = ""
        match = re.match(r"^\[(\d{4}-\d{2}-\d{2})\]\s+(.+)$", text)
        if match:
            created_at = match.group(1)
            text = match.group(2).strip()
        clean = sanitize_fact(text)
        if not clean:
            continue
        digest_text = _erasure_digest_text(clean)
        if digest_text in active_texts:
            continue  # pre-binding duplicate of a governed row
        if _text_is_erased(clean):
            continue  # forgotten content: dropped from the projection everywhere
        preserved.append(f"- [{created_at}] {clean}" if created_at else f"- {clean}")

    body = ["<!-- New memories append below -->", *bound, *preserved]
    content = "\n".join([*preamble, *body]).rstrip() + "\n"
    write_text_atomic(path, content)


def _verify_erasure(erased_texts: list[str], erased_record_ids: list[str]) -> None:
    """Success-after-verification law for forget.

    Every model-visible memory path is re-read AFTER the rewrites: the
    canonical rows, the legacy mirror ingest, the combined view, the excerpt
    the bootstrap injects as ``runtime_memory``, and the mirror's own record
    bindings. Any hit raises ``MemoryErasureError`` — the caller must not
    acknowledge the forget.
    """
    problems: list[str] = []
    needles = [
        needle
        for needle in (
            _erasure_digest_text(text) for text in erased_texts
        )
        if needle
    ]
    # A forget is ID-scoped by design ("Forget" on one row never deletes MORE than that row). A
    # surviving row that legitimately carries the same text -- a twin -- is not a leak of the erased
    # record; its text is its own. Measured 2026-09-07: forgetting one of two identical rows raised
    # MemoryErasureError for the twin and the forget was never acknowledged. Text checks below apply
    # only to needles no surviving canonical row still owns; the id-bound checks stay unconditional.
    erased_ids = {str(record_id) for record_id in erased_record_ids}
    surviving_texts: list[str] = []
    for row in load_jsonl(memory_entries_path()):
        if str(row.get("status") or "") == _ERASED_STATUS or str(row.get("record_id")) in erased_ids:
            continue
        surviving_texts.append(str(row.get("text") or "").casefold())
    needles = [needle for needle in needles if not any(needle in text for text in surviving_texts)]
    for needle in needles:
        for row in load_jsonl(memory_entries_path()):
            if str(row.get("status") or "") == _ERASED_STATUS:
                continue
            if needle in str(row.get("text") or "").casefold():
                problems.append(
                    f"canonical row {row.get('record_id')!r} still carries erased text"
                )
        for row in legacy_memory_entries():
            if needle in str(row.get("text") or "").casefold():
                problems.append("legacy mirror ingest still yields erased text")
        for row in combined_memory_entries():
            if needle in str(row.get("text") or "").casefold():
                problems.append("combined view still yields erased text")
        if needle in load_memory_excerpt(max_chars=2_000_000).casefold():
            problems.append("model-visible memory excerpt still carries erased text")
    try:
        mirror_text = memory_path().read_text(encoding="utf-8", errors="replace")
    except OSError:
        mirror_text = ""
    for record_id in erased_record_ids:
        if f"<!--mem:{record_id}-->" in mirror_text:
            problems.append(f"mirror still binds erased record {record_id!r}")
    if problems:
        raise MemoryErasureError("; ".join(dict.fromkeys(problems)))


def _trim_memory_entries_locked() -> None:
    """Size-trim the canonical store without ever aging out tombstones."""
    trim_jsonl_file(
        memory_entries_path(),
        max_bytes=MAX_MEMORY_INDEX_BYTES,
        always_keep=lambda row: str(row.get("status") or "") == _ERASED_STATUS,
    )


_AUTHORITY_RANK = {
    "model_inference": 1,
    "confirmed_memory": 2,
    "verified_external": 3,
    "verified_action": 4,
    "user_correction": 5,
}
_CORRECTION_RE = re.compile(
    r"\b(?:actually|correction|correct that|i meant|not .+[,;]?\s*(?:it is|it's)|"
    r"changed to|changed my mind|is now|update(?:d)? to|call me)\b",
    re.IGNORECASE,
)


def resolve_memory_access_policy(
    *,
    access_policy: ContextAccessPolicy | None = None,
    chat_id: str | None = None,
) -> ContextAccessPolicy:
    """Return the current persisted policy for one immutable chat namespace.

    Caller-supplied grants are never trusted.  Even when a policy object is
    provided, its namespace and grants are reloaded from the server-owned
    SQLite records before a memory command can read or mutate anything.
    """
    if access_policy is not None and not isinstance(
        access_policy,
        ContextAccessPolicy,
    ):
        raise TypeError("access_policy must be a ContextAccessPolicy")
    policy_chat = (
        str(access_policy.chat_id or "").strip()
        if access_policy is not None
        else ""
    )
    requested_chat = str(chat_id or "").strip()
    if policy_chat and requested_chat and policy_chat != requested_chat:
        raise ValueError("memory access policy does not match chat_id")
    resolved_chat = policy_chat or requested_chat
    if not resolved_chat:
        raise ValueError("chat_id or access_policy is required")

    namespace = load_chat_namespace(resolved_chat)
    if namespace is None:
        raise ValueError("chat namespace does not exist")
    if namespace.lifecycle_state != "active":
        raise ValueError(
            f"chat namespace is {namespace.lifecycle_state}"
        )
    return ContextAccessPolicy(
        chat_id=namespace.chat_id,
        project_id=namespace.project_id,
        namespace_state=namespace.lifecycle_state,
        grants=list_context_imports(namespace.chat_id),
    )


def _memory_write_allowed(
    *,
    access_policy: ContextAccessPolicy,
    scope: str,
    project_id: str,
) -> bool:
    if scope == "chat":
        return True
    if scope == "project":
        return bool(
            project_id
            and project_id == access_policy.project_id
            and project_id in access_policy.imported_project_ids
        )
    if scope == "user_profile":
        return access_policy.allow_user_profile_context
    return False


def _memory_command_row_allowed(
    row: dict[str, Any],
    *,
    access_policy: ContextAccessPolicy,
) -> bool:
    """Command reads never expose another chat, even through a chat import."""
    if contains_secret(str(row.get("text") or "")):
        return False
    # A8 serve-time gate: a fact carrying governed WITHHELD/ERASED bytes is
    # suppressed on EVERY read lane (operator listing AND model-context
    # recall) — the crash window between a failed sweep step and its resume
    # never discloses. Live-store failure fails closed.
    from core.finalization import writer_may_persist_text

    if not writer_may_persist_text(str(row.get("text") or "")):
        return False
    if not _memory_row_allowed(row, access_policy=access_policy):
        return False
    scope = str(row.get("scope") or "").strip().lower()
    if scope == "chat":
        origin_chat = str(
            row.get("origin_chat_id") or row.get("session_id") or ""
        ).strip()
        return origin_chat == access_policy.chat_id
    return scope in {"project", "user_profile"}


_LEGACY_NAME_RE = re.compile(
    r"(?<![\w/\\-])(?<![A-Za-z0-9_]\.)VOOL(?!\.[A-Za-z0-9_])(?![\w/\\-])",
    re.IGNORECASE,
)
# Capturing split: odd-indexed parts are the code spans themselves, and are passed through verbatim.
_CODE_SPAN_RE = re.compile(r"(`[^`\n]*`)")


def _canonical_agent_name_in_memory(text: str) -> str:
    """Rewrite the assistant's LEGACY name in recalled memory to the runtime's canonical one.

    MEMORY.md written before the VOOL rename still asserts "# VOOL Persistent Memory" and
    "**My name**: VOOL" as stored facts, and the model repeats them verbatim -- it introduced itself
    as VOOL even after the system prompt, persona row, self-knowledge doc and an explicit override
    line all said VOOL. Instructing a small local model to disregard an explicit memory block does not
    work; the contradiction has to be gone before it is read.

    Applied HERE, at the single read point, so every consumer is covered -- the bootstrap context and
    the separate memory-prompt builder both inject this text. The file on disk is never rewritten:
    this is the user's data, it needs no migration, and a name they deliberately set is preserved
    (only the legacy default is mapped).
    """
    body = str(text or "")
    if not body:
        return body
    try:
        from core.onboarding import get_agent_display_name

        canonical = str(get_agent_display_name() or "").strip()
    except Exception:
        return body
    if not canonical or canonical.upper() == "VOOL":
        return body
    # Only rewrite the standalone word. A stored memory row legitimately contains real paths and
    # identifiers -- `~/Desktop/vool-local-product`, `vool_runtime/...`, `vool.api` -- and turning
    # those into VOOL would hand the model a path that does not exist, which is worse than the wrong
    # name. A trailing dot only blocks the rewrite when it joins an identifier (`vool.api`), so a
    # sentence ending in the name is still corrected.
    #
    # Backticked spans are left alone wholesale: the CLI binary is still named `vool`, so
    # `vool resolve <name>.null` is a command the operator can paste, and rewriting it produces one
    # that does not exist. Prose outside the span is still corrected.
    return "".join(
        part if index % 2 else _LEGACY_NAME_RE.sub(canonical, part)
        for index, part in enumerate(_CODE_SPAN_RE.split(body))
    )


# Public alias: other memory modules build prompt text from stored rows too and must apply the
# same mapping rather than re-implementing it.
canonical_agent_name_in_memory = _canonical_agent_name_in_memory


def _governed_excerpt_rows() -> list[dict[str, Any]]:
    """Governed rows eligible to render as model-visible memory.

    The excerpt is the raw-injection lane the bootstrap assembles into the
    ``runtime_memory`` context item on private lanes, so it takes the same
    fences every other serving path takes: secret-screened, A8
    ERASE/WITHHOLD-gated, and — because erased rows carry no text at all —
    structurally unable to yield forgotten content.
    """
    from core.finalization import writer_may_persist_text

    rows = [
        row
        for row in load_jsonl(memory_entries_path())
        if _row_is_active_memory(row)
        and not contains_secret(str(row.get("text") or ""))
        and _memory_expiry_is_current(row)
        and writer_may_persist_text(str(row.get("text") or ""))
    ]
    rows.sort(
        key=lambda row: (
            str(row.get("created_at") or ""),
            str(row.get("record_id") or ""),
        )
    )
    return rows


def load_memory_excerpt(*, max_chars: int = 2200) -> str:
    """Render the model-visible memory projection from GOVERNED entries.

    P0 erasure law: MEMORY.md is a human-readable projection of the canonical
    store and is never trusted as model context. A corrupt, stale, restored
    or hand-edited mirror therefore cannot inject anything into a prompt —
    only governed rows render, and a forgotten fact has no row left to
    render. (Owner identity and the privacy pact ride the bootstrap from the
    identity authority, not from this file.)
    """
    _ensure_memory_files()
    try:
        from core.onboarding import get_agent_display_name

        agent_name = str(get_agent_display_name() or "").strip() or "VOOL"
    except Exception:
        agent_name = "VOOL"
    lines = [
        f"- [{str(row.get('created_at') or '')[:10]}] "
        f"{_canonical_agent_name_in_memory(str(row.get('text') or '').strip())}"
        for row in _governed_excerpt_rows()
        if str(row.get("text") or "").strip()
    ]
    text = (
        f"# {agent_name} Persistent Memory\n\n"
        f"{_LEARNED_KNOWLEDGE_MARKER}\n\n" + "\n".join(lines)
    ).strip()
    if len(text) <= max_chars:
        return text
    head_chars = max(160, min(max_chars // 2, 800))
    tail_chars = max(160, max_chars - head_chars - 7)
    return text[:head_chars].rstrip() + "\n...\n" + text[-tail_chars:].lstrip()


def add_memory_fact(
    fact: str,
    *,
    category: str = "fact",
    session_id: str | None = None,
    source: str = "manual",
    confidence: float = 0.85,
    keywords: list[str] | None = None,
    share_scope: str | None = None,
    project_id: str | None = None,
    scope: str = "chat",
    authority: str = "confirmed_memory",
    fact_key: str | None = None,
    expires_at: str | None = None,
    review_after: str | None = None,
    access_policy: ContextAccessPolicy | None = None,
) -> bool:
    _ensure_memory_files()
    clean = sanitize_fact(fact)
    if not clean or contains_secret(clean):
        return False
    clean_session = str(
        session_id
        or (access_policy.chat_id if access_policy is not None else "")
    ).strip()
    clean_scope = str(scope or "").strip().lower()
    if not clean_session or clean_scope not in {
        "chat",
        "project",
        "user_profile",
    }:
        return False
    try:
        resolved_policy = resolve_memory_access_policy(
            access_policy=access_policy,
            chat_id=clean_session,
        )
    except (TypeError, ValueError):
        return False
    clean_project = str(
        project_id or resolved_policy.project_id or ""
    ).strip()
    if not _memory_write_allowed(
        access_policy=resolved_policy,
        scope=clean_scope,
        project_id=clean_project,
    ):
        return False
    normalized_share_scope = normalize_share_scope(
        share_scope
        or session_memory_policy(clean_session).get("share_scope"),
        default="local_only",
    )

    recorded = record_memory_entry(
        clean,
        category=category,
        session_id=clean_session,
        source=source,
        confidence=confidence,
        keywords=keywords,
        share_scope=normalized_share_scope,
        project_id=clean_project,
        scope=clean_scope,
        authority=authority,
        fact_key=fact_key,
        expires_at=expires_at,
        review_after=review_after,
        access_policy=resolved_policy,
    )
    if not recorded:
        return False

    # MEMORY.md is a generated projection of the canonical store (P0 erasure
    # law): refreshed from governed rows under the same lock, each line bound
    # to its record id, so a forget erases by identity and no mirror line can
    # outlive its row. It remains a human-readable audit surface only — never
    # eligible for provider context without structured provenance.
    with _MEMORY_ENTRY_LOCK:
        _rewrite_memory_mirror_locked()
    return True


def forget_memory(
    keyword: str,
    *,
    access_policy: ContextAccessPolicy | None = None,
    chat_id: str | None = None,
) -> int:
    _ensure_memory_files()
    token = keyword.strip().lower()
    if not token or contains_secret(token):
        return 0
    policy = resolve_memory_access_policy(
        access_policy=access_policy,
        chat_id=chat_id,
    )
    return forget_structured_memory(
        token,
        access_policy=policy,
    )


def forget_memory_record(record_id: str) -> bool:
    """Remove exactly ONE stored entry by its record id. True when a row was removed.

    The per-row "Forget" affordance needs precision the keyword path cannot give: forgetting by
    keyword removes every match, and a UI button that silently deletes MORE than the row under
    the cursor is a trust defect. Id-scoped, lock-held, rewrite-on-change — the same persistence
    discipline as the keyword path — and, since the P0 erasure law, the row becomes a durable
    tombstone, the mirror projection is rewritten, and success returns only after every
    model-visible path re-reads without the entry.
    """
    wanted = str(record_id or "").strip()
    if not wanted:
        return False
    _ensure_memory_files()
    with _MEMORY_ENTRY_LOCK:
        erased_texts = _erase_rows_locked([wanted])
        if not erased_texts:
            return False
        _rewrite_memory_mirror_locked()
    _verify_erasure(erased_texts, [wanted])
    return True


def sweep_governed_memory_rows(request_id: str) -> bool:
    """A8 derivative sweep for the canonical store — through the authority.

    The finalization erasure traversal removes memory rows stamped with an
    erased request lineage. That removal crosses the same seams every other
    mutation of ``memory_entries.jsonl`` crosses: the entry lock (so it can
    never interleave with a concurrent record/forget read-modify-write and
    silently drop it) and the MEMORY.md projection refresh (so no bound
    mirror line outlives a swept row). Returns True when a row was removed.
    """
    clean_request_id = str(request_id or "").strip()
    if not clean_request_id:
        return False
    _ensure_memory_files()
    with _MEMORY_ENTRY_LOCK:
        rows = load_jsonl(memory_entries_path())
        kept = [
            row
            for row in rows
            if str(row.get("request_id") or "") != clean_request_id
        ]
        if len(kept) == len(rows):
            return False
        rewrite_jsonl(memory_entries_path(), kept)
        _rewrite_memory_mirror_locked()
        return True


def list_memory_entries(
    *,
    access_policy: ContextAccessPolicy | None = None,
    chat_id: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    _ensure_memory_files()
    policy = resolve_memory_access_policy(
        access_policy=access_policy,
        chat_id=chat_id,
    )
    rows = [
        dict(row)
        for row in load_jsonl(memory_entries_path())
        if _memory_command_row_allowed(
            row,
            access_policy=policy,
        )
    ]
    rows.sort(
        key=lambda row: (
            str(row.get("created_at") or ""),
            str(row.get("record_id") or ""),
        )
    )
    return rows[-max(1, int(limit)) :]


def summarize_memory(
    *,
    access_policy: ContextAccessPolicy | None = None,
    chat_id: str | None = None,
    limit: int = 10,
) -> list[str]:
    return [
        _canonical_agent_name_in_memory(str(row.get("text") or "").strip())
        for row in list_memory_entries(
            access_policy=access_policy,
            chat_id=chat_id,
            limit=limit,
        )
        if str(row.get("text") or "").strip()
    ]


def recent_conversation_events(
    session_id: str, *, limit: int = 6, include_artifacts: bool = False
) -> list[dict[str, Any]]:
    """This chat's most recent conversation rows, oldest-first.

    Artifact rows (``artifact_kind`` set — an assistant-authored card such as a council
    verdict, written by ``append_assistant_artifact_event``) are EXCLUDED by default,
    because every caller of this function but one is a recall or extraction path feeding
    the model or the memory pipeline, and an artifact is not something the operator said.
    Excluding at the read side means the rule holds for readers written later without
    each of them having to know about it. The transcript reader — ``/api/chat/history``,
    which paints the chat log — asks for them explicitly.
    """
    _ensure_memory_files()
    rows = load_jsonl(conversation_log_path())
    out: list[dict[str, Any]] = []
    for row in reversed(rows):
        if str(row.get("session_id") or "") != str(session_id):
            continue
        if not include_artifacts and str(row.get("artifact_kind") or "").strip():
            continue
        out.append(row)
        if len(out) >= max(1, int(limit)):
            break
    return list(reversed(out))


# One process, POSTs served on a threadpool — serialize the whole read-modify-write of the meta file
# so two simultaneous rename/archive actions can't clobber each other (last-writer-wins data loss).
_SESSION_META_LOCK = threading.Lock()
_SESSION_TITLE_MAX = 200
_SESSION_EMOJI_MAX = 8   # a couple of emoji code points (some emoji are multi-codepoint), never a long string
_SESSION_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")   # strict #rrggbb only — never arbitrary CSS


def _parse_meta_dict(path: Path) -> dict[str, dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8", errors="replace") or "{}")
    if not isinstance(data, dict):
        raise ValueError("session meta is not an object")
    return {str(sid): meta for sid, meta in data.items() if isinstance(meta, dict)}


def load_session_meta() -> dict[str, dict[str, Any]]:
    """User-set per-session overrides for the /chat sidebar: {session_id: {title?, archived?}}.

    Stored in a small JSON file alongside the conversation log. A corrupt/partial file is NOT silently
    discarded: it is preserved as ``<name>.corrupt-<ts>`` for diagnosis and recovered from the last-good
    ``<name>.bak`` when possible; only if no valid copy exists do we fall back to an empty dict.
    """
    path = chat_session_meta_path()
    if not path.exists():
        return {}
    try:
        return _parse_meta_dict(path)
    except (json.JSONDecodeError, ValueError, OSError):
        pass
    # Preserve the corrupt file, then try the backup.
    with contextlib.suppress(OSError):
        path.replace(path.with_name(path.name + f".corrupt-{int(time.time())}"))
    bak = path.with_name(path.name + ".bak")
    if bak.exists():
        try:
            recovered = _parse_meta_dict(bak)
            with contextlib.suppress(OSError):
                path.write_text(json.dumps(recovered, ensure_ascii=False), encoding="utf-8")
            return recovered
        except (json.JSONDecodeError, ValueError, OSError):
            return {}
    return {}


def set_session_meta(
    session_id: str, *, title: str | None = None, archived: bool | None = None,
    project_id: str | None = None, emoji: str | None = None, color: str | None = None,
) -> dict[str, Any]:
    """Set a custom title, archived flag, project binding, emoji marker, and/or accent colour for one session.

    Only the passed fields change. An empty/whitespace title clears the override (falls back to the
    first user message); an empty ``project_id`` unbinds the session from any project; an empty
    ``emoji`` clears the marker; an empty/invalid ``color`` clears the accent (stored as ``#rrggbb`` or
    ``""``). Metadata only — never touches the transcript. The whole read-modify-write runs under a
    process lock; the write is atomic (temp + fsync + os.replace) and keeps a last-good ``.bak``.
    Returns the entry.
    """
    sid = str(session_id or "").strip()
    if not sid:
        return {}
    if load_chat_namespace(sid) is None:
        ensure_chat_namespace(sid)
    with _SESSION_META_LOCK:
        meta = load_session_meta()
        entry = dict(meta.get(sid) or {})
        if title is not None:
            clean_title = " ".join(str(title).split()).strip()[:_SESSION_TITLE_MAX]
            if clean_title:
                entry["title"] = clean_title
            else:
                entry.pop("title", None)
        if emoji is not None:
            clean_emoji = "".join(str(emoji).split())[:_SESSION_EMOJI_MAX]   # strip whitespace, cap length
            if clean_emoji:
                entry["emoji"] = clean_emoji
            else:
                entry.pop("emoji", None)   # empty -> clear the marker
        if color is not None:
            clean_color = str(color).strip().lower()
            if _SESSION_HEX_COLOR.match(clean_color):
                entry["color"] = clean_color
            else:
                entry.pop("color", None)   # empty/invalid -> clear the accent
        if archived is not None:
            entry["archived"] = bool(archived)
        if project_id is not None:
            clean_project = str(project_id).strip()
            if clean_project:
                entry["project_id"] = clean_project
            else:
                entry.pop("project_id", None)  # empty -> unbind
        meta[sid] = entry
        path = chat_session_meta_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(meta, ensure_ascii=False)
        tmp = path.with_name(path.name + ".tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        with contextlib.suppress(OSError):  # last-good backup for corrupt-recovery on the next load
            path.with_name(path.name + ".bak").write_text(payload, encoding="utf-8")
        if archived is not None:
            from core.context_namespace import set_chat_namespace_state

            set_chat_namespace_state(
                sid,
                "archived" if archived else "active",
            )
        if project_id is not None:
            # The namespace carries a project_id too, and list_conversation_sessions falls back to
            # it when the meta entry has none. Writing only the meta therefore did not unbind
            # anything: clearing the meta left the namespace binding, the fallback re-resolved the
            # chat to the same project, and because a project id is derived from its folder path,
            # deleting a project and recreating it on that folder handed every chat straight back.
            # Measured on the reporting runtime: 61 chats showed under a freshly recreated project,
            # 60 of them bound by the namespace alone. Both stores move together now.
            from core.context_namespace import set_chat_namespace_project

            with contextlib.suppress(ValueError):  # no namespace, or not active -> nothing to bind
                set_chat_namespace_project(sid, str(project_id).strip())
        return entry


def list_conversation_sessions(*, limit: int = 60) -> list[dict[str, Any]]:
    """Distinct chat sessions from the conversation log, newest-active first.

    Backs the /chat sidebar. Each entry is {session_id, title, created_at, updated_at, turn_count,
    archived}. ``title`` is a user-set override if present, else the first user message of the session
    (the log is append-ordered); ``updated_at`` its most recent turn. The log is secret-redacted and
    size-trimmed at write time, so very old sessions can age out — this reflects what is still
    persisted, not an all-time history.
    """
    _ensure_memory_files()
    rows = load_jsonl(conversation_log_path())
    meta = load_session_meta()
    by_session: dict[str, dict[str, Any]] = {}
    from core.context_namespace import list_chat_namespaces

    # One request-local snapshot, including tombstones. Reopening SQLite for
    # every transcript row repeatedly parses the entire schema on a real store.
    namespaces = {n.chat_id: n for n in list_chat_namespaces(include_deleted=True, limit=None)}
    for namespace in namespaces.values():
        if namespace.lifecycle_state == "deleted":
            continue
        by_session[namespace.chat_id] = {
            "session_id": namespace.chat_id,
            "title": "",
            "created_at": namespace.created_at,
            "updated_at": namespace.updated_at,
            "turn_count": 0,
            "project_id": namespace.project_id,
            "archived": namespace.lifecycle_state == "archived",
        }
    for row in rows:
        session_id = str(row.get("session_id") or "").strip()
        if not session_id:
            continue
        namespace = namespaces.get(session_id)
        if namespace is not None and namespace.lifecycle_state == "deleted":
            continue
        user_text = str(row.get("user") or "").strip()
        ts = str(row.get("ts") or "")
        entry = by_session.get(session_id)
        if entry is None or int(entry.get("turn_count") or 0) == 0:
            created_at = str(entry.get("created_at") or ts) if entry else ts
            project_id = str(entry.get("project_id") or "") if entry else ""
            archived = bool(entry.get("archived")) if entry else False
            by_session[session_id] = {
                "session_id": session_id,
                "title": user_text[:80],
                "created_at": created_at,
                "updated_at": ts,
                "turn_count": 1,
                "project_id": project_id,
                "archived": archived,
            }
            continue
        entry["turn_count"] = int(entry.get("turn_count") or 0) + 1
        if ts and ts >= str(entry.get("updated_at") or ""):
            entry["updated_at"] = ts
        if not entry.get("title") and user_text:
            entry["title"] = user_text[:80]
    for entry in by_session.values():
        override = meta.get(entry["session_id"]) or {}
        namespace = namespaces.get(entry["session_id"])
        custom_title = str(override.get("title") or "").strip()
        if custom_title:
            entry["title"] = custom_title
        elif not str(entry.get("title") or "").strip():
            entry["title"] = "New chat"
        entry["archived"] = bool(
            override.get("archived")
            or (
                namespace is not None
                and namespace.lifecycle_state == "archived"
            )
        )
        entry["project_id"] = str(
            override.get("project_id")
            or (namespace.project_id if namespace is not None else "")
            or entry.get("project_id")
            or ""
        )
        entry["emoji"] = str(override.get("emoji") or "")
        entry["color"] = str(override.get("color") or "")
    # A chat bound to a project (or renamed) BEFORE its first message has meta but no transcript yet —
    # surface it so it appears in the sidebar (under its project) immediately, not only after turn one.
    for raw_sid, override in meta.items():
        sid = str(raw_sid).strip()
        if not sid or sid in by_session:
            continue
        namespace = namespaces.get(sid)
        if namespace is not None and namespace.lifecycle_state == "deleted":
            continue
        title = str((override or {}).get("title") or "").strip()
        project_id = str((override or {}).get("project_id") or "").strip()
        emoji = str((override or {}).get("emoji") or "").strip()
        color = str((override or {}).get("color") or "").strip()
        if not (title or project_id or emoji or color):
            continue
        by_session[sid] = {
            "session_id": sid,
            "title": title or "New chat",
            "created_at": "",
            "updated_at": "",
            "turn_count": 0,
            "archived": bool(
                (override or {}).get("archived")
                or (
                    namespace is not None
                    and namespace.lifecycle_state == "archived"
                )
            ),
            "project_id": project_id,
            "emoji": emoji,
            "color": color,
        }
    sessions = sorted(by_session.values(), key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    return sessions[: max(1, int(limit))]


def search_relevant_memory(
    query_text: str,
    *,
    access_policy: ContextAccessPolicy,
    topic_hints: list[str] | None = None,
    limit: int = 4,
) -> list[dict[str, Any]]:
    try:
        policy = resolve_memory_access_policy(access_policy=access_policy)
    except (TypeError, ValueError):
        return []
    query_tokens = set(keyword_tokens_filtered(" ".join([query_text, *(topic_hints or [])])))
    # Scope is applied while reading the store, before semantic scoring,
    # temporal ordering, or any other retrieval reinforcement.
    rows = [
        row
        for row in combined_memory_entries()
        if _memory_row_allowed(row, access_policy=policy)
    ]
    ranked: list[tuple[float, dict[str, Any]]] = []
    for row in rows:
        item_tokens = set(row.get("keywords") or keyword_tokens_filtered(str(row.get("text") or "")))
        overlap = len(query_tokens & item_tokens) if query_tokens else 0
        if query_tokens and overlap <= 0:
            continue
        semantic = overlap / max(1, len(query_tokens)) if query_tokens else 0.0
        recency = recency_score(str(row.get("created_at") or ""))
        category_boost = 0.08 if str(row.get("category") or "") in {"instruction", "preference"} else 0.0
        confidence = max(0.2, min(1.0, float(row.get("confidence") or 0.7)))
        score = (0.55 * semantic) + (0.20 * confidence) + (0.17 * recency) + category_boost
        ranked.append((score, {**row, "score": round(score, 4)}))
    ranked.sort(key=lambda item: (item[0], str(item[1].get("created_at") or "")), reverse=True)
    # Same read-point rule as load_memory_excerpt: a stored row saying "**My name**: VOOL" is the
    # user's data and stays on disk, but it must not reach the model as a competing fact.
    return [
        {**row, "text": _canonical_agent_name_in_memory(str(row.get("text") or ""))}
        for _, row in ranked[: max(1, int(limit))]
    ]


_SAFE_DISPUTE_IDENTIFIER_RE = re.compile(
    r"^[A-Za-z][A-Za-z0-9_.:@/-]{0,127}$"
)


def find_disputed_memory_facts(
    query_text: str,
    *,
    access_policy: ContextAccessPolicy,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Return safe descriptors for unresolved durable-memory conflicts.

    The persisted namespace and grants are reloaded before reading the durable
    store. Scope, lifecycle, provenance, expiry, and secret checks happen before
    query relevance is calculated. Raw fact values are never returned.
    """
    clean_query = " ".join(str(query_text or "").split()).strip()
    if not clean_query or contains_secret(clean_query):
        return []
    try:
        policy = resolve_memory_access_policy(access_policy=access_policy)
    except (TypeError, ValueError):
        return []

    query_tokens = set(keyword_tokens_filtered(clean_query))
    if not query_tokens:
        return []

    grouped: dict[
        tuple[str, str, str],
        list[dict[str, Any]],
    ] = {}
    for row in load_jsonl(memory_entries_path()):
        if not _memory_dispute_row_allowed(
            row,
            access_policy=policy,
        ):
            continue
        fact_key = str(row.get("fact_key") or "").strip()
        scope = str(row.get("scope") or "").strip().lower()
        scope_identity = _memory_scope_identity(row)
        grouped.setdefault(
            (fact_key, scope, scope_identity),
            [],
        ).append(row)

    ranked: list[tuple[float, str, dict[str, Any]]] = []
    for (fact_key, scope, _scope_identity), rows in grouped.items():
        if any(
            str(row.get("status") or "").strip().lower() == "active"
            for row in rows
        ):
            continue
        disputed_by_record: dict[str, dict[str, Any]] = {}
        for row in rows:
            if str(row.get("status") or "").strip().lower() != "disputed":
                continue
            record_identity = str(row.get("record_id") or "").strip()
            if not record_identity:
                continue
            disputed_by_record.setdefault(record_identity, row)
        disputed = list(disputed_by_record.values())
        content_hashes = {
            _memory_content_hash(row)
            for row in disputed
        }
        if len(disputed) < 2 or len(content_hashes) < 2:
            continue

        candidate_tokens = set(keyword_tokens_filtered(fact_key))
        for row in disputed:
            candidate_tokens.update(
                str(token).strip().lower()
                for token in row.get("keywords") or []
                if str(token).strip()
            )
            candidate_tokens.update(
                keyword_tokens_filtered(str(row.get("text") or ""))
            )
        overlap = len(query_tokens & candidate_tokens)
        if overlap <= 0:
            continue

        descriptor = _memory_dispute_descriptor(
            fact_key=fact_key,
            scope=scope,
            rows=disputed,
            content_hashes=content_hashes,
        )
        score = overlap / max(1, len(query_tokens))
        ranked.append(
            (
                score,
                str(descriptor["fact_key"]),
                descriptor,
            )
        )

    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [
        descriptor
        for _score, _fact_key, descriptor in ranked[: max(1, int(limit))]
    ]


def _memory_dispute_row_allowed(
    row: dict[str, Any],
    *,
    access_policy: ContextAccessPolicy,
) -> bool:
    fact_key = str(row.get("fact_key") or "").strip()
    record_id = str(row.get("record_id") or "").strip()
    scope = str(row.get("scope") or "").strip().lower()
    status = str(row.get("status") or "").strip().lower()
    source = str(row.get("source") or "").strip()
    text = str(row.get("text") or "")
    origin_chat = str(
        row.get("origin_chat_id") or row.get("session_id") or ""
    ).strip()
    origin_project = str(
        row.get("origin_project_id") or row.get("project_id") or ""
    ).strip()
    provenance = row.get("provenance")
    if (
        not fact_key
        or not record_id
        or scope not in {"chat", "project", "user_profile"}
        or status not in {"active", "disputed"}
        or not source
        or not text.strip()
        or contains_secret(text)
        or contains_secret(fact_key)
        or not origin_chat
        or not isinstance(provenance, dict)
    ):
        return False
    if (
        not str(provenance.get("kind") or "").strip()
        or not str(provenance.get("recorded_at") or "").strip()
        or str(provenance.get("origin_chat_id") or "").strip()
        != origin_chat
        or str(provenance.get("origin_project_id") or "").strip()
        != origin_project
    ):
        return False

    origin_namespace = load_chat_namespace(origin_chat)
    if (
        origin_namespace is None
        or origin_namespace.lifecycle_state != "active"
    ):
        return False
    if scope == "project" and (
        not origin_project
        or origin_namespace.project_id != origin_project
    ):
        return False
    if not _memory_expiry_is_current(row):
        return False

    metadata = {
        "scope": scope,
        "source": source,
        "source_id": str(row.get("source_id") or "").strip(),
        # Scope authorization is independent of the deliberately quarantined
        # disputed status. The status itself was checked above.
        "status": "active",
        "origin_chat_id": origin_chat,
        "origin_project_id": origin_project,
        "provenance": provenance,
    }
    allowed, _reason = access_policy.allows_metadata(
        source_type="runtime_memory",
        metadata=metadata,
    )
    return allowed


def _memory_expiry_is_current(row: dict[str, Any]) -> bool:
    expires_at = str(row.get("expires_at") or "").strip()
    if not expires_at:
        return True
    try:
        expiry = datetime.fromisoformat(expires_at)
    except ValueError:
        return False
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    return (
        expiry.astimezone(timezone.utc)
        > datetime.now(timezone.utc)
    )


def _memory_content_hash(row: dict[str, Any]) -> str:
    return hashlib.sha256(
        str(row.get("text") or "").encode("utf-8")
    ).hexdigest()


def _safe_dispute_identifier(value: Any, *, prefix: str) -> str:
    clean = str(value or "").strip()
    if (
        _SAFE_DISPUTE_IDENTIFIER_RE.fullmatch(clean)
        and not contains_secret(clean)
    ):
        return clean
    digest = hashlib.sha256(clean.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}:{digest}"


def _memory_dispute_descriptor(
    *,
    fact_key: str,
    scope: str,
    rows: list[dict[str, Any]],
    content_hashes: set[str],
) -> dict[str, Any]:
    record_ids = sorted(
        {
            _safe_dispute_identifier(
                row.get("record_id"),
                prefix="record",
            )
            for row in rows
        }
    )
    origin_chat_ids = sorted(
        {
            _safe_dispute_identifier(
                row.get("origin_chat_id") or row.get("session_id"),
                prefix="chat",
            )
            for row in rows
        }
    )
    provenance_hashes = sorted(
        hashlib.sha256(
            json.dumps(
                row.get("provenance"),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
        ).hexdigest()
        for row in rows
    )
    descriptor: dict[str, Any] = {
        "fact_key": _safe_dispute_identifier(
            fact_key,
            prefix="fact-key",
        ),
        "scope": scope,
        "record_count": len(rows),
        "record_ids": record_ids,
        "content_hashes": sorted(content_hashes),
        "provenance_hash": hashlib.sha256(
            "\0".join(provenance_hashes).encode("ascii")
        ).hexdigest(),
    }
    if scope == "chat" and len(origin_chat_ids) == 1:
        descriptor["origin_chat_id"] = origin_chat_ids[0]
    else:
        descriptor["origin_chat_ids"] = origin_chat_ids
    if scope == "project":
        descriptor["origin_project_id"] = _safe_dispute_identifier(
            rows[0].get("origin_project_id")
            or rows[0].get("project_id"),
            prefix="project",
        )
    return descriptor


def search_session_summaries(
    query_text: str,
    *,
    access_policy: ContextAccessPolicy,
    topic_hints: list[str] | None = None,
    limit: int = 3,
    exclude_session_id: str | None = None,
) -> list[dict[str, Any]]:
    try:
        policy = resolve_memory_access_policy(access_policy=access_policy)
    except (TypeError, ValueError):
        return []
    query_tokens = set(keyword_tokens_filtered(" ".join([query_text, *(topic_hints or [])])))
    archived_sessions = {
        str(sid)
        for sid, meta in load_session_meta().items()
        if isinstance(meta, dict) and bool(meta.get("archived"))
    }
    latest_by_session: dict[str, dict[str, Any]] = {}
    for row in load_jsonl(session_summaries_path()):
        session_id = str(row.get("session_id") or "").strip()
        if not session_id:
            continue
        previous = latest_by_session.get(session_id)
        if previous is None or str(row.get("created_at") or "") > str(previous.get("created_at") or ""):
            latest_by_session[session_id] = row

    ranked: list[tuple[float, dict[str, Any]]] = []
    for row in latest_by_session.values():
        row_session_id = str(row.get("session_id") or "")
        if exclude_session_id and row_session_id == exclude_session_id:
            continue
        if bool(row.get("archived")) or row_session_id in archived_sessions:
            continue
        namespace = load_chat_namespace(row_session_id)
        if namespace is not None and namespace.lifecycle_state != "active":
            continue
        metadata = {
            "scope": "chat",
            "source": "session_summary",
            "status": str(row.get("status") or "active"),
            "origin_chat_id": row_session_id,
            "origin_project_id": str(row.get("project_id") or "").strip(),
            "provenance": row.get("provenance"),
        }
        allowed, _reason = policy.allows_metadata(
            source_type="session_summary",
            metadata=metadata,
        )
        if not allowed:
            continue
        item_tokens = set(row.get("keywords") or keyword_tokens_filtered(str(row.get("summary") or "")))
        overlap = len(query_tokens & item_tokens) if query_tokens else 0
        if query_tokens and overlap <= 0:
            continue
        semantic = overlap / max(1, len(query_tokens)) if query_tokens else 0.0
        recency = recency_score(str(row.get("created_at") or ""))
        score = (0.60 * semantic) + (0.25 * recency) + 0.15 * 0.68
        ranked.append((score, {**row, "score": round(score, 4)}))
    ranked.sort(key=lambda item: (item[0], str(item[1].get("created_at") or "")), reverse=True)
    # Summaries written before the rename quote the assistant's own prior replies back at it
    # ("Last assistant outcome: I'm VOOL") -- a self-reinforcing loop that survives any system-prompt
    # override. Neutralise on read; already-stored rows need no migration.
    return [
        {**row, "summary": _canonical_agent_name_in_memory(str(row.get("summary") or ""))}
        for _, row in ranked[: max(1, int(limit))]
    ]


def search_user_heuristics(
    query_text: str,
    *,
    access_policy: ContextAccessPolicy,
    topic_hints: list[str] | None = None,
    limit: int = 4,
) -> list[dict[str, Any]]:
    try:
        policy = resolve_memory_access_policy(access_policy=access_policy)
    except (TypeError, ValueError):
        return []
    if not policy.allow_user_profile_context:
        return []
    query_tokens = set(keyword_tokens_filtered(" ".join([query_text, *(topic_hints or [])])))
    ranked: list[tuple[float, dict[str, Any]]] = []
    for row in load_jsonl(user_heuristics_path()):
        metadata = {
            "scope": "user_profile",
            "source": "user_heuristic",
            "source_id": str(row.get("source_id") or "profile:confirmed"),
            "status": str(row.get("status") or ""),
            "origin_chat_id": str(row.get("origin_chat_id") or row.get("session_id") or ""),
            "origin_project_id": str(row.get("origin_project_id") or row.get("project_id") or ""),
            "provenance": row.get("provenance"),
        }
        allowed, _reason = policy.allows_metadata(
            source_type="user_heuristic",
            metadata=metadata,
        )
        if not allowed or not isinstance(row.get("provenance"), dict):
            continue
        category = str(row.get("category") or "").strip().lower()
        item_tokens = set(row.get("keywords") or keyword_tokens_filtered(str(row.get("text") or "")))
        overlap = len(query_tokens & item_tokens) if query_tokens else 0
        semantic = overlap / max(1, len(query_tokens)) if query_tokens else 0.0
        if query_tokens and overlap <= 0 and category not in _HEURISTIC_ALWAYS_INCLUDE:
            continue
        mentions = max(1, int(row.get("mentions") or 1))
        strength = min(1.0, mentions / 3.0)
        recency = recency_score(str(row.get("updated_at") or row.get("created_at") or ""))
        confidence = max(0.35, min(1.0, float(row.get("confidence") or 0.7)))
        category_boost = 0.10 if category in _HEURISTIC_ALWAYS_INCLUDE else 0.0
        score = (0.48 * semantic) + (0.18 * strength) + (0.16 * confidence) + (0.12 * recency) + category_boost
        ranked.append((score, {**row, "score": round(score, 4)}))
    ranked.sort(
        key=lambda item: (
            item[0],
            int(item[1].get("mentions") or 0),
            str(item[1].get("updated_at") or ""),
        ),
        reverse=True,
    )
    return [row for _, row in ranked[: max(1, int(limit))]]


def sanitize_fact(text: str) -> str:
    clean = " ".join(str(text or "").split()).strip()
    if not clean:
        return ""
    return clean[:320]


def derive_fact_key(
    text: str,
    *,
    category: str,
    keywords: list[str] | None = None,
) -> str:
    clean = " ".join(str(text or "").lower().split()).strip(" .,:;!?")
    if str(category or "").strip().lower() == "preference" and re.search(
        r"\bprefer\b",
        clean,
    ):
        return "preference"
    clean = re.sub(
        r"^(?:actually|correction|correct that|update|updated|now)[,:]?\s*",
        "",
        clean,
    )
    match = re.search(
        r"^(?P<subject>.+?)\s+(?:is|are|was|were|equals?|=|"
        r"changed to|updated to|is now)\s+.+$",
        clean,
    )
    if match:
        subject = re.sub(
            r"^(?:my|our|the|a|an|operator(?:'s)?)\s+",
            "",
            match.group("subject").strip(),
        )
        subject = re.sub(r"[^a-z0-9_. -]+", "", subject)
        subject = " ".join(subject.split())[:100]
        if subject:
            return f"{str(category or 'fact').lower()}:{subject}"
    normalized_keywords = list(keywords or keyword_tokens_filtered(clean))[:4]
    return (
        f"{str(category or 'fact').strip().lower()}:"
        + ":".join(normalized_keywords)
    )


def is_user_correction(text: str) -> bool:
    return bool(_CORRECTION_RE.search(str(text or "")))


def record_memory_entry(
    text: str,
    *,
    category: str,
    session_id: str,
    source: str,
    confidence: float,
    keywords: list[str] | None,
    share_scope: str | None,
    project_id: str | None = None,
    scope: str,
    authority: str,
    fact_key: str | None,
    expires_at: str | None,
    review_after: str | None,
    access_policy: ContextAccessPolicy | None = None,
) -> bool:
    _ensure_memory_files()
    clean = sanitize_fact(text)
    if not clean:
        return False
    clean_session = str(session_id or "").strip()
    clean_project = str(project_id or "").strip()
    clean_scope = str(scope or "").strip().lower()
    clean_authority = str(authority or "").strip().lower()
    if (
        contains_secret(clean)
        or not clean_session
        or clean_scope not in {"chat", "project", "user_profile"}
        or clean_authority
        not in {
            "user_correction",
            "verified_action",
            "verified_external",
            "confirmed_memory",
            "model_inference",
        }
    ):
        return False
    # A8 write fence (ERASE-dominance): governed WITHHELD/ERASED bytes are
    # refused at the durable boundary — memory can never resurrect them.
    from core.finalization import writer_may_persist_text

    if not writer_may_persist_text(clean):
        return False
    try:
        resolved_policy = resolve_memory_access_policy(
            access_policy=access_policy,
            chat_id=clean_session,
        )
    except (TypeError, ValueError):
        return False
    if not _memory_write_allowed(
        access_policy=resolved_policy,
        scope=clean_scope,
        project_id=clean_project,
    ):
        return False
    normalized = clean.lower()
    record_id = f"memory-{uuid.uuid4().hex}"
    created_at = utcnow()
    normalized_keywords = list(keywords or keyword_tokens_filtered(clean))[:16]
    clean_fact_key = str(fact_key or "").strip()
    if not clean_fact_key:
        clean_fact_key = derive_fact_key(
            clean,
            category=str(category or "fact"),
            keywords=normalized_keywords,
        )
    scope_identity = (
        clean_session
        if clean_scope == "chat"
        else clean_project
        if clean_scope == "project"
        else "profile:confirmed"
    )
    initial_status = (
        "disputed"
        if clean_authority == "model_inference"
        else "active"
    )
    payload = {
        "record_id": record_id,
        "created_at": created_at,
        # A8 pass-001 payload lineage: the governing request id at write time
        # ('' on legacy lanes — never fabricated).
        "request_id": _current_request_lineage(),
        "last_confirmed_at": created_at,
        "text": clean,
        "category": str(category or "fact"),
        "fact_key": clean_fact_key,
        "scope": clean_scope,
        "origin_chat_id": clean_session,
        "origin_project_id": clean_project,
        "session_id": clean_session,
        "project_id": clean_project,
        "source": str(source or "manual"),
        "source_id": (
            "profile:confirmed"
            if clean_scope == "user_profile"
            else record_id
        ),
        "status": initial_status,
        "authority": clean_authority,
        "superseded_record_id": "",
        "expires_at": str(expires_at or "").strip(),
        "review_after": str(review_after or "").strip(),
        "provenance": {
            "kind": str(source or "manual"),
            "source_id": (
                "profile:confirmed"
                if clean_scope == "user_profile"
                else record_id
            ),
            "content_hash": hashlib.sha256(clean.encode()).hexdigest(),
            "origin_chat_id": clean_session,
            "origin_project_id": clean_project,
            "recorded_at": created_at,
        },
        "confidence": max(0.2, min(1.0, float(confidence))),
        "keywords": normalized_keywords,
        "share_scope": normalize_share_scope(share_scope, default="local_only"),
    }
    with _MEMORY_ENTRY_LOCK:
        existing = load_jsonl(memory_entries_path())
        if any(
            str(row.get("text") or "").strip().lower() == normalized
            and str(row.get("fact_key") or "").strip() == clean_fact_key
            and _memory_scope_identity(row) == scope_identity
            and str(row.get("status") or "").strip().lower()
            in {"active", "disputed"}
            for row in reversed(existing[-400:])
        ):
            return False

        active_conflicts = [
            row
            for row in existing
            if str(row.get("fact_key") or "").strip() == clean_fact_key
            and _memory_scope_identity(row) == scope_identity
            and str(row.get("status") or "").strip().lower() == "active"
        ]
        if initial_status == "active" and active_conflicts:
            strongest_rank = max(
                _AUTHORITY_RANK.get(
                    str(row.get("authority") or "").strip().lower(),
                    0,
                )
                for row in active_conflicts
            )
            incoming_rank = _AUTHORITY_RANK[clean_authority]
            strongest = next(
                row
                for row in reversed(active_conflicts)
                if _AUTHORITY_RANK.get(
                    str(row.get("authority") or "").strip().lower(),
                    0,
                )
                == strongest_rank
            )
            if incoming_rank > strongest_rank:
                for row in active_conflicts:
                    row["status"] = "superseded"
                    row["superseded_record_id"] = record_id
            elif incoming_rank == strongest_rank:
                for row in active_conflicts:
                    row["status"] = "disputed"
                payload["status"] = "disputed"
            else:
                payload["status"] = "superseded"
                payload["superseded_record_id"] = str(
                    strongest.get("record_id") or ""
                )

        rewrite_jsonl(
            memory_entries_path(),
            [*existing, payload],
        )
        _trim_memory_entries_locked()
    return True


def _memory_scope_identity(row: dict[str, Any]) -> str:
    scope = str(row.get("scope") or "").strip().lower()
    if scope == "chat":
        return str(
            row.get("origin_chat_id") or row.get("session_id") or ""
        ).strip()
    if scope == "project":
        return str(
            row.get("origin_project_id") or row.get("project_id") or ""
        ).strip()
    if scope == "user_profile":
        return "profile:confirmed"
    return ""


_FORGET_SUBJECT_STRIP_RE = re.compile(
    r"^(?:forget|erase|remove|delete|drop)\b[\s,]*", re.IGNORECASE
)


def _forget_subject_fact_key(token: str) -> str:
    """The subject fact_key a forget TOKEN names, or "" when the token is a VALUE.

    Mirrors ``derive_fact_key``'s subject arm: articles/possessives stripped, the same
    character class, the same 100-char cap -- so "the backup code" resolves to exactly the
    key "remember/correction the backup code is X" wrote. Tokens carrying digits are values,
    not subjects, and return "" (they keep the text-match path only)."""
    cleaned = _FORGET_SUBJECT_STRIP_RE.sub("", str(token or "").strip().lower())
    cleaned = re.sub(r"^(?:my|our|the|a|an|operator(?:'s)?)\s+", "", cleaned)
    cleaned = re.sub(r"^(?:my|our|the|a|an)\s+", "", cleaned)
    cleaned = re.sub(r"\b(?:is|are|was|were|value|entry|fact|memory)\b\s*$", "", cleaned).strip()
    if not cleaned or any(ch.isdigit() for ch in cleaned):
        return ""
    subject = re.sub(r"[^a-z0-9_. -]+", "", cleaned)
    subject = " ".join(subject.split())[:100]
    if not subject or len(subject.split()) > 6:
        return ""
    return f"fact:{subject}"


def forget_structured_memory(
    token: str,
    *,
    access_policy: ContextAccessPolicy | None = None,
    chat_id: str | None = None,
) -> int:
    """The production forget authority.

    Selection may use the caller's keyword (that is the UX); ERASURE binds to
    the selected rows' record ids. Each selected row becomes a durable
    tombstone in one atomic rewrite of the canonical store, the MEMORY.md
    projection is regenerated so no mirror line outlives its row, and the
    count is returned only after every model-visible path has re-read without
    the erased text (``MemoryErasureError`` otherwise — no acknowledgement
    for an unverified forget).
    """
    clean_token = str(token or "").strip().lower()
    if not clean_token or contains_secret(clean_token):
        return 0
    policy = resolve_memory_access_policy(
        access_policy=access_policy,
        chat_id=chat_id,
    )
    # A SUBJECT-shaped forget ("forget the backup code") must also reach the row a
    # CORRECTION wrote: corrections store "Current corrected <label>: <value>" under the
    # subject's fact_key, and that text need not contain the caller's phrasing -- so a
    # text-only match left the corrected value alive after the user forgot the subject
    # (measured on the served candidate: "forget the backup code" removed 1 row while
    # "Current corrected code: XQ-991-VERDE" survived and stayed retrievable). Matching
    # the same key space the writers use closes that gap without loosening value-match.
    subject_key = _forget_subject_fact_key(clean_token)
    with _MEMORY_ENTRY_LOCK:
        rows = load_jsonl(memory_entries_path())
        target_ids = [
            str(row.get("record_id") or "").strip()
            for row in rows
            if str(row.get("record_id") or "").strip()
            and (
                clean_token in str(row.get("text") or "").lower()
                or (subject_key and str(row.get("fact_key") or "").strip().lower() == subject_key)
            )
            and _memory_command_row_allowed(
                row,
                access_policy=policy,
            )
        ]
        erased_texts = _erase_rows_locked(target_ids)

        # A summary is chat-scoped derived data.  Forgetting may clear only
        # summaries from the requesting chat; another chat's summary is never
        # inspected or rewritten by this command.
        summaries = load_jsonl(session_summaries_path())
        kept_summaries = [
            row
            for row in summaries
            if not (
                str(row.get("session_id") or "").strip() == policy.chat_id
                and clean_token
                in str(row.get("summary") or "").lower()
            )
        ]
        if len(kept_summaries) != len(summaries):
            rewrite_jsonl(session_summaries_path(), kept_summaries)
        # Refresh the projection on EVERY forget command (not only when rows
        # were erased): a retry after a crash between the canonical erasure
        # and the mirror rewrite heals the stale projection here.
        _rewrite_memory_mirror_locked()
    if erased_texts:
        _verify_erasure(erased_texts, target_ids)
    return len(erased_texts)


def _write_session_meta_atomic(meta: dict[str, Any]) -> None:
    path = chat_session_meta_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(meta, ensure_ascii=False)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    with contextlib.suppress(OSError):
        path.with_name(path.name + ".bak").write_text(payload, encoding="utf-8")


def forget_sessions_memory(session_ids: Any) -> int:
    """Purge learned memory + session summaries authored in the given sessions (transcripts untouched).

    Used when a project is deleted with "forget its context": everything VOOL learned inside that
    project is dropped so it never resurfaces elsewhere. Returns the number of rows removed.
    P0 erasure law: purged memory rows become durable tombstones and the
    MEMORY.md projection is rewritten, so the purge obeys the same
    no-resurrection law as an explicit forget.
    """
    ids = {str(s).strip() for s in (session_ids or []) if str(s).strip()}
    if not ids:
        return 0
    _ensure_memory_files()
    with _MEMORY_ENTRY_LOCK:
        target_ids = [
            str(row.get("record_id") or "").strip()
            for row in load_jsonl(memory_entries_path())
            if str(row.get("session_id") or "").strip() in ids
            and str(row.get("record_id") or "").strip()
        ]
        erased_texts = _erase_rows_locked(target_ids)
        removed = len(erased_texts)
        summaries = load_jsonl(session_summaries_path())
        kept = [
            row for row in summaries if str(row.get("session_id") or "").strip() not in ids
        ]
        if len(kept) != len(summaries):
            removed += len(summaries) - len(kept)
            rewrite_jsonl(session_summaries_path(), kept)
        if erased_texts:
            _rewrite_memory_mirror_locked()
    if erased_texts:
        _verify_erasure(erased_texts, target_ids)
    return removed


def erase_finalizations_for_session(session_id: str) -> list[dict[str, Any]]:
    """A8 rebind (D1): a chat's governed payloads transition through the ONE
    availability authority before any local transcript/memory removal.

    Finalizations in scope are identified from the conversation log — by
    lineage (request_id stamps) for new turns and by exact content hash for
    legacy pre-lineage turns. Each erasure runs the canonical traversal
    (a7 row law + derivative sweep). Returns per-finalization outcomes.
    """
    sid = str(session_id or "").strip()
    if not sid:
        return []
    from core.finalization import (
        erase_finalization_payload,
        get_finalization_by_content,
        get_finalization_by_request_id,
    )

    fids: list[str] = []
    seen: set[str] = set()

    def _collect(row: dict[str, Any]) -> None:
        rid = str(row.get("request_id") or "").strip()
        if rid:
            try:
                found = get_finalization_by_request_id(rid, principal="owner_local")
            except Exception:
                found = None
            if found is not None:
                fid = str(found.get("finalization_id") or "")
                if fid and fid not in seen:
                    seen.add(fid)
                    fids.append(fid)
        assistant = str(row.get("assistant") or "").strip()
        if assistant:
            binding = get_finalization_by_content(assistant)
            fid = str((binding or {}).get("finalization_id") or "")
            if fid and fid not in seen:
                seen.add(fid)
                fids.append(fid)

    for row in load_jsonl(conversation_log_path()):
        if str(row.get("session_id") or "").strip() != sid:
            continue
        if isinstance(row, dict):
            _collect(row)
    return [
        erase_finalization_payload(
            fid,
            reason="chat_delete",
            governance_actor="owner_local",
        )
        for fid in fids
    ]


def delete_conversation_session(session_id: str) -> bool:
    """Permanently remove ONE chat: its transcript, its learned memory + summaries, its meta entry,
    and its namespace.

    Returns True if anything was removed. The chat's governed payloads first
    transition through the canonical A8 availability authority (ERASED +
    derivative traversal), and its DB dialogue/feedback rows are deleted so no
    cross-session reader can surface them.

    The namespace transition is not bookkeeping — it is what makes the chat stop existing. A chat's
    project binding lives only in the meta entry, while the chat's existence is owned by the
    namespace table, and ``list_conversation_sessions`` re-seeds every listing from
    ``list_chat_namespaces()``. Dropping the meta without transitioning the namespace therefore
    deleted the chat's PROJECT and kept the chat: it reappeared with ``project_id=""``, which is what
    General means. On the reporting runtime that had accumulated 92 such orphans, so deleting a chat
    out of a project visibly moved it to General and left the total unchanged.
    """
    sid = str(session_id or "").strip()
    if not sid:
        return False
    removed = False
    # The answer "deleted" must mean the chat's transcript went. The availability
    # authority below removes the log rows itself while erasing the governed
    # payloads, so the JSONL sweep further down finds nothing left to count and
    # the route reported ``deleted: False`` for every ordinary chat. Count what is
    # about to go before it goes.
    if any(str(row.get("session_id") or "").strip() == sid for row in load_jsonl(conversation_log_path())):
        removed = True
    # A8 rebind (D1): erase the session's governed payloads through the ONE
    # availability authority FIRST — the conversation log is still present to
    # identify the finalizations in scope (lineage + legacy content match).
    erase_finalizations_for_session(sid)
    # DB dialogue rows are session-keyed: remove them so the cross-session
    # reader can never serve turns of a deleted chat.
    try:
        from storage.dialogue_memory import delete_dialogue_rows_for_session

        # A chat whose transcript lived only in the DB rows (the ordinary served case) was reported
        # as ``deleted: False`` by the route above because only the JSONL sweep counted: the rows
        # went, and the answer said nothing had.
        if delete_dialogue_rows_for_session(sid) > 0:
            removed = True
    except Exception:
        pass
    for path in (conversation_log_path(), session_summaries_path()):
        rows = load_jsonl(path)
        kept = [row for row in rows if str(row.get("session_id") or "").strip() != sid]
        if len(kept) != len(rows):
            rewrite_jsonl(path, kept)
            removed = True
    # P0 erasure law: the chat's memory rows become durable tombstones and the
    # mirror projection is rewritten, so deleting a chat cannot leave its
    # facts alive in (or resurrectable from) MEMORY.md.
    with _MEMORY_ENTRY_LOCK:
        target_ids = [
            str(row.get("record_id") or "").strip()
            for row in load_jsonl(memory_entries_path())
            if str(row.get("session_id") or "").strip() == sid
            and str(row.get("record_id") or "").strip()
        ]
        erased_texts = _erase_rows_locked(target_ids)
        if erased_texts:
            removed = True
            _rewrite_memory_mirror_locked()
    if erased_texts:
        _verify_erasure(erased_texts, target_ids)
    with _SESSION_META_LOCK:
        meta = load_session_meta()
        if sid in meta:
            del meta[sid]
            _write_session_meta_atomic(meta)
            removed = True
    from core.context_namespace import load_chat_namespace, set_chat_namespace_state

    namespace = load_chat_namespace(sid)
    if namespace is not None and namespace.lifecycle_state != "deleted":
        set_chat_namespace_state(sid, "deleted")
        removed = True
    return removed


def combined_memory_entries() -> list[dict[str, Any]]:
    _ensure_memory_files()
    entries = [
        row
        for row in load_jsonl(memory_entries_path())
        if str(row.get("status") or "") != _ERASED_STATUS
    ]
    seen = {str(row.get("text") or "").strip().lower() for row in entries}
    for row in legacy_memory_entries():
        text_key = str(row.get("text") or "").strip().lower()
        if text_key and text_key not in seen:
            entries.append(row)
            seen.add(text_key)
    return entries


def legacy_memory_entries() -> list[dict[str, Any]]:
    """Ingest the MEMORY.md projection's unbound legacy lines — through the erasure law.

    P0 law: a line BOUND to a record id (``<!--mem:...-->``) is a projection of
    a governed row and never yields a legacy entry — the row serves from the
    canonical store or not at all, so a mirror line whose row was erased,
    superseded or trimmed cannot resurrect it. An UNBOUND line (legacy or
    hand-written) is legacy input: retained on disk for the human, but a line
    whose text carries a durable erasure tombstone is dropped here, so a
    manually re-added forgotten fact cannot come back through the mirror.
    Legacy rows remain provenance-quarantined from serving (``_memory_row_allowed``).
    """
    path = memory_path()
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped.startswith("- "):
            continue
        text = stripped[2:].strip()
        if _MIRROR_BIND_RE.search(text):
            # Bound projection line: governed identity decides, not the bytes.
            continue
        created_at = ""
        match = re.match(r"^\[(\d{4}-\d{2}-\d{2})\]\s+(.+)$", text)
        if match:
            created_at = match.group(1)
            text = match.group(2).strip()
        clean = sanitize_fact(text)
        if not clean:
            continue
        if _text_is_erased(clean):
            continue
        out.append(
            {
                "created_at": created_at,
                "text": clean,
                "category": "fact",
                "session_id": "",
                "source": "legacy_markdown",
                "confidence": 0.74,
                "keywords": keyword_tokens_filtered(clean),
                "share_scope": "local_only",
            }
        )
    return out


def replace_name_memory(
    new_name_fact: str,
    *,
    access_policy: ContextAccessPolicy | None = None,
    chat_id: str | None = None,
) -> int:
    _ensure_memory_files()
    clean = sanitize_fact(new_name_fact)
    if not clean or contains_secret(clean):
        return 0
    policy = resolve_memory_access_policy(
        access_policy=access_policy,
        chat_id=chat_id,
    )
    if not policy.allow_user_profile_context:
        return 0
    rows = load_jsonl(memory_entries_path())
    active_names = [
        row
        for row in rows
        if str(row.get("category") or "").strip().lower() == "name"
        and str(row.get("status") or "").strip().lower() == "active"
        and _memory_command_row_allowed(
            row,
            access_policy=policy,
        )
    ]
    added = add_memory_fact(
        clean,
        category="name",
        session_id=policy.chat_id,
        source="explicit_name_replacement",
        confidence=1.0,
        project_id=policy.project_id,
        scope="user_profile",
        authority="user_correction",
        access_policy=policy,
    )
    return len(active_names) if added else 0


def keyword_tokens_filtered(text: str, *, limit: int = 16) -> list[str]:
    base = keyword_tokens(str(text or ""), limit=max(limit * 2, limit))
    out: list[str] = []
    for token in base:
        normalized = token.strip("'")
        if len(normalized) < 3 or normalized in _STOPWORDS:
            continue
        out.append(normalized)
        if len(out) >= limit:
            break
    return out


def trim_text(text: str, max_chars: int) -> str:
    clean = " ".join(str(text or "").split()).strip()
    if len(clean) <= max_chars:
        return clean
    return clean[: max(0, max_chars - 3)].rstrip() + "..."


def recency_score(timestamp: str) -> float:
    if not timestamp:
        return 0.35
    try:
        dt = datetime.fromisoformat(timestamp)
    except Exception:
        try:
            dt = datetime.fromisoformat(f"{timestamp}T00:00:00+00:00")
        except Exception:
            return 0.35
    age_days = max(0.0, (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds() / 86400.0)
    return max(0.2, 1.0 - min(age_days / 120.0, 0.8))


def _ensure_memory_files() -> None:
    ensure_memory_files(ensure_policy_table=ensure_session_policy_table)


def _memory_row_allowed(
    row: dict[str, Any],
    *,
    access_policy: ContextAccessPolicy,
) -> bool:
    """Apply scope before scoring a durable-memory candidate."""
    origin_chat = str(
        row.get("origin_chat_id") or row.get("session_id") or ""
    ).strip()
    origin_project = str(
        row.get("origin_project_id") or row.get("project_id") or ""
    ).strip()
    provenance = row.get("provenance")
    if not origin_chat or not isinstance(provenance, dict):
        # Ambiguous legacy records are retained on disk but quarantined.
        return False
    namespace = load_chat_namespace(origin_chat)
    if namespace is None or namespace.lifecycle_state != "active":
        return False
    expires_at = str(row.get("expires_at") or "").strip()
    if expires_at:
        try:
            expiry = datetime.fromisoformat(expires_at)
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            if expiry.astimezone(timezone.utc) <= datetime.now(timezone.utc):
                return False
        except ValueError:
            return False
    metadata = {
        "scope": str(row.get("scope") or "").strip().lower(),
        "source": str(row.get("source") or "").strip().lower(),
        "source_id": str(row.get("source_id") or "").strip(),
        "status": str(row.get("status") or "").strip().lower(),
        "origin_chat_id": origin_chat,
        "origin_project_id": origin_project,
        "provenance": provenance,
    }
    allowed, _reason = access_policy.allows_metadata(
        source_type="runtime_memory",
        metadata=metadata,
    )
    return allowed
