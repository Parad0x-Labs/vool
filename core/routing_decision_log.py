"""Owner-local routing-decision telemetry: which family answered each turn, and why.

Every front-door dispatch decision is appended here — the fast-path family that claimed the
message (its ``reason``), or ``model_lane`` when everything fell through to the model. This is
the measurement layer for routing accuracy: mis-routes stop being anecdotes ("not what I asked")
and become rows you can grep, count, and turn into gauntlet cases.

Strictly local: one JSONL file under the runtime data dir, never transmitted anywhere. Appends
are lock-guarded and fail-soft — telemetry must never break a turn. The file self-rotates
(keeps the newest tail) so it cannot grow without bound.

**Data minimization (schema 2, alert 155's real class closed): the row stores structured
diagnostic metadata, never message text.** No request prefix, truncation fragment, message
digest or message length is written — none of the row's fields is derived from the dispatched
message. A row carries: the timestamp, the folded session id, the code-built family/claims/
arbiter labels, and ``handled``. Recognizable request text used to persist here as a
redacted-then-truncated ``message`` prefix; pattern redaction is not a guarantee that arbitrary
text is non-sensitive (a custom password sentence matched no shape), so the column is gone, not
better-redacted. Rows are versioned (``record_schema``); the one-time
:func:`migrate_legacy_decision_log` strips the plaintext ``message`` column from legacy rows
without touching their other fields. What remains user-input-derived in this owner's durable
state is exactly one value, stated exactly: the shadow record's ``message_redacted_digest`` —
a plain SHA-256 of the already-redacted, already-truncated text. It is not plaintext and not
recoverable, but an observer holding the store can CONFIRM A GUESS of a low-entropy message
("yes"/"no"-class turns, dictionary-grade requests) by hashing candidates; high-entropy content
is not exposed. Redaction before that digest fails closed: when secret-shape protection is
unavailable or faults, the digest covers a refusal sentinel, never the raw text.

The row's other fields are identifiers and code vocabularies, NOT message-derived text. The
session id is FOLDED here, at this owner, through the one shared identity authority
(``core.chat_session_identity.canonical_chat_session_id``): a canonical ``openclaw:`` id
passes through unchanged (the served path is byte-identical), and ANY other handle —
including a secret-bearing one a client chose to name its chat by — persists only as its
``openclaw:<digest>`` folding in BOTH the JSONL row and the shadow record, never as the
raw text a caller happened to provide. Family and arbiter strings are code-built vocabularies
(family constants, arbiter menu options, lane names, failure reasons), not free text from
callers; ``claims`` carries would-claim probe family names, also code-built.

**Deletion (chat erasure now includes routing telemetry):** deleting a chat removes its
routing rows from BOTH sinks — the JSONL and the shadow sqlite — through
:func:`purge_session_routing_telemetry`, called by ``delete_conversation_session`` and the
served delete routes. Only the deleted chat's folded identity is removed; other sessions'
rows and other record types are untouched, and surviving shadow rows keep their canonical
bytes, digests and type validation (deletion removes whole rows, it never rewrites survivors).
The shadow store's rows are append-only diagnostics with chat deletion as their ONE removal
authority — no rotation, pruning or silent trimming exists there. A turn that outlives its
chat's deletion must not resurrect telemetry: the namespace lifecycle authority (durable,
irreversible, flipped by the delete doors BEFORE erasure) suppresses new writes for a deleted
session, and a post-write recheck re-purges if a delete landed mid-write. There is no served
reader of message-shaped data: ``recent_decisions``/``decision_stats`` serve owner-local
diagnostics and gauntlet mining only, over the metadata columns. Correlating a row with its
turn means joining on ``session_id`` + ``ts`` against the authorized conversation view.
"""
from __future__ import annotations

import contextlib
import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.memory.files import utcnow
from core.routing_authority_v2 import RoutingAuthorityV2ShadowStore
from core.runtime_paths import data_path

_LOG_FILE = "routing_decisions.jsonl"
_LOG_LOCK = threading.Lock()
_MESSAGE_MAX = 160          # the shadow digest's input is redacted-then-truncated to this
_ROTATE_BYTES = 512 * 1024  # rotate at ~512KB ...
_ROTATE_KEEP = 1000         # ... keeping the newest N rows

#: Schema 2: structured metadata only. Schema 1 (no marker) rows carried a redacted
#: ``message`` prefix; :func:`migrate_legacy_decision_log` strips it on upgrade.
_ROW_SCHEMA_VERSION = 2
_LEGACY_MIGRATION_ATTEMPTED = False


