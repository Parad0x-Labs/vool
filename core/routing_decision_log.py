"""Owner-local routing-decision telemetry: which family answered each turn, and why.

Every front-door dispatch decision is appended here — the fast-path family that claimed the
message (its ``reason``), or ``model_lane`` when everything fell through to the model. This is
the measurement layer for routing accuracy: mis-routes stop being anecdotes ("not what I asked")
and become rows you can grep, count, and turn into gauntlet cases.

Strictly local: one JSONL file under the runtime data dir, never transmitted anywhere. The
message is truncated + secret-redacted before it is written. Appends are lock-guarded and
fail-soft — telemetry must never break a turn. The file self-rotates (keeps the newest tail)
so it cannot grow without bound.

Data minimization and retention, exactly: of the MESSAGE, only a redacted-then-truncated
(160 chars) prefix is written, and that write fails closed — the row refuses the message
whenever secret-shape protection is unavailable or redaction faults. The redaction guarantee
is that narrow: shapes the shared redactor masks (recovery phrases among them, when the
canonical wordlist is armed) are masked; no detector makes arbitrary text secret-free. The
row's other fields — timestamp, session id (capped at 80 chars), family/claims/arbiter
labels — are identifiers, NOT passed through the message redactor. The session id is FOLDED
here, at this owner, through the one shared identity authority
(``core.chat_session_identity.canonical_chat_session_id``): a canonical ``openclaw:`` id
passes through unchanged (the served path is byte-identical), and ANY other handle —
including a secret-bearing one a client chose to name its chat by — persists only as its
``openclaw:<digest>`` folding in BOTH the JSONL row and the shadow record, never as the
raw text a caller happened to provide. Family and arbiter strings are code-built
vocabularies (family constants, arbiter menu options, lane names, failure reasons), not
message-derived text. The shadow sqlite record stores a SHA-256 digest of the
already-redacted message plus the same identifier metadata — not only a digest. There is no
served reader: ``recent_decisions``/``decision_stats`` serve owner-local diagnostics and
gauntlet mining only. Rows are diagnostics, not conversation: chat deletion's promise covers
the transcript, learned memory, meta, namespace and DB dialogue rows — routing rows persist
as telemetry until the ~512KB rotation trigger fires, which then keeps the newest 1000 rows
(a file under the threshold keeps everything; the shadow store has no rotation policy of its
own).
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from core.memory.files import utcnow
from core.routing_authority_v2 import RoutingAuthorityV2ShadowStore
from core.runtime_paths import data_path

_LOG_FILE = "routing_decisions.jsonl"
_LOG_LOCK = threading.Lock()
_MESSAGE_MAX = 160          # enough to recognize the request, small enough to stay cheap
_ROTATE_BYTES = 512 * 1024  # rotate at ~512KB ...
_ROTATE_KEEP = 1000         # ... keeping the newest N rows


def decisions_path() -> Path:
    return data_path(_LOG_FILE)


def _clean_message(text: str) -> str:
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
    which is the correlation operators grep by.
    """
    from core.chat_session_identity import canonical_chat_session_id

    return canonical_chat_session_id(str(session_id or ""))


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
    """
    try:
        folded_session_ref = _fold_session_ref(session_id)
        row = {
            "ts": utcnow(),
            "session_id": folded_session_ref[:80],
            "message": _clean_message(user_input),
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
            with path.open("a", encoding="utf-8") as handle:
                handle.write(payload)
            _maybe_rotate(path)
    except Exception:
        pass  # observability, not correctness
    # Feed the same decision to the per-turn REACH record, so every family that already reports
    # here is covered without instrumenting each of its call sites separately -- the front door's
    # fast paths, the intent arbiter, the stepped audit and the permission policy all reach this
    # function today. Its own try block: a fault here must not cost the JSONL write above, which is
    # why this is not folded into it.
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
                str(row.get("message") or "").encode("utf-8")
            ).hexdigest(),
            claims=tuple(str(c)[:40] for c in (claims or [])[:8]),
            arbiter=str(arbiter or ""),
        )
        with _SHADOW_LOCK:
            _shadow_store().persist_shadow_record(record)
    except Exception:
        pass


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


__all__ = ["decision_stats", "decisions_path", "recent_decisions", "record_decision"]
