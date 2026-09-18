"""Exact rollback and crash recovery for coverage-recorded effects.

The v1 rollback (``core/blackbox/rollback.py``) restores ``workspace.*`` file intents and the
coverage recorder's declared-path effects -- both speak ``effect_intended``/``effect_terminal``.
THIS module owns the effects only the coverage lane produces:

- ``coverage_scan_intended``/``coverage_scan_terminal``: the scan pair for shell-shaped tools.
  Restore walks the terminal's drift rows and puts every path back to its BEFORE observation --
  same bytes (from the verified CAS), same mode, same mtime -- children-first for created trees,
  typed refusal on divergence, all-or-nothing per effect, itself journaled.
- Open scan effects (a crash between INTENDED and TERMINAL) are closed first by
  ``recover_open_coverage``: the intended entry carries the full preimage table, so the recovery
  pass scans the workspace NOW, diffs, and writes a truthful ``unknown_crashed`` terminal with
  the drift it can prove. Never success: the only claim is what the disk holds.
- ``coverage_post_terminal`` (irreversible local) and external effects refuse restoration: their
  capabilities say rollback_support=none, and this module agrees with them.

Authority: restoration is an operator act. The same v1 rollback tokens govern it
(``core.blackbox.authority``), minted server-side, single-use, expiring, bound to the turn and
root; ``restore_coverage_turn`` verifies and consumes one before any byte moves.
"""
from __future__ import annotations

import contextlib
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.blackbox import authority as authority_module
from core.blackbox.coverage.highrisk import classify_path
from core.blackbox.coverage.scan import ScanResult, diff_scans, scan_workspace
from core.blackbox.snapshot import PathObservation, observe_path
from core.blackbox.store import (
    OUTCOME_UNKNOWN_CRASHED,
    BlackboxStore,
    default_store,
    utcnow,
)
from storage.blackbox.blobs import BlobCorruptError, BlobMissingError

SCHEMA = "blackbox_effect_v1"
STATUS_NOT_AUTHORIZED = "blackbox_rollback_not_authorized"
STATUS_CONFLICT = "blackbox_rollback_conflict"
STATUS_UNKNOWN = "blackbox_unknown_turn"
STATUS_NOT_REVERSIBLE = "blackbox_not_reversible"


@dataclass
class RestoreStep:
    path: str
    action: str  # restore | remove | none | already
    before: dict[str, Any]
    after: dict[str, Any]
    conflict: str = ""


@dataclass
class RestorePlan:
    turn_id: str
    root: Path
    effect_ids: list[str] = field(default_factory=list)
    steps: list[RestoreStep] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    not_reversible: list[str] = field(default_factory=list)
    #: workspace-relative paths that existed BEFORE the effect (from the intended entry's
    #: embedded table): an empty directory is only removed if no before-path lived under it.
    before_paths: set[str] = field(default_factory=set)


def scan_effects(store: BlackboxStore) -> dict[str, dict[str, Any]]:
    """effect_id -> latest coverage scan entry (intended or terminal) in journal order."""
    effects: dict[str, dict[str, Any]] = {}
    for entry in store.entries():
        if str(entry.get("kind") or "") == "coverage_scan_intended" or str(entry.get("kind") or "") == "coverage_scan_terminal":
            effects[str(entry.get("effect_id"))] = entry
    return effects


def _ids(row: dict[str, Any], keyring) -> tuple[bool, str, str]:
    """(exists, kind, content_id) for an observation row. A CURRENT observation's id is computed
    in memory from its raw digest; recorded rows already carry the opaque id. Raw digests never
    leave this function."""
    exists = bool(row.get("exists"))
    kind = str(row.get("kind") or "missing")
    content_id = str(row.get("content_id") or "")
    if exists and kind == "file" and not content_id:
        raw = str(row.get("sha256") or "")
        if raw and keyring is not None:
            content_id = keyring.id_for(raw)
    return exists, kind, content_id


def _same_state(row_a: dict[str, Any], row_b: dict[str, Any], keyring) -> bool:
    ea, ka, ia = _ids(row_a, keyring)
    eb, kb, ib = _ids(row_b, keyring)
    if ea != eb or ka != kb:
        return False
    return (not ea) or ia == ib