def decisions_path() -> Path:
    return data_path(_LOG_FILE)


def _clean_message(text: str) -> str:
    """The redacted message — ONLY an intermediate value for the shadow digest, never stored.

    Fails closed: without secret-shape protection the result is a refusal sentinel, so the
    digest can never be of raw text this owner was unable to screen. Byte-identical to the
    schema-1 computation so digests stay comparable across the schema change.
    """
    try:
        from core.secret_redaction import mnemonic_redaction_available, redact_secrets

        # Recovery-phrase detection needs the canonical BIP-39 wordlist. When it is not
        # available (a broken install is the known case: the wordlist must ship as package
        # data), "redacted" would be a plaintext success for a phrase this log cannot see.
        # Telemetry fails closed here; the turn itself and the transcript are unaffected.
        if not mnemonic_redaction_available():
            return "[message unavailable: secret-shape protection unavailable]"
        # Redact the intact input before formatting/truncation can split a secret.
        return " ".join(redact_secrets(str(text or "")).split())[:_MESSAGE_MAX]
    except Exception:
        # Telemetry may fail soft; confidentiality must fail closed.
        return "[message unavailable: redaction failed]"


def _fold_session_ref(session_id: str) -> str:
    """The identifier this log persists for a session: the ONE folded-handle identity.

    The chat doors fold a caller's handle through ``core.chat_session_identity`` before a turn
    runs, but this owner persisted whatever a caller passed — so a secret-bearing handle (a
    client that names its chat by content) reached BOTH durable sinks as raw text, unreduced by
    the message redactor that never sees identifiers. Folding here makes the identifier law the
    owner's own instead of every caller's memory: canonical ``openclaw:`` ids pass through
    unchanged (the served path is byte-identical), every other handle persists only as its
    digest, and doors that disagree about a handle's shape still land on the same identity —
    which is the correlation operators grep by. It is also the key deletion matches on.
    """
    from core.chat_session_identity import canonical_chat_session_id

    return canonical_chat_session_id(str(session_id or ""))


def _session_telemetry_suppressed(folded_ref: str, raw_handle: str) -> bool:
    """True when this chat is deleted and must not grow or keep routing telemetry.

    The ONE erasure authority for chat existence is the namespace lifecycle: both served
    delete doors flip it to ``deleted`` BEFORE erasing the transcript, the state is durable
    and irreversible, and ``delete_conversation_session`` flips it too. That authority —
    not a new tombstone manager — is what a late or in-flight write is checked against.

    Identity is resolved by CHAT, not by spelling: a native handle and the canonical id it
    folds to are one chat, and the guard consults the namespace owner's identity
    resolution, so a write under either spelling of a deleted chat is suppressed — a
    folded-handle late write can no longer step around a tombstone stored under the raw
    handle. FAIL-CLOSED: when the erasure authority cannot answer — an unreadable or
    faulting namespace store — the answer is "suppressed". Optional diagnostic persistence
    must not be re-enabled by a fault; the model turn itself is unaffected (this owner
    never raises into it).
    """
    try:
        from core.context_namespace import chat_namespace_deleted_for_identity

        for handle in dict.fromkeys((folded_ref, str(raw_handle or "").strip())):
            if not handle:
                continue
            if chat_namespace_deleted_for_identity(handle):
                return True
    except Exception:
        return True
    return False


@dataclass(frozen=True)
class MigrationResult:
    """Typed outcome of the one-time legacy ``message``-column strip (schema 1 → 2)."""

    rows_total: int
    rows_migrated: int
    rows_already_current: int
    rows_malformed_preserved: int
    rewritten: bool
    errors: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass(frozen=True)
class TelemetryPurgeResult:
    """Typed outcome of removing ONE chat's routing telemetry from both durable sinks."""

    session_ref: str
    jsonl_rows_removed: int
    shadow_rows_removed: int
    shadow_rows_unattributable: int
    errors: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        parts = [
            f"jsonl rows removed: {self.jsonl_rows_removed}",
            f"shadow rows removed: {self.shadow_rows_removed}",
        ]
        if self.shadow_rows_unattributable:
            parts.append(
                f"shadow rows preserved (corrupted, session unidentifiable): "
                f"{self.shadow_rows_unattributable}"
            )
        if self.errors:
            parts.append("errors: " + "; ".join(self.errors))
        return ", ".join(parts)


