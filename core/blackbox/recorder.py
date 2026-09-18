"""The recording seam: wrap one authorized workspace mutation so the journal holds INTENDED before
any byte moves and TERMINAL before any caller hears "done".

Order, per effect:

    resolve + snapshot before (blob) -> append INTENDED -> handler -> snapshot after (blob)
        -> append TERMINAL -> return the handler's result (+ details["blackbox"])

What the caller sees when the recorder itself cannot do its job:

- INTENDED cannot be appended: the handler never runs; the result is a typed
  ``blackbox_unavailable`` refusal and the workspace is untouched.
- TERMINAL cannot be appended: the bytes may already be on disk. The result is ``ok=False`` with
  status ``blackbox_unrecorded``, carrying the handler's own result verbatim under
  ``details["handler_result"]`` -- the effect happened, and nobody is told it succeeded until a
  terminal record exists. A later recovery pass closes the open INTENDED as ``unknown_crashed``.

Path candidates come from the same argument the handler reads (``path``) or, for a unified
diff, from the same header parser the patch engine uses. A handler that then reports a path the
recorder had not snapshotted produces a ``coverage_gap`` entry; it is never silently absorbed.
"""
from __future__ import annotations

import contextlib
import hashlib
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from core.blackbox import authority as authority_module
from core.blackbox.identity import identity_from_context
from core.blackbox.snapshot import MISSING, PathObservation, canonical_relative, missing_ancestors, observe_path
from core.blackbox.store import (
    OUTCOME_FAILED,
    OUTCOME_INTERRUPTED,
    OUTCOME_NO_CHANGE,
    OUTCOME_REFUSED,
    OUTCOME_SUCCEEDED,
    OUTCOME_UNKNOWN,
    BlackboxStore,
    default_store,
    utcnow,
)

SCHEMA = "blackbox_effect_v1"
STATUS_UNAVAILABLE = "blackbox_unavailable"
STATUS_UNRECORDED = "blackbox_unrecorded"

# intent -> operation family. Anything not here is journaled as ``unscoped`` (no paths known
# ahead of the handler), which status() and the evidence report treat as a coverage gap.
RECORDED_INTENTS: dict[str, str] = {
    "workspace.write_file": "write",
    "workspace.replace_in_file": "replace",
    "workspace.apply_unified_diff": "patch",
    "workspace.ensure_directory": "mkdir",
    "workspace.rollback_last_change": "restore",
}


def _result_type():
    from core.runtime_execution_tools import RuntimeExecutionResult

    return RuntimeExecutionResult


def _result_to_dict(result: Any) -> dict[str, Any]:
    return {
        "handled": bool(getattr(result, "handled", True)),
        "ok": bool(getattr(result, "ok", False)),
        "status": str(getattr(result, "status", "") or ""),
        "response_text": str(getattr(result, "response_text", "") or ""),
        "details": dict(getattr(result, "details", {}) or {}),
    }


