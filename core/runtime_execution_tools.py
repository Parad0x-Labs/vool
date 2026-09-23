from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # annotation-only: the refusal type is imported lazily at the doors
    from core.effect_budget import EffectBudgetRefusedError

import json
import os
import platform
import re
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from core import policy_engine
from core.effect_reconciliation import (
    EffectOutcomeUnknown,
    peek_in_flight_effect,
)
from core.execution.artifacts import (
    atomic_write_text,
    build_command_artifact,
    build_failure_artifact,
    build_file_diff_artifact,
    content_sha256,
    latest_workspace_mutation,
    mark_workspace_mutation_promoted,
    record_workspace_mutation,
    rollback_last_workspace_mutation,
)
from core.execution.git_tools import git_diff_workspace, git_status_workspace, git_summary_workspace
from core.execution.models import _tool_observation
from core.execution.validation_tools import (
    failure_message_line,
    is_failure_pointer_line,
    render_validation_result,
    runtime_validation_command,
    validation_command,
)
from core.execution.workspace_tools import (
    apply_unified_diff_workspace,
    force_fresh_source_timestamp,
    iter_workspace_files_with_status,
    list_tree_workspace,
    symbol_search_workspace,
    workspace_scan_limit,
)
from core.execution.workspace_tools import (
    relative_path as workspace_relative_path,
)
from core.execution.workspace_tools import (
    resolve_workspace_path as resolve_workspace_path_impl,
)
from core.execution_gate import ExecutionGate
from core.hardware_tier import probe_machine
from core.install_recommendations import install_recommendation_machine_summary
from core.knowledge_marketplace import purchase_knowledge, search_listings
from core.learning import promote_verified_procedure
from core.runtime_paths import PROJECT_ROOT, resolve_workspace_root
from core.runtime_tool_contracts import runtime_tool_contract_map, runtime_tool_contracts
from core.tool_argument_aliases import bind_known_argument_aliases
from core.tool_memo import memoize, register_pure_tool
from core.tool_memo import policy_epoch as memo_policy_epoch

# NIA-014 purity registrations — the ONLY memo opt-ins, greppable by law. Static
# machine facts only; everything else executes fresh forever until an operator
# adds a named registration here. TTL bounds staleness if the machine changes;
# the version rides in every memo key, so bumping it invalidates prior entries.
register_pure_tool("workspace.identity", ttl_seconds=300, version="v1")
register_pure_tool("machine.inspect_specs", ttl_seconds=60, version="v1")
from core.web0_tools import (
    web0_add_block,
    web0_add_gated_section,
    web0_compile_preview,
    web0_create_project,
    web0_encrypt_whole_site,
    web0_fill_slots,
    web0_open_builder_draft,
    web0_publish,
)
from network.signer import get_local_peer_id
from sandbox.network_guard import parse_command
from sandbox.sandbox_runner import SandboxRunner

_EXECUTION_REQUEST_MARKERS = (
    "run ",
    "execute ",
    "command",
    "shell",
    "terminal",
    "read file",
    "open file",
    "search code",
    "find in files",
    "edit file",
    "change file",
    "patch file",
    "write file",
    "write files",
    "replace in file",
    # Natural file-mutation phrasing real users type from the chat/API surface.
    # Without these, "create a file test.txt with hello" never trips the
    # execution gate, so the tool loop is skipped and workspace.write_file is
    # never reached even though the executor is fully wired.
    "create a file",
    "create the file",
    "create file",
    "write a file",
    "write to file",
    "write to a file",
    "make a file",
    "new file",
    "save a file",
    "save to file",
    "save to a file",
    "save it to",
    "save this to",
    "append to file",
    "append to a file",
    "add to file",
    "overwrite file",
    "overwrite a file",
    "folder",
    "directory",
    "mkdir",
    "start coding",
    "initial files",
    "bootstrap",
    "pytest",
    "rg ",
    "grep ",
    "proceed",
    "do it",
    "go ahead",
    "carry on",
    "start working",
    "just do it",
    "deliver it",
    "submit it",
)

_WORKSPACE_EXECUTION_TARGET_RE = re.compile(
    r"\b(?:repo|repository|workspace|(?:this|the|my|our|current)\s+project)\b",
    re.IGNORECASE,
)
_WORKSPACE_EXECUTION_VERB_RE = re.compile(
    r"\b(?:audit|browse|build|change|check|create|delete|edit|find|fix|inspect|list|"
    r"locate|modify|open|patch|read|repair|review|run|save|scan|search|show|store|"
    r"summari[sz]e|test|trace|work\s+on|write)\b",
    re.IGNORECASE,
)
_WORKSPACE_CONTENT_QUESTION_RE = re.compile(
    r"\bwhat(?:'s|\s+is|\s+are)\s+(?:in|inside|under)\s+"
    r"(?:this|the|my|our|current)?\s*(?:repo|repository|workspace|project)\b",
    re.IGNORECASE,
)
_FILE_LINE_RE = re.compile(r"(?P<path>[A-Za-z0-9_./-]+\.[A-Za-z0-9_+-]+):(?P<line>\d+)")
_SAFE_MACHINE_DIRECTORY_NAMES = ("Desktop", "Downloads", "Documents")


@dataclass
class RuntimeExecutionResult:
    handled: bool
    ok: bool
    status: str
    response_text: str = ""
    details: dict[str, Any] = field(default_factory=dict)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dispatch_with_mutation_activity(
    intent: str,
    *,
    workspace_root: Path,
    source_context: dict[str, Any] | None,
    handler: Any,
    arguments: dict[str, Any] | None = None,
) -> RuntimeExecutionResult:
    """Run a mutating handler and emit exactly one Activity event for it -- including when the
    handler RAISES rather than returns a clean failure. Without this, an exception (e.g. a disk
    write failing mid-atomic-swap) unwinds straight past a per-branch emit call to the dispatcher's
    outer `except Exception` a few lines below, and the failure never reaches Activity at all.
    The exception is re-raised unchanged afterward so the outer handler's existing error-response
    shape is untouched -- this only adds an event, it does not change what gets returned.

    THE Blackbox seam: every workspace mutation the runtime dispatches crosses this function, so
    the flight recorder wraps the handler HERE -- INTENDED (with the before bytes in the verified
    CAS) is durable before the handler moves a byte, TERMINAL (with the after bytes) is durable
    before the result is returned. See ``core.blackbox.recorder`` for the exact ordering and for
    what a caller sees when the journal itself is unavailable.

    P1 AMENDMENT — the file-write budget gate: every workspace WRITE and
    DELETE crosses this function, so the reservation crosses it here too —
    RESERVED at authorization (the ledger's open_effect, before the handler
    can move a byte), CONSUMED immediately before the handler runs, terminal
    reconciled from the handler's real outcome. A budget refusal returns a
    typed refusal result and the handler NEVER runs: zero filesystem writes.
    Unbudgeted (no operator rule): an honest pass, no rows, no events.
    """
    from core.blackbox.recorder import recorded_workspace_mutation
    from core.effect_budget import EffectBudgetRefusedError

    try:
        effect = _open_file_write_budget_effect(intent, workspace_root=workspace_root)
    except EffectBudgetRefusedError as budget_refusal:
        return _budget_refusal_result(intent, budget_refusal)
    started_at = _now_iso()
    try:
        if effect is not None:
            # consume immediately before execution — the unit is spent the
            # moment the handler is about to move bytes
            effect.begin_attempt()
        result = recorded_workspace_mutation(
            intent,
            arguments,
            workspace_root=workspace_root,
            source_context=source_context,
            handler=handler,
        )
    except EffectBudgetRefusedError as budget_refusal:
        # a released reservation (rollback before execution): the mutation
        # never runs and the refusal is the typed result
        return _budget_refusal_result(intent, budget_refusal)
    except Exception as exc:
        if effect is not None:
            effect.fail(exc=exc)
        _emit_mutation_activity_event(
            source_context=source_context,
            intent=intent,
            workspace_root=workspace_root,
            result=RuntimeExecutionResult(handled=True, ok=False, status="error", response_text=str(exc), details={}),
            started_at=started_at,
            completed_at=_now_iso(),
        )
        raise
    if effect is not None:
        if getattr(result, "ok", False):
            effect.succeed(reason="workspace mutation applied")
        else:
            effect.fail(reason="workspace mutation returned a failure result")
    _emit_mutation_activity_event(
        source_context=source_context,
        intent=intent,
        workspace_root=workspace_root,
        result=result,
        started_at=started_at,
        completed_at=_now_iso(),
    )
    return result


def with_mutation_coverage(
    intent: str,
    arguments: dict[str, Any],
    *,
    source_context: dict[str, Any] | None,
    workspace_root: Path,
    handler: Any,
) -> RuntimeExecutionResult:
    """THE coverage seam for every runtime-dispatched tool capable of local mutation outside the
    v1 workspace-file intents: shell commands, formatters/tests/lint, machine writes and moves,
    skill creation, media state.

    The tool's OWN declared capability decides what happens: no declaration is a typed refusal
    (fail closed -- an undeclared mutation does not run); a coverage recorder wraps the handler
    between durable Blackbox observations; the v1 flight recorder and the external receipt lanes
    pass through unchanged because their intents already journal through their own authorities.
    """
    from core.blackbox.coverage.registry import (
        RECORDER_COVERAGE_DECLARED,
        RECORDER_COVERAGE_POST,
        RECORDER_COVERAGE_SCAN,
        mutation_coverage_decision,
    )

    decision = mutation_coverage_decision(intent)
    if not decision.covered:
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="blackbox_coverage_required",
            response_text=(
                f"`{intent}` was not executed: it is capable of local mutation and carries no "
                "Blackbox coverage declaration (scope, reversibility, snapshot strategy, receipt "
                "lifecycle, rollback support, recorder). An undeclared mutation does not run."
            ),
            details={
                "executed": False,
                "coverage_reason": decision.reason,
                "observation": _tool_observation(
                    intent=intent,
                    tool_surface="blackbox_coverage",
                    ok=False,
                    status="blackbox_coverage_required",
                ),
            },
        )
    if decision.recorder not in {RECORDER_COVERAGE_SCAN, RECORDER_COVERAGE_DECLARED, RECORDER_COVERAGE_POST}:
        return handler()  # v1 flight recorder / external receipts own this intent's journaling
    from core.blackbox.coverage.recorder import recorded_capability_mutation

    # CP2 — THE COMMAND/MACHINE BUDGET DOOR: a coverage-recorded local mutation owes its budget
    # unit BEFORE the recorder can run the handler. The class comes from the contract's OWN
    # side_effect_class (never an intent-name regex): shell-shaped classes reserve `command`,
    # workspace-writing classes reserve `file_write`; any other class is unbudgeted by name and
    # passes (the gateway's law). A refusal returns the typed refusal result with the handler,
    # the recorder, and every subprocess/transport/write the mutation would have touched UNRUN.
    from core.effect_budget import EffectBudgetRefusedError

    try:
        budget_effect = _open_mutation_budget_effect(intent, workspace_root=workspace_root)
    except EffectBudgetRefusedError as budget_refusal:
        return _budget_refusal_result(intent, budget_refusal, door="mutation")
    try:
        result = recorded_capability_mutation(
            intent,
            arguments,
            handler=handler,
            source_context=source_context,
            workspace_root=workspace_root,
        )
    except EffectBudgetRefusedError as budget_refusal:
        return _budget_refusal_result(intent, budget_refusal, door="mutation")
    except BaseException:
        if budget_effect is not None:
            # the handler RAISED mid-flight: the attempt happened, its unit is spent
            # (never refunded), and the effect settles failed from the raise
            budget_effect.begin_attempt()
            budget_effect.fail(reason="mutation raised before an outcome")
        raise
    if budget_effect is None:
        return result
    details = dict(getattr(result, "details", None) or {})
    recorder_refused_before_execution = (
        not bool(details.get("executed", True))
        and str(getattr(result, "status", "") or "").startswith("blackbox_")
    )
    if recorder_refused_before_execution:
        # Reserved-then-never-run: the recorder refused BEFORE the handler ran (high-risk target,
        # incomplete preimage capture, unavailable key). The unit returns to the pool: the ledger
        # leg records the cancelled terminal and releases its still-reserved unit; the direct leg's
        # cancel releases the reservation itself.
        from core.effect_budget import release_effect_reservations

        budget_effect.cancel(reason=f"coverage refused {intent} before execution")
        release_effect_reservations(getattr(budget_effect, "effect_id", "") or "")
        return result
    # The handler ran under observation: the unit is spent, and the terminal settles from the
    # mutation's real outcome — one reservation per dispatch, no double charge (a retry that
    # names this effect continues the same reservation at the ledger).
    budget_effect.begin_attempt()
    if getattr(result, "ok", False):
        budget_effect.succeed(reason="coverage-recorded mutation settled")
    else:
        budget_effect.fail(reason="coverage-recorded mutation returned a failure result")
    return result




def _open_file_write_budget_effect(intent: str, *, workspace_root: Path):
    """Reserve one file_write unit at the one real gate for this door.

    Returns the lifecycle-shaped handle (None when unbudgeted). A budget
    refusal raises `EffectBudgetRefusedError` — the caller turns it into the
    typed refusal result and the handler never runs.
    """
    from core import effect_budget

    if not effect_budget.active_budgets("file_write"):
        return None
    from core.effect_gateway import (
        DECISION_ALLOWED,
        LIFECYCLE_AUTHORIZED,
        EffectReceipt,
        current_effect_ledger,
    )

    ledger = current_effect_ledger()
    if ledger is None:
        # this door's dispatcher guarantees a ledger (it opens the named
        # scope at the owning entry point); a direct caller without one
        # still reserves — the budget must not be bypassable by arriving
        # from an unforeseen path
        receipt = effect_budget.reserve_effect_units(
            "file_write",
            owner_ref="runtime_execution_tools.mutation_door",
            project_key=str(workspace_root or ""),
        )
        return _DirectBudgetHandle(receipt) if not receipt.unbudgeted else None
    return ledger.open_effect(
        EffectReceipt(
            effect_class="file_write",
            decision=DECISION_ALLOWED,
            lifecycle=LIFECYCLE_AUTHORIZED,
            reason=f"workspace mutation {intent}",
            decided_by="runtime_execution_tools.mutation_door",
        )
    )


#: The budget class a declared effect owes, derived from its CONTRACT's own side_effect_class —
#: the same vocabulary `_GATEWAY_CLASS_MAP` speaks, so this door and the gateway can never
#: disagree about what a class means. ONE table for builtin and REGISTERED (plugin/MCP)
#: contracts alike: there is no plugin-specific budget policy. Classes absent here have no
#: budget-catalog equivalent yet (`credit_spend`, `wallet_spend` — the wallet lane routes those
#: at the gateway seam itself); they are unbudgeted by name and pass (the gateway's law), never
#: silently counted under a neighbor.
_MUTATION_BUDGET_CLASS_BY_SIDE_EFFECT = {
    "sandbox_command": "command",
    "validation_command": "command",
    "task_orchestration": "command",
    "workspace_write": "file_write",
    "builder_state": "file_write",
    "creative_state": "file_write",
    "network_send": "network_fetch",
    "network_publish": "public_write",
    "media_generation": "provider_call",
}


def _open_mutation_budget_effect(intent: str, *, workspace_root: Path):
    """Reserve the budget unit a coverage-recorded local mutation owes at THIS door.

    Same shape as the file-write door: None when the contract's class is unbudgeted by name
    (or no operator rule is active); a lifecycle handle whose unit is consumed when the
    handler actually runs and settles from the mutation's real outcome; a typed refusal when
    the budget gate says no — in which case neither the recorder nor the handler runs.
    """
    from core import effect_budget
    from core.tool_registry import tool_for_intent

    contract = tool_for_intent(intent)
    if contract is None:
        # no contract anywhere: the coverage decision above already answered
        # this intent; there is no budget vocabulary to speak for it
        return None
    budget_class = _MUTATION_BUDGET_CLASS_BY_SIDE_EFFECT.get(str(getattr(contract, "side_effect_class", "") or ""), "")
    if not budget_class or not effect_budget.active_budgets(budget_class):
        return None
    from core.effect_gateway import (
        DECISION_ALLOWED,
        LIFECYCLE_AUTHORIZED,
        EffectReceipt,
        current_effect_ledger,
    )

    ledger = current_effect_ledger()
    if ledger is None:
        receipt = effect_budget.reserve_effect_units(
            budget_class,
            owner_ref="runtime_execution_tools.coverage_door",
            project_key=str(workspace_root or ""),
        )
        return _DirectBudgetHandle(receipt) if not receipt.unbudgeted else None
    return ledger.open_effect(
        EffectReceipt(
            effect_class=budget_class,
            decision=DECISION_ALLOWED,
            lifecycle=LIFECYCLE_AUTHORIZED,
            reason=f"coverage-recorded mutation {intent}",
            decided_by="runtime_execution_tools.coverage_door",
        )
    )


def _budget_refusal_result(
    intent: str, refusal: EffectBudgetRefusedError, *, door: str = "workspace mutation"
) -> RuntimeExecutionResult:
    """The typed refusal this door returns when the budget gate refuses:
    the handler never ran, `ok=False`, and the typed code is on the record."""
    return RuntimeExecutionResult(
        handled=True,
        ok=False,
        status="blocked_by_effect_budget",
        response_text=(
            f"{door} {intent!r} refused by the effect budget: "
            f"{refusal.code}: {refusal.detail}"
            + (f" [rule {refusal.rule}]" if refusal.rule else "")
        ),
        details={
            "effect_budget": {
                "code": refusal.code,
                "detail": refusal.detail,
                "rule": refusal.rule,
            }
        },
    )


class _DirectBudgetHandle:
    """The no-ledger leg of the file-write door: a reservation made directly
    at the authority, exposing the same begin/terminal surface the lifecycle
    handle has (consume immediately before execution; terminal reconcile is
    the durable reservation state itself — an attempted mutation's unit is
    spent, never refunded)."""

    __slots__ = ("_reservation_id", "effect_id")

    def __init__(self, receipt) -> None:
        from core.effect_budget import bind_reservation_effect

        self._reservation_id = receipt.reservation_id
        self.effect_id = receipt.effect_id
        if receipt.effect_id:
            bind_reservation_effect(receipt.reservation_id, receipt.effect_id)

    def begin_attempt(self) -> int:
        from core.effect_budget import consume_reservation

        consume_reservation(self._reservation_id)
        return 1

    def succeed(self, *, status=None, reason: str = "") -> str:
        return "succeeded"

    def fail(self, *, exc=None, reason: str = "") -> str:
        return "failed"

    def cancel(self, *, reason: str = "") -> str:
        from core.effect_budget import release_reservation

        release_reservation(self._reservation_id, reason=reason or "reserved-then-never-run")
        return "cancelled"


_DAEMON_COMMIT_SHA_CACHE: dict[str, str] = {}


