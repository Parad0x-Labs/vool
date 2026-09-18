"""M2 — one shared task-class → capability-family vocabulary.

Proven defect these tests were built against (RED at d8abf90b):

    The task router validates classification against 16 production task
    classes (``core.task_router._VALID_TASK_CLASSES``), but
    ``family_hint_from_task_class`` was keyed on a different, imaginary
    vocabulary (``file_read``, ``web_search``, ``image_gen``…) that the router
    never produces.  Only ``debugging`` and ``research`` intersected — 2/16.
    Tool-shaped classes such as ``file_inspection``, ``workspace_audit`` and
    ``shell_guidance`` returned ``None``, so their turns got the
    family-navigation fallback instead of their family's tools.

The repair is ONE shared authority — ``core.task_class_vocabulary`` — consumed
by both the router (valid classes) and capability discovery (family mapping),
with an explicit tool-neutral allowlist carrying written reasons.
"""
from __future__ import annotations

import pytest

from core import capability_graph as cg
from core.capability_graph import family_hint_from_task_class, init_graph, model_visible_specs, reset
from core.task_router import _VALID_TASK_CLASSES

try:  # absent at base — the tolerant import lets the RED runs show 2/16
    from core.task_class_vocabulary import NO_TOOL_FAMILY, TASK_CLASS_TO_FAMILY, VALID_TASK_CLASSES
except ImportError:  # pragma: no cover — base-state only
    NO_TOOL_FAMILY = {}
    TASK_CLASS_TO_FAMILY = {}
    VALID_TASK_CLASSES = frozenset()

RESERVED = ("respond.direct", "operator.list_tools", "capability.expand_family")


@pytest.fixture(autouse=True)
def _production_like_graph():
    reset()
    init_graph()
    yield
    reset()
    init_graph()


# ---------------------------------------------------------------------------
#  V1: completeness — every production task class is deliberately classified
# ---------------------------------------------------------------------------

def test_V1_every_valid_task_class_deliberately_classified() -> None:
    """Every router class maps to a family OR sits on the explicit
    tool-neutral allowlist with a written reason.  RED at base: 2/16 mapped,
    0 allowlisted, 14 unclassified."""
    mapped: list[str] = []
    neutral: list[str] = []
    unclassified: list[str] = []
    for cls in sorted(_VALID_TASK_CLASSES):
        hint = family_hint_from_task_class(cls)
        if hint is not None:
            mapped.append(cls)
        elif cls in NO_TOOL_FAMILY:
            neutral.append(cls)
        else:
            unclassified.append(cls)
    assert not unclassified, (
        f"{len(unclassified)} of {len(_VALID_TASK_CLASSES)} production task classes are "
        f"neither family-mapped nor explicitly tool-neutral: {unclassified} "
        f"(mapped={len(mapped)}: {mapped}; neutral={len(neutral)}: {neutral})"
    )
    # A class must be exactly one of the two — never both.
    both = set(mapped) & set(NO_TOOL_FAMILY)
    assert not both, f"classes both mapped and allowlisted tool-neutral: {sorted(both)}"


def test_V1b_allowlist_reasons_are_written() -> None:
    assert NO_TOOL_FAMILY, "the tool-neutral allowlist is missing entirely"
    for cls, reason in NO_TOOL_FAMILY.items():
        assert cls in _VALID_TASK_CLASSES, f"allowlist carries an unknown class: {cls}"
        assert str(reason).strip(), f"tool-neutral class {cls} has no written reason"


# ---------------------------------------------------------------------------
#  V2: the mandated minimum mappings
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("task_class", "family"),
    [
        ("file_inspection", "workspace"),
        ("workspace_audit", "workspace"),
        ("shell_guidance", "sandbox"),
        ("research", "web"),
        ("debugging", "workspace"),
    ],
)
def test_V2_mandated_minimum_mappings(task_class: str, family: str) -> None:
    assert family_hint_from_task_class(task_class) == family


# ---------------------------------------------------------------------------
#  V3: every mapped family exists in the canonical graph ontology
# ---------------------------------------------------------------------------

def test_V3_mapped_families_are_canonical() -> None:
    assert TASK_CLASS_TO_FAMILY, "the shared mapping is missing entirely"
    for cls, family in TASK_CLASS_TO_FAMILY.items():
        assert cls in _VALID_TASK_CLASSES, f"mapping carries an unknown class: {cls}"
        assert family in cg._CANONICAL_FAMILIES, (
            f"{cls} maps to {family!r}, which is not in the canonical ontology"
        )


def test_V3b_vocabulary_coherence_is_a_live_invariant() -> None:
    from core.task_class_vocabulary import vocabulary_violations

    assert vocabulary_violations(cg._CANONICAL_FAMILIES) == []


