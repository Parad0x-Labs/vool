"""The blast matrix must name real dependents, stay a strict subset, and escalate on hubs.

The tool exists to answer one operational question: fix Z touched module B -- which tests
guard the features that depend on B? Its two failure directions are exactly opposite and
both defeat the purpose:

- SELECT-ALL degrades into "run the full suite every time", which is the cost this tool
  exists to avoid, and worse, it hides the answer to the actual question.
- SELECT-NOTHING (or missing a real dependent) is how a fix to Z silently breaks A.

So the tests pin both directions against the REAL repository graph, not a synthetic one:
a known leaf module must pull in its known direct test and stay far below the full suite;
a file that tests reach by READING it as text (never importing it) must still be selected;
and the always-run sentinels must exist on disk, because a renamed sentinel silently
shrinks the safety net.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ops.blast_matrix import HUB_FRACTION, SENTINELS, BlastMatrix, _module_name

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def matrix() -> BlastMatrix:
    return BlastMatrix()


def test_a_leaf_change_selects_its_real_dependents_and_nothing_like_everything(matrix: BlastMatrix) -> None:
    """core/within_turn_retraction.py is a measured leaf: imported by core/execution/planner.py
    and by its own test. Selection must include the direct test AND stay a small fraction of
    the suite -- the select-all failure direction is caught here."""

    report = matrix.select(["core/within_turn_retraction.py"], max_hops=0)
    selected = set(report["selected"])

    assert "tests/test_an_instruction_taken_back_is_not_an_instruction.py" in selected
    # MEASURED, not guessed: the UNBOUNDED closure of this leaf reaches 607/1085 tests,
    # because everything funnels through apps.vool_agent -- which is the coupling defect
    # the matrix exists to expose, not a selection bug. The per-fix set is the hop-bounded
    # one, and at hop 0 it must be a handful of directly-importing tests, not the repo.
    assert 0 < len(selected) < report["total_tests"] * 0.05, (
        f"a hop-0 leaf change selected {len(selected)}/{report['total_tests']} tests -- "
        "the matrix has degraded into select-(almost-)all"
    )


def test_a_transitive_dependent_is_reached_through_the_import_graph(matrix: BlastMatrix) -> None:
    """Reach must be MULTI-hop, and this test must prove depth, not adjacency.

    The first version asserted only that the planner (a DIRECT importer, hop 1) was in the
    closure -- and a sabotage that cut BFS to direct-importers-only sailed straight through
    it, because hop 1 still worked. Caught by the sabotage run, fixed here: the closure of
    this leaf must contain modules at distance >= 2, or a fix to Z can break an A that sits
    two imports away and the matrix will never name it.
    """

    dist, _ = matrix.affected_modules(["core/within_turn_retraction.py"])

    assert dist.get("core.execution.planner") == 1
    deep = {m: d for m, d in dist.items() if d >= 2}
    assert deep, (
        "the importer closure stops at direct importers -- transitive reach is dead, "
        "and every dependent more than one import away is invisible"
    )


def test_a_file_reached_by_reading_not_importing_is_still_selected(matrix: BlastMatrix) -> None:
    """The chat-page tests read core/vool_chat_page.py as source text and drive its JS --
    no import edge exists. The path-literal channel must catch them; an import-only matrix
    is blind to this entire class."""

    report = matrix.select(["core/vool_chat_page.py"])
    selected = report["selected"]

    assert "tests/test_the_event_log_shows_the_whole_chat.py" in selected


def test_lazy_function_local_imports_are_edges(matrix: BlastMatrix) -> None:
    """This repo imports inside function bodies pervasively (from core.x import y at call
    time). Those edges must exist in the graph, or half the runtime is invisible."""

    # planner.py imports within_turn_retraction ONLY inside function bodies.
    closure, _ = matrix.affected_modules(["core/within_turn_retraction.py"])

    assert "core.execution.planner" in closure, (
        "a function-local import produced no graph edge -- ast.walk coverage broke"
    )


def test_every_sentinel_exists_on_disk() -> None:
    """A renamed sentinel must fail loudly here, not silently shrink the always-run set."""

    missing = [s for s in SENTINELS if not (REPO / s).is_file()]

    assert not missing, f"sentinels missing on disk: {missing}"


def test_the_hub_threshold_is_a_real_escalation_not_a_constant_lie(matrix: BlastMatrix) -> None:
    """Selection strictly over HUB_FRACTION must set the escalation flag; at-or-under must
    not. Checked by driving select() against the flag's own inputs rather than re-deriving
    the ratio independently -- the flag IS the contract."""

    leaf = matrix.select(["core/within_turn_retraction.py"], max_hops=0)
    ratio = leaf["full_closure_test_count"] / leaf["total_tests"]

    # Escalation is judged on the FULL closure regardless of the hop bound asked for --
    # a narrow bound must never hide that the change is repo-wide.
    assert leaf["hub_escalation"] == (ratio > HUB_FRACTION)


def test_hop_bounding_shrinks_without_losing_the_direct_test(matrix: BlastMatrix) -> None:
    """The tiering contract itself: hops=0 is a subset of hops=2 is a subset of unbounded,
    and the direct test survives at every tier."""

    h0 = set(matrix.select(["core/within_turn_retraction.py"], max_hops=0)["selected"])
    h2 = set(matrix.select(["core/within_turn_retraction.py"], max_hops=2)["selected"])
    full = set(matrix.select(["core/within_turn_retraction.py"])["selected"])

    direct = "tests/test_an_instruction_taken_back_is_not_an_instruction.py"
    assert direct in h0 and direct in h2 and direct in full
    assert h0 <= h2 <= full
    assert len(h0) < len(full)


def test_module_name_resolution_is_repo_shaped() -> None:
    assert _module_name(REPO / "core/within_turn_retraction.py") == "core.within_turn_retraction"
    assert _module_name(REPO / "core/execution/planner.py") == "core.execution.planner"
    assert _module_name(REPO / "README.md") is None
