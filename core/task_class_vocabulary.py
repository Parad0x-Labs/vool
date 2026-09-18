"""One shared task-class → capability-family vocabulary (M2).

This module is the SINGLE authority for the production task-class vocabulary
and its relationship to capability families.  Two consumers, one source:

- ``core.task_router`` validates model/regex classification against
  ``VALID_TASK_CLASSES`` (the same frozenset object — no copy).
- ``core.capability_graph.family_hint_from_task_class`` resolves the family
  hint through ``family_for_task_class``.

Every production task class is classified DELIBERATELY, exactly one way:

- mapped in ``TASK_CLASS_TO_FAMILY`` to a canonical capability family, or
- listed in ``NO_TOOL_FAMILY`` as explicitly tool-neutral, with the written
  reason.  Tool-neutral is a decision, not an omission: those turns keep the
  bounded family-navigation set instead of being forced into a fake family.

Unknown task classes resolve to ``None`` — never a guessed family, never
keyword routing.

Coherence is enforced, not hoped for: ``vocabulary_violations`` checks that
the two structures partition the vocabulary and stay inside the canonical
graph ontology, and ``core.capability_graph`` raises through it at import.

This module imports nothing from ``core`` so both consumers can depend on it
in any order (zero import-cycle risk).
"""
from __future__ import annotations

# The production task-class vocabulary. The task router validates every
# classification result against exactly this set.
VALID_TASK_CLASSES: frozenset[str] = frozenset(
    {
        "chat_conversation",
        "research",
        "debugging",
        "system_design",
        "integration_orchestration",
        "shell_guidance",
        "file_inspection",
        "workspace_audit",
        "config",
        "dependency_resolution",
        "security_hardening",
        "business_advisory",
        "food_nutrition",
        "relationship_advisory",
        "creative_ideation",
        "general_advisory",
    }
)

# Tool-shaped classes → the canonical capability family whose tools serve them.
# Values must exist in core.capability_graph._CANONICAL_FAMILIES.
TASK_CLASS_TO_FAMILY: dict[str, str] = {
    # Workspace lane: these turns read, audit or repair the active tree.
    "file_inspection": "workspace",
    "workspace_audit": "workspace",
    "debugging": "workspace",
    # Config turns in this runtime are config-FILE work (reasoning_engine and
    # runtime_execution_tools both treat config as a workspace-reading class);
    # the zero-implementation "system" family would serve nothing.
    "config": "workspace",
    # Manifests/lockfiles live in the tree; workspace.validate runs the checks.
    "dependency_resolution": "workspace",
    # A design grounded in the actual tree beats one from memory; the ranked
    # workspace catalog leads with read/list/search, so no write pressure.
    "system_design": "workspace",
    # Hardening reviews code and config in the tree.
    "security_hardening": "workspace",
    # Bounded local command execution.
    "shell_guidance": "sandbox",
    # Live evidence gathering.
    "research": "web",
    # Task-envelope orchestration has its own family.
    "integration_orchestration": "orchestration",
}

# Explicitly tool-neutral classes, each with the written reason. A neutral
# turn keeps the bounded family-navigation set (model can still discover);
# it is never forced into a family whose tools are not its subject.
NO_TOOL_FAMILY: dict[str, str] = {
    "chat_conversation": (
        "ordinary conversation; forcing a family would push every greeting "
        "toward a tool call"
    ),
    "general_advisory": "advisory prose; no capability family is its subject",
    "business_advisory": "advisory prose; no capability family is its subject",
    "food_nutrition": "advisory prose; no capability family is its subject",
    "relationship_advisory": "advisory prose; no capability family is its subject",
    "creative_ideation": (
        "creative writing/ideation; tools are not its subject and unsolicited "
        "tool pressure degrades the lane"
    ),
}


def family_for_task_class(task_class: str) -> str | None:
    """The capability family for a task class, or None.

    None means either explicitly tool-neutral (``NO_TOOL_FAMILY``) or an
    unknown class — both keep the caller's family-navigation fallback.
    Deterministic lookup only; no keyword routing.
    """
    return TASK_CLASS_TO_FAMILY.get(str(task_class or "").strip().lower())


def vocabulary_violations(canonical_families: frozenset[str]) -> list[str]:
    """Coherence check: empty list, or human-readable violations.

    The canonical family set is passed in (it is owned by
    ``core.capability_graph``) so this module stays import-free.
    """
    violations: list[str] = []
    mapped = set(TASK_CLASS_TO_FAMILY)
    neutral = set(NO_TOOL_FAMILY)

    for cls in sorted(mapped & neutral):
        violations.append(f"task class {cls!r} is both family-mapped and tool-neutral")
    for cls in sorted(VALID_TASK_CLASSES - mapped - neutral):
        violations.append(
            f"task class {cls!r} is neither family-mapped nor explicitly tool-neutral"
        )
    for cls in sorted((mapped | neutral) - VALID_TASK_CLASSES):
        violations.append(f"{cls!r} is not a production task class")
    for cls, family in sorted(TASK_CLASS_TO_FAMILY.items()):
        if family not in canonical_families:
            violations.append(
                f"task class {cls!r} maps to {family!r}, not a canonical family"
            )
    for cls, reason in sorted(NO_TOOL_FAMILY.items()):
        if not str(reason).strip():
            violations.append(f"tool-neutral class {cls!r} has no written reason")
    return violations


def assert_vocabulary_coherent(canonical_families: frozenset[str]) -> None:
    """Raise if the vocabulary is incoherent (consumed at import by the graph)."""
    violations = vocabulary_violations(canonical_families)
    if violations:
        raise RuntimeError(
            "task-class vocabulary incoherent: " + "; ".join(violations)
        )


__all__ = [
    "NO_TOOL_FAMILY",
    "TASK_CLASS_TO_FAMILY",
    "VALID_TASK_CLASSES",
    "assert_vocabulary_coherent",
    "family_for_task_class",
    "vocabulary_violations",
]
