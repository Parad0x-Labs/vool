"""Operator utility over the Liquefy cold-log projection (C11).

Laws this module implements (dossier 2026-08-31 §36; lane FLASH_LIQUEFY_OPERATOR_UTILITY):

- AUTHORITY: the hash-chained Blackbox journal stays the only authority. Every
  function here reads the journal and writes only the additive projection;
  rebuild never mutates the journal, and a projection failure never touches it.
- BOUNDED: every listing/search takes an explicit limit, reports how many events
  were scanned, and states truncation truth (`truncated`/`has_more`) instead of
  implying completeness.
- TYPED: filters are typed fields (time bounds, source, kind, event/turn/
  session/trace ids, outcome class) — never a raw query language.
- TRUTHFUL RESULT ENVELOPE: every result carries source tier (hot/cold),
  event identity (seq + ref.event_id), time bounds, limit/truncation truth and
  verification status. Cold reads cross the store's verify-before-serve gates;
  any corruption fails CLOSED (typed error), never a narrowed silent answer.
- REDACTION: all projection writes go through ``LiquefyLogStore.append``, whose
  redaction runs BEFORE persistence — including rebuilt events.
- SINK FAILURE VISIBILITY: projection-health publishes the sink's counted
  failures and the store's corruption receipts so a broken sink is observable
  without breaking the authoritative append.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from core.liquefy import hooks
from core.liquefy.store import LiquefyLogStore, LiquefyStoreError

REBUILD_STATE_SCHEMA = "vool.liquefy.rebuild_state.v1"

#: outcome values that count as a fault for the typed ``fault`` filter
_FAULT_OUTCOMES = frozenset({"failed", "refused", "unknown_crashed", "interrupted", "unknown"})
_DEFAULT_SCAN_LIMIT = 20000


class OperatorInputError(ValueError):
    """A typed filter was malformed (bad time bound, bad limit). Fail typed."""


# --------------------------------------------------------------------------- helpers


def _resolve_store(store: LiquefyLogStore | None) -> LiquefyLogStore:
    resolved = store if store is not None else hooks.get_default_store()
    if resolved is None:
        raise LiquefyStoreError(
            "liquefy projection lane is disabled (VOOL_LIQUEFY_LOGS=0) or its home is unwritable"
        )
    return resolved


def _parse_bound(raw: str, field: str) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise OperatorInputError(f"{field} is not a parsable ISO-8601 timestamp: {raw!r}") from exc
    if parsed.tzinfo is None:
        raise OperatorInputError(f"{field} must carry an explicit UTC offset (got {raw!r})")
    return parsed


def _event_ts(env: dict) -> datetime | None:
    raw = str(env.get("ts") or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _event_identity(env: dict) -> dict:
    ref = env.get("ref") if isinstance(env.get("ref"), dict) else {}
    return {
        "seq": env.get("seq"),
        "event_id": ref.get("event_id") or "",
        "turn_id": ref.get("turn_id") or "",
        "blackbox_seq": ref.get("blackbox_seq"),
        "effect_id": ref.get("effect_id") or "",
        "session_id": env.get("session_id") or "",
        "trace_id": env.get("trace_id") or "",
    }


def _outcome_of(env: dict) -> str:
    payload = env.get("payload") if isinstance(env.get("payload"), dict) else {}
    return str(payload.get("outcome") or "")


def _is_fault(env: dict) -> bool:
    payload = env.get("payload") if isinstance(env.get("payload"), dict) else {}
    if _outcome_of(env) in _FAULT_OUTCOMES:
        return True
    return bool(payload.get("error")) or str(env.get("kind") or "") == "coverage_gap"


# ------------------------------------------------------------------ scan primitive


def _iter_events(store: LiquefyLogStore, *, scan_cap: int = _DEFAULT_SCAN_LIMIT):
    """Yield (tier, segment_id, envelope) oldest-first: cold segments in chain
    order, then unsealed hot beyond the sealed horizon. The cold side crosses
    the receipted verify-before-serve read path — corruption fails closed here,
    which is what makes every downstream result's verification status true.

    The horizon dedup law lives in the store: hot_entries() never returns a
    sealed seq, so a cursor-overlap event is served exactly once.
    """
    yielded = 0
    for row in store.segments():
        for record in store._segment_records_receipted(row["segment_id"]):
            yield "cold", row["segment_id"], record
            yielded += 1
            if yielded >= scan_cap:
                return
    for env in store.hot_entries():
        yield "hot", None, env
        yielded += 1
        if yielded >= scan_cap:
            return


def _iter_events_scoped(store: LiquefyLogStore, filters: dict, *, scan_cap: int = _DEFAULT_SCAN_LIMIT):
    """Typed-filter scan over _iter_events; returns (matches_iterator, scanned_counter)."""
    time_from = filters.get("time_from")
    time_to = filters.get("time_to")
    skipped_time = {"n": 0}

    def _matches(tier: str, segment_id: str | None, env: dict) -> bool:
        if time_from is not None or time_to is not None:
            ts = _event_ts(env)
            if ts is None:
                skipped_time["n"] += 1
                return False
            if time_from is not None and ts < time_from:
                return False
            if time_to is not None and ts > time_to:
                return False
        if filters.get("source") and env.get("source") != filters["source"]:
            return False
        if filters.get("kind") and env.get("kind") != filters["kind"]:
            return False
        ref = env.get("ref") if isinstance(env.get("ref"), dict) else {}
        if filters.get("event_id") and ref.get("event_id") != filters["event_id"]:
            return False
        if filters.get("session_id") and env.get("session_id") != filters["session_id"]:
            return False
        if filters.get("trace_id") and env.get("trace_id") != filters["trace_id"]:
            return False
        if filters.get("turn_id") and ref.get("turn_id") != filters["turn_id"]:
            return False
        if filters.get("outcome") and _outcome_of(env) != filters["outcome"]:
            return False
        if filters.get("fault") == "any" and not _is_fault(env):
            return False
        if filters.get("text") and filters["text"] not in json.dumps(env, ensure_ascii=False):  # noqa: SIM103
            # Kept as the last arm of a guard chain: every other filter reads the same way,
            # and collapsing only this one into `return not (...)` breaks that symmetry.
            return False
        return True

    def _gen():
        scanned = 0
        for tier, segment_id, env in _iter_events(store, scan_cap=scan_cap):
            scanned += 1  # noqa: SIM113 - enumerate() would rebind per-iteration; this counter
            # is closed over by the generator and yielded, which is the scan-truth contract.
            if _matches(tier, segment_id, env):
                yield tier, segment_id, env, scanned
        yield from ()  # generator ends; scanned is closed over per-generator

    return _gen(), skipped_time


# --------------------------------------------------------------------------- health


def projection_health(
    *,
    store: LiquefyLogStore | None = None,
    blackbox: Any = None,
    deep: bool = False,
    receipt_tail: int = 5,
    requester: str = "operator",
) -> dict:
    """Bounded projection + journal health for the operator.

    Publishes the sink failure count and corruption receipts — a projection
    sink that keeps failing is VISIBLE here while the journal keeps appending.
    """
    resolved = _resolve_store(store)
    stats = resolved.stats()
    segments = resolved.segments()
    verify_report = resolved.verify(deep=bool(deep))

    journal_report: dict[str, Any] | None = None
    journal_entries: int | None = None
    sink_wired: bool | None = None
    if blackbox is None:
        try:
            from core.blackbox.store import default_store as blackbox_default_store

            blackbox = blackbox_default_store()
        except Exception:
            blackbox = None
    if blackbox is not None:
        report = blackbox.verify()
        journal_report = {
            "ok": bool(report.ok),
            "reason": report.reason,
            "entries": report.entries,
            "first_bad_seq": report.first_bad_seq,
        }
        journal_entries = report.entries
        sink_wired = getattr(blackbox, "liquefy_sink", None) is not None

    corruption_receipts = _receipt_lines(resolved, "corrupt", tail=receipt_tail)
    hot_min = stats.get("hot_min_seq")
    cold_first = segments[0]["first_seq"] if segments else None
    cold_last = segments[-1]["last_seq"] if segments else None
    return {
        "lane": {
            "enabled": hooks.enabled(),
            "root": str(resolved.root),
            "default_root": hooks.default_root(),
        },
        "authority": {
            "journal_is_authority": True,
            "projection_is_authority": False,
            "journal_entries": journal_entries,
            "journal_chain": journal_report,
        },
        "tiers": {
            "cold": {
                "segments": len(segments),
                "events": sum(int(row.get("last_seq", 0)) - int(row.get("first_seq", 0)) + 1 for row in segments),
                "first_seq": cold_first,
                "last_seq": cold_last,
                "chain_tip": stats.get("chain_tip", ""),
            },
            "hot": {
                "events": stats.get("hot_events", 0),
                "first_seq": hot_min,
                "last_seq": stats.get("hot_max_seq"),
            },
            "last_sealed_seq": stats.get("last_sealed_seq", 0),
        },
        "segments": [
            {"segment_id": row.get("segment_id"), "first_seq": row.get("first_seq"), "last_seq": row.get("last_seq")}
            for row in segments
        ],
        "verification": {
            "status": "verified" if verify_report.get("ok") else "failed",
            "fail_closed": True,
            "store_report": verify_report,
            "deep": bool(deep),
        },
        "sink": {
            "wired": sink_wired,
            "failure_count": hooks.failure_count(),
            "note": "projection failures are counted, never propagated to the journal append",
        },
        "receipts": {
            "corruption": corruption_receipts,
            "restore_lines": _receipt_line_count(resolved, "restore.jsonl"),
            "erasure_lines": _receipt_line_count(resolved, "erasure.jsonl"),
            "hot_torn_quarantined": len(list((resolved.receipts_dir).glob("hot-torn-*.jsonl"))),
        },
    }


def _receipt_lines(store: LiquefyLogStore, kind: str, *, tail: int) -> list[dict]:
    rows: list[dict] = []
    for path in sorted(store.receipts_dir.glob("*.corrupt.json")):
        try:
            for line in path.read_text().splitlines():
                if not line.strip():
                    continue
                parsed = json.loads(line)
                rows.append(dict(parsed) if isinstance(parsed, dict) else {"unparsable": str(parsed)})
        except (ValueError, OSError):
            rows.append({"segment_id": path.stem, "unparsable": True})
    return rows[-tail:]


def _receipt_line_count(store: LiquefyLogStore, name: str) -> int:
    import contextlib

    path = store.receipts_dir / name
    if not path.exists():
        return 0
    with contextlib.suppress(OSError):
        return sum(1 for line in path.read_text().splitlines() if line.strip())
    return 0


# --------------------------------------------------------------------------- search


def search_events(
    *,
    store: LiquefyLogStore | None = None,
    time_from: str = "",
    time_to: str = "",
    source: str = "",
    kind: str = "",
    event_id: str = "",
    session_id: str = "",
    trace_id: str = "",
    turn_id: str = "",
    outcome: str = "",
    fault: str = "",
    text: str = "",
    limit: int = 50,
    scan_cap: int = _DEFAULT_SCAN_LIMIT,
    requester: str = "operator",
) -> dict:
    """Typed, bounded search across hot + cold WITHOUT a query language.

    Every filter is a named field. The result envelope carries the applied time
    bounds, per-event source tier, limit/truncation truth and the verification
    status of every segment a result was served from.
    """
    resolved = _resolve_store(store)
    limit = int(limit)
    if limit < 1:
        raise OperatorInputError("limit must be >= 1")
    filters = {
        "time_from": _parse_bound(time_from, "time_from"),
        "time_to": _parse_bound(time_to, "time_to"),
        "source": str(source or "").strip(),
        "kind": str(kind or "").strip(),
        "event_id": str(event_id or "").strip(),
        "session_id": str(session_id or "").strip(),
        "trace_id": str(trace_id or "").strip(),
        "turn_id": str(turn_id or "").strip(),
        "outcome": str(outcome or "").strip(),
        "fault": str(fault or "").strip(),
        "text": str(text or ""),
    }
    if filters["fault"] and filters["fault"] != "any":
        raise OperatorInputError("fault filter accepts 'any' (use outcome=<code> for an exact outcome)")

    matches, skipped_time = _iter_events_scoped(resolved, filters, scan_cap=scan_cap)
    events: list[dict] = []
    truncated = False
    segments_served: list[str] = []
    scanned = 0
    for tier, segment_id, env, scanned in matches:  # noqa: B007 - `scanned` is read AFTER the
        # loop for scanned_events / scan_cap_reached; renaming it would silently drop the
        # truncation disclosure that sabotage S7 protects.
        if len(events) >= limit:
            truncated = True
            break
        identity = _event_identity(env)
        events.append(
            {
                "seq": env.get("seq"),
                "ts": env.get("ts"),
                "kind": env.get("kind"),
                "source": env.get("source"),
                "tier": tier,
                "segment_id": segment_id,
                "outcome": _outcome_of(env),
                "redactions": env.get("redactions") or {},
                "event_id": identity["event_id"],
                "turn_id": identity["turn_id"],
                "session_id": identity["session_id"],
                "trace_id": identity["trace_id"],
                "payload": env.get("payload"),
            }
        )
        if segment_id and segment_id not in segments_served:
            segments_served.append(segment_id)

    served_ts = [event["ts"] for event in events if event.get("ts")]
    return {
        "query": {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in filters.items()},
        "time_bounds": {
            "from": filters["time_from"].isoformat() if filters["time_from"] else "",
            "to": filters["time_to"].isoformat() if filters["time_to"] else "",
            "oldest_served_ts": min(served_ts) if served_ts else "",
            "newest_served_ts": max(served_ts) if served_ts else "",
            "events_skipped_unparsable_ts": skipped_time["n"],
        },
        "limit": limit,
        "returned": len(events),
        "truncated": truncated,
        "has_more": truncated,
        "scanned_events": scanned,
        "scan_cap": scan_cap,
        "scan_cap_reached": scanned >= scan_cap,
        "verification": {
            "status": "verified",
            "fail_closed": True,
            "segments_served_from": segments_served,
            "note": "cold reads crossed verify-before-serve; any corruption raises instead of narrowing results",
        },
        "events": events,
    }


# ------------------------------------------------------------------------ retrieval


def retrieve_event(
    *,
    event_id: str = "",
    seq: int = 0,
    session_id: str = "",
    trace_id: str = "",
    turn_id: str = "",
    store: LiquefyLogStore | None = None,
    scan_cap: int = _DEFAULT_SCAN_LIMIT,
    requester: str = "operator",
) -> dict:
    """Resolve ONE referenced event to its exact projection record.

    Tiers and verification semantics match search_events. Raises
    ``LookupError`` when no reference resolves so callers fail typed, not vague.
    """
    resolved = _resolve_store(store)
    if seq:
        env = resolved.read(int(seq), requester=requester)  # cold read writes a restore receipt
        tier, segment_id = _tier_for_seq(resolved, int(seq))
        return _retrieved(env, tier, segment_id)
    refs = {
        "event_id": str(event_id or "").strip(),
        "session_id": str(session_id or "").strip(),
        "trace_id": str(trace_id or "").strip(),
        "turn_id": str(turn_id or "").strip(),
    }
    given = {k: v for k, v in refs.items() if v}
    if not given:
        raise OperatorInputError("retrieve_event needs seq or one of event_id/session_id/trace_id/turn_id")
    filters = {**given, "time_from": None, "time_to": None}
    matches, _ = _iter_events_scoped(resolved, filters, scan_cap=scan_cap)
    for tier, segment_id, env, _scanned in matches:
        return _retrieved(env, tier, segment_id)
    raise LookupError(f"no projection event resolves {given}")


def _tier_for_seq(store: LiquefyLogStore, seq: int) -> tuple[str, str | None]:
    for row in store.segments():
        if int(row.get("first_seq", 0)) <= seq <= int(row.get("last_seq", 0)):
            return "cold", row.get("segment_id")
    return "hot", None


def _retrieved(env: dict, tier: str, segment_id: str | None) -> dict:
    identity = _event_identity(env)
    return {
        "found": True,
        "tier": tier,
        "segment_id": segment_id,
        "verification": {
            "status": "verified",
            "fail_closed": True,
            "note": "cold retrievals verify both hashes before serve and persist a restore receipt",
        },
        "event": {
            "seq": env.get("seq"),
            "ts": env.get("ts"),
            "kind": env.get("kind"),
            "source": env.get("source"),
            "outcome": _outcome_of(env),
            "redactions": env.get("redactions") or {},
            "event_id": identity["event_id"],
            "turn_id": identity["turn_id"],
            "blackbox_seq": identity["blackbox_seq"],
            "session_id": identity["session_id"],
            "trace_id": identity["trace_id"],
            "payload": env.get("payload"),
        },
    }


# --------------------------------------------------------------------------- verify


def verify_segments(
    *,
    segment_id: str = "",
    deep: bool = False,
    column: str = "",
    store: LiquefyLogStore | None = None,
    requester: str = "operator",
) -> dict:
    """Verify one segment (header + chain position + optional deep blob read +
    optional PCC disclosure proof) or the whole sealed chain."""
    resolved = _resolve_store(store)
    target = str(segment_id or "").strip()
    if target:
        header = resolved.segment_header(target)  # SegmentMissingError / SegmentCorruptError fail closed
        result: dict[str, Any] = {
            "segment_id": target,
            "header": {
                k: header.get(k)
                for k in (
                    "schema", "canonical_form", "segment_id", "first_seq", "last_seq", "event_count",
                    "original_sha256", "blob_sha256", "codec", "encrypted", "retention_class", "sealed_at",
                    "prev_segment_hash",
                )
            },
        }
        if column:
            evidence = resolved.disclosure_evidence(target, str(column))
            result["disclosure"] = evidence
        if deep:
            records = resolved._segment_records_receipted(target)
            result["deep_read"] = {"events": len(records)}
            if column:
                values = [record.get(str(column)) for record in records]
                result["disclosure_verified"] = resolved.verify_claimed_column(target, str(column), values)
        chain = resolved.verify(deep=False)
        in_chain = any(row.get("segment_id") == target for row in resolved.segments())
        result["chain_contains_segment"] = in_chain
        result["chain_report"] = chain
        result["verification"] = {
            "status": "verified" if in_chain and chain.get("ok") else "failed",
            "fail_closed": True,
        }
        return result
    report = resolved.verify(deep=bool(deep))
    return {
        "scope": "chain",
        "verification": {
            "status": "verified" if report.get("ok") else "failed",
            "fail_closed": True,
        },
        "report": report,
        "segments": [row.get("segment_id") for row in resolved.segments()],
    }


# --------------------------------------------------------------------------- rebuild


def rebuild_projection(
    *,
    blackbox: Any = None,
    store: LiquefyLogStore | None = None,
    batch_size: int = 200,
    max_entries: int = 0,
    resume: bool = True,
    cancel: Callable[[], bool] | None = None,
    requester: str = "operator",
) -> dict:
    """Rebuild the projection from the authoritative journal.

    - READ-ONLY over the journal: the journal is never written here. Its bytes
      before and after are the caller's to verify.
    - IDEMPOTENT: an entry already projected (by ref.blackbox_seq) is skipped;
      a re-run appends nothing.
    - RESUMABLE: per-batch state (last ingested journal seq) survives restarts;
      the dedup set is the second, independent guard.
    - BOUNDED: ``max_entries`` caps journal entries examined per invocation;
      ``cancel`` (checked between batches) stops cleanly with a resume point.
    """
    from core.liquefy.hooks import blackbox_entry_to_event

    resolved = _resolve_store(store)
    if blackbox is None:
        from core.blackbox.store import default_store as blackbox_default_store

        blackbox = blackbox_default_store()
    if int(batch_size) < 1:
        raise OperatorInputError("batch_size must be >= 1")
    batch_size = int(batch_size)
    max_entries = max(0, int(max_entries))

    entries = blackbox.entries()  # authority read; never written back
    existing = _existing_blackbox_seqs(resolved)

    state_path = resolved.root / "rebuild_state.json"
    resume_from = -1
    if resume and state_path.exists():
        try:
            resume_from = int(json.loads(state_path.read_text()).get("last_blackbox_seq") or -1)
        except (ValueError, OSError):
            resume_from = -1

    appended = 0
    skipped_existing = 0
    skipped_resumed = 0
    examined = 0
    batches = 0
    batch: list[dict] = []
    last_ingested = resume_from
    status = "complete"

    for entry in entries:
        entry_seq = int(entry.get("seq") or 0)
        if entry_seq <= resume_from:
            skipped_resumed += 1
            continue
        examined += 1
        if entry_seq in existing:
            skipped_existing += 1
            last_ingested = max(last_ingested, entry_seq)
            continue
        batch.append(blackbox_entry_to_event(entry))
        last_ingested = max(last_ingested, entry_seq)
        if len(batch) >= batch_size:
            resolved.append(batch)  # redaction happens inside append, before persistence
            appended += len(batch)
            batches += 1
            batch = []
            _write_rebuild_state(state_path, last_ingested)
            if cancel is not None and cancel():
                status = "cancelled"
                break
        if max_entries and examined >= max_entries:
            status = "bounded"
            break
    if batch and status in ("complete", "cancelled", "bounded"):
        # Everything examined is projected before any exit — bounded and
        # cancelled runs leave no half-examined gap behind.
        resolved.append(batch)
        appended += len(batch)
        batches += 1
        batch = []
    if status in ("complete", "cancelled", "bounded"):
        _write_rebuild_state(state_path, last_ingested)

    _append_receipt_line(
        resolved.receipts_dir / "rebuild.jsonl",
        {
            "action": "rebuild_from_journal",
            "status": status,
            "journal_entries": len(entries),
            "examined": examined,
            "appended": appended,
            "skipped_already_projected": skipped_existing,
            "skipped_resumed": skipped_resumed,
            "batches": batches,
            "last_blackbox_seq": last_ingested,
            "requester": requester,
        },
    )
    return {
        "status": status,
        "journal_entries": len(entries),
        "examined": examined,
        "appended": appended,
        "skipped_already_projected": skipped_existing,
        "skipped_resumed": skipped_resumed,
        "batches": batches,
        "last_blackbox_seq": last_ingested,
        "journal_unchanged_witness": blackbox.verify().ok,
        "verification": resolved.verify(deep=False),
    }


def _existing_blackbox_seqs(store: LiquefyLogStore) -> set[int]:
    """Every journal seq already projected (cold + hot). The dedup boundary."""
    seqs: set[int] = set()
    for _tier, _segment_id, env in _iter_events(store):
        ref = env.get("ref") if isinstance(env.get("ref"), dict) else {}
        raw = ref.get("blackbox_seq")
        if raw is None:
            continue
        try:
            seqs.add(int(raw))
        except (TypeError, ValueError):
            continue
    return seqs


def _write_rebuild_state(path: Path, last_blackbox_seq: int) -> None:
    tmp = path.with_name(f".{path.name}.tmp-{id(path)}")
    tmp.write_text(
        json.dumps(
            {
                "schema": REBUILD_STATE_SCHEMA,
                "last_blackbox_seq": int(last_blackbox_seq),
                "updated_at": _utcnow_iso(),
            },
            sort_keys=True,
        )
    )
    tmp.replace(path)


def _utcnow_iso() -> str:
    from datetime import timezone

    return datetime.now(timezone.utc).isoformat()


def _append_receipt_line(path: Path, receipt: dict) -> None:
    import os

    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, json.dumps(receipt, ensure_ascii=False).encode("utf-8") + b"\n")
        os.fsync(fd)
    finally:
        os.close(fd)


# --------------------------------------------------------------------------- restore


def restore_projection(
    *,
    store: LiquefyLogStore | None = None,
    requester: str = "operator",
) -> dict:
    """Restore/recover per the store's cursor laws (read-only over the journal).

    - a torn hot tail is quarantined verbatim and the good prefix kept
      (constructor repair — a fresh store instance is opened here);
    - an orphan segment (crash between segment write and cursor advance) is
      re-sealed: the content-derived id overwrites the orphan atomically;
    - the sealed chain is verified afterwards and a deterministic export
      digest is reported so a before/after byte-exact comparison is possible.
    """
    root = (_resolve_store(store)).root
    torn_before = sorted(p.name for p in (root / "receipts").glob("hot-torn-*.jsonl"))
    fresh = LiquefyLogStore(root)  # constructor runs the cursor load + hot-tail repair
    torn_after = sorted(p.name for p in (root / "receipts").glob("hot-torn-*.jsonl"))
    quarantined = [name for name in torn_after if name not in torn_before]

    known = {row.get("segment_id") for row in fresh.segments()}
    orphans = sorted(
        path.stem for path in sorted((root / "segments").glob("*.lsegh")) if path.stem not in known
    )
    resealed: dict | None = None
    if orphans:
        # The cursor law: hot still holds the unsealed truth; a re-seal overwrites
        # the orphan under the same content-derived id.
        resealed_raw = fresh.seal()
        resealed = dict(resealed_raw.__dict__) if resealed_raw is not None else None

    report = fresh.verify(deep=True)
    export_path = root / "receipts" / "restore-export.jsonl"
    exported = fresh.export(export_path)
    export_sha = hashlib.sha256(export_path.read_bytes()).hexdigest()

    return {
        "root": str(root),
        "repaired_hot_tail": bool(quarantined),
        "quarantined_tails": quarantined,
        "orphan_segments": orphans,
        "resealed": resealed,
        "verification": {
            "status": "verified" if report.get("ok") else "failed",
            "fail_closed": True,
        },
        "verify_report": report,
        "export": {"events": exported, "sha256": export_sha, "path": str(export_path)},
        "stats": fresh.stats(),
    }


__all__ = [
    "OperatorInputError",
    "projection_health",
    "rebuild_projection",
    "restore_projection",
    "retrieve_event",
    "search_events",
    "verify_segments",
]