def _daemon_commit_sha() -> str:
    """The running daemon's own commit SHA, computed once per process.

    Correlates a mutation Activity event with `/api/runtime/version`'s `commit` field so an
    external observer can tell which build produced a given event without re-deriving it.
    """
    if "sha" not in _DAEMON_COMMIT_SHA_CACHE:
        sha = ""
        try:
            result = subprocess.run(
                ["git", "-C", str(PROJECT_ROOT), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                sha = str(result.stdout or "").strip()
        except Exception:
            sha = ""
        _DAEMON_COMMIT_SHA_CACHE["sha"] = sha
    return _DAEMON_COMMIT_SHA_CACHE["sha"]


# Event types this dispatcher can emit for a workspace mutation/rollback -- a closed, named set
# rather than free-text, even though `emit_runtime_event`'s underlying store accepts any string.
_MUTATION_EVENT_ROLLBACK_CONFLICT = "workspace_mutation_rollback_conflict"
_MUTATION_EVENT_ROLLBACK_COMPLETED = "workspace_mutation_rollback_completed"
_MUTATION_EVENT_COMPLETED = "workspace_mutation_completed"
_MUTATION_EVENT_FAILED = "workspace_mutation_failed"
_MUTATION_PERMISSION_DENIAL_STATUSES = frozenset({"disabled", "permission_denied"})
_MUTATION_REASON_STATUSES = frozenset({"stale_base", "stale_revert_conflict", "ambiguous_match", "invalid_patch_syntax", "target_missing"})


def _emit_mutation_activity_event(
    *,
    source_context: dict[str, Any] | None,
    intent: str,
    workspace_root: Path,
    result: RuntimeExecutionResult,
    started_at: str,
    completed_at: str,
) -> None:
    """Emit one canonical mutation event on the EXISTING runtime session-event/Activity path
    (`core.runtime_task_events.emit_runtime_event` -> `runtime_session_events`) rather than a new
    audit store. This is the same pipe the Activity panel and the SSE task rail already read, so a
    mutation now shows up there without a second ledger to keep in sync.

    Deliberately excluded: raw file content. Only hashes, a size-bounded diff preview (already
    capped by `diff_preview()`), and identifiers go out -- `emit_runtime_event` -> `append_runtime_event`
    already redacts secrets from `message`/`details` at the write boundary, so this rides that
    protection rather than duplicating it.

    Scope note: "permission decision" here reflects only what THIS layer can see -- whether the
    tool's own in-handler policy checks (`filesystem.allow_write_workspace`, the audit-policy gate)
    allowed or denied the call. The upstream mode-based approve/deny decision in
    `core.mode_permission_policy.decide_tool_call()` runs before `execute_runtime_tool` is ever
    reached and is not re-derived here -- a call already blocked there never produces this event.

    Telemetry must never break the tool call it describes: any failure here (a locked event-store
    file, a malformed source_context) is swallowed rather than propagated, so an Activity-plumbing
    problem can never turn a successful write into a reported failure.
    """
    try:
        _emit_mutation_activity_event_impl(
            source_context=source_context,
            intent=intent,
            workspace_root=workspace_root,
            result=result,
            started_at=started_at,
            completed_at=completed_at,
        )
    except Exception:
        return


def _emit_mutation_activity_event_impl(
    *,
    source_context: dict[str, Any] | None,
    intent: str,
    workspace_root: Path,
    result: RuntimeExecutionResult,
    started_at: str,
    completed_at: str,
) -> None:
    from core.runtime_task_events import emit_runtime_event

    details = result.details if isinstance(result.details, dict) else {}
    status = str(result.status or "")
    canonical_target = str(details.get("path") or "").strip()
    if not canonical_target:
        candidate_paths = details.get("changed_paths") or details.get("paths") or details.get("restored_paths") or []
        canonical_target = ", ".join(str(item).strip() for item in candidate_paths if str(item).strip())

    mutation_record = details.get("mutation_record") if isinstance(details.get("mutation_record"), dict) else {}
    rollback_id = str(mutation_record.get("mutation_id") or details.get("mutation_id") or "").strip()

    artifacts = details.get("artifacts") if isinstance(details.get("artifacts"), list) else []
    first_artifact = artifacts[0] if artifacts and isinstance(artifacts[0], dict) else {}
    # PRESENCE, not truthiness: `details["before_hash"] == ""` (a create -- correctly, there is no
    # prior file) and `details["before_hash"]` being ABSENT (this handler never set the key at all,
    # e.g. apply_unified_diff, which only carries hashes inside its artifact) are different facts.
    # An `or`-chain on truthiness cannot tell them apart -- an empty-but-present "" would fall
    # through to the artifact's OWN independently-computed `content_sha256("")`, a real 64-char
    # hash, silently turning a correct "no prior file" into what reads as a genuine (but wrong)
    # hash. Only fall back to the artifact when the handler's own details never set this key.
    before_hash = str(details["before_hash"]).strip() if "before_hash" in details else str(first_artifact.get("before_hash") or "").strip()
    after_hash = str(details["after_hash"]).strip() if "after_hash" in details else str(first_artifact.get("after_hash") or "").strip()
    # Every write_file/replace_in_file/apply_unified_diff handler already reports "created" /
    # "updated" / "replaced" / "deleted" in its own details -- carried through here so a reader
    # doesn't have to infer "was this a create?" from before_hash happening to be empty, which is
    # also (ambiguously) what an unrelated bug that dropped a real before_hash would look like.
    action = str(details.get("action") or "").strip()
    diff_summary = str(first_artifact.get("diff_preview") or "").strip()
    if not diff_summary:
        # An empty diff_summary is sometimes correct (rollback produces no file_diff artifact at
        # all; a write whose new content is byte-identical to the old has nothing to show) -- but
        # silent emptiness reads the same as "something went wrong building it". State which case
        # this is instead of leaving the field blank.
        diff_summary = "(no diff: content unchanged)" if artifacts else "(no diff: this operation does not produce a file diff)"

    permission_decision = "denied" if status in _MUTATION_PERMISSION_DENIAL_STATUSES else "allowed"
    conflict_reason = status if status in _MUTATION_REASON_STATUSES else ""

    if status == "stale_revert_conflict":
        event_type = _MUTATION_EVENT_ROLLBACK_CONFLICT
    elif intent == "workspace.rollback_last_change" and result.ok:
        event_type = _MUTATION_EVENT_ROLLBACK_COMPLETED
    elif result.ok:
        event_type = _MUTATION_EVENT_COMPLETED
    else:
        event_type = _MUTATION_EVENT_FAILED

    emit_runtime_event(
        source_context,
        event_type=event_type,
        message=str(result.response_text or "")[:400],
        details={
            "task_id": str((source_context or {}).get("task_id") or "").strip(),
            "tool_intent": intent,
            "canonical_target": canonical_target,
            "workspace_identity": str(workspace_root),
            "permission_decision": permission_decision,
            "operation_started_at": started_at,
            "operation_completed_at": completed_at,
            "result_state": status,
            "ok": bool(result.ok),
            "action": action,
            "before_hash": before_hash,
            "after_hash": after_hash,
            "diff_summary": diff_summary,
            "rollback_id": rollback_id,
            "error_class": "" if result.ok else status,
            "conflict_reason": conflict_reason,
            "daemon_sha": _daemon_commit_sha(),
        },
    )


def _extract_failure_summary(*, command: str, stdout: str, stderr: str, returncode: int) -> str:
    if int(returncode or 0) == 0:
        return ""
    return failure_message_line(stdout, stderr)[:260] or f"`{command}` exited with code {int(returncode or 0)}"


def _extract_command_failure_followup(*, stdout: str, stderr: str) -> dict[str, Any]:
    combined = "\n".join(part for part in (str(stderr or "").strip(), str(stdout or "").strip()) if part).strip()
    file_match = _FILE_LINE_RE.search(combined)
    path = str(file_match.group("path") or "").strip() if file_match else ""
    line_number = int(file_match.group("line") or 0) if file_match else 0
    diagnostic_query = ""
    lines = [item.strip() for item in combined.splitlines() if item.strip()]
    # A line that only points at the failure (a stack frame, `node:assert:150`, the echoed `throw ...`) names no
    # expression to look up.
    lines = [item for item in lines if not is_failure_pointer_line(item)] or lines
    for line in lines:
        lowered = line.lower()
        if "assert" not in lowered:
            continue
        normalized = line.lstrip("> ").strip()
        expression = normalized.partition("assert")[2].strip() or normalized
        call_match = re.search(r"([A-Za-z_][A-Za-z0-9_\.]*)\s*\(", expression)
        if call_match:
            diagnostic_query = f"{call_match.group(1)}("
            break
        left_side = re.split(r"\s*(==|!=|<=|>=| is | in )\s*", expression, maxsplit=1)[0].strip()
        if left_side:
            diagnostic_query = left_side[:160]
            break
    if not diagnostic_query:
        for line in lines:
            lowered = line.lower()
            if any(token in lowered for token in ("error", "failed", "exception", "traceback")):
                diagnostic_query = line[:160]
                break
    return {
        "error_path": path,
        "error_line": line_number,
        "diagnostic_query": diagnostic_query,
    }


def runtime_execution_capability_ledger() -> list[dict[str, Any]]:
    grouped: dict[str, list[Any]] = {}
    for contract in runtime_tool_contracts():
        grouped.setdefault(contract.capability_id, []).append(contract)
    ledger: list[dict[str, Any]] = []
    for capability_id, contracts in grouped.items():
        supported_intents = [contract.intent for contract in contracts if contract.supported]
        all_supported = len(supported_intents) == len(contracts)
        any_supported = bool(supported_intents)
        support_level = "supported" if all_supported else ("partial" if any_supported else "unsupported")
        reasons = [contract.unsupported_reason for contract in contracts if not contract.supported and contract.unsupported_reason]
        ledger.append(
            {
                "capability_id": capability_id,
                "surface": contracts[0].tool_surface,
                "claim": contracts[0].capability_claim,
                "supported": any_supported,
                "support_level": support_level,
                "availability_state": "ready" if all_supported else ("partial" if any_supported else "unavailable"),
                "unsupported_reason": " ".join(dict.fromkeys(reasons)),
                "intents": [contract.intent for contract in contracts],
                "dispatchable_intents": supported_intents,
                "public_tag": capability_id,
            }
        )
    return ledger


def runtime_execution_tool_specs(*, web_available_fn: Any = None) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    for contract in runtime_tool_contracts(web_available_fn=web_available_fn):
        if not contract.supported:
            continue
        specs.append(
            {
                "intent": contract.intent,
                "description": contract.description,
                "read_only": contract.read_only,
                "arguments": dict(contract.input_schema),
                "json_schema": dict(contract.json_schema),
                "output_schema": dict(contract.output_schema),
                "side_effect_class": contract.side_effect_class,
                "approval_requirement": contract.approval_requirement,
                "timeout_policy": contract.timeout_policy,
                "retry_policy": contract.retry_policy,
                "artifact_emission": contract.artifact_emission,
                "error_contract": contract.error_contract,
            }
        )
    return specs


def extract_observation_followup_hints(observation: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(observation or {})
    intent = str(payload.get("intent") or "").strip()
    if not intent:
        return {}
    if intent == "machine.list_directory":
        entries = [dict(item) for item in list(payload.get("entries") or []) if isinstance(item, dict)]
        return {
            "intent": intent,
            "path": str(payload.get("path") or "").strip(),
            "directories_only": bool(payload.get("directories_only", False)),
            "entry_count": int(payload.get("count") or len(entries)),
            "entries": entries,
            "paths": [str(item.get("path") or "").strip() for item in entries if str(item.get("path") or "").strip()],
        }
    if intent == "machine.inspect_specs":
        return {
            "intent": intent,
            "chip_name": str(payload.get("chip_name") or "").strip(),
            "os_name": str(payload.get("os_name") or "").strip(),
            "os_version": str(payload.get("os_version") or "").strip(),
            "cpu_cores": int(payload.get("cpu_cores") or 0),
            "ram_gb": float(payload.get("ram_gb") or 0.0),
            "gpu_name": str(payload.get("gpu_name") or "").strip(),
            "vram_gb": float(payload.get("vram_gb") or 0.0) if payload.get("vram_gb") is not None else None,
            "accelerator": str(payload.get("accelerator") or "").strip(),
            "recommended_model": str(payload.get("recommended_model") or "").strip(),
            "selected_tier": str(payload.get("selected_tier") or "").strip(),
            "capacity_bucket": str(payload.get("capacity_bucket") or "").strip(),
            "recommended_bundle_models": [
                str(item).strip()
                for item in list(payload.get("recommended_bundle_models") or [])
                if str(item).strip()
            ],
            "display_name": str(payload.get("display_name") or "").strip(),
            "display_native_resolution": str(payload.get("display_native_resolution") or "").strip(),
            "display_current_resolution": str(payload.get("display_current_resolution") or "").strip(),
            "screen_size": str(payload.get("screen_size") or "").strip(),
        }
    if intent == "machine.read_file":
        lines = [dict(item) for item in list(payload.get("lines") or []) if isinstance(item, dict)]
        return {
            "intent": intent,
            "path": str(payload.get("path") or "").strip(),
            "start_line": int(payload.get("start_line") or 1),
            "line_count": int(payload.get("line_count") or 0),
            "lines": lines,
            "content": "\n".join(str(item.get("text") or "") for item in lines),
            "verbatim": bool(payload.get("verbatim", False)),
        }
    if intent == "machine.ensure_directory":
        return {
            "intent": intent,
            "path": str(payload.get("path") or "").strip(),
            "action": str(payload.get("action") or "").strip(),
            "already_present": bool(payload.get("already_present", False)),
        }
    if intent == "machine.write_file":
        return {
            "intent": intent,
            "path": str(payload.get("path") or "").strip(),
            "line_count": int(payload.get("line_count") or 0),
            "action": str(payload.get("action") or "").strip(),
        }
    if intent == "web0.open_builder_draft":
        return {
            "intent": intent,
            "builder_url": str(payload.get("builder_url") or "").strip(),
            "template": str(payload.get("template") or "").strip(),
            "domain": str(payload.get("domain") or "").strip(),
            "title": str(payload.get("title") or "").strip(),
        }
    if intent == "workspace.search_text":
        matches = [dict(item) for item in list(payload.get("matches") or []) if isinstance(item, dict)]
        primary = dict((matches[:1] or [{}])[0] or {})
        return {
            "intent": intent,
            "match_count": int(payload.get("match_count") or len(matches)),
            "paths": [str(item.get("path") or "").strip() for item in matches if str(item.get("path") or "").strip()],
            "primary_path": str(primary.get("path") or "").strip(),
            "primary_line": int(primary.get("line") or 0) if str(primary.get("line") or "").strip() else 0,
            "primary_snippet": str(primary.get("snippet") or "").strip(),
        }
    if intent == "workspace.symbol_search":
        matches = [dict(item) for item in list(payload.get("matches") or []) if isinstance(item, dict)]
        primary = dict((matches[:1] or [{}])[0] or {})
        return {
            "intent": intent,
            "symbol": str(payload.get("symbol") or "").strip(),
            "match_count": int(payload.get("match_count") or len(matches)),
            "paths": [str(item.get("path") or "").strip() for item in matches if str(item.get("path") or "").strip()],
            "primary_path": str(primary.get("path") or "").strip(),
            "primary_line": int(primary.get("line") or 0) if str(primary.get("line") or "").strip() else 0,
            "primary_kind": str(primary.get("kind") or "").strip(),
        }
    if intent == "workspace.read_file":
        lines = [dict(item) for item in list(payload.get("lines") or []) if isinstance(item, dict)]
        return {
            "intent": intent,
            "path": str(payload.get("path") or "").strip(),
            "start_line": int(payload.get("start_line") or 1),
            "line_count": int(payload.get("line_count") or 0),
            "lines": lines,
            "content": "\n".join(str(item.get("text") or "") for item in lines),
            "verbatim": bool(payload.get("verbatim", False)),
        }
    if intent == "workspace.ensure_directory":
        return {
            "intent": intent,
            "path": str(payload.get("path") or "").strip(),
            "action": str(payload.get("action") or "").strip(),
            "already_present": bool(payload.get("already_present", False)),
        }
    if intent == "workspace.write_file":
        return {
            "intent": intent,
            "path": str(payload.get("path") or "").strip(),
            "line_count": int(payload.get("line_count") or 0),
            "action": str(payload.get("action") or "").strip(),
        }
    if intent == "workspace.apply_unified_diff":
        return {
            "intent": intent,
            "paths": [str(item).strip() for item in list(payload.get("paths") or []) if str(item).strip()],
            "engine": str(payload.get("engine") or "").strip(),
        }
    if intent == "workspace.replace_in_file":
        return {
            "intent": intent,
            "path": str(payload.get("path") or "").strip(),
            "replacements": int(payload.get("replacements") or 0),
        }
    if intent == "workspace.rollback_last_change":
        return {
            "intent": intent,
            "restored_paths": [str(item).strip() for item in list(payload.get("restored_paths") or []) if str(item).strip()],
            "removed_paths": [str(item).strip() for item in list(payload.get("removed_paths") or []) if str(item).strip()],
        }
    if intent in {"workspace.run_tests", "workspace.run_lint", "workspace.run_formatter"}:
        followup = _extract_command_failure_followup(
            stdout=str(payload.get("stdout") or ""),
            stderr=str(payload.get("stderr") or ""),
        )
        return {
            "intent": intent,
            "command": str(payload.get("command") or "").strip(),
            "cwd": str(payload.get("cwd") or "").strip(),
            "returncode": int(payload.get("returncode") or 0),
            "success": bool(payload.get("success", False)),
            "error_path": str(payload.get("error_path") or followup.get("error_path") or "").strip(),
            "error_line": int(payload.get("error_line") or followup.get("error_line") or 0),
            "diagnostic_query": str(payload.get("diagnostic_query") or followup.get("diagnostic_query") or "").strip(),
        }
    if intent in {"workspace.git_status", "workspace.git_diff"}:
        return {
            "intent": intent,
            "command": str(payload.get("command") or "").strip(),
            "cwd": str(payload.get("cwd") or "").strip(),
            "returncode": int(payload.get("returncode") or 0),
            "success": bool(payload.get("success", False)),
        }
    if intent == "workspace.git_summary":
        return {
            "intent": intent,
            "cwd": str(payload.get("cwd") or "").strip(),
            "branch": str(payload.get("branch") or "").strip(),
            "commit": str(payload.get("commit") or "").strip(),
            "dirty": bool(payload.get("dirty", False)),
            "local_branch_count": int(payload.get("local_branch_count") or 0),
            "remote_branch_count": int(payload.get("remote_branch_count") or 0),
            "total_branch_count": int(payload.get("total_branch_count") or 0),
            "today_date": str(payload.get("today_date") or "").strip(),
            "today_commit_count": int(payload.get("today_commit_count") or 0),
            "yesterday_date": str(payload.get("yesterday_date") or "").strip(),
            "yesterday_commit_count": int(payload.get("yesterday_commit_count") or 0),
            "recent_commits": [dict(item) for item in list(payload.get("recent_commits") or []) if isinstance(item, dict)],
            "commit_count_scope": str(payload.get("commit_count_scope") or "").strip(),
            "timezone_label": str(payload.get("timezone_label") or "").strip(),
        }
    if intent == "orchestration.execute_envelope":
        return {
            "intent": intent,
            "task_id": str(payload.get("task_id") or "").strip(),
            "task_role": str(payload.get("task_role") or "").strip(),
            "receipt_count": int(payload.get("receipt_count") or 0),
            "child_count": int(payload.get("child_count") or 0),
            "merged_strategy": str(payload.get("merged_strategy") or "").strip(),
        }
    if intent == "sandbox.run_command":
        followup = _extract_command_failure_followup(
            stdout=str(payload.get("stdout") or ""),
            stderr=str(payload.get("stderr") or ""),
        )
        return {
            "intent": intent,
            "command": str(payload.get("command") or "").strip(),
            "cwd": str(payload.get("cwd") or "").strip(),
            "returncode": int(payload.get("returncode") or 0),
            "success": bool(payload.get("success", False)),
            "error_path": str(followup.get("error_path") or "").strip(),
            "error_line": int(followup.get("error_line") or 0),
            "diagnostic_query": str(followup.get("diagnostic_query") or "").strip(),
        }
    if intent == "web.search":
        results = [dict(item) for item in list(payload.get("results") or []) if isinstance(item, dict)]
        primary = dict((results[:1] or [{}])[0] or {})
        return {
            "intent": intent,
            "result_count": int(payload.get("result_count") or len(results)),
            "primary_url": str(primary.get("url") or "").strip(),
            "primary_domain": str(primary.get("origin_domain") or primary.get("domain") or "").strip(),
            "domains": [
                str(item.get("origin_domain") or item.get("domain") or "").strip()
                for item in results
                if str(item.get("origin_domain") or item.get("domain") or "").strip()
            ],
        }
    if intent in {"web.fetch", "browser.render", "web.research"}:
        return {
            "intent": intent,
            "url": str(payload.get("url") or payload.get("final_url") or "").strip(),
            "final_url": str(payload.get("final_url") or payload.get("url") or "").strip(),
            "query": str(payload.get("query") or "").strip(),
            "status": str(payload.get("status") or "").strip(),
            "hit_count": int(payload.get("hit_count") or 0),
            "evidence_strength": str(payload.get("evidence_strength") or "").strip(),
            "title": str(payload.get("title") or "").strip(),
        }
    return {"intent": intent}


def looks_like_execution_request(user_text: str, *, task_class: str) -> bool:
    lowered = str(user_text or "").strip().lower()
    if not lowered:
        return False
    # Finding C, 2026-08-04: "what is this project about" / "explain this codebase" never reached
    # a workspace tool at all -- classified as external web research and refused with "Live
    # research is not available on this runtime" -- because this whole function returns False
    # before the advice-only gate below is even relevant: `looks_like_advice_only_execution_prompt`
    # trips on the bare word "explain" and stops here, and `_WORKSPACE_CONTENT_QUESTION_RE` only
    # ever matched the literal "what is/are in/inside/under this repo/workspace/project" shape, not
    # "about"/"for"/"overview"/"explain". Checked BEFORE the advice-only gate deliberately: that
    # gate exists to stop a hedge-worded prompt from launching a MUTATING action, and a read-only
    # question about what the bound workspace even is was never the case it was written for.
    from core.agent_runtime.workspace_intent_detection import (
        contains_listing_intent,
        contains_overview_intent,
    )

    if contains_overview_intent(lowered):
        return True
    # Finding B, 2026-08-04, live-confirmed gap in the round-3 fix: "peek inside this dir for me"
    # passed `should_attempt_tool_intent` in isolation (Finding B's own planner.py fix) but a real
    # end-to-end run still answered with hallucinated file names and no tool call at all, because
    # `should_keep_ai_first_chat_lane` (which gates the AI-first chat lane BEFORE the tool-intent
    # gate is ever reached) consults THIS function, not the planner one, for its
    # `explicit_runtime_workflow_request` check -- and this function had the overview hook but not
    # the listing one. Same shape, same safety bar as the overview hook above: `contains_listing_intent`
    # already requires either an unambiguous strong word ("files"/"ls"/"contents"/"inventory") or a
    # weak word paired with an explicit demonstrative ("this folder"/"here"), so it does not turn
    # ordinary chat into an execution request.
    if contains_listing_intent(lowered):
        return True
    if looks_like_advice_only_execution_prompt(lowered):
        return False
    # Artifact creation uses the same imperative/opt-out contract as the builder.
    # Requiring a workspace keyword here lost explicit named-file instructions.
    from core.agent_runtime.build_request_intent import is_build_instruction
    from core.instructional_request import asks_for_instructions_not_execution

    if (
        is_build_instruction(user_text, scope="artifact")
        and not asks_for_instructions_not_execution(user_text)
    ):
        return True
    from core.tool_demand_signals import is_explicit_code_repair

    if is_explicit_code_repair(user_text):
        return True
    if task_class in {"debugging", "dependency_resolution", "config", "file_inspection", "shell_guidance"}:
        return True
    if _WORKSPACE_CONTENT_QUESTION_RE.search(lowered):
        return True
    if (
        _WORKSPACE_EXECUTION_TARGET_RE.search(lowered)
        and _WORKSPACE_EXECUTION_VERB_RE.search(lowered)
    ):
        return True
    return any(marker in lowered for marker in _EXECUTION_REQUEST_MARKERS)


def looks_like_advice_only_execution_prompt(user_text: str) -> bool:
    lowered = f" {' '.join(str(user_text or '').strip().lower().split())} "
    if not lowered.strip():
        return False
    hard_stop_markers = (
        " do not edit ",
        " don't edit ",
        " do not write ",
        " don't write ",
        " do not modify ",
        " don't modify ",
        " no file changes ",
        " no files ",
        " advice only ",
        " plan only ",
        " just plan ",
        " explain only ",
    )
    if any(marker in lowered for marker in hard_stop_markers):
        return True
    advisory_markers = (
        " plan ",
        " explain ",
        " review ",
        " advise ",
        " recommend ",
        " design ",
        " think through ",
        " reason through ",
        " what should ",
        " should i ",
        " should we ",
    )
    execution_markers = (
        " run ",
        " execute ",
        " apply ",
        " edit ",
        " write ",
        " modify ",
        " create ",
        " delete ",
        " remove ",
        " commit ",
        " push ",
        " install ",
        " start working ",
        " carry on ",
        " go ahead ",
        " do it ",
    )
    return any(marker in lowered for marker in advisory_markers) and not any(
        marker in lowered for marker in execution_markers
    )


def _int_arg(arguments: dict[str, Any], key: str, default: int) -> int:
    raw = str((arguments or {}).get(key) or "").strip()
    return int(raw) if raw.isdigit() else default


def _machine_disk_usage(arguments: dict[str, Any]) -> RuntimeExecutionResult:
    from core import machine_diagnostics

    scope = str((arguments or {}).get("drive") or "").strip()
    rows = machine_diagnostics.disk_usage(drives=[scope]) if scope else machine_diagnostics.disk_usage()
    if not rows:
        # Scoped ask that read nothing: honest, drive-specific message (not a guess, not all-drives).
        missing = f"I couldn't read drive {scope} on this machine." if scope else "I couldn't read any drive on this machine."
        return RuntimeExecutionResult(
            handled=True, ok=False, status="no_results",
            response_text=missing,
            details={"observation": _tool_observation(intent="machine.disk_usage", tool_surface="machine", ok=False, status="no_results", scope=scope)},
        )
    if scope:
        # The user named one drive -> report that drive only, never the all-drive total.
        row = rows[0]
        response = (
            f"Drive {row['mount']} — {row['free_gb']} GB free of {row['total_gb']} GB "
            f"({row['percent_used']}% used)."
        )
        return RuntimeExecutionResult(
            handled=True, ok=True, status="executed",
            response_text=response,
            details={
                "result_type": "drive_space",
                "requested_drive": scope,
                "reported_drive": str(row["mount"]),
                "drives": rows,
                "observation": _tool_observation(intent="machine.disk_usage", tool_surface="machine", ok=True, status="executed", drive_count=1, scope=scope),
            },
        )
    lines = [f"{r['mount']}  {r['free_gb']} GB free of {r['total_gb']} GB ({r['percent_used']}% used)" for r in rows]
    total_free = round(sum(r["free_gb"] for r in rows), 1)
    return RuntimeExecutionResult(
        handled=True, ok=True, status="executed",
        response_text="Drive space:\n" + "\n".join(lines) + f"\nTotal free: {total_free} GB across {len(rows)} drive(s).",
        details={
            "result_type": "drive_space",
            "requested_drive": "",
            "drives": rows,
            "observation": _tool_observation(intent="machine.disk_usage", tool_surface="machine", ok=True, status="executed", drive_count=len(rows), total_free_gb=total_free),
        },
    )


# Directory names that are never a user's folder and make full-drive walks slow.
_FIND_FOLDER_SKIP_DIRS = frozenset(
    {
        "$recycle.bin",
        "$windows.~bt",
        "system volume information",
        "windows",
        "windows.old",
        "winsxs",
        "programdata",
        "recovery",
        "config.msi",
        "msocache",
        "node_modules",
        ".git",
        "__pycache__",
        "site-packages",
        "appdata",
    }
)
_FIND_FOLDER_MAX_DEPTH = 6
_FIND_FOLDER_MAX_MATCHES = 25
_FIND_FOLDER_TIME_BUDGET_S = 10.0



_FIND_FILE_MAX_MATCHES = 25


def _machine_find_file(
    arguments: dict[str, Any], *, workspace_root: Path | None = None
) -> RuntimeExecutionResult:
    """Find a FILE by name under the safe machine roots.

    This capability did not exist. `machine.find_folder` matches directories only,
    `workspace.list_files` can match files but is confined to the bound project, and
    `workspace.read_file` stats a single path. So "find vool_agent.py on my Desktop" — a request
    `find(1)` answers in 44 milliseconds — had no lane that could answer it, and the runtime told an
    operator the file did not exist while three copies sat on their Desktop.

    Deliberately the same BFS as `_machine_find_folder`: same safe roots, same skip list, same
    depth/time/match ceilings, and the same `(st_dev, st_ino)` identity check that stops macOS
    firmlinks reporting one file twice. Matching is case-insensitive and substring, because the
    operator who typed `Vool_agent.PY` meant the file that is on disk as `vool_agent.py`.

    ANVIL F3, 2026-08-07: this walk never descends into another project's directory (checked via
    `_is_other_git_project`, the same policy `machine.list_directory` applies to what it NAMES) --
    a chat bound to project-a could otherwise `machine.find_file` its way to a file living inside
    project-b even though `list_directory` no longer names project-b at all. `machine.read_file`
    needs no change: it was never the leak, and an EXPLICIT path to project-b typed by the user
    still reads exactly as before -- only implicit, by-name discovery is scoped here.
    """
    name = str(arguments.get("name") or "").strip()
    if not name:
        return RuntimeExecutionResult(
            handled=True, ok=False, status="invalid_arguments",
            response_text="Tell me the file name to look for.",
            details={},
        )
    needle = name.lower()
    roots = [root for root in _safe_machine_roots() if os.path.isdir(root)]
    if not roots:
        return RuntimeExecutionResult(
            handled=True, ok=False, status="unavailable",
            response_text="None of the searchable local folders are readable right now.",
            details={},
        )

    import time as _time
    from collections import deque

    deadline = _time.monotonic() + _FIND_FOLDER_TIME_BUDGET_S
    matches: list[str] = []
    scanned_dirs = 0
    timed_out = False
    seen: set[tuple[int, int]] = set()

    def _first_visit(path: str) -> bool:
        try:
            info = os.stat(path, follow_symlinks=False)
        except OSError:
            return True
        key = (info.st_dev, info.st_ino)
        if key in seen:
            return False
        seen.add(key)
        return True

    queue: deque[tuple[str, int]] = deque((root, 0) for root in roots if _first_visit(root))
    while queue:
        if _time.monotonic() > deadline:
            timed_out = True
            break
        current, depth = queue.popleft()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    lowered_name = entry.name.lower()
                    if lowered_name in _FIND_FOLDER_SKIP_DIRS:
                        continue
                    try:
                        is_dir = entry.is_dir(follow_symlinks=False)
                    except OSError:
                        continue
                    if is_dir:
                        if _is_other_git_project(Path(entry.path), workspace_root=workspace_root):
                            continue
                        if depth + 1 <= _FIND_FOLDER_MAX_DEPTH and _first_visit(entry.path):
                            scanned_dirs += 1
                            queue.append((entry.path, depth + 1))
                        continue
                    if needle in lowered_name:
                        if not _first_visit(entry.path):
                            continue
                        matches.append(entry.path)
                        if len(matches) >= _FIND_FILE_MAX_MATCHES:
                            queue.clear()
                            break
        except OSError:
            continue

    scope = ", ".join(_home_relative_label(Path(root)) for root in roots)
    observation = _tool_observation(
        intent="machine.find_file",
        tool_surface="machine",
        ok=True,
        status="executed",
        query=name,
        match_count=len(matches),
        scanned_dirs=scanned_dirs,
        searched_roots=roots,
        timed_out=timed_out,
        truncated=len(matches) >= _FIND_FILE_MAX_MATCHES,
    )
    if matches:
        lines = [f"Found {len(matches)} file{'s' if len(matches) != 1 else ''} matching '{name}':"]
        lines.extend(f"- {path}" for path in matches)
        lines.append(f"Searched: {scope} (to depth {_FIND_FOLDER_MAX_DEPTH}; system folders skipped).")
        if len(matches) >= _FIND_FILE_MAX_MATCHES:
            lines.append("Showing the first matches only; refine the name to narrow the search.")
        if timed_out:
            lines.append("The search hit its time limit, so deeper folders were not fully scanned.")
        return RuntimeExecutionResult(
            handled=True, ok=True, status="executed",
            response_text="\n".join(lines),
            details={"matches": matches, "observation": observation},
        )
    # A miss is a completed search, and it says WHERE it looked. "does not exist" is a claim about
    # the machine that a bounded search is not entitled to make.
    lines = [
        f"No file matching '{name}' found in {scope} "
        f"(searched to depth {_FIND_FOLDER_MAX_DEPTH}; system folders skipped)."
    ]
    if timed_out:
        lines.append("The search hit its time limit before covering everything, so a deeper copy could exist.")
    return RuntimeExecutionResult(
        handled=True, ok=True, status="executed",
        response_text="\n".join(lines),
        details={"matches": [], "observation": observation},
    )

def _resolved_existing_directory(value: str) -> str | None:
    """The absolute path ``value`` names, if it is path-shaped and that directory exists.

    Returns None for a bare folder name, which is what the disk search is actually for. Only a
    string that already looks like a path is considered, so a folder legitimately named with a
    tilde or a dot is not misread as a location.
    """

    text = str(value or "").strip().strip("'\"")
    if not text:
        return None
    looks_like_path = text.startswith(("~", "/", ".")) or os.sep in text or "/" in text
    if not looks_like_path:
        return None
    try:
        candidate = os.path.abspath(os.path.expanduser(os.path.expandvars(text)))
        return candidate if os.path.isdir(candidate) else None
    except (OSError, ValueError):
        return None


# Words that describe WHERE or WHOSE, rather than naming the folder. A phrase built only from
# these carries no name to search for.
_FIND_FOLDER_STOPWORDS = frozenset(
    {
        "a", "all", "an", "and", "any", "at", "every", "files", "folder", "folders", "for",
        "from", "in", "inside", "into", "is", "it", "its", "me", "my", "of", "on", "our", "please",
        "s", "show", "that", "the", "their", "there", "this", "to", "under", "what", "whats",
        "where", "which", "with", "your",
        # Function words and generic ask-verbs. "what ARE the folders on my desktop" left "are"
        # as the only meaningful token, and a 3-letter substring probe then matched it inside an
        # unrelated directory name (NULL-ARE-NTED), hijacking a plain root listing to that folder.
        # A word that describes the ASKING can never name the folder being asked about.
        "am", "are", "be", "been", "being", "can", "could", "did", "do", "does", "give", "had",
        "has", "have", "he", "i", "kept", "lives", "look", "may", "might", "must", "name", "peek",
        "see", "she", "should", "sitting", "stashed", "stored", "stuff", "tell", "they", "things",
        "walk", "was", "we", "were", "will", "would", "you",
        # Discourse fillers. "ok and where is the foldeR?!" (measured live 2026-09-18) reduced to
        # the folder name `ok` and the runtime answered a where-question with a whole-disk search
        # for folders named "ok". An acknowledgement can never name a folder either.
        "alright", "fine", "good", "great", "hello", "hey", "hi", "hmm", "lol", "nah", "nice",
        "no", "np", "okey", "ok", "okay", "oops", "right", "sure", "thanks", "thank", "thx",
        "yeah", "yes", "yep", "yo",
    }
)

# A location word does not name a folder, but it does say where to start looking.
_FIND_FOLDER_SCOPE_HINTS = {
    "desktop": "~/Desktop",
    "downloads": "~/Downloads",
    "documents": "~/Documents",
    "home": "~",
}


def _find_folder_name_from_phrase(text: str) -> tuple[str, str]:
    """Reduce a descriptive phrase to a searchable name and a starting scope.

    Callers pass what the user said. "my notes folder on the desktop" arrived here verbatim as the
    folder *name*, so the search walked every drive to depth 6 looking for a directory literally
    called that, and answered "not found" for `~/Desktop/my-notes-folder` — measured against the
    deployed build on 2026-07-28, and the most natural way a person phrases the request.

    Returns ``(name, scope)``. An already-simple name is returned unchanged, so a real folder
    called "my documents" is not mangled: reduction only happens when the phrase contains
    stopwords, which a bare folder name does not.
    """

    raw = " ".join(str(text or "").split()).strip(" .'\"")
    # A path is never a phrase, even when it contains words from the list. macOS temp paths look
    # like /private/var/folders/xy/... — reducing that would strip "folders" out of a real
    # location and turn a resolvable path into a disk-wide search for nothing.
    if _resolved_existing_directory(raw) is not None or "/" in raw or os.sep in raw:
        return raw, ""
    words = [w for w in re.split(r"[^\w\-]+", raw.lower()) if w]
    if not words or not any(w in _FIND_FOLDER_STOPWORDS for w in words):
        return raw, ""

    scope = ""
    for word in words:
        if word in _FIND_FOLDER_SCOPE_HINTS:
            scope = _FIND_FOLDER_SCOPE_HINTS[word]
            break
    # A scope word is also a location, so drop it from the name only when it is not the only
    # thing left — "what is on the desktop" genuinely means the Desktop itself.
    meaningful = [w for w in words if w not in _FIND_FOLDER_STOPWORDS and w not in _FIND_FOLDER_SCOPE_HINTS]
    if not meaningful:
        if scope:
            # "what is on the desktop" genuinely means the Desktop itself.
            return "", scope
        # The whole phrase was function words and fillers with no location hint ("folder",
        # "ok and where is the folder"). Returning the RAW phrase here made the runtime search
        # the whole disk for directories literally named "folder" (measured live 2026-09-18).
        # A residue of nothing is not a name; the caller falls open instead of searching.
        return "", ""
    # The stopword list is an ALLOWLIST of function words, so any word the author did not enumerate
    # survives as if it were a proper noun. Measured live: "its somewhwere on my desktop in one of
    # folders i think" reduced to the folder name `somewhwere one think` — a typo, a quantifier and
    # a verb — and the runtime then walked the whole disk looking for it. A residue that is only
    # vague words is not a name; returning nothing lets the caller ask instead of searching for
    # nonsense.
    if len(meaningful) > 2 and not any(
        _looks_like_a_name(word) for word in meaningful
    ):
        return "", scope
    return " ".join(meaningful), scope


def _looks_like_a_name(word: str) -> bool:
    """Whether one token could plausibly be part of a folder/file NAME rather than prose.

    Deliberately shape-based, not a vocabulary: a name-ish token carries a separator, a digit, an
    extension, or internal capitalisation — the things prose words do not have. A phrase made only
    of ordinary lowercase words has told us nothing to search for.
    """
    token = str(word or "")
    if not token:
        return False
    if any(ch.isdigit() for ch in token):
        return True
    if any(sep in token for sep in ("-", "_", ".", "/")):
        return True
    return token != token.lower()


def _machine_find_folder(
    arguments: dict[str, Any], *, workspace_root: Path | None = None
) -> RuntimeExecutionResult:
    """Case-insensitive folder-name search across the machine's drive roots.

    Read-only, bounded (depth/time/match caps), skips system/noise directories and
    never follows symlinks or junctions. Returns verified paths, the searched scope,
    and an honest not-found message.

    ANVIL F3, 2026-08-07: neither the scoped shortcut scan nor the full BFS below descends into,
    or matches, another project's directory (`_is_other_git_project`) -- a chat bound to
    project-a could otherwise `machine.find_folder` its way straight to project-b even though
    `list_directory` no longer names it. `_resolved_existing_directory`'s two direct-resolution
    shortcuts above (an already path-shaped query) are untouched: a user who types an explicit
    path is asking a different, allowed question, not doing implicit by-name discovery.
    """
    import time as _time
    from collections import deque

    from core import machine_diagnostics

    name = " ".join(str((arguments or {}).get("name") or (arguments or {}).get("query") or "").split()).strip(" .'\"")
    if not name:
        return RuntimeExecutionResult(
            handled=True, ok=False, status="missing_argument",
            response_text="Tell me the folder name to look for (for example: find my Dropbox folder).",
            details={"observation": _tool_observation(intent="machine.find_folder", tool_surface="machine", ok=False, status="missing_argument")},
        )
    # A path is not a name. Callers routinely pass the whole thing a user typed
    # ("~/Desktop/vool-fresh-7z9"), and searching for a directory *named* that can never match:
    # the answer came back "No folder matching '~/Desktop/vool-fresh-7z9' found on /" after a
    # depth-6 walk of every drive, for a folder that was sitting right there. Two separate costs
    # — a wrong answer, and a full-disk scan on every such call.
    #
    # `~` is the specific trap: os.path.isdir("~/Desktop/x") is False while the expanded form is
    # True, so an unexpanded tilde looks exactly like a nonexistent path.
    # A description is not a name. Reduce it before anything decides to walk the disk.
    phrase_name, phrase_scope = _find_folder_name_from_phrase(name)
    if phrase_scope and not phrase_name:
        # The phrase named only a location ("what is on the desktop") — that place IS the answer.
        located = _resolved_existing_directory(phrase_scope)
        if located:
            return RuntimeExecutionResult(
                handled=True,
                ok=True,
                status="executed",
                response_text=f"Found 1 folder matching '{name}':\n- {located}",
                details={
                    "observation": _tool_observation(
                        intent="machine.find_folder", tool_surface="machine", ok=True,
                        status="executed", query=name, match_count=1, scanned_dirs=0,
                        searched_roots=[], timed_out=False, truncated=False, resolved_directly=True,
                    ),
                    "matches": [located],
                    "resolved_target": located,
                },
            )
    if phrase_name and phrase_name != name:
        # Try the reduced name inside the hinted scope first: cheap, exact, and it is where a
        # user who said "on the desktop" expects it to look.
        scope_root = _resolved_existing_directory(phrase_scope) if phrase_scope else None
        if scope_root:
            needle_sep_probe = re.sub(r"[\s_\-]+", "", phrase_name.lower())
            try:
                with os.scandir(scope_root) as entries:
                    for entry in entries:
                        if not entry.is_dir(follow_symlinks=False):
                            continue
                        if _is_other_git_project(Path(entry.path), workspace_root=workspace_root):
                            continue
                        compact = re.sub(r"[\s_\-]+", "", entry.name.lower())
                        if needle_sep_probe and needle_sep_probe in compact:
                            return RuntimeExecutionResult(
                                handled=True, ok=True, status="executed",
                                response_text=f"Found 1 folder matching '{name}':\n- {entry.path}",
                                details={
                                    "observation": _tool_observation(
                                        intent="machine.find_folder", tool_surface="machine",
                                        ok=True, status="executed", query=name, match_count=1,
                                        scanned_dirs=1, searched_roots=[scope_root],
                                        timed_out=False, truncated=False, resolved_directly=True,
                                    ),
                                    "matches": [entry.path],
                                    "resolved_target": entry.path,
                                },
                            )
            except OSError:
                pass
        # Nothing in the hinted place: fall through to the full search with the REDUCED name,
        # never the sentence, so the walk at least has something findable to look for.
        name = phrase_name

    resolved = _resolved_existing_directory(name)
    if resolved is not None:
        return RuntimeExecutionResult(
            handled=True,
            ok=True,
            status="executed",
            response_text=f"Found 1 folder matching '{name}':\n- {resolved}",
            details={
                "observation": _tool_observation(
                    intent="machine.find_folder",
                    tool_surface="machine",
                    ok=True,
                    status="executed",
                    query=name,
                    match_count=1,
                    scanned_dirs=0,
                    searched_roots=[],
                    timed_out=False,
                    truncated=False,
                    resolved_directly=True,
                ),
                "matches": [resolved],
                "resolved_target": resolved,
            },
        )

    needle = name.lower()
    # Separator-insensitive needle: "token hunter" must also find "token-hunter" / "token_hunter" /
    # "tokenhunter". Folder names mix spaces, hyphens and underscores freely, so collapse them all away
    # on both sides before comparing. (Without this, a folder plainly visible in a listing reads as
    # "not found" the moment its name uses a different separator than the query — a real reported bug.)
    needle_sep = re.sub(r"[\s_\-]+", "", needle)
    roots = [str(row.get("mount") or "") for row in machine_diagnostics.disk_usage() if str(row.get("mount") or "")]
    if not roots:
        return RuntimeExecutionResult(
            handled=True, ok=False, status="no_results",
            response_text="I couldn't read any drive on this machine to search.",
            details={"observation": _tool_observation(intent="machine.find_folder", tool_surface="machine", ok=False, status="no_results")},
        )
    deadline = _time.monotonic() + _FIND_FOLDER_TIME_BUDGET_S
    matches: list[str] = []
    scanned_dirs = 0
    timed_out = False
    # macOS mounts the data volume at BOTH `/Users/...` and `/System/Volumes/Data/Users/...` via a
    # firmlink. They are not symlinks, so `realpath` does not collapse them: the walk descends the
    # whole home directory twice and reports each hit twice. Measured live 2026-07-31, "find the
    # folder called vool-lego-probe" answered "Found 2 folders" and listed one directory under both
    # names -- `ls -di` gives inode 102487738 for both. A count of 2 for one folder is a wrong
    # answer, and walking the tree twice is what makes these searches take the better part of a
    # minute. Identity is (st_dev, st_ino); anything already visited under another name is skipped.
    seen: set[tuple[int, int]] = set()

    def _first_visit(path: str) -> bool:
        try:
            info = os.stat(path, follow_symlinks=False)
        except OSError:
            return True  # unreadable: fall back to visiting it, never drop a branch silently
        key = (info.st_dev, info.st_ino)
        if key in seen:
            return False
        seen.add(key)
        return True

    queue: deque[tuple[str, int]] = deque((root, 0) for root in roots if _first_visit(root))
    while queue:
        if _time.monotonic() > deadline:
            timed_out = True
            break
        current, depth = queue.popleft()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if not entry.is_dir(follow_symlinks=False):
                            continue
                    except OSError:
                        continue
                    entry_name = entry.name
                    lowered_name = entry_name.lower()
                    if lowered_name in _FIND_FOLDER_SKIP_DIRS:
                        continue
                    if _is_other_git_project(Path(entry.path), workspace_root=workspace_root):
                        continue
                    if not _first_visit(entry.path):
                        continue
                    scanned_dirs += 1
                    if needle in lowered_name or (needle_sep and needle_sep in re.sub(r"[\s_\-]+", "", lowered_name)):
                        matches.append(entry.path)
                        if len(matches) >= _FIND_FOLDER_MAX_MATCHES:
                            queue.clear()
                            break
                    if depth + 1 <= _FIND_FOLDER_MAX_DEPTH:
                        queue.append((entry.path, depth + 1))
        except OSError:
            continue
    scope = ", ".join(roots)
    observation = _tool_observation(
        intent="machine.find_folder",
        tool_surface="machine",
        ok=True,
        status="executed",
        query=name,
        match_count=len(matches),
        scanned_dirs=scanned_dirs,
        searched_roots=roots,
        timed_out=timed_out,
        truncated=len(matches) >= _FIND_FOLDER_MAX_MATCHES,
    )
    if matches:
        lines = [f"Found {len(matches)} folder{'s' if len(matches) != 1 else ''} matching '{name}':"]
        lines.extend(f"- {path}" for path in matches)
        lines.append(f"Searched: {scope} (to depth {_FIND_FOLDER_MAX_DEPTH}, system folders skipped).")
        if len(matches) >= _FIND_FOLDER_MAX_MATCHES:
            lines.append("Showing the first matches only; refine the name to narrow the search.")
        if timed_out:
            lines.append("The search hit its time limit, so deeper folders were not fully scanned.")
        return RuntimeExecutionResult(
            handled=True, ok=True, status="executed",
            response_text="\n".join(lines),
            details={"matches": matches, "observation": observation},
        )
    not_found = [
        f"No folder matching '{name}' found on {scope} "
        f"(searched to depth {_FIND_FOLDER_MAX_DEPTH}; system folders skipped)."
    ]
    if timed_out:
        not_found.append("The search hit its time limit before covering everything, so a deeper folder could exist.")
    return RuntimeExecutionResult(
        handled=True, ok=True, status="executed",
        response_text=" ".join(not_found),
        details={"matches": [], "observation": observation},
    )


def _machine_find_largest(
    arguments: dict[str, Any], *, workspace_root: Path | None = None
) -> RuntimeExecutionResult:
    import time as _time

    from core import machine_diagnostics

    requested_drive = str((arguments or {}).get("drive") or (arguments or {}).get("path") or "").strip()
    if requested_drive:
        root, resolution_error = _resolve_find_largest_root(requested_drive)
        if root is None:
            allowed_roots = [_home_relative_label(r) for r in _safe_machine_roots()]
            return RuntimeExecutionResult(
                handled=True,
                ok=False,
                status="not_allowed",
                response_text=resolution_error
                or f"I can only inspect safe local directories in this lane: {', '.join(allowed_roots)}.",
                details={
                    "observation": _tool_observation(
                        intent="machine.find_largest",
                        tool_surface="machine",
                        ok=False,
                        status="not_allowed",
                        allowed_roots=allowed_roots,
                    ),
                },
            )
    else:
        if os.name == "nt":
            # Windows: the system drive (C:) holds the user profile, so scan it.
            rows = machine_diagnostics.disk_usage()
            root = next((str(r.get("mount") or "") for r in rows if str(r.get("mount") or "").upper().startswith("C")), "")
            if not root:
                root = rows[0]["mount"] if rows else "C:\\"
        else:
            # macOS/Linux: default to the user's home, not "/". Home is where the space the user
            # actually cares about lives, it stays inside the confined roots, and it scans fast —
            # scanning "/" times out on system dirs and returns a useless partial lower bound.
            root = str(_machine_home())
    try:
        top = int(str((arguments or {}).get("top") or "8"))
    except (TypeError, ValueError):
        top = 8
    top = max(1, min(top, 25))
    # "biggest FILE" wants the largest individual (often nested) file, not a top-level folder
    # aggregate; "biggest FOLDERS" wants folders only; an unqualified "biggest items" keeps the
    # mixed top-level ranking.
    kind = str((arguments or {}).get("kind") or "both").strip().lower()
    result_type = {"files": "largest_file", "folders": "largest_folders"}.get(kind, "largest_items")
    _scan_start = _time.monotonic()
    scan_top = _find_largest_scan_ceiling(top)
    if kind == "files":
        result = machine_diagnostics.largest_files(root, top=scan_top)
        noun = "files"
    else:
        result = machine_diagnostics.largest_entries(root, top=max(scan_top, top))
        noun = "folders" if kind == "folders" else "items"
    elapsed_ms = int((_time.monotonic() - _scan_start) * 1000)
    raw_entries = list(result.get("entries") or [])
    if kind == "folders":
        raw_entries = [e for e in raw_entries if str(e.get("kind") or "") == "folder"]
    hidden_count = 0
    entries: list[dict[str, Any]] = []
    for entry in raw_entries:
        if _is_under_other_git_project(Path(str(entry.get("path") or "")), workspace_root=workspace_root):
            hidden_count += 1
            continue
        entries.append(entry)
        if len(entries) >= top:
            break
    hidden_note = (
        f" ({hidden_count} result(s) from other project(s) were left out.)" if hidden_count else ""
    )
    if result.get("error") == "unreadable" or not entries:
        return RuntimeExecutionResult(
            handled=True, ok=False, status="no_results",
            response_text=f"I couldn't scan {root} for its largest {noun}, so I won't guess.{hidden_note}",
            details={
                "hidden_other_project_results": hidden_count,
                "observation": _tool_observation(intent="machine.find_largest", tool_surface="machine", ok=False, status="no_results", root=root, hidden_other_project_results=hidden_count),
            },
        )
    lines = [f"Largest {noun} on {root} (measured, largest first):"]
    for entry in entries:
        partial = "" if entry.get("complete") else " (partial — scan hit its time limit)"
        lines.append(
            f"- {entry['name']} — {entry['size_gb']} GB [{entry['kind']}]{partial} · {entry['delete_safety']}"
        )
    if not result.get("complete"):
        lines.append("Some folders were not fully measured within the time budget; sizes marked (partial) are a lower bound.")
    if hidden_note:
        lines.append(hidden_note.strip(" ()"))
    lines.append("I won't delete anything without your explicit go-ahead on a specific path.")
    return RuntimeExecutionResult(
        handled=True, ok=True, status="executed",
        response_text="\n".join(lines),
        details={
            "result_type": result_type,
            "requested_drive": requested_drive,
            "root": root,
            "searched_roots": [root],
            "elapsed_ms": elapsed_ms,
            "entries": entries,
            "complete": bool(result.get("complete")),
            "hidden_other_project_results": hidden_count,
            "observation": _tool_observation(intent="machine.find_largest", tool_surface="machine", ok=True, status="executed", root=root, entry_count=len(entries), complete=bool(result.get("complete")), hidden_other_project_results=hidden_count),
        },
    )


def _machine_display_inspect(arguments: dict[str, Any]) -> RuntimeExecutionResult:
    from core import machine_diagnostics

    info = machine_diagnostics.display_info()
    if not info.get("supported"):
        # `display_info` reads the Windows WMI tables and nothing else. But this runtime ALREADY
        # reads the display on macOS -- `_machine_display_details` shells out to system_profiler and
        # `machine.inspect_specs` prints its answer -- so refusing here told the user the runtime
        # could not see something it was reporting one tool over. Measured live 2026-07-31: all
        # eight of "whats my screen resolution", "what refresh rate is my monitor at", "how many
        # displays do i have" and the rest answered "Display inspection is only available on Windows
        # in this build", while the spec sheet on the same host said "Native display resolution:
        # 4480 x 2520 / Current display mode: 2240 x 1260 @ 60.00Hz" -- which `system_profiler
        # SPDisplaysDataType` confirms exactly.
        details = _machine_display_details()
        if details.get("native_resolution") or details.get("current_resolution"):
            lines = [f"- {details.get('name') or 'Display'}"]
            if details.get("native_resolution"):
                lines.append(f"- Native resolution: {details['native_resolution']}")
            if details.get("current_resolution"):
                lines.append(f"- Current mode: {details['current_resolution']}")
            if details.get("screen_size"):
                lines.append(f"- Screen size: {details['screen_size']}")
            else:
                lines.append("Physical screen size could not be verified, so I won't estimate it.")
            return RuntimeExecutionResult(
                handled=True, ok=True, status="executed",
                response_text="Display:\n" + "\n".join(lines),
                details={
                    "displays": [details],
                    "observation": _tool_observation(
                        intent="machine.display_inspect", tool_surface="machine", ok=True,
                        status="executed", display_count=1, source="system_profiler",
                    ),
                },
            )
        return RuntimeExecutionResult(
            handled=True, ok=False, status="unsupported_platform",
            response_text="I couldn't read the display configuration on this platform, so I won't guess it.",
            details={"observation": _tool_observation(intent="machine.display_inspect", tool_surface="machine", ok=False, status="unsupported_platform")},
        )
    displays = list(info.get("displays") or [])
    if not displays:
        return RuntimeExecutionResult(
            handled=True, ok=False, status="no_results",
            response_text="I couldn't read the display configuration from Windows, so I won't guess it.",
            details={"observation": _tool_observation(intent="machine.display_inspect", tool_surface="machine", ok=False, status="no_results")},
        )
    lines = ["Display" + ("s" if len(displays) != 1 else "") + ":"]
    for entry in displays:
        refresh = f" @ {entry['refresh_hz']} Hz" if entry.get("refresh_hz") else ""
        lines.append(f"- {entry.get('name') or 'Display'}: {entry['width']} x {entry['height']}{refresh}")
    physical = dict(info.get("physical") or {})
    if physical.get("verified") and physical.get("diagonal_in"):
        lines.append(f"Physical size: about {physical['diagonal_in']}\" diagonal (from the monitor's EDID).")
    else:
        lines.append("Physical screen size could not be verified (no EDID data), so I won't estimate it.")
    return RuntimeExecutionResult(
        handled=True, ok=True, status="executed",
        response_text="\n".join(lines),
        details={
            "displays": displays,
            "physical": physical,
            "observation": _tool_observation(intent="machine.display_inspect", tool_surface="machine", ok=True, status="executed", display_count=len(displays)),
        },
    )


def _machine_event_log_errors(arguments: dict[str, Any]) -> RuntimeExecutionResult:
    from core import machine_diagnostics

    report = machine_diagnostics.event_log_errors(limit=_int_arg(arguments, "limit", 25))
    if not report.get("supported"):
        return RuntimeExecutionResult(
            handled=True, ok=False, status="unsupported_platform",
            response_text="Windows Event Log checks are only available on Windows.",
            details={"report": report, "observation": _tool_observation(intent="machine.event_log_errors", tool_surface="machine", ok=False, status="unsupported_platform")},
        )
    parts: list[str] = []
    for entry in report.get("logs", []):
        if entry.get("text"):
            parts.append(f"[{entry['log']}]\n{entry['text']}")
        elif entry.get("error"):
            parts.append(f"[{entry['log']}] could not be read: {entry['error']}")
    body = "\n\n".join(parts) if parts else "No recent Critical/Error events found."
    return RuntimeExecutionResult(
        handled=True, ok=True, status="executed",
        response_text="Recent Windows errors (Critical/Error):\n\n" + body,
        details={"report": report, "observation": _tool_observation(intent="machine.event_log_errors", tool_surface="machine", ok=True, status="executed")},
    )


def _machine_list_processes(arguments: dict[str, Any]) -> RuntimeExecutionResult:
    from core import machine_diagnostics

    sort = "cpu" if str((arguments or {}).get("sort") or "").strip().lower() == "cpu" else "memory"
    report = machine_diagnostics.top_processes(limit=_int_arg(arguments, "limit", 12), sort=sort)
    procs = report.get("processes") or []
    if not procs:
        return RuntimeExecutionResult(
            handled=True, ok=False, status="no_results",
            response_text="I couldn't read the process list on this machine.",
            details={"report": report, "observation": _tool_observation(intent="machine.list_processes", tool_surface="machine", ok=False, status="no_results")},
        )
    if sort == "cpu":
        heading = "Top processes by CPU right now:"
        lines = [
            f"{p.get('name', '?')} (pid {p.get('pid', '?')})  {p.get('cpu_percent', 0)}% CPU  {p.get('rss_mb', 0)} MB"
            for p in procs
        ]
    else:
        heading = "Top processes by memory:"
        lines = [f"{p.get('name', '?')} (pid {p.get('pid', '?')})  {p.get('rss_mb', 0)} MB" for p in procs]
    return RuntimeExecutionResult(
        handled=True, ok=True, status="executed",
        response_text=heading + "\n" + "\n".join(lines),
        details={
            "processes": procs, "source": report.get("source"), "sort": sort,
            "observation": _tool_observation(intent="machine.list_processes", tool_surface="machine", ok=True, status="executed", process_count=len(procs), sort=sort),
        },
    )


def _format_uptime(seconds: float) -> str:
    total = int(max(0.0, float(seconds)))
    days, rest = divmod(total, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _machine_host_state(arguments: dict[str, Any]) -> RuntimeExecutionResult:
    """Uptime, battery and chassis — the live host facts the static spec sheet does not carry."""
    del arguments
    from core import machine_diagnostics

    state = machine_diagnostics.host_state()
    lines: list[str] = []
    uptime_seconds = state.get("uptime_seconds")
    if uptime_seconds is not None:
        lines.append(f"- Uptime: {_format_uptime(uptime_seconds)}")
    chassis = str(state.get("chassis") or "").strip()
    model = str(state.get("hardware_model") or "").strip()
    if chassis:
        lines.append(f"- Chassis: {chassis}" + (f" ({model})" if model else ""))
    battery = dict(state.get("battery") or {})
    present = battery.get("present")
    if present is True and battery.get("percent") is not None:
        power = "plugged in" if battery.get("plugged_in") else "on battery"
        lines.append(f"- Battery: {battery['percent']}% ({power})")
    elif present is False:
        # Stated plainly rather than omitted: "this machine has no battery" IS the answer to
        # "what's the battery percentage" on a desktop, and silence would read as a failed read.
        lines.append("- Battery: none (this machine has no battery)")
    if not lines:
        return RuntimeExecutionResult(
            handled=True, ok=False, status="no_results",
            response_text="I couldn't read this machine's uptime or power state.",
            details={"state": state, "observation": _tool_observation(intent="machine.host_state", tool_surface="machine", ok=False, status="no_results")},
        )
    return RuntimeExecutionResult(
        handled=True, ok=True, status="executed",
        response_text="Current state of this host:\n" + "\n".join(lines),
        details={
            "state": state,
            "observation": _tool_observation(
                intent="machine.host_state", tool_surface="machine", ok=True, status="executed",
                uptime_seconds=uptime_seconds, chassis=chassis, hardware_model=model,
                battery_present=present, battery_percent=battery.get("percent"),
                battery_plugged_in=battery.get("plugged_in"),
            ),
        },
    )


def _machine_move_path(
    arguments: dict[str, Any], *, workspace_root: Path | None = None
) -> RuntimeExecutionResult:
    from core.machine_file_ops import move_path

    # ANVIL F6, 2026-08-07: the real dispatcher always supplies the bound workspace plus the
    # safe machine roots as the containment boundary -- a positive allowlist, not another
    # denylist. `move_path` itself defaults `allowed_roots` to `None` (no containment beyond the
    # existing protected-path denylist) purely so its own unit tests, which move plain files
    # around an arbitrary tmp directory unrelated to any workspace, keep working unmodified.
    allowed_roots = list(_safe_machine_roots())
    if workspace_root is not None:
        allowed_roots.append(workspace_root)
    result = move_path(
        str((arguments or {}).get("source") or ""),
        str((arguments or {}).get("destination") or ""),
        allowed_roots=allowed_roots,
    )
    return RuntimeExecutionResult(
        handled=True,
        ok=result.ok,
        status=result.status,
        response_text=result.message,
        details={
            "source": result.source,
            "dest": result.dest,
            "observation": _tool_observation(
                intent="machine.move_path", tool_surface="machine", ok=result.ok, status=result.status,
                source=result.source, dest=result.dest,
            ),
        },
    )


def _operator_profile_tool(intent: str, arguments: dict[str, Any], source_context: dict[str, Any] | None) -> RuntimeExecutionResult:
    """The model lane into the Operator Profile: a structured tool call becomes the same typed
    proposal the deterministic front-door lane emits, applied by the ONE authority."""
    from core import operator_profile as profile
    from core.operator_profile_interpretation import proposals_from_tool_arguments
    from core.operator_profile_turn import current_turn_scope

    bound_principal, bound_session, _project = current_turn_scope()
    principal = bound_principal or profile.principal_for_request(source_context)
    session_id = bound_session or str((source_context or {}).get("runtime_session_id") or (source_context or {}).get("chat_id") or "")
    if intent == "profile.list":
        items = [item.as_dict() for item in profile.list_items(principal, session_id=session_id)] if principal else []
        return RuntimeExecutionResult(
            handled=True, ok=True, status="ok",
            response_text="\n".join(f"- {i['label']}: {i['value_text']} ({i['scope']})" for i in items) or "Nothing is saved in the profile yet.",
            details={"items": items, "observation": _tool_observation(intent=intent, tool_surface="profile", ok=True, status="ok")},
        )
    proposals = proposals_from_tool_arguments(intent, arguments)
    if not proposals or not principal:
        return RuntimeExecutionResult(
            handled=True, ok=False, status="invalid_arguments",
            response_text="That is not a profile category I store." if principal else "No profile is available for this caller.",
            details={"observation": _tool_observation(intent=intent, tool_surface="profile", ok=False, status="invalid_arguments")},
        )
    proposal = proposals[0]
    if proposal.action == "forget":
        items = profile.list_items(principal, session_id=session_id)
        if proposal.category != "*":
            items = [i for i in items if i.category == proposal.category]
        reports = [profile.forget_item(i.item_id, actor="model_tool").report for i in items]
        text = "; ".join(reports) if reports else "Nothing to forget."
        return RuntimeExecutionResult(handled=True, ok=True, status="ok", response_text=text,
                                      details={"observation": _tool_observation(intent=intent, tool_surface="profile", ok=True, status="ok")})
    if proposal.strength == "explicit":
        change = profile.remember(principal, proposal.category, proposal.value, scope=proposal.scope, session_id=session_id, origin="explicit", actor="model_tool")
    else:
        change = profile.propose_candidate(principal, proposal.category, proposal.value, session_id=session_id)
    ok = change.kind in {"saved", "updated", "unchanged", "candidate", "conflict"}
    return RuntimeExecutionResult(
        handled=True, ok=ok, status=change.kind, response_text=change.report,
        details={**change.as_dict(), "observation": _tool_observation(intent=intent, tool_surface="profile", ok=ok, status=change.kind)},
    )


def _email_send(arguments: dict[str, Any]) -> RuntimeExecutionResult:
    args = arguments or {}
    to = args.get("to") or ""
    subject = str(args.get("subject") or "")
    body = str(args.get("body") or "")
    account = str(args.get("account") or "")  # "" = the operator's default account (profile binding)
    allow_send = bool(args.get("allow_send", False))
    approve = bool(args.get("approve", False))
    to_display = to if isinstance(to, str) else ", ".join(str(r) for r in (to or []))
    if not (allow_send and approve):
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="user_action_required",
            response_text=(
                f"Ready to email {to_display or '(no recipient)'} - subject: {subject!r}. "
                "Re-call email.send with allow_send=true and approve=true to actually send."
            ),
            details={
                "action_required": {
                    "intent": "email.send",
                    "confirm_arguments": {
                        "to": to,
                        "subject": subject,
                        "body": body,
                        "account": account,
                        "allow_send": True,
                        "approve": True,
                    },
                },
                "observation": _tool_observation(
                    intent="email.send", tool_surface="email", ok=False,
                    status="user_action_required", to=str(to_display), subject=subject,
                ),
            },
        )
    from core.email_tools import send_email

    result = send_email(to=to, subject=subject, body=body, account=account)
    return RuntimeExecutionResult(
        handled=True,
        ok=result.ok,
        status=result.status,
        response_text=result.message,
        details={
            **result.details,
            "observation": _tool_observation(
                intent="email.send", tool_surface="email", ok=result.ok, status=result.status,
                to=str(to_display), subject=subject,
            ),
        },
    )


# What a model needs to ACT on email tool results rides in the observation the tool loop hands
# back: identifiers, the headers a reply binds to, a snippet, the body when opened, the exact
# draft under review and the send receipt. Without it the loop told the model only
# "1 message(s) matched" (measured 2026-09-14 on a served turn), so no model could open the
# right thread or ground a reply. Bounded here, and the observation block shares the prompt
# window fairly and clips rather than drops; whatever is cut says so.
_EMAIL_EVIDENCE_MESSAGE_CAP = 20
_EMAIL_EVIDENCE_FIELDS = ("provider_id", "message_id", "thread_id", "conversation_id", "from", "to",
                          "reply_to", "subject", "date", "snippet", "in_reply_to", "references")
_EMAIL_RECEIPT_EVIDENCE_FIELDS = ("outcome", "message_id", "to", "subject", "account", "acceptance", "provider",
                                  "verified_principal", "representation", "parent_resolved", "thread_id",
                                  "reconciliation_evidence")
_EMAIL_RECOVERY_EVIDENCE_FIELDS = ("status", "reason", "quarantine", "send_hold", "unresolved_markers",
                                   "message_ids_in_damaged_store")


def _clipped_evidence(value: Any, limit: int) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[:limit] + f" …[{len(text) - limit} more characters not shown]"


def _email_messages_evidence(messages: Any, *, body_limit: int = 0) -> tuple[list[dict[str, Any]], int]:
    rows = [message for message in list(messages or []) if isinstance(message, dict)]
    evidence: list[dict[str, Any]] = []
    for message in rows[:_EMAIL_EVIDENCE_MESSAGE_CAP]:
        entry = {key: _clipped_evidence(message.get(key), 300)
                 for key in _EMAIL_EVIDENCE_FIELDS if str(message.get(key) or "").strip()}
        if body_limit and str(message.get("body") or "").strip():
            entry["body"] = _clipped_evidence(message.get("body"), body_limit)
        evidence.append(entry)
    return evidence, max(0, len(rows) - _EMAIL_EVIDENCE_MESSAGE_CAP)


def _email_draft_evidence(draft: Any) -> dict[str, Any]:
    if not isinstance(draft, dict) or not draft.get("draft_id"):
        return {}
    return {
        "draft_id": str(draft.get("draft_id") or ""),
        "version": draft.get("version"),
        "status": str(draft.get("status") or ""),
        "account": str(draft.get("account_resolved") or "default"),
        "to": list(draft.get("to") or []),
        "subject": str(draft.get("subject") or ""),
        "body": _clipped_evidence(draft.get("body"), 4000),
        "in_reply_to": str(draft.get("in_reply_to") or ""),
        "references": list(draft.get("references") or []),
        "approved": bool(draft.get("approved")),
        "approval_stale": bool(draft.get("approval_stale")),
    }


def _email_fields_evidence(record: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(record, dict):
        return {}
    return {key: record[key] for key in fields if record.get(key) not in (None, "", [], {})}


def _email_read(arguments: dict[str, Any]) -> RuntimeExecutionResult:
    from core.email_tools import search_email

    args = arguments or {}
    try:
        limit = int(args.get("limit", 10))
    except Exception:
        limit = 10
    result = search_email(
        account=str(args.get("account") or ""),
        folder=str(args.get("folder") or "INBOX"),
        sender=str(args.get("sender") or args.get("from") or ""),
        subject=str(args.get("subject") or ""),
        since=str(args.get("since") or ""),
        before=str(args.get("before") or ""),
        unseen_only=bool(args.get("unseen_only", False)),
        seen_only=bool(args.get("seen_only", False)),
        limit=limit,
    )
    evidence, omitted = _email_messages_evidence(result.messages)
    return RuntimeExecutionResult(
        handled=True,
        ok=result.ok,
        status=result.status,
        response_text=result.message,
        details={
            "messages": result.messages,
            "observation": _tool_observation(
                intent="email.read", tool_surface="email", ok=result.ok, status=result.status,
                count=len(result.messages), messages=evidence, messages_omitted=omitted or None,
            ),
        },
    )


def _email_open(arguments: dict[str, Any]) -> RuntimeExecutionResult:
    from core.email_tools import open_message, open_thread

    args = arguments or {}
    message_id = str(args.get("message_id") or "").strip()
    thread = bool(args.get("thread", False))
    if not message_id:
        return RuntimeExecutionResult(
            handled=True, ok=False, status="missing_message_id",
            response_text="No message id to open; search or read first and name the message.",
            details={"observation": _tool_observation(
                intent="email.open", tool_surface="email", ok=False, status="missing_message_id")},
        )
    result = (
        open_thread(account=str(args.get("account") or ""), folder=str(args.get("folder") or "INBOX"),
                    message_id=message_id)
        if thread
        else open_message(account=str(args.get("account") or ""), folder=str(args.get("folder") or "INBOX"),
                          message_id=message_id)
    )
    evidence, omitted = _email_messages_evidence(result.messages, body_limit=1500 if thread else 4000)
    return RuntimeExecutionResult(
        handled=True,
        ok=result.ok,
        status=result.status,
        response_text=result.message,
        details={
            "messages": result.messages,
            "thread": bool(thread),
            "observation": _tool_observation(
                intent="email.open", tool_surface="email", ok=result.ok, status=result.status,
                count=len(result.messages), thread=bool(thread), messages=evidence,
                messages_omitted=omitted or None,
            ),
        },
    )


def _draft_review_text(draft: dict[str, Any]) -> str:
    """The user-visible review card: everything an approval decision depends on.

    The body shown here IS the canonical final body (signature already folded,
    Re: prefix already applied) — what the wire will carry if approved."""
    to = ", ".join(draft.get("to") or [])
    lines = [
        f"Draft {draft.get('draft_id')} (version {draft.get('version')}) on account "
        f"'{draft.get('account_resolved') or 'default'}':",
        f"To: {to}",
        f"Subject: {draft.get('subject')}",
        "",
        str(draft.get("body") or ""),
    ]
    if draft.get("in_reply_to"):
        lines.append(f"(threaded reply to {draft['in_reply_to']})")
    return "\n".join(lines)


def _email_session_scope(source_context: dict[str, Any] | None) -> tuple[str, str]:
    """(principal, session) for draft ownership, mirroring the profile tool's binding."""
    from core.operator_profile import principal_for_request

    context = source_context if isinstance(source_context, dict) else {}
    principal = str(context.get("principal") or principal_for_request(context) or "")
    session = str(context.get("runtime_session_id") or context.get("chat_id") or context.get("session_id") or "")
    return principal, session


def _email_draft_tool(
    intent: str, arguments: dict[str, Any], source_context: dict[str, Any] | None
) -> RuntimeExecutionResult:
    from core import email_drafts

    args = arguments or {}
    principal, session = _email_session_scope(source_context)
    draft_id = str(args.get("draft_id") or "").strip()

    if intent == "email.draft.save":
        # The recipient first (the Contacts law the wallet consumer already follows): an
        # address given directly stays that address; a saved contact resolves to exactly one
        # saved email endpoint, or the request asks. A request that NAMED A CONTACT and could
        # not pick one address is a QUESTION for the user (which Alex? no address saved), not a
        # failed tool run; a genuinely invalid recipient stays a typed refusal. Nothing is
        # drafted for a guessed address, and the draft that IS saved carries exact addresses.
        _to_entries = args.get("to")
        _to_entries = [_to_entries] if isinstance(_to_entries, str) else list(_to_entries or [])
        if _to_entries:
            from core.contacts.consumers import resolve_email_recipients

            _outcome = resolve_email_recipients([str(entry) for entry in _to_entries])
            if not _outcome.ok:
                _resolution = _outcome.resolution or {}
                _question = _outcome.status in {
                    "recipient_ambiguous", "recipient_not_found", "recipient_has_no_address",
                }
                return RuntimeExecutionResult(
                    handled=True, ok=_question, status=_outcome.status,
                    response_text=f"{_outcome.message} Nothing was drafted or sent.",
                    details={"recipient": _resolution, "observation": _tool_observation(
                        intent=intent, tool_surface="email", ok=_question, status=_outcome.status,
                        choices=list(_resolution.get("choices") or []) or None,
                        suggestions=list(_resolution.get("suggestions") or []) or None)},
                )
            _to_entries = [
                # An address given directly STAYS as given: a ``Name <address>`` entry keeps
                # its display name -- the wire form the reply should carry -- while the
                # resolution below has already validated the address inside it.
                entry if ("<" in entry and snapshot.get("value") in entry) else str(snapshot.get("value") or snapshot.get("address") or "")
                for entry, snapshot in zip(
                    [str(e) for e in _to_entries], _outcome.snapshots, strict=True
                )
            ]
        result = email_drafts.save_draft(
            to=_to_entries or args.get("to"), subject=str(args.get("subject") or ""), body=str(args.get("body") or ""),
            account=str(args.get("account") or ""), in_reply_to=str(args.get("in_reply_to") or ""),
            references=args.get("references") if isinstance(args.get("references"), list) else None,
            kind=str(args.get("kind") or "compose"), draft_id=draft_id,
            session_id=session, principal=principal or None,
        )
        text = (
            _draft_review_text(result.draft)
            + "\nReview this draft; say what to change, or approve it to send."
            if result.ok else result.message
        )
    elif intent == "email.draft.get":
        result = email_drafts.get_draft(
            draft_id, session_id=session, principal=principal or None, latest=not draft_id,
        )
        text = _draft_review_text(result.draft) if result.ok else result.message
        if result.ok:
            state = "approved" if result.draft.get("approved") else (
                "approval is stale (content changed since approval)" if result.draft.get("approval_stale")
                else str(result.draft.get("status") or "draft")
            )
            text += f"\nState: {state}."
    elif intent == "email.draft.approve":
        expected = args.get("expected_version")
        result = email_drafts.approve_draft(
            draft_id, session_id=session, principal=principal or None,
            expected_version=int(expected) if isinstance(expected, (int, float, str)) and str(expected).strip().isdigit() else None,
            latest=not draft_id,
        )
        text = (
            f"Approved draft {result.draft.get('draft_id')} (version {result.draft.get('version')}) for "
            f"{', '.join(result.draft.get('to') or [])}. Sending still needs the send call; nothing has "
            "gone out yet."
            if result.ok else result.message
        )
    elif intent == "email.draft.send":
        result = email_drafts.send_draft(
            draft_id, session_id=session, principal=principal or None, latest=not draft_id,
        )
        if result.status in {"not_approved", "needs_reapproval"}:
            return RuntimeExecutionResult(
                handled=True, ok=False, status="user_action_required",
                response_text=(
                    result.message + "\n\n" + _draft_review_text(result.draft or {})
                    if result.draft else result.message
                ),
                details={
                    "action_required": {
                        "intent": "email.draft.approve",
                        "confirm_arguments": {"draft_id": (result.draft or {}).get("draft_id") or draft_id},
                        "then": {"intent": "email.draft.send", "arguments": {"draft_id": (result.draft or {}).get("draft_id") or draft_id}},
                    },
                    "observation": _tool_observation(
                        intent="email.draft.send", tool_surface="email", ok=False,
                        status="user_action_required", draft_id=(result.draft or {}).get("draft_id") or draft_id,
                        draft=_email_draft_evidence(result.draft) or None,
                    ),
                },
            )
        text = result.message
    elif intent == "email.draft.reconcile":
        result = email_drafts.reconcile_draft(draft_id, session_id=session, principal=principal or None)
        text = result.message
    else:  # pragma: no cover - dispatch guards the intent set
        return RuntimeExecutionResult(
            handled=False, ok=False, status="unsupported",
            response_text=f"Unknown email draft intent {intent}.",
        )
    observation = _tool_observation(
        intent=intent, tool_surface="email", ok=result.ok, status=result.status,
        draft_id=(result.draft or {}).get("draft_id") or draft_id,
        draft=_email_draft_evidence(result.draft) or None,
        receipt=_email_fields_evidence(result.details.get("receipt"), _EMAIL_RECEIPT_EVIDENCE_FIELDS) or None,
        store_recovery=_email_fields_evidence(result.details.get("store_recovery"),
                                              _EMAIL_RECOVERY_EVIDENCE_FIELDS) or None,
    )
    return RuntimeExecutionResult(
        handled=True,
        ok=result.ok,
        status=result.status,
        response_text=text if result.ok or intent == "email.draft.get" else result.message,
        details={
            "draft": result.draft,
            **({"receipt": result.details.get("receipt")} if result.details.get("receipt") else {}),
            # A store recovery record (quarantine, markers, named Message-IDs, hold) travels with
            # the result so the turn can show it as structured state, not only as prose.
            **({"store_recovery": result.details.get("store_recovery")}
               if result.details.get("store_recovery") else {}),
            "observation": observation,
        },
    )


def _email_reply(arguments: dict[str, Any]) -> RuntimeExecutionResult:
    args = arguments or {}
    to = args.get("to") or ""
    subject = str(args.get("subject") or "")
    body = str(args.get("body") or "")
    account = str(args.get("account") or "")  # "" = the operator's default account (profile binding)
    in_reply_to = str(args.get("in_reply_to") or "")
    references = args.get("references") if isinstance(args.get("references"), list) else None
    allow_send = bool(args.get("allow_send", False))
    approve = bool(args.get("approve", False))
    to_display = to if isinstance(to, str) else ", ".join(str(r) for r in (to or []))
    if not (allow_send and approve):
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="user_action_required",
            response_text=(
                f"Ready to reply to {to_display or '(no recipient)'} (subject: {subject!r}). "
                "Re-call email.reply with allow_send=true and approve=true to send."
            ),
            details={
                "action_required": {
                    "intent": "email.reply",
                    "confirm_arguments": {
                        "to": to,
                        "subject": subject,
                        "body": body,
                        "account": account,
                        "in_reply_to": in_reply_to,
                        "references": references or [],
                        "allow_send": True,
                        "approve": True,
                    },
                },
                "observation": _tool_observation(
                    intent="email.reply", tool_surface="email", ok=False,
                    status="user_action_required", to=str(to_display), subject=subject,
                ),
            },
        )
    from core.email_tools import reply_email

    result = reply_email(to=to, subject=subject, body=body, account=account, in_reply_to=in_reply_to,
                         references=references)
    return RuntimeExecutionResult(
        handled=True,
        ok=result.ok,
        status=result.status,
        response_text=result.message,
        details={
            **result.details,
            "observation": _tool_observation(
                intent="email.reply", tool_surface="email", ok=result.ok, status=result.status,
                to=str(to_display), subject=subject,
            ),
        },
    )


def _image_generate(arguments: dict[str, Any]) -> RuntimeExecutionResult:
    from core.media_tools import generate_image

    args = arguments or {}
    result = generate_image(prompt=str(args.get("prompt") or ""), account=str(args.get("account") or "default"))
    return RuntimeExecutionResult(
        handled=True,
        ok=result.ok,
        status=result.status,
        response_text=result.message,
        details={
            "image_ref": result.image_ref,
            "observation": _tool_observation(
                intent="image.generate", tool_surface="media", ok=result.ok, status=result.status,
                image_ref=result.image_ref,
            ),
        },
    )


def _video_generate(arguments: dict[str, Any]) -> RuntimeExecutionResult:
    from core.media_tools import generate_video

    args = arguments or {}
    result = generate_video(prompt=str(args.get("prompt") or ""), account=str(args.get("account") or "default"))
    return RuntimeExecutionResult(
        handled=True,
        ok=result.ok,
        status=result.status,
        response_text=result.message,
        details={
            "video_ref": result.ref,
            "provider": result.provider,
            "observation": _tool_observation(
                intent="video.generate", tool_surface="media", ok=result.ok, status=result.status,
                video_ref=result.ref, provider=result.provider,
            ),
        },
    )


def _contacts_tool(intent: str, arguments: dict[str, Any], source_context: dict[str, Any] | None) -> RuntimeExecutionResult:
    """PA Contacts (core.contacts.tools): search, resolve, save, update, delete. Nothing here sends, messages or pays."""
    from core.contacts import tools as contacts_tools

    outcome = contacts_tools.run(intent, arguments, source_context)
    ok, status = bool(outcome["ok"]), str(outcome["status"])
    return RuntimeExecutionResult(
        handled=True, ok=ok, status=status, response_text=str(outcome["response_text"]),
        details={**outcome["details"], "observation": _tool_observation(intent=intent, tool_surface="contacts", ok=ok, status=status, **outcome["observation"])},
    )


def _wallet_model_tool(intent: str, arguments: dict[str, Any], source_context: dict[str, Any] | None) -> RuntimeExecutionResult:
    """The model-facing wallet surface: status, propose, simulate, payment_status.

    Read and propose only. There is deliberately no import here of anything that approves,
    signs, raises a limit or broadcasts; those live behind the owner-local API. Wording keeps
    "nothing was signed / sent" explicit so the final honesty seal does not read a truthful
    report as a false completion claim.
    """
    from core.wallet import config as wallet_config
    from core.wallet import custody, proposals, redaction
    from core.wallet.errors import WalletFault
    from core.wallet.status import wallet_status

    args = arguments if isinstance(arguments, dict) else {}
    context = source_context if isinstance(source_context, dict) else None

    def refusal(exc: WalletFault) -> RuntimeExecutionResult:
        return RuntimeExecutionResult(
            handled=True, ok=False, status=exc.code,
            response_text=f"{exc.user_message or exc.code} Nothing was signed and nothing was sent.",
            details={"fault": exc.to_dict(), "observation": _tool_observation(intent=intent, tool_surface="wallet", ok=False, status=exc.code, fault_code=exc.code)},
        )

    try:
        if not wallet_config.wallet_enabled():
            custody.require_enabled(source_context=context)
        if intent == "wallet.status":
            status = wallet_status()
            lines = [
                f"Wallet {status.get('label') or '(unnamed)'} on {status.get('network')}: custody={status.get('custody_mode')}, public key {status.get('public_key') or '(none)'}.",
                f"Pending approvals: {status.get('pending_approvals', 0)}. Mainnet enabled: {status.get('mainnet_enabled')}. Limits (SOL minor units): {status.get('limits') or {}}.",
            ]
            last = status.get("last_receipt") or {}
            if last:
                lines.append(f"Last receipt: proposal {last.get('proposal_id')} state={last.get('state')} tx_signature={redaction.publish_identifier(last.get('tx_signature')) or '-'}.")
            return RuntimeExecutionResult(handled=True, ok=True, status="ok", response_text="\n".join(lines), details={"status": status, "observation": _tool_observation(intent=intent, tool_surface="wallet", ok=True, status="ok", pending_approvals=int(status.get("pending_approvals") or 0))})
        if intent == "wallet.propose":
            from core.contacts.consumers import resolve_wallet_recipient
            from core.contacts.resolver import describe_snapshot
            from core.wallet import environment, transfers

            hints = {key: args.get(key) for key in ("amount", "chain", "network", "wallet_id") if args.get(key) not in (None, "")}
            pilot_path = bool(hints) or custody.default_wallet() is None or custody.seal_policy((custody.default_wallet() or custody.WalletProfile(wallet_id="", mode="", network="", public_key="", label="", created_at="")).wallet_id) == custody.PILOT_SEAL_POLICY
            legacy_profile = None if pilot_path else custody.default_wallet()
            # The recipient first (PA Contacts): an address given directly stays that address; a saved contact resolves to
            # exactly one wallet address saved for a fitting network of the active environment, or the request asks.
            # Nothing is proposed for a guessed payee, and a saved address is only ever used on the network it was saved for.
            requested_chain = str(args.get("chain") or "").strip().lower()
            recipient_outcome = resolve_wallet_recipient(
                str(args.get("destination") or ""),
                network=str(args.get("network") or "") or (legacy_profile.network if legacy_profile is not None else ""),
                chain=transfers._CHAIN_HINTS.get(requested_chain, requested_chain),
                asset=str(args.get("asset") or ("SOL" if legacy_profile is not None else "")),
                environment=environment.active_environment().environment,
            )
            if not recipient_outcome.ok:
                resolution = recipient_outcome.resolution or {}
                # A request that NAMED A CONTACT and could not pick one recipient is a QUESTION for the user
                # (which Alex? nothing saved for that name), not a failed tool run: it is served as a grounded
                # result so the turn's narration delivers it. A genuinely invalid request (an explicit address
                # that is not an address, a malformed payload) stays a typed refusal exactly as before.
                question = recipient_outcome.status in {
                    "recipient_ambiguous", "recipient_not_found", "recipient_has_no_address",
                }
                return RuntimeExecutionResult(
                    handled=True, ok=question, status=recipient_outcome.status,
                    response_text=f"{recipient_outcome.message} Nothing was proposed, signed or sent.",
                    details={"recipient": resolution, "observation": _tool_observation(
                        intent=intent, tool_surface="wallet", ok=question, status=recipient_outcome.status,
                        choices=list(resolution.get("choices") or []) or None, suggestions=list(resolution.get("suggestions") or []) or None)},
                )
            recipient = recipient_outcome.snapshots[0]
            pinned_network = str(recipient.get("chain_network") or "") if recipient.get("resolution") == "contact" else ""
            if pilot_path:
                # the caller path: one account on one row in the active environment, the amount parsed exactly
                resolved = transfers.resolve_request(
                    destination=str(recipient["value"]), asset=str(args.get("asset") or ""), amount=args.get("amount"), amount_minor=args.get("amount_minor"),
                    chain=str(args.get("chain") or ""), network=str(args.get("network") or "") or pinned_network, wallet_id=str(args.get("wallet_id") or ""), source_context=context,
                )
                proposal = proposals.propose_transaction(
                    wallet_id=resolved["wallet_id"], destination=resolved["destination"], amount_minor=int(resolved["amount_minor"]), asset=resolved["asset"],
                    origin=proposals.ORIGIN_MODEL, memo=str(args.get("memo") or ""), idempotency_key=str(args.get("idempotency_key") or ""), source_context=context, network=resolved["network"],
                    recipient=recipient,
                )
            else:
                profile = legacy_profile
                proposal = proposals.propose_transaction(
                    wallet_id=profile.wallet_id, destination=str(recipient["value"]), amount_minor=int(args.get("amount_minor") or 0),
                    asset=str(args.get("asset") or "SOL"), origin=proposals.ORIGIN_MODEL, memo=str(args.get("memo") or ""), idempotency_key=str(args.get("idempotency_key") or ""), source_context=context,
                    network=pinned_network, recipient=recipient,
                )
            from core.wallet import lifecycle

            prepared = lifecycle.default_lifecycle(source_context=context).prepare(proposal.proposal_id)
            who = describe_snapshot(recipient) or prepared.destination
            text = (
                f"Proposal {prepared.proposal_id}: {prepared.amount_minor} {prepared.asset} (minor units) to {who} on {prepared.network}. State: {prepared.state}.\n"
                + (f"Check before approving: {recipient['warning']}\n" if recipient.get("warning") else "")
                + "Nothing has been signed. Approve it in your wallet with your PIN, biometric or external wallet; I cannot approve, sign or broadcast."
            )
            return RuntimeExecutionResult(handled=True, ok=True, status=prepared.state, response_text=text, details={"proposal": prepared.to_dict(), "observation": _tool_observation(intent=intent, tool_surface="wallet", ok=True, status=prepared.state, proposal_id=prepared.proposal_id, amount_minor=prepared.amount_minor, asset=prepared.asset)})
        if intent == "x402.propose":
            # proposal-only: park a capped x402 offer (v2 first, v1 compatibility where safe)
            # for the owner. This lane NEVER approves, signs, broadcasts or retries a paid
            # resource; those are owner-local lifecycle doors this handler cannot reach.
            from core.wallet import x402 as wallet_x402
            from core.wallet import x402_v2

            wallet_id = (custody.default_wallet() or custody.WalletProfile(wallet_id="", mode="", network="", public_key="", label="", created_at="")).wallet_id
            if not wallet_id:
                raise WalletFault("wallet_not_found", context={"reason": "no_wallet_registered"}, message="No wallet is set up yet, so nothing was proposed.")
            resource_url = str(args.get("resource_url") or "").strip()
            if resource_url:
                outcome = wallet_x402.fetch_paid_resource(resource_url, wallet_id=wallet_id, source_context=context)
                if outcome.status == wallet_x402.OUTCOME_PAYMENT_REQUIRED and outcome.proposal_id:
                    text = f"x402 offer parked as proposal {outcome.proposal_id} (state pending_approval). Nothing has been signed; approve it in your wallet."
                    return RuntimeExecutionResult(handled=True, ok=True, status="pending_approval", response_text=text, details={"proposal_id": outcome.proposal_id, "observation": _tool_observation(intent=intent, tool_surface="wallet", ok=True, status="pending_approval", proposal_id=outcome.proposal_id)})
                return RuntimeExecutionResult(handled=True, ok=True, status=outcome.status, response_text=f"The resource answered {outcome.http_status} (no capped proposal was parked: status={outcome.status}).", details={"outcome": outcome.to_dict(), "observation": _tool_observation(intent=intent, tool_surface="wallet", ok=True, status=outcome.status)})
            status_code = int(args.get("status") or 0)
            headers_arg = args.get("headers") if isinstance(args.get("headers"), dict) else {}
            body_arg = args.get("body")
            v2_offer = x402_v2.parse_payment_required(headers_arg, body_arg, status=status_code)
            if v2_offer is not None:
                entry, reason = x402_v2.select_offer(v2_offer, wallet_id=wallet_id, source_context=context)
                if entry is None:
                    raise WalletFault("x402_scheme_unavailable", context={"reason": reason})
                proposal = x402_v2.propose_from_v2_offer(v2_offer, entry, wallet_id=wallet_id, source_context=context)
            else:
                request = wallet_x402.detect_x402(status_code, headers_arg, body_arg)
                if request is None:
                    raise WalletFault("wallet_not_found", context={"reason": "x402_not_detected"}, message="That response carried no x402 offer, so nothing was proposed.")
                proposal = wallet_x402.propose_from_x402(request, wallet_id=wallet_id, source_context=context)
            text = f"x402 offer parked as proposal {proposal.proposal_id} on {proposal.network}. Nothing has been signed; approve it in your wallet; I cannot approve, sign or broadcast."
            return RuntimeExecutionResult(handled=True, ok=True, status=proposal.state, response_text=text, details={"proposal": proposal.to_dict(), "observation": _tool_observation(intent=intent, tool_surface="wallet", ok=True, status=proposal.state, proposal_id=proposal.proposal_id)})
        proposal_id = str(args.get("proposal_id") or "").strip()
        proposal = proposals.get_proposal(proposal_id)
        if proposal is None:
            raise WalletFault("wallet_not_found", context={"proposal_id": proposal_id}, message="That proposal was not found.")
        if intent == "wallet.simulate":
            simulation = dict(proposal.simulation or {})
            if not simulation:
                from core.wallet import lifecycle

                prepared = lifecycle.default_lifecycle(source_context=context).prepare(proposal.proposal_id)
                simulation = dict(prepared.simulation or {})
            text = f"Simulation for {proposal.proposal_id} {'succeeded' if simulation.get('ok') else 'did not pass'} (fee {simulation.get('fee_minor', 0)} minor units). Nothing was signed and nothing was sent."
            return RuntimeExecutionResult(handled=True, ok=True, status="simulate_only", response_text=text, details={"simulation": simulation, "observation": _tool_observation(intent=intent, tool_surface="wallet", ok=True, status="simulate_only", proposal_id=proposal.proposal_id)})
        from core.wallet import transfers

        transfer = transfers.latest_receipt(proposal.proposal_id)
        if transfer is not None:
            # a Crypto Pilot transfer: the label and detail come from the transfer record, never from a bare proposal state
            tx_id = redaction.publish_identifier(transfer.get("tx_id")) or "-"
            text = (
                f"{proposal.proposal_id}: {transfer['state_label']} ({transfer['state']}). {transfer['detail']} "
                f"{transfer['amount_human']} {transfer['display_symbol']} to {transfer['to_address']} on {transfer['display_name']}; transaction {tx_id}."
            )
            if transfer.get("explorer_url"):
                text += f" {transfer['explorer_link_text']}: {transfer['explorer_url']}"
            return RuntimeExecutionResult(handled=True, ok=True, status="ok", response_text=text, details={"proposal": proposal.to_dict(), "transfer": transfer, "observation": _tool_observation(intent=intent, tool_surface="wallet", ok=True, status="ok", proposal_id=proposal.proposal_id, state=transfer["state"])})
        text = f"{proposal.proposal_id}: state={proposal.state}, tx_signature={redaction.publish_identifier(proposal.tx_signature) or '-'}, network={proposal.network}, amount={proposal.amount_minor} {proposal.asset} to {proposal.destination}."
        if proposal.state == proposals.STATE_PENDING_APPROVAL:
            text += " Awaiting the owner's approval; nothing has been signed."
        elif proposal.state == proposals.STATE_CONFIRMED:
            text += " Confirmed on the network."
        return RuntimeExecutionResult(handled=True, ok=True, status="ok", response_text=text, details={"proposal": proposal.to_dict(), "observation": _tool_observation(intent=intent, tool_surface="wallet", ok=True, status="ok", proposal_id=proposal.proposal_id, state=proposal.state)})
    except WalletFault as exc:
        return refusal(exc)


def _wallet_spend(arguments: dict[str, Any]) -> RuntimeExecutionResult:
    """RETIRED `wallet.spend`: a typed, receipt-backed refusal that points at the canonical proposal door.

    The model may PROPOSE (wallet.propose); approval, signing and broadcast are operator-only and happen in
    the wallet, never in a tool argument. `allow_spend`/`approve` flags are ignored by construction.
    """
    from core.wallet.authority import refuse_legacy

    args = arguments or {}
    fault = refuse_legacy("tool.wallet.spend")
    return RuntimeExecutionResult(
        handled=True,
        ok=False,
        status=fault.code,
        response_text=f"{fault.user_message} Use wallet.propose to propose this payment; the owner approves it in the wallet.",
        details={
            "fault": fault.to_dict(),
            "observation": _tool_observation(intent="wallet.spend", tool_surface="wallet", ok=False, status=fault.code, fault_id=fault.fault_id, to=str(args.get("to") or ""), asset=str(args.get("asset") or "SOL")),
        },
    )


def _x_trending(arguments: dict[str, Any]) -> RuntimeExecutionResult:
    from core.x_tools import fetch_trending

    args = arguments or {}
    result = fetch_trending(
        account=str(args.get("account") or "default"),
        woeid=int(args.get("woeid") or 1),
        limit=int(args.get("limit") or 20),
    )
    return RuntimeExecutionResult(
        handled=True,
        ok=result.ok,
        status=result.status,
        response_text=result.message,
        details={
            "trends": result.trends,
            "observation": _tool_observation(
                intent="x.trending", tool_surface="x", ok=result.ok, status=result.status,
                trend_count=len(result.trends),
            ),
        },
    )


def _demo_plan(arguments: dict[str, Any], *, source_context: dict[str, Any] | None = None) -> RuntimeExecutionResult:
    from core.demo_planner import plan_image_set, plan_video_demo
    from core.demo_source import brief_from_url

    args = arguments or {}
    url = str(args.get("url") or "").strip()
    presenter = str(args.get("presenter") or "person").strip() or "person"
    try:
        seconds = int(args.get("seconds") or 30)
    except (TypeError, ValueError):
        seconds = 30
    if not url:
        return RuntimeExecutionResult(
            handled=True, ok=False, status="error",
            response_text="Give me a GitHub repo or docs URL to plan a demo from (e.g. github.com/owner/repo).",
            details={"observation": _tool_observation(
                intent="demo.plan", tool_surface="demo", ok=False, status="error")},
        )
    if not _remote_fetch_allowed(source_context):
        return RuntimeExecutionResult(
            handled=True, ok=False, status="disabled_by_policy",
            response_text="Remote fetch is disabled for this turn, so demo.plan cannot read the URL.",
            details={"observation": _tool_observation(
                intent="demo.plan", tool_surface="demo", ok=False, status="disabled_by_policy", url=url)},
        )
    fmt = str(args.get("format") or "slideshow").strip().lower()
    brief = brief_from_url(url, presenter=presenter, total_seconds=seconds)
    if brief is None:
        return RuntimeExecutionResult(
            handled=True, ok=False, status="error",
            response_text=f"Couldn't read a product page from {url}. Check it is a public GitHub repo or docs URL.",
            details={"observation": _tool_observation(
                intent="demo.plan", tool_surface="demo", ok=False, status="error", url=url)},
        )
    if fmt in ("video_story", "video-story", "story", "video"):
        # Continuous VIDEO story (scene + on-camera dialogue), not a subtitled shot list + image set.
        # The tool returns the writing pack; the agent's model completes it into the finished script.
        # A saved creative set (``set``) locks the host + world across a series of product videos.
        from core.video_story import build_video_story_pack
        set_name = str(args.get("set") or "").strip()
        pack = build_video_story_pack(brief, presenter=presenter, total_seconds=seconds, set_name=set_name)
        return RuntimeExecutionResult(
            handled=True, ok=True, status="ok",
            response_text=pack.instruction,
            details={
                "video_story_pack": pack.to_dict(),
                "product": pack.product,
                "observation": _tool_observation(
                    intent="demo.plan", tool_surface="demo", ok=True, status="ok",
                    url=url, format="video_story", total_seconds=pack.total_seconds,
                    feature_count=len(pack.features)),
            },
        )
    plan = plan_video_demo(brief)
    unapplied: list[str] = []
    edits = args.get("edits")
    if isinstance(edits, list) and edits:
        from core.demo_plan_edit import apply_edits
        plan, unapplied = apply_edits(plan, edits)
    images = plan_image_set(plan.brief)
    return RuntimeExecutionResult(
        handled=True, ok=True, status="ok",
        response_text=plan.to_markdown(),
        details={
            "plan": plan.to_dict(),
            "images": [image.to_dict() for image in images],
            "product": plan.brief.product_name,
            "unapplied_edits": unapplied,
            "observation": _tool_observation(
                intent="demo.plan", tool_surface="demo", ok=True, status="ok",
                url=url, shot_count=len(plan.shots), total_seconds=plan.total_seconds),
        },
    )


def _set_save(arguments: dict[str, Any]) -> RuntimeExecutionResult:
    from core.creative_set import Character, CreativeSet, save_set, set_from_description

    args = arguments or {}
    name = str(args.get("name") or "").strip()
    description = str(args.get("description") or "").strip()

    # A natural-language paragraph seeds a first draft; explicit structured args override it field-by-field.
    parsed = set_from_description(description, name=name) if description else None
    if parsed is not None and not name and parsed.name != "Untitled set":
        name = parsed.name
    if not name:
        return RuntimeExecutionResult(
            handled=True, ok=False, status="error", response_text="A set needs a name.",
            details={"observation": _tool_observation(
                intent="set.save", tool_surface="set", ok=False, status="error")},
        )

    def field(key: str) -> str:
        explicit = str(args.get(key) or "").strip()
        if explicit:
            return explicit
        return getattr(parsed, key) if parsed is not None else ""

    characters: list = []
    raw = args.get("characters")
    if isinstance(raw, list) and raw:
        characters = [Character.from_dict(c) for c in raw if isinstance(c, dict)]
    else:
        base = parsed.characters[0] if (parsed is not None and parsed.characters) else None
        # Explicit character-level args override the parsed character field-by-field.
        if base is not None or any(
            args.get(k) for k in ("character", "appearance", "wardrobe", "voice", "personality")
        ):
            characters = [Character(
                name=str(args.get("character") or "").strip() or (base.name if base else "") or "Character",
                appearance=str(args.get("appearance") or "").strip() or (base.appearance if base else ""),
                wardrobe=str(args.get("wardrobe") or "").strip() or (base.wardrobe if base else ""),
                voice=str(args.get("voice") or "").strip() or (base.voice if base else ""),
                personality=str(args.get("personality") or "").strip() or (base.personality if base else ""),
            )]
    cset = CreativeSet(
        name=name, characters=tuple(characters),
        setting=field("setting"), background=field("background"),
        style=field("style"), palette=field("palette"),
        aspect=(str(args.get("aspect") or "").strip() or (parsed.aspect if parsed else "") or "16:9"),
        mood=field("mood"), story=field("story"), negative=field("negative"),
    )
    save_set(cset)
    return RuntimeExecutionResult(
        handled=True, ok=True, status="ok",
        response_text=f"Saved set '{name}' with {len(characters)} character(s). Use it with set.use.",
        details={"set": cset.to_dict(), "observation": _tool_observation(
            intent="set.save", tool_surface="set", ok=True, status="ok",
            set_name=name, characters=len(characters))},
    )


def _set_use(arguments: dict[str, Any]) -> RuntimeExecutionResult:
    from core.creative_set import compose_scene_prompt, get_set, list_set_names

    args = arguments or {}
    name = str(args.get("name") or args.get("set") or "").strip()
    script = str(args.get("script") or "").strip()
    medium = str(args.get("medium") or "video").strip() or "video"
    camera = str(args.get("camera") or "").strip()
    if not name:
        return RuntimeExecutionResult(
            handled=True, ok=False, status="error",
            response_text=f"Which set? Saved sets: {', '.join(list_set_names()) or '(none yet)'}.",
            details={"observation": _tool_observation(
                intent="set.use", tool_surface="set", ok=False, status="error")},
        )
    cset = get_set(name)
    if cset is None:
        return RuntimeExecutionResult(
            handled=True, ok=False, status="error",
            response_text=f"No set named '{name}'. Saved sets: {', '.join(list_set_names()) or '(none yet)'}.",
            details={"observation": _tool_observation(
                intent="set.use", tool_surface="set", ok=False, status="error", set_name=name)},
        )
    prompt = compose_scene_prompt(cset, script, medium=medium, camera=camera)
    return RuntimeExecutionResult(
        handled=True, ok=True, status="ok", response_text=prompt,
        details={"prompt": prompt, "set": cset.name, "medium": medium,
                 "observation": _tool_observation(
                     intent="set.use", tool_surface="set", ok=True, status="ok",
                     set_name=cset.name, medium=medium)},
    )


# The alias table and binder used to be defined here a SECOND time, byte-for-byte identical to
# `core.tool_argument_aliases`. That module's docstring documents why the two must never drift:
# `core/mode_permission_policy.py`'s `decide_tool_call()` binds aliases with its own copy BEFORE
# deciding read/write/approval, and this dispatcher binds them AGAIN before executing — if the two
# tables disagree, the gate classifies a different call than the one that runs (confirmed live: a
# model calling `file_path=secrets.txt` was gated as `create_files` but executed as an overwrite).
# Importing the one table both call sites read closes that drift permanently instead of just
# re-syncing it once more.
def _memoized_runtime_result(
    intent: str,
    arguments: dict[str, Any],
    produce: Any,
    *,
    scope: tuple[str, ...] = (),
) -> RuntimeExecutionResult:
    """Run a read-only runtime tool through the purity-gated memo (NIA-014).

    The memo is JSON-strict by law; RuntimeExecutionResult is a flat dataclass, so
    it is stored as its true dict shape and reconstructed typed on hit — a hit is
    the same type as a miss, never a lying dict. Unregistered tools always run
    fresh (fail-closed gate); the registrations above are the whole opt-in surface.

    C10: the hit/miss verdict the memo returns is MEASURED here — the only
    component that mechanically knows it — and stamped into the result's
    details and observation before it reaches any caller or receipt. A hit
    without provenance is not a hit. The stamp is attached AFTER the memo
    answers (on a hit the stored observation is served back verbatim, so the
    truth can only be attached truthfully out here); it never claims anything
    about provider prompt caches, which nothing in this runtime measures.
    """
    from dataclasses import asdict

    def _produce_json() -> Any:
        result = produce()
        return asdict(result) if isinstance(result, RuntimeExecutionResult) else result

    payload, cache_hit = memoize(intent, arguments, _produce_json, scope=scope)
    if isinstance(payload, dict) and "handled" in payload and "status" in payload:
        result = RuntimeExecutionResult(**payload)
    else:
        return payload
    cache_truth = {
        "cache_hit": bool(cache_hit),
        "measured": True,
        "memo_tool": intent,
        "policy_epoch": memo_policy_epoch(),
    }
    details = dict(result.details or {})
    details["cache"] = cache_truth
    observation = details.get("observation")
    if isinstance(observation, dict):
        merged_observation = dict(observation)
        merged_observation["cache"] = dict(cache_truth)
        details["observation"] = merged_observation
    result.details = details
    return result


def _bind_known_argument_aliases(
    arguments: dict[str, Any],
    *,
    contract: Any,
) -> dict[str, Any]:
    """Rename a model's near-miss argument names onto the fields this contract actually declares."""
    return bind_known_argument_aliases(arguments, input_schema=getattr(contract, "input_schema", None))


def _file_boundary_fault(
    code: str,
    intent: str,
    source_context: dict[str, Any] | None,
    *,
    status: str,
    reason: str,
) -> None:
    """File the typed fault this tool boundary just decided. Best-effort, never raises.

    The boundary owns the mapping: a denial decided here becomes its catalog record here,
    so no wrapper downstream can re-classify it. A security-relevant code (permission
    denied) also opens its observation on the security-event plane, at the same instant.
    """
    try:
        from core.faults.recorder import identity_from_context, record_fault
        from core.faults.records import FaultRecord

        turn_key, session_id = identity_from_context(source_context)
        record_fault(
            FaultRecord.for_code(
                code,
                authority="core.runtime_execution_tools",
                turn_key=turn_key,
                session_id=session_id,
                dedupe=intent,
                context={
                    "tool_name": intent,
                    "status": status,
                    "reason": reason,
                    "decision": "boundary_refusal",
                },
            )
        )
    except Exception:
        # A fault-plane outage must never turn a handled refusal into an unhandled one.
        pass


def _dispatch_runtime_tool(
    intent: str,
    arguments: dict[str, Any],
    *,
    source_context: dict[str, Any] | None = None,
    trusted_local_only: bool = False,
) -> RuntimeExecutionResult | None:
    contract = runtime_tool_contract_map().get(intent)
    if contract is None:
        return None
    if intent == "browser.render":
        # Typed unavailable — the contract is an external-lane stub (no browser automation
        # backend is wired on this runtime), and the door must SAY so instead of declining
        # with None, which a skill-guided turn would read as success.
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="browser_backend_unavailable",
            response_text=(
                "browser.render has no automation backend wired on this runtime — the "
                "result is typed unavailable, not a silent pass. Use web.fetch for "
                "content checks."
            ),
            details={"intent": intent, "unavailable_reason": "no_browser_backend"},
        )
    # A DIRECT runtime-tool call — no turn ledger active — is legitimate library use,
    # and this function is its owning entry point: it opens the R2b1 sanctioned named
    # scope itself so the effect doors below have a ledger to attribute to. A call
    # already inside a turn (or another scope) defers to it; one recursion deep.
    from core.effect_gateway import current_effect_ledger, named_background_effect_scope

    if current_effect_ledger() is None:
        with named_background_effect_scope(
            "runtime_tool.direct_call", source_context=dict(source_context or {})
        ):
            return _dispatch_runtime_tool(
                intent, arguments, source_context=source_context, trusted_local_only=trusted_local_only
            )
    # A contract DESCRIBES a tool; it does not claim the right to execute it. `operator.*`,
    # `hive.*`, `web.search/research`, `pay.x402`, `sell.quote` and `respond.direct` are dispatched
    # by their own lanes further down `tool_intent_executor.execute_tool_intent`, and returning a
    # result here would short-circuit that dispatch — which is exactly what broke the operator gate
    # the last time these intents were contracted. Declining lets the caller fall through to the
    # lane that owns the work, while the contract still supplies the side-effect class and approval
    # requirement the permission layer reads BEFORE any of this runs.
    if str(getattr(contract, "handler", "runtime")) != "runtime":
        return None
    # Strip any underscore-prefixed keys from the model/caller-supplied arguments before dispatch:
    # these are INTERNAL trust flags (e.g. _trusted_local_only, which self-selects the weaker
    # sandbox isolation for the test runner) that only trusted server code may set. Without this a
    # prompt-injected tool call could forge _trusted_local_only:true and disable network isolation.
    # `arguments` is a data channel the model can populate, so the flag may only arrive through the
    # `trusted_local_only` keyword, which is reachable from Python call sites in server code alone.
    if any(str(k).startswith("_") for k in arguments):
        arguments = {k: v for k, v in arguments.items() if not str(k).startswith("_")}
    arguments = _bind_known_argument_aliases(arguments, contract=contract)
    # The vool-browser lane (C06): one authority for isolated disposable-browser
    # journeys. Dispatched through its own api so receipts, budgets and the
    # per-origin permission store stay in ONE module.
    if intent.startswith("vool-browser."):
        from core.vool_browser.api import handle_intent as _browser_handle

        lane = _browser_handle(intent, arguments)
        return RuntimeExecutionResult(
            handled=True,
            ok=lane.ok,
            status=lane.status,
            response_text=lane.response_text,
            details={"intent": intent, "observation": lane.observation},
        )
    unknown_arguments = sorted(str(key) for key in arguments if str(key) not in contract.input_schema)
    if unknown_arguments:
        allowed_arguments = sorted(str(key) for key in contract.input_schema)
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="invalid_arguments",
            response_text=(
                f"`{intent}` received unsupported argument(s): {', '.join(unknown_arguments)}. "
                f"Allowed arguments: {', '.join(allowed_arguments) or 'none'}."
            ),
            details={
                "unknown_arguments": unknown_arguments,
                "allowed_arguments": allowed_arguments,
                "observation": _tool_observation(
                    intent=intent,
                    tool_surface=contract.tool_surface,
                    ok=False,
                    status="invalid_arguments",
                    unknown_arguments=unknown_arguments,
                    allowed_arguments=allowed_arguments,
                ),
            },
        )
    # Audit permissions are enforced at the tool boundary, not only by the stepped driver's happy
    # path. This makes an explicitly read-only audit unable to mutate state even if a future caller
    # forgets the orchestration-level check or a model emits a tool call directly.
    from core.agent_runtime.audit_policy import audit_tool_permission_denial

    audit_denial = audit_tool_permission_denial(
        intent,
        str(getattr(contract, "side_effect_class", "") or ""),
        source_context,
        arguments,
    )
    if audit_denial:
        _file_boundary_fault(
            "permission_denied",
            intent,
            source_context,
            status="permission_denied",
            reason=audit_denial,
        )
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="permission_denied",
            response_text=f"`{intent}` was not executed: {audit_denial}.",
            details={
                "reason": audit_denial,
                "observation": _tool_observation(
                    intent=intent,
                    tool_surface=contract.tool_surface,
                    ok=False,
                    status="permission_denied",
                    reason=audit_denial,
                ),
            },
        )
    if trusted_local_only:
        arguments = {**arguments, "_trusted_local_only": True}
    if not contract.supported:
        _file_boundary_fault(
            "tool_unavailable",
            intent,
            source_context,
            status="disabled",
            reason=contract.unsupported_reason,
        )
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="disabled",
            response_text=contract.unsupported_reason,
            details={
                "observation": _tool_observation(
                    intent=intent,
                    tool_surface=contract.tool_surface,
                    ok=False,
                    status="disabled",
                    reason=contract.unsupported_reason,
                ),
            },
        )
    workspace_root = _workspace_root(source_context)
    # Past every guard above, this IS the dispatch, so the turn can now say which tool it ran rather
    # than which lane claimed the message. A workspace read reports `workspace_runtime_fast_path` as
    # its route reason -- the name of the front-door family -- while the operation the user asked
    # for, and the one Activity already names, is `workspace.read_file`. Recorded before the call so
    # a tool that raises is still attributed; the outcome is carried by the result, not by presence.
    from core.turn_model_call_ledger import record_tool_execution

    record_tool_execution(source_context, intent)


    def _covered(fn):
        # Blackbox coverage for every LOCAL-mutating dispatch below that is not already wrapped
        # by the v1 flight recorder (workspace.* file intents). No-ops for read-only intents.
        return with_mutation_coverage(
            intent, arguments, source_context=source_context, workspace_root=workspace_root, handler=fn
        )

    if intent.startswith("code.task."):
        # The coding assistant's control plane: one runtime, composed from the authorities above
        # this line, that executes every proposed step back through THIS door.
        from core.code_assistant.task_runtime import dispatch_code_task_intent

        return dispatch_code_task_intent(intent, arguments, source_context=source_context, workspace_root=workspace_root)
    if intent.startswith("repo."):
        # RepoOps' control plane: one runtime, composed from the authorities above this line,
        # that executes every local step back through THIS door and every remote observation
        # through the KAS forge boundary.
        from core.repoops.plane import dispatch_repo_intent

        return dispatch_repo_intent(intent, arguments, source_context=source_context, workspace_root=workspace_root)
    try:
        if intent == "capability.expand_family":
            return _capability_expand_family(arguments, source_context=source_context)
        if intent == "workspace.identity":
            return _memoized_runtime_result(
                "workspace.identity",
                arguments,
                lambda: _workspace_identity(workspace_root),
                scope=(str(workspace_root),),
            )
        if intent == "workspace.list_files":
            return _list_files(arguments, workspace_root=workspace_root)
        if intent == "machine.list_directory":
            return _list_machine_directory(arguments, workspace_root=workspace_root)
        if intent == "machine.inspect_specs":
            return _memoized_runtime_result(
                "machine.inspect_specs",
                arguments,
                lambda: _inspect_machine_specs(),
            )
        if intent == "machine.disk_usage":
            return _machine_disk_usage(arguments)
        if intent == "machine.find_folder":
            return _machine_find_folder(arguments, workspace_root=workspace_root)
        if intent == "machine.find_file":
            return _machine_find_file(arguments, workspace_root=workspace_root)
        if intent == "machine.display_inspect":
            return _machine_display_inspect(arguments)
        if intent == "machine.find_largest":
            return _machine_find_largest(arguments, workspace_root=workspace_root)
        if intent == "machine.event_log_errors":
            return _machine_event_log_errors(arguments)
        if intent == "machine.list_processes":
            return _machine_list_processes(arguments)
        if intent == "machine.host_state":
            return _machine_host_state(arguments)
        if intent == "machine.move_path":
            return _covered(lambda: _machine_move_path(arguments, workspace_root=workspace_root))
        if intent == "machine.read_file":
            return _read_machine_file(arguments)
        if intent == "machine.ensure_directory":
            return _covered(lambda: _ensure_machine_directory(arguments))
        if intent == "machine.write_file":
            return _covered(
                lambda: _write_machine_file(
                    arguments,
                    reviewed_destination=_approved_destination_for(source_context, intent, arguments.get("path")),
                )
            )
        if intent == "pdf.extract_text":
            return _pdf_extract_text(arguments)
        if intent == "pdf.ocr":
            return _pdf_ocr(arguments)
        if intent == "skill.list":
            return _skill_list(arguments)
        if intent == "skill.create":
            return _covered(lambda: _skill_create(arguments))
        if intent == "skill.validate":
            return _skill_validate(arguments)
        if intent == "skill.install":
            return _skill_install(arguments)
        if intent == "skill.rollback":
            return _skill_rollback(arguments)
        if intent == "skill.inspect":
            return _skill_inspect(arguments)
        if intent in {"contacts.search", "contacts.resolve", "contacts.save", "contacts.update", "contacts.delete"}:
            return _contacts_tool(intent, arguments, source_context)
        if intent in {"profile.remember", "profile.forget", "profile.list"}:
            return _operator_profile_tool(intent, arguments, source_context)
        if intent in {"wallet.status", "wallet.propose", "wallet.simulate", "wallet.payment_status", "x402.propose"}:
            return _wallet_model_tool(intent, arguments, source_context)
        if intent == "email.send":
            return _email_send(arguments)
        if intent == "email.read":
            return _email_read(arguments)
        if intent == "email.reply":
            return _email_reply(arguments)
        if intent == "email.open":
            return _email_open(arguments)
        if intent in {"email.draft.save", "email.draft.get", "email.draft.approve",
                      "email.draft.send", "email.draft.reconcile"}:
            return _email_draft_tool(intent, arguments, source_context)
        if intent == "image.generate":
            return _image_generate(arguments)
        if intent == "video.generate":
            return _video_generate(arguments)
        if intent == "media.open":
            from core.media_studio_chat_tools import media_open

            return _covered(lambda: media_open(arguments))
        if intent == "media.inspect":
            from core.media_studio_chat_tools import media_inspect

            return media_inspect(arguments)
        if intent == "media.edit":
            from core.media_studio_chat_tools import media_edit

            return _covered(lambda: media_edit(arguments))
        if intent == "media.undo":
            from core.media_studio_chat_tools import media_undo_redo

            return _covered(lambda: media_undo_redo(arguments, direction="undo"))
        if intent == "media.redo":
            from core.media_studio_chat_tools import media_undo_redo

            return _covered(lambda: media_undo_redo(arguments, direction="redo"))
        if intent == "media.export":
            from core.media_studio_chat_tools import media_export

            return _covered(lambda: media_export(arguments))
        if intent == "x.trending":
            return _x_trending(arguments)
        if intent == "demo.plan":
            return _demo_plan(arguments, source_context=source_context)
        if intent == "set.save":
            return _set_save(arguments)
        if intent == "set.use":
            return _set_use(arguments)
        if intent == "wallet.spend":
            return _wallet_spend(arguments)
        if intent == "web0.open_builder_draft":
            return _web0_open_builder_draft(arguments)
        if intent == "web0.create_project":
            return _web0_create_project(arguments)
        if intent == "web0.fill_slots":
            return _web0_fill_slots(arguments)
        if intent == "web0.add_block":
            return _web0_add_block(arguments)
        if intent == "web0.add_gated_section":
            return _web0_add_gated_section(arguments)
        if intent == "web0.encrypt_whole_site":
            return _web0_encrypt_whole_site(arguments)
        if intent == "web0.compile_preview":
            return _web0_compile_preview(arguments)
        if intent == "web0.publish":
            return _web0_publish(arguments, source_context=source_context)
        if intent == "marketplace.search_listings":
            return _marketplace_search_listings(arguments)
        if intent == "marketplace.purchase_knowledge":
            return _marketplace_purchase_knowledge(arguments)
        if intent == "web.fetch":
            return _web_fetch(arguments, source_context=source_context)
        if intent == "workspace.list_tree":
            return _list_tree(arguments, workspace_root=workspace_root)
        if intent == "workspace.search_text":
            return _search_text(arguments, workspace_root=workspace_root)
        if intent == "workspace.symbol_search":
            return _symbol_search(arguments, workspace_root=workspace_root)
        if intent == "workspace.read_file":
            return _read_file(arguments, workspace_root=workspace_root)
        if intent == "workspace.ensure_directory":
            return _dispatch_with_mutation_activity(
                intent, workspace_root=workspace_root, source_context=source_context, arguments=arguments,
                handler=lambda: _ensure_directory(
                    arguments,
                    workspace_root=workspace_root,
                    reviewed_destination=_approved_destination_for(source_context, intent, arguments.get("path")),
                ),
            )
        if intent == "workspace.write_file":
            return _dispatch_with_mutation_activity(
                intent, workspace_root=workspace_root, source_context=source_context, arguments=arguments,
                handler=lambda: _write_file(
                    arguments,
                    workspace_root=workspace_root,
                    session_id=_runtime_session_id(source_context),
                    reviewed_destination=_approved_destination_for(source_context, intent, arguments.get("path")),
                ),
            )
        if intent == "workspace.replace_in_file":
            return _dispatch_with_mutation_activity(
                intent, workspace_root=workspace_root, source_context=source_context, arguments=arguments,
                handler=lambda: _replace_in_file(
                    arguments,
                    workspace_root=workspace_root,
                    session_id=_runtime_session_id(source_context),
                    reviewed_destination=_approved_destination_for(source_context, intent, arguments.get("path")),
                ),
            )
        if intent == "workspace.apply_unified_diff":
            return _dispatch_with_mutation_activity(
                intent, workspace_root=workspace_root, source_context=source_context, arguments=arguments,
                handler=lambda: _apply_unified_diff(
                    arguments,
                    workspace_root=workspace_root,
                    session_id=_runtime_session_id(source_context),
                    reviewed_destinations=_approved_destinations_for(source_context, intent),
                ),
            )
        if intent == "workspace.git_status":
            return _git_status(arguments, workspace_root=workspace_root)
        if intent == "workspace.git_diff":
            return _git_diff(arguments, workspace_root=workspace_root)
        if intent == "workspace.git_summary":
            return _git_summary(arguments, workspace_root=workspace_root)
        if intent == "workspace.rollback_last_change":
            return _dispatch_with_mutation_activity(
                intent, workspace_root=workspace_root, source_context=source_context, arguments=arguments,
                handler=lambda: _rollback_last_change(
                    arguments, workspace_root=workspace_root, session_id=_runtime_session_id(source_context),
                    source_context=source_context,
                ),
            )
        if intent == "workspace.run_tests":
            result = _covered(lambda: _run_validation("workspace.run_tests", arguments, workspace_root=workspace_root, source_context=source_context))
            return _attach_procedure_learning(
                result,
                validation_intent="workspace.run_tests",
                workspace_root=workspace_root,
                source_context=source_context,
            )
        if intent == "workspace.run_lint":
            result = _covered(lambda: _run_validation("workspace.run_lint", arguments, workspace_root=workspace_root, source_context=source_context))
            return _attach_procedure_learning(
                result,
                validation_intent="workspace.run_lint",
                workspace_root=workspace_root,
                source_context=source_context,
            )
        if intent == "workspace.run_formatter":
            result = _covered(lambda: _run_validation("workspace.run_formatter", arguments, workspace_root=workspace_root, source_context=source_context))
            return _attach_procedure_learning(
                result,
                validation_intent="workspace.run_formatter",
                workspace_root=workspace_root,
                source_context=source_context,
            )
        if intent == "code.review_evidence":
            # The coding-assistant lane's read-only projection; owned by core/code_assistant.
            # This branch is the whole registration: the contract lives in
            # runtime_tool_contracts.py and the handler in core.code_assistant.review.
            from core.code_assistant.review import review_evidence_tool

            return review_evidence_tool(arguments, workspace_root=workspace_root, source_context=source_context)
        if intent == "orchestration.execute_envelope":
            return _execute_task_envelope_intent(
                arguments,
                workspace_root=workspace_root,
                session_id=_runtime_session_id(source_context),
                source_context=source_context,
            )
        if intent == "sandbox.run_command":
            return _covered(lambda: _run_command(arguments, workspace_root=workspace_root, source_context=source_context))
    except EffectOutcomeUnknown:
        # Already typed as reconciliation-required by an inner layer; never
        # collapse it into a plain failure result here.
        raise
    except Exception as exc:
        # ── A6 AB-1 boundary ────────────────────────────────────────────────
        # A mutating effect claimed PREPARED->DISPATCHED before this call has
        # crossed the dispatch boundary: whether its physical mutation landed
        # is UNPROVABLE, so re-raise typed and let the executor classify
        # UNKNOWN durably. Everything else keeps today's error-result shape.
        claim = peek_in_flight_effect()
        if claim is not None and str(claim.get("tool_name") or "") == intent:
            raise EffectOutcomeUnknown(
                reason="post_dispatch_exception",
                detail=f"{type(exc).__name__}: {exc}",
            ) from exc
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="error",
            response_text=f"Execution tool `{intent}` failed: {exc}",
            details={
                "error": str(exc),
                # The type, alongside the message. `str(exc)` on its own leaves a triage unable to
                # tell a PermissionError from a TimeoutError from a local TypeError -- three
                # different investigations that read identically once the class is dropped. This is
                # the class NAME only; the traceback is deliberately not carried into a result that
                # reaches the Activity panel.
                "exception_class": type(exc).__name__,
                "observation": _tool_observation(
                    intent=intent,
                    tool_surface="runtime_execution",
                    ok=False,
                    status="error",
                    error=str(exc),
                ),
            },
        )
    return RuntimeExecutionResult(
        handled=True,
        ok=False,
        status="unsupported",
        response_text=f"I won't fake it: `{intent}` is not supported by the runtime execution layer.",
        details={
            "observation": _tool_observation(
                intent=intent,
                tool_surface="runtime_execution",
                ok=False,
                status="unsupported",
            ),
        },
    )


