"""The Blackbox store: journal + blobs + the semantics layered on them (turn index, crash
recovery, retention, status).

Entry kinds (``kind``):

    effect_intended   before the physical mutation: identity, canonical root/path, operation,
                      before observation (+blob), intended change
    effect_terminal   after it: outcome, handler status, after observation (+blob), timestamps;
                      a recovery-written terminal carries ``recovery`` and outcome
                      ``unknown_crashed``
    coverage_gap      a handler reported paths the recorder had not pre-snapshotted
    rollback_authorized / rollback_authorization_consumed
    rollback_started / rollback_refused / rollback_committed
    retention_pruned  which turns lost their blobs and can no longer be restored
"""
from __future__ import annotations

import contextlib
import os
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.blackbox import snapshot as snapshot_module
from core.blackbox.snapshot import PathObservation
from storage.blackbox.blobs import BlobStore
from storage.blackbox.journal import ChainReport, Journal

DEFAULT_MAX_BLOB_BYTES = 32 * 1024 * 1024
DEFAULT_MAX_TOTAL_BLOB_BYTES = 2 * 1024 * 1024 * 1024
DEFAULT_KEEP_TURNS = 500
_AUTO_PRUNE_EVERY = 50

OUTCOME_SUCCEEDED = "succeeded"
OUTCOME_NO_CHANGE = "no_change"
OUTCOME_REFUSED = "refused"
OUTCOME_FAILED = "failed"
OUTCOME_UNKNOWN = "unknown"
OUTCOME_UNKNOWN_CRASHED = "unknown_crashed"
OUTCOME_INTERRUPTED = "interrupted"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class EffectRecord:
    effect_id: str
    intended: dict[str, Any]
    terminal: dict[str, Any] | None = None

    @property
    def turn_id(self) -> str:
        return str(self.intended.get("turn_id") or "")

    @property
    def root(self) -> str:
        return str(self.intended.get("root") or "")

    @property
    def path(self) -> str:
        return str(self.intended.get("path") or "")

    @property
    def before(self) -> PathObservation:
        return PathObservation.from_dict(self.intended.get("before"))

    @property
    def after(self) -> PathObservation | None:
        if self.terminal is None:
            return None
        return PathObservation.from_dict(self.terminal.get("after"))

    @property
    def outcome(self) -> str:
        return str((self.terminal or {}).get("outcome") or "")


@dataclass
class TurnSummary:
    turn_id: str
    root: str
    first_seq: int
    effect_ids: list[str] = field(default_factory=list)
    sessions: set[str] = field(default_factory=set)
    pruned: bool = False
    rolled_back: bool = False
    is_rollback: bool = False


