"""Explicit rollback of ONE turn's workspace effects, byte-exact, all-or-nothing, idempotent,
interruption-recoverable, and itself journaled.

Reached only through ``workspace.rollback_last_change`` dispatched across the authorized
execution boundary with a server-side directive (``source_context["blackbox_rollback"]``)
carrying an operator token -- see ``authority``. The permission authority decides first (a MANUAL
mode still answers ``pending_approval``); this module runs only on ALLOW.

Plan (pass 1, no writes):
  for every path the turn touched: initial = the FIRST effect's before, final = the LAST
  effect's after. Current disk is observed WITHOUT following symlinks.
    current == final    -> a step is pending (restore / remove / rmdir / none)
    current == initial  -> already restored (a resumed or repeated rollback), no step
    anything else       -> typed conflict, and NOTHING is written
  A symlinked leaf, a parent chain resolving outside the root, a hard-linked target, a lexical
  escape in a journal path, a missing or corrupt blob, an uncaptured before, a pruned turn, or a
  crashed effect whose outcome could not be reconciled is a typed refusal in the same pass.

Execute (pass 2): steps in reverse journal order, each re-checked immediately before acting,
each journaled INTENDED -> TERMINAL under a ``rollback:<uuid>`` identity linked to the original
effect and turn, then ``rollback_committed``. A crash between steps leaves a resumable state:
the next attempt finds the restored paths at their initial bytes and only performs the rest.
"""
from __future__ import annotations

import contextlib
import os
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.blackbox import authority as authority_module
from core.blackbox.identity import EffectIdentity, identity_from_context
from core.blackbox.snapshot import (
    PathObservation,
    observe_path,
    parent_chain_inside_root,
    relative_path_escapes,
)
from core.blackbox.store import (
    OUTCOME_FAILED,
    OUTCOME_SUCCEEDED,
    OUTCOME_UNKNOWN_CRASHED,
    BlackboxStore,
    EffectRecord,
    default_store,
    utcnow,
)
from storage.blackbox.blobs import BlobCorruptError, BlobMissingError

SCHEMA = "blackbox_effect_v1"
STATUS_CONFLICT = "blackbox_rollback_conflict"
STATUS_UNRECOVERABLE = "blackbox_rollback_unrecoverable"
STATUS_INTEGRITY = "blackbox_journal_integrity"
STATUS_NOT_AUTHORIZED = "blackbox_rollback_not_authorized"
STATUS_ALREADY = "already_rolled_back"
STATUS_UNKNOWN_TURN = "blackbox_unknown_turn"


@dataclass
class Step:
    path: str
    target: Path
    initial: PathObservation
    final: PathObservation
    effect_ids: list[str]
    action: str  # restore | remove | rmdir | none
    state: str = "pending"  # pending | already
    missing_parents: list[str] = field(default_factory=list)


@dataclass
class Plan:
    turn_id: str
    root: Path
    steps: list[Step]
    conflicts: list[dict[str, Any]]
    unrecoverable: str
    resumed: bool
    already_committed: bool
    effects: list[EffectRecord]


# ---------------------------------------------------------------------------- diagnosis ----


def _diagnose_conflict(root: Path, step: Step, current: PathObservation) -> str | None:
    """Why the current disk state forbids acting on this step, or None when it is safe.
    Called by module-global lookup so a sabotage that disables it is visible as one seam."""
    if relative_path_escapes(step.path):
        return "path_escapes_root"
    if not parent_chain_inside_root(root, step.target):
        return "parent_directory_escapes_workspace"
    if current.kind == "symlink":
        return "target_replaced_by_symlink"
    if current.kind == "file" and current.nlink > 1:
        return "hardlinked_target"
    if current.kind == "other":
        return "target_type_changed"
    if current.same_content_as(step.final):
        if step.action == "rmdir" and any(step.target.iterdir()):
            return "directory_not_empty"
        return None
    if current.same_content_as(step.initial):
        return None  # already restored
    if step.final.exists and not current.exists:
        return "target_missing"
    if not step.final.exists and current.exists:
        return "unexpected_existence"
    if current.kind != step.final.kind:
        return "target_type_changed"
    return "content_changed"


