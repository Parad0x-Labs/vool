"""
core/usage_meter.py
===================
Per-response token-usage ledger, split by cost class: free-local, free-cloud, and paid-cloud.

Every served model response records one row (provider, model, cost class, prompt + output
tokens). :func:`usage_summary` then aggregates by cost class so a user can see how many
tokens ran **free on their local model** vs how many went to a **paid cloud** lane — the
"stop paying cloud rates to say hello" value made visible.

Design
------
* **Fail-soft, always.** Metering is observability, never correctness — :func:`record_usage`
  swallows every error and returns False rather than raising, so a metering write can never
  abort a chat turn. Reads likewise degrade to an empty summary.
* **Tokens are the accurate metric; dollars are a rough estimate.** Cloud pricing varies per
  model, so the paid-side ``usd_estimate`` uses a single blended rate
  (``VOOL_CLOUD_USD_PER_TOKEN``, overridable) and is labelled an estimate — the token
  counts are exact, the dollar figure is a hint.
* **Scope: the SERVED response per turn.** Capture happens at the router's served-decision
  funnel, so extra real spend on race losers, verifier passes, or (if it ever bypasses the
  funnel) streamed turns is not separately metered. The bias is always conservative — the
  paid total can be understated, never overstated. Bounded by retention
  (``VOOL_USAGE_KEEP_SECONDS``, default 90 days), pruned opportunistically on write.

Persistence
-----------
SQLite via ``storage.db.get_connection()``; the schema is created lazily on first use.
"""
from __future__ import annotations

import contextlib
import logging
import os
import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any

logger = logging.getLogger("vool.usage_meter")

_SCHEMA_LOCK = Lock()
# Set of DB paths whose schema has been created. A single process-global bool would skip schema
# creation after a runtime-home/DB switch, silently no-opping all metering on the new (empty) DB.
_SCHEMA_READY_PATHS: set[str] = set()

COST_FREE_LOCAL = "free_local"
COST_FREE_CLOUD = "free_cloud"
COST_PAID_CLOUD = "paid_cloud"
COST_REMOTE_UNKNOWN = "remote_unknown"
_COST_CLASSES = (COST_FREE_LOCAL, COST_FREE_CLOUD, COST_PAID_CLOUD, COST_REMOTE_UNKNOWN)

# Rough blended cloud price per token for the paid-side dollar ESTIMATE only (token counts
# are exact). Overridable; cloud pricing is model-specific so this is a hint, not a bill.
_DEFAULT_USD_PER_PAID_TOKEN = 2.0e-6  # ~$2 per 1M tokens

# Retention: rows older than this are pruned opportunistically on write so the table can't
# grow without bound. Overridable via VOOL_USAGE_KEEP_SECONDS.
_DEFAULT_KEEP_SECONDS = 90 * 24 * 60 * 60  # 90 days