def execute_runtime_tool(
    intent: str,
    arguments: dict[str, Any],
    *,
    source_context: dict[str, Any] | None = None,
    trusted_local_only: bool = False,
) -> RuntimeExecutionResult | None:
    """THE runtime tool door, with the council seat fence in front of it.

    This function is where every real runtime effect actually happens, and — unlike
    `tool_intent_executor.execute_tool_intent`, which is only ONE of its callers — it is
    reachable directly from the sub-task envelope executor (`core/orchestration/executor.py`),
    the machine fast paths, the research tool loop and the turn front door. That is why the
    council fence lives here and not next to the mode matrix: a fence on a path a caller can
    walk around is a fence with a gate in it, and the 2026-09-02 seat that rewrote
    `core/agent_runtime/turn_planner_hook.py` walked around exactly that.

    For a context that is not a registered council seat, `evaluate` returns "no opinion" and
    this is a plain pass-through: ordinary tools keep the behaviour they had, byte for byte.

    For a seat, the verdict decides three things before the dispatch below ever runs — may
    this class be exercised at all, has the operator granted it for THIS run and THIS seat,
    and does every path it names land inside the seat's own disposable workspace — and the
    workspace root the dispatch resolves against is replaced by that workspace, so even an
    approved builder seat cannot reach the operator's checkout through a relative path.
    """
    from core.council import containment as council_containment

    verdict = council_containment.evaluate(intent, arguments, source_context)
    if not verdict.contained:
        return _dispatch_runtime_tool(
            intent, arguments, source_context=source_context, trusted_local_only=trusted_local_only
        )

    binding = verdict.binding
    target = verdict.target or str((arguments or {}).get("path") or "")
    if verdict.refusal:
        council_containment.record_effect(
            binding.run_id,
            binding.seat_id,
            intent=intent,
            side_effect_class=verdict.side_effect_class,
            outcome=(
                council_containment.OUTCOME_CANCELLED
                if not binding.live
                else council_containment.OUTCOME_REFUSED
            ),
            detail=verdict.refusal,
            target=target,
        )
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status=council_containment.CONTAINED_STATUS,
            response_text=f"`{intent}` was not executed: {verdict.refusal}.",
            details={
                "council_run_id": binding.run_id,
                "council_seat_id": binding.seat_id,
                "council_contained": True,
                "side_effect_class": verdict.side_effect_class,
                "reason": verdict.refusal,
                "executed": False,
                "observation": _tool_observation(
                    intent=intent,
                    tool_surface="council_seat_containment",
                    ok=False,
                    status=council_containment.CONTAINED_STATUS,
                    reason=verdict.refusal,
                ),
            },
        )

    if verdict.workspace is not None:
        # A granted mutating call is re-rooted onto the seat's disposable workspace. The
        # jail check above already proved every path argument lands inside it; this is what
        # makes a RELATIVE path land there too, instead of in whatever checkout the run was
        # convened against.
        source_context = {
            **dict(source_context or {}),
            "workspace": str(verdict.workspace),
            "workspace_root": str(verdict.workspace),
        }

    result = _dispatch_runtime_tool(
        intent, arguments, source_context=source_context, trusted_local_only=trusted_local_only
    )
    if verdict.mutating:
        council_containment.record_effect(
            binding.run_id,
            binding.seat_id,
            intent=intent,
            side_effect_class=verdict.side_effect_class,
            outcome=(
                council_containment.OUTCOME_EXECUTED
                if result is not None and getattr(result, "ok", False)
                else council_containment.OUTCOME_FAILED
            ),
            detail=str(getattr(result, "status", "") or "no_handler"),
            target=target,
        )
    return result


