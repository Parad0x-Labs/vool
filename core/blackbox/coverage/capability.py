"""The declaration every mutation-capable tool must carry before it may run.

A tool that can change bytes on this machine declares, in ONE frozen record, what it may touch
(``scope``), whether the change can be undone (``effect_class``), how the Blackbox observes it
(``snapshot_strategy``), when receipts are written (``receipt_lifecycle``), whether rollback is
offered (``rollback_support``) and WHICH recorder journals it (``recorder``). The registry refuses
a mutation-capable tool that carries no valid declaration, and the executor refuses one whose
declaration is missing at dispatch -- fail closed on both ends.

The vocabulary is closed and the implications are checked, not assumed:

- ``external`` scope can never be reversible: an effect on another system cannot be undone by
  restoring local bytes, so it records as irreversible with its own approval/receipt lifecycle.
- ``reversible`` requires a preimage strategy (``declared_paths`` or ``workspace_scan``), an
  INTENDED-then-TERMINAL receipt lifecycle and exact rollback support. Anything less cannot
  claim reversibility.
- ``machine`` scope may never declare ``workspace_scan``: scanning the whole machine is exactly
  the whole-machine snapshot this lane refuses to take. Machine-scope effects observe only the
  paths they name.
- ``postimage_only`` is the honest strategy for a local effect that cannot be undone byte-exact
  (media project state, a cleanup that deletes): it enters the Blackbox journal with what the
  disk holds afterwards, and it never claims rollback.

Local vs not-local: the classes counted as LOCAL mutation are the ones that change bytes through
this process -- ``workspace_write``, ``sandbox_command``, ``validation_command``. Process-internal
state (builder drafts, capability switches) and network-spend classes are not file mutations and
carry their own effect authority elsewhere.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

SCOPE_WORKSPACE = "workspace"
SCOPE_MACHINE = "machine"
SCOPE_EXTERNAL = "external"

CLASS_REVERSIBLE = "reversible"
CLASS_IRREVERSIBLE = "irreversible"

STRATEGY_DECLARED_PATHS = "declared_paths"
STRATEGY_WORKSPACE_SCAN = "workspace_scan"
STRATEGY_POSTIMAGE_ONLY = "postimage_only"
STRATEGY_RECEIPT_ONLY = "receipt_only"

RECEIPT_INTENT_THEN_TERMINAL = "intent_then_terminal"
RECEIPT_TERMINAL_ONLY = "terminal_only"

ROLLBACK_EXACT = "exact"
ROLLBACK_NONE = "none"

#: Side-effect classes that mean "this tool can change local bytes through this process".
LOCAL_MUTATION_CLASSES = frozenset({"workspace_write", "sandbox_command", "validation_command"})

SCOPES = frozenset({SCOPE_WORKSPACE, SCOPE_MACHINE, SCOPE_EXTERNAL})
EFFECT_CLASSES = frozenset({CLASS_REVERSIBLE, CLASS_IRREVERSIBLE})
STRATEGIES = frozenset(
    {STRATEGY_DECLARED_PATHS, STRATEGY_WORKSPACE_SCAN, STRATEGY_POSTIMAGE_ONLY, STRATEGY_RECEIPT_ONLY}
)
RECEIPT_LIFECYCLES = frozenset({RECEIPT_INTENT_THEN_TERMINAL, RECEIPT_TERMINAL_ONLY})
ROLLBACK_SUPPORTS = frozenset({ROLLBACK_EXACT, ROLLBACK_NONE})


@dataclass(frozen=True)
class MutationCapability:
    tool: str
    scope: str
    effect_class: str
    snapshot_strategy: str
    receipt_lifecycle: str
    rollback_support: str
    recorder: str
    notes: str = ""

    def problems(self) -> list[str]:
        """Every way this declaration contradicts itself. Empty means it may be registered."""
        found: list[str] = []
        if not str(self.tool or "").strip():
            found.append("a capability must name its tool")
        if self.scope not in SCOPES:
            found.append(f"unknown scope {self.scope!r}")
        if self.effect_class not in EFFECT_CLASSES:
            found.append(f"unknown effect class {self.effect_class!r}")
        if self.snapshot_strategy not in STRATEGIES:
            found.append(f"unknown snapshot strategy {self.snapshot_strategy!r}")
        if self.receipt_lifecycle not in RECEIPT_LIFECYCLES:
            found.append(f"unknown receipt lifecycle {self.receipt_lifecycle!r}")
        if self.rollback_support not in ROLLBACK_SUPPORTS:
            found.append(f"unknown rollback support {self.rollback_support!r}")
        if not str(self.recorder or "").strip():
            found.append("a mutation-capable tool must declare its recorder")
        if found:
            return found
        if self.scope == SCOPE_EXTERNAL:
            if self.effect_class == CLASS_REVERSIBLE:
                found.append("an external effect can never be called rollback-capable")
            if self.snapshot_strategy != STRATEGY_RECEIPT_ONLY:
                found.append("an external effect records receipts, not local snapshots")
            if self.rollback_support != ROLLBACK_NONE:
                found.append("an external effect offers no local rollback")
        else:
            if self.effect_class == CLASS_REVERSIBLE:
                if self.snapshot_strategy not in {STRATEGY_DECLARED_PATHS, STRATEGY_WORKSPACE_SCAN}:
                    found.append("a reversible effect must capture preimages (declared paths or workspace scan)")
                if self.receipt_lifecycle != RECEIPT_INTENT_THEN_TERMINAL:
                    found.append("a reversible effect is journaled INTENDED before TERMINAL")
                if self.rollback_support != ROLLBACK_EXACT:
                    found.append("a reversible effect supports exact rollback or it is not reversible")
            else:
                if self.snapshot_strategy not in {STRATEGY_POSTIMAGE_ONLY, STRATEGY_RECEIPT_ONLY}:
                    found.append("an irreversible local effect may not claim a preimage strategy")
                if self.rollback_support != ROLLBACK_NONE:
                    found.append("an irreversible effect offers no rollback")
        if self.scope == SCOPE_MACHINE and self.snapshot_strategy == STRATEGY_WORKSPACE_SCAN:
            found.append("no whole-machine snapshots: a machine-scope effect observes only its declared paths")
        if self.scope == SCOPE_WORKSPACE and self.snapshot_strategy == STRATEGY_RECEIPT_ONLY:
            found.append("a workspace effect changes local bytes; it must journal more than a receipt")
        return found

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> MutationCapability | None:
        data = dict(payload or {})
        if not data:
            return None
        try:
            return cls(
                tool=str(data.get("tool") or ""),
                scope=str(data.get("scope") or ""),
                effect_class=str(data.get("effect_class") or ""),
                snapshot_strategy=str(data.get("snapshot_strategy") or ""),
                receipt_lifecycle=str(data.get("receipt_lifecycle") or ""),
                rollback_support=str(data.get("rollback_support") or ""),
                recorder=str(data.get("recorder") or ""),
                notes=str(data.get("notes") or ""),
            )
        except TypeError:
            return None


class CapabilityError(ValueError):
    """A mutation capability declaration contradicts itself. Raised at registration, never at dispatch."""


def validated(capability: MutationCapability) -> MutationCapability:
    problems = capability.problems()
    if problems:
        raise CapabilityError(f"{capability.tool}: {('; '.join(problems)).capitalize()}")
    return capability


__all__ = [
    "CLASS_IRREVERSIBLE",
    "CLASS_REVERSIBLE",
    "LOCAL_MUTATION_CLASSES",
    "RECEIPT_INTENT_THEN_TERMINAL",
    "RECEIPT_TERMINAL_ONLY",
    "ROLLBACK_EXACT",
    "ROLLBACK_NONE",
    "SCOPE_EXTERNAL",
    "SCOPE_MACHINE",
    "SCOPE_WORKSPACE",
    "STRATEGY_DECLARED_PATHS",
    "STRATEGY_POSTIMAGE_ONLY",
    "STRATEGY_RECEIPT_ONLY",
    "STRATEGY_WORKSPACE_SCAN",
    "CapabilityError",
    "MutationCapability",
    "validated",
]
