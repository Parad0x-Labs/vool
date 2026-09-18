"""The capability registry: the one place a mutation-capable tool's declaration lives.

Builtin tools seed their declarations at first use (``ensure_builtin_capabilities``); plugin and
MCP contracts register theirs through ``tool_registry.register``, which REFUSES a mutation-capable
contract that carries no declaration. The executor asks ``mutation_coverage_decision`` before any
dispatch: a local-mutating intent with no declaration is a typed refusal, not an unrecorded run.

The decision reads the CONTRACT's own side-effect class -- the same field permissions, receipts
and the catalog read -- so coverage cannot drift from what the runtime believes the tool does.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

from core.blackbox.coverage.capability import (
    LOCAL_MUTATION_CLASSES,
    MutationCapability,
    validated,
)

RECORDER_WORKSPACE_FILE = "blackbox.workspace_file"  # the v1 flight recorder (core/blackbox/recorder.py)
RECORDER_COVERAGE_SCAN = "blackbox.coverage_scan"  # pre/post workspace scan (shell, formatters, tests)
RECORDER_COVERAGE_DECLARED = "blackbox.coverage_declared"  # declared-path preimages (machine writes, moves)
RECORDER_COVERAGE_POST = "blackbox.coverage_post"  # postimage-only journaling for irreversible local effects
RECORDER_EFFECT_RECEIPT = "effect.receipt"  # the effect authority's own receipts (external effects)

_lock = threading.RLock()
_capabilities: dict[str, MutationCapability] = {}
_builtins_seeded = False


def _builtin_capabilities() -> list[MutationCapability]:
    """Every builtin intent whose contract class is a local mutation, with its declaration.

    ``workspace.*`` file intents journal through the v1 flight recorder already; shell-shaped
    intents and formatters get the pre/post workspace scan; machine writes and moves observe their
    declared paths; media project state and the temp cleanup are local but not byte-undoable, so
    they journal postimages and claim no rollback; the calendar event leaves this machine.
    """
    workspace_file = dict(
        scope="workspace",
        effect_class="reversible",
        snapshot_strategy="declared_paths",
        receipt_lifecycle="intent_then_terminal",
        rollback_support="exact",
        recorder=RECORDER_WORKSPACE_FILE,
    )
    workspace_scan = dict(
        scope="workspace",
        effect_class="reversible",
        snapshot_strategy="workspace_scan",
        receipt_lifecycle="intent_then_terminal",
        rollback_support="exact",
        recorder=RECORDER_COVERAGE_SCAN,
    )
    machine_declared = dict(
        scope="machine",
        effect_class="reversible",
        snapshot_strategy="declared_paths",
        receipt_lifecycle="intent_then_terminal",
        rollback_support="exact",
        recorder=RECORDER_COVERAGE_DECLARED,
        notes="machine-scope effects observe only the paths their arguments declare",
    )
    local_post = dict(
        scope="machine",
        effect_class="irreversible",
        snapshot_strategy="postimage_only",
        receipt_lifecycle="terminal_only",
        rollback_support="none",
        recorder=RECORDER_COVERAGE_POST,
    )
    declarations: list[dict[str, Any]] = [
        {"tool": "workspace.write_file", **workspace_file},
        {"tool": "workspace.replace_in_file", **workspace_file},
        {"tool": "workspace.apply_unified_diff", **workspace_file},
        {"tool": "workspace.ensure_directory", **workspace_file},
        {"tool": "workspace.rollback_last_change", **workspace_file},
        {"tool": "sandbox.run_command", **workspace_scan},
        {"tool": "workspace.run_tests", **workspace_scan},
        {"tool": "workspace.run_lint", **workspace_scan},
        {"tool": "workspace.run_formatter", **workspace_scan},
        {"tool": "machine.write_file", **machine_declared},
        {"tool": "machine.ensure_directory", **machine_declared},
        {"tool": "machine.move_path", **machine_declared},
        {"tool": "skill.create", **machine_declared},
        {"tool": "operator.move_path", **machine_declared},
        {"tool": "operator.save_note", **workspace_file},
        {"tool": "media.open", **local_post},
        {"tool": "media.edit", **local_post},
        {"tool": "media.undo", **local_post},
        {"tool": "media.redo", **local_post},
        {"tool": "media.export", **local_post},
        {"tool": "operator.cleanup_temp_files", **local_post},
        # VOOL-BROWSER (C06): the browser lane's writes are all confined to its own
        # disposable scratch (profiles, screenshots, downloads, staged uploads) — local,
        # byte-undoable by deletion, journaled as postimages; the lane's own receipts
        # carry the operator-facing hash/size evidence.
        {"tool": "vool-browser.session.open", **local_post},
        {"tool": "vool-browser.session.close", **local_post},
        {"tool": "vool-browser.screenshot", **local_post},
        {"tool": "vool-browser.download", **local_post},
        {"tool": "vool-browser.upload.stage", **local_post},
        {"tool": "vool-browser.upload", **local_post},
        {"tool": "vool-browser.cancel", **local_post},
        # C07 money-surface writes (profile store, reconciliation receipts)
        {"tool": "vool-browser.profile.confirm", **local_post},
        {"tool": "vool-browser.checkout.begin", **local_post},
        {"tool": "vool-browser.checkout.handoff", **local_post},
        {"tool": "vool-browser.order.reconcile", **local_post},
        # ORCHESTRATORS (CP2): the code-task and RepoOps control planes are mutation-capable
        # CONTRACTS whose actual mutations execute as INNER intents back through the one door,
        # each carrying its own coverage; the orchestrator itself journals its control
        # transitions in its own task journal. The delegated recorder states exactly that:
        # the census may not treat them as undeclared holes, and the dispatch seam passes them
        # through (never double-journaling what their inner intents already journal). Rollback
        # truth rests on the inner effects' captured preimages (the task rollback restores
        # pre-task bytes through the Blackbox).
        {
            "tool": "code.task.step",
            "scope": "workspace",
            "effect_class": "reversible",
            "snapshot_strategy": "declared_paths",
            "receipt_lifecycle": "intent_then_terminal",
            "rollback_support": "exact",
            "recorder": "blackbox.coverage_delegated",
            "notes": "orchestrator: inner intents through the one door carry their own coverage",
        },
        {
            "tool": "code.task.rollback",
            "scope": "workspace",
            "effect_class": "reversible",
            "snapshot_strategy": "declared_paths",
            "receipt_lifecycle": "intent_then_terminal",
            "rollback_support": "exact",
            "recorder": "blackbox.coverage_delegated",
            "notes": "the rollback's truth IS the Blackbox: it restores the preimages the inner effects captured",
        },
        {
            "tool": "repo.step",
            "scope": "workspace",
            "effect_class": "reversible",
            "snapshot_strategy": "declared_paths",
            "receipt_lifecycle": "intent_then_terminal",
            "rollback_support": "exact",
            "recorder": "blackbox.coverage_delegated",
            "notes": "orchestrator: inner intents through the one door carry their own coverage",
        },
        {
            "tool": "repo.git",
            "scope": "workspace",
            "effect_class": "reversible",
            "snapshot_strategy": "declared_paths",
            "receipt_lifecycle": "intent_then_terminal",
            "rollback_support": "exact",
            "recorder": "blackbox.coverage_delegated",
            "notes": "orchestrator: local git steps execute as inner intents through the one door",
        },
        {
            "tool": "operator.schedule_calendar_event",
            "scope": "external",
            "effect_class": "irreversible",
            "snapshot_strategy": "receipt_only",
            "receipt_lifecycle": "terminal_only",
            "rollback_support": "none",
            "recorder": RECORDER_EFFECT_RECEIPT,
            "notes": "the event leaves this machine through EventKit; its own approval and receipt govern it",
        },
    ]
    return [validated(MutationCapability(**item)) for item in declarations]


def ensure_builtin_capabilities() -> dict[str, MutationCapability]:
    global _builtins_seeded
    with _lock:
        if not _builtins_seeded:
            for capability in _builtin_capabilities():
                _capabilities.setdefault(capability.tool, capability)
            _builtins_seeded = True
        return dict(_capabilities)


def reset_registered() -> None:
    """Drop non-builtin declarations (tests, plugin reload). Builtin seeds are re-derived."""
    global _builtins_seeded
    with _lock:
        _capabilities.clear()
        _builtins_seeded = False
        ensure_builtin_capabilities()


def register_capability(capability: MutationCapability, *, replace: bool = False) -> MutationCapability:
    validated(capability)
    with _lock:
        ensure_builtin_capabilities()
        existing = _capabilities.get(capability.tool)
        if existing is not None and not replace and existing != capability:
            from core.blackbox.coverage.capability import CapabilityError

            raise CapabilityError(
                f"{capability.tool}: a different mutation capability is already registered "
                f"({existing.recorder}/{existing.scope}); unregister it first"
            )
        _capabilities[capability.tool] = capability
    return capability


def unregister_capability(tool: str) -> bool:
    with _lock:
        return _capabilities.pop(str(tool), None) is not None


def capability_for(tool: str) -> MutationCapability | None:
    with _lock:
        ensure_builtin_capabilities()
        return _capabilities.get(str(tool or "").strip())


def registered_capabilities() -> dict[str, MutationCapability]:
    with _lock:
        ensure_builtin_capabilities()
        return dict(_capabilities)


def is_local_mutation_class(side_effect_class: str) -> bool:
    return str(side_effect_class or "") in LOCAL_MUTATION_CLASSES


@dataclass(frozen=True)
class CoverageDecision:
    intent: str
    mutation_capable: bool
    covered: bool
    capability: MutationCapability | None
    reason: str

    @property
    def recorder(self) -> str:
        return self.capability.recorder if self.capability is not None else ""


def mutation_coverage_decision(intent: str) -> CoverageDecision:
    """May this intent run, and under which recorder, judged by its OWN contract."""
    name = str(intent or "").strip()
    try:
        from core.tool_registry import tool_for_intent

        contract = tool_for_intent(name)
    except Exception:
        contract = None
    if contract is None:
        # No contract anywhere: not this gate's tool. The dispatch chain's own unsupported
        # fall-through answers it; a mutation-capable tool ALWAYS has a contract here.
        return CoverageDecision(name, False, True, None, "no_contract")
    side_effect_class = str(getattr(contract, "side_effect_class", "") or "")
    if not is_local_mutation_class(side_effect_class):
        return CoverageDecision(name, False, True, None, f"side_effect_class={side_effect_class or 'none'} is not a local mutation")
    capability = capability_for(name)
    if capability is None:
        return CoverageDecision(
            name, True, False, None, "no mutation capability is declared for this local-mutating tool"
        )
    declared = getattr(contract, "mutation", None)
    if declared is not None and not isinstance(declared, dict):
        return CoverageDecision(name, True, False, capability, "the contract's mutation declaration is malformed")
    if declared is not None:
        from_declaration = MutationCapability.from_dict(declared)
        if from_declaration is None or from_declaration.tool != name:
            return CoverageDecision(name, True, False, capability, "the contract's mutation declaration does not match this tool")
    return CoverageDecision(name, True, True, capability, "declared")


def uncovered_local_mutating_builtins() -> list[str]:
    """Builtin local-mutating intents with NO capability -- the audit answer that must be empty."""
    ensure_builtin_capabilities()
    try:
        from core.tool_registry import registered_tools

        contracts = registered_tools()
    except Exception:
        contracts = ()
    out: list[str] = []
    for contract in contracts:
        if str(getattr(contract, "source", "builtin") or "builtin") != "builtin":
            continue
        if is_local_mutation_class(str(getattr(contract, "side_effect_class", "") or "")) and capability_for(
            str(contract.intent)
        ) is None:
            out.append(str(contract.intent))
    return sorted(out)


__all__ = [
    "RECORDER_COVERAGE_DECLARED",
    "RECORDER_COVERAGE_POST",
    "RECORDER_COVERAGE_SCAN",
    "RECORDER_EFFECT_RECEIPT",
    "RECORDER_WORKSPACE_FILE",
    "CoverageDecision",
    "capability_for",
    "ensure_builtin_capabilities",
    "is_local_mutation_class",
    "mutation_coverage_decision",
    "register_capability",
    "registered_capabilities",
    "reset_registered",
    "uncovered_local_mutating_builtins",
    "unregister_capability",
]
