"""The coverage recorder: run one mutation-capable tool between two durable Blackbox observations.

This is the wrapper for every mutation path whose targets are not statically known or that lives
outside the v1 workspace-file intents -- shell commands, formatters/test runners, machine writes,
moves, plugin calls, MCP calls, coding-lane edits. The v1 flight recorder
(``core.blackbox.recorder``) stays the authority for ``workspace.*`` file intents; both write the
same schema into the same journal, CAS and store.

Protocol, per strategy:

- ``workspace_scan``: scan+capture the workspace BEFORE (content-addressed preimages in the
  verified CAS, manifest hash, bounds, degraded flag) -> append ``coverage_scan_intended`` ->
  run the handler -> scan AFTER -> append ``coverage_scan_terminal`` carrying the drift: every
  changed/created/removed path with before+after observations, high-risk kinds and per-path
  rollback capability. Success publishes only after the terminal record is durable.
- ``declared_paths``: snapshot each declared target before (v1 protocol: ``effect_intended`` per
  path with the before blob) -> handler -> ``effect_terminal`` per path with after blobs. A
  handler that then reports paths the declaration missed produces ``coverage_gap`` entries,
  exactly like the v1 recorder.
- ``postimage_only``: nothing before (the effect is not undoable); after the handler, the
  reported paths are observed and journaled ``coverage_post_terminal`` -- the local mutation
  ENTERS the Blackbox with its postimage and never claims rollback.
- ``receipt_only`` (external): not wrapped here. The effect authority's own approval and receipt
  govern it; the capability exists so the declaration cannot lie and call it rollback-capable.

Fail-closed rules enforced BEFORE the handler runs:

- no capability declaration -> the executor refuses the dispatch (``blackbox_coverage_required``);
- declared or command-named targets classifying high-risk (credentials, Git metadata, startup,
  configuration, release keys) -> typed refusal ``blackbox_high_risk_refused`` naming kinds and
  paths only, unless the operator allowed exactly that for this turn;
- a scan-strategy capability with no workspace root -> typed refusal
  ``blackbox_coverage_no_workspace_root`` (an unobservable mutation does not run).

Crash/interruption: the INTENDED record is durable before the handler; if the process dies before
the terminal, the store's recovery pass closes it ``unknown_crashed`` with the drift reconciled
against the disk, and -- preimages being in the CAS -- the restore path can still act.

No secret bytes in logs: journal entries carry observations (hashes, sizes, modes, blob ids)
ONLY. The recorder strips any content-looking key from what it writes, and high-risk drift items
keep kind+hash+path. Bytes live in the CAS (0600), never in the journal.
"""
from __future__ import annotations

import contextlib
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from core.blackbox.coverage import highrisk
from core.blackbox.coverage.capability import (
    STRATEGY_DECLARED_PATHS,
    STRATEGY_POSTIMAGE_ONLY,
    STRATEGY_WORKSPACE_SCAN,
    MutationCapability,
)
from core.blackbox.coverage.registry import capability_for
from core.blackbox.coverage.scan import (
    ScanBounds,
    ScanResult,
    declared_targets_for_command,
    diff_scans,
    scan_workspace,
)
from core.blackbox.identity import identity_from_context
from core.blackbox.snapshot import MISSING, PathObservation
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
STATUS_COVERAGE_REQUIRED = "blackbox_coverage_required"
STATUS_HIGH_RISK = "blackbox_high_risk_refused"
STATUS_NO_ROOT = "blackbox_coverage_no_workspace_root"
STATUS_UNAVAILABLE = "blackbox_unavailable"
STATUS_UNRECORDED = "blackbox_unrecorded"
STATUS_CAPTURE_INCOMPLETE = "blackbox_reversible_capture_incomplete"
STATUS_KEY_UNAVAILABLE = "blackbox_key_unavailable"
STATUS_DOWNGRADE_REQUIRED = "blackbox_irreversible_downgrade_required"

