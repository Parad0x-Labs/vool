"""Semantic routing, Phase 0: types, instrumentation and measurement. No routing authority.

Nothing in this package decides a turn. It exists to make three things true before any behaviour
changes: the contract the resolver must satisfy is written down and pinned by tests; the path a turn
actually takes is recorded accurately; and the runtime's dependence on lexical vocabulary is
measured rather than argued about.

Imports are lazy through `__getattr__` so that `import core.semantic` costs nothing on a turn that
does not use it -- the runtime's hot path imports `core.semantic.reach` directly and that module
pulls in only the standard library.
"""
from __future__ import annotations

from typing import Any

__all__ = [
    "CANONICAL_REPRESENTATION",
    "AdmissionResult",
    "CanonicalText",
    "CertainResult",
    "EntitySpan",
    "GraphSemanticResolver",
    "IntentProposal",
    "ModelSemanticResolver",
    "PermissionRecord",
    "ReasonCode",
    "RequestShape",
    "ResolutionOutcome",
    "ResolutionState",
    "SemanticProposalBackend",
    "SpanBindingError",
    "ValidatedIntent",
    "admit",
    "admit_all",
    "build_resolution_receipt",
    "emit_resolution_receipt",
    "verify_receipt_against_reach",
]

_EXPORTS = {
    "CANONICAL_REPRESENTATION": "core.semantic.canonical_text",
    "CanonicalText": "core.semantic.canonical_text",
    "EntitySpan": "core.semantic.canonical_text",
    "SpanBindingError": "core.semantic.canonical_text",
    "AdmissionResult": "core.semantic.types",
    "CertainResult": "core.semantic.types",
    "GraphSemanticResolver": "core.semantic.types",
    "IntentProposal": "core.semantic.types",
    "ModelSemanticResolver": "core.semantic.resolver",
    "SemanticProposalBackend": "core.semantic.resolver",
    "PermissionRecord": "core.semantic.types",
    "ReasonCode": "core.semantic.types",
    "RequestShape": "core.semantic.types",
    "ResolutionOutcome": "core.semantic.types",
    "ResolutionState": "core.semantic.types",
    "ValidatedIntent": "core.semantic.types",
    "admit": "core.semantic.admission",
    "admit_all": "core.semantic.admission",
    "build_resolution_receipt": "core.semantic.receipt",
    "emit_resolution_receipt": "core.semantic.receipt",
    "verify_receipt_against_reach": "core.semantic.receipt",
}


def __getattr__(name: str) -> Any:
    module_path = _EXPORTS.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    return getattr(importlib.import_module(module_path), name)


def _probe_reaches_model_layer_for_tests() -> None:
    """Deliberately touch the model layer FROM this package, so a tripwire can be proven to fire.

    Exists only for `tests/semantic_phase0/test_semantic_zero_provider_invocations.py`. A proof that
    "the semantic layer never reaches the model layer" is worthless unless something demonstrates the
    detector firing when it does -- the first version of that proof counted successful adapter builds
    and reported zero in an environment where building was impossible, with a real injected call
    sitting in the code.

    Nothing in the runtime calls this. It is the control, and it is deliberately visible: if the
    semantic package ever legitimately needs the model layer, this function stops being the only
    place that reaches it and the test that asserts so will say which other one does.
    """
    from core.model_registry import ModelRegistry

    ModelRegistry().list_manifests(limit=1)


def _probe_direct_adapter_for_tests() -> None:
    """Reach a provider WITHOUT the registry, so the direct-path tripwire can be proven to fire.

    The registry-only detector stayed green against exactly this shape. Nothing in the runtime calls
    this; it exists so the proof's control covers the path the review actually used.
    """
    from adapters.base_adapter import ModelAdapter

    for cls in ModelAdapter.__subclasses__():
        for method in ("run_text_task", "invoke", "health_check"):
            if method in vars(cls):
                try:
                    getattr(cls, method)(object.__new__(cls))
                except Exception:
                    return
                return