class BlackboxStore:
    def __init__(self, root: Path | str, *, max_blob_bytes: int = DEFAULT_MAX_BLOB_BYTES,
                 max_total_blob_bytes: int = DEFAULT_MAX_TOTAL_BLOB_BYTES, keep_turns: int = DEFAULT_KEEP_TURNS) -> None:
        self.root = Path(root)
        self.max_blob_bytes = int(max_blob_bytes)
        self.max_total_blob_bytes = int(max_total_blob_bytes)
        self.keep_turns = int(keep_turns)
        self.journal = Journal(self.root)
        # The encrypted CAS (2026-09-02 amendment): every blob this store writes is sealed v2
        # under keys from the canonical secret authority. No key source -> the fail-closed
        # sentinel: writes raise typed, nothing is ever stored plaintext instead.
        from core.blackbox.coverage.cas_keys import resolve_keyring

        self.cas_keyring = resolve_keyring()
        if self.cas_keyring is None:
            from storage.blackbox.cas import UnavailableCAS

            cas = UnavailableCAS()
        else:
            from storage.blackbox.cas import EncryptedBlobStore

            cas = EncryptedBlobStore(self.root / "cas-v2", self.cas_keyring)
        self.blobs = BlobStore(self.root, cas=cas)
        # Compressed searchable projection (core/liquefy). Additive only: the
        # journal above stays the authority and the original recovery path; the
        # sink is wired by default_store() and never called when unset.
        self.liquefy_sink: Any = None
        self._recovered_once = False
        self._effects_since_prune_check = 0
        self._lock = threading.RLock()

    # -- encrypted CAS migration ---------------------------------------------------------------
    def migrate_blobs_to_v2(self) -> dict[str, Any]:
        """Seal every legacy plaintext blob into the encrypted CAS, byte-verified, deleting the
        plaintext only after its sealed copy reads back identical. Crash-safe per blob; an
        interrupted pass is re-runnable and resumes. The completion is journaled."""
        if self.cas_keyring is None:
            return {"ok": False, "reason": "cas_key_unavailable"}
        from storage.blackbox.cas import migrate_v1_blobs

        with self.exclusive():
            report = migrate_v1_blobs(self.blobs.blob_dir, self.blobs.cas)
            report["ok"] = not report.get("corrupt") and not report.get("failed") and report.get("remaining") == 0
            self.append({"schema": "blackbox_effect_v1", "kind": "cas_migrated", **{k: v for k, v in report.items() if k != "ok"}})
        return report

    # -- primitives ----------------------------------------------------------------------------
    @contextlib.contextmanager
    def exclusive(self) -> Iterator[None]:
        with self.journal.exclusive():
            yield

    def append(self, entry: dict[str, Any]) -> dict[str, Any]:
        full = self.journal.append(entry)
        sink = self.liquefy_sink
        if sink is not None:
            # The projection must never break the recorder: its own failure path
            # is a counted no-op, not an exception on the Blackbox seam.
            with contextlib.suppress(Exception):
                sink(full)
        return full

    def entries(self) -> list[dict[str, Any]]:
        return self.journal.entries()

    def verify(self) -> ChainReport:
        return self.journal.verify()

    def snapshot(self, target: Path) -> PathObservation:
        # Module-attribute lookup on purpose: the capture seam is ONE name a sabotage can disable.
        return snapshot_module.snapshot_path(target, blobs=self.blobs, max_bytes=self.max_blob_bytes)

    # -- indexes -------------------------------------------------------------------------------
    def effect_records(self, entries: list[dict[str, Any]] | None = None) -> dict[str, EffectRecord]:
        records: dict[str, EffectRecord] = {}
        for entry in entries if entries is not None else self.entries():
            kind = str(entry.get("kind") or "")
            effect_id = str(entry.get("effect_id") or "")
            if kind == "effect_intended" and effect_id:
                records[effect_id] = EffectRecord(effect_id, entry)
            elif kind == "effect_terminal" and effect_id and effect_id in records:
                if records[effect_id].terminal is None:
                    records[effect_id].terminal = entry
        return records

    def turn_index(self, entries: list[dict[str, Any]] | None = None) -> dict[str, TurnSummary]:
        entries = entries if entries is not None else self.entries()
        turns: dict[str, TurnSummary] = {}
        for entry in entries:
            kind = str(entry.get("kind") or "")
            if kind == "effect_intended":
                turn_id = str(entry.get("turn_id") or "")
                summary = turns.get(turn_id)
                if summary is None:
                    summary = turns[turn_id] = TurnSummary(
                        turn_id, str(entry.get("root") or ""), int(entry.get("seq") or 0),
                        is_rollback=turn_id.startswith("rollback:"),
                    )
                summary.effect_ids.append(str(entry.get("effect_id") or ""))
                summary.sessions.add(str(entry.get("session_id") or ""))
            elif kind == "retention_pruned":
                for turn_id in list(entry.get("turn_ids") or []):
                    if str(turn_id) in turns:
                        turns[str(turn_id)].pruned = True
            elif kind == "rollback_committed":
                target = str(entry.get("rollback_of_turn") or "")
                if target in turns:
                    turns[target].rolled_back = True
        return turns

    def effects_for_turn(self, turn_id: str, *, entries: list[dict[str, Any]] | None = None) -> list[EffectRecord]:
        records = self.effect_records(entries)
        return [record for record in records.values() if record.turn_id == turn_id]

    def open_effects(self, entries: list[dict[str, Any]] | None = None) -> list[EffectRecord]:
        return [record for record in self.effect_records(entries).values() if record.terminal is None]

    # -- crash recovery ------------------------------------------------------------------------
    def recover_open_effects(self) -> list[dict[str, Any]]:
        """Close every INTENDED that never reached TERMINAL with a truthful ``unknown_crashed``
        terminal reconciled against the disk NOW. Never claims success: the only thing known is
        what the path holds at recovery time and how that compares with what was recorded."""
        recovered: list[dict[str, Any]] = []
        with self.exclusive():
            for record in self.open_effects():
                target = Path(record.root) / record.path
                current = self.snapshot(target) if not record.intended.get("path_unresolved") else snapshot_module.observe_path(target)
                before = record.before
                intended = dict(record.intended.get("intended") or {})
                intended_sha = intended.get("after_sha256")
                intended_exists = intended.get("after_exists")
                if current.same_content_as(before):
                    reconciled = "matches_before"
                elif (intended_sha is not None and current.kind == "file" and current.sha256 == str(intended_sha)) or (intended_sha is None and intended_exists is True and current.exists and before.kind == "missing" and current.kind == "dir"):
                    reconciled = "matches_intended_after"
                elif intended_sha is None and intended_exists is None:
                    reconciled = "intended_unknown"
                else:
                    reconciled = "differs_from_both"
                terminal = {
                    "schema": "blackbox_effect_v1",
                    "kind": "effect_terminal",
                    "effect_id": record.effect_id,
                    "turn_id": record.turn_id,
                    "session_id": record.intended.get("session_id"),
                    "root": record.root,
                    "path": record.path,
                    "intent": record.intended.get("intent"),
                    "operation": record.intended.get("operation"),
                    "outcome": OUTCOME_UNKNOWN_CRASHED,
                    "status": "",
                    "after": current.to_dict(),
                    "started_at": record.intended.get("ts"),
                    "completed_at": utcnow(),
                    "recovery": {"reconciled": reconciled, "recovered_at": utcnow(), "recovered_by_pid": os.getpid()},
                }
                recovered.append(self.append(terminal))
        self._recovered_once = True
        return recovered

    def ensure_recovered(self) -> None:
        with self._lock:
            if self._recovered_once:
                return
            self.recover_open_effects()

    # -- retention -----------------------------------------------------------------------------
    def _blob_references(self, entries: list[dict[str, Any]]) -> dict[str, set[str]]:
        """blob sha -> set of turn ids referencing it (before or after)."""
        refs: dict[str, set[str]] = {}
        for entry in entries:
            kind = str(entry.get("kind") or "")
            if kind not in {"effect_intended", "effect_terminal"}:
                continue
            observation = entry.get("before") if kind == "effect_intended" else entry.get("after")
            blob = (observation or {}).get("blob") if isinstance(observation, dict) else None
            if blob:
                refs.setdefault(str(blob), set()).add(str(entry.get("turn_id") or ""))
        return refs

    def prune(self, *, keep_turns: int | None = None, max_total_blob_bytes: int | None = None) -> dict[str, Any]:
        """Drop the blobs of the oldest turns and SAY SO in the journal. The journal entries stay
        (they are the evidence); the turn is marked pruned and refuses restoration afterwards."""
        with self.exclusive():
            entries = self.entries()
            turns = self.turn_index(entries)
            candidates = [t for t in sorted(turns.values(), key=lambda t: t.first_seq) if not t.pruned]
            to_prune: list[TurnSummary] = []
            if keep_turns is not None and len(candidates) > keep_turns:
                to_prune.extend(candidates[: len(candidates) - keep_turns])
            if max_total_blob_bytes is not None:
                total = self.blobs.total_bytes()
                refs = self._blob_references(entries)
                chosen = {t.turn_id for t in to_prune}
                for turn in candidates:
                    if total <= max_total_blob_bytes:
                        break
                    if turn.turn_id in chosen:
                        continue
                    chosen.add(turn.turn_id)
                    to_prune.append(turn)
                    for blob, owners in refs.items():
                        if owners and owners <= chosen:
                            with contextlib.suppress(OSError):
                                total -= self.blobs.path_for(blob).stat().st_size
            if not to_prune:
                return {"pruned_turn_ids": [], "blobs_deleted": 0, "bytes_freed": 0}
            pruned_ids = [t.turn_id for t in to_prune]
            pruned_set = set(pruned_ids) | {t.turn_id for t in turns.values() if t.pruned}
            refs = self._blob_references(entries)
            deleted = 0
            freed = 0
            for blob, owners in refs.items():
                if owners and owners <= pruned_set and self.blobs.has(blob):
                    with contextlib.suppress(OSError):
                        freed += self.blobs.path_for(blob).stat().st_size
                    if self.blobs.delete(blob):
                        deleted += 1
            self.append(
                {
                    "schema": "blackbox_effect_v1",
                    "kind": "retention_pruned",
                    "turn_ids": pruned_ids,
                    "blobs_deleted": deleted,
                    "bytes_freed": freed,
                    "policy": {"keep_turns": keep_turns, "max_total_blob_bytes": max_total_blob_bytes},
                }
            )
            return {"pruned_turn_ids": pruned_ids, "blobs_deleted": deleted, "bytes_freed": freed}

    def maybe_auto_prune(self) -> dict[str, Any] | None:
        """Bounded retention without an operator: every N effects, enforce the configured
        turn and byte ceilings. Cheap by construction (a counter), stated in status()."""
        with self._lock:
            self._effects_since_prune_check += 1
            if self._effects_since_prune_check < _AUTO_PRUNE_EVERY:
                return None
            self._effects_since_prune_check = 0
        return self.prune(keep_turns=self.keep_turns, max_total_blob_bytes=self.max_total_blob_bytes)

    # -- status --------------------------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        entries = self.entries()
        turns = self.turn_index(entries)
        records = self.effect_records(entries)
        effect_turns = [t for t in turns.values() if not t.is_rollback]
        uncaptured = sum(
            1 for record in records.values()
            if record.before.exists and record.before.kind == "file" and not record.before.bytes_captured
        )
        open_effects = [r.effect_id for r in records.values() if r.terminal is None]
        chain = self.verify()
        journal_bytes = 0
        with contextlib.suppress(OSError):
            journal_bytes = self.journal.journal_path.stat().st_size
        recoverable = [t for t in effect_turns if not t.pruned]
        return {
            "store": str(self.root),
            "journal_entries": len(entries),
            "journal_bytes": journal_bytes,
            "chain": {"ok": chain.ok, "reason": chain.reason, "entries": chain.entries, "head_count": chain.head_count,
                      "first_bad_seq": chain.first_bad_seq},
            "turns_total": len(effect_turns),
            "turns_recoverable": len(recoverable),
            "turns_pruned": sum(1 for t in effect_turns if t.pruned),
            "turns_rolled_back": sum(1 for t in effect_turns if t.rolled_back),
            "rollback_turns": sum(1 for t in turns.values() if t.is_rollback),
            "oldest_recoverable_turn": (min(recoverable, key=lambda t: t.first_seq).turn_id if recoverable else ""),
            "effects_total": len(records),
            "effects_open": open_effects,
            "effects_uncaptured": uncaptured,
            "blob_count": sum(1 for _ in self.blobs.iter_sha256()),
            "blob_bytes": self.blobs.total_bytes(),
            "limits": {
                "max_blob_bytes": self.max_blob_bytes,
                "max_total_blob_bytes": self.max_total_blob_bytes,
                "keep_turns": self.keep_turns,
                "auto_prune_every_effects": _AUTO_PRUNE_EVERY,
            },
        }