_BUSY_RETRIES = 3
_BUSY_RETRY_SECONDS = 0.01

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS token_usage (
    entry_id      TEXT NOT NULL PRIMARY KEY,
    created_at    TEXT NOT NULL,
    created_ts    REAL NOT NULL,
    provider_id   TEXT NOT NULL,
    model_id      TEXT NOT NULL,
    cost_class    TEXT NOT NULL,
    prompt_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    usd_actual    REAL,
    prompt_tokens_reported INTEGER NOT NULL DEFAULT 1,
    output_tokens_reported INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_token_usage_ts    ON token_usage(created_ts);
CREATE INDEX IF NOT EXISTS idx_token_usage_class ON token_usage(cost_class);
"""


def _now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()


def _active_db_key() -> str:
    try:
        from storage.db import active_default_db_path

        return str(active_default_db_path())
    except Exception:
        return "<default>"


def _ensure_schema(conn: Any) -> None:
    # Keyed on the ACTIVE DB path so a runtime-home switch re-creates the schema on the new DB
    # instead of skipping it because a global flag was already set for the old one.
    key = _active_db_key()
    if key in _SCHEMA_READY_PATHS:
        return
    with _SCHEMA_LOCK:
        if key in _SCHEMA_READY_PATHS:
            return
        for stmt in _CREATE_TABLE_SQL.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(stmt)
        # Additive migration for tables created before usd_actual existed. Idempotent: a
        # duplicate-column error on a table that already has it is expected and ignored.
        with contextlib.suppress(Exception):
            conn.execute("ALTER TABLE token_usage ADD COLUMN usd_actual REAL")
        # Additive migration for tables created before the reported-vs-zero distinction existed.
        # DEFAULT 1: every pre-existing row was written back when "reported" and "zero" were
        # indistinguishable at the call site, so treating history as "reported" preserves what
        # those rows already displayed as instead of silently reclassifying old data as missing.
        with contextlib.suppress(Exception):
            conn.execute("ALTER TABLE token_usage ADD COLUMN prompt_tokens_reported INTEGER NOT NULL DEFAULT 1")
        with contextlib.suppress(Exception):
            conn.execute("ALTER TABLE token_usage ADD COLUMN output_tokens_reported INTEGER NOT NULL DEFAULT 1")
        conn.commit()
        _SCHEMA_READY_PATHS.add(key)


def _usd_per_paid_token() -> float:
    raw = str(os.environ.get("VOOL_CLOUD_USD_PER_TOKEN", "")).strip()
    if raw:
        try:
            return max(0.0, float(raw))
        except ValueError:
            pass
    return _DEFAULT_USD_PER_PAID_TOKEN


def _keep_seconds() -> float:
    raw = str(os.environ.get("VOOL_USAGE_KEEP_SECONDS", "")).strip()
    if raw:
        try:
            v = float(raw)
            if v > 0:
                return v
        except ValueError:
            pass
    return _DEFAULT_KEEP_SECONDS


def _normalize_cost_class(cost_class: str) -> str:
    cc = str(cost_class or "").strip().lower()
    return cc if cc in _COST_CLASSES else COST_REMOTE_UNKNOWN


def _reported_row_cost_class(cost_class: str, *, provider_id: str = "", model_id: str = "") -> str:
    """Repair legacy OpenRouter ``:free`` rows at read time using catalog proof.

    Older builds stored every OpenRouter response as ``paid_cloud`` for policy safety, including
    verified zero-price variants.  Never trust the suffix alone: a row moves to ``free_cloud`` only
    when that exact model still exists in the cached catalog and the catalog classifies it free.
    This changes display/aggregation truth only; provider-selection and spend gates are untouched.
    """

    normalized = _normalize_cost_class(cost_class)
    provider = str(provider_id or "").strip().lower()
    model = str(model_id or "").strip().lower()
    if normalized != COST_PAID_CLOUD or not provider.startswith("openrouter-byok:") or not model.endswith(":free"):
        return normalized
    try:
        from core.openrouter_catalog import model_is_free, safe_all_models

        models, _age = safe_all_models(allow_network=False)
        if any(str(item.model_id or "").strip().lower() == model and model_is_free(item) for item in models):
            return COST_FREE_CLOUD
    except Exception:
        pass
    return normalized


def _record_usage_once(
    *,
    provider_id: str,
    model_id: str,
    cost_class: str,
    prompt_tokens: int,
    output_tokens: int,
    usd_actual: float | None = None,
    now: float | None = None,
    prompt_tokens_reported: bool = True,
    output_tokens_reported: bool = True,
) -> bool:
    """Record one served response's token usage. Fail-soft: returns True on success, False on
    any error (never raises), so a metering write cannot abort a chat turn. A row is skipped
    only when nothing measurable happened — zero tokens on both sides AND no positive actual
    cost. A row that carries a real charge but no token counts (e.g. a streamed response where
    the provider returned ``usage.cost`` without token fields) is still recorded so paid spend
    is never silently dropped.

    ``usd_actual`` is the real per-request dollar cost when the provider returns one (e.g.
    OpenRouter's ``usage.cost``); leave it None to fall back to the blended estimate.

    ``prompt_tokens_reported`` / ``output_tokens_reported`` distinguish "the provider told us
    this was 0" from "the provider told us nothing" — both default True so every existing caller
    that never learned about this parameter keeps recording exactly as before. A caller that
    actually knows the field was absent from the provider's response should pass False, so
    :func:`usage_summary`'s ``calls_missing_usage`` count reflects reality instead of collapsing
    both cases to the same stored 0.
    """
    try:
        pt = max(0, int(prompt_tokens or 0))
        ot = max(0, int(output_tokens or 0))
        ua: float | None = None
        if usd_actual is not None:
            try:
                v = float(usd_actual)
                if v >= 0:
                    ua = v
            except (TypeError, ValueError):
                ua = None
        if pt == 0 and ot == 0 and not (ua and ua > 0):
            return False
        ts = _now_ts() if now is None else float(now)
        from storage.db import get_connection

        conn = get_connection()
        try:
            _ensure_schema(conn)
            conn.execute(
                """
                INSERT INTO token_usage
                    (entry_id, created_at, created_ts, provider_id, model_id,
                     cost_class, prompt_tokens, output_tokens, usd_actual,
                     prompt_tokens_reported, output_tokens_reported)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
                    ts,
                    str(provider_id or ""),
                    str(model_id or ""),
                    _normalize_cost_class(cost_class),
                    pt,
                    ot,
                    ua,
                    1 if prompt_tokens_reported else 0,
                    1 if output_tokens_reported else 0,
                ),
            )
            # Opportunistic prune so the table can't grow without bound. Indexed on
            # created_ts, so this is a cheap range delete — and a no-op when nothing is
            # old enough.
            conn.execute("DELETE FROM token_usage WHERE created_ts < ?", (ts - _keep_seconds(),))
            conn.commit()
            return True
        finally:
            conn.close()
    except Exception as exc:  # pragma: no cover - defensive; metering never breaks a turn
        if isinstance(exc, sqlite3.OperationalError) and any(
            word in str(exc).lower() for word in ("busy", "locked")
        ):
            raise
        logger.debug("usage_meter record failed (%s); usage not recorded", exc)
        return False


