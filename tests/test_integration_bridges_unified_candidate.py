"""The three bridges the nine-lane integration exposed, pinned at their own seams.

Each one is a lane's rule that a SECOND decision site never asked. The regression tests
that caught them live in other files and drive whole turns; these pin the predicates
directly, so a future change that re-breaks one says which rule it broke.

None of these is a shape guard. Every case below is asserted in BOTH directions: the
turn that must be released, and the turn that must still be held.
"""

from __future__ import annotations

import pytest

from core.agent_runtime.agent import _one_model_call_already_answers_every_part
from core.agent_runtime.demand_ownership import demand_coverage

# The family that has a response-level completeness guard of its own: ordinary prose,
# nothing in it needing the world. Splitting these asks the model once per unit, and the
# model answers the WHOLE turn every time, so the merge appends the answer to itself.
ONE_MODEL_CALL_ANSWERS_ALL = [
    "Explain ocean blue. Calculate 39 × 24. Give 7-word title.",
    "Explain how soap works. Calculate 46 x 19. Give a 7-word title.",
]

# Turns the demand path MUST keep. Each needs something one model call cannot supply --
# a file on disk, a live quote, an observation of the world.
DEMAND_PATH_MUST_KEEP = [
    "Return exactly the second line of notes.txt. Calculate 37 x 19. "
    "Decide whether that line describes walking.",
    "No web. Read notes.txt and give the current ETH price.",
    "Explain how a hash table works and tell me the weather in Kaunas",
    "convert 100 usd to eur and explain what a hedge fund is",
    "weather in Kaunas and Rome plus explain inflation briefly",
]


@pytest.mark.parametrize("text", ONE_MODEL_CALL_ANSWERS_ALL)
def test_an_all_ordinary_multi_part_turn_stays_in_the_single_plain_lane(text: str) -> None:
    """BRIDGE 1. `turn_planner.plan_turn` already refuses to split this family. The
    demand path is a second decomposer that reached dispatch without asking."""
    assert _one_model_call_already_answers_every_part(text) is True


@pytest.mark.parametrize("text", DEMAND_PATH_MUST_KEEP)
def test_a_turn_needing_the_world_is_never_released_to_the_plain_lane(text: str) -> None:
    """The falsifiable direction of BRIDGE 1, and the reason it takes TWO authorities.

    `is_ordinary_multi_part_plain_task` alone reads True for the hash-table/weather
    turn, which the demand path must own -- plan_turn declining it is precisely how the
    demand path comes to see it. Asking `requirements_for` of every execution unit is
    what keeps that turn, and every other turn here, on the demand path.
    """
    assert _one_model_call_already_answers_every_part(text) is False


def test_the_workspace_read_lane_claims_nothing_of_a_pure_search_turn() -> None:
    """BRIDGE 2. The read lane's coverage probe is the file-READ recognizer, so it
    speaks for only half of what its handler serves. On a pure search turn it claims no
    unit, and a finalize verdict about it is not a verdict about the search."""
    coverage = demand_coverage('look through the workspace and find "runtime_capabilities"')
    assert coverage.unit_count == 2
    assert coverage.lane_unit_ids("workspace_read_fast_path") == ()


@pytest.mark.parametrize(
    "text",
    [
        "Return exactly the second line of notes.txt. Calculate 37 x 19. "
        "Decide whether that line describes walking.",
        "No web. Read notes.txt and give the current ETH price.",
    ],
)
def test_the_workspace_read_lane_still_claims_part_of_a_real_mixed_turn(text: str) -> None:
    """The falsifiable direction of BRIDGE 2: on every turn the veto exists to police,
    the read lane DOES claim part of the turn, so the veto is still asked."""
    coverage = demand_coverage(text)
    claimed = coverage.lane_unit_ids("workspace_read_fast_path")
    assert claimed, text
    assert len(claimed) < coverage.unit_count, text


def test_the_conductor_handoff_target_only_exists_on_a_mixed_turn() -> None:
    """BRIDGE 3. The conductor stands down so the DEMAND-OWNED MIXED lane can run a
    demand its plan cannot. That lane only takes a mixed turn, so on a non-mixed turn
    there is nobody to hand off to and standing down just discards the better answer."""
    # Two live-data reads: the catalog reads both units as the live lanes' own, so no hand-off
    # target exists and the conductor must not stand down.
    handed_off = "What is the weather in Rome and also what is the gold price"
    assert demand_coverage(handed_off).mixed is False
    # A purchase-shaped ask ("how much gold I can buy") is nobody's per-unit coverage under the
    # typed lane catalog (be427e87): it is the composite planner's, so beside a weather read the
    # turn IS mixed and the hand-off target exists. The earlier example turned on the pre-catalog
    # market-quote probe claiming it for the live lane.
    assert demand_coverage("What is weather in Rome also tell me how much gold I can buy").mixed is True

    incident = (
        "Return exactly the second line of notes.txt. Calculate 37 x 19. "
        "Decide whether that line describes walking."
    )
    assert demand_coverage(incident).mixed is True