_FORBIDDEN_PAYLOAD_KEYS = frozenset({"content", "bytes", "stdout_raw", "preimage", "postimage", "body"})
HIGH_RISK_ALLOW_KEY = "_blackbox_high_risk_allowed"
#: Operator-approved irreversible downgrade: {operator: str, reason: str}. Present and well-formed
#: BEFORE execution, a reversible capability may run degraded as postimage-only, journaled first.
#: Never accepted after the fact -- there is no code path that flips reversibility post-execution.
DOWNGRADE_KEY = "_blackbox_irreversible_downgrade"


def _keyring_for(store: BlackboxStore):
    keyring = getattr(store, "cas_keyring", None)
    if keyring is None:
        from storage.blackbox.cas import UnavailableCAS

        if isinstance(getattr(store.blobs, "cas", None), UnavailableCAS):
            from core.blackbox.coverage.cas_keys import CasKeyError

            raise CasKeyError("the CAS keyring is unavailable")
    return keyring


def _opaque_row(row: dict[str, Any], keyring) -> dict[str, Any]:
    """A journal-safe observation row: the raw content digest is replaced by the keyed opaque
    content id (equal plaintext -> equal id, no public digest); bytes stay only in the CAS."""
    import copy

    out = copy.deepcopy(row)
    raw = str(out.pop("sha256", "") or "")
    if raw:
        if keyring is None:
            from core.blackbox.coverage.cas_keys import CasKeyError

            raise CasKeyError("cannot journal a content id without the CAS keyring")
        out["content_id"] = keyring.id_for(raw)
    return out


def _capture_complete(files: dict[str, dict[str, Any]], *, degraded: bool) -> tuple[bool, list[str]]:
    """(complete, offending paths): every existing file has a captured, restorable preimage blob
    (or proven missing state); the scan is not degraded."""
    if degraded:
        return False, ["<scan-degraded>"]
    offending = [rel for rel, row in files.items() if row.get("exists") and (row.get("capture_skipped") or not row.get("blob"))]
    return (not offending), offending
#: ``source_context`` keys the runtime may carry a workspace root under, read in this order.
_WORKSPACE_ROOT_KEYS = ("workspace_root", "workspace_root_str", "root", "council_workspace")


def workspace_root_from_context(source_context: dict[str, Any] | None) -> Path | None:
    """The workspace root, derived by the SAME authority the runtime dispatch uses
    (``runtime_execution_tools._workspace_root``), so a coverage scan can never root itself
    somewhere the sandbox would not."""
    try:
        from core.runtime_execution_tools import _workspace_root

        return _workspace_root(source_context)
    except Exception:
        for key in _WORKSPACE_ROOT_KEYS:
            raw = str((source_context or {}).get(key) or "").strip()
            if raw:
                with contextlib.suppress(OSError):
                    return Path(raw).expanduser().resolve()
    return None


def _scrub(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if str(key) not in _FORBIDDEN_PAYLOAD_KEYS}


def _result_type():
    from core.runtime_execution_tools import RuntimeExecutionResult

    return RuntimeExecutionResult


def _default_result_factory(intent: str, status: str, text: str, details: dict[str, Any]) -> Any:
    from core.execution.models import _tool_observation

    payload = _scrub(dict(details))
    payload.setdefault(
        "observation", _tool_observation(intent=intent, tool_surface="blackbox_coverage", ok=False, status=status)
    )
    return _result_type()(handled=True, ok=False, status=status, response_text=text, details=payload)


def _refusal_with(
    factory: Callable[[str, str, str, dict[str, Any]], Any],
    intent: str,
    status: str,
    text: str,
    **details: Any,
) -> Any:
    return factory(intent, status, text, _scrub(dict(details)))


def _result_paths(result: Any) -> list[str]:
    details = dict(getattr(result, "details", {}) or {})
    out: list[str] = []
    for key in ("path", "target", "dest", "destination"):
        single = str(details.get(key) or "").strip()
        if single:
            out.append(single)
    for key in ("paths", "changed_paths", "removed_paths", "written_paths", "moved_to", "created_paths"):
        for item in list(details.get(key) or []):
            text = str(item).strip()
            if text and text not in out:
                out.append(text)
    return out


