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
better-redacted. Rows are versioned (``record_schema``); :func:`migrate_legacy_decision_log`
strips the plaintext ``message`` column from legacy rows without touching their other fields —
attempted once per process for EACH configured runtime home, serialized with appends. A
PARTIAL migration stays partial: malformed lines are preserved byte-identical and may still
carry old message text (the result and the owner docstring say so; they are never silently
dropped, "fixed", or called plaintext-free). What remains user-input-derived in this owner's
durable state is exactly one value, stated exactly: the shadow record's
``message_redacted_digest`` — a plain SHA-256 of the already-redacted, already-truncated text.
It is not plaintext and not recoverable, but an observer holding the store can CONFIRM A GUESS
of a low-entropy message ("yes"/"no"-class turns, dictionary-grade requests) by hashing
candidates; high-entropy content is not exposed. Redaction before that digest fails closed:
when secret-shape protection is unavailable or faults, the digest covers a refusal sentinel,
never the raw text.

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
served delete routes. Identity, not spelling, decides what is "this chat": the namespace
erasure authority records each row's canonical identity, so a late write under ANY handle
spelling of a deleted chat is suppressed, and an unreadable namespace store suppresses
telemetry too (fail-closed — optional diagnostics never return because a fault hid the
tombstone). A delete that lands mid-write is caught by a post-write recheck against the
same authority: an idempotent self-purge, never a second tombstone manager. Only the deleted chat's folded identity is removed; other sessions' rows and
other record types are untouched, and surviving shadow rows keep their canonical bytes,
digests and type validation (deletion removes whole rows, it never rewrites survivors).
The shadow store's rows are append-only diagnostics with chat deletion as their ONE removal
authority — no rotation, pruning or silent trimming exists there. Corruption is accounted,
not papered over: corrupted rows or lines whose session cannot be soundly established —
including storage shapes the store's contract does not vouch for — are preserved and
COUNTED as unattributable, and the served delete doors report incomplete verification
instead of claiming complete erasure while such bytes survive. There is no served reader of
message-shaped data: ``recent_decisions``/``decision_stats`` serve owner-local diagnostics
and gauntlet mining only, over the metadata columns. Correlating a row with its turn means
joining on ``session_id`` + ``ts`` against the authorized conversation view.
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
#: Which runtime logs this process has already ATTEMPTED to migrate. Scoped to the log's
#: resolved path — a process-global boolean skipped the second configured runtime home,
#: leaving its legacy plaintext in place. ``True``/``False`` keep their legacy meanings
#: (everything attempted / nothing attempted) for compatibility with code that resets the
#: flag between homes: a failed attempt is still an attempt (never retried per-call; the
#: next process, or the explicit operator entrypoint, is the retry).
_LEGACY_MIGRATION_ATTEMPTED: set[str] | bool = set()


def _migration_attempted(path: Path) -> bool:
    state = _LEGACY_MIGRATION_ATTEMPTED
    if state is True:
        return True
    if not state:
        return False
    return str(path) in state


def _mark_migration_attempted(path: Path) -> None:
    global _LEGACY_MIGRATION_ATTEMPTED
    if _LEGACY_MIGRATION_ATTEMPTED is True:
        return
    if isinstance(_LEGACY_MIGRATION_ATTEMPTED, bool):
        _LEGACY_MIGRATION_ATTEMPTED = set()
    _LEGACY_MIGRATION_ATTEMPTED.add(str(path))


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
    jsonl_rows_unattributable: int = 0
    errors: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        parts = [
            f"jsonl rows removed: {self.jsonl_rows_removed}",
            f"shadow rows removed: {self.shadow_rows_removed}",
        ]
        if self.jsonl_rows_unattributable:
            parts.append(
                f"jsonl lines preserved (corrupted, naming this chat): "
                f"{self.jsonl_rows_unattributable}"
            )
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

    Serialized with the append path under ``_LOG_LOCK`` (the lazy in-turn attempt runs
    inside that lock already; this public entrypoint takes it too, so an explicit
    operator migration cannot overwrite a concurrent append that landed mid-rewrite —
    a waiting writer re-runs after the replace and its complete row survives alongside
    the sanitized legacy metadata). Bounded and reviewable: one pass over the file, an
    atomic replace, no backup or debug copy of the plaintext (the old bytes are gone the
    moment the replace lands). Every non-content field of a migrated row is preserved;
    malformed lines are preserved BYTE-identical — the rewrite splits raw bytes on
    ``\\n`` only — and counted, never silently dropped or "fixed". A PARTIAL migration is
    reported as what it is: ``rows_malformed_preserved`` lines survive untouched and may
    still contain old message text, so a nonzero count means the durable log is not yet
    plaintext-free even though every migrated row omits ``message``.

    Idempotent: a file with no legacy ``message`` columns is not rewritten at all. Raises
    on storage errors (operators call this explicitly); the in-turn lazy attempt catches
    its own faults so a migration failure never costs the append.
    """
    target = Path(path) if path is not None else decisions_path()
    with _LOG_LOCK:
        return _migrate_locked(target)


def _migrate_locked(target: Path) -> MigrationResult:
    """The migration body. Caller holds ``_LOG_LOCK`` (never take it again here)."""
    if not target.exists():
        return MigrationResult(0, 0, 0, 0, False)
    lines, had_trailing_newline = _split_jsonl_bytes(target.read_bytes())
    out_lines: list[bytes] = []
    migrated = 0
    current = 0
    malformed = 0
    for line in lines:
        try:
            row = json.loads(line.decode("utf-8", "replace"))
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
            out_lines.append(json.dumps(row, ensure_ascii=False).encode("utf-8"))
            migrated += 1
        elif row.get("record_schema") == _ROW_SCHEMA_VERSION:
            out_lines.append(line)
            current += 1
        else:
            row["record_schema"] = _ROW_SCHEMA_VERSION
            out_lines.append(json.dumps(row, ensure_ascii=False).encode("utf-8"))
            migrated += 1
    if not migrated:
        return MigrationResult(len(lines), 0, current, malformed, False)
    tmp = target.with_name(target.name + ".migrate.tmp")
    out = b"\n".join(out_lines) + (b"\n" if (had_trailing_newline and out_lines) else b"")
    tmp.write_bytes(out)
    os.replace(tmp, target)
    return MigrationResult(len(lines), migrated, current, malformed, True)


def _attempt_lazy_migration_locked(path: Path) -> None:
    """One migration attempt per runtime log, inside the append lock, never costing the append.

    The attempted-marker is scoped to the log's resolved path, so a process that serves a
    SECOND configured runtime home still migrates that home's legacy file; a failed
    attempt is not retried within the process (that would be per-call filesystem work),
    and the explicit operator entrypoint remains the retry that raises instead of failing
    quietly. No plaintext is created either way — the upgrade only ever removes it.
    """
    if _migration_attempted(path):
        return
    _mark_migration_attempted(path)
    with contextlib.suppress(Exception):
        _migrate_locked(path)


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
    ``ok`` is False while any remains. Corruption is accounted, not papered over: a
    corrupted shadow row whose session cannot be identified — including rows whose
    storage shapes violate the store's declared contract, whose bytes their digest cannot
    vouch for — is preserved (never silently discarded with the whole log) and counted in
    ``shadow_rows_unattributable``; a malformed JSONL line that NAMES this chat but cannot
    be parsed is preserved byte-identical and counted in ``jsonl_rows_unattributable``.
    Either count means the served delete door must report incomplete verification: the
    sinks still hold a record this purge could not soundly remove.
    """
    match_ref = _fold_session_ref(session_id)
    if not match_ref:
        return TelemetryPurgeResult("", 0, 0, 0)
    errors: list[str] = []
    jsonl_removed = 0
    jsonl_unattributable = 0
    shadow_removed = 0
    shadow_unattributable = 0
    try:
        jsonl_removed, jsonl_unattributable = _remove_jsonl_rows_for_session(match_ref)
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
        jsonl_rows_unattributable=jsonl_unattributable,
        errors=tuple(errors),
    )