def record_usage(
    *,
    provider_id: str,
    model_id: str,
    cost_class: str,
    prompt_tokens: int,
    output_tokens: int,
    usd_actual: float | None = None,
    now: float | None = None,
    prompt_tokens_reported: bool = True,
    output_tokens_reported: bool = True,
) -> bool:
    """Record usage without letting transient SQLite contention abort a chat turn."""
    for attempt in range(_BUSY_RETRIES):
        try:
            return _record_usage_once(
                provider_id=provider_id,
                model_id=model_id,
                cost_class=cost_class,
                prompt_tokens=prompt_tokens,
                output_tokens=output_tokens,
                usd_actual=usd_actual,
                now=now,
                prompt_tokens_reported=prompt_tokens_reported,
                output_tokens_reported=output_tokens_reported,
            )
        except sqlite3.OperationalError as exc:
            is_busy = any(word in str(exc).lower() for word in ("busy", "locked"))
            if not is_busy:
                logger.debug("usage_meter record failed (%s); usage not recorded", exc)
                return False
            if attempt == _BUSY_RETRIES - 1:
                logger.debug("usage_meter remained busy after retries (%s); usage not recorded", exc)
                return False
            time.sleep(_BUSY_RETRY_SECONDS * (attempt + 1))
    return False


def _empty_bucket() -> dict[str, Any]:
    return {"responses": 0, "prompt_tokens": 0, "output_tokens": 0, "total_tokens": 0, "calls_missing_usage": 0}


_RANGE_KEYS = ("today", "week", "month")


# Sane epoch bounds (year 1 .. 9999). A number outside this — e.g. a JavaScript millisecond
# timestamp mistaken for seconds — is rejected rather than allowed to raise inside fromtimestamp.
_SANE_EPOCH_MIN = -62135596800.0
_SANE_EPOCH_MAX = 253402300799.0


def _utc_iso(ts: float | None) -> str | None:
    """UTC ISO echo for an epoch, or None if absent/out of range — never raises."""
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    except (ValueError, OverflowError, OSError):
        return None