def _outcome_for(result: Any, drift_count: int) -> str:
    if bool(getattr(result, "ok", False)):
        return OUTCOME_SUCCEEDED if drift_count else OUTCOME_NO_CHANGE
    status = str(getattr(result, "status", "") or "")
    return OUTCOME_REFUSED if status in {"pending_approval", "blocked_by_mode", "cancelled"} else OUTCOME_FAILED


def declared_paths_for(intent: str, arguments: dict[str, Any], *, capability: MutationCapability, workspace_root: Path | None) -> list[str]:
    """Absolute paths this dispatch declares it will touch, read from the same inputs the handler reads."""
    declared: list[str] = []
    for key in ("path", "target", "dest", "destination", "to", "output_path"):
        raw = str(arguments.get(key) or "").strip()
        if raw:
            candidate = Path(raw)
            if not candidate.is_absolute() and workspace_root is not None:
                candidate = workspace_root / candidate
            declared.append(str(candidate))
    for key in ("paths", "targets", "outputs"):
        for item in list(arguments.get(key) or []):
            raw = str(item or "").strip()
            if raw:
                candidate = Path(raw)
                if not candidate.is_absolute() and workspace_root is not None:
                    candidate = workspace_root / candidate
                declared.append(str(candidate))
    command = str(arguments.get("command") or arguments.get("cmd") or "").strip()
    if command and workspace_root is not None:
        cwd = workspace_root
        raw_cwd = str(arguments.get("cwd") or "").strip()
        if raw_cwd:
            with contextlib.suppress(OSError):
                cwd = (Path(raw_cwd) if Path(raw_cwd).is_absolute() else workspace_root / raw_cwd).resolve()
        declared.extend(declared_targets_for_command(command, cwd=cwd))
    # `from` is a keyword, so the move-pair spellings are read explicitly.
    for key in ("from", "source", "src"):
        raw = str(arguments.get(key) or "").strip()
        if raw and intent.endswith("move_path"):
            candidate = Path(raw)
            if not candidate.is_absolute() and workspace_root is not None:
                candidate = workspace_root / candidate
            declared.append(str(candidate))
    return declared


def _high_risk_gate(intent: str, declared: list[str], source_context: dict[str, Any]) -> str | None:
    """A refusal reason when a declared target is high-risk and this turn did not allow it."""
    risks = highrisk.high_risk_for_paths(declared)
    if not risks:
        return None
    if (source_context or {}).get(HIGH_RISK_ALLOW_KEY) is True:
        return None
    named = ", ".join(f"{path} ({kind})" for path, kind in sorted(risks.items()))
    return (
        f"`{intent}` was not executed: it names high-risk local paths -- {named}. "
        "Credentials, Git metadata, startup files, configuration and release keys need an explicit "
        "operator allowance for this turn before any byte of them moves."
    )


def _coverage_block(
    capability: MutationCapability,
    *,
    declared: list[str],
    high_risk: dict[str, str],
    bounds: ScanBounds | None = None,
) -> dict[str, Any]:
    return _scrub(
        {
            "capability": capability.to_dict(),
            "declared_paths": declared,
            "high_risk": high_risk,
            "bounds": {"max_files": bounds.max_files, "max_total_capture_bytes": bounds.max_total_capture_bytes} if bounds else None,
        }
    )