def migrate_legacy_decision_log(path: Path | None = None) -> MigrationResult:
    """Strip the plaintext ``message`` column from legacy JSONL rows (schema 1 → 2).

    Bounded and reviewable: one pass over the file, an atomic replace, no backup or debug
    copy of the plaintext (the old bytes are gone the moment the replace lands). Every
    non-content field of a migrated row is preserved; malformed lines are preserved
    byte-identical and counted — a corrupt line is never silently dropped or "fixed".
    Idempotent: a file with no legacy ``message`` columns is not rewritten at all. Raises
    on storage errors (operators call this explicitly); the in-turn lazy attempt catches
    its own faults so a migration failure never costs the append.
    """
    target = Path(path) if path is not None else decisions_path()
    if not target.exists():
        return MigrationResult(0, 0, 0, 0, False)
    lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    out_lines: list[str] = []
    migrated = 0
    current = 0
    malformed = 0
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            out_lines.append(line)
            malformed += 1
            continue
        if not isinstance(row, dict):
            out_lines.append(line)
            malformed += 1
            continue
        if "message" in row:
            row.pop("message")
            row["record_schema"] = _ROW_SCHEMA_VERSION
            out_lines.append(json.dumps(row, ensure_ascii=False))
            migrated += 1
        elif row.get("record_schema") == _ROW_SCHEMA_VERSION:
            out_lines.append(line)
            current += 1
        else:
            row["record_schema"] = _ROW_SCHEMA_VERSION
            out_lines.append(json.dumps(row, ensure_ascii=False))
            migrated += 1
    if not migrated:
        return MigrationResult(len(lines), 0, current, malformed, False)
    tmp = target.with_name(target.name + ".migrate.tmp")
    tmp.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    os.replace(tmp, target)
    return MigrationResult(len(lines), migrated, current, malformed, True)


def _attempt_lazy_migration_locked(path: Path) -> None:
    """One migration attempt per process, inside the append lock, never costing the append.

    A failed attempt is not retried within the process (that would be per-call filesystem
    work); the next process retries, and the explicit operator entrypoint raises instead of
    failing quietly. No plaintext is created either way — the upgrade only ever removes it.
    """
    global _LEGACY_MIGRATION_ATTEMPTED
    if _LEGACY_MIGRATION_ATTEMPTED:
        return
    _LEGACY_MIGRATION_ATTEMPTED = True
    with contextlib.suppress(Exception):
        migrate_legacy_decision_log(path)