def _web0_open_builder_draft(arguments: dict[str, Any]) -> RuntimeExecutionResult:
    result = web0_open_builder_draft(
        str(arguments.get("title") or "VOOL-built Web0 draft"),
        str(arguments.get("code") or ""),
        domain=str(arguments.get("domain") or ""),
    )
    builder_url = str(result.get("builder_url") or "")
    response = (
        "Web0 local builder draft ready:\n"
        f"{builder_url}\n\n"
        "This is local preview/edit only. Publishing to Arweave/mainnet still requires an explicit publish command and wallet confirmation."
    )
    return RuntimeExecutionResult(
        handled=True,
        ok=True,
        status="executed",
        response_text=response,
        details={
            **result,
            "observation": _tool_observation(
                intent="web0.open_builder_draft",
                tool_surface="web0",
                ok=True,
                status="executed",
                builder_url=builder_url,
                template=str(result.get("template") or ""),
                domain=str(result.get("domain") or ""),
                title=str(result.get("title") or ""),
            ),
        },
    )


def _web0_result(
    intent: str,
    result: dict[str, Any],
    *,
    ok_response: str,
    **observation_fields: Any,
) -> RuntimeExecutionResult:
    error = str(result.get("error") or "").strip()
    if error:
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="rejected",
            response_text=f"`{intent}` could not complete: {error}",
            details={
                **result,
                "observation": _tool_observation(
                    intent=intent, tool_surface="web0", ok=False, status="rejected", error=error
                ),
            },
        )
    return RuntimeExecutionResult(
        handled=True,
        ok=True,
        status="executed",
        response_text=ok_response,
        details={
            **result,
            "observation": _tool_observation(
                intent=intent,
                tool_surface="web0",
                ok=True,
                status="executed",
                project_id=str(result.get("project_id") or ""),
                result_status=str(result.get("status") or ""),
                **observation_fields,
            ),
        },
    )


