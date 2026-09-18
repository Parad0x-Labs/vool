"""Deterministic tool-result memoization behind an explicit purity gate.

C³ layer L0 (OX-CONTEXT-RUNTIME, Part III): a deterministic tool asked twice
with the same normalized arguments against the same workspace state should be
EXECUTED once and served from the memo to every requester — Council runs and
speculative prefetch included. Hit rate is a schedulable runtime property with
a receipt, not luck.

Laws:

    ONLY TOOLS DECLARED PURE ARE EVER MEMOIZED — the gate is fail-closed.
    A MEMO HIT CARRIES PROVENANCE (which key, how many hits) — never a silent
    substitution, and a memo never satisfies a tool the registry has not
    cleared.

Purity is an operator/architecture decision, not an inference: registering a
tool here asserts "same normalized args → same result" (read-only listers,
validators, currency tables). Anything stateful, external-effecting, or
time-sensitive registers with a TTL — or not at all, which simply means
always-execute. Unregistered tools flow through untouched; nothing breaks.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from storage.db import get_connection

_log = logging.getLogger(__name__)

_LOCK = threading.Lock()
# name -> (ttl_seconds, version). 0 = no expiry. The version is part of every
# memo key, so re-registering a tool with a NEW version invalidates its prior
# entries by construction (the key changes) — measured misses, not stale hits.
_PURE_TOOLS: dict[str, tuple[int, str]] = {}

# Policy epoch: a runtime-wide counter folded into EVERY memo key. A policy
# change bumps it and every subsequent lookup is an honest miss — a cached
# result computed under an old policy must never serve a new one. The bump
# is receipted to the append-only event chain.
_POLICY_EPOCH = 0


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def policy_epoch() -> int:
    with _LOCK:
        return _POLICY_EPOCH


def bump_policy_epoch(reason: str = "") -> int:
    """Invalidate every memo by policy: all keys change, all hits become misses.

    Receipted to the hash chain (best effort — an unavailable chain must not
    block the invalidation itself) so the bump is an auditable event.
    """
    global _POLICY_EPOCH
    with _LOCK:
        _POLICY_EPOCH += 1
        epoch = _POLICY_EPOCH
    try:
        from storage.event_hash_chain import append_hashed_event

        append_hashed_event(
            f"tool-memo-policy-epoch:{epoch}",
            {
                "event": "tool_memo.policy_epoch_bumped",
                "epoch": epoch,
                "reason": reason or "unspecified",
                "bumped_at": _utcnow().isoformat(),
            },
        )
    except Exception:
        _log.warning("policy epoch %d bump was not receipted (chain unavailable)", epoch)
    _log.info("tool memo policy epoch bumped to %d (%s)", epoch, reason or "unspecified")
    return epoch


def _init_table() -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tool_memo_cache (
                memo_key TEXT PRIMARY KEY,
                tool TEXT NOT NULL,
                result_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT,
                hits INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tool_memo_tool ON tool_memo_cache(tool, created_at)"
        )
        conn.commit()
    finally:
        conn.close()


# ── Purity registry (the gate) ───────────────────────────────────────────────


def register_pure_tool(name: str, *, ttl_seconds: int = 0, version: str = "") -> None:
    """Declare a tool PURE: same normalized args → same result.

    ``ttl_seconds`` bounds how long a memo may serve (0 = until evicted).
    ``version`` is the result-schema version: it is part of every memo key,
    so re-registering with a new version invalidates all prior entries for
    this tool — they become honest misses. Re-registration is the ONLY way
    a registered tool's gate entry changes; nothing else about it is mutable.
    """
    if not name:
        raise ValueError("tool name may not be empty")
    if ttl_seconds < 0:
        raise ValueError("ttl_seconds may not be negative")
    with _LOCK:
        _PURE_TOOLS[name] = (int(ttl_seconds), str(version))


def is_pure(name: str) -> bool:
    with _LOCK:
        return name in _PURE_TOOLS


def _ttl_for(name: str) -> int:
    with _LOCK:
        entry = _PURE_TOOLS.get(name)
        return entry[0] if entry else 0


def _version_for(name: str) -> str:
    with _LOCK:
        entry = _PURE_TOOLS.get(name)
        return entry[1] if entry else ""


def invalidate_tool(name: str) -> int:
    """Physically drop every stored entry for one tool; returns rows removed.

    The epoch and version mechanisms already make old entries unreachable
    (honest misses); this removes the bytes outright for operators who ask
    for it explicitly.
    """
    _init_table()
    conn = get_connection()
    try:
        cursor = conn.execute("DELETE FROM tool_memo_cache WHERE tool = ?", (name,))
        conn.commit()
        return int(cursor.rowcount or 0)
    finally:
        conn.close()


# ── Keys ─────────────────────────────────────────────────────────────────────


def memo_key(
    name: str,
    args: dict[str, Any] | None,
    *,
    scope: Sequence[str] = (),
    time_bucket: str = "",
    policy_epoch: int | None = None,
    version: str | None = None,
) -> str:
    """Stable key over normalized tool name + args + execution scope.

    ``scope`` carries the context the result depends on (e.g. workspace state
    hash, resolved account id); ``time_bucket`` lets callers bucket volatile
    data by period (e.g. a UTC date for rate tables) without pretending it is
    pure forever. ``policy_epoch`` and ``version`` default to the LIVE epoch
    and the tool's registered version, so a policy bump or a version
    re-registration changes every key they affect — invalidation by
    construction, never by hopeful bookkeeping.
    """
    epoch = policy_epoch if policy_epoch is not None else _POLICY_EPOCH
    resolved_version = version if version is not None else _version_for(name)
    canonical = json.dumps(
        {
            "tool": name,
            "args": args or {},
            "scope": list(scope),
            "bucket": time_bucket,
            "epoch": int(epoch),
            "version": str(resolved_version),
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ── Store ────────────────────────────────────────────────────────────────────


def lookup(name: str, key: str, *, now: datetime | None = None) -> tuple[Any, bool] | None:
    """Return (result, cache_hit=True) for a live memo entry, else None.

    Expired entries are treated as misses (and left in place; a sweeper is
    unnecessary for correctness). Hits increment the receipted hit counter —
    the "measured, not assumed" number cost reports cite.
    """
    _init_table()
    stamp = (now or _utcnow()).isoformat()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT result_json, expires_at FROM tool_memo_cache WHERE memo_key = ?",
            (key,),
        ).fetchone()
        if row is None:
            return None
        if row["expires_at"] and str(row["expires_at"]) <= stamp:
            return None
        conn.execute(
            "UPDATE tool_memo_cache SET hits = hits + 1 WHERE memo_key = ?", (key,)
        )
        conn.commit()
        return json.loads(row["result_json"]), True
    finally:
        conn.close()


def record(
    name: str,
    key: str,
    result: Any,
    *,
    ttl_seconds: int | None = None,
    now: datetime | None = None,
) -> None:
    """Store a result under ``key``. Non-JSON results are not cached.

    Deliberately NO ``default=`` coercion: a memo that serves back
    ``str(result)`` where the tool returned an object is a lying hit. Only
    results that serialize to their true JSON shape may be memoized.
    """
    _init_table()
    stamp = now or _utcnow()
    try:
        result_json = json.dumps(result, sort_keys=True)
    except (TypeError, ValueError):
        _log.debug("memo record skipped for %s: result not JSON-serializable", name)
        return
    ttl = _ttl_for(name) if ttl_seconds is None else int(ttl_seconds)
    expires_at = (stamp + timedelta(seconds=ttl)).isoformat() if ttl > 0 else None
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO tool_memo_cache (
                memo_key, tool, result_json, created_at, expires_at, hits
            ) VALUES (?, ?, ?, ?, ?, 0)
            """,
            (key, name, result_json, stamp.isoformat(), expires_at),
        )
        conn.commit()
    finally:
        conn.close()