# -- default store ------------------------------------------------------------------------------
_DEFAULT: BlackboxStore | None = None
_DEFAULT_LOCK = threading.Lock()


def _int_env(name: str, default: int) -> int:
    raw = str(os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default


def store_root() -> Path:
    override = str(os.environ.get("VOOL_BLACKBOX_DIR") or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    from core.runtime_paths import data_path

    return data_path("blackbox")


def default_store() -> BlackboxStore:
    global _DEFAULT
    with _DEFAULT_LOCK:
        root = store_root()
        if _DEFAULT is None or _DEFAULT.root != root:
            _DEFAULT = BlackboxStore(
                root,
                max_blob_bytes=_int_env("VOOL_BLACKBOX_MAX_BLOB_BYTES", DEFAULT_MAX_BLOB_BYTES),
                max_total_blob_bytes=_int_env("VOOL_BLACKBOX_MAX_TOTAL_BYTES", DEFAULT_MAX_TOTAL_BLOB_BYTES),
                keep_turns=_int_env("VOOL_BLACKBOX_KEEP_TURNS", DEFAULT_KEEP_TURNS),
            )
            _wire_liquefy_sink(_DEFAULT)
        return _DEFAULT


def _wire_liquefy_sink(store: BlackboxStore) -> None:
    """Attach the compressed-searchable projection to the process store. The
    hook itself is lazy and fail-open; when the lane is disabled the sink stays
    unset and append() is byte-for-byte the base behavior."""
    try:
        from core.liquefy import hooks as liquefy_hooks

        if liquefy_hooks.enabled():
            store.liquefy_sink = liquefy_hooks.record_blackbox_entry
    except Exception:
        store.liquefy_sink = None


def reset_default_store() -> None:
    global _DEFAULT
    with _DEFAULT_LOCK:
        _DEFAULT = None


__all__ = [
    "OUTCOME_FAILED",
    "OUTCOME_INTERRUPTED",
    "OUTCOME_NO_CHANGE",
    "OUTCOME_REFUSED",
    "OUTCOME_SUCCEEDED",
    "OUTCOME_UNKNOWN",
    "OUTCOME_UNKNOWN_CRASHED",
    "BlackboxStore",
    "EffectRecord",
    "TurnSummary",
    "default_store",
    "reset_default_store",
    "store_root",
    "utcnow",
]
