"""One turn, one planner artifact — across every `source_context` copy.

Recovered 2026-08-29 from `a27c353a`, which was dropped in a port (its sibling commit from the
same branch landed; this one did not) and shipped with no test at all. The defect it fixes is a
paid one: `bind_provider_deadline` and friends copy `source_context`, so two consumers of the SAME
turn each installed their own artifact and paid TWO generations of the same classification
question — measured on the conductor's clause ask racing the generic planner's ask.

These tests are what the original never had: they fail if the cross-copy registry is removed.
"""

from __future__ import annotations

import pytest

from core.agent_runtime.turn_planner_hook import (
    _SHARED_PLANNER_ARTIFACT_KEY,
    SharedPlannerArtifact,
    ensure_shared_planner_artifact,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    """Each test owns the registry; a leaked turn id from a neighbour would fake a pass."""
    from core.agent_runtime import turn_planner_hook

    turn_planner_hook._ARTIFACTS_BY_TURN.clear()
    yield
    turn_planner_hook._ARTIFACTS_BY_TURN.clear()


def test_separate_context_copies_of_one_turn_share_one_artifact() -> None:
    """THE defect: two copies of one turn's context must not mint two artifacts."""
    first_copy = {"turn_id": "turn-alpha"}
    second_copy = {"turn_id": "turn-alpha"}  # a genuine copy, not the same dict

    first = ensure_shared_planner_artifact(first_copy)
    second = ensure_shared_planner_artifact(second_copy)

    assert first is second, (
        "each context copy minted its own artifact — the turn pays two generations of the "
        "same classification question"
    )
    assert second_copy[_SHARED_PLANNER_ARTIFACT_KEY] is first, (
        "the shared artifact must be re-installed onto the later copy, so a third consumer "
        "reading that copy directly also sees it"
    )


def test_distinct_turns_never_share_an_artifact() -> None:
    """The registry must not leak one turn's classification into the next turn."""
    alpha = ensure_shared_planner_artifact({"turn_id": "turn-alpha"})
    beta = ensure_shared_planner_artifact({"turn_id": "turn-beta"})

    assert alpha is not beta
    assert alpha.turn_id == "turn-alpha" and beta.turn_id == "turn-beta"


def test_an_artifact_already_on_the_context_wins_without_touching_the_registry() -> None:
    """The pre-existing per-context path still short-circuits — this recovery is additive."""
    installed = SharedPlannerArtifact(turn_id="turn-gamma")
    context = {"turn_id": "turn-gamma", _SHARED_PLANNER_ARTIFACT_KEY: installed}

    assert ensure_shared_planner_artifact(context) is installed


def test_a_turnless_context_gets_a_fresh_artifact_and_is_never_registered() -> None:
    """No turn id means no identity to share on; such artifacts must not pollute the registry."""
    from core.agent_runtime import turn_planner_hook

    first = ensure_shared_planner_artifact({})
    second = ensure_shared_planner_artifact({})

    assert first is not second, "without a turn id there is nothing to key sharing on"
    assert turn_planner_hook._ARTIFACTS_BY_TURN == {}, "an unkeyed artifact must not be registered"


def test_registry_is_bounded_so_a_long_lived_process_cannot_grow_without_limit() -> None:
    """Stale turns are never valid to reuse, so the bound may clear — but it must hold."""
    from core.agent_runtime import turn_planner_hook

    limit = turn_planner_hook._ARTIFACT_REGISTRY_MAX
    for index in range(limit * 2 + 3):
        ensure_shared_planner_artifact({"turn_id": f"turn-{index}"})

    assert len(turn_planner_hook._ARTIFACTS_BY_TURN) <= limit, (
        "the registry grew past its bound — a long-lived daemon would leak every turn it served"
    )
    # And the mechanism still works immediately after a clear.
    left = ensure_shared_planner_artifact({"turn_id": "turn-after-clear"})
    right = ensure_shared_planner_artifact({"turn_id": "turn-after-clear"})
    assert left is right


def test_none_context_is_tolerated() -> None:
    """Callers pass None on paths with no context; this must not raise."""
    assert isinstance(ensure_shared_planner_artifact(None), SharedPlannerArtifact)
