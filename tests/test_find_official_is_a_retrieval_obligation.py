"""Being told to "find the official <spec>" is an explicit retrieval demand.

The one authority that owns "this turn demanded retrieval, so it requires current information" is
``core.task_router.looks_like_explicit_lookup_request`` (consumed by the ``explicit_lookup_request``
signal in ``core.current_information_signals``). It already covered "look it up", "search online",
"browse", and web-marked "find/search/google …". It did NOT cover a bare "find the official X":

    "Find the official weight of the Apple Watch Ultra 2."
        -> answer_mode=DIRECT  current_information_required=False   (measured 2026-09-08)

So the unsourced-claim guard never fired and the model supplied the figure from its weights. An
official published spec is an external fact; the fix teaches the ONE authority to recognise it
(rather than adding a second escalation path elsewhere), and — on the same seam — to honour a
within-turn retraction, so a cancelled "find the official …" is not forced to retrieve.
"""
from __future__ import annotations

import pytest

from core.execution_requirements import requirements_for
from core.task_router import looks_like_explicit_lookup_request
from core.unsourced_current_claim import inspect_unsourced_current_claim
from core.within_turn_retraction import turn_retracts_an_instruction

CONTEXT = {"surface": "api"}

FIND_OFFICIAL = {
    "watch": "Find the official weight of the Apple Watch Ultra 2.",
    "portal": "Find the official screen size of the Sony PlayStation Portal.",
    "pixel": "Find me the official battery capacity of the Google Pixel 9 Pro.",
}

# A retrieval that is cancelled inside the same turn, phrased so analyze_retrieval_constraints does
# NOT see a prohibition (forbids_external_retrieval=False). The retraction needs a LIVE follow-up
# request to be a retraction at all, so that continuation is an ordinary imperative -- never a
# "do not browse"-style clause, which the analyzer now reads as a prohibition and would shadow the
# guard under test. The turn_retracts_an_instruction guard on the lookup authority is then the only
# thing that can stop the cancelled search — which is what makes it load-bearing.
RETRACTED_SEARCH = (
    "Find me the top-rated coffee grinder under $100 on the web. WAIT. Abort the product search. "
    "Instead, tell me which grinder you already know."
)

# "find" that is not a web lookup at all. Must not be dragged into retrieval.
FIND_NOT_A_LOOKUP = {
    "bug": "Find the bug in this function that raises a KeyError.",
    "derivative": "Find the derivative of x^2 + 3x.",
    "synonym": "Find a synonym for the word happy.",
}


@pytest.mark.parametrize("name", sorted(FIND_OFFICIAL))
def test_find_the_official_spec_requires_current_information(name: str) -> None:
    requirements = requirements_for(FIND_OFFICIAL[name], source_context=CONTEXT)
    assert requirements.current_information_required, (
        f"{name}: the user said to find the OFFICIAL value and the turn acquired no obligation to "
        f"look — the unsourced-claim guard cannot fire and the model's own number ships unchecked"
    )
    assert requirements.answer_mode == "GROUNDED"


@pytest.mark.parametrize("name", sorted(FIND_OFFICIAL))
def test_the_one_authority_recognises_find_official(name: str) -> None:
    assert looks_like_explicit_lookup_request(FIND_OFFICIAL[name]) is True


def test_a_retracted_search_is_not_read_as_an_active_lookup() -> None:
    """The safety half: the retraction guard on the lookup authority is load-bearing.

    The constraint analyzer does not see this within-turn retraction, so removing the guard would
    let a cancelled search read as an active retrieval demand again.
    """
    from core.retrieval_constraints import analyze_retrieval_constraints

    constraints = analyze_retrieval_constraints(RETRACTED_SEARCH)
    assert not constraints.forbids_external_retrieval and not constraints.forbids_all_tools, (
        "fixture drift: the constraint analyzer must NOT catch this retraction, else the guard is "
        "not what is being tested"
    )
    assert turn_retracts_an_instruction(RETRACTED_SEARCH) is True, "fixture drift: must read retracted"
    assert looks_like_explicit_lookup_request(RETRACTED_SEARCH) is False, (
        "a cancelled search was still read as an active retrieval demand — the retraction guard "
        "on looks_like_explicit_lookup_request is what must stop it"
    )


@pytest.mark.parametrize("name", sorted(FIND_NOT_A_LOOKUP))
def test_find_that_is_not_a_web_lookup_stays_direct(name: str) -> None:
    assert looks_like_explicit_lookup_request(FIND_NOT_A_LOOKUP[name]) is False


def test_the_obligation_reaches_the_guard_that_uses_it() -> None:
    """The point of the repair: the fabrication is CAUGHT, not merely reclassified.

    A reason code nobody consumes would satisfy the tests above and fix nothing. This feeds an
    answer that asserts an official value with nothing retrieved through the actual guard.
    """
    requirements = requirements_for(FIND_OFFICIAL["watch"], source_context=CONTEXT)
    verdict = inspect_unsourced_current_claim(
        answer="The Apple Watch Ultra 2 weighs 61.4 grams.",
        requires_current=bool(requirements.current_information_required),
        notes=[],  # retrieval produced nothing usable
    )
    assert verdict.unsupported, (
        "the turn requires current information, observed nothing, and states a measured value — "
        f"the guard must not let it stand. verdict={verdict.as_dict()}"
    )