def recover_open_coverage(store: BlackboxStore) -> list[dict[str, Any]]:
    """Close every coverage_scan_intended that never reached a terminal (a crash mid-handler).

    The intended entry's embedded preimage table is diffed against the workspace NOW; the
    terminal that closes the effect claims only what the disk holds, never success. A journal
    whose chain does not verify is NEVER extended here -- laundering a tampered chain back to
    "recovered" would be the worst lie this module could tell."""
    chain = store.verify()
    if not chain.ok:
        from storage.blackbox.journal import JournalIntegrityError

        raise JournalIntegrityError(f"the journal does not verify ({chain.reason}); refusing to recover onto it")
    closed: list[dict[str, Any]] = []
    terminal_ids = {
        str(entry.get("effect_id"))
        for entry in store.entries()
        if str(entry.get("kind") or "") == "coverage_scan_terminal"
    }
    for entry in store.entries():
        if str(entry.get("kind") or "") != "coverage_scan_intended":
            continue
        effect_id = str(entry.get("effect_id"))
        if effect_id in terminal_ids:
            continue
        root = Path(str(entry.get("root") or ""))
        files_before = {str(rel): dict(item) for rel, item in dict(entry.get("files") or {}).items()}
        before = ScanResult(files=files_before, manifest_sha256=str(entry.get("manifest_sha256") or ""),
                            degraded=bool(entry.get("degraded")), files_scanned=len(files_before),
                            bytes_captured=0, capture_skipped=0)
        after_scan = scan_workspace(root, store=store) if str(root) else None
        if after_scan is None:
            after = ScanResult({}, "", True, 0, 0, 0)
            drift = []
        else:
            # The embedded table is journal-opaque (content ids); the fresh scan holds raw
            # digests in memory only. One keyed conversion, then both speak the same id.
            keyring = getattr(store, "cas_keyring", None)
            converted = {
                rel: {**row, "content_id": (keyring.id_for(row["sha256"]) if keyring is not None and row.get("sha256") else str(row.get("content_id") or ""))}
                for rel, row in after_scan.files.items()
            }
            after = ScanResult(files=converted, manifest_sha256=after_scan.manifest_sha256,
                               degraded=after_scan.degraded, files_scanned=len(converted),
                               bytes_captured=0, capture_skipped=0)
            drift = diff_scans(before, after, hash_key="content_id")
        rows = [
            {"path": item.path, "drift_kind": item.kind, "before": item.before, "after": item.after,
             "high_risk": classify_path(item.path) or "", "rollback_capable": bool(item.rollback_capable)}
            for item in drift
        ]
        closed.append(
            store.append(
                {
                    "schema": SCHEMA,
                    "kind": "coverage_scan_terminal",
                    "effect_id": effect_id,
                    "turn_id": entry.get("turn_id"),
                    "session_id": entry.get("session_id"),
                    "root": entry.get("root"),
                    "intent": entry.get("intent"),
                    "outcome": OUTCOME_UNKNOWN_CRASHED,
                    "status": "",
                    "ok": False,
                    "manifest_sha256_before": entry.get("manifest_sha256"),
                    "manifest_sha256_after": after.manifest_sha256,
                    "degraded": bool(entry.get("degraded") or after.degraded),
                    "drift": rows,
                    "rollback_capable": bool(all(row["rollback_capable"] for row in rows)) if rows else True,
                    "recovery": {"reconciled": "crash", "recovered_at": utcnow(), "recovered_by_pid": os.getpid()},
                    "started_at": entry.get("ts"),
                    "completed_at": utcnow(),
                }
            )
        )
    return closed


