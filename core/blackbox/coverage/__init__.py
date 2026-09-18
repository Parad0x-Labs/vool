"""Capability-driven Blackbox coverage: every local mutation path enters the journal or fails
closed. See ``capability`` (the declaration), ``registry`` (the gate), ``highrisk`` (path
detection), ``scan`` (bounded pre/post workspace scans), ``recorder`` (the wrapper),
``restore`` (exact rollback + crash recovery)."""
from core.blackbox.coverage.capability import (
    LOCAL_MUTATION_CLASSES,
    MutationCapability,
    validated,
)
from core.blackbox.coverage.registry import (
    CoverageDecision,
    capability_for,
    ensure_builtin_capabilities,
    mutation_coverage_decision,
    register_capability,
    registered_capabilities,
    reset_registered,
    uncovered_local_mutating_builtins,
)

__all__ = [
    "LOCAL_MUTATION_CLASSES",
    "CoverageDecision",
    "MutationCapability",
    "capability_for",
    "ensure_builtin_capabilities",
    "mutation_coverage_decision",
    "register_capability",
    "registered_capabilities",
    "reset_registered",
    "uncovered_local_mutating_builtins",
    "validated",
]