def _web0_create_project(arguments: dict[str, Any]) -> RuntimeExecutionResult:
    result = web0_create_project(
        str(arguments.get("template_id") or ""),
        str(arguments.get("domain") or ""),
        str(arguments.get("project_name") or ""),
    )
    return _web0_result(
        "web0.create_project",
        result,
        ok_response=(
            f"Web0 builder project `{result.get('project_id', '')}` created for "
            f"{result.get('domain', '')} ({result.get('source', 'local_draft')})."
        ),
        domain=str(result.get("domain") or ""),
    )


def _web0_fill_slots(arguments: dict[str, Any]) -> RuntimeExecutionResult:
    slots = arguments.get("slots")
    result = web0_fill_slots(
        str(arguments.get("project_id") or ""),
        slots if isinstance(slots, dict) else {},
    )
    updated = result.get("updated_slots") or []
    return _web0_result(
        "web0.fill_slots",
        result,
        ok_response=f"Filled {len(updated)} slot(s): {', '.join(str(s) for s in updated)}.",
    )


def _web0_add_block(arguments: dict[str, Any]) -> RuntimeExecutionResult:
    block = arguments.get("block")
    result = web0_add_block(
        str(arguments.get("project_id") or ""),
        str(arguments.get("page_id") or "home"),
        block if isinstance(block, dict) else {},
        int(arguments.get("position", -1) or -1),
    )
    return _web0_result(
        "web0.add_block",
        result,
        ok_response=(
            f"Block added to page `{result.get('page_id', 'home')}` "
            f"({result.get('block_count', 0)} block(s) on the page)."
        ),
    )


def _coerce_str_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _web0_add_gated_section(arguments: dict[str, Any]) -> RuntimeExecutionResult:
    result = web0_add_gated_section(
        str(arguments.get("project_id") or ""),
        str(arguments.get("page_id") or "home"),
        str(arguments.get("content") or ""),
        _coerce_str_list(arguments.get("whitelist")),
        str(arguments.get("mode") or "whitelist"),
        str(arguments.get("label") or "Private content"),
        int(arguments.get("position", -1) or -1),
    )
    return _web0_result(
        "web0.add_gated_section",
        result,
        ok_response=(
            f"Gated section added (block `{result.get('block_id', '')}`, "
            f"{result.get('wallets_registered', 0)} wallet(s) whitelisted). Only the ciphertext ships."
        ),
        block_id=str(result.get("block_id") or ""),
    )


def _web0_encrypt_whole_site(arguments: dict[str, Any]) -> RuntimeExecutionResult:
    result = web0_encrypt_whole_site(
        str(arguments.get("project_id") or ""),
        _coerce_str_list(arguments.get("whitelist")),
        str(arguments.get("mode") or "whitelist"),
        str(arguments.get("label") or "Private content"),
    )
    return _web0_result(
        "web0.encrypt_whole_site",
        result,
        ok_response=(
            f"Encrypted {result.get('blocks_protected', 0)} block(s) behind the wallet gate; "
            "the plaintext is no longer present in the compiled page."
        ),
        blocks_protected=int(result.get("blocks_protected") or 0),
    )


def _web0_compile_preview(arguments: dict[str, Any]) -> RuntimeExecutionResult:
    result = web0_compile_preview(str(arguments.get("project_id") or ""))
    return _web0_result(
        "web0.compile_preview",
        result,
        ok_response=(
            f"Compiled a local preview ({result.get('size_kb', 0)} KB, "
            f"source={result.get('source', 'local_fallback')}). Nothing was published."
        ),
        size_kb=float(result.get("size_kb") or 0.0),
    )


def _web0_publish(
    arguments: dict[str, Any],
    *,
    source_context: dict[str, Any] | None,
) -> RuntimeExecutionResult:
    """Gated off by default. The autonomous layer never publishes on its own.

    BOTH gates are read from the trusted ``source_context``, never from the
    model-supplied ``arguments``: the ``allow_network_publish`` opt-in and the
    ``vool_wallet``. The model can emit the intent but can neither flip the
    opt-in nor conjure a wallet, so a request alone is never sufficient to
    publish — only a trusted caller that has wired both can.
    """
    project_id = str(arguments.get("project_id") or "")
    ctx = source_context or {}
    allow_network_publish = bool(ctx.get("allow_network_publish", False))
    wallet = ctx.get("vool_wallet")

    if not allow_network_publish or wallet is None:
        reason = (
            "No publish opt-in was given (allow_network_publish is off)."
            if not allow_network_publish
            else "No publishing wallet is wired into this runtime."
        )
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="requires_opt_in",
            response_text=(
                "I won't publish to Arweave from here. "
                f"{reason} Publishing uploads content permanently and needs an explicit "
                "opt-in plus a caller-supplied wallet — it never runs autonomously."
            ),
            details={
                "project_id": project_id,
                "status": "publish_gated_off",
                "allow_network_publish": allow_network_publish,
                "wallet_present": wallet is not None,
                "observation": _tool_observation(
                    intent="web0.publish",
                    tool_surface="web0",
                    ok=False,
                    status="requires_opt_in",
                    project_id=project_id,
                    allow_network_publish=allow_network_publish,
                    wallet_present=wallet is not None,
                ),
            },
        )

    result = web0_publish(project_id, wallet, allow_network_publish=True)
    return _web0_result(
        "web0.publish",
        result,
        ok_response=(
            f"Published to Arweave: {result.get('permanent_url', '')} "
            f"(tx {result.get('arweave_txid', '')})."
        ),
        arweave_txid=str(result.get("arweave_txid") or ""),
    )


def _marketplace_search_listings(arguments: dict[str, Any]) -> RuntimeExecutionResult:
    listings = search_listings(
        query=str(arguments.get("query") or ""),
        domain_tag=str(arguments.get("domain_tag") or ""),
        max_results=int(arguments.get("max_results", 50) or 50),
    )
    rows = [
        {
            "shard_id": item.shard_id,
            "title": item.title,
            "seller_peer_id": item.seller_peer_id,
            "price_credits": item.price_credits,
            "quality_score": item.quality_score,
            "purchase_count": item.purchase_count,
            "domain_tags": list(item.domain_tags),
        }
        for item in listings
    ]
    return RuntimeExecutionResult(
        handled=True,
        ok=True,
        status="executed",
        response_text=(f"Found {len(rows)} marketplace listing(s)." if rows else "No marketplace listings matched."),
        details={
            "listings": rows,
            "count": len(rows),
            "observation": _tool_observation(
                intent="marketplace.search_listings",
                tool_surface="marketplace",
                ok=True,
                status="executed",
                count=len(rows),
                shard_ids=[r["shard_id"] for r in rows][:20],
            ),
        },
    )


def _marketplace_purchase_knowledge(arguments: dict[str, Any]) -> RuntimeExecutionResult:
    """Buy a shard for the LOCAL node. The buyer is always this node's peer id —
    the model can pick a shard but cannot spend another peer's credits."""
    shard_id = str(arguments.get("shard_id") or "").strip()
    if not shard_id:
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="rejected",
            response_text="`marketplace.purchase_knowledge` needs a shard_id.",
            details={
                "observation": _tool_observation(
                    intent="marketplace.purchase_knowledge",
                    tool_surface="marketplace",
                    ok=False,
                    status="rejected",
                    error="missing_shard_id",
                ),
            },
        )

    buyer = get_local_peer_id()
    receipt_id = str(arguments.get("receipt_id") or "").strip() or None
    result = purchase_knowledge(buyer, shard_id, receipt_id=receipt_id)

    ok = bool(result.get("ok"))
    reason = str(result.get("reason") or "")
    charged = float(result.get("charged_credits") or 0.0)
    if ok and reason == "already_purchased":
        response = f"Already unlocked shard `{shard_id}` — no credits charged."
    elif ok:
        response = f"Purchased shard `{shard_id}` for {charged} credit(s); access unlocked."
    else:
        response = f"Could not buy shard `{shard_id}`: {reason}."
    status = "executed" if ok else "rejected"

    return RuntimeExecutionResult(
        handled=True,
        ok=ok,
        status=status,
        response_text=response,
        details={
            **result,
            "buyer_peer_id": buyer,
            "observation": _tool_observation(
                intent="marketplace.purchase_knowledge",
                tool_surface="marketplace",
                ok=ok,
                status=status,
                shard_id=shard_id,
                reason=reason,
                charged_credits=charged,
            ),
        },
    )


def _remote_fetch_allowed(source_context: dict[str, Any] | None) -> bool:
    if "allow_remote_fetch" in (source_context or {}):
        return bool((source_context or {}).get("allow_remote_fetch"))
    return bool(policy_engine.allow_web_fallback())


def _web_url(raw_url: object) -> str:
    url = str(raw_url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("web tools require an absolute http(s) URL")
    return url


def _web_fetch(arguments: dict[str, Any], *, source_context: dict[str, Any] | None) -> RuntimeExecutionResult:
    url = _web_url(arguments.get("url"))
    # this runtime's own service (its chat and owner doors) is never a fetch destination (core.runtime_served_origins)
    from core.runtime_served_origins import runtime_origin_refusal

    own_service = runtime_origin_refusal(url)
    if own_service:
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="runtime_origin_refused",
            response_text=f"Web fetch did not open `{url}`: {own_service}",
            details={
                "observation": _tool_observation(intent="web.fetch", tool_surface="web", ok=False, status="runtime_origin_refused", url=url),
            },
        )
    if not _remote_fetch_allowed(source_context):
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="disabled_by_policy",
            response_text=f"Web fetch is disabled by policy for `{url}`.",
            details={
                "observation": _tool_observation(
                    intent="web.fetch",
                    tool_surface="web",
                    ok=False,
                    status="disabled_by_policy",
                    url=url,
                ),
            },
        )
    timeout_seconds = max(1.0, min(float(arguments.get("timeout_seconds") or 12.0), 30.0))
    max_bytes = max(16_384, min(int(policy_engine.max_fetch_bytes()), 2_000_000))
    # The counter the URL-grounding backstop reads. `tools/web/http_fetch.py` records its attempts;
    # this path goes to urllib directly and recorded none, so a page this tool really did fetch
    # still looked unfetched. Measured 2026-07-30: with the URL lane in the front door,
    # "take a look at https://example.org and summarise what is there" and "read out what's at
    # http://neverssl.com" fetched successfully in ~2s and the user was shown "You linked a URL and
    # I did not open it on this turn" — the backstop overwriting the fetched page with a denial
    # that it had been fetched. Only the phrasings that miss `_URL_REVIEW_INTENT_RE` survived,
    # which is why "fetch ... and tell me what the page says" worked and "open ..." did not.
    # The ONE outbound door: veto (ContextVar) is enforced and the attempt
    # reported into the owning turn's ledger BEFORE any socket opens. A refusal
    # surfaces as the tool's existing "disabled by policy" result shape.
    from core.remote_fetch_policy import RemoteFetchRefusedError, open_remote

    request = urllib.request.Request(url, headers={"User-Agent": "VOOL-runtime/1.0", "Accept": "text/html,text/plain,*/*"})
    try:
        with open_remote(request, timeout=timeout_seconds) as response:
            final_url = str(response.geturl() or url)
            content_type = str(response.headers.get("content-type") or "").strip()
            body = response.read(max_bytes + 1)
    except RemoteFetchRefusedError:
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="disabled_by_policy",
            response_text=f"Web fetch is disabled by policy for `{url}`.",
            details={
                "observation": _tool_observation(
                    intent="web.fetch",
                    tool_surface="web",
                    ok=False,
                    status="disabled_by_policy",
                    url=url,
                ),
            },
        )
    except urllib.error.HTTPError as exc:
        status = "login_wall" if int(exc.code or 0) in {401, 403} else "error"
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status=status,
            response_text=f"Web fetch for `{url}` failed with HTTP {int(exc.code or 0)}.",
            details={
                "observation": _tool_observation(
                    intent="web.fetch",
                    tool_surface="web",
                    ok=False,
                    status=status,
                    url=url,
                    http_status=int(exc.code or 0),
                ),
            },
        )
    except Exception as exc:
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="error",
            response_text=f"Web fetch for `{url}` failed: {exc}",
            details={
                "observation": _tool_observation(
                    intent="web.fetch",
                    tool_surface="web",
                    ok=False,
                    status="error",
                    url=url,
                    error=str(exc),
                ),
            },
        )
    truncated = len(body) > max_bytes
    text = body[:max_bytes].decode("utf-8", errors="replace")
    return RuntimeExecutionResult(
        handled=True,
        ok=True,
        status="ok",
        response_text=f"Fetched `{final_url}` ({len(text)} chars{', truncated' if truncated else ''}).",
        details={
            "url": url,
            "final_url": final_url,
            "content_type": content_type,
            "text": text,
            "truncated": truncated,
            "observation": _tool_observation(
                intent="web.fetch",
                tool_surface="web",
                ok=True,
                status="ok",
                url=url,
                final_url=final_url,
                content_type=content_type,
                text=text,
                truncated=truncated,
            ),
        },
    )


def _capability_expand_family(
    arguments: dict[str, Any], *, source_context: dict[str, Any] | None
) -> RuntimeExecutionResult:
    """The model's legal escalation past its bounded per-turn tool offer.

    Records the requested family for THIS turn and reports the bounded set the next
    model round will carry. The tool itself changes nothing else: read-only, no side
    effects, and the family must be canonical — an unknown name is a structured
    refusal, never a silent no-op the model could mistake for an empty family.
    """
    from core.capability_graph import _CANONICAL_FAMILIES, model_visible_specs

    family = str(arguments.get("family") or "").strip().lower()
    if not family:
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="invalid_arguments",
            response_text="`capability.expand_family` needs a `family` name.",
            details={
                "observation": _tool_observation(
                    intent="capability.expand_family",
                    tool_surface="capability",
                    ok=False,
                    status="invalid_arguments",
                ),
            },
        )
    if family not in _CANONICAL_FAMILIES:
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="unknown_family",
            response_text=(
                f"`{family}` is not a capability family. Ask `operator.list_tools` "
                "for the family table first."
            ),
            details={
                "family": family,
                "observation": _tool_observation(
                    intent="capability.expand_family",
                    tool_surface="capability",
                    ok=False,
                    status="unknown_family",
                    family=family,
                ),
            },
        )

    # What the family itself can seat right now: its available members, as the family's own bounded
    # offer seats them. That offer's reserved seats and navigation fallback are not the family, so a
    # family with nothing available is refused here rather than recorded -- recording it would spend
    # the turn's expansion cap and widen every later round with tools the model did not ask for.
    from core.capability_graph import _HARD_MAX_CANDIDATES, DiscoveryRequest, discover

    offered = [
        str(spec.get("intent") or "")
        for spec in model_visible_specs(family_hint=family)
        if str(spec.get("intent") or "")
    ]
    members = {
        candidate.tool_intent
        for candidate in discover(DiscoveryRequest(family=family), max_candidates=_HARD_MAX_CANDIDATES).candidates
        if candidate.available
    }
    seated = [intent for intent in offered if intent in members]
    if not seated:
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="family_unavailable",
            response_text=(
                f"Nothing in the `{family}` family is available on this runtime, so no tools were added. "
                "Ask `operator.list_tools` for the families that are."
            ),
            details={
                "family": family,
                "observation": _tool_observation(
                    intent="capability.expand_family",
                    tool_surface="capability",
                    ok=False,
                    status="family_unavailable",
                    family=family,
                ),
            },
        )
    from core.tool_offer_state import _EXPANSION_MAX_FAMILIES, record_family_expansion

    turn_families = record_family_expansion(source_context, family)
    if family not in turn_families:
        # Say what happened instead of claiming a seat: a turn at its expansion cap, or a call
        # that names no turn to seat anything in, leaves every later round's offer unchanged.
        status = "expansion_limit_reached" if turn_families else "no_turn_to_expand"
        response_text = (
            f"`{family}` was not added: this turn already expanded "
            + ", ".join(f"`{name}`" for name in turn_families)
            + f", and a turn expands at most {_EXPANSION_MAX_FAMILIES} families. "
            "Use those tools, or answer with what you have."
            if turn_families
            else "`capability.expand_family` seats a family for the later model rounds of a turn, "
            "and this call names no turn."
        )
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status=status,
            response_text=response_text,
            details={
                "family": family,
                "turn_families": list(turn_families),
                "observation": _tool_observation(
                    intent="capability.expand_family",
                    tool_surface="capability",
                    ok=False,
                    status=status,
                    family=family,
                    turn_families=list(turn_families),
                ),
            },
        )
    # Every later round of this turn builds its offer with the family (core.tool_offer_state) and
    # re-reads policy and availability each time.
    listing = ", ".join(seated)
    return RuntimeExecutionResult(
        handled=True,
        ok=True,
        status="ok",
        response_text=(
            f"Seated the `{family}` family for the rest of this turn: {listing}. "
            "Call one of them now."
        ),
        details={
            "family": family,
            "seated_intents": seated,
            "turn_families": list(turn_families),
            "observation": _tool_observation(
                intent="capability.expand_family",
                tool_surface="capability",
                ok=True,
                status="ok",
                family=family,
                seated_intents=seated,
            ),
        },
    )


def _workspace_identity(workspace_root: Path) -> RuntimeExecutionResult:
    """Answer "where is my workspace / what project am I in" in one line.

    Sixteen workspace tools existed and not one of them reported WHERE the workspace is, so a model
    asked that question reached for the closest available thing and listed the tree. Observed live
    2026-07-28: "do you see what folder our workspace is set on?" returned ~50 lines and 13,938
    tokens without ever naming the folder.

    A question about a setting deserves an answer about the setting. Counts are cheap and bounded
    (one non-recursive scandir) and make the reply useful without becoming a listing.
    """

    root = Path(workspace_root)
    name = root.name or str(root)
    exists = root.is_dir()
    files = directories = 0
    if exists:
        try:
            with os.scandir(root) as entries:
                for entry in entries:
                    if entry.name.startswith("."):
                        continue
                    try:
                        directories += 1 if entry.is_dir(follow_symlinks=False) else 0
                        files += 0 if entry.is_dir(follow_symlinks=False) else 1
                    except OSError:
                        continue
        except OSError:
            pass
    is_git = exists and (root / ".git").exists()

    if not exists:
        text = f"The workspace is set to `{root}`, but that folder does not exist right now."
    else:
        parts = [f"The workspace is set to `{root}`."]
        parts.append(
            f"That is the project `{name}`" + (" (a git repository)" if is_git else "")
            + f", holding {files} file{'s' if files != 1 else ''} and "
            f"{directories} folder{'s' if directories != 1 else ''} at the top level."
        )
        text = " ".join(parts)

    return RuntimeExecutionResult(
        handled=True,
        ok=exists,
        status="executed" if exists else "not_found",
        response_text=text,
        details={
            "observation": _tool_observation(
                intent="workspace.identity",
                tool_surface="workspace",
                ok=exists,
                status="executed" if exists else "not_found",
                path=str(root),
                name=name,
                is_git_repository=is_git,
                top_level_files=files,
                top_level_directories=directories,
            ),
            "resolved_target": str(root),
        },
    )


def _workspace_root(source_context: dict[str, Any] | None) -> Path:
    raw = str((source_context or {}).get("workspace") or (source_context or {}).get("workspace_root") or "").strip()
    return resolve_workspace_root(raw or None)


def _resolve_workspace_path(raw_path: str | None, *, workspace_root: Path, write: bool = False) -> Path:
    resolved = resolve_workspace_path_impl(raw_path, workspace_root=workspace_root)
    if write:
        from core.runtime_state_protection import refusal as _protected_refusal

        reason = _protected_refusal(resolved, write=True)
        if reason:
            raise ValueError(reason)
    return resolved

def resolve_write_target(intent: str, raw_path: str | None, source_context: dict[str, Any] | None) -> Path | None:
    """Where a write-type call PHYSICALLY lands, resolved by the authority its writer uses: the
    machine's safe home directories for `machine.*` (`_resolve_machine_directory`); for
    `workspace.*`, the confined, symlink-resolved path (`_resolve_workspace_path`) under
    `_workspace_root` -- the trusted context's `workspace`, then `workspace_root`, then the
    configured workspace. Permission classification and approval matching call this, so no
    decision about a write can describe a different file than the one the writer touches. None
    when no path is named or the writer itself would refuse the path."""
    path = str(raw_path or "").strip()
    if not path:
        return None
    try:
        if str(intent or "").strip().lower().startswith("machine."):
            return _resolve_machine_directory(path)
        return _resolve_workspace_path(path, workspace_root=_workspace_root(source_context))
    except (ValueError, OSError, RuntimeError):
        return None


#: The source-context key a code-task step's inner call carries its approved proposal's reviewed
#: destinations under (`CodeTaskRuntime._execute`). A carried record only ever NARROWS a write: it
#: can refuse one, never authorize one.
APPROVED_DESTINATIONS_CONTEXT_KEY = "_code_task_approved_destinations"


def _approved_destinations_for(source_context: dict[str, Any] | None, intent: str) -> list[dict[str, Any]] | None:
    """Every reviewed destination carried for this tool, or None when none is carried."""
    records = source_context.get(APPROVED_DESTINATIONS_CONTEXT_KEY) if isinstance(source_context, dict) else None
    if not isinstance(records, list):
        return None
    clean_intent = str(intent or "").strip()
    matching = [record for record in records if isinstance(record, dict) and record.get("intent") == clean_intent]
    return matching or None


def _approved_destination_for(
    source_context: dict[str, Any] | None, intent: str, raw_path: str | None
) -> dict[str, Any] | None:
    """The reviewed destination carried for THIS write (same tool, same named path), or None."""
    clean_path = str(raw_path or "").strip()
    for record in _approved_destinations_for(source_context, intent) or []:
        if record.get("path") == clean_path:
            return record
    return None


def _destination_kind(target: Path) -> str:
    return "file" if target.is_file() else "directory" if target.is_dir() else "absent"


def _reviewed_destination_result(
    intent: str, *, surface: str, status: str, label: str, reason: str, detail: str = ""
) -> RuntimeExecutionResult:
    return RuntimeExecutionResult(
        handled=True,
        ok=False,
        status=status,
        response_text=f"`{label}` {reason}; nothing was written. Re-propose against the file as it is now.",
        details={
            "path": label,
            "reason": detail or reason,
            "observation": _tool_observation(intent=intent, tool_surface=surface, ok=False, status=status, path=label),
        },
    )


def _reviewed_destination_refusal(
    intent: str, *, surface: str, record: dict[str, Any] | None, target: Path, label: str
) -> RuntimeExecutionResult | None:
    """The writer's own check of a carried reviewed destination, after its resolution and before any
    byte moves: the target it resolved must BE the reviewed canonical target, of the reviewed kind,
    holding the reviewed bytes. None when no record is carried or all hold. (The bytes are checked
    again inside the pinned directory immediately before the rename.)"""
    if record is None:
        return None
    import hashlib

    if str(target) != str(record.get("target") or ""):
        return _reviewed_destination_result(
            intent, surface=surface, status="destination_changed", label=label,
            reason="now resolves to a different file than the one reviewed",
        )
    try:
        kind = _destination_kind(target)
        actual = hashlib.sha256(target.read_bytes()).hexdigest() if kind == "file" else ""
    except OSError:
        kind, actual = "unreadable", None
    if kind != str(record.get("kind") or kind) or actual != str(record.get("prior_sha256") or ""):
        return _reviewed_destination_result(
            intent, surface=surface, status="stale_base", label=label,
            reason="no longer holds what was reviewed",
        )
    return None


def _relative_path(path: Path, *, workspace_root: Path) -> str:
    return workspace_relative_path(path, workspace_root=workspace_root)


def _truncate(text: str, *, limit: int = 1800) -> str:
    value = str(text or "")
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + "\n...[truncated]"


def _home_relative_label(path: Path) -> str:
    home = _machine_home().resolve()
    try:
        relative = path.resolve().relative_to(home)
        return "~" if str(relative) in {"", "."} else f"~/{relative.as_posix()}"
    except Exception:
        return path.as_posix()


def _safe_machine_roots() -> tuple[Path, ...]:
    home = _machine_home().resolve()
    return tuple((home / name).resolve() for name in _SAFE_MACHINE_DIRECTORY_NAMES)


def _machine_home() -> Path:
    try:
        return Path.home()
    except Exception:
        pass
    for key in ("HOME", "USERPROFILE"):
        value = str(os.environ.get(key) or "").strip()
        if value:
            return Path(value).expanduser()
    return Path("~").expanduser()


def _expand_machine_user(raw: str) -> Path:
    value = str(raw or "").strip()
    if value in {"~", "~/", "~\\"}:
        return _machine_home()
    if value.startswith("~/") or value.startswith("~\\"):
        return _machine_home() / value[2:]
    return Path(value).expanduser()


def _resolve_machine_directory(raw_path: str | None, *, write: bool = False) -> Path:
    """A safe machine directory path, refused when it is this runtime's own state (or, for a write, its running code)."""
    target = _resolve_safe_machine_directory(raw_path)
    from core.runtime_state_protection import refusal as _protected_refusal

    reason = _protected_refusal(target, write=write)
    if reason:
        raise ValueError(reason)
    return target


def _resolve_safe_machine_directory(raw_path: str | None) -> Path:
    raw = str(raw_path or "").strip()
    if not raw:
        return _safe_machine_roots()[0]
    lowered = raw.lower().strip()
    alias_map = {
        "desktop": "Desktop",
        "my desktop": "Desktop",
        "~/desktop": "Desktop",
        "downloads": "Downloads",
        "my downloads": "Downloads",
        "~/downloads": "Downloads",
        "documents": "Documents",
        "my documents": "Documents",
        "~/documents": "Documents",
        "docs": "Documents",
    }
    if lowered in alias_map:
        return (_machine_home() / alias_map[lowered]).resolve()
    candidate = _expand_machine_user(raw)
    if not candidate.is_absolute():
        candidate = (_machine_home() / candidate).resolve()
    else:
        candidate = candidate.resolve()
    for root in _safe_machine_roots():
        if candidate == root or root in candidate.parents:
            return candidate
    allowed = ", ".join(_home_relative_label(root) for root in _safe_machine_roots())
    raise ValueError(f"I can only inspect safe local directories in this lane: {allowed}.")


def _is_windows_platform() -> bool:
    """Indirection over `os.name == "nt"` so tests can simulate "running on Windows" for a
    single call without mutating the real `os.name` -- `pathlib.Path` itself branches on
    `os.name` to choose `WindowsPath`/`PosixPath`, so patching the global attribute directly
    breaks every OTHER `Path(...)` construction for the duration of the patch, including ones
    made deep inside unrelated modules during a full agent turn."""
    return os.name == "nt"


