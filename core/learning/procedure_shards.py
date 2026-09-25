from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from core.runtime_paths import data_path

from .policy import LearningPolicy

#: Serialized schema tag. v3 semantics (amendment round): v1 files (no ``status`` /
#: ``schema_version``) load as ``legacy_unvalidated`` — preserved history, never trusted reuse;
#: current-version creation defaults to candidate; expiry fails closed.
SCHEMA_TAG = "vool.procedure_shard.v3"
SCHEMA_VERSION = 3
LEGACY_SCHEMA_TAG = "vool.procedure_shard.v1"

_STORE_LOCK = threading.RLock()

_VALID_STATUSES = {
    LearningPolicy.STATUS_CANDIDATE,
    LearningPolicy.STATUS_PROMOTED,
    LearningPolicy.STATUS_DEMOTED,
    LearningPolicy.STATUS_LEGACY_UNVALIDATED,
}


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(value: str) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _cost_or_none(value: Any) -> float | None:
    """Unknown cost must stay unknown. A stored 0.0 was never a measurement (the v2 writer
    defaulted it), so it loads back as ``None`` rather than a fabricated zero."""
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0.0 else None


@dataclass(frozen=True)
class ProcedureShardV1:
    procedure_id: str
    task_class: str
    title: str
    preconditions: tuple[str, ...]
    steps: tuple[str, ...]
    tool_receipts: tuple[dict[str, Any], ...]
    validation: dict[str, Any]
    rollback: dict[str, Any]
    privacy_class: str
    shareability: str
    success_signal: str
    liquefy_bundle_ref: str = ""
    reuse_count: int = 0
    verified_reuse_count: int = 0
    last_reused_at: str = ""
    last_reuse_task_class: str = ""
    last_reuse_outcome: str = ""
    created_at: str = field(default_factory=_utcnow_iso)
    # --- lifecycle, scope, evidence, corrections (defaulted for older-file compat) ---
    schema_version: int = SCHEMA_VERSION
    status: str = LearningPolicy.STATUS_CANDIDATE
    owner_scope: str = LearningPolicy.OWNER_SCOPE_LOCAL
    origin_session_id: str = ""
    evidence: tuple[dict[str, Any], ...] = ()
    promoted_at: str = ""
    expires_at: str = ""
    demoted_reason: str = ""
    demoted_at: str = ""
    corrections: tuple[dict[str, Any], ...] = ()
    consecutive_unverified_reuses: int = 0
    estimated_cost_usd: float | None = None
    # --- v3: typed terminal feedback + explicit migration provenance ---
    consecutive_failures: int = 0
    failure_count: int = 0
    last_terminal_outcome: str = ""
    last_terminal_at: str = ""
    last_terminal_detail: str = ""
    migrated_from: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["schema"] = SCHEMA_TAG
        payload["preconditions"] = list(self.preconditions)
        payload["steps"] = list(self.steps)
        payload["tool_receipts"] = [dict(item) for item in self.tool_receipts]
        payload["evidence"] = [dict(item) for item in self.evidence]
        payload["corrections"] = [dict(item) for item in self.corrections]
        return payload

    @property
    def expiry_state(self) -> str:
        """``valid`` / ``expired`` / ``unknown``. Unknown (blank or unparsable) is NOT fresh:
        a promoted shard that cannot prove a live expiry fails closed."""
        raw = str(self.expires_at or "").strip()
        if not raw:
            return "unknown"
        expiry = _parse_iso(raw)
        if expiry is None:
            return "unknown"
        return "expired" if datetime.now(timezone.utc) >= expiry.astimezone(timezone.utc) else "valid"

    @property
    def is_expired(self) -> bool:
        return self.expiry_state == "expired"

    def eligible_for_reuse(self) -> bool:
        """The ranker's gate: only a live, promoted, locally-scoped procedure may be consumed.

        Candidates (one verified execution is not learning), demoted lessons (operator correction,
        unverified-reuse streak, verified failures), legacy-unvalidated records (v1 history that
        never passed this policy's evidence rules) and expired or UNPROVEN-expiry ones are all
        invisible to reuse regardless of how well their text matches the query.
        """
        if self.status != LearningPolicy.STATUS_PROMOTED:
            return False
        if self.expiry_state != "valid":
            return False
        if self.owner_scope != LearningPolicy.OWNER_SCOPE_LOCAL:
            return False
        return self.shareability in {"local_only", "trusted_hive"}

    @classmethod
    def create(
        cls,
        *,
        task_class: str,
        title: str,
        preconditions: list[str] | tuple[str, ...],
        steps: list[str] | tuple[str, ...],
        tool_receipts: list[dict[str, Any]] | tuple[dict[str, Any], ...],
        validation: dict[str, Any],
        rollback: dict[str, Any],
        privacy_class: str,
        shareability: str,
        success_signal: str,
        liquefy_bundle_ref: str = "",
        reuse_count: int = 0,
        verified_reuse_count: int = 0,
        last_reused_at: str = "",
        last_reuse_task_class: str = "",
        last_reuse_outcome: str = "",
        status: str = LearningPolicy.STATUS_CANDIDATE,
        owner_scope: str = LearningPolicy.OWNER_SCOPE_LOCAL,
        origin_session_id: str = "",
        evidence: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
        promoted_at: str = "",
        expires_at: str = "",
        demoted_reason: str = "",
        demoted_at: str = "",
        corrections: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
        consecutive_unverified_reuses: int = 0,
        estimated_cost_usd: float | None = None,
        consecutive_failures: int = 0,
        failure_count: int = 0,
        last_terminal_outcome: str = "",
        last_terminal_at: str = "",
        last_terminal_detail: str = "",
        migrated_from: str = "",
        procedure_id: str = "",
    ) -> ProcedureShardV1:
        clean_status = str(status or "").strip() or LearningPolicy.STATUS_CANDIDATE
        if clean_status not in _VALID_STATUSES:
            clean_status = LearningPolicy.STATUS_DEMOTED
        return cls(
            procedure_id=str(procedure_id or "").strip() or f"procedure-{uuid.uuid4().hex}",
            task_class=str(task_class or "").strip() or "general",
            title=str(title or "").strip(),
            preconditions=tuple(str(item).strip() for item in preconditions if str(item).strip()),
            steps=tuple(str(item).strip() for item in steps if str(item).strip()),
            tool_receipts=tuple(dict(item) for item in tool_receipts if isinstance(item, dict)),
            validation=dict(validation or {}),
            rollback=dict(rollback or {}),
            privacy_class=str(privacy_class or "local_private"),
            shareability=str(shareability or "local_only"),
            success_signal=str(success_signal or "").strip() or "verified_success",
            liquefy_bundle_ref=str(liquefy_bundle_ref or "").strip(),
            reuse_count=max(0, int(reuse_count or 0)),
            verified_reuse_count=max(0, int(verified_reuse_count or 0)),
            last_reused_at=str(last_reused_at or "").strip(),
            last_reuse_task_class=str(last_reuse_task_class or "").strip(),
            last_reuse_outcome=str(last_reuse_outcome or "").strip(),
            created_at=_utcnow_iso(),
            schema_version=SCHEMA_VERSION,
            status=clean_status,
            owner_scope=str(owner_scope or "").strip() or LearningPolicy.OWNER_SCOPE_LOCAL,
            origin_session_id=str(origin_session_id or "").strip(),
            evidence=tuple(dict(item) for item in evidence if isinstance(item, dict)),
            promoted_at=str(promoted_at or "").strip(),
            expires_at=str(expires_at or "").strip(),
            demoted_reason=str(demoted_reason or "").strip(),
            demoted_at=str(demoted_at or "").strip(),
            corrections=tuple(dict(item) for item in corrections if isinstance(item, dict)),
            consecutive_unverified_reuses=max(0, int(consecutive_unverified_reuses or 0)),
            estimated_cost_usd=_cost_or_none(estimated_cost_usd),
            consecutive_failures=max(0, int(consecutive_failures or 0)),
            failure_count=max(0, int(failure_count or 0)),
            last_terminal_outcome=str(last_terminal_outcome or "").strip(),
            last_terminal_at=str(last_terminal_at or "").strip(),
            last_terminal_detail=str(last_terminal_detail or "").strip()[:200],
            migrated_from=str(migrated_from or "").strip(),
        )