def plan_restore_turn(store: BlackboxStore, turn_id: str) -> RestorePlan:
    """Pass 1, no writes: what restoring this turn's coverage effects would do, and every reason
    it must refuse instead. All-or-nothing per turn across its scan effects."""
    entries = store.entries()
    roots: set[str] = set()
    scan_terminals: list[dict[str, Any]] = []
    not_reversible: list[str] = []
    for entry in entries:
        kind = str(entry.get("kind") or "")
        turn = str(entry.get("turn_id") or "")
        if turn != str(turn_id):
            continue
        if kind == "coverage_scan_terminal":
            if bool(entry.get("downgraded_to_irreversible")):
                # The operator approved irreversibility BEFORE execution; that decision stands.
                not_reversible.append(
                    f"{entry.get('intent')}: executed under an approved irreversible downgrade ({entry.get('effect_id')})"
                )
                continue
            scan_terminals.append(entry)
            if str(entry.get("root") or ""):
                roots.add(str(entry.get("root")))
        elif kind == "coverage_post_terminal":
            not_reversible.append(f"{entry.get('intent')}: irreversible local effect ({entry.get('effect_id')})")
    if not scan_terminals:
        if not_reversible:
            plan = RestorePlan(turn_id=str(turn_id), root=Path(sorted(roots)[0]) if roots else Path(), not_reversible=not_reversible)
            keyring = getattr(store, "cas_keyring", None)
            if keyring is None:
                plan.conflicts.append({"reason": "cas_key_unavailable",
                                       "detail": "the encrypted-CAS keyring is unavailable; rollback refuses to act"})
            return plan
        raise LookupError(STATUS_UNKNOWN)
    plan = RestorePlan(turn_id=str(turn_id), root=Path(sorted(roots)[0]) if roots else Path(), not_reversible=not_reversible)
    keyring = getattr(store, "cas_keyring", None)
    if keyring is None:
        # No keyring: no ids to compare against, no blobs to restore from. Restoration fails
        # closed rather than act on state it cannot verify.
        plan.conflicts.append({"reason": "cas_key_unavailable",
                               "detail": "the encrypted-CAS keyring is unavailable; rollback refuses to act"})
        return plan
    intended_files: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if str(entry.get("kind") or "") == "coverage_scan_intended":
            intended_files[str(entry.get("effect_id"))] = dict(entry.get("files") or {})
    for effect_id, files in intended_files.items():
        if effect_id in {str(e.get("effect_id")) for e in scan_terminals}:
            plan.before_paths.update(str(rel) for rel in files)
    steps: list[RestoreStep] = []
    conflicts: list[dict[str, Any]] = []
    for entry in scan_terminals:
        plan.effect_ids.append(str(entry.get("effect_id")))
        if not bool(entry.get("rollback_capable", True)):
            conflicts.append({"effect_id": entry.get("effect_id"), "reason": "drift_not_rollback_capable",
                              "detail": "a drifted path has no captured preimage; restoring would invent bytes"})
            continue
        root = Path(str(entry.get("root") or plan.root))
        for row in reversed(list(entry.get("drift") or [])):
            rel = str(row.get("path") or "")
            if not rel:
                continue
            target = root / rel
            before = dict(row.get("before") or {})
            after = dict(row.get("after") or {})
            try:
                current = observe_path(target)
            except OSError as exc:
                conflicts.append({"effect_id": entry.get("effect_id"), "path": rel,
                                  "reason": "observe_failed", "detail": f"{type(exc).__name__}: {exc}"})
                continue
            current_row = current.to_dict()
            if _same_state(current_row, after, keyring):
                if not bool(before.get("exists")):
                    steps.append(RestoreStep(rel, "remove", before, after))
                elif str(before.get("kind")) == "file" and before.get("bytes_captured") and before.get("blob"):
                    steps.append(RestoreStep(rel, "restore", before, after))
                else:
                    conflicts.append({"effect_id": entry.get("effect_id"), "path": rel,
                                      "reason": "preimage_uncaptured", "detail": "the before state was never captured"})
            elif _same_state(current_row, before, keyring):
                steps.append(RestoreStep(rel, "already", before, after))
            else:
                conflicts.append({"effect_id": entry.get("effect_id"), "path": rel,
                                  "reason": "postimage_diverged",
                                  "detail": "the path changed again after this effect; refusing to overwrite"})
    plan.steps = steps
    plan.conflicts = conflicts
    return plan


def _remove_created_ancestors(target: Path, *, root: Path, before_paths: set[str], performed: list[dict[str, Any]]) -> None:
    """Walk upward removing directories the BEFORE tree never had, stopping at the first
    non-empty one, the root, or any directory a before-path lived under."""
    with contextlib.suppress(OSError):
        parent = target.parent
        while parent != root:
            rel = str(parent.relative_to(root))
            if any(path == rel or path.startswith(rel + "/") for path in before_paths):
                return
            if any(parent.iterdir()):
                return
            parent.rmdir()
            performed.append({"path": rel, "action": "remove_dir"})
            parent = parent.parent


def _atomic_write_bytes(path: Path, data: bytes, *, mode: int | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".restore")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise
    if mode is not None:
        with contextlib.suppress(OSError):
            os.chmod(path, mode)