def _blob_problem(store: BlackboxStore, observation: PathObservation) -> str | None:
    if not observation.exists or observation.kind != "file":
        return None
    if not observation.bytes_captured or not observation.blob:
        return "bytes_not_captured"
    try:
        store.blobs.get(observation.blob)
    except BlobMissingError:
        return "blob_missing"
    except BlobCorruptError:
        return "blob_corrupt"
    return None


def _action_for(initial: PathObservation, final: PathObservation) -> str:
    if initial.same_content_as(final):
        return "none"
    if initial.exists and initial.kind == "file":
        return "restore"
    if initial.exists and initial.kind == "dir":
        return "none" if final.kind == "dir" else "restore_dir"
    if not initial.exists and final.kind == "dir":
        return "rmdir"
    if not initial.exists and final.exists:
        return "remove"
    return "none"


# --------------------------------------------------------------------------------- plan ----


def plan_turn_rollback(store: BlackboxStore, *, turn_id: str, workspace_root: Path) -> Plan:
    root = workspace_root.resolve()
    entries = store.entries()
    turns = store.turn_index(entries)
    summary = turns.get(turn_id)
    effects = [record for record in store.effects_for_turn(turn_id, entries=entries) if record.root == str(root)]
    effects.sort(key=lambda record: int(record.intended.get("seq") or 0))
    already_committed = any(
        e.get("kind") == "rollback_committed" and e.get("rollback_of_turn") == turn_id and e.get("root") == str(root)
        for e in entries
    )
    resumed = any(
        e.get("kind") == "rollback_started" and e.get("rollback_of_turn") == turn_id and e.get("root") == str(root)
        for e in entries
    ) and not already_committed
    plan = Plan(turn_id, root, [], [], "", resumed, already_committed, effects)
    if summary is None or not effects:
        plan.unrecoverable = "unknown_turn"
        return plan
    if already_committed:
        return plan
    if summary.pruned:
        plan.unrecoverable = "blobs_pruned"
        return plan

    by_path: dict[str, list[EffectRecord]] = {}
    for record in effects:
        if record.intended.get("path_unresolved") or not record.path:
            continue
        by_path.setdefault(record.path, []).append(record)

    for path, records in by_path.items():
        first, last = records[0], records[-1]
        if last.terminal is None:
            plan.unrecoverable = "effect_still_open"
            return plan
        if last.outcome == OUTCOME_UNKNOWN_CRASHED:
            reconciled = str((last.terminal.get("recovery") or {}).get("reconciled") or "")
            if reconciled not in {"matches_before", "matches_intended_after"}:
                plan.unrecoverable = f"crashed_effect_unreconciled:{reconciled or 'unknown'}"
                return plan
        initial = first.before
        final = last.after or initial
        target = root / path
        step = Step(
            path=path, target=target, initial=initial, final=final,
            effect_ids=[r.effect_id for r in records], action=_action_for(initial, final),
            missing_parents=list(first.intended.get("missing_parents") or []),
        )
        if step.action == "restore_dir":
            plan.unrecoverable = "directory_removed_by_turn_not_restorable"
            return plan
        current = observe_path(target)
        reason = _diagnose_conflict(root, step, current)
        if reason is None and step.action == "restore":
            reason = _blob_problem(store, initial)
            if reason == "bytes_not_captured":
                plan.unrecoverable = reason
                return plan
        if reason is not None:
            plan.conflicts.append(
                {
                    "path": path,
                    "reason": reason,
                    "expected_sha256": final.sha256,
                    "current_sha256": current.sha256,
                }
            )
            continue
        if current.same_content_as(step.initial) and not current.same_content_as(step.final):
            step.state = "already"
        plan.steps.append(step)
    return plan


# ------------------------------------------------------------------------------ execute ----