def _parse_boundary(value: Any, *, end: bool) -> float | None:
    """Parse a since/until boundary given as a UTC epoch (number) or a local ``YYYY-MM-DD``
    date. A bare date resolves to LOCAL midnight; for an ``until`` bound the day itself is
    included by advancing to the next local midnight. Returns a UTC epoch, or None if unparsable
    or out of the sane epoch range (fail-soft — an out-of-range value never raises).
    """
    raw = str(value or "").strip()
    if not raw:
        return None
    # Epoch seconds (int or float) — accept only within the sane range.
    try:
        if raw.replace(".", "", 1).isdigit():
            v = float(raw)
            return v if _SANE_EPOCH_MIN <= v <= _SANE_EPOCH_MAX else None
    except ValueError:
        pass
    try:
        y, m, d = (int(part) for part in raw.split("-", 2))
        # NAIVE local wall-clock date, advance the day BEFORE localizing so the resulting boundary
        # gets the local UTC offset for its OWN date (correct across a DST transition), then ->epoch.
        local_midnight = datetime(y, m, d)
        if end:
            local_midnight = local_midnight + timedelta(days=1)
        return local_midnight.astimezone().timestamp()
    except (ValueError, OverflowError):
        return None


def resolve_window(
    *,
    range_: str | None = None,
    since: Any = None,
    until: Any = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Resolve a reporting window to absolute UTC epoch bounds, computing calendar boundaries in
    LOCAL time so "today"/"this week"/"this month" match the user's wall clock (Windows-safe:
    ``datetime.fromtimestamp(...).astimezone()``, never a tz-name string). A named ``range_``
    (today | week(from Monday) | month) wins; otherwise an explicit ``since``/``until`` pair
    (epoch or ``YYYY-MM-DD``) applies. Returns ``{range, since_ts, until_ts, since, until}`` with
    iso echoes; ``since_ts``/``until_ts`` are None when unbounded.
    """
    now_ts = _now_ts() if now is None else float(now)
    key = str(range_ or "").strip().lower()
    since_ts: float | None = None
    until_ts: float | None = None
    label = ""
    if key in _RANGE_KEYS:
        label = key
        # Compute the boundary as NAIVE local wall-clock, then attach the offset for the boundary's
        # OWN date via .astimezone() — so a start that lands on the other side of a DST switch from
        # `now` gets the right offset (no fixed-offset skew).
        start = datetime.fromtimestamp(now_ts).replace(hour=0, minute=0, second=0, microsecond=0)
        if key == "week":
            start = start - timedelta(days=start.weekday())  # Monday
        elif key == "month":
            start = start.replace(day=1)
        since_ts = start.astimezone().timestamp()
        until_ts = now_ts  # "so far" — never reports the future
    else:
        since_ts = _parse_boundary(since, end=False)
        until_ts = _parse_boundary(until, end=True)
        if since_ts is not None or until_ts is not None:
            label = "custom"
    return {
        "range": label,
        "since_ts": since_ts,
        "until_ts": until_ts,
        "since": _utc_iso(since_ts),
        "until": _utc_iso(until_ts),
    }


def _paid_usd_from_row(usd_actual_sum: float, unpriced_tokens: int, rate: float) -> tuple[float, bool]:
    """Headline paid dollars for one grouped row: exact charges where the provider gave one,
    blended estimate for the rest. Returns (usd, all_actual)."""
    usd = float(usd_actual_sum or 0.0) + int(unpriced_tokens or 0) * rate
    return usd, int(unpriced_tokens or 0) == 0


def usage_by_model(
    *,
    since_ts: float | None = None,
    until_ts: float | None = None,
    now: float | None = None,  # accepted for signature parity with the window helpers
) -> list[dict[str, Any]]:
    """Per-model token totals over an absolute ``[since_ts, until_ts)`` window (either bound
    optional). One row per (provider, model, cost class), sorted by total tokens descending.
    Paid rows carry the same exact-plus-estimate dollar figure as :func:`usage_summary`.
    Fail-soft: any error yields an empty list.
    """
    rate = _usd_per_paid_token()
    rows_out: list[dict[str, Any]] = []
    try:
        clauses: list[str] = []
        params: list[Any] = []
        if since_ts is not None:
            clauses.append("created_ts >= ?")
            params.append(float(since_ts))
        if until_ts is not None:
            clauses.append("created_ts < ?")
            params.append(float(until_ts))
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

        from storage.db import get_connection

        conn = get_connection()
        try:
            _ensure_schema(conn)
            rows = conn.execute(
                f"""
                SELECT provider_id, model_id, cost_class,
                       COUNT(*)                        AS responses,
                       COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                       COALESCE(SUM(output_tokens), 0) AS output_tokens,
                       COALESCE(SUM(usd_actual), 0.0)  AS usd_actual_sum,
                       COALESCE(SUM(CASE WHEN usd_actual IS NULL THEN prompt_tokens + output_tokens ELSE 0 END), 0) AS unpriced_tokens
                FROM token_usage
                {where}
                GROUP BY provider_id, model_id, cost_class
                """,
                tuple(params),
            ).fetchall()
        finally:
            conn.close()

        merged: dict[tuple[str, str, str], dict[str, Any]] = {}
        for row in rows:
            cc = _reported_row_cost_class(row[2], provider_id=row[0], model_id=row[1])
            pt = int(row[4] or 0)
            ot = int(row[5] or 0)
            entry: dict[str, Any] = {
                "provider_id": str(row[0] or ""),
                "model_id": str(row[1] or ""),
                "cost_class": cc,
                "responses": int(row[3] or 0),
                "prompt_tokens": pt,
                "output_tokens": ot,
                "total_tokens": pt + ot,
            }
            if cc == COST_PAID_CLOUD:
                usd, all_actual = _paid_usd_from_row(float(row[6] or 0.0), int(row[7] or 0), rate)
                entry["usd"] = round(usd, 6)
                entry["all_actual"] = all_actual
            key = (entry["provider_id"], entry["model_id"], entry["cost_class"])
            prior = merged.get(key)
            if prior is None:
                merged[key] = entry
                continue
            for field in ("responses", "prompt_tokens", "output_tokens", "total_tokens"):
                prior[field] += entry[field]
            if cc == COST_PAID_CLOUD:
                prior["usd"] = round(float(prior.get("usd") or 0.0) + float(entry.get("usd") or 0.0), 6)
                prior["all_actual"] = bool(prior.get("all_actual")) and bool(entry.get("all_actual"))
        rows_out.extend(merged.values())
        rows_out.sort(key=lambda e: e["total_tokens"], reverse=True)
    except Exception as exc:  # pragma: no cover - defensive; metering never breaks a turn
        logger.debug("usage_by_model failed (%s); returning empty list", exc)
    return rows_out


def last_served_call() -> dict[str, Any] | None:
    """The most recent model response this runtime actually served, or ``None`` if none ever was.

    Every served response is metered here with the provider and model that produced it, so this
    table is the durable evidence for "which lane answered that turn?" — the same fact the chat
    footer renders per turn, but readable AFTER the turn, from any thread. ``core.memory_first_router``
    keeps the live figure on a thread-local that is reset at the start of every turn, so it is empty
    by construction on a fast-path turn that answers without calling a model; only this row survives.

    A fast path answering from runtime state records nothing, so the row returned is the last real
    model call — which is exactly what "did a cloud model answer that?" is asking about.

    Returns ``{provider_id, model_id, cost_class, lane, created_at, prompt_tokens, output_tokens}``
    where ``lane`` is ``"cloud"`` or ``"local"``. Fail-soft: any error yields ``None``.
    """
    try:
        from storage.db import get_connection

        conn = get_connection()
        try:
            _ensure_schema(conn)
            row = conn.execute(
                """
                SELECT provider_id, model_id, cost_class, created_at, prompt_tokens, output_tokens
                FROM token_usage
                ORDER BY created_ts DESC
                LIMIT 1
                """
            ).fetchone()
        finally:
            conn.close()
    except Exception as exc:  # pragma: no cover - defensive; metering never breaks a turn
        logger.debug("last_served_call failed (%s); reporting no evidence", exc)
        return None
    if not row:
        return None
    provider_id = str(row[0] or "")
    model_id = str(row[1] or "")
    cost_class = _reported_row_cost_class(str(row[2] or ""), provider_id=provider_id, model_id=model_id)
    return {
        "provider_id": provider_id,
        "model_id": model_id,
        "cost_class": cost_class,
        "lane": "local" if cost_class == COST_FREE_LOCAL else "cloud",
        "created_at": str(row[3] or ""),
        "prompt_tokens": int(row[4] or 0),
        "output_tokens": int(row[5] or 0),
    }


def usage_summary(
    *,
    window_seconds: float | None = None,
    since_ts: float | None = None,
    until_ts: float | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Aggregate token usage by cost class over an optional trailing window.

    Returns a dict with a per-cost-class breakdown plus a paid-side dollar ESTIMATE::

        {
          "since": <iso or None>,
          "free_local":     {responses, prompt_tokens, output_tokens, total_tokens},
          "free_cloud":     {responses, prompt_tokens, output_tokens, total_tokens},
          "paid_cloud":     {..., "usd_estimate": float},
          "remote_unknown": {...},
          "total_tokens": int,
          "usd_per_paid_token": float,
        }

    Fail-soft: any error yields an all-zero summary rather than raising.
    """
    summary: dict[str, Any] = {
        "since": None,
        COST_FREE_LOCAL: _empty_bucket(),
        COST_FREE_CLOUD: _empty_bucket(),
        COST_PAID_CLOUD: {**_empty_bucket(), "usd_estimate": 0.0, "usd_actual": 0.0, "usd": 0.0, "all_actual": True},
        COST_REMOTE_UNKNOWN: _empty_bucket(),
        "total_tokens": 0,
        "calls_missing_usage": 0,
        "usd_per_paid_token": _usd_per_paid_token(),
    }
    try:
        # Absolute [since_ts, until_ts) bounds win when given; otherwise a trailing window.
        clauses: list[str] = []
        params_list: list[Any] = []
        if since_ts is not None or until_ts is not None:
            if since_ts is not None:
                clauses.append("created_ts >= ?")
                params_list.append(float(since_ts))
                summary["since"] = _utc_iso(float(since_ts))
            if until_ts is not None:
                clauses.append("created_ts < ?")
                params_list.append(float(until_ts))
                summary["until"] = _utc_iso(float(until_ts))
        elif window_seconds is not None and window_seconds > 0:
            cutoff = (_now_ts() if now is None else float(now)) - float(window_seconds)
            clauses.append("created_ts >= ?")
            params_list.append(cutoff)
            summary["since"] = _utc_iso(cutoff)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params: tuple[Any, ...] = tuple(params_list)

        from storage.db import get_connection

        conn = get_connection()
        try:
            _ensure_schema(conn)
            rows = conn.execute(
                f"""
                SELECT provider_id, model_id, cost_class,
                       COUNT(*)                     AS responses,
                       COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                       COALESCE(SUM(output_tokens), 0) AS output_tokens,
                       COALESCE(SUM(usd_actual), 0.0) AS usd_actual_sum,
                       COALESCE(SUM(CASE WHEN usd_actual IS NULL THEN prompt_tokens + output_tokens ELSE 0 END), 0) AS unpriced_tokens,
                       COALESCE(SUM(CASE WHEN prompt_tokens_reported = 0 OR output_tokens_reported = 0 THEN 1 ELSE 0 END), 0) AS calls_missing_usage
                FROM token_usage
                {where}
                GROUP BY provider_id, model_id, cost_class
                """,
                params,
            ).fetchall()
        finally:
            conn.close()

        total = 0
        missing_usage_total = 0
        for row in rows:
            cc = _reported_row_cost_class(row[2], provider_id=row[0], model_id=row[1])
            pt = int(row[4] or 0)
            ot = int(row[5] or 0)
            missing_usage_total += int(row[8] or 0)
            bucket = summary[cc]
            bucket["responses"] += int(row[3] or 0)
            bucket["prompt_tokens"] += pt
            bucket["output_tokens"] += ot
            bucket["total_tokens"] += pt + ot
            bucket["calls_missing_usage"] += int(row[8] or 0)
            total += pt + ot
            if cc == COST_PAID_CLOUD:
                usd_actual_sum = float(row[6] or 0.0)
                unpriced = int(row[7] or 0)
                bucket["usd_actual"] += usd_actual_sum
                # Headline: exact charges where the provider gave us one, blended estimate
                # for the rest (rows with no usd_actual).
                bucket["usd"] += usd_actual_sum + unpriced * summary["usd_per_paid_token"]
                if unpriced > 0:
                    bucket["all_actual"] = False
        summary["total_tokens"] = total
        summary["calls_missing_usage"] = missing_usage_total
        paid = summary[COST_PAID_CLOUD]
        paid["usd_estimate"] = round(paid["total_tokens"] * summary["usd_per_paid_token"], 6)
        paid["usd_actual"] = round(paid["usd_actual"], 6)
        paid["usd"] = round(paid["usd"], 6)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("usage_meter summary failed (%s); returning empty summary", exc)
    return summary