# ---------------------------------------------------------------------------
#  V4: one shared authority — the router consumes the same object
# ---------------------------------------------------------------------------

def test_V4_router_and_discovery_share_one_vocabulary() -> None:
    assert _VALID_TASK_CLASSES is VALID_TASK_CLASSES, (
        "the router's valid-class set is not the shared vocabulary object — "
        "two vocabularies again"
    )


# ---------------------------------------------------------------------------
#  V5: unknown task classes return None
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bogus", ["nonexistent_class", "", "   ", "file_read", "web_search"])
def test_V5_unknown_task_class_returns_none(bogus: str) -> None:
    assert family_hint_from_task_class(bogus) is None


# ---------------------------------------------------------------------------
#  V6: deterministic family selection per class
# ---------------------------------------------------------------------------

def test_V6_family_selection_deterministic() -> None:
    for cls in sorted(_VALID_TASK_CLASSES):
        first = family_hint_from_task_class(cls)
        for _ in range(3):
            assert family_hint_from_task_class(cls) == first
        if first is not None:
            specs_a = [s["intent"] for s in model_visible_specs(family_hint=first)]
            specs_b = [s["intent"] for s in model_visible_specs(family_hint=first)]
            assert specs_a == specs_b, f"{cls}: catalog varies between identical calls"


# ---------------------------------------------------------------------------
#  V7: catalog controls — the mapped family serves its own tools
# ---------------------------------------------------------------------------

def test_V7_workspace_classes_get_workspace_reads_not_web() -> None:
    for cls in ("file_inspection", "workspace_audit", "debugging"):
        family = family_hint_from_task_class(cls)
        visible = [s["intent"] for s in model_visible_specs(family_hint=family)]
        tools = [i for i in visible if i not in RESERVED]
        assert tools and all(i.startswith("workspace.") for i in tools), f"{cls}: {visible}"
        assert "workspace.read_file" in tools and "workspace.list_tree" in tools, f"{cls}: {visible}"
        assert "web.search" not in visible, f"{cls}: web tool leaked: {visible}"


def test_V7b_research_gets_web_not_workspace_writes() -> None:
    family = family_hint_from_task_class("research")
    visible = [s["intent"] for s in model_visible_specs(family_hint=family)]
    assert "web.search" in visible, visible
    # Family-true: every non-reserved tool is a web tool. (web.fetch rides the
    # runtime's web policy flag, so its presence is not pinned here.)
    tools = [i for i in visible if i not in RESERVED]
    assert tools and all(i.startswith("web.") for i in tools), visible
    assert not any(i.startswith("workspace.") for i in visible), visible


def test_V7c_shell_guidance_gets_sandbox() -> None:
    family = family_hint_from_task_class("shell_guidance")
    visible = [s["intent"] for s in model_visible_specs(family_hint=family)]
    assert "sandbox.run_command" in visible, visible


# ---------------------------------------------------------------------------
#  V8: ordinary conversation stays tool-neutral
# ---------------------------------------------------------------------------

def test_V8_ordinary_conversation_tool_neutral() -> None:
    for cls in ("chat_conversation", "general_advisory", "creative_ideation"):
        assert family_hint_from_task_class(cls) is None, (
            f"{cls} was forced into a tool family instead of staying neutral"
        )
        assert cls in NO_TOOL_FAMILY, f"{cls} is neutral but not explicitly allowlisted"
    # A neutral hint still yields the bounded family-navigation set, unchanged.
    visible = [s["intent"] for s in model_visible_specs(family_hint=None)]
    assert len(visible) <= cg._DEFAULT_MAX_CANDIDATES
    for intent in RESERVED:
        assert intent in visible


# ---------------------------------------------------------------------------
#  SAB: removing a mapped class must trip the completeness check
# ---------------------------------------------------------------------------

def test_SAB_removing_mapped_class_trips_completeness() -> None:
    """Sabotage: drop shell_guidance from the shared mapping.

    Proves test_V1/test_V3b are non-vacuous: the same violation check both
    rely on must name the dropped class."""
    import core.task_class_vocabulary as tcv

    saved = tcv.TASK_CLASS_TO_FAMILY
    try:
        mutilated = {k: v for k, v in saved.items() if k != "shell_guidance"}
        assert "shell_guidance" in saved, "fixture assumption broken"
        tcv.TASK_CLASS_TO_FAMILY = mutilated  # MALICIOUS: class removed

        violations = tcv.vocabulary_violations(cg._CANONICAL_FAMILIES)
        assert violations, "sabotage did not bite: no violation reported"
        assert any("shell_guidance" in v for v in violations), violations
        # And the served lookup indeed lost the class — the exact V2 failure.
        assert tcv.family_for_task_class("shell_guidance") is None
    finally:
        tcv.TASK_CLASS_TO_FAMILY = saved