def _fsync_directory(path: Path) -> None:
    with contextlib.suppress(OSError):
        fd = os.open(str(path), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _restore_file_bytes(target: Path, data: bytes, mode: int | None) -> None:
    """Write exact bytes via a same-directory temp file and an atomic replace; never in place
    (an in-place write through a hard link would edit the other name). Module-global on purpose:
    the interruption test kills the process from here."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=f".{target.name}.", suffix=".bbrestore")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, mode if isinstance(mode, int) else 0o644)
        if target.is_symlink():
            raise RuntimeError("target became a symlink during restore")
        os.replace(tmp_name, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise
    _fsync_directory(target.parent)


def _rollback_identity(source_context: dict[str, Any], *, task_id: str) -> EffectIdentity:
    base = identity_from_context(source_context, task_id=task_id)
    return EffectIdentity(**{**base.to_dict(), "turn_id": f"rollback:{uuid.uuid4().hex}"})


def execute_turn_rollback(
    store: BlackboxStore,
    *,
    turn_id: str,
    workspace_root: Path,
    source_context: dict[str, Any],
    operator: str,
    nonce: str,
    task_id: str = "",
) -> dict[str, Any]:
    """Plan under the store lock, then act step by step. Returns a typed summary dict."""
    root = workspace_root.resolve()
    authority_payload = source_context.get("_blackbox_permission")
    if not isinstance(authority_payload, dict):
        authority_payload = {"effect": "none", "mode": "", "actions": [], "reason": "no decision was taken"}
    with store.exclusive():
        store.recover_open_effects()
        chain = store.verify()
        if not chain.ok:
            return {"status": STATUS_INTEGRITY, "reason": chain.reason, "first_bad_seq": chain.first_bad_seq,
                    "entries": chain.entries, "head_count": chain.head_count}
        plan = plan_turn_rollback(store, turn_id=turn_id, workspace_root=root)
        if plan.already_committed:
            return {"status": STATUS_ALREADY, "rollback_of_turn": turn_id, "restored_paths": [], "removed_paths": [],
                    "already_restored_paths": [], "resumed": False}
        if plan.unrecoverable == "unknown_turn":
            return {"status": STATUS_UNKNOWN_TURN, "rollback_of_turn": turn_id, "reason": "unknown_turn"}
        if plan.unrecoverable:
            store.append({"schema": SCHEMA, "kind": "rollback_refused", "rollback_of_turn": turn_id, "root": str(root),
                          "operator": operator, "nonce": nonce, "reason": plan.unrecoverable, "conflicts": []})
            return {"status": STATUS_UNRECOVERABLE, "rollback_of_turn": turn_id, "reason": plan.unrecoverable}
        if plan.conflicts:
            store.append({"schema": SCHEMA, "kind": "rollback_refused", "rollback_of_turn": turn_id, "root": str(root),
                          "operator": operator, "nonce": nonce, "reason": "conflict", "conflicts": plan.conflicts})
            return {"status": STATUS_CONFLICT, "rollback_of_turn": turn_id, "conflicts": plan.conflicts}

        identity = _rollback_identity(source_context, task_id=task_id or f"rollback:{turn_id}")
        store.append({"schema": SCHEMA, "kind": "rollback_started", "rollback_of_turn": turn_id, "root": str(root),
                      "operator": operator, "nonce": nonce, "rollback_turn_id": identity.turn_id, "resumed": plan.resumed,
                      "steps": [{"path": s.path, "action": s.action, "state": s.state} for s in plan.steps]})
        restored: list[str] = []
        removed: list[str] = []
        already: list[str] = [s.path for s in plan.steps if s.state == "already"]
        left_nonempty: list[str] = []
        base = {"schema": SCHEMA, **identity.to_dict(), "root": str(root), "intent": "workspace.rollback_last_change",
                "authority": dict(authority_payload), "rollback_of_turn": turn_id, "group_id": identity.turn_id}
        for step in reversed(plan.steps):
            if step.state == "already" or step.action == "none":
                continue
            current = observe_path(step.target)
            reason = _diagnose_conflict(root, step, current)
            if reason is not None:
                # The world moved between plan and act. Stop here; nothing else is touched, the
                # journal shows exactly how far this got, and the next attempt resumes.
                store.append({"schema": SCHEMA, "kind": "rollback_refused", "rollback_of_turn": turn_id, "root": str(root),
                              "operator": operator, "nonce": nonce, "reason": "conflict_during_execution",
                              "conflicts": [{"path": step.path, "reason": reason, "expected_sha256": step.final.sha256,
                                             "current_sha256": current.sha256}]})
                return {"status": STATUS_CONFLICT, "rollback_of_turn": turn_id, "restored_paths": restored,
                        "removed_paths": removed, "conflicts": [{"path": step.path, "reason": reason,
                                                                 "expected_sha256": step.final.sha256,
                                                                 "current_sha256": current.sha256}]}
            if current.same_content_as(step.initial):
                already.append(step.path)
                continue
            effect_id = f"effect-{uuid.uuid4().hex}"
            started_at = utcnow()
            before = store.snapshot(step.target)
            store.append({**base, "kind": "effect_intended", "effect_id": effect_id, "path": step.path,
                          "path_unresolved": False, "unresolved_reason": "", "operation": step.action,
                          "rollback_of_effect": step.effect_ids[-1], "rollback_of_effects": list(step.effect_ids),
                          "before": before.to_dict(),
                          "intended": {"description": f"rollback {step.action}", "after_sha256": step.initial.sha256 or None,
                                       "after_exists": step.initial.exists},
                          "missing_parents": [], "started_at": started_at})
            outcome = OUTCOME_SUCCEEDED
            error = ""
            try:
                if step.action == "restore":
                    data = store.blobs.get(str(step.initial.blob))
                    _restore_file_bytes(step.target, data, step.initial.mode)
                    restored.append(step.path)
                elif step.action == "remove":
                    if step.target.is_symlink():
                        raise RuntimeError("target became a symlink before remove")
                    step.target.unlink()
                    _fsync_directory(step.target.parent)
                    removed.append(step.path)
                    for parent in step.missing_parents:
                        parent_path = root / parent
                        try:
                            parent_path.rmdir()
                            removed.append(parent)
                        except OSError:
                            left_nonempty.append(parent)
                            break
                elif step.action == "rmdir":
                    step.target.rmdir()
                    _fsync_directory(step.target.parent)
                    removed.append(step.path)
            except Exception as exc:
                outcome = OUTCOME_FAILED
                error = f"{type(exc).__name__}: {exc}"
            after = store.snapshot(step.target)
            terminal = {**base, "kind": "effect_terminal", "effect_id": effect_id, "path": step.path, "operation": step.action,
                        "rollback_of_effect": step.effect_ids[-1], "outcome": outcome, "status": "executed" if not error else "error",
                        "ok": not error, "after": after.to_dict(), "started_at": started_at, "completed_at": utcnow()}
            if error:
                terminal["exception"] = error
            store.append(terminal)
            if error:
                return {"status": STATUS_CONFLICT, "rollback_of_turn": turn_id, "restored_paths": restored,
                        "removed_paths": removed, "conflicts": [{"path": step.path, "reason": f"restore_failed:{error}",
                                                                 "expected_sha256": step.final.sha256,
                                                                 "current_sha256": after.sha256}]}
        store.append({"schema": SCHEMA, "kind": "rollback_committed", "rollback_of_turn": turn_id, "root": str(root),
                      "operator": operator, "nonce": nonce, "rollback_turn_id": identity.turn_id,
                      "restored_paths": restored, "removed_paths": removed, "already_restored_paths": already,
                      "left_nonempty_dirs": left_nonempty, "resumed": plan.resumed})
        return {"status": "executed", "rollback_of_turn": turn_id, "rollback_turn_id": identity.turn_id,
                "restored_paths": restored, "removed_paths": removed, "already_restored_paths": already,
                "left_nonempty_dirs": left_nonempty, "resumed": plan.resumed}


# ------------------------------------------------------------------- the intent seam ----


def execute_rollback_directive(directive: dict[str, Any], *, workspace_root: Path, source_context: dict[str, Any], store: BlackboxStore | None = None) -> Any:
    """Called by the ``workspace.rollback_last_change`` handler when the server-side directive is
    present. Verifies and consumes the operator token, then plans and executes."""
    from core.execution.models import _tool_observation
    from core.runtime_execution_tools import RuntimeExecutionResult

    store = store or default_store()
    turn_id = str(directive.get("turn_id") or "")
    operator = str(directive.get("operator") or "")
    root = workspace_root.resolve()

    def _result(ok: bool, status: str, text: str, summary: dict[str, Any]) -> RuntimeExecutionResult:
        return RuntimeExecutionResult(
            handled=True, ok=ok, status=status, response_text=text,
            details={
                "blackbox_rollback": summary,
                "restored_paths": list(summary.get("restored_paths") or []),
                "removed_paths": list(summary.get("removed_paths") or []),
                "observation": _tool_observation(intent="workspace.rollback_last_change", tool_surface="blackbox",
                                                 ok=ok, status=status, rollback_of_turn=turn_id),
            },
        )

    verdict = authority_module.verify_rollback_authorization(
        str(directive.get("authorization") or ""), turn_id=turn_id, workspace_root=root, store=store
    )
    if not verdict.ok:
        with contextlib.suppress(Exception):
            store.append({"schema": SCHEMA, "kind": "rollback_refused", "rollback_of_turn": turn_id, "root": str(root),
                          "operator": operator, "nonce": verdict.nonce, "reason": f"not_authorized:{verdict.reason}", "conflicts": []})
        return _result(False, STATUS_NOT_AUTHORIZED,
                       f"Rollback of turn `{turn_id}` was not authorized ({verdict.reason}). Nothing was written.",
                       {"rollback_of_turn": turn_id, "reason": verdict.reason, "restored_paths": [], "removed_paths": []})
    authority_module.consume_rollback_authorization(verdict.nonce, store=store, reason="rollback_attempt")
    summary = execute_turn_rollback(store, turn_id=turn_id, workspace_root=root, source_context=source_context,
                                    operator=operator, nonce=verdict.nonce,
                                    task_id=str(source_context.get("_blackbox_task_id") or ""))
    status = str(summary.get("status") or "")
    if status == "executed":
        lines = [f"Rolled back turn `{turn_id}` from the Blackbox journal."]
        lines += [f"- restored `{p}`" for p in summary.get("restored_paths") or []]
        lines += [f"- removed `{p}`" for p in summary.get("removed_paths") or []]
        lines += [f"- already at its original bytes: `{p}`" for p in summary.get("already_restored_paths") or []]
        return _result(True, "executed", "\n".join(lines), summary)
    if status == STATUS_ALREADY:
        return _result(True, STATUS_ALREADY, f"Turn `{turn_id}` was already rolled back; nothing was written.", summary)
    if status == STATUS_CONFLICT:
        lines = [f"- `{c.get('path')}`: {c.get('reason')}" for c in summary.get("conflicts") or []]
        return _result(False, STATUS_CONFLICT,
                       f"I did not roll back turn `{turn_id}`: the workspace no longer matches what that turn left "
                       "on disk, so restoring would overwrite something that happened since. Nothing was written.\n"
                       + "\n".join(lines), summary)
    if status == STATUS_INTEGRITY:
        return _result(False, STATUS_INTEGRITY,
                       f"The Blackbox journal does not verify ({summary.get('reason')} at entry "
                       f"{summary.get('first_bad_seq')}); no rollback runs on an unverified journal.", summary)
    if status == STATUS_UNKNOWN_TURN:
        return _result(False, STATUS_UNKNOWN_TURN, f"No Blackbox effects are recorded for turn `{turn_id}` in this workspace.", summary)
    return _result(False, STATUS_UNRECOVERABLE,
                   f"Turn `{turn_id}` cannot be restored: {summary.get('reason')}. Nothing was written.", summary)


__all__ = [
    "STATUS_ALREADY",
    "STATUS_CONFLICT",
    "STATUS_INTEGRITY",
    "STATUS_NOT_AUTHORIZED",
    "STATUS_UNKNOWN_TURN",
    "STATUS_UNRECOVERABLE",
    "Plan",
    "Step",
    "execute_rollback_directive",
    "execute_turn_rollback",
    "plan_turn_rollback",
]