# ── Entry point ──────────────────────────────────────────────────────────────


def memoize(
    name: str,
    args: dict[str, Any] | None,
    fn: Callable[[], Any],
    *,
    scope: Sequence[str] = (),
    time_bucket: str = "",
) -> tuple[Any, bool]:
    """Execute ``fn`` through the memo gate; return (result, cache_hit).

    Fail-closed by construction: an unregistered tool is executed fresh with
    ``cache_hit=False`` — memoization is never granted by default, and no
    caller can opt a tool in at the call site (that is what
    ``register_pure_tool`` is for, and it is greppable).
    """
    if not is_pure(name):
        return fn(), False
    key = memo_key(name, args, scope=scope, time_bucket=time_bucket)
    hit = lookup(name, key)
    if hit is not None:
        _log.debug("tool memo hit: %s key=%s", name, key[:12])
        return hit
    result = fn()
    record(name, key, result)
    return result, False


def memo_stats(tool: str | None = None) -> dict[str, int]:
    """Measured hit accounting for cost reports."""
    _init_table()
    conn = get_connection()
    try:
        if tool:
            row = conn.execute(
                "SELECT COUNT(*) AS entries, COALESCE(SUM(hits), 0) AS hits "
                "FROM tool_memo_cache WHERE tool = ?",
                (tool,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) AS entries, COALESCE(SUM(hits), 0) AS hits FROM tool_memo_cache"
            ).fetchone()
        return {"entries": int(row["entries"]), "hits": int(row["hits"])}
    finally:
        conn.close()