def record_decision(
    *,
    session_id: str,
    user_input: str,
    family: str,
    handled: bool,
    claims: list[str] | None = None,
    arbiter: str = "",
) -> None:
    """Append one routing decision. Fail-soft: never raises into the turn.

    ``family`` is the fast-path reason that answered (or ``model_lane``); ``claims`` is the
    would-claim probe result when it was computed (which families matched the message);
    ``arbiter`` records an arbitration outcome ("picked:<family>", "timeout", "disabled", ...).
    The dispatched message is NOT persisted in any form: the JSONL row is metadata-only
    (schema 2); the shadow record keeps its existing digest of the redacted text.
    """
    redacted_message = ""
    try:
        folded_session_ref = _fold_session_ref(session_id)
        if _session_telemetry_suppressed(folded_session_ref, session_id):
            return
        redacted_message = _clean_message(user_input)
        row = {
            "record_schema": _ROW_SCHEMA_VERSION,
            "ts": utcnow(),
            "session_id": folded_session_ref[:80],
            "family": str(family or "")[:80],
            "handled": bool(handled),
        }
        if claims:
            row["claims"] = [str(c)[:40] for c in claims][:8]
        if arbiter:
            row["arbiter"] = str(arbiter)[:80]
        path = decisions_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(row, ensure_ascii=False) + "\n"
        with _LOG_LOCK:
            _attempt_lazy_migration_locked(path)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(payload)
            _maybe_rotate(path)
    except Exception:
        pass  # observability, not correctness
    # Feed the same decision to the per-turn REACH record, so every family that already reports
    # here is covered without instrumenting each of its call sites separately -- the front door's
    # fast paths, the intent arbiter, the stepped audit and the permission policy all reach this
    # function today. Its own try block: a fault here must not cost the JSONL write above, which
    # is why this is not folded into it.
    #
    # Recorded as TELEMETRY, never as a claim. `handled=True` here means "this family produced the
    # answer it was asked for" -- it is not the same fact as "this gate preempted the model", and
    # mapping it onto CLAIMED let a late telemetry row retroactively assert it had preempted a turn
    # whose lane was decided elsewhere. A hostile review found exactly that. The authoritative
    # claimed/declined records are the explicit lane instrumentation in `apps.vool_agent`; this is
    # observation about observation.
    try:
        from core.semantic import reach as semantic_reach

        semantic_reach.record(
            str(family or ""),
            semantic_reach.OUTCOME_TELEMETRY,
            detail=f"handled={bool(handled)}{(' ' + str(arbiter)) if arbiter else ''}"[:200],
        )
    except Exception:
        pass
    # NIA-010 (2026-08-30): the same decision also lands as a typed, canonical,
    # tamper-evident shadow record — the Phase-0 routing-authority contracts take
    # their first live seam. Non-authorizing by construction (the store's closed
    # schema gates it); carries a digest of the already-redacted message, never
    # raw text. Own try block, same fail-soft law: shadow telemetry must never
    # break a turn, and a shadow fault must not cost the JSONL write above.
    try:
        import hashlib

        from core.routing_authority_v2 import RoutingDecisionShadowV2

        record = RoutingDecisionShadowV2(
            recorded_at_unix_ms=_unix_ms_now(row["ts"]),
            session_ref=folded_session_ref,
            family=str(family or ""),
            handled=bool(handled),
            message_redacted_digest=hashlib.sha256(
                redacted_message.encode("utf-8")
            ).hexdigest(),
            claims=tuple(str(c)[:40] for c in (claims or [])[:8]),
            arbiter=str(arbiter or ""),
        )
        with _SHADOW_LOCK:
            _shadow_store().persist_shadow_record(record)
    except Exception:
        pass
    # Race closure: a delete can land between the guard above and these writes. Re-check
    # the same authority and remove this session's rows if its chat stopped existing while
    # the turn was being recorded — an idempotent self-purge, never a second tombstone.
    try:
        if _session_telemetry_suppressed(folded_session_ref, session_id):
            purge_session_routing_telemetry(session_id)
    except Exception:
        pass


def purge_session_routing_telemetry(session_id: str) -> TelemetryPurgeResult:
    """Remove ONE chat's routing telemetry from BOTH durable sinks. Typed, idempotent.

    Called by ``delete_conversation_session`` and re-run (as verification, not as a
    duplicate authority) by the served delete routes. Only rows whose folded session
    identity matches are removed: other sessions' diagnostic rows survive in both sinks,
    and the shadow deletion is restricted to ``RoutingDecisionShadowV2`` rows — no other
    record type is ever considered. Surviving shadow rows are untouched whole rows, so
    their canonical bytes, digests and type validation remain exactly what they were.

    Failure behavior is typed, bounded and reported — never a raised exception into the
    caller's erasure flow, never a swallowed success: ``errors`` names each sink fault and
    ``ok`` is False while any remains. A corrupted shadow row whose session cannot be
    identified is preserved (never silently discarded with the whole log) and counted in
    ``shadow_rows_unattributable``.
    """
    match_ref = _fold_session_ref(session_id)
    if not match_ref:
        return TelemetryPurgeResult("", 0, 0, 0, ("empty session id"))
    errors: list[str] = []
    jsonl_removed = 0
    shadow_removed = 0
    shadow_unattributable = 0
    try:
        jsonl_removed = _remove_jsonl_rows_for_session(match_ref)
    except Exception as exc:
        errors.append(f"jsonl: {exc}")
    try:
        shadow_removed, shadow_unattributable = _remove_shadow_rows_for_session(match_ref)
    except Exception as exc:
        errors.append(f"shadow: {exc}")
    return TelemetryPurgeResult(
        session_ref=match_ref,
        jsonl_rows_removed=jsonl_removed,
        shadow_rows_removed=shadow_removed,
        shadow_rows_unattributable=shadow_unattributable,
        errors=tuple(errors),
    )