def usage_notices(
    *, summary: dict[str, Any] | None = None, window_seconds: float | None = None, now: float | None = None
) -> list[str]:
    """Provider usage and monetary spend notices; reservation counts are not a quota."""
    notices: list[str] = []
    try:
        _s = summary if summary is not None else usage_summary(window_seconds=window_seconds, now=now)
        paid = _s.get(COST_PAID_CLOUD, {})
        pt = int(paid.get("total_tokens") or 0)
        if pt > 0:
            usd = float(paid.get("usd") or 0.0)
            money = f"${usd:.4f}" if paid.get("all_actual") else f"~${usd:.4f}"
            notices.append(f"Cloud spend so far: {money} ({pt:,} paid tokens).")
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("usage_notices spend line failed (%s)", exc)
    return notices


def format_report(summary: dict[str, Any] | None = None) -> str:
    """Human-readable local/free-cloud/paid-cloud token report (CLI / text surface)."""
    s = summary if summary is not None else usage_summary()
    local = s.get(COST_FREE_LOCAL, {})
    free_cloud = s.get(COST_FREE_CLOUD, {})
    paid = s.get(COST_PAID_CLOUD, {})
    unknown = s.get(COST_REMOTE_UNKNOWN, {})
    total = int(s.get("total_tokens") or 0)
    lt, ft, pt, ut = (int(b.get("total_tokens") or 0) for b in (local, free_cloud, paid, unknown))
    exact = bool(paid.get("all_actual")) and pt > 0
    usd = float(paid.get("usd") if exact else paid.get("usd_estimate") or 0.0)
    money = f"${usd:.6f} actual" if exact else f"~${usd:.6f} est."
    local_pct = round(100.0 * lt / total, 1) if total else 0.0
    lines = [
        "VOOL token usage",
        f"  Local (free):  {lt:>10,} tokens  ({int(local.get('responses') or 0)} responses)",
        f"  Cloud (free):  {ft:>10,} tokens  ({int(free_cloud.get('responses') or 0)} responses)",
        f"  Cloud (paid):  {pt:>10,} tokens  ({int(paid.get('responses') or 0)} responses)  {money}",
    ]
    if ut:
        lines.append(f"  Other remote:  {ut:>10,} tokens  ({int(unknown.get('responses') or 0)} responses)")
    lines.append(f"  Total:         {total:>10,} tokens")
    lines.append(
        f"  -> {local_pct:.1f}% of your tokens ran free on local." if total else "  -> No usage recorded yet."
    )
    for notice in usage_notices(summary=s):
        lines.append(f"  * {notice}")
    return "\n".join(lines)


__all__ = [
    "COST_FREE_CLOUD",
    "COST_FREE_LOCAL",
    "COST_PAID_CLOUD",
    "COST_REMOTE_UNKNOWN",
    "format_report",
    "last_served_call",
    "record_usage",
    "resolve_window",
    "usage_by_model",
    "usage_notices",
    "usage_summary",
]


if __name__ == "__main__":  # pragma: no cover - manual CLI report
    print(format_report())