def procedures_dir() -> str:
    return str(data_path("learning", "procedures"))


def _record_path(procedure_id: str) -> Path:
    """Only a portable record basename can select a procedure or its lock file."""
    key = str(procedure_id or "").strip()
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", key) is None:
        raise ValueError("invalid procedure identifier")
    path = data_path("learning", "procedures") / f"{key}.json"
    if path.is_symlink() or path.with_suffix(".json.lock").is_symlink():
        raise ValueError("procedure records and locks cannot be symbolic links")
    return path


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write-then-rename so a crash mid-update can never leave a torn shard file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, indent=2, sort_keys=True))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


class _StoreFileLock:
    """Cross-process advisory lock for one shard file.

    In-process mutation is serialized by ``_STORE_LOCK``; the fcntl lock closes the two-daemon /
    daemon+CLI window where two processes would otherwise read the same counters and both write
    their own view, silently losing one update.
    """

    def __init__(self, shard_path: Path) -> None:
        self._lock_path = shard_path.with_suffix(shard_path.suffix + ".lock")
        self._handle: int | None = None

    def __enter__(self) -> _StoreFileLock:
        import fcntl

        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = os.open(str(self._lock_path), os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        fcntl.flock(self._handle, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc: object) -> None:
        import fcntl

        if self._handle is not None:
            fcntl.flock(self._handle, fcntl.LOCK_UN)
            os.close(self._handle)
            self._handle = None


def save_procedure_shard(shard: ProcedureShardV1) -> str:
    path = _record_path(shard.procedure_id)
    with _STORE_LOCK:
        _atomic_write_json(path, shard.to_dict())
    return str(path)


def _read_shard_file(path: Path) -> ProcedureShardV1 | None:
    try:
        if path.is_symlink():
            return None
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    return _procedure_shard_from_payload(payload)


def load_procedure_shards() -> list[ProcedureShardV1]:
    root = data_path("learning", "procedures")
    if not root.exists():
        return []
    shards: list[ProcedureShardV1] = []
    for path in sorted(root.glob("*.json")):
        shard = _read_shard_file(path)
        if shard is not None and shard.procedure_id:
            shards.append(shard)
    return shards


def update_procedure_shard(
    procedure_id: str,
    mutator: Callable[[ProcedureShardV1], ProcedureShardV1 | None],
) -> ProcedureShardV1 | None:
    """Locked read-modify-write for one shard. ``mutator`` returns the next shard, or None to
    leave the file untouched. Returns the persisted shard or None when it does not exist (a
    deleted shard is never resurrected by a late writer)."""
    clean_id = str(procedure_id or "").strip()
    if not clean_id:
        return None
    try:
        path = _record_path(clean_id)
    except ValueError:
        return None
    with _STORE_LOCK, _StoreFileLock(path):
        shard = _read_shard_file(path)
        if shard is None:
            return None
        next_shard = mutator(shard)
        if next_shard is None:
            return shard
        if next_shard.procedure_id != clean_id:
            raise ValueError("a procedure mutation cannot change its record identity")
        _atomic_write_json(path, next_shard.to_dict())
        return next_shard


def find_or_create_procedure_shard(
    procedure_id: str,
    build: Callable[[], ProcedureShardV1],
    mutate: Callable[[ProcedureShardV1], ProcedureShardV1],
) -> tuple[ProcedureShardV1, bool]:
    """One signature-scoped transaction: either the shard exists and ``mutate`` runs inside the
    same lock that read it, or it is created by ``build``. Two processes racing the first
    promotion converge on one shard; the loser's mutation still applies (inside the lock) rather
    than opening a duplicate. ``returns (shard, created)``."""
    clean_id = str(procedure_id or "").strip()
    if not clean_id:
        raise ValueError("find_or_create_procedure_shard requires a procedure id")
    path = _record_path(clean_id)
    with _STORE_LOCK, _StoreFileLock(path):
        existing = _read_shard_file(path)
        if existing is None:
            shard = build()
            if shard.procedure_id != clean_id:
                raise ValueError("a procedure must be created under its own identity")
            _atomic_write_json(path, shard.to_dict())
            _prune_store_locked(keep_ids={clean_id})
            return shard, True
        next_shard = mutate(existing)
        if next_shard.procedure_id != clean_id:
            raise ValueError("a procedure mutation cannot change its record identity")
        _atomic_write_json(path, next_shard.to_dict())
        return next_shard, False


#: Prune priority for capacity bounding: least-trusted state goes first, live promoted last.
_PRUNE_BUCKETS = {
    LearningPolicy.STATUS_LEGACY_UNVALIDATED: 0,
    LearningPolicy.STATUS_CANDIDATE: 1,
    LearningPolicy.STATUS_DEMOTED: 2,
    LearningPolicy.STATUS_PROMOTED: 3,
}


def _prune_store_locked(*, keep_ids: set[str]) -> int:
    """Bound the store to MAX_STORED_PROCEDURES shards (never touching ``keep_ids``). Returns
    the number of records removed. Live promoted shards sort last; within a bucket the oldest
    created_at goes first."""
    root = data_path("learning", "procedures")
    if not root.exists():
        return 0
    entries: list[tuple[int, str, str, Path]] = []
    for path in sorted(root.glob("*.json")):
        shard = _read_shard_file(path)
        if shard is None or not shard.procedure_id or shard.procedure_id in keep_ids:
            continue
        bucket = _PRUNE_BUCKETS.get(shard.status, 1)
        if shard.status == LearningPolicy.STATUS_PROMOTED and shard.expiry_state != "valid":
            bucket = 2  # a promoted shard that cannot prove liveness prunes with the demoted
        entries.append((bucket, shard.created_at or "", shard.procedure_id, path))
    excess = len(entries) - LearningPolicy.MAX_STORED_PROCEDURES + 1
    if excess <= 0:
        return 0
    entries.sort()
    removed = 0
    for _bucket, _created, _pid, path in entries:
        if removed >= excess:
            break
        path.unlink(missing_ok=True)
        path.with_suffix(path.suffix + ".lock").unlink(missing_ok=True)
        removed += 1
    return removed


def record_reuse_terminal(
    *,
    procedure_ids: list[str] | tuple[str, ...],
    task_class: str,
    terminal: str,
    detail: str = "",
) -> list[ProcedureShardV1]:
    """Typed terminal reuse feedback. Closed vocabulary: ``successful`` / ``failed`` /
    ``cancelled`` / ``unvalidated``.

    * ``successful`` — the reuse produced a verified validation signal;
    * ``unvalidated`` — ok but no validation signal (the only negative the existing executor
      caller can emit today);
    * ``failed`` — the lesson was consumed and its validation RAN AND FAILED: disconfirmation,
      demoting after DEMOTE_AFTER_VERIFIED_REUSE_FAILURES consecutive events;
    * ``cancelled`` — the turn was abandoned: recorded, counts neither way.

    Transport failures (provider errors, timeouts) are model-health vocabulary, NOT lesson
    feedback: they are not a value this API accepts, and passing one is refused without any
    state change. Unknown values are refused the same way.
    """
    clean_terminal = str(terminal or "").strip().lower()
    if clean_terminal not in LearningPolicy.TERMINAL_OUTCOMES:
        return []
    clean_ids = {str(item).strip() for item in procedure_ids if str(item).strip()}
    if not clean_ids:
        return []
    updated: list[ProcedureShardV1] = []
    for procedure_id in sorted(clean_ids):
        stamp = _utcnow_iso()

        def _apply(shard: ProcedureShardV1, *, _stamp: str = stamp) -> ProcedureShardV1:
            base = dict(
                last_terminal_outcome=clean_terminal,
                last_terminal_at=_stamp,
                last_terminal_detail=str(detail or "").strip()[:200],
                last_reused_at=_stamp,
                last_reuse_task_class=str(task_class or "").strip() or shard.task_class,
                last_reuse_outcome={
                    LearningPolicy.TERMINAL_SUCCESS: "verified_success",
                    LearningPolicy.TERMINAL_UNVALIDATED: "completed",
                    LearningPolicy.TERMINAL_FAILURE: "validation_failed",
                    LearningPolicy.TERMINAL_CANCELLED: "cancelled",
                }[clean_terminal],
            )
            if clean_terminal == LearningPolicy.TERMINAL_CANCELLED:
                # Abandonment teaches nothing: no counters, no streak movement.
                return replace(shard, **base)

            reuse_count = int(shard.reuse_count or 0) + 1
            verified = clean_terminal == LearningPolicy.TERMINAL_SUCCESS
            unverified_streak = int(shard.consecutive_unverified_reuses or 0)
            failure_streak = int(shard.consecutive_failures or 0)
            demote_reason = ""
            if verified:
                unverified_streak = 0
                failure_streak = 0
            elif clean_terminal == LearningPolicy.TERMINAL_UNVALIDATED:
                unverified_streak += 1
                failure_streak = 0
                if shard.status == LearningPolicy.STATUS_PROMOTED and unverified_streak >= LearningPolicy.DEMOTE_AFTER_CONSECUTIVE_UNVERIFIED_REUSES:
                    demote_reason = f"unverified_reuse_streak:{unverified_streak}"
            else:  # failed
                failure_streak += 1
                unverified_streak = 0
                if shard.status == LearningPolicy.STATUS_PROMOTED and failure_streak >= LearningPolicy.DEMOTE_AFTER_VERIFIED_REUSE_FAILURES:
                    demote_reason = f"verified_reuse_failure:{failure_streak}"

            return replace(
                shard,
                **base,
                reuse_count=reuse_count,
                verified_reuse_count=int(shard.verified_reuse_count or 0) + (1 if verified else 0),
                consecutive_unverified_reuses=unverified_streak,
                consecutive_failures=failure_streak,
                failure_count=int(shard.failure_count or 0) + (1 if clean_terminal == LearningPolicy.TERMINAL_FAILURE else 0),
                status=LearningPolicy.STATUS_DEMOTED if demote_reason else shard.status,
                demoted_reason=demote_reason or shard.demoted_reason,
                demoted_at=_stamp if demote_reason else shard.demoted_at,
            )

        result = update_procedure_shard(procedure_id, _apply)
        if result is not None:
            updated.append(result)
    return updated


def record_procedure_reuse(
    *,
    procedure_ids: list[str] | tuple[str, ...],
    task_class: str,
    verified: bool,
    outcome: str,
) -> list[ProcedureShardV1]:
    """Legacy caller signature (the orchestration executor): maps onto the typed vocabulary --
    ``verified=True`` is a successful reuse, ``verified=False`` is an ok-but-unvalidated one.
    Failures and cancellations cannot arrive through this signature; the integration seam
    (``record_reuse_terminal``) owns them."""
    return record_reuse_terminal(
        procedure_ids=procedure_ids,
        task_class=task_class,
        terminal=LearningPolicy.TERMINAL_SUCCESS if verified else LearningPolicy.TERMINAL_UNVALIDATED,
        detail=str(outcome or ""),
    )


def invalidate_procedure(
    procedure_id: str,
    *,
    reason: str,
    actor: str = "operator",
) -> ProcedureShardV1 | None:
    """User correction: a lesson the operator says is wrong stops being reused immediately.

    Demotion by correction is terminal for automatic promotion -- later verified evidence appends
    to the demoted shard but cannot resurrect it; only an explicit operator path can. A correction
    on an already-demoted shard is recorded (idempotent) without rewriting history.
    """
    clean_reason = str(reason or "").strip() or "operator_correction"
    stamp = _utcnow_iso()

    def _apply(shard: ProcedureShardV1) -> ProcedureShardV1:
        correction = {"actor": str(actor or "").strip() or "operator", "reason": clean_reason, "corrected_at": stamp}
        was_live = shard.status in {LearningPolicy.STATUS_CANDIDATE, LearningPolicy.STATUS_PROMOTED}
        return replace(
            shard,
            status=LearningPolicy.STATUS_DEMOTED,
            demoted_reason=clean_reason if was_live else shard.demoted_reason,
            demoted_at=stamp if was_live else shard.demoted_at,
            corrections=tuple([*shard.corrections, correction])[-LearningPolicy.MAX_EVIDENCE_EVENTS_PER_SHARD:],
        )

    return update_procedure_shard(procedure_id, _apply)


def delete_procedure(procedure_id: str) -> bool:
    """Remove a lesson entirely (operator forget path). Returns whether a file was removed."""
    clean_id = str(procedure_id or "").strip()
    if not clean_id:
        return False
    try:
        path = _record_path(clean_id)
    except ValueError:
        return False
    with _STORE_LOCK, _StoreFileLock(path):
        if not path.exists():
            return False
        path.unlink()
    return True


def list_procedure_records() -> list[dict[str, Any]]:
    """Operator inspection projection: identity + lifecycle, never raw step/receipt payloads.

    This is the data contract for the (not yet wired) operator surface; the route seam is
    specified in PB01_INTEGRATION_HANDOFF_20260903.md as pending integration.
    """
    records: list[dict[str, Any]] = []
    for shard in load_procedure_shards():
        records.append(
            {
                "procedure_id": shard.procedure_id,
                "task_class": shard.task_class,
                "title": shard.title,
                "status": shard.status,
                "eligible_for_reuse": shard.eligible_for_reuse(),
                "expiry_state": shard.expiry_state,
                "owner_scope": shard.owner_scope,
                "origin_session_id": shard.origin_session_id,
                "evidence_count": len(shard.evidence),
                "promoted_at": shard.promoted_at,
                "expires_at": shard.expires_at,
                "demoted_reason": shard.demoted_reason,
                "demoted_at": shard.demoted_at,
                "corrections": [dict(item) for item in shard.corrections],
                "reuse_count": int(shard.reuse_count or 0),
                "verified_reuse_count": int(shard.verified_reuse_count or 0),
                "consecutive_unverified_reuses": int(shard.consecutive_unverified_reuses or 0),
                "consecutive_failures": int(shard.consecutive_failures or 0),
                "failure_count": int(shard.failure_count or 0),
                "last_terminal_outcome": shard.last_terminal_outcome,
                "last_terminal_at": shard.last_terminal_at,
                "estimated_cost_usd": shard.estimated_cost_usd,
                "migrated_from": shard.migrated_from,
                "last_reused_at": shard.last_reused_at,
                "last_reuse_outcome": shard.last_reuse_outcome,
                "created_at": shard.created_at,
            }
        )
    return records


def promotion_expiry_stamp(now: datetime | None = None) -> str:
    moment = now or datetime.now(timezone.utc)
    return (moment + timedelta(days=LearningPolicy.PROMOTION_TTL_DAYS)).isoformat()


def _procedure_shard_from_payload(payload: dict[str, Any]) -> ProcedureShardV1:
    # v1 files (no ``status`` and no ``schema_version``) predate the evidence rules entirely:
    # they load as explicit ``legacy_unvalidated`` history — preserved, inspectable, deletable,
    # and never trusted for reuse until revalidated through this policy's evidence path.
    is_v1 = "status" not in payload and "schema_version" not in payload
    status = str(payload.get("status") or "").strip()
    if not status:
        status = LearningPolicy.STATUS_LEGACY_UNVALIDATED if is_v1 else LearningPolicy.STATUS_CANDIDATE
    if status not in _VALID_STATUSES:
        status = LearningPolicy.STATUS_DEMOTED
    migrated_from = str(payload.get("migrated_from") or "").strip()
    if is_v1 and not migrated_from:
        migrated_from = LEGACY_SCHEMA_TAG
    return ProcedureShardV1(
        procedure_id=str(payload.get("procedure_id") or ""),
        task_class=str(payload.get("task_class") or ""),
        title=str(payload.get("title") or ""),
        preconditions=tuple(str(item).strip() for item in list(payload.get("preconditions") or []) if str(item).strip()),
        steps=tuple(str(item).strip() for item in list(payload.get("steps") or []) if str(item).strip()),
        tool_receipts=tuple(dict(item) for item in list(payload.get("tool_receipts") or []) if isinstance(item, dict)),
        validation=dict(payload.get("validation") or {}),
        rollback=dict(payload.get("rollback") or {}),
        privacy_class=str(payload.get("privacy_class") or "local_private"),
        shareability=str(payload.get("shareability") or "local_only"),
        success_signal=str(payload.get("success_signal") or "verified_success"),
        liquefy_bundle_ref=str(payload.get("liquefy_bundle_ref") or ""),
        reuse_count=max(0, int(payload.get("reuse_count") or 0)),
        verified_reuse_count=max(0, int(payload.get("verified_reuse_count") or 0)),
        last_reused_at=str(payload.get("last_reused_at") or ""),
        last_reuse_task_class=str(payload.get("last_reuse_task_class") or ""),
        last_reuse_outcome=str(payload.get("last_reuse_outcome") or ""),
        created_at=str(payload.get("created_at") or ""),
        schema_version=SCHEMA_VERSION,
        status=status,
        owner_scope=str(payload.get("owner_scope") or LearningPolicy.OWNER_SCOPE_LOCAL),
        origin_session_id=str(payload.get("origin_session_id") or ""),
        evidence=tuple(dict(item) for item in list(payload.get("evidence") or []) if isinstance(item, dict)),
        promoted_at=str(payload.get("promoted_at") or ""),
        expires_at=str(payload.get("expires_at") or ""),
        demoted_reason=str(payload.get("demoted_reason") or ""),
        demoted_at=str(payload.get("demoted_at") or ""),
        corrections=tuple(dict(item) for item in list(payload.get("corrections") or []) if isinstance(item, dict)),
        consecutive_unverified_reuses=max(0, int(payload.get("consecutive_unverified_reuses") or 0)),
        estimated_cost_usd=_cost_or_none(payload.get("estimated_cost_usd")),
        consecutive_failures=max(0, int(payload.get("consecutive_failures") or 0)),
        failure_count=max(0, int(payload.get("failure_count") or 0)),
        last_terminal_outcome=str(payload.get("last_terminal_outcome") or ""),
        last_terminal_at=str(payload.get("last_terminal_at") or ""),
        last_terminal_detail=str(payload.get("last_terminal_detail") or ""),
        migrated_from=migrated_from,
    )