def _split_jsonl_bytes(raw: bytes) -> tuple[list[bytes], bool]:
    """Split raw log bytes into lines on ``\\n`` only, remembering the trailing newline.

    Binary on purpose: a ``str.splitlines()`` view would also cut on ``\\r``, ``\\x85`` and
    the Unicode line separators, and an ``errors="replace"`` decode would rewrite invalid
    UTF-8 — both would silently change the bytes of lines this owner promises to preserve
    verbatim. Only ``\\n`` is a row separator here (this owner writes ``\\n``-terminated
    UTF-8), so every other byte survives exactly as stored.
    """
    segments = raw.split(b"\n")
    had_trailing_newline = segments[-1] == b""
    lines = segments[:-1] if had_trailing_newline else segments
    return lines, had_trailing_newline


def _remove_jsonl_rows_for_session(match_ref: str) -> tuple[int, int]:
    """Drop the matching session's rows from the JSONL; preserve everything else verbatim.

    Atomic rewrite under the append lock (same single-process locking law as rotation).
    Malformed lines and other sessions' rows are copied BYTE-identical — the rewrite
    works on raw bytes and splits only on ``\\n`` — so a delete never rewrites or
    "repairs" rows it has no authority over, whatever their encoding damage. A malformed
    line that NAMES this chat (the folded id appears in its bytes) is preserved too, but
    counted as unattributable: it may carry this chat's old plaintext, and reporting it
    beats either silently keeping it or deleting half-understood bytes.
    """
    path = decisions_path()
    if not path.exists():
        return 0, 0
    match_bytes = match_ref.encode("utf-8")
    with _LOG_LOCK:
        lines, had_trailing_newline = _split_jsonl_bytes(path.read_bytes())
        kept: list[bytes] = []
        removed = 0
        unattributable = 0
        for line in lines:
            try:
                row = json.loads(line.decode("utf-8", "replace"))
            except ValueError:
                if match_bytes in line:
                    unattributable += 1
                kept.append(line)
                continue
            if isinstance(row, dict) and str(row.get("session_id") or "") == match_ref:
                removed += 1
            else:
                kept.append(line)
        if not removed:
            return removed, unattributable
        out = b"\n".join(kept) + (b"\n" if (had_trailing_newline and kept) else b"")
        tmp = path.with_name(path.name + ".purge.tmp")
        tmp.write_bytes(out)
        os.replace(tmp, path)
        return removed, unattributable


def _remove_shadow_rows_for_session(match_ref: str) -> tuple[int, int]:
    """Delete the matching session's ``RoutingDecisionShadowV2`` rows; count the unattributable.

    Attribution reads the store's CENSUS, not the well-formed-only listing: every row of
    the type is accounted for. A row whose payload cannot be parsed has no identifiable
    session — it is preserved and counted, because deleting an unattributable row could
    discard another chat's (or another type's) record, and keeping it leaves evidence a
    corrupted store exists. Rows whose digest or storage shapes violate the declared
    contract are counted the same way: their bytes are not what their digest vouches for,
    so a session_ref parsed out of them would be trusting rewritten bytes. Deletion is by
    digest, restricted to the one record type, in one transaction.
    """
    import json as _json

    store = _shadow_store()
    census = store.shadow_payload_census(record_type="RoutingDecisionShadowV2")
    digests: list[str] = []
    unattributable = census.anomalous_rows
    for digest, payload in census.payloads:
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