def recorded_capability_mutation(
    intent: str,
    arguments: dict[str, Any] | None,
    *,
    handler: Callable[[], Any],
    source_context: dict[str, Any] | None = None,
    workspace_root: Path | None = None,
    declared_paths: list[str] | None = None,
    store: BlackboxStore | None = None,
    bounds: ScanBounds | None = None,
    result_factory: Callable[[str, str, str, dict[str, Any]], Any] | None = None,
) -> Any:
    """Run ``handler`` inside the coverage recorder. Returns the handler's result with
    ``details["blackbox"]`` attached, or a typed recorder refusal (see module docstring).
    ``result_factory`` builds refusals in the CALLING lane's result type (executor plugin/MCP
    calls answer ToolIntentExecution, not RuntimeExecutionResult)."""
    factory = result_factory or _default_result_factory

    def _refusal(intent_name: str, status: str, text: str, **extra: Any) -> Any:
        return _refusal_with(factory, intent_name, status, text, **extra)

    capability = capability_for(intent)
    if capability is None:
        return _refusal(
            intent,
            STATUS_COVERAGE_REQUIRED,
            f"`{intent}` was not executed: it is capable of local mutation and no mutation capability "
            "(scope, reversibility, snapshot strategy, receipt lifecycle, rollback support, recorder) "
            "is declared for it. An undeclared mutation does not run.",
            executed=False,
        )
    context = dict(source_context or {})
    arguments = dict(arguments or {})
    store = store or default_store()
    identity = identity_from_context(context)
    root = Path(workspace_root).resolve() if workspace_root is not None else workspace_root_from_context(context)
    strategy = capability.snapshot_strategy

    # The encrypted CAS key governs everything this recorder journals: without it there is no
    # preimage capture and no opaque content id, so a mutation that needs the Blackbox refuses
    # BEFORE the handler runs. There is no plaintext mode to fall back to.
    try:
        keyring = _keyring_for(store)
    except Exception as exc:
        return _refusal(
            intent,
            STATUS_KEY_UNAVAILABLE,
            f"`{intent}` was not executed: the Blackbox encrypted-CAS key is unavailable "
            f"({type(exc).__name__}: {exc}). An effect that cannot be recorded fails closed; it "
            "does not run unrecorded and it is never stored in plaintext.",
            executed=False,
        )

    declared = list(declared_paths) if declared_paths is not None else declared_paths_for(
        intent, arguments, capability=capability, workspace_root=root
    )
    gate_reason = _high_risk_gate(intent, declared, context)
    if gate_reason is not None:
        risks = highrisk.high_risk_for_paths(declared)
        return _refusal(
            intent,
            STATUS_HIGH_RISK,
            gate_reason,
            executed=False,
            high_risk=risks,
            declared_paths=declared,
        )
    if strategy == STRATEGY_WORKSPACE_SCAN and root is None:
        return _refusal(
            intent,
            STATUS_NO_ROOT,
            f"`{intent}` was not executed: its capability records workspace mutations by scanning the "
            "workspace, and no workspace root is bound to this execution. An unobservable mutation "
            "does not run.",
            executed=False,
        )

    bounds = bounds or ScanBounds()
    base = {
        "schema": SCHEMA,
        **identity.to_dict(),
        "intent": intent,
        **({"root": str(root)} if root is not None else {}),
    }

    with contextlib.suppress(Exception):  # the open effect stays visible in status(); recovery must not block
        store.ensure_recovered()

    started_at = utcnow()
    scan_effect_id = f"effect-{uuid.uuid4().hex}"
    before_scan: ScanResult | None = None
    planned: list[dict[str, Any]] | None = None

    downgrade = context.get(DOWNGRADE_KEY) if isinstance(context.get(DOWNGRADE_KEY), dict) else None
    downgraded = False

    if strategy == STRATEGY_WORKSPACE_SCAN:
        try:
            before_scan = scan_workspace(root, store=store, bounds=bounds)
            # ROLLBACK TRUTH (2026-09-02 amendment): a reversible, unknown-scope effect may run
            # ONLY when every path it could mutate holds a captured restorable preimage or a
            # proven missing-state record. Incomplete bounds refuse here -- BEFORE execution --
            # unless the operator approved an irreversible downgrade for exactly this turn, which
            # is journaled FIRST and switches this execution to postimage-only.
            if capability.effect_class == "reversible":
                complete, offending = _capture_complete(before_scan.files, degraded=before_scan.degraded)
                if not complete:
                    if downgrade is None or not str(downgrade.get("operator") or "").strip():
                        return _refusal(
                            intent,
                            STATUS_CAPTURE_INCOMPLETE,
                            f"`{intent}` was not executed: its capability is reversible, and the "
                            f"preimage scan is incomplete for {len(offending)} path(s) (uncaptured "
                            "or degraded bounds). A reversible effect does not run on top of a "
                            "preimage it cannot restore. Re-run with complete capture, or approve "
                            "an explicit irreversible downgrade (_blackbox_irreversible_downgrade) "
                            "BEFORE execution.",
                            executed=False,
                            incomplete_paths=offending[:32],
                        )
                    store.append(
                        {
                            **base,
                            "kind": "coverage_downgrade",
                            "effect_id": scan_effect_id,
                            "approved_by": str(downgrade.get("operator")),
                            "reason": str(downgrade.get("reason") or ""),
                            **({"ticket": str(downgrade.get("ticket"))} if downgrade.get("ticket") else {}),
                            "from_capability": capability.to_dict(),
                            "to_strategy": STRATEGY_POSTIMAGE_ONLY,
                            "incomplete_paths": offending[:32],
                            "recorded_before_execution": True,
                        }
                    )
                    downgraded = True
            journal_files = {rel: _opaque_row(row, keyring) for rel, row in before_scan.files.items()}
            store.append(
                {
                    **base,
                    "kind": "coverage_scan_intended",
                    "effect_id": scan_effect_id,
                    "coverage": _coverage_block(
                        capability, declared=declared, high_risk=highrisk.high_risk_for_paths(declared), bounds=bounds
                    ),
                    "manifest_sha256": before_scan.manifest_sha256,
                    # The preimage table rides IN the intended entry (MAC'd, hash-chained): after a
                    # crash it is the only durable statement of what the workspace held before the
                    # handler ran, and the recovery pass diffs against it. Rows carry the keyed
                    # OPAQUE content id -- never the raw plaintext digest; bytes stay in the CAS.
                    "files": journal_files,
                    "files_scanned": before_scan.files_scanned,
                    "bytes_captured": before_scan.bytes_captured,
                    "degraded": before_scan.degraded,
                    "downgraded_to_irreversible": downgraded,
                    "started_at": started_at,
                }
            )
        except Exception as exc:
            status = STATUS_KEY_UNAVAILABLE if type(exc).__name__ in {"CasKeyError", "BlobKeyUnavailableError"} else STATUS_UNAVAILABLE
            return _refusal(
                intent,
                status,
                f"`{intent}` was not executed: the Blackbox could not record the intended effect "
                f"({type(exc).__name__}: {exc}). An effect that cannot be recorded does not run.",
                executed=False,
                blackbox={"turn_id": identity.turn_id, "terminal_recorded": False, "journal_error": f"{type(exc).__name__}: {exc}"},
            )
    elif strategy == STRATEGY_DECLARED_PATHS:
        try:
            planned = []
            seen: set[str] = set()
            for raw in declared:
                target = Path(raw)
                rel = str(target)
                if rel in seen:
                    continue
                seen.add(rel)
                observation = store.snapshot(target)
                planned.append({"path": rel, "before": observation.to_dict()})
            # ROLLBACK TRUTH: a reversible declared-path effect may run only when EVERY declared
            # target holds a captured restorable preimage or a proven missing-state record.
            if capability.effect_class == "reversible":
                uncaptured = [
                    item["path"] for item in planned
                    if item["before"].get("exists") and (not item["before"].get("bytes_captured") or not item["before"].get("blob"))
                ]
                if uncaptured:
                    if downgrade is None or not str(downgrade.get("operator") or "").strip():
                        return _refusal(
                            intent,
                            STATUS_CAPTURE_INCOMPLETE,
                            f"`{intent}` was not executed: its capability is reversible, and a "
                            f"declared target has no captured restorable preimage ({len(uncaptured)} "
                            "path(s), e.g. beyond the capture limit). A reversible effect does not "
                            "run on top of a preimage it cannot restore; approve an explicit "
                            "irreversible downgrade BEFORE execution to proceed without rollback.",
                            executed=False,
                            incomplete_paths=uncaptured[:32],
                        )
                    store.append(
                        {
                            **base,
                            "kind": "coverage_downgrade",
                            "effect_id": scan_effect_id,
                            "approved_by": str(downgrade.get("operator")),
                            "reason": str(downgrade.get("reason") or ""),
                            "from_capability": capability.to_dict(),
                            "to_strategy": STRATEGY_POSTIMAGE_ONLY,
                            "incomplete_paths": uncaptured[:32],
                            "recorded_before_execution": True,
                        }
                    )
                    downgraded = True
            group_id = f"group-{uuid.uuid4().hex}" if len(planned) > 1 else ""
            for item in planned:
                entry = store.append(
                    {
                        **base,
                        "kind": "effect_intended",
                        "effect_id": f"effect-{uuid.uuid4().hex}",
                        "path": item["path"],
                        "operation": "coverage_declared",
                        "before": _opaque_row(item["before"], keyring),
                        "intended": {"description": f"{intent} declared target", "after_sha256": None, "after_exists": None},
                        "group_id": group_id,
                        "downgraded_to_irreversible": downgraded,
                        "coverage": _coverage_block(capability, declared=declared, high_risk=highrisk.high_risk_for_paths(declared)),
                        "started_at": started_at,
                    }
                )
                item["effect_id"] = str(entry.get("effect_id"))
            if not planned:
                entry = store.append(
                    {
                        **base,
                        "kind": "effect_intended",
                        "effect_id": scan_effect_id,
                        "path": "",
                        "path_unresolved": True,
                        "unresolved_reason": "no path candidates for this intent",
                        "operation": "unscoped",
                        "before": MISSING.to_dict(),
                        "intended": {"description": intent, "after_sha256": None, "after_exists": None},
                        "coverage": _coverage_block(capability, declared=declared, high_risk={}),
                        "started_at": started_at,
                    }
                )
                planned.append({"path": "", "before": MISSING.to_dict(), "effect_id": str(entry.get("effect_id"))})
        except Exception as exc:
            return _refusal(
                intent,
                STATUS_UNAVAILABLE,
                f"`{intent}` was not executed: the Blackbox could not record the intended effect "
                f"({type(exc).__name__}: {exc}). An effect that cannot be recorded does not run.",
                executed=False,
                blackbox={"turn_id": identity.turn_id, "terminal_recorded": False, "journal_error": f"{type(exc).__name__}: {exc}"},
            )

    try:
        result = handler()
    except BaseException as exc:
        from core.effect_reconciliation import EffectOutcomeUnknown

        override = OUTCOME_UNKNOWN if isinstance(exc, EffectOutcomeUnknown) else (
            OUTCOME_INTERRUPTED if not isinstance(exc, Exception) else ""
        )
        with contextlib.suppress(Exception):
            _write_terminal(
                store,
                base,
                intent=intent,
                capability=capability,
                strategy=strategy,
                root=root,
                declared=declared,
                bounds=bounds,
                started_at=started_at,
                result=None,
                outcome_override=override or OUTCOME_FAILED,
                exception=f"{type(exc).__name__}: {exc}",
                before_scan=before_scan if strategy == STRATEGY_WORKSPACE_SCAN else None,
                planned=planned if strategy == STRATEGY_DECLARED_PATHS else None,
                scan_effect_id=scan_effect_id,
                keyring=keyring,
                downgraded=downgraded,
            )
        raise

    try:
        terminal = _write_terminal(
            store,
            base,
            intent=intent,
            capability=capability,
            strategy=strategy,
            root=root,
            declared=declared,
            bounds=bounds,
            started_at=started_at,
            result=result,
            outcome_override="",
            exception="",
            before_scan=before_scan if strategy == STRATEGY_WORKSPACE_SCAN else None,
            planned=planned if strategy == STRATEGY_DECLARED_PATHS else None,
            scan_effect_id=scan_effect_id,
            keyring=keyring,
            downgraded=downgraded,
        )
    except Exception as exc:
        return _refusal(
            intent,
            STATUS_UNRECORDED,
            f"`{intent}` ran, but the Blackbox could not record its terminal state "
            f"({type(exc).__name__}: {exc}). Its outcome is not claimed until a terminal record "
            "exists; the handler's own result is attached under details.handler_result.",
            executed=True,
            handler_result={
                "handled": bool(getattr(result, "handled", True)),
                "ok": bool(getattr(result, "ok", False)),
                "status": str(getattr(result, "status", "") or ""),
            },
            blackbox={"turn_id": identity.turn_id, "terminal_recorded": False, "journal_error": f"{type(exc).__name__}: {exc}"},
        )

    details = dict(getattr(result, "details", {}) or {})
    details["blackbox"] = {
        "turn_id": identity.turn_id,
        "terminal_recorded": True,
        "coverage": terminal.get("coverage_summary"),
        "effect_ids": terminal.get("effect_ids", []),
    }
    try:
        import dataclasses

        if dataclasses.is_dataclass(result) and not isinstance(result, type):
            return dataclasses.replace(result, details=details)
    except Exception:
        pass
    return type(result)(
        handled=bool(getattr(result, "handled", True)),
        ok=bool(getattr(result, "ok", False)),
        status=str(getattr(result, "status", "") or ""),
        response_text=str(getattr(result, "response_text", "") or ""),
        details=details,
    )


