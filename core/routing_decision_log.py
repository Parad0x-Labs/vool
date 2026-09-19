"""Owner-local routing-decision telemetry: which family answered each turn, and why.

Every front-door dispatch decision is appended here — the fast-path family that claimed the
message (its ``reason``), or ``model_lane`` when everything fell through to the model. This is
the measurement layer for routing accuracy: mis-routes stop being anecdotes ("not what I asked")
and become rows you can grep, count, and turn into gauntlet cases.

Strictly local: one JSONL file under the runtime data dir, never transmitted anywhere. The
message is truncated + secret-redacted before it is written. Appends are lock-guarded and
fail-soft — telemetry must never break a turn. The file self-rotates (keeps the newest tail)
so it cannot grow without bound.
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
    flat = " ".join(str(text or "").split())[:_MESSAGE_MAX]
    try:
        from core.secret_redaction import redact_secrets

        return redact_secrets(flat)
    except Exception:
        return flat


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
        row = {
            "ts": utcnow(),
            "session_id": str(session_id or "")[:80],
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
            session_ref=str(session_id or ""),
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