def _find_largest_scan_ceiling(top: int) -> int:
    """How many raw, unfiltered results to pull from `core.machine_diagnostics` before applying
    project-binding filtering and truncating to `top`.

    ANVIL, final round: the first version of this fix fixed the over-fetch at a constant 25 --
    ANVIL then demonstrated more than 25 dominating hidden-sibling entries under-returning
    legitimate results that genuinely existed further down the real ranking. 25 was a magic
    constant, not a correctness contract; this scales with what was actually asked for instead
    (`top * 10`), bounded at 500 so it stays a fixed ceiling, never an unbounded walk. This costs
    nothing extra: the walk underneath (`largest_files`'s `os.walk`, `largest_entries`'s full
    top-level `scandir`) already covers the same ground bounded by its own wall-clock budget
    regardless of how many results are kept -- raising the kept-count only grows a small
    in-memory heap/list, not the amount of filesystem read.
    """
    return min(500, max(100, top * 10))


def _resolve_find_largest_root(requested_drive: str) -> tuple[str | None, str]:
    """The root `machine.find_largest` may scan for an EXPLICIT `drive`/`path` argument.
    Returns `(root, "")` on success, or `(None, message)` naming the allowed roots.

    ANVIL, final round, HIGH: `find_largest` never validated `requested_drive` at all. While
    `machine.list_directory`/`machine.read_file` correctly refuse `/etc`, `/var/log`, `~/.ssh`,
    an explicit `find_largest` call with those SAME paths scanned them and returned real
    results (`/etc/services`, `~/.ssh/known_hosts`, ...) from a chat bound to an unrelated
    Desktop project -- inconsistent with the sibling tools, and unsafe.

    Reuses `_resolve_machine_directory` -- the exact contract `list_directory`/`read_file`
    already enforce -- for anything Desktop/Downloads/Documents-shaped, tilde forms included.
    Also accepts the WHOLE home directory (macOS/Linux) or a bare drive letter (`C:\\`, `D:\\`
    on Windows) as explicitly nameable targets: unlike the read/list/write lanes, aggregate
    disk-usage measurement has always legitimately needed that broader view, and it is exactly
    what this tool's own pre-existing default (no `drive` argument at all) already scans --
    naming it explicitly is not a new grant, it is the same grant this tool always had, merely
    spelled out. `~/.ssh` still falls through to `_resolve_machine_directory` and is still
    rejected: it resolves to a SUBDIRECTORY of home, never to home itself, so this allowance
    cannot be used to reach it.
    """
    raw = str(requested_drive or "").strip()
    if _is_windows_platform():
        bare_drive = raw.rstrip("\\/")
        if len(bare_drive) == 2 and bare_drive[1] == ":" and bare_drive[0].isalpha():
            return raw, ""
    else:
        try:
            if Path(raw).expanduser().resolve() == _machine_home().resolve():
                return str(_machine_home()), ""
        except OSError:
            pass
    try:
        return str(_resolve_machine_directory(raw)), ""
    except ValueError as exc:
        return None, str(exc)


def _runtime_session_id(source_context: dict[str, Any] | None) -> str:
    return str((source_context or {}).get("session_id") or "").strip()


def _result_from_payload(
    *,
    handled: bool = True,
    ok: bool,
    status: str,
    response_text: str,
    details: dict[str, Any],
) -> RuntimeExecutionResult:
    return RuntimeExecutionResult(
        handled=handled,
        ok=ok,
        status=status,
        response_text=response_text,
        details=details,
    )


# A path costs ~43 bytes, and the walk that produces it has already run in full before any cap
# applies — so a low enumeration ceiling saves no I/O and buys only blindness. 200 was scaffolding
# carried in from the baseline import (d72bcc4) and nobody chose it for this product. Measured
# 2026-07-31 on a 682-file repository: the cut landed mid-alphabet at `plugins/`, hid all 275 files
# under `tests/`, and the audit reported "No tests discovered — workspace-wide" as a P1 finding.
# That false finding cost a full second audit run — ~5k cheap input tokens saved against ~6k
# expensive output tokens respent. A ceiling must bind on pathological input, not on ordinary
# repositories; 5000 paths is ~54k tokens and clears essentially every real project.
_LIST_FILES_CEILING = 5000
# The walk itself runs past the reply's cap so the reply can report an accurate total, but a
# runaway tree still needs a floor under it.
_WALK_SCAN_CEILING = 50000
# Search rows. The scan behind these is already complete by default (`workspace_scan_limit` returns
# None unless a caller opts into a bound), so this only ever capped how many HITS came back — and
# 100 hits of a common symbol is a prefix of the answer, not the answer.
_SEARCH_TEXT_CEILING = 1000
# Reading a file: `read_text().splitlines()` has already pulled the whole thing into memory before
# the slice, so 160/400 saved no I/O. 43% of this repo's Python files exceed 160 lines and 15%
# exceed 400 — a ceiling a caller could not raise past the size of the files they needed to read.
_READ_FILE_DEFAULT_LINES = 2000
_READ_FILE_CEILING = 50000


def _iter_workspace_files(
    target: Path,
    *,
    workspace_root: Path,
    glob_pattern: str,
    limit: int,
) -> tuple[list[Path], bool]:
    return iter_workspace_files_with_status(
        target,
        workspace_root=workspace_root,
        glob_pattern=glob_pattern,
        limit=limit,
    )


def _is_probably_text(path: Path) -> bool:
    try:
        sample = path.read_bytes()[:4096]
    except Exception:
        return False
    return b"\x00" not in sample


def _list_files(arguments: dict[str, Any], *, workspace_root: Path) -> RuntimeExecutionResult:
    target = _resolve_workspace_path(arguments.get("path"), workspace_root=workspace_root)
    limit = max(1, min(int(arguments.get("limit") or 200), _LIST_FILES_CEILING))
    glob_pattern = str(arguments.get("glob") or "**/*").strip() or "**/*"
    if target.is_file():
        matched, scan_truncated = ([target], False)
    else:
        # Walk past the reply's own cap. `sorted(rglob("*"))` has already materialised and sorted
        # the entire tree before any cap can apply, so capping the WALK saves no I/O — it only
        # throws away the total, and the total is what lets the reply say how much it dropped.
        matched, scan_truncated = _iter_workspace_files(
            target,
            workspace_root=workspace_root,
            glob_pattern=glob_pattern,
            limit=_WALK_SCAN_CEILING,
        )
    rows = matched[:limit]
    dropped = len(matched) - len(rows)
    truncated = bool(dropped) or scan_truncated
    if not rows:
        status = "truncated_no_results" if truncated else "no_results"
        return RuntimeExecutionResult(
            handled=True,
            ok=True,
            status=status,
            response_text=(
                f"No files matched inside `{_relative_path(target, workspace_root=workspace_root)}`"
                + (" within the first scan budget; the search was truncated." if truncated else ".")
            ),
            details={
                "path": _relative_path(target, workspace_root=workspace_root),
                "truncated": truncated,
                "observation": _tool_observation(
                    intent="workspace.list_files",
                    tool_surface="workspace",
                    ok=True,
                    status=status,
                    path=_relative_path(target, workspace_root=workspace_root),
                    paths=[],
                    count=0,
                    truncated=truncated,
                ),
            },
        )
    relative_rows = [_relative_path(path, workspace_root=workspace_root) for path in rows]
    lines = [f"Workspace files under `{_relative_path(target, workspace_root=workspace_root)}`:"]
    for relative in relative_rows:
        lines.append(f"- {relative}")
    if truncated:
        # Say what was lost, in the reply body. A `truncated` boolean buried in `details` is not
        # read by the model that then writes "no tests in this repo" off the surviving prefix. The
        # walk is alphabetical, so what gets cut is never a sample of the tree — it is its tail.
        total = len(matched)
        lines.append(
            f"\n…and {dropped} more file(s) not listed ({total} eligible in total, listed alphabetically). "
            "This listing is a prefix, not the whole tree: files sorting after the last entry above "
            "are missing from it. It cannot support any conclusion that something is ABSENT from "
            "this project — re-run with a higher `limit`, or check the specific path directly."
        )
    return RuntimeExecutionResult(
        handled=True,
        ok=True,
        status="truncated" if truncated else "executed",
        response_text="\n".join(lines),
        details={
            "path": _relative_path(target, workspace_root=workspace_root),
            "count": len(relative_rows),
            "total": len(matched),
            "dropped": dropped,
            "paths": relative_rows,
            "truncated": truncated,
            "observation": _tool_observation(
                intent="workspace.list_files",
                tool_surface="workspace",
                ok=True,
                status="truncated" if truncated else "executed",
                path=_relative_path(target, workspace_root=workspace_root),
                count=len(relative_rows),
                # `total` and `dropped` travel with the observation, not just in `details`. The
                # observation is what the answering model is handed; a disclosure that stops at
                # `details` is a disclosure nobody reads, which is how a 200-of-682 listing became
                # "no tests in this repo".
                total=len(matched),
                dropped=dropped,
                paths=relative_rows,
                truncated=truncated,
            ),
        },
    )


def _dir_identity(path: Path) -> tuple[int, int] | None:
    try:
        info = path.stat()
    except OSError:
        return None
    return (info.st_dev, info.st_ino)


def _same_directory(a: Path, b: Path) -> bool:
    """Whether `a` and `b` name the same directory, by canonical filesystem identity rather than
    string/Path equality. ANVIL F4, 2026-08-07: a raw-path comparison misses a symlinked
    workspace root and macOS firmlink aliases (`/Users/...` vs `/System/Volumes/Data/Users/...`,
    which `Path.resolve()` does NOT collapse -- the existing `(st_dev, st_ino)` identity check
    used by `_machine_find_folder`/`_machine_find_file` for the same reason is reused here)."""
    identity_a, identity_b = _dir_identity(a), _dir_identity(b)
    if identity_a is not None and identity_b is not None:
        return identity_a == identity_b
    return a == b


def _is_ancestor_or_self(candidate: Path, of: Path) -> bool:
    """Whether `candidate` is `of` itself, or an ancestor directory of it -- by canonical
    identity, so a nested bound project (`Desktop/group/project-c`) correctly recognizes
    `Desktop/group` as on its own lineage, not just its immediate parent."""
    node = of
    while True:
        if _same_directory(candidate, node):
            return True
        parent = node.parent
        if _same_directory(parent, node):
            return False
        node = parent


def _has_git_marker(path: Path) -> bool:
    """Whether `path` looks like a git project root (a `.git` directory, or a `.git` FILE --
    a worktree). Guarded (ANVIL R4, round 3): `.exists()` on an unreadable, race-deleted, or
    otherwise unprobeable entry can raise `OSError`, and every caller here scans SIBLING
    entries in a loop -- letting that propagate would abort the whole scan and silently drop
    every entry after the unprobeable one, not just fail to classify that one entry. An
    unprobeable entry is treated as "not a project," same as any other unreadable filesystem
    entry elsewhere in this module -- never as trusted or hidden by default.
    """
    try:
        return (path / ".git").exists()
    except OSError:
        return False


def _is_other_git_project(path: Path, *, workspace_root: Path | None) -> bool:
    """Whether `path` is the root of a DIFFERENT git project than the bound workspace -- "
    different" meaning not on the workspace's own ancestor-or-self lineage. Shared by
    `_sibling_projects_to_hide` (what a listing names) and `_machine_find_folder`/
    `_machine_find_file` (what a search may even walk into).

    `workspace_root=None` means no project is bound (a direct/legacy caller, not a real turn) --
    there is nothing to isolate FROM, so nothing is hidden.
    """
    if workspace_root is None:
        return False
    return _has_git_marker(path) and not _is_ancestor_or_self(path, workspace_root)


def _is_under_other_git_project(path: Path, *, workspace_root: Path | None) -> bool:
    """Whether `path` (file or directory) lives INSIDE some OTHER git project -- not the bound
    workspace's own lineage. Walks `path`'s ancestor chain for the nearest enclosing git marker.

    Unlike `_is_other_git_project` (checked once per candidate during a live BFS that never
    descends past a hidden boundary, so nothing beneath one is ever visited to begin with), this
    is for filtering results a walk ALREADY produced -- `machine.find_largest` (ANVIL R2)
    delegates its walk to `core.machine_diagnostics`, which has no notion of project binding and
    returns full paths regardless of what project they sit inside.
    """
    if workspace_root is None:
        return False
    if _is_ancestor_or_self(workspace_root, path):
        return False
    node = path if path.is_dir() else path.parent
    while True:
        if _has_git_marker(node):
            return True
        parent = node.parent
        if _same_directory(parent, node):
            return False
        node = parent


def _hiding_applies(target: Path, *, workspace_root: Path) -> bool:
    """Whether implicit-discovery hiding is in scope for a `machine.list_directory` on `target`.

    False when `target` is the bound workspace itself, or something inside it -- browsing your
    own project reveals nothing foreign to hide. Otherwise true when SOME ancestor of `target`,
    up to its enclosing safe machine root, is a strict ancestor of the bound workspace -- i.e.
    `target` is reachable from a directory genuinely on the way to (or beside) the bound project,
    even if `target` itself is one step further over through a non-project container.

    ANVIL R3, round 3: the first version of this check only asked whether `target` ITSELF was a
    strict ancestor of the bound workspace. That protected `Desktop` (an ancestor of
    `Desktop/container/project-a`) but not `Desktop/container/sub` -- a SIBLING of the bound
    project's own container, one level further over, not on `target`'s literal ancestor chain --
    so listing `container` correctly hid a sibling project sitting directly in it, but listing
    `container/sub` hid nothing, and whatever sibling project lived there became fully reachable
    and readable. Walking target's OWN ancestor chain looking for a protected ancestor closes
    that gap without needing to re-derive the same check for every nesting depth by hand.

    Bounded at the safe-root boundary (Desktop/Downloads/Documents) so a workspace bound
    somewhere entirely unrelated to those roots does not trivially "share" the filesystem root
    with every listing there and hide everything -- see `_safe_machine_roots`.
    """
    if _is_ancestor_or_self(workspace_root, target):
        return False
    safe_roots = _safe_machine_roots()
    node = target
    while True:
        if _is_ancestor_or_self(node, workspace_root):
            return True
        if any(_same_directory(node, root) for root in safe_roots):
            return False
        parent = node.parent
        if _same_directory(parent, node):
            return False
        node = parent


def _sibling_projects_to_hide(target: Path, *, workspace_root: Path) -> set[str]:
    """Names, directly under `target`, of OTHER git projects on the way to (or beside) the bound
    workspace.

    Found 2026-08-07: a chat bound to one project could `machine.list_directory` its own parent
    folder (typically ~/Desktop) and see every sibling repository sitting next to it, purely
    because Desktop/Downloads/Documents are globally "safe" roots -- a project binding bought no
    read isolation one directory up. See `_hiding_applies` for when this fires at all. A direct
    child that is itself another project's root (`.git` present) is hidden UNLESS that child is
    on the workspace's own lineage. A caller who names a sibling project's own path explicitly
    (`~/Desktop/other-project`) is asking a different, allowed question and lists it in full --
    see `_list_machine_directory`.
    """
    if not _hiding_applies(target, workspace_root=workspace_root):
        return set()
    hidden: set[str] = set()
    for child in target.iterdir():
        if not child.is_dir() or _is_ancestor_or_self(child, workspace_root):
            continue
        if _has_git_marker(child):
            hidden.add(child.name)
    return hidden


def _list_machine_directory(
    arguments: dict[str, Any], *, workspace_root: Path
) -> RuntimeExecutionResult:
    try:
        target = _resolve_machine_directory(arguments.get("path"))
    except ValueError:
        allowed_roots = [_home_relative_label(root) for root in _safe_machine_roots()]
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="not_allowed",
            response_text=f"I can only inspect safe local directories in this lane: {', '.join(allowed_roots)}.",
            details={
                "observation": _tool_observation(
                    intent="machine.list_directory",
                    tool_surface="machine",
                    ok=False,
                    status="not_allowed",
                    allowed_roots=allowed_roots,
                ),
            },
        )
    if target.is_file():
        # "does not exist" about a file that plainly does exist is the worst thing this tool can
        # say. Measured on the deployed build 2026-07-30: "show me whats in
        # ~/Desktop/vool-acceptance-probe/haiku.txt" and "print the contents of
        # .../nested/deep.txt" were both routed here and both answered "Local directory ... does
        # not exist" about files sitting on the Desktop. Routing is fixed upstream; this says the
        # true thing if anything ever lands here again.
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="not_a_directory",
            response_text=(
                f"`{_home_relative_label(target)}` is a file, not a directory. "
                "Ask me to read it and I will."
            ),
            details={
                "path": _home_relative_label(target),
                "observation": _tool_observation(
                    intent="machine.list_directory",
                    tool_surface="machine",
                    ok=False,
                    status="not_a_directory",
                    path=_home_relative_label(target),
                ),
            },
        )
    if not target.exists() or not target.is_dir():
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="not_found",
            response_text=f"Local directory `{_home_relative_label(target)}` does not exist.",
            details={
                "path": _home_relative_label(target),
                "observation": _tool_observation(
                    intent="machine.list_directory",
                    tool_surface="machine",
                    ok=False,
                    status="not_found",
                    path=_home_relative_label(target),
                ),
            },
        )
    directories_only = bool(arguments.get("directories_only", False))
    limit = max(1, min(int(arguments.get("limit") or 50), 200))
    hidden_names = _sibling_projects_to_hide(target, workspace_root=workspace_root)
    hidden_count = len(hidden_names)
    hidden_sentence = (
        f"{hidden_count} sibling project folder(s) next to the active workspace were left out of "
        "this listing -- ask for that path by name, e.g. `~/Desktop/<name>`, to see it."
        if hidden_count
        else ""
    )
    hidden_note = f" ({hidden_sentence})" if hidden_sentence else ""
    # Collect everything that matches BEFORE applying the limit. The loop used to stop at the limit,
    # so a directory with more entries than that was reported as if the page were the whole thing:
    # "just the folders in ~/Desktop please" printed 50 of 81 folders, ended mid-alphabet at "pdf
    # compress/", carried status "executed" and count 50, and said nothing about the 31 it dropped.
    # Measured on the deployed build 2026-07-30. A list that silently stops is a wrong answer, not
    # a short one.
    matched: list[dict[str, Any]] = []
    for child in sorted(target.iterdir(), key=lambda item: item.name.lower()):
        if child.name.startswith(".") or child.name in hidden_names:
            continue
        if directories_only and not child.is_dir():
            continue
        matched.append(
            {
                "name": child.name,
                "path": _home_relative_label(child),
                "type": "directory" if child.is_dir() else "file",
            }
        )
    entries = matched[:limit]
    dropped = len(matched) - len(entries)
    if not entries:
        noun = "folders" if directories_only else "entries"
        return RuntimeExecutionResult(
            handled=True,
            ok=True,
            status="no_results",
            response_text=f"No visible {noun} matched in `{_home_relative_label(target)}`.{hidden_note}",
            details={
                "path": _home_relative_label(target),
                "count": 0,
                "entries": [],
                "directories_only": directories_only,
                "hidden_sibling_projects": hidden_count,
                "observation": _tool_observation(
                    intent="machine.list_directory",
                    tool_surface="machine",
                    ok=True,
                    status="no_results",
                    path=_home_relative_label(target),
                    count=0,
                    directories_only=directories_only,
                    entries=[],
                    hidden_sibling_projects=hidden_count,
                ),
            },
        )
    label = "Visible folders" if directories_only else "Visible entries"
    if bool(arguments.get("count_only", False)):
        # "how many files are in ~/Downloads" was answered with the first thirty filenames and a
        # bare "-...", so the number it asked for was nowhere in the reply and could not be
        # recovered from it. Count everything, not just the page we would have printed.
        folders = sum(
            1
            for child in target.iterdir()
            if not child.name.startswith(".") and child.name not in hidden_names and child.is_dir()
        )
        total = sum(
            1
            for child in target.iterdir()
            if not child.name.startswith(".") and child.name not in hidden_names
        )
        files = total - folders
        counted = folders if directories_only else total
        noun = "visible folders" if directories_only else "visible entries"
        detail = "" if directories_only else f" ({folders} folders, {files} files)"
        return RuntimeExecutionResult(
            handled=True,
            ok=True,
            status="executed",
            response_text=f"`{_home_relative_label(target)}` holds {counted} {noun}{detail}.{hidden_note}",
            details={
                "path": _home_relative_label(target),
                "count": counted,
                "entries": entries,
                "directories_only": directories_only,
                "hidden_sibling_projects": hidden_count,
                "observation": _tool_observation(
                    intent="machine.list_directory",
                    tool_surface="machine",
                    ok=True,
                    status="executed",
                    path=_home_relative_label(target),
                    count=counted,
                    directories_only=directories_only,
                    entries=entries,
                    hidden_sibling_projects=hidden_count,
                ),
            },
        )
    lines = [f"{label} under `{_home_relative_label(target)}`:"]
    for entry in entries:
        suffix = "/" if entry["type"] == "directory" else ""
        lines.append(f"- {entry['name']}{suffix}")
    if dropped:
        noun = "folders" if directories_only else "entries"
        lines.append(f"…and {dropped} more {noun} ({len(matched)} in total).")
    if hidden_sentence:
        lines.append(hidden_sentence)
    return RuntimeExecutionResult(
        handled=True,
        ok=True,
        status="truncated" if dropped else "executed",
        response_text="\n".join(lines),
        details={
            "path": _home_relative_label(target),
            "count": len(entries),
            "total": len(matched),
            "truncated": bool(dropped),
            "entries": entries,
            "directories_only": directories_only,
            "hidden_sibling_projects": hidden_count,
            "observation": _tool_observation(
                intent="machine.list_directory",
                tool_surface="machine",
                ok=True,
                status="truncated" if dropped else "executed",
                path=_home_relative_label(target),
                count=len(entries),
                total=len(matched),
                hidden_sibling_projects=hidden_count,
                truncated=bool(dropped),
                directories_only=directories_only,
                entries=entries,
            ),
        },
    )


def _read_machine_file(arguments: dict[str, Any]) -> RuntimeExecutionResult:
    if not policy_engine.get("filesystem.allow_read_workspace", True):
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="disabled",
            response_text="Safe local file reads are disabled by policy.",
            details={
                "observation": _tool_observation(
                    intent="machine.read_file",
                    tool_surface="machine",
                    ok=False,
                    status="disabled",
                ),
            },
        )
    try:
        target = _resolve_machine_directory(arguments.get("path"))
    except ValueError as exc:
        if str(exc).startswith("protected_runtime_state"):
            return RuntimeExecutionResult(
                handled=True,
                ok=False,
                status="not_allowed",
                response_text=str(exc),
                details={
                    "observation": _tool_observation(
                        intent="machine.read_file", tool_surface="machine", ok=False, status="not_allowed", reason="protected_runtime_state",
                    ),
                },
            )
        allowed_roots = [_home_relative_label(root) for root in _safe_machine_roots()]
        # Echo the requested path: "that path" forced the reader to match the refusal back to
        # their own message (matrix §6, acceptance turn 16). Bounded, and the path is the
        # user's own text restated, never new information.
        requested = " ".join(str(arguments.get("path") or "").split())[:200]
        if requested:
            try:
                requested_label = _home_relative_label(Path(requested))
            except Exception:
                requested_label = requested
        else:
            requested_label = "that path"
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="not_allowed",
            response_text=(
                f"I cannot read `{requested_label}` in this lane. "
                f"I can only read files inside: {', '.join(allowed_roots)}. "
                "For repo or project paths, use workspace.read_file against the active workspace root."
            ),
            details={
                "observation": _tool_observation(
                    intent="machine.read_file",
                    tool_surface="machine",
                    ok=False,
                    status="not_allowed",
                    allowed_roots=allowed_roots,
                    requested_path=requested_label,
                ),
            },
        )
    if not target.exists() or not target.is_file():
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="not_found",
            response_text=f"Local file `{_home_relative_label(target)}` does not exist.",
            details={
                "path": _home_relative_label(target),
                "observation": _tool_observation(
                    intent="machine.read_file",
                    tool_surface="machine",
                    ok=False,
                    status="not_found",
                    path=_home_relative_label(target),
                ),
            },
        )
    if not _is_probably_text(target):
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="binary_file",
            response_text=f"Local file `{_home_relative_label(target)}` does not look like readable text.",
            details={
                "path": _home_relative_label(target),
                "observation": _tool_observation(
                    intent="machine.read_file",
                    tool_surface="machine",
                    ok=False,
                    status="binary_file",
                    path=_home_relative_label(target),
                ),
            },
        )
    start_line = max(1, int(arguments.get("start_line") or 1))
    max_lines = max(1, min(int(arguments.get("max_lines") or _READ_FILE_DEFAULT_LINES), _READ_FILE_CEILING))
    verbatim = bool(arguments.get("verbatim", False))
    full_text = target.read_text(encoding="utf-8", errors="replace")
    content_hash = content_sha256(full_text)
    content = full_text.splitlines()
    chunk = content[start_line - 1 : start_line - 1 + max_lines]
    # Same contract as workspace.read_file: the read has already happened, so the slice hides size
    # rather than saving work. A partial read that reports `executed` with no total is a fragment
    # a model cannot distinguish from a whole file.
    total_lines = len(content)
    truncated = (start_line - 1 + len(chunk)) < total_lines
    if not chunk:
        return RuntimeExecutionResult(
            handled=True,
            ok=True,
            status="empty_slice",
            response_text=f"Local file `{_home_relative_label(target)}` has no lines in that range.",
            details={
                "path": _home_relative_label(target),
                "start_line": start_line,
                "line_count": 0,
                "lines": [],
                "hash": content_hash,
                "observation": _tool_observation(
                    intent="machine.read_file",
                    tool_surface="machine",
                    ok=True,
                    status="empty_slice",
                    path=_home_relative_label(target),
                    start_line=start_line,
                    line_count=0,
                    lines=[],
                    verbatim=verbatim,
                    hash=content_hash,
                ),
            },
        )
    numbered = [f"{start_line + offset}: {line}" for offset, line in enumerate(chunk)]
    line_rows = [
        {"line_number": start_line + offset, "text": line}
        for offset, line in enumerate(chunk)
    ]
    rendered_body = "\n".join(chunk) if verbatim else "\n".join(numbered)
    response_text = (
        rendered_body
        if verbatim
        else f"Local file `{_home_relative_label(target)}`:\n" + rendered_body
    ) + (
        f"\n\n…lines {start_line + len(chunk)}-{total_lines} not shown "
        f"({total_lines} lines in total). This is a fragment; it cannot support any conclusion "
        f"about what the file does NOT contain. Re-read from line {start_line + len(chunk)} to continue."
        if truncated
        else ""
    )
    return RuntimeExecutionResult(
        handled=True,
        ok=True,
        status="truncated" if truncated else "executed",
        response_text=response_text,
        details={
            "path": _home_relative_label(target),
            "start_line": start_line,
            "line_count": len(chunk),
            "total_lines": total_lines,
            "truncated": truncated,
            "lines": line_rows,
            "verbatim": verbatim,
            "hash": content_hash,
            "observation": _tool_observation(
                intent="machine.read_file",
                tool_surface="machine",
                ok=True,
                status="truncated" if truncated else "executed",
                path=_home_relative_label(target),
                start_line=start_line,
                line_count=len(chunk),
                total_lines=total_lines,
                truncated=truncated,
                lines=line_rows,
                verbatim=verbatim,
                hash=content_hash,
            ),
        },
    )


def _machine_os_details() -> tuple[str, str]:
    system = str(platform.system() or "").strip()
    if system == "Darwin":
        version = str(platform.mac_ver()[0] or "").strip() or str(platform.release() or "").strip()
        return "macOS", version
    if system == "Windows":
        return "Windows", str(platform.version() or platform.release() or "").strip()
    if system:
        return system, str(platform.release() or "").strip()
    return "Unknown", ""


def _machine_chip_name() -> str:
    system = str(platform.system() or "").strip().lower()
    try:
        if system == "darwin":
            completed = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            brand = str(completed.stdout or "").strip()
            if brand:
                return brand
    except Exception:
        pass
    processor = str(platform.processor() or "").strip()
    if processor:
        return processor
    machine = str(platform.machine() or "").strip()
    if machine:
        return machine
    return "unknown"


def _inspect_machine_specs() -> RuntimeExecutionResult:
    probe = probe_machine()
    recommendation_summary = install_recommendation_machine_summary(probe=probe)
    # Last-resort default if the summary carries no model: qwen2.5:7b fits 7-8GB GPUs and CPU,
    # so the emergency default is not the oversized qwen3:8b (partial-offloads on 6-10GB VRAM).
    recommended_model = str(recommendation_summary.get("ollama_model") or "").strip() or "qwen2.5:7b"
    recommended_bundle_models = tuple(
        str(item).strip()
        for item in recommendation_summary.get("recommended_bundle_models") or ()
        if str(item).strip()
    )
    selected_tier = str(recommendation_summary.get("selected_tier") or "").strip()
    capacity_bucket = str(recommendation_summary.get("capacity_bucket") or "").strip()
    os_name, os_version = _machine_os_details()
    chip_name = _machine_chip_name()
    display = _machine_display_details()
    response_lines = [
        "Machine specs for this host:",
        f"- OS: {os_name}{f' {os_version}' if os_version else ''}",
        f"- Chip: {chip_name}",
        f"- CPU cores: {probe.cpu_cores}",
        f"- RAM: {round(probe.ram_gb, 1)} GiB",
        f"- Accelerator: {probe.accelerator or 'cpu'}",
        f"- GPU: {probe.gpu_name or 'none'}",
    ]
    accelerator = str(probe.accelerator or "").strip().lower()
    accelerator_status = str(getattr(probe, "accelerator_status", "") or "").strip()
    if not accelerator_status:
        accelerator_status = "cpu" if accelerator == "cpu" else "usable"
    accelerator_advice = str(getattr(probe, "accelerator_advice", "") or "").strip()
    if accelerator_status and accelerator_status not in {"usable", "cpu"}:
        response_lines.append(f"- Accelerator status: {accelerator_status}")
    if accelerator_advice:
        response_lines.append(f"- Accelerator advice: {accelerator_advice}")
    if probe.vram_gb is not None:
        label = "Unified memory" if str(probe.accelerator or "").strip().lower() == "mps" else "VRAM"
        response_lines.append(f"- {label}: {round(probe.vram_gb, 1)} GiB")
    if str(display.get("name") or "").strip():
        response_lines.append(f"- Display: {display['name']}")
    if str(display.get("native_resolution") or "").strip():
        response_lines.append(f"- Native display resolution: {display['native_resolution']}")
    if str(display.get("current_resolution") or "").strip():
        response_lines.append(f"- Current display mode: {display['current_resolution']}")
    if str(display.get("screen_size") or "").strip():
        response_lines.append(f"- Screen size: {display['screen_size']}")
    response_lines.append(f"- Recommended local model: {recommended_model}")
    if recommended_bundle_models:
        response_lines.append(f"- Recommended local bundle: {', '.join(recommended_bundle_models)}")
    observation = _tool_observation(
        intent="machine.inspect_specs",
        tool_surface="machine",
        ok=True,
        status="executed",
        chip_name=chip_name,
        os_name=os_name,
        os_version=os_version,
        cpu_cores=probe.cpu_cores,
        ram_gb=round(probe.ram_gb, 1),
        gpu_name=probe.gpu_name or "",
        vram_gb=round(probe.vram_gb, 1) if probe.vram_gb is not None else None,
        accelerator=probe.accelerator,
        accelerator_status=accelerator_status,
        accelerator_advice=accelerator_advice,
        recommended_model=recommended_model,
        selected_tier=selected_tier,
        capacity_bucket=capacity_bucket,
        recommended_bundle_models=list(recommended_bundle_models),
        display_name=str(display.get("name") or "").strip(),
        display_native_resolution=str(display.get("native_resolution") or "").strip(),
        display_current_resolution=str(display.get("current_resolution") or "").strip(),
        screen_size=str(display.get("screen_size") or "").strip(),
    )
    return RuntimeExecutionResult(
        handled=True,
        ok=True,
        status="executed",
        response_text="\n".join(response_lines),
        details={
            "chip_name": chip_name,
            "os_name": os_name,
            "os_version": os_version,
            "cpu_cores": probe.cpu_cores,
            "ram_gb": round(probe.ram_gb, 1),
            "gpu_name": probe.gpu_name or "",
            "vram_gb": round(probe.vram_gb, 1) if probe.vram_gb is not None else None,
            "accelerator": probe.accelerator,
            "accelerator_status": accelerator_status,
            "accelerator_advice": accelerator_advice,
            "recommended_model": recommended_model,
            "selected_tier": selected_tier,
            "capacity_bucket": capacity_bucket,
            "recommended_bundle_models": list(recommended_bundle_models),
            "display_name": str(display.get("name") or "").strip(),
            "display_native_resolution": str(display.get("native_resolution") or "").strip(),
            "display_current_resolution": str(display.get("current_resolution") or "").strip(),
            "screen_size": str(display.get("screen_size") or "").strip(),
            "observation": observation,
        },
    )


def _machine_display_details() -> dict[str, str]:
    if str(platform.system() or "").strip().lower() != "darwin":
        return {}
    try:
        completed = subprocess.run(
            ["system_profiler", "SPDisplaysDataType", "-json"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        if int(completed.returncode or 0) != 0:
            return {}
        payload = json.loads(str(completed.stdout or "").strip() or "{}")
    except Exception:
        return {}
    for gpu in list(payload.get("SPDisplaysDataType") or []):
        for display in list((gpu or {}).get("spdisplays_ndrvs") or []):
            if not isinstance(display, dict):
                continue
            name = str(display.get("_name") or "").strip()
            native_resolution = str(
                display.get("_spdisplays_pixels")
                or display.get("spdisplays_pixelresolution")
                or ""
            ).strip()
            current_resolution = str(display.get("spdisplays_resolution") or "").strip()
            screen_size = ""
            if name.lower() == "imac" and native_resolution == "4480 x 2520":
                screen_size = "24-inch (inferred from Apple 4.5K iMac panel)"
            return {
                "name": name,
                "native_resolution": native_resolution,
                "current_resolution": current_resolution,
                "screen_size": screen_size,
            }
    return {}


def _ensure_machine_directory(arguments: dict[str, Any]) -> RuntimeExecutionResult:
    if not policy_engine.get("filesystem.allow_write_workspace", False):
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="disabled",
            response_text="Safe local machine writes are disabled by policy.",
            details={
                "observation": _tool_observation(
                    intent="machine.ensure_directory",
                    tool_surface="machine",
                    ok=False,
                    status="disabled",
                ),
            },
        )
    try:
        target = _resolve_machine_directory(arguments.get("path"), write=True)
    except ValueError as exc:
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="not_allowed",
            response_text=str(exc).replace("inspect safe local directories in", "write inside"),
            details={
                "observation": _tool_observation(
                    intent="machine.ensure_directory",
                    tool_surface="machine",
                    ok=False,
                    status="not_allowed",
                    allowed_roots=[_home_relative_label(root) for root in _safe_machine_roots()],
                ),
            },
        )
    already_present = target.exists()
    target.mkdir(parents=True, exist_ok=True)
    status = "already_exists" if already_present else "executed"
    action = "already_present" if already_present else "created"
    return RuntimeExecutionResult(
        handled=True,
        ok=True,
        status=status,
        response_text=(
            f"Directory `{_home_relative_label(target)}` already existed."
            if already_present
            else f"Created directory `{_home_relative_label(target)}`."
        ),
        details={
            "path": _home_relative_label(target),
            "action": action,
            "already_present": already_present,
            "observation": _tool_observation(
                intent="machine.ensure_directory",
                tool_surface="machine",
                ok=True,
                status=status,
                path=_home_relative_label(target),
                action=action,
                already_present=already_present,
            ),
        },
    )