def _write_terminal(
    store: BlackboxStore,
    base: dict[str, Any],
    *,
    intent: str,
    capability: MutationCapability,
    strategy: str,
    root: Path | None,
    declared: list[str],
    bounds: ScanBounds | None,
    started_at: str,
    result: Any | None,
    outcome_override: str,
    exception: str,
    before_scan: ScanResult | None,
    planned: list[dict[str, Any]] | None,
    scan_effect_id: str,
    keyring=None,
    downgraded: bool = False,
) -> dict[str, Any]:
    completed_at = utcnow()
    ok = bool(getattr(result, "ok", False)) if result is not None else False
    effect_ids: list[str] = []

    if strategy == STRATEGY_WORKSPACE_SCAN:
        assert root is not None and before_scan is not None
        after_scan = scan_workspace(root, store=store, bounds=bounds)
        drift = diff_scans(before_scan, after_scan)
        drift_rows: list[dict[str, Any]] = []
        for item in drift:
            risks = highrisk.classify_path(item.path) or highrisk.classify_path(str(Path(root) / item.path))
            drift_rows.append(
                _scrub(
                    {
                        "path": item.path,
                        "drift_kind": item.kind,
                        "before": _opaque_row(item.before, keyring),
                        "after": _opaque_row(item.after, keyring),
                        "high_risk": risks or "",
                        "rollback_capable": bool(item.rollback_capable and not before_scan.degraded and not after_scan.degraded),
                    }
                )
            )
        outcome = outcome_override or _outcome_for(result, len(drift))
        # NEVER downgrade after execution: the capability stays what it declared. An execution
        # that ran under an approved pre-execution downgrade says so; one whose AFTER scan
        # degraded reports it and cannot claim rollback -- but nothing flips reversibility here.
        entry = store.append(
            {
                **base,
                "kind": "coverage_scan_terminal",
                "effect_id": scan_effect_id,
                "outcome": outcome,
                "status": str(getattr(result, "status", "") or "") if result is not None else "",
                "ok": ok,
                "manifest_sha256_before": before_scan.manifest_sha256,
                "manifest_sha256_after": after_scan.manifest_sha256,
                "degraded": bool(before_scan.degraded or after_scan.degraded),
                "degraded_after_scan": bool(after_scan.degraded and not before_scan.degraded),
                "downgraded_to_irreversible": downgraded,
                "drift": drift_rows,
                "rollback_capable": bool(not downgraded and all(row["rollback_capable"] for row in drift_rows)) if drift_rows else (not downgraded and not after_scan.degraded),
                "coverage": _coverage_block(capability, declared=declared, high_risk={row["path"]: row["high_risk"] for row in drift_rows if row["high_risk"]}),
                "started_at": started_at,
                "completed_at": completed_at,
                **({"exception": exception} if exception else {}),
            }
        )
        effect_ids.append(str(entry.get("effect_id")))
        return {
            "effect_ids": effect_ids,
            "coverage_summary": {
                "strategy": strategy,
                "drift_count": len(drift_rows),
                "drift_paths": [row["path"] for row in drift_rows],
                "rollback_capable": bool(entry.get("rollback_capable")),
                "degraded": bool(entry.get("degraded")),
                "downgraded_to_irreversible": downgraded,
                "high_risk": {row["path"]: row["high_risk"] for row in drift_rows if row["high_risk"]},
            },
            "entry": entry,
        }

    if strategy == STRATEGY_DECLARED_PATHS:
        written: list[dict[str, Any]] = []
        for item in planned or []:
            target = Path(item["path"]) if item["path"] else None
            after = store.snapshot(target) if target is not None else MISSING
            before = PathObservation.from_dict(item.get("before"))
            outcome = outcome_override or (
                (OUTCOME_NO_CHANGE if after.same_content_as(before) else OUTCOME_SUCCEEDED)
                if ok
                else (OUTCOME_REFUSED if after.same_content_as(before) else OUTCOME_FAILED)
            )
            entry = store.append(
                {
                    **base,
                    "kind": "effect_terminal",
                    "effect_id": item.get("effect_id") or f"effect-{uuid.uuid4().hex}",
                    "path": item["path"],
                    "operation": "coverage_declared",
                    "outcome": outcome,
                    "status": str(getattr(result, "status", "") or "") if result is not None else "",
                    "ok": ok,
                    "downgraded_to_irreversible": downgraded,
                    "after": _opaque_row(after.to_dict(), keyring),
                    "started_at": started_at,
                    "completed_at": completed_at,
                    **({"exception": exception} if exception else {}),
                }
            )
            effect_ids.append(str(entry.get("effect_id")))
            written.append(entry)
        return {
            "effect_ids": effect_ids,
            "coverage_summary": {
                "strategy": strategy,
                "declared_count": len(planned or []),
                "downgraded_to_irreversible": downgraded,
                "rollback_capable": not downgraded,
            },
            "entry": written[-1] if written else {},
        }

    # postimage_only: nothing was claimed before; journal what the disk holds now for the paths
    # the handler itself reported. A local irreversible effect enters the Blackbox honestly.
    paths = _result_paths(result) if result is not None else []
    rows: list[dict[str, Any]] = []
    for raw in paths:
        target = Path(raw)
        observation = store.snapshot(target)
        rows.append(
            _scrub(
                {
                    "path": str(target),
                    "after": _opaque_row(observation.to_dict(), keyring),
                    "high_risk": highrisk.classify_path(target) or "",
                }
            )
        )
    entry = store.append(
        {
            **base,
            "kind": "coverage_post_terminal",
            "effect_id": scan_effect_id,
            "outcome": outcome_override or (OUTCOME_SUCCEEDED if ok else OUTCOME_FAILED),
            "status": str(getattr(result, "status", "") or "") if result is not None else "",
            "ok": ok,
            "postimages": rows,
            "rollback_capable": False,
            "coverage": _coverage_block(capability, declared=declared, high_risk={row["path"]: row["high_risk"] for row in rows if row["high_risk"]}),
            "started_at": started_at,
            "completed_at": completed_at,
            **({"exception": exception} if exception else {}),
        }
    )
    effect_ids.append(str(entry.get("effect_id")))
    return {
        "effect_ids": effect_ids,
        "coverage_summary": {"strategy": strategy, "observed_count": len(rows), "rollback_capable": False},
        "entry": entry,
    }


__all__ = [
    "DOWNGRADE_KEY",
    "HIGH_RISK_ALLOW_KEY",
    "STATUS_CAPTURE_INCOMPLETE",
    "STATUS_COVERAGE_REQUIRED",
    "STATUS_DOWNGRADE_REQUIRED",
    "STATUS_HIGH_RISK",
    "STATUS_KEY_UNAVAILABLE",
    "STATUS_NO_ROOT",
    "STATUS_UNAVAILABLE",
    "STATUS_UNRECORDED",
    "declared_paths_for",
    "recorded_capability_mutation",
    "workspace_root_from_context",
]