def _sha_text(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def candidate_paths(intent: str, arguments: dict[str, Any], *, workspace_root: Path, session_id: str) -> list[str]:
    """Raw path strings the handler for ``intent`` will touch, read from the same inputs it reads."""
    if intent in {"workspace.write_file", "workspace.replace_in_file", "workspace.ensure_directory"}:
        raw = str(arguments.get("path") or "").strip()
        return [raw] if raw else []
    if intent == "workspace.apply_unified_diff":
        from core.execution.workspace_tools import _extract_patch_paths

        patch_text = str(arguments.get("patch") or arguments.get("diff") or "").strip()
        return list(_extract_patch_paths(patch_text))
    if intent == "workspace.rollback_last_change":
        from core.execution.artifacts import latest_workspace_mutation

        record = latest_workspace_mutation(session_id=session_id or None, workspace_root=workspace_root)
        if not record:
            return []
        return [str(change.get("path") or "") for change in list(record.get("changes") or []) if str(change.get("path") or "")]
    return []


def intended_change(intent: str, arguments: dict[str, Any], before: PathObservation) -> dict[str, Any]:
    if intent == "workspace.write_file":
        content = str(arguments.get("content") or "")
        return {"description": "write full content", "after_sha256": _sha_text(content), "after_exists": True}
    if intent == "workspace.ensure_directory":
        return {"description": "ensure directory exists", "after_sha256": None, "after_exists": True}
    if intent == "workspace.replace_in_file":
        return {"description": "replace text", "after_sha256": None, "after_exists": True}
    if intent == "workspace.apply_unified_diff":
        return {"description": "apply unified diff", "after_sha256": None, "after_exists": None}
    if intent == "workspace.rollback_last_change":
        return {"description": "restore last tracked mutation", "after_sha256": None, "after_exists": None}
    return {"description": intent, "after_sha256": None, "after_exists": None}


def operation_for(intent: str, before: PathObservation) -> str:
    family = RECORDED_INTENTS.get(intent, "unscoped")
    if family == "write":
        return "create" if not before.exists else "write"
    if family == "patch" and not before.exists:
        return "create"
    return family


def _result_paths(result: Any) -> list[str]:
    details = dict(getattr(result, "details", {}) or {})
    out: list[str] = []
    single = str(details.get("path") or "").strip()
    if single:
        out.append(single)
    for key in ("paths", "changed_paths", "restored_paths", "removed_paths"):
        for item in list(details.get(key) or []):
            text = str(item).strip()
            if text and text not in out:
                out.append(text)
    return out


def _outcome_for(result: Any, before: PathObservation, after: PathObservation) -> str:
    if bool(getattr(result, "ok", False)):
        return OUTCOME_NO_CHANGE if after.same_content_as(before) else OUTCOME_SUCCEEDED
    status = str(getattr(result, "status", "") or "")
    if status == "unknown_outcome_reconciliation_required":
        return OUTCOME_UNKNOWN
    return OUTCOME_REFUSED if after.same_content_as(before) else OUTCOME_FAILED


def _refusal(intent: str, status: str, text: str, **details: Any) -> Any:
    from core.execution.models import _tool_observation

    payload = dict(details)
    payload["observation"] = _tool_observation(intent=intent, tool_surface="blackbox", ok=False, status=status)
    return _result_type()(handled=True, ok=False, status=status, response_text=text, details=payload)


class _Planned:
    __slots__ = ("before", "effect_id", "intended", "missing_parents", "operation", "path", "raw", "target", "unresolved")

    def __init__(self, raw: str) -> None:
        self.effect_id = f"effect-{uuid.uuid4().hex}"
        self.raw = raw
        self.path = ""
        self.target: Path | None = None
        self.before = MISSING
        self.intended: dict[str, Any] = {}
        self.operation = ""
        self.missing_parents: list[str] = []
        self.unresolved = ""


def recorded_workspace_mutation(
    intent: str,
    arguments: dict[str, Any] | None,
    *,
    workspace_root: Path,
    source_context: dict[str, Any] | None,
    handler: Callable[[], Any],
    store: BlackboxStore | None = None,
) -> Any:
    """Run ``handler`` inside the flight recorder. Returns the handler's result with
    ``details["blackbox"]`` attached, or a typed recorder refusal (see module docstring)."""
    context = dict(source_context or {})
    arguments = dict(arguments or {})
    if intent == "workspace.rollback_last_change" and authority_module.directive_from_context(context) is not None:
        # The Blackbox turn rollback journals every restore itself, under the rollback's own
        # identity, with linkage to the original effects. Wrapping it again would double-record.
        return handler()

    store = store or default_store()
    identity = identity_from_context(context)
    root = workspace_root.resolve()
    session_id = identity.session_id
    authority_payload = context.get("_blackbox_permission")
    if not isinstance(authority_payload, dict):
        authority_payload = {"effect": "none", "mode": "", "actions": [], "reason": "no decision was taken"}

    from core.execution.workspace_tools import resolve_workspace_path

    planned: list[_Planned] = []
    seen: set[str] = set()
    for raw in candidate_paths(intent, arguments, workspace_root=root, session_id=session_id):
        item = _Planned(raw)
        try:
            target = resolve_workspace_path(raw, workspace_root=root)
            item.target = target
            item.path = canonical_relative(root, target) if target != root else "."
        except (ValueError, OSError) as exc:
            item.unresolved = f"{type(exc).__name__}: {exc}"
        if item.path in seen and item.path:
            continue
        seen.add(item.path)
        planned.append(item)

    try:
        store.ensure_recovered()
    except Exception as exc:  # recovery must not block; the open effect stays visible in status()
        recovery_error = f"{type(exc).__name__}: {exc}"
    else:
        recovery_error = ""

    group_id = f"group-{uuid.uuid4().hex}" if len(planned) > 1 else ""
    started_at = utcnow()
    base = {
        "schema": SCHEMA,
        **identity.to_dict(),
        "root": str(root),
        "intent": intent,
        "authority": dict(authority_payload),
        "group_id": group_id,
    }
    intended_entries: list[dict[str, Any]] = []
    try:
        for item in planned:
            if item.target is not None and not item.unresolved:
                item.before = store.snapshot(item.target)
                item.missing_parents = missing_ancestors(root, item.target) if not item.before.exists else []
            item.intended = intended_change(intent, arguments, item.before)
            item.operation = operation_for(intent, item.before)
            entry = {
                **base,
                "kind": "effect_intended",
                "effect_id": item.effect_id,
                "path": item.path or item.raw,
                "path_unresolved": bool(item.unresolved),
                "unresolved_reason": item.unresolved,
                "operation": item.operation,
                "before": item.before.to_dict(),
                "intended": item.intended,
                "missing_parents": item.missing_parents,
                "started_at": started_at,
            }
            intended_entries.append(store.append(entry))
        if not planned:
            intended_entries.append(
                store.append({**base, "kind": "effect_intended", "effect_id": f"effect-{uuid.uuid4().hex}", "path": "",
                              "path_unresolved": True, "unresolved_reason": "no path candidates for this intent",
                              "operation": "unscoped", "before": MISSING.to_dict(),
                              "intended": intended_change(intent, arguments, MISSING), "missing_parents": [],
                              "started_at": started_at})
            )
    except Exception as exc:
        return _refusal(
            intent,
            STATUS_UNAVAILABLE,
            f"`{intent}` was not executed: the Blackbox journal could not record the intended effect "
            f"({type(exc).__name__}: {exc}). An effect that cannot be recorded does not run.",
            executed=False,
            blackbox={"turn_id": identity.turn_id, "terminal_recorded": False, "journal_error": f"{type(exc).__name__}: {exc}"},
        )

    def _observe_after(item: _Planned) -> PathObservation:
        if item.target is None or item.unresolved:
            return MISSING
        try:
            return store.snapshot(item.target)
        except Exception:
            return observe_path(item.target)

    def _terminals(result: Any | None, *, outcome_override: str = "", exception: str = "") -> list[dict[str, Any]]:
        completed_at = utcnow()
        written: list[dict[str, Any]] = []
        for item in planned or [None]:
            if item is None:
                after = MISSING
                before = MISSING
                path = ""
                effect_id = str(intended_entries[0]["effect_id"])
                operation = "unscoped"
            else:
                after = _observe_after(item)
                before = item.before
                path = item.path or item.raw
                effect_id = item.effect_id
                operation = item.operation
            if outcome_override:
                outcome = outcome_override
            elif result is None:
                outcome = OUTCOME_FAILED  # the handler raised: nothing it did is a considered refusal
            else:
                outcome = _outcome_for(result, before, after)
            entry = {
                **base,
                "kind": "effect_terminal",
                "effect_id": effect_id,
                "path": path,
                "operation": operation,
                "outcome": outcome,
                "status": str(getattr(result, "status", "") or "") if result is not None else "",
                "ok": bool(getattr(result, "ok", False)) if result is not None else False,
                "after": after.to_dict(),
                "started_at": started_at,
                "completed_at": completed_at,
            }
            if exception:
                entry["exception"] = exception
            written.append(store.append(entry))
        return written

    try:
        result = handler()
    except BaseException as exc:  # the terminal is written for every exit; then the exit continues
        from core.effect_reconciliation import EffectOutcomeUnknown

        if isinstance(exc, EffectOutcomeUnknown):
            override = OUTCOME_UNKNOWN
        elif isinstance(exc, Exception):
            override = ""
        else:
            override = OUTCOME_INTERRUPTED
        with contextlib.suppress(Exception):
            _terminals(None, outcome_override=override, exception=f"{type(exc).__name__}: {exc}")
        raise

    try:
        terminal_entries = _terminals(result)
    except Exception as exc:
        return _refusal(
            intent,
            STATUS_UNRECORDED,
            f"`{intent}` ran, but the Blackbox could not record its terminal state "
            f"({type(exc).__name__}: {exc}). Its outcome is not claimed until a terminal record exists; "
            "the handler's own result is attached under details.handler_result.",
            executed=True,
            handler_result=_result_to_dict(result),
            blackbox={"turn_id": identity.turn_id, "effect_ids": [e["effect_id"] for e in intended_entries],
                      "terminal_recorded": False, "journal_error": f"{type(exc).__name__}: {exc}"},
        )

    recorded_paths = {item.path for item in planned if item.path}
    coverage_gap = [path for path in _result_paths(result) if path not in recorded_paths and path != "."]
    if coverage_gap and result is not None and bool(getattr(result, "ok", False)):
        with contextlib.suppress(Exception):
            store.append({**base, "kind": "coverage_gap", "paths": coverage_gap, "effect_ids": [e["effect_id"] for e in intended_entries]})
    with contextlib.suppress(Exception):
        store.maybe_auto_prune()

    details = dict(getattr(result, "details", {}) or {})
    details["blackbox"] = {
        "turn_id": identity.turn_id,
        "effect_ids": [e["effect_id"] for e in intended_entries],
        "terminal_recorded": True,
        "bytes_captured": all(item.before.bytes_captured for item in planned),
        "coverage_gap": coverage_gap,
        "recovery_error": recovery_error,
        "outcomes": {e["effect_id"]: e["outcome"] for e in terminal_entries},
    }
    return type(result)(
        handled=bool(getattr(result, "handled", True)),
        ok=bool(getattr(result, "ok", False)),
        status=str(getattr(result, "status", "") or ""),
        response_text=str(getattr(result, "response_text", "") or ""),
        details=details,
    )


__all__ = [
    "RECORDED_INTENTS",
    "STATUS_UNAVAILABLE",
    "STATUS_UNRECORDED",
    "candidate_paths",
    "recorded_workspace_mutation",
]