def execute_restore(store: BlackboxStore, plan: RestorePlan, *, operator: str) -> dict[str, Any]:
    """Pass 2: perform the planned steps, each re-checked immediately before acting, journaled
    ``coverage_restore_started`` -> ``coverage_restore_committed``. A conflict found mid-flight
    stops the restore with ``coverage_restore_refused`` and restores nothing further."""
    started = store.append(
        {
            "schema": SCHEMA,
            "kind": "coverage_restore_started",
            "turn_id": f"rollback:{plan.turn_id}",
            "rollback_of_turn": plan.turn_id,
            "effect_ids": plan.effect_ids,
            "operator": str(operator or ""),
            "steps": [{"path": step.path, "action": step.action} for step in plan.steps],
        }
    )
    performed: list[dict[str, Any]] = []
    root = plan.root
    keyring = getattr(store, "cas_keyring", None)
    for step in plan.steps:
        target = root / step.path
        before = PathObservation.from_dict(step.before)
        current = observe_path(target)
        current_row = current.to_dict()
        if not _same_state(current_row, step.after, keyring):
            if _same_state(current_row, step.before, keyring):
                continue  # someone restored it first; idempotent
            store.append(
                {
                    "schema": SCHEMA,
                    "kind": "coverage_restore_refused",
                    "turn_id": f"rollback:{plan.turn_id}",
                    "rollback_of_turn": plan.turn_id,
                    "path": step.path,
                    "reason": "postimage_diverged",
                    "started_seq": started.get("seq"),
                }
            )
            return {"ok": False, "status": STATUS_CONFLICT, "path": step.path, "performed": performed}
        try:
            if step.action == "restore":
                blob = before.blob or ""
                data = store.blobs.get(blob)
                _atomic_write_bytes(target, data, mode=before.mode)
                mtime_ns = int(dict(step.before).get("mtime_ns") or 0)
                if mtime_ns:
                    with contextlib.suppress(OSError):
                        os.utime(target, ns=(mtime_ns, mtime_ns))
                performed.append({"path": step.path, "action": "restore"})
            elif step.action == "remove":
                if current.kind == "dir":
                    with contextlib.suppress(OSError):
                        target.rmdir()
                else:
                    target.unlink()
                performed.append({"path": step.path, "action": "remove"})
                _remove_created_ancestors(target, root=root, before_paths=plan.before_paths, performed=performed)
        except (OSError, BlobMissingError, BlobCorruptError) as exc:
            store.append(
                {
                    "schema": SCHEMA,
                    "kind": "coverage_restore_refused",
                    "turn_id": f"rollback:{plan.turn_id}",
                    "rollback_of_turn": plan.turn_id,
                    "path": step.path,
                    "reason": f"{type(exc).__name__}: {exc}",
                    "started_seq": started.get("seq"),
                }
            )
            return {"ok": False, "status": STATUS_CONFLICT, "path": step.path, "performed": performed}
    committed = store.append(
        {
            "schema": SCHEMA,
            "kind": "coverage_restore_committed",
            "turn_id": f"rollback:{plan.turn_id}",
            "rollback_of_turn": plan.turn_id,
            "effect_ids": plan.effect_ids,
            "performed": performed,
            "started_seq": started.get("seq"),
        }
    )
    return {"ok": True, "status": "committed", "performed": performed, "seq": committed.get("seq")}


def restore_coverage_turn(
    *,
    turn_id: str,
    workspace_root: Path | str,
    token: str,
    store: BlackboxStore | None = None,
) -> dict[str, Any]:
    """The operator entry: verify the rollback token for THIS turn and root, recover any open
    coverage effect, then restore the turn's coverage effects exactly or refuse."""
    store = store or default_store()
    chain = store.verify()
    if not chain.ok:
        return {"ok": False, "status": "blackbox_journal_integrity", "reason": chain.reason}
    verdict = authority_module.verify_rollback_authorization(
        str(token or ""), turn_id=str(turn_id), workspace_root=workspace_root, store=store
    )
    if not verdict.ok:
        return {"ok": False, "status": STATUS_NOT_AUTHORIZED, "reason": verdict.reason}
    recover_open_coverage(store)
    try:
        plan = plan_restore_turn(store, str(turn_id))
    except LookupError:
        return {"ok": False, "status": STATUS_UNKNOWN, "reason": "no coverage effects for this turn"}
    if plan.conflicts:
        return {"ok": False, "status": STATUS_CONFLICT, "conflicts": plan.conflicts}
    if plan.not_reversible:
        return {"ok": False, "status": STATUS_NOT_REVERSIBLE, "not_reversible": plan.not_reversible}
    result = execute_restore(store, plan, operator=f"token:{verdict.nonce}")
    if result.get("ok"):
        authority_module.consume_rollback_authorization(verdict.nonce, store=store, reason=f"coverage:{turn_id}")
    return result


__all__ = [
    "STATUS_CONFLICT",
    "STATUS_NOT_AUTHORIZED",
    "STATUS_NOT_REVERSIBLE",
    "STATUS_UNKNOWN",
    "RestorePlan",
    "RestoreStep",
    "execute_restore",
    "plan_restore_turn",
    "recover_open_coverage",
    "restore_coverage_turn",
    "scan_effects",
]