def _write_machine_file(
    arguments: dict[str, Any], *, reviewed_destination: dict[str, Any] | None = None
) -> RuntimeExecutionResult:
    if not policy_engine.get("filesystem.allow_write_workspace", False):
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="disabled",
            response_text="Safe local machine writes are disabled by policy.",
            details={
                "observation": _tool_observation(
                    intent="machine.write_file",
                    tool_surface="machine",
                    ok=False,
                    status="disabled",
                ),
            },
        )
    try:
        target = _resolve_machine_directory(arguments.get("path"), write=True)
    except ValueError as exc:
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="not_allowed",
            response_text=str(exc).replace("inspect safe local directories in", "write inside"),
            details={
                "observation": _tool_observation(
                    intent="machine.write_file",
                    tool_surface="machine",
                    ok=False,
                    status="not_allowed",
                    allowed_roots=[_home_relative_label(root) for root in _safe_machine_roots()],
                ),
            },
        )
    # ── Execution authorization ────────────────────────────────────────
    # The canonical permission boundary: ExecutionGate decides whether this
    # typed effect on the exact resolved resource may proceed.
    # This check binds to the canonical path, not a display alias or
    # caller-supplied flag.
    _gate_decision = ExecutionGate.evaluate_machine_effect(
        effect_type="machine.write_file",
        resolved_path=str(target),
    )
    if _gate_decision.get("decision") != "authorized":
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="blocked",
            response_text=(
                f"Machine write to `{_home_relative_label(target)}` "
                f"was not authorized by execution policy."
            ),
            details={
                "reason": _gate_decision.get("reason", ""),
                "observation": _tool_observation(
                    intent="machine.write_file",
                    tool_surface="machine",
                    ok=False,
                    status="blocked",
                    reason=_gate_decision.get("reason", ""),
                ),
            },
        )
    # ── Physical mutation ──────────────────────────────────────────────
    refusal = _reviewed_destination_refusal(
        "machine.write_file", surface="machine", record=reviewed_destination, target=target,
        label=_home_relative_label(target),
    )
    if refusal is not None:
        return refusal
    content = str(arguments.get("content") or "")
    target.parent.mkdir(parents=True, exist_ok=True)
    already_present = target.exists()
    expected_hash = str(arguments.get("expected_hash") or "").strip().lower()
    if already_present and expected_hash:
        # A claimed prior-content hash is ENFORCED at the physical mutation: the write
        # refuses when the file on disk is not the bytes the caller claimed to replace.
        # Without this the hash would be classification evidence the mutation never checks.
        import hashlib

        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        if actual != expected_hash:
            return RuntimeExecutionResult(
                handled=True,
                ok=False,
                status="stale_content",
                response_text=(
                    "The file on disk is not the content this write claimed to replace "
                    "(expected_hash mismatch); nothing was written."
                ),
                details={
                    "observation": _tool_observation(
                        intent="machine.write_file",
                        tool_surface="machine",
                        ok=False,
                        status="stale_content",
                    ),
                },
            )
    if reviewed_destination is not None:
        # A reviewed destination is written inside its pinned parent directory, with its reviewed bytes
        # re-read there just before the rename (`pinned_atomic_write_text`).
        from core.execution.artifacts import DestinationChangedError, ReviewedBaseChangedError, pinned_atomic_write_text

        try:
            pinned_atomic_write_text(target, content, expected_prior_sha256=str(reviewed_destination.get("prior_sha256") or ""))
        except ReviewedBaseChangedError as exc:
            return _reviewed_destination_result(
                "machine.write_file", surface="machine", status="stale_base", label=_home_relative_label(target),
                reason="changed while it was being written", detail=str(exc),
            )
        except DestinationChangedError as exc:
            return _reviewed_destination_result(
                "machine.write_file", surface="machine", status="destination_changed", label=_home_relative_label(target),
                reason="changed while it was being written", detail=str(exc),
            )
    else:
        target.write_text(content, encoding="utf-8")
    action = "updated" if already_present else "created"
    status = "updated" if already_present else "executed"
    line_count = len(content.splitlines()) if content else 0
    return RuntimeExecutionResult(
        handled=True,
        ok=True,
        status=status,
        response_text=(
            f"Updated file `{_home_relative_label(target)}`."
            if already_present
            else f"Created file `{_home_relative_label(target)}`."
        ),
        details={
            "path": _home_relative_label(target),
            "line_count": line_count,
            "action": action,
            "observation": _tool_observation(
                intent="machine.write_file",
                tool_surface="machine",
                ok=True,
                status=status,
                path=_home_relative_label(target),
                line_count=line_count,
                action=action,
            ),
        },
    )


def _list_tree(arguments: dict[str, Any], *, workspace_root: Path) -> RuntimeExecutionResult:
    payload = list_tree_workspace(arguments, workspace_root=workspace_root)
    details = dict(payload.get("details") or {})
    details["observation"] = _tool_observation(
        intent="workspace.list_tree",
        tool_surface="workspace",
        ok=bool(payload.get("ok")),
        status=str(payload.get("status") or ""),
        path=str(details.get("path") or "").strip(),
        entries=list(details.get("entries") or []),
        truncated=bool(details.get("truncated", False)),
    )
    return _result_from_payload(
        ok=bool(payload.get("ok")),
        status=str(payload.get("status") or ""),
        response_text=str(payload.get("response_text") or ""),
        details=details,
    )


def _search_text(arguments: dict[str, Any], *, workspace_root: Path) -> RuntimeExecutionResult:
    query = str(arguments.get("query") or "").strip()
    if not query:
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="invalid_arguments",
            response_text="workspace.search_text needs a non-empty `query`.",
            details={
                "observation": _tool_observation(
                    intent="workspace.search_text",
                    tool_surface="workspace",
                    ok=False,
                    status="invalid_arguments",
                ),
            },
        )
    try:
        target = _resolve_workspace_path(arguments.get("path"), workspace_root=workspace_root)
    except ValueError as exc:
        raw_path = str(arguments.get("path") or "").strip()
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="scope_violation",
            response_text=str(exc),
            details={
                "path": raw_path,
                "observation": _tool_observation(
                    intent="workspace.search_text",
                    tool_surface="workspace",
                    ok=False,
                    status="scope_violation",
                    path=raw_path,
                ),
            },
        )
    limit = max(1, min(int(arguments.get("limit") or 50), _SEARCH_TEXT_CEILING))
    glob_pattern = str(arguments.get("glob") or "**/*").strip() or "**/*"
    matches: list[str] = []
    match_rows: list[dict[str, Any]] = []
    read_errors: list[str] = []
    lowered = query.lower()
    scan_limit = workspace_scan_limit(arguments)
    scan, scan_truncated = _iter_workspace_files(target, workspace_root=workspace_root, glob_pattern=glob_pattern, limit=scan_limit)
    # Two independent ways this answer can be partial, and only one of them used to be reported.
    # `scan_truncated` is the file-scan budget, which is None by default and therefore almost never
    # fires. Hitting the MATCH limit set nothing at all: status stayed "executed", truncated stayed
    # False, and the reply read as a complete answer. Because the walk is ordered, the first files
    # consume the whole budget, so the rows are an alphabetical prefix rather than a sample — a
    # search for a common symbol returned every hit from `AGENTS.md` and none from the 733 files
    # under `core/`. The conclusion that silently authorises is "this symbol has no other callers".
    match_limit_reached = False
    for path in scan:
        # A single read, not `_is_probably_text()` followed by a separate `path.read_text()`:
        # `_is_probably_text` does its OWN read internally and swallows any exception into a bare
        # `False` ("not text"), so a permission-denied file used to be silently folded into "binary,
        # skip it" before this function's own read attempt -- and its error -- was ever reached. A
        # file that could not be read at all is NOT the same fact as "this file has no match":
        # silently skipping it used to make a scan where every file failed to open indistinguishable
        # from one that read every file cleanly and found nothing, both reporting a confident
        # `no_results` with ok=True.
        try:
            raw_bytes = path.read_bytes()
        except Exception:
            read_errors.append(_relative_path(path, workspace_root=workspace_root))
            continue
        if b"\x00" in raw_bytes[:4096]:
            continue  # genuinely binary, not a read failure -- same sniff `is_probably_text` uses
        try:
            lines = raw_bytes.decode("utf-8", errors="replace").splitlines()
        except Exception:
            read_errors.append(_relative_path(path, workspace_root=workspace_root))
            continue
        for index, line in enumerate(lines, start=1):
            if lowered not in line.lower():
                continue
            relative = _relative_path(path, workspace_root=workspace_root)
            snippet = line.strip()[:220]
            matches.append(f"- {relative}:{index} {snippet}")
            match_rows.append({"path": relative, "line": index, "snippet": snippet})
            if len(matches) >= limit:
                match_limit_reached = True
                break
        if len(matches) >= limit:
            match_limit_reached = True
            break
    truncated = bool(scan_truncated or match_limit_reached)
    if not matches:
        if read_errors:
            # Cannot claim a complete negative when part of the scan never actually happened.
            return RuntimeExecutionResult(
                handled=True,
                ok=False,
                status="search_incomplete",
                response_text=(
                    f'Could not confirm "{query}" has no matches: {len(read_errors)} file(s) could '
                    "not be read during the scan, so this is not a complete search. Unreadable: "
                    + ", ".join(read_errors[:10])
                    + ("…" if len(read_errors) > 10 else "")
                ),
                details={
                    "query": query,
                    "matches": [],
                    "read_errors": read_errors,
                    "truncated": truncated,
                    "scan_limit": scan_limit,
                    "observation": _tool_observation(
                        intent="workspace.search_text",
                        tool_surface="workspace",
                        ok=False,
                        status="search_incomplete",
                        query=query,
                        matches=[],
                        match_count=0,
                        read_errors=read_errors,
                        truncated=truncated,
                    ),
                },
            )
        status = "truncated_no_results" if truncated else "no_results"
        return RuntimeExecutionResult(
            handled=True,
            ok=True,
            status=status,
            response_text=(
                f'No text matches for "{query}" were found in the workspace.'
                + (f" The eligible-file scan was truncated at {scan_limit} files, so this is not a complete negative result." if truncated else "")
            ),
            details={
                "query": query,
                "matches": [],
                "truncated": truncated,
                "scan_limit": scan_limit,
                "observation": _tool_observation(
                    intent="workspace.search_text",
                    tool_surface="workspace",
                    ok=True,
                    status=status,
                    query=query,
                    matches=[],
                    match_count=0,
                    truncated=truncated,
                ),
            },
        )
    return RuntimeExecutionResult(
        handled=True,
        ok=True,
        status="truncated" if truncated else "executed",
        response_text=(
            f'Search matches for "{query}":\n' + "\n".join(matches)
            + (
                f"\n\nTRUNCATED at the {limit}-match limit. The walk is ordered, so these are the "
                "FIRST matches found, not a sample of all of them — files later in the walk were "
                "never reached. This cannot support any conclusion that something has no other "
                "occurrences (for example that a symbol has no other callers). Re-run with a "
                "higher `limit` or a narrower `path` before concluding anything is absent."
                if match_limit_reached
                else ""
            )
            + (
                f"\n\nThe eligible-file scan was truncated at {scan_limit} files."
                if scan_truncated
                else ""
            )
        ),
        details={
            "query": query,
            "match_count": len(match_rows),
            "matches": match_rows,
            "truncated": truncated,
            "match_limit_reached": match_limit_reached,
            "scan_truncated": bool(scan_truncated),
            "match_limit": limit,
            "scan_limit": scan_limit,
            "observation": _tool_observation(
                intent="workspace.search_text",
                tool_surface="workspace",
                ok=True,
                status="truncated" if truncated else "executed",
                query=query,
                match_count=len(match_rows),
                matches=match_rows,
                truncated=truncated,
            ),
        },
    )


def _symbol_search(arguments: dict[str, Any], *, workspace_root: Path) -> RuntimeExecutionResult:
    payload = symbol_search_workspace(arguments, workspace_root=workspace_root)
    details = dict(payload.get("details") or {})
    details["observation"] = _tool_observation(
        intent="workspace.symbol_search",
        tool_surface="workspace",
        ok=bool(payload.get("ok")),
        status=str(payload.get("status") or ""),
        symbol=str(details.get("symbol") or "").strip(),
        match_count=int(details.get("match_count") or len(list(details.get("matches") or []))),
        matches=list(details.get("matches") or []),
        truncated=bool(details.get("truncated", False)),
    )
    return _result_from_payload(
        ok=bool(payload.get("ok")),
        status=str(payload.get("status") or ""),
        response_text=str(payload.get("response_text") or ""),
        details=details,
    )


def _nearby_workspace_matches(name: str, *, workspace_root: Path, limit: int = 5) -> list[str]:
    """Where else in the workspace a file of this NAME lives.

    `_read_file` stats exactly one path. Reporting that single miss as "does not exist" is a claim
    about the whole tree the tool never looked at — measured live, the runtime said
    "File vool_agent.py does not exist" while the file sat at `apps/vool_agent.py`, one directory
    down. A bounded rglob turns a false denial into a useful answer.
    """
    stem = str(name or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not stem:
        return []
    found: list[str] = []
    try:
        for candidate in workspace_root.rglob(stem):
            if not candidate.is_file():
                continue
            parts = {part.lower() for part in candidate.parts}
            if parts & {".git", "node_modules", "__pycache__", "generated", "site-packages"}:
                continue
            found.append(_relative_path(candidate, workspace_root=workspace_root))
            if len(found) >= limit:
                break
    except Exception:
        return []
    return found


def _read_file(arguments: dict[str, Any], *, workspace_root: Path) -> RuntimeExecutionResult:
    try:
        target = _resolve_workspace_path(arguments.get("path"), workspace_root=workspace_root)
    except ValueError as exc:
        # A workspace-escape (traversal, absolute path outside the project, a symlink that now
        # points outside it) used to propagate past this function entirely and get caught by the
        # dispatcher's generic `except Exception`, which reports status="error" -- indistinguishable
        # from any other unexpected failure. A scope violation is not an unexpected failure, it is a
        # specific, expected refusal, and callers need a status they can branch on for it.
        raw_path = str(arguments.get("path") or "").strip()
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="scope_violation",
            response_text=str(exc),
            details={
                "path": raw_path,
                "observation": _tool_observation(
                    intent="workspace.read_file",
                    tool_surface="workspace",
                    ok=False,
                    status="scope_violation",
                    path=raw_path,
                ),
            },
        )
    if target.exists() and not target.is_file():
        # A directory (or another non-regular type: fifo, device, socket) is not "no file here" --
        # something IS there, it is just not a file this tool can read. Folding this into
        # not_found told a caller who passed a directory by mistake that nothing existed at all.
        shown = _relative_path(target, workspace_root=workspace_root)
        kind = "directory" if target.is_dir() else "non-regular file"
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="invalid_target_type",
            response_text=f"`{shown}` is a {kind}, not a file I can read.",
            details={
                "path": shown,
                "target_type": kind,
                "observation": _tool_observation(
                    intent="workspace.read_file",
                    tool_surface="workspace",
                    ok=False,
                    status="invalid_target_type",
                    path=shown,
                    target_type=kind,
                ),
            },
        )
    if not target.exists():
        shown = _relative_path(target, workspace_root=workspace_root)
        elsewhere = _nearby_workspace_matches(
            str(arguments.get("path") or ""), workspace_root=workspace_root
        )
        if elsewhere:
            miss_text = (
                f"There is no file at `{shown}`, but that name exists elsewhere in this project:\n"
                + "\n".join(f"- {path}" for path in elsewhere)
            )
        else:
            miss_text = (
                f"There is no file at `{shown}`, and no file of that name elsewhere in this "
                "project. If it may be outside the project folder, ask me to search your Desktop, "
                "Downloads and Documents for it."
            )
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="not_found",
            response_text=miss_text,
            details={
                "path": _relative_path(target, workspace_root=workspace_root),
                "observation": _tool_observation(
                    intent="workspace.read_file",
                    tool_surface="workspace",
                    ok=False,
                    status="not_found",
                    path=_relative_path(target, workspace_root=workspace_root),
                ),
            },
        )
    if not _is_probably_text(target):
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="binary_file",
            response_text=f"File `{_relative_path(target, workspace_root=workspace_root)}` does not look like readable text.",
            details={
                "path": _relative_path(target, workspace_root=workspace_root),
                "observation": _tool_observation(
                    intent="workspace.read_file",
                    tool_surface="workspace",
                    ok=False,
                    status="binary_file",
                    path=_relative_path(target, workspace_root=workspace_root),
                ),
            },
        )
    start_line = max(1, int(arguments.get("start_line") or 1))
    max_lines = max(1, min(int(arguments.get("max_lines") or _READ_FILE_DEFAULT_LINES), _READ_FILE_CEILING))
    verbatim = bool(arguments.get("verbatim", False))
    raw_bytes = target.read_bytes()
    try:
        full_text = raw_bytes.decode("utf-8")
        decode_lossy = False
    except UnicodeDecodeError:
        # Not valid UTF-8 (Latin-1, another 8-bit encoding, or genuinely corrupt bytes). The old
        # unconditional `errors="replace"` silently swapped every undecodable byte for U+FFFD with
        # no signal anything was lost -- a caller reading `caf<FFFD> r<FFFD>sum<FFFD>` had no way to
        # tell that from a file that actually contains the replacement character. Declaring the
        # fallback is the fix asked for here, not building real encoding detection: this file's
        # actual encoding is still unknown, only that UTF-8 wasn't it.
        full_text = raw_bytes.decode("utf-8", errors="replace")
        decode_lossy = True
    # Hashed over the WHOLE file, not the shown slice: this is what a caller diffs its own belief
    # against, and what write_file/replace_in_file's `expected_hash` precondition compares to. A
    # slice-scoped hash would change every time the caller paged to a different `start_line` for the
    # exact same on-disk file, which is not "current content identity", it's "current view identity".
    content_hash = content_sha256(full_text)
    content = full_text.splitlines()
    chunk = content[start_line - 1 : start_line - 1 + max_lines]
    # The whole file is already in `content`; the slice only decides what to SHOW, so a low ceiling
    # saves no I/O. Without `total_lines` a 160-line file and a 4,620-line file produced identical
    # envelopes, and a model that had seen 3.5% of a module could not tell — which is how a reader
    # concludes "this file has no error handling". A stated total turns an invisible amputation
    # into a paginate-and-continue.
    total_lines = len(content)
    truncated = (start_line - 1 + len(chunk)) < total_lines
    decode_lossy_note = (
        "Note: this file is not valid UTF-8. Undecodable byte(s) were replaced with the U+FFFD "
        "placeholder character below, so the exact original bytes are not shown.\n\n"
        if decode_lossy
        else ""
    )
    if not chunk:
        return RuntimeExecutionResult(
            handled=True,
            ok=True,
            status="empty_slice",
            response_text=decode_lossy_note + f"File `{_relative_path(target, workspace_root=workspace_root)}` has no lines in that range.",
            details={
                "path": _relative_path(target, workspace_root=workspace_root),
                "start_line": start_line,
                "line_count": 0,
                "lines": [],
                "hash": content_hash,
                "decode_lossy": decode_lossy,
                "observation": _tool_observation(
                    intent="workspace.read_file",
                    tool_surface="workspace",
                    ok=True,
                    status="empty_slice",
                    path=_relative_path(target, workspace_root=workspace_root),
                    start_line=start_line,
                    line_count=0,
                    lines=[],
                    hash=content_hash,
                    decode_lossy=decode_lossy,
                ),
            },
        )
    numbered = [f"{start_line + offset}: {line}" for offset, line in enumerate(chunk)]
    line_rows = [
        {"line_number": start_line + offset, "text": line}
        for offset, line in enumerate(chunk)
    ]
    rendered_body = "\n".join(chunk) if verbatim else "\n".join(numbered)
    # Even a verbatim read says so, because verbatim is what the audit uses and a silent
    # partial read is exactly what makes an audit confident about code it never saw.
    tail_note = (
        f"\n\n…lines {start_line + len(chunk)}-{total_lines} not shown "
        f"({total_lines} lines in total). This is a fragment; it cannot support any conclusion "
        f"about what the file does NOT contain. Re-read from line {start_line + len(chunk)} to continue."
        if truncated
        else ""
    )
    response_text = decode_lossy_note + (
        rendered_body
        if verbatim
        else f"File `{_relative_path(target, workspace_root=workspace_root)}`:\n" + rendered_body
    ) + tail_note
    return RuntimeExecutionResult(
        handled=True,
        ok=True,
        status="truncated" if truncated else "executed",
        response_text=response_text,
        details={
            "path": _relative_path(target, workspace_root=workspace_root),
            "start_line": start_line,
            "line_count": len(chunk),
            "total_lines": total_lines,
            "truncated": truncated,
            "lines": line_rows,
            "verbatim": verbatim,
            "hash": content_hash,
            "decode_lossy": decode_lossy,
            "observation": _tool_observation(
                intent="workspace.read_file",
                tool_surface="workspace",
                ok=True,
                status="truncated" if truncated else "executed",
                path=_relative_path(target, workspace_root=workspace_root),
                start_line=start_line,
                line_count=len(chunk),
                total_lines=total_lines,
                truncated=truncated,
                lines=line_rows,
                verbatim=verbatim,
                hash=content_hash,
                decode_lossy=decode_lossy,
            ),
        },
    )


def _ensure_directory(
    arguments: dict[str, Any], *, workspace_root: Path, reviewed_destination: dict[str, Any] | None = None
) -> RuntimeExecutionResult:
    if not policy_engine.get("filesystem.allow_write_workspace", False):
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="disabled",
            response_text="Workspace writes are disabled by policy.",
            details={
                "observation": _tool_observation(
                    intent="workspace.ensure_directory",
                    tool_surface="workspace",
                    ok=False,
                    status="disabled",
                ),
            },
        )
    target = _resolve_workspace_path(arguments.get("path"), workspace_root=workspace_root, write=True)
    relative_path = _relative_path(target, workspace_root=workspace_root)
    refusal = _reviewed_destination_refusal(
        "workspace.ensure_directory", surface="workspace", record=reviewed_destination, target=target, label=relative_path,
    )
    if refusal is not None:
        return refusal
    already_present = target.exists()
    target.mkdir(parents=True, exist_ok=True)
    status = "already_exists" if already_present else "executed"
    action = "confirmed" if already_present else "created"
    return RuntimeExecutionResult(
        handled=True,
        ok=True,
        status=status,
        response_text=(
            f"Directory `{relative_path}` already existed."
            if already_present
            else f"Created directory `{relative_path}`."
        ),
        details={
            "path": relative_path,
            "action": action,
            "already_present": already_present,
            "observation": _tool_observation(
                intent="workspace.ensure_directory",
                tool_surface="workspace",
                ok=True,
                status=status,
                path=relative_path,
                action=action,
                already_present=already_present,
            ),
        },
    )


def _write_file(
    arguments: dict[str, Any],
    *,
    workspace_root: Path,
    session_id: str,
    reviewed_destination: dict[str, Any] | None = None,
) -> RuntimeExecutionResult:
    if not policy_engine.get("filesystem.allow_write_workspace", False):
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="disabled",
            response_text="Workspace writes are disabled by policy.",
            details={
                "observation": _tool_observation(
                    intent="workspace.write_file",
                    tool_surface="workspace",
                    ok=False,
                    status="disabled",
                ),
            },
        )
    target = _resolve_workspace_path(arguments.get("path"), workspace_root=workspace_root, write=True)
    content = str(arguments.get("content") or "")
    existed = target.exists()
    previous_mtime_ns = int(target.stat().st_mtime_ns) if existed and target.is_file() else 0
    previous = target.read_bytes().decode("utf-8", errors="replace") if existed else ""
    previous_hash = content_sha256(previous) if existed else ""
    # Captured BEFORE the write specifically so a rollback can restore the exact pre-mutation mode
    # later, even if something else re-chmods the file (without touching its content) in between
    # the mutation and the rollback -- see rollback_last_workspace_mutation's explicit re-apply.
    previous_mode = (target.stat().st_mode & 0o7777) if existed and target.is_file() else None
    relative_path = _relative_path(target, workspace_root=workspace_root)
    # Optimistic-concurrency precondition: a caller that read the file (or created its plan) before
    # some OTHER writer touched it is working from a stale belief about what's on disk. Without this,
    # `write_file` always wins the race silently, overwriting whatever changed in between with no
    # sign anything was lost. `expected_hash` is optional and omitted entirely by callers that never
    # read the file first (e.g. a brand-new file), so this changes nothing for the common case.
    expected_hash = str(arguments.get("expected_hash") or "").strip()
    if expected_hash and expected_hash != previous_hash:
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="stale_base",
            response_text=(
                f"`{relative_path}` has changed since you last read it "
                f"(expected hash `{expected_hash[:12]}…`, found `{(previous_hash or '(no file)')[:12]}…`). "
                "Re-read the file before writing it again, or omit `expected_hash` to overwrite anyway."
            ),
            details={
                "path": relative_path,
                "expected_hash": expected_hash,
                "current_hash": previous_hash,
                "observation": _tool_observation(
                    intent="workspace.write_file",
                    tool_surface="workspace",
                    ok=False,
                    status="stale_base",
                    path=relative_path,
                    expected_hash=expected_hash,
                    current_hash=previous_hash,
                ),
            },
        )
    refusal = _reviewed_destination_refusal(
        "workspace.write_file", surface="workspace", record=reviewed_destination,
        target=target, label=relative_path,
    )
    if refusal is not None:
        return refusal
    if reviewed_destination is not None:
        # A reviewed destination is written inside its pinned parent directory, with its reviewed bytes
        # re-read there just before the rename (`pinned_atomic_write_text`).
        from core.execution.artifacts import DestinationChangedError, ReviewedBaseChangedError, pinned_atomic_write_text

        try:
            pinned_atomic_write_text(target, content, expected_prior_sha256=str(reviewed_destination.get("prior_sha256") or ""))
        except ReviewedBaseChangedError as exc:
            return _reviewed_destination_result(
                "workspace.write_file", surface="workspace", status="stale_base", label=relative_path,
                reason="changed while it was being written", detail=str(exc),
            )
        except DestinationChangedError as exc:
            return _reviewed_destination_result(
                "workspace.write_file", surface="workspace", status="destination_changed", label=relative_path,
                reason="changed while it was being written", detail=str(exc),
            )
    else:
        atomic_write_text(target, content)
    force_fresh_source_timestamp(target, minimum_mtime_ns=previous_mtime_ns)
    after_hash = content_sha256(content)
    # `atomic_write_text` guarantees the target exists with the right mode once it returns without
    # raising -- this stat is defensive against nothing more than a test double that mocks the
    # write itself without honoring that contract; never fatal on its own account.
    try:
        after_mode = target.stat().st_mode & 0o7777
    except OSError:
        after_mode = None
    line_count = len(content.splitlines()) or (1 if content else 0)
    diff_artifact = build_file_diff_artifact(
        path=relative_path,
        action="updated" if existed else "created",
        before=previous,
        after=content,
        extra={"line_count": line_count},
    )
    mutation_record = record_workspace_mutation(
        session_id=session_id,
        workspace_root=workspace_root,
        intent="workspace.write_file",
        changes=[
            {
                "path": relative_path,
                "action": "updated" if existed else "created",
                "existed_before": existed,
                "existed_after": True,
                "before_text": previous,
                "after_text": content,
                "before_mode": previous_mode,
            }
        ],
    )
    return RuntimeExecutionResult(
        handled=True,
        ok=True,
        status="executed",
        response_text=(
            f"{'Updated' if existed else 'Created'} file `{relative_path}` "
            f"with {line_count} lines."
        ),
        details={
            "path": relative_path,
            "line_count": line_count,
            "action": "updated" if existed else "created",
            "before_hash": previous_hash,
            "after_hash": after_hash,
            "before_mode": previous_mode,
            "after_mode": after_mode,
            "artifacts": [diff_artifact],
            "mutation_record": {
                "mutation_id": str(mutation_record.get("mutation_id") or "").strip(),
                "session_key": str(mutation_record.get("session_key") or "").strip(),
            },
            "observation": _tool_observation(
                intent="workspace.write_file",
                tool_surface="workspace",
                ok=True,
                status="executed",
                path=relative_path,
                line_count=line_count,
                action="updated" if existed else "created",
                before_hash=previous_hash,
                after_hash=after_hash,
                before_mode=previous_mode,
                after_mode=after_mode,
                diff_preview=str(diff_artifact.get("diff_preview") or ""),
            ),
        },
    )


def _replace_in_file(
    arguments: dict[str, Any],
    *,
    workspace_root: Path,
    session_id: str,
    reviewed_destination: dict[str, Any] | None = None,
) -> RuntimeExecutionResult:
    if not policy_engine.get("filesystem.allow_write_workspace", False):
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="disabled",
            response_text="Workspace writes are disabled by policy.",
            details={
                "observation": _tool_observation(
                    intent="workspace.replace_in_file",
                    tool_surface="workspace",
                    ok=False,
                    status="disabled",
                ),
            },
        )
    target = _resolve_workspace_path(arguments.get("path"), workspace_root=workspace_root, write=True)
    if not target.exists() or not target.is_file():
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="not_found",
            response_text=f"File `{_relative_path(target, workspace_root=workspace_root)}` does not exist.",
            details={
                "path": _relative_path(target, workspace_root=workspace_root),
                "observation": _tool_observation(
                    intent="workspace.replace_in_file",
                    tool_surface="workspace",
                    ok=False,
                    status="not_found",
                    path=_relative_path(target, workspace_root=workspace_root),
                ),
            },
        )
    old_text = str(arguments.get("old_text") or "")
    new_text = str(arguments.get("new_text") or "")
    replace_all = bool(arguments.get("replace_all", False))
    if not old_text:
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="invalid_arguments",
            response_text="workspace.replace_in_file needs non-empty `old_text`.",
            details={
                "path": _relative_path(target, workspace_root=workspace_root),
                "observation": _tool_observation(
                    intent="workspace.replace_in_file",
                    tool_surface="workspace",
                    ok=False,
                    status="invalid_arguments",
                    path=_relative_path(target, workspace_root=workspace_root),
                ),
            },
        )
    content = target.read_bytes().decode("utf-8", errors="replace")
    previous_mtime_ns = int(target.stat().st_mtime_ns)
    previous_mode = target.stat().st_mode & 0o7777
    relative_path = _relative_path(target, workspace_root=workspace_root)
    previous_hash = content_sha256(content)
    expected_hash = str(arguments.get("expected_hash") or "").strip()
    if expected_hash and expected_hash != previous_hash:
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="stale_base",
            response_text=(
                f"`{relative_path}` has changed since you last read it "
                f"(expected hash `{expected_hash[:12]}…`, found `{previous_hash[:12]}…`). "
                "Re-read the file before editing it again."
            ),
            details={
                "path": relative_path,
                "expected_hash": expected_hash,
                "current_hash": previous_hash,
                "observation": _tool_observation(
                    intent="workspace.replace_in_file",
                    tool_surface="workspace",
                    ok=False,
                    status="stale_base",
                    path=relative_path,
                    expected_hash=expected_hash,
                    current_hash=previous_hash,
                ),
            },
        )
    occurrences = content.count(old_text)
    if occurrences <= 0:
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="no_match",
            response_text=f"`old_text` was not found in `{relative_path}`.",
            details={
                "path": relative_path,
                "replacements": 0,
                "observation": _tool_observation(
                    intent="workspace.replace_in_file",
                    tool_surface="workspace",
                    ok=False,
                    status="no_match",
                    path=relative_path,
                    replacements=0,
                ),
            },
        )
    # `old_text` matching more than once is ambiguous, not a green light to guess: silently taking
    # "the first occurrence" (the previous behavior) can edit the WRONG one of several identical
    # blocks with no signal to the caller that anything was ambiguous. `replace_all=true` is the
    # explicit opt-in for "yes, all of them" — anything else with more than one match must refuse
    # and ask for more disambiguating context, the same distinction CORE INVARIANT 5 requires of
    # `apply_unified_diff`'s patch engines.
    if occurrences > 1 and not replace_all:
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="ambiguous_match",
            response_text=(
                f"`old_text` matches {occurrences} locations in `{relative_path}`. Include more "
                "surrounding context to select exactly one, or pass `replace_all: true` to replace "
                "every occurrence."
            ),
            details={
                "path": relative_path,
                "replacements": 0,
                "match_count": occurrences,
                "observation": _tool_observation(
                    intent="workspace.replace_in_file",
                    tool_surface="workspace",
                    ok=False,
                    status="ambiguous_match",
                    path=relative_path,
                    replacements=0,
                    match_count=occurrences,
                ),
            },
        )
    if replace_all:
        updated = content.replace(old_text, new_text)
        replaced = occurrences
    else:
        updated = content.replace(old_text, new_text, 1)
        replaced = 1
    refusal = _reviewed_destination_refusal(
        "workspace.replace_in_file", surface="workspace", record=reviewed_destination,
        target=target, label=relative_path,
    )
    if refusal is not None:
        return refusal
    if reviewed_destination is not None:
        # A reviewed destination is written inside its pinned parent directory, with its reviewed bytes
        # re-read there just before the rename (`pinned_atomic_write_text`).
        from core.execution.artifacts import DestinationChangedError, ReviewedBaseChangedError, pinned_atomic_write_text

        try:
            pinned_atomic_write_text(target, updated, expected_prior_sha256=str(reviewed_destination.get("prior_sha256") or ""))
        except ReviewedBaseChangedError as exc:
            return _reviewed_destination_result(
                "workspace.replace_in_file", surface="workspace", status="stale_base", label=relative_path,
                reason="changed while it was being written", detail=str(exc),
            )
        except DestinationChangedError as exc:
            return _reviewed_destination_result(
                "workspace.replace_in_file", surface="workspace", status="destination_changed", label=relative_path,
                reason="changed while it was being written", detail=str(exc),
            )
    else:
        atomic_write_text(target, updated)
    force_fresh_source_timestamp(target, minimum_mtime_ns=previous_mtime_ns)
    after_hash = content_sha256(updated)
    try:
        after_mode = target.stat().st_mode & 0o7777
    except OSError:
        after_mode = None
    diff_artifact = build_file_diff_artifact(
        path=relative_path,
        action="replaced",
        before=content,
        after=updated,
        extra={
            "replacements": replaced,
            "old_text_preview": _truncate(old_text, limit=180),
            "new_text_preview": _truncate(new_text, limit=180),
        },
    )
    mutation_record = record_workspace_mutation(
        session_id=session_id,
        workspace_root=workspace_root,
        intent="workspace.replace_in_file",
        changes=[
            {
                "path": relative_path,
                "action": "replaced",
                "existed_before": True,
                "existed_after": True,
                "before_text": content,
                "after_text": updated,
                "before_mode": previous_mode,
            }
        ],
    )
    return RuntimeExecutionResult(
        handled=True,
        ok=True,
        status="executed",
        response_text=(
            f"Applied {replaced} replacement{'s' if replaced != 1 else ''} in "
            f"`{relative_path}`."
        ),
        details={
            "path": relative_path,
            "replacements": replaced,
            "before_hash": previous_hash,
            "after_hash": after_hash,
            "before_mode": previous_mode,
            "after_mode": after_mode,
            "artifacts": [diff_artifact],
            "mutation_record": {
                "mutation_id": str(mutation_record.get("mutation_id") or "").strip(),
                "session_key": str(mutation_record.get("session_key") or "").strip(),
            },
            "observation": _tool_observation(
                intent="workspace.replace_in_file",
                tool_surface="workspace",
                ok=True,
                status="executed",
                path=relative_path,
                replacements=replaced,
                before_hash=previous_hash,
                after_hash=after_hash,
                before_mode=previous_mode,
                after_mode=after_mode,
                diff_preview=str(diff_artifact.get("diff_preview") or ""),
            ),
        },
    )