def _remove_jsonl_rows_for_session(match_ref: str) -> int:
    """Drop the matching session's rows from the JSONL; preserve everything else verbatim.

    Atomic rewrite under the append lock (same single-process locking law as rotation):
    malformed lines and other sessions' rows are copied byte-identical, so a delete never
    rewrites or "repairs" rows it has no authority over.
    """
    path = decisions_path()
    if not path.exists():
        return 0
    with _LOG_LOCK:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        kept: list[str] = []
        removed = 0
        for line in lines:
            try:
                row = json.loads(line)
            except ValueError:
                kept.append(line)
                continue
            if isinstance(row, dict) and str(row.get("session_id") or "") == match_ref:
                removed += 1
            else:
                kept.append(line)
        if not removed:
            return 0
        tmp = path.with_name(path.name + ".purge.tmp")
        tmp.write_text("\n".join(kept) + "\n", encoding="utf-8")
        os.replace(tmp, path)
        return removed


def _remove_shadow_rows_for_session(match_ref: str) -> tuple[int, int]:
    """Delete the matching session's ``RoutingDecisionShadowV2`` rows; count the unattributable.

    Enumeration is deliberately unvalidated: a row that fails strict validation is still
    enumerable so its attribution can be decided. A row whose payload cannot be parsed has
    no identifiable session — it is preserved and counted, because deleting an
    unattributable row could discard another chat's (or another type's) record, and
    keeping it leaves evidence a corrupted store exists. Deletion itself is by digest,
    restricted to the one record type, in one transaction.
    """
    import json as _json

    store = _shadow_store()
    digests: list[str] = []
    unattributable = 0
    for digest, payload in store.list_shadow_payloads(
        record_type="RoutingDecisionShadowV2"
    ):
        try:
            row = _json.loads(payload.decode("utf-8", "replace"))
        except ValueError:
            unattributable += 1
            continue
        # a valid RoutingDecisionShadowV2 payload always carries session_ref; without it
        # the row's session is unidentifiable — preserve and count it, never delete blind
        if not isinstance(row, dict) or "session_ref" not in row:
            unattributable += 1
            continue
        if str(row.get("session_ref") or "") == match_ref:
            digests.append(digest)
    removed = 0
    if digests:
        removed = store.delete_shadow_records(
            record_type="RoutingDecisionShadowV2", record_digests=tuple(digests)
        )
    return removed, unattributable


def _unix_ms_now(ts: Any) -> int:
    """Epoch ms from the row timestamp when it carries tz info, wall clock otherwise."""
    try:
        if hasattr(ts, "timestamp"):
            return int(ts.timestamp() * 1000)
    except Exception:
        pass
    import time

    return int(time.time() * 1000)


_SHADOW_LOCK = threading.Lock()
_SHADOW_STORE: RoutingAuthorityV2ShadowStore | None = None


def _shadow_store() -> RoutingAuthorityV2ShadowStore:
    """Lazy singleton over the owner-local shadow database (created on first use)."""
    global _SHADOW_STORE
    if _SHADOW_STORE is None:
        from core.routing_authority_v2 import RoutingAuthorityV2ShadowStore

        path = data_path("routing_authority_v2_shadow.sqlite")
        path.parent.mkdir(parents=True, exist_ok=True)
        _SHADOW_STORE = RoutingAuthorityV2ShadowStore(path)
    return _SHADOW_STORE


def _maybe_rotate(path: Path) -> None:
    try:
        if path.stat().st_size <= _ROTATE_BYTES:
            return
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        tail = lines[-_ROTATE_KEEP:]
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text("\n".join(tail) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        pass


def recent_decisions(limit: int = 100) -> list[dict[str, Any]]:
    """Newest-last tail of the decision log (owner-local diagnostics + gauntlet mining)."""
    try:
        path = decisions_path()
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        out: list[dict[str, Any]] = []
        for line in lines[-max(1, int(limit)):]:
            try:
                row = json.loads(line)
                if isinstance(row, dict):
                    out.append(row)
            except json.JSONDecodeError:
                continue
        return out
    except Exception:
        return []


def decision_stats(limit: int = 1000) -> dict[str, Any]:
    """Family counts over the recent tail — the raw material for a routing-accuracy read."""
    rows = recent_decisions(limit)
    families: dict[str, int] = {}
    ambiguous = 0
    for row in rows:
        families[str(row.get("family") or "?")] = families.get(str(row.get("family") or "?"), 0) + 1
        if len(row.get("claims") or []) >= 2:
            ambiguous += 1
    return {"total": len(rows), "families": families, "ambiguous": ambiguous}


__all__ = [
    "MigrationResult",
    "TelemetryPurgeResult",
    "decision_stats",
    "decisions_path",
    "migrate_legacy_decision_log",
    "purge_session_routing_telemetry",
    "recent_decisions",
    "record_decision",
]