def _apply_unified_diff(
    arguments: dict[str, Any],
    *,
    workspace_root: Path,
    session_id: str,
    reviewed_destinations: list[dict[str, Any]] | None = None,
) -> RuntimeExecutionResult:
    if not policy_engine.get("filesystem.allow_write_workspace", False):
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="disabled",
            response_text="Workspace writes are disabled by policy.",
            details={
                "observation": _tool_observation(
                    intent="workspace.apply_unified_diff",
                    tool_surface="workspace",
                    ok=False,
                    status="disabled",
                ),
            },
        )
    payload = apply_unified_diff_workspace(
        arguments, workspace_root=workspace_root, session_id=session_id, reviewed_destinations=reviewed_destinations
    )
    details = dict(payload.get("details") or {})
    details["observation"] = _tool_observation(
        intent="workspace.apply_unified_diff",
        tool_surface="workspace",
        ok=bool(payload.get("ok")),
        status=str(payload.get("status") or ""),
        paths=list(details.get("paths") or []),
        engine=str(details.get("engine") or ""),
    )
    return _result_from_payload(
        ok=bool(payload.get("ok")),
        status=str(payload.get("status") or ""),
        response_text=str(payload.get("response_text") or ""),
        details=details,
    )


def _git_status(arguments: dict[str, Any], *, workspace_root: Path) -> RuntimeExecutionResult:
    payload = git_status_workspace(arguments, workspace_root=workspace_root)
    details = dict(payload.get("details") or {})
    details["observation"] = _tool_observation(
        intent="workspace.git_status",
        tool_surface="workspace",
        ok=bool(payload.get("ok")),
        status=str(payload.get("status") or ""),
        cwd=str(details.get("cwd") or ""),
        stdout=str(details.get("stdout") or ""),
        stderr=str(details.get("stderr") or ""),
        returncode=int(details.get("returncode") or 0),
    )
    return _result_from_payload(
        ok=bool(payload.get("ok")),
        status=str(payload.get("status") or ""),
        response_text=str(payload.get("response_text") or ""),
        details=details,
    )


def _git_diff(arguments: dict[str, Any], *, workspace_root: Path) -> RuntimeExecutionResult:
    payload = git_diff_workspace(arguments, workspace_root=workspace_root)
    details = dict(payload.get("details") or {})
    details["observation"] = _tool_observation(
        intent="workspace.git_diff",
        tool_surface="workspace",
        ok=bool(payload.get("ok")),
        status=str(payload.get("status") or ""),
        cwd=str(details.get("cwd") or ""),
        stdout=str(details.get("stdout") or ""),
        stderr=str(details.get("stderr") or ""),
        returncode=int(details.get("returncode") or 0),
    )
    return _result_from_payload(
        ok=bool(payload.get("ok")),
        status=str(payload.get("status") or ""),
        response_text=str(payload.get("response_text") or ""),
        details=details,
    )


def _git_summary(arguments: dict[str, Any], *, workspace_root: Path) -> RuntimeExecutionResult:
    payload = git_summary_workspace(arguments, workspace_root=workspace_root)
    details = dict(payload.get("details") or {})
    details["observation"] = _tool_observation(
        intent="workspace.git_summary",
        tool_surface="workspace",
        ok=bool(payload.get("ok")),
        status=str(payload.get("status") or ""),
        cwd=str(details.get("cwd") or ""),
        branch=str(details.get("branch") or ""),
        commit=str(details.get("commit") or ""),
        dirty=bool(details.get("dirty", False)),
        local_branch_count=int(details.get("local_branch_count") or 0),
        remote_branch_count=int(details.get("remote_branch_count") or 0),
        total_branch_count=int(details.get("total_branch_count") or 0),
        today_date=str(details.get("today_date") or ""),
        today_commit_count=int(details.get("today_commit_count") or 0),
        yesterday_date=str(details.get("yesterday_date") or ""),
        yesterday_commit_count=int(details.get("yesterday_commit_count") or 0),
        recent_commits=[dict(item) for item in list(details.get("recent_commits") or []) if isinstance(item, dict)],
        commit_count_scope=str(details.get("commit_count_scope") or ""),
        timezone_label=str(details.get("timezone_label") or ""),
    )
    return _result_from_payload(
        ok=bool(payload.get("ok")),
        status=str(payload.get("status") or ""),
        response_text=str(payload.get("response_text") or ""),
        details=details,
    )


def _rollback_last_change(
    arguments: dict[str, Any],
    *,
    workspace_root: Path,
    session_id: str,
    source_context: dict[str, Any] | None = None,
) -> RuntimeExecutionResult:
    del arguments
    # Blackbox turn rollback. The directive lives in the SERVER-BUILT source context, never in the
    # model's arguments (this intent's schema is empty, so any argument is rejected upstream as
    # invalid_arguments). Without a directive this stays the legacy last-mutation rollback.
    from core.blackbox.authority import directive_from_context

    directive = directive_from_context(source_context)
    if directive is not None:
        from core.blackbox.rollback import execute_rollback_directive

        return execute_rollback_directive(directive, workspace_root=workspace_root, source_context=dict(source_context or {}))
    rollback = rollback_last_workspace_mutation(session_id=session_id, workspace_root=workspace_root)
    if rollback is None:
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="no_tracked_change",
            response_text="There is no VOOL-tracked workspace mutation to roll back in this session.",
            details={
                "restored_paths": [],
                "removed_paths": [],
                "observation": _tool_observation(
                    intent="workspace.rollback_last_change",
                    tool_surface="workspace",
                    ok=False,
                    status="no_tracked_change",
                    restored_paths=[],
                    removed_paths=[],
                ),
            },
        )
    if rollback.get("status") == "conflict":
        # The current on-disk state no longer matches what THIS mutation left behind -- something
        # else (a user, another process, an external edit) touched the file(s) since. Refusing
        # here is the whole point: restoring `before_text` unconditionally would silently destroy
        # that later change with no way to get it back.
        conflicts = [dict(item) for item in list(rollback.get("conflicts") or []) if isinstance(item, dict)]
        conflict_lines = [
            f"- `{item.get('path')}`: {item.get('reason')}" for item in conflicts
        ]
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="stale_revert_conflict",
            response_text=(
                "I did not roll back this change: the file(s) it touched no longer match what "
                "that mutation left on disk, so reverting would overwrite something else that "
                "happened since. Nothing was written.\n" + "\n".join(conflict_lines)
            ),
            details={
                "mutation_id": str(rollback.get("mutation_id") or "").strip(),
                "conflicts": conflicts,
                "restored_paths": [],
                "removed_paths": [],
                "observation": _tool_observation(
                    intent="workspace.rollback_last_change",
                    tool_surface="workspace",
                    ok=False,
                    status="stale_revert_conflict",
                    conflicts=conflicts,
                    restored_paths=[],
                    removed_paths=[],
                ),
            },
        )
    restored_paths = [str(item).strip() for item in list(rollback.get("restored_paths") or []) if str(item).strip()]
    removed_paths = [str(item).strip() for item in list(rollback.get("removed_paths") or []) if str(item).strip()]
    lines = ["Rolled back the last VOOL-tracked workspace change."]
    for path in restored_paths:
        lines.append(f"- restored `{path}`")
    for path in removed_paths:
        lines.append(f"- removed `{path}`")
    return RuntimeExecutionResult(
        handled=True,
        ok=True,
        status="executed",
        response_text="\n".join(lines),
        details={
            "restored_paths": restored_paths,
            "removed_paths": removed_paths,
            "mutation_id": str(rollback.get("mutation_id") or "").strip(),
            "observation": _tool_observation(
                intent="workspace.rollback_last_change",
                tool_surface="workspace",
                ok=True,
                status="executed",
                restored_paths=restored_paths,
                removed_paths=removed_paths,
            ),
        },
    )


def _run_validation(
    intent: str,
    arguments: dict[str, Any],
    *,
    workspace_root: Path,
    source_context: dict[str, Any] | None = None,
) -> RuntimeExecutionResult:
    command = runtime_validation_command(validation_command(intent, arguments))
    command_arguments = dict(arguments)
    command_arguments["command"] = command
    command_arguments["_trusted_local_only"] = True
    runtime_result = _run_command(command_arguments, workspace_root=workspace_root, source_context=source_context)
    inner_status = str(runtime_result.status or "")
    if inner_status not in {"executed", "command_failed", "timed_out", "cancelled"}:
        # The command never became a run: a confinement or permission refusal, a policy block,
        # an unavailable sandbox. Rendering it as a validation result would fabricate the one
        # field the journal's evidence classification keys on -- a `returncode` for a process
        # that never started -- which measured live turned a refused out-of-workspace `cwd`
        # into a journaled failed verification. The typed refusal crosses the door as what it
        # is; only a run that reached a terminal outcome of its own is rendered as a command.
        inner_details = dict(getattr(runtime_result, "details", {}) or {})
        inner_details.pop("observation", None)
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status=inner_status or "not_executed",
            response_text=(
                f"{intent} did not run ({inner_status or 'not_executed'}): "
                f"{runtime_result.response_text or 'the command was refused before it started'}"
            ),
            details={
                **inner_details,
                "command": command,
                "executed": False,
                "observation": _tool_observation(
                    intent=intent,
                    tool_surface="workspace",
                    ok=False,
                    status=inner_status or "not_executed",
                    command=command,
                    error=str(runtime_result.response_text or ""),
                ),
            },
        )
    payload = render_validation_result(
        intent,
        command=command,
        cwd=str(runtime_result.details.get("cwd") or "."),
        runner_result={
            "status": runtime_result.status,
            "stdout": runtime_result.details.get("stdout"),
            "stderr": runtime_result.details.get("stderr"),
            "returncode": runtime_result.details.get("returncode"),
            "success": runtime_result.details.get("success"),
        },
        label={
            "workspace.run_tests": "Validation test run",
            "workspace.run_lint": "Validation lint run",
            "workspace.run_formatter": "Validation formatter run",
        }[intent],
    )
    details = dict(payload.get("details") or {})
    followup = _extract_command_failure_followup(
        stdout=str(details.get("stdout") or ""),
        stderr=str(details.get("stderr") or ""),
    )
    details["error_path"] = str(followup.get("error_path") or "").strip()
    details["error_line"] = int(followup.get("error_line") or 0)
    details["diagnostic_query"] = str(followup.get("diagnostic_query") or "").strip()
    details["observation"] = _tool_observation(
        intent=intent,
        tool_surface="workspace",
        ok=bool(payload.get("ok")),
        status=str(payload.get("status") or ""),
        command=str(details.get("command") or ""),
        cwd=str(details.get("cwd") or ""),
        returncode=int(details.get("returncode") or 0),
        success=bool(details.get("success", False)),
        failure_summary=str(details.get("failure_summary") or ""),
        error_path=str(details.get("error_path") or ""),
        error_line=int(details.get("error_line") or 0),
        diagnostic_query=str(details.get("diagnostic_query") or ""),
    )
    return _result_from_payload(
        ok=bool(payload.get("ok")),
        status=str(payload.get("status") or ""),
        response_text=str(payload.get("response_text") or ""),
        details=details,
    )


def _execute_task_envelope_intent(
    arguments: dict[str, Any],
    *,
    workspace_root: Path,
    session_id: str,
    source_context: dict[str, Any] | None,
) -> RuntimeExecutionResult:
    payload = arguments.get("task_envelope")
    if not isinstance(payload, dict):
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="invalid_arguments",
            response_text="orchestration.execute_envelope requires a `task_envelope` object.",
            details={
                "observation": _tool_observation(
                    intent="orchestration.execute_envelope",
                    tool_surface="orchestration",
                    ok=False,
                    status="invalid_arguments",
                ),
            },
        )
    from core.orchestration import execute_task_envelope, task_envelope_from_dict

    try:
        envelope = task_envelope_from_dict(payload)
    except Exception as exc:
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="invalid_arguments",
            response_text=f"Invalid task envelope payload: {exc}",
            details={
                "error": str(exc),
                "observation": _tool_observation(
                    intent="orchestration.execute_envelope",
                    tool_surface="orchestration",
                    ok=False,
                    status="invalid_arguments",
                ),
            },
        )
    envelope_result = execute_task_envelope(
        envelope,
        workspace_root=str(workspace_root),
        session_id=session_id or envelope.task_id,
        source_context=source_context,
    )
    # ROOT-CAUSE CONTRACT — the repair lane's typed writer: what the envelope
    # ACTUALLY produced (preflight failing capture, patch receipts at the
    # seam, final validation green) lands on the turn's diagnosis. A no-op for
    # turns that carry no contract (ordinary envelopes are untouched).
    try:
        from core.root_cause_contract import record_envelope_outcome as _record_rc

        _record_rc(
            source_context,
            envelope_arguments=dict(arguments or {}),
            envelope_result=envelope_result,
        )
    except Exception:
        pass
    details = {
        "task_envelope": envelope.to_dict(),
        "envelope_result": envelope_result.merge_payload(),
        "receipts": [dict(item) for item in envelope_result.receipts],
        "graph": list(envelope_result.details.get("graph") or []),
        "scheduled_children": list(envelope_result.details.get("scheduled_children") or []),
        "merged_result": dict(envelope_result.details.get("merged_result") or {}),
        "step_results": list(envelope_result.details.get("step_results") or []),
        "observation": _tool_observation(
            intent="orchestration.execute_envelope",
            tool_surface="orchestration",
            ok=envelope_result.ok,
            status=envelope_result.status,
            task_id=envelope.task_id,
            task_role=envelope.role,
            receipt_count=len(envelope_result.receipts),
            child_count=len(list(envelope.inputs.get("subtasks") or [])),
            merged_strategy=envelope.merge_strategy if envelope.role == "queen" else "",
        ),
    }
    return RuntimeExecutionResult(
        handled=True,
        ok=envelope_result.ok,
        status=envelope_result.status,
        response_text=envelope_result.output_text,
        details=details,
    )


def _attach_procedure_learning(
    result: RuntimeExecutionResult,
    *,
    validation_intent: str,
    workspace_root: Path,
    source_context: dict[str, Any] | None,
) -> RuntimeExecutionResult:
    if not result.ok:
        return result
    session_id = _runtime_session_id(source_context)
    mutation = latest_workspace_mutation(
        session_id=session_id,
        workspace_root=workspace_root,
        require_unpromoted=True,
    )
    if not mutation:
        return result
    changed_paths = [str(item.get("path") or "").strip() for item in list(mutation.get("changes") or []) if str(item.get("path") or "").strip()]
    validation_details = dict(result.details or {})
    task_envelope = dict((source_context or {}).get("task_envelope") or {})
    envelope_inputs = dict(task_envelope.get("inputs") or {})
    task_class = str(
        (source_context or {}).get("task_class")
        or (source_context or {}).get("execution_task_class")
        or envelope_inputs.get("task_class")
        or "coding_operator"
    ).strip() or "coding_operator"
    shard = promote_verified_procedure(
        task_class=task_class,
        title=_procedure_title(task_class=task_class, validation_intent=validation_intent, changed_paths=changed_paths),
        preconditions=[
            "workspace is writable",
            "VOOL tracked a mutation in the active session",
        ],
        steps=[
            _procedure_step_for_intent(str(mutation.get("intent") or "").strip()),
            _procedure_step_for_intent(validation_intent),
        ],
        tool_receipts=[
            {
                "intent": str(mutation.get("intent") or "").strip(),
                "mutation_id": str(mutation.get("mutation_id") or "").strip(),
                "paths": changed_paths,
            },
            {
                "intent": validation_intent,
                "command": str(validation_details.get("command") or "").strip(),
                "returncode": int(validation_details.get("returncode") or 0),
            },
        ],
        validation={
            "ok": True,
            "tool": validation_intent,
            "command": str(validation_details.get("command") or "").strip(),
            "returncode": int(validation_details.get("returncode") or 0),
        },
        rollback={
            "intent": "workspace.rollback_last_change",
            "mutation_id": str(mutation.get("mutation_id") or "").strip(),
        },
        privacy_class="local_private",
        shareability="local_only",
        liquefy_bundle_ref=str((source_context or {}).get("liquefy_bundle_ref") or "").strip(),
    )
    if shard is None:
        return result
    mark_workspace_mutation_promoted(
        session_id=session_id,
        workspace_root=workspace_root,
        mutation_id=str(mutation.get("mutation_id") or "").strip(),
        procedure_id=shard.procedure_id,
    )
    result.details["procedure_shard"] = shard.to_dict()
    result.details["procedure_reuse"] = {
        "procedure_id": shard.procedure_id,
        "title": shard.title,
        "task_class": shard.task_class,
    }
    return result


def _procedure_title(*, task_class: str, validation_intent: str, changed_paths: list[str]) -> str:
    if changed_paths:
        return f"{task_class}: validate {', '.join(changed_paths[:2])} with {validation_intent}"
    return f"{task_class}: verify workspace mutation with {validation_intent}"


def _procedure_step_for_intent(intent: str) -> str:
    mapping = {
        "workspace.write_file": "write the target file",
        "workspace.replace_in_file": "replace the targeted text in place",
        "workspace.apply_unified_diff": "apply the code patch as a unified diff",
        "workspace.run_tests": "run the bounded test command",
        "workspace.run_lint": "run the linter for the workspace",
        "workspace.run_formatter": "run the formatter check for the workspace",
    }
    return mapping.get(str(intent or "").strip(), str(intent or "").strip() or "run the verified workflow step")


class _CallableCancelToken:
    """A cancel signal handed down as a callable/flag, given the ``is_set`` interface the job
    runner polls. Read failure counts as SET: a signal that cannot be read is not permission to
    keep a child process running."""

    __slots__ = ("_token",)

    def __init__(self, token: Any) -> None:
        self._token = token

    def is_set(self) -> bool:
        try:
            token = self._token
            return bool(token() if callable(token) else token)
        except Exception:
            return True


def _cancel_token_from(source_context: dict[str, Any] | None):
    """The live cancellation signal for this call, or None. Same rule as the executor's
    pre-dispatch check: a signal that cannot be read counts as SET, never as permission to run.

    The turn context carries this key in three shapes across the runtime -- a ``threading.Event``
    from the served door, the composite event a code task builds, and a callable/flag from the
    conductor and router seams. Only the first two answer ``is_set``; handing the job runner a
    bare callable used to raise AttributeError mid-wait and take the command down with it, so the
    other shapes are adapted here rather than each caller being taught the runner's interface."""
    context = source_context or {}
    token = context.get("cancel_event") or context.get("cancellation_token")
    if token is None:
        return None
    if hasattr(token, "is_set"):
        return token
    return _CallableCancelToken(token)


SANDBOX_CWD_OUTSIDE_WORKSPACE = "cwd_outside_workspace"


def sandbox_cwd_refusal(raw_cwd: Any, *, workspace_root: Path) -> RuntimeExecutionResult | None:
    """The typed refusal for a `sandbox.run_command` working directory outside the workspace, or
    None when the directory is inside it (or not given).

    Decided BEFORE anything is dispatched, so it is a refusal and says so. Measured live 2026-09-07
    (Manual mode): the check raised `ValueError` from inside the dispatch boundary; the A6 boundary
    read the raise as a post-dispatch exception and answered "Outcome unknown -- reconciliation
    required ... retrying it is blocked" for a command that never started. The executor consults
    this same function before asking an operator to approve the action, so nobody approves a
    command the runtime was always going to refuse.
    """
    cwd_text = str(raw_cwd or "").strip()
    if not cwd_text:
        return None
    try:
        _resolve_workspace_path(cwd_text, workspace_root=workspace_root)
    except ValueError as exc:
        text = (
            f"sandbox.run_command refused: the working directory `{cwd_text}` is outside the active "
            f"workspace (`{workspace_root}`). Nothing was run. {exc}"
        )
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status=SANDBOX_CWD_OUTSIDE_WORKSPACE,
            response_text=text,
            details={
                "executed": False,
                "cwd": cwd_text,
                "workspace_root": str(workspace_root),
                "observation": _tool_observation(
                    intent="sandbox.run_command",
                    tool_surface="sandbox",
                    ok=False,
                    status=SANDBOX_CWD_OUTSIDE_WORKSPACE,
                    error=str(exc),
                ),
            },
        )
    return None


def _run_command(
    arguments: dict[str, Any], *, workspace_root: Path, source_context: dict[str, Any] | None = None
) -> RuntimeExecutionResult:
    command = str(arguments.get("command") or arguments.get("cmd") or "").strip()
    # Direct handler invocation (tests, library callers): same owning-entry-point
    # rule as the executor — name the scope so the command gate attributes this
    # execution instead of denying it as an unattributable effect. Inside a turn
    # the turn's ledger defers the scope and its frozen policy governs.
    from core.effect_gateway import current_effect_ledger, named_background_effect_scope

    if current_effect_ledger() is None:
        with named_background_effect_scope(
            "sandbox_command.direct_call", source_context={"workspace_root": str(workspace_root)}
        ):
            return _run_command(arguments, workspace_root=workspace_root, source_context=source_context)
    if not command:
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="invalid_arguments",
            response_text="sandbox.run_command needs a non-empty `command`.",
            details={
                "observation": _tool_observation(
                    intent="sandbox.run_command",
                    tool_surface="sandbox",
                    ok=False,
                    status="invalid_arguments",
                ),
            },
        )
    raw_cwd = arguments.get("cwd")
    refusal = sandbox_cwd_refusal(raw_cwd, workspace_root=workspace_root)
    if refusal is not None:
        return refusal
    cwd = _resolve_workspace_path(raw_cwd, workspace_root=workspace_root) if raw_cwd else workspace_root
    if cwd.is_file():
        cwd = cwd.parent
    from core.agent_runtime.audit_policy import isolated_proof_read_roots

    proof_reads = isolated_proof_read_roots(source_context)
    runner = SandboxRunner(
        ExecutionGate(),
        str(workspace_root),
        network_isolation_mode=_trusted_local_network_mode(command, arguments=arguments),
        **({"read_roots": tuple(Path(root) for root in proof_reads)} if proof_reads else {}),
    )
    # The operator's stop button reaches INTO the command: the turn's cancel signal (and, for a
    # code task step, the task's own) is polled by the job runner while the child runs, so a
    # cancellation kills the process instead of waiting for it to finish.
    result = runner.run_command(command, cwd=str(cwd), cancel_event=_cancel_token_from(source_context))
    relative_cwd = _relative_path(cwd, workspace_root=workspace_root)
    status = str(result.get("status") or ("blocked_by_policy" if result.get("error") else ""))
    if status and status != "executed":
        stdout = _truncate(str(result.get("stdout") or ""), limit=2400)
        stderr = _truncate(str(result.get("stderr") or ""), limit=1600)
        command_artifact = build_command_artifact(
            command=command,
            cwd=relative_cwd,
            returncode=int(result.get("returncode", 0) or 0),
            stdout=stdout,
            stderr=stderr,
            status=status,
        )
        failure_summary = _extract_failure_summary(
            command=command,
            stdout=stdout,
            stderr=stderr,
            returncode=int(result.get("returncode", 0) or 0),
        )
        failure_artifacts = []
        if failure_summary:
            failure_artifacts.append(
                build_failure_artifact(
                    command=command,
                    cwd=relative_cwd,
                    returncode=int(result.get("returncode", 0) or 0),
                    stdout=stdout,
                    stderr=stderr,
                    summary=failure_summary,
                )
            )
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status=status,
            response_text=str(result.get("error") or f"Command could not run: {status}"),
            details={
                **dict(result),
                "artifacts": [command_artifact, *failure_artifacts],
                "observation": _tool_observation(
                    intent="sandbox.run_command",
                    tool_surface="sandbox",
                    ok=False,
                    status=status,
                    command=command,
                    cwd=relative_cwd,
                    returncode=int(result.get("returncode", 0) or 0),
                    stdout=stdout,
                    stderr=stderr,
                    error=str(result.get("error") or ""),
                    failure_summary=failure_summary,
                ),
            },
        )
    stdout = _truncate(str(result.get("stdout") or ""), limit=2400)
    stderr = _truncate(str(result.get("stderr") or ""), limit=1600)
    returncode = int(result.get("returncode", 0) or 0)
    # The sandbox ran it, so the CALL was handled -- but a nonzero exit means the COMMAND failed, and
    # this returned ok=True/status="executed" for every exit code. That is the same false-green class
    # the tool is used to detect: `cat missing.txt` produced a signed receipt saying ok, and the turn
    # was marked tool_executed, so nothing downstream could tell a working command from a broken one.
    # returncode/success were correct in details all along; only the flags the ledger and the receipt
    # read were wrong. response_text is unchanged -- the model still gets the exit code and both
    # streams, which is what it needs to react.
    command_failed = returncode != 0
    command_artifact = build_command_artifact(
        command=command,
        cwd=relative_cwd,
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        status="command_failed" if command_failed else "executed",
    )
    failure_summary = _extract_failure_summary(
        command=command,
        stdout=stdout,
        stderr=stderr,
        returncode=returncode,
    )
    failure_artifacts = []
    if failure_summary:
        failure_artifacts.append(
            build_failure_artifact(
                command=command,
                cwd=relative_cwd,
                returncode=returncode,
                stdout=stdout,
                stderr=stderr,
                summary=failure_summary,
            )
        )
    lines = [
        f"Command {'failed' if command_failed else 'executed'} in `{relative_cwd}`:",
        f"$ {command}",
        f"- Exit code: {returncode}",
    ]
    if stdout:
        lines.append(f"- Stdout:\n{stdout}")
    if stderr:
        lines.append(f"- Stderr:\n{stderr}")
    return RuntimeExecutionResult(
        handled=True,
        ok=not command_failed,
        status="command_failed" if command_failed else "executed",
        response_text="\n".join(lines),
        details={
            "command": command,
            "cwd": relative_cwd,
            "returncode": returncode,
            "success": bool(result.get("success", False)),
            "stdout": stdout,
            "stderr": stderr,
            "artifacts": [command_artifact, *failure_artifacts],
            "observation": _tool_observation(
                intent="sandbox.run_command",
                tool_surface="sandbox",
                ok=not command_failed,
                status="command_failed" if command_failed else "executed",
                command=command,
                cwd=relative_cwd,
                returncode=returncode,
                success=bool(result.get("success", False)),
                stdout=stdout,
                stderr=stderr,
                failure_summary=failure_summary,
            ),
        },
    )


def _trusted_local_network_mode(command: str, *, arguments: dict[str, Any]) -> str | None:
    if not bool(arguments.get("_trusted_local_only", False)):
        return None
    argv = parse_command(command)
    if not argv:
        return None
    base = Path(str(argv[0] or "")).name.lower()
    if os.name == "nt" and base.endswith(".exe"):
        base = base[:-4]
    if base in {"pytest", "ruff"}:
        return "heuristic_only"
    if base not in {"python", "python3"} or len(argv) < 3:
        return None
    if argv[1] != "-m":
        return None
    module = str(argv[2] or "").lower()
    # unittest is a stdlib test runner (the bundle has no pytest), so a `python -m unittest ...` run of
    # the user's own generated tests is trusted-local the same way pytest/ruff/compileall are.
    if module in {"pytest", "ruff", "unittest"}:
        return "heuristic_only"
    if len(argv) >= 4 and argv[1:4] == ["-m", "compileall", "-q"]:
        return "heuristic_only"
    return None


# ---------------------------------------------------------------------------------------------
# PDF and skill handlers.
#
# Each returns the module's own structured result verbatim in `details`, so the observation the
# model reads carries the same `status` vocabulary the tool contract advertises. An empty `text`
# with `status: ok` would read as "this PDF is blank" - the one thing a scanned PDF is not - so
# `pdf.extract_text` reports `no_text_layer` and names `pdf.ocr` as the next step.
# ---------------------------------------------------------------------------------------------


def _pdf_result(intent: str, result: dict[str, Any], *, verb: str) -> RuntimeExecutionResult:
    status = str(result.get("status") or "error")
    ok = status == "ok"
    if ok:
        text = str(result.get("text") or "")
        response = (
            f"{verb} `{result.get('path')}` - {result.get('pages_read')} of "
            f"{result.get('pages_total')} page(s), {result.get('characters')} character(s)."
            f"\n\n{text}"
        )
    else:
        response = f"Could not read `{result.get('path')}`: {result.get('reason') or status}."
    return RuntimeExecutionResult(
        handled=True,
        ok=ok,
        status=status,
        response_text=response,
        details={
            **result,
            "observation": _tool_observation(
                intent=intent,
                tool_surface="pdf",
                ok=ok,
                status=status,
                path=str(result.get("path") or ""),
                pages_read=int(result.get("pages_read") or 0),
                pages_total=int(result.get("pages_total") or 0),
                characters=int(result.get("characters") or 0),
                reason=str(result.get("reason") or ""),
            ),
        },
    )


def _pdf_extract_text(arguments: dict[str, Any]) -> RuntimeExecutionResult:
    from core.pdf_tools import extract_text

    result = extract_text(
        str(arguments.get("path") or ""),
        max_pages=int(arguments.get("max_pages") or 0),
    )
    return _pdf_result("pdf.extract_text", result, verb="Read")


def _pdf_ocr(arguments: dict[str, Any]) -> RuntimeExecutionResult:
    from core.pdf_tools import ocr

    result = ocr(
        str(arguments.get("path") or ""),
        max_pages=int(arguments.get("max_pages") or 0),
        scale=float(arguments.get("scale") or 2.0),
    )
    return _pdf_result("pdf.ocr", result, verb="Recognised text in")


def _skill_result(intent: str, result: dict[str, Any], *, response: str) -> RuntimeExecutionResult:
    status = str(result.get("status") or "error")
    ok = status == "ok"
    return RuntimeExecutionResult(
        handled=True,
        ok=ok,
        status=status,
        response_text=response if ok else (
            str(result.get("reason") or "")
            or "; ".join(str(item) for item in (result.get("problems") or []))
            or f"skill step failed: {status}"
        ),
        details={
            **result,
            "observation": _tool_observation(
                intent=intent,
                tool_surface="skill",
                ok=ok,
                status=status,
                path=str(result.get("path") or ""),
                problems=list(result.get("problems") or []),
                reason=str(result.get("reason") or ""),
            ),
        },
    )


def _skill_list(arguments: dict[str, Any]) -> RuntimeExecutionResult:
    from core.skill_tools import list_skills

    result = list_skills(str(arguments.get("workspace") or ""))

    def _section(title: str, rows: list[dict[str, Any]]) -> str:
        if not rows:
            return ""
        lines = [f"**{title}** ({len(rows)})"]
        for row in rows:
            name = str(row.get("name") or "?")
            desc = str(row.get("description") or "").strip()
            path = str(row.get("path") or "")
            summary = f" — {desc}" if desc else ""
            lines.append(f"- `{name}`{summary}\n  `{path}`")
        return "\n".join(lines)

    sections = [
        _section("In the workspace", list(result.get("workspace_skills") or [])),
        _section("Staged (drafted, not active)", list(result.get("staged_skills") or [])),
        _section("Installed (active)", list(result.get("installed_skills") or [])),
    ]
    body = "\n\n".join(section for section in sections if section)
    total = int(result.get("total") or 0)
    if not total:
        scanned = str(result.get("workspace") or "").strip()
        body = (
            f"No skills found. I scanned {scanned or 'no workspace (none is bound to this chat)'}, "
            "the staging area, and the installed plugins tree for SKILL.md files."
        )
    return _skill_result("skill.list", result, response=body or f"{total} skill(s).")


def _skill_create(arguments: dict[str, Any]) -> RuntimeExecutionResult:
    from core.skill_tools import create_skill

    result = create_skill(
        name=str(arguments.get("name") or ""),
        description=str(arguments.get("description") or ""),
        body=str(arguments.get("body") or ""),
        allowed_tools=tuple(arguments.get("allowed_tools") or ()),
        triggers=tuple(arguments.get("triggers") or ()),
        overwrite=bool(arguments.get("overwrite", False)),
    )
    return _skill_result(
        "skill.create",
        result,
        response=(
            f"Drafted the skill at `{result.get('path')}`. It is not active yet - "
            "validate it, then install it."
        ),
    )


def _skill_validate(arguments: dict[str, Any]) -> RuntimeExecutionResult:
    from core.skill_tools import validate_skill

    result = validate_skill(str(arguments.get("path") or ""))
    return _skill_result(
        "skill.validate",
        result,
        response=(
            f"`{result.get('name')}` validates: {result.get('body_characters')} character(s) of "
            "instructions, and every tool it names exists."
        ),
    )


def _skill_install(arguments: dict[str, Any]) -> RuntimeExecutionResult:
    from core.skill_tools import install_skill

    result = install_skill(
        str(arguments.get("path") or ""),
        plugin_id=str(arguments.get("plugin_id") or "local-skills"),
        overwrite=bool(arguments.get("overwrite", False)),
    )
    version = result.get("version")
    version_text = f" as version {version}" if isinstance(version, int) else ""
    return _skill_result(
        "skill.install",
        result,
        response=(
            f"Installed `{result.get('name')}` at `{result.get('path')}`{version_text}. "
            "It is active from the next turn."
        ),
    )


def _skill_rollback(arguments: dict[str, Any]) -> RuntimeExecutionResult:
    from core.skill_tools import rollback_skill, skill_history

    name = str(arguments.get("skill") or arguments.get("path") or "")
    raw_version = arguments.get("version")
    try:
        version = int(raw_version)
    except (TypeError, ValueError):
        return _skill_result(
            "skill.rollback",
            {"status": "invalid_arguments", "reason": "version must be a number"},
            response="I need the version number to roll back to.",
        )
    result = rollback_skill(name, version=version)
    if result.get("status") == "ok":
        history = skill_history(name)
        return _skill_result(
            "skill.rollback",
            result,
            response=(
                f"Rolled `{result.get('name')}` back to version {result.get('restored_from')} "
                f"byte for byte; it is now head version {result.get('version')} of "
                f"{history.get('head_version')} recorded. The loader picks it up next turn."
            ),
        )
    return _skill_result(
        "skill.rollback",
        result,
        response=str(result.get("reason") or "the rollback was not performed"),
    )


def _skill_inspect(arguments: dict[str, Any]) -> RuntimeExecutionResult:
    from core.native_skill_library import inspect_skill

    result = inspect_skill(str(arguments.get("id") or ""))
    if result.get("status") != "ok":
        return _skill_result(
            "skill.inspect",
            result,
            response=str(result.get("reason") or "that skill could not be inspected"),
        )
    contract = result.get("contract") or {}
    availability = "available" if result.get("available") else f"not available ({result.get('reason')})"
    body = (
        f"`{result.get('id')}` v{result.get('version')} — "
        f"risk class {contract.get('risk_class')}, "
        f"task families {contract.get('task_families')}, "
        f"{'enabled' if result.get('enabled') else 'disabled'}, {availability}. "
        f"Verification: {contract.get('verification')}. "
        f"Stopping conditions: {contract.get('stopping_conditions')}."
    )
    return _skill_result("skill.inspect", result, response=body)
