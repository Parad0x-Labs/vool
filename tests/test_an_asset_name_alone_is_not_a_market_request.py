"""SENTINEL G4, 2026-08-06: naming a commodity is not asking what it costs.

The defect, exactly as G4 exposed it through the public chat path:

    "Explain gold structure."                            -> market_quote("Gold")
    "Not price — I mean the chemical structure of gold." -> market_quote("Gold")

Traced to its FIRST wrong production decision, in `core.execution_requirements.
_live_data_classification`:

    has_price = bool(price_assets_named(text)) or ...

`price_assets_named` answers "which asset aliases does this message NAME?" -- by presence alone,
no market word required. That line read the answer as "is this a market request?". They are
different questions, and `gold` is a metal before it is a ticker: naming it admitted the turn to
LIVE_DATA with the `market_prices` toolset, after which every step was individually correct and
collectively wrong (`build_live_data_plan` -> `_market_subtask` -> `operation="market_quote"`).

Every test here drives the REAL production entry point, `VoolAgent.run_once()` -- the same method
`apps/vool_api_server.py` calls -- and observes what the production planner actually SCHEDULED,
not what a helper predicate returns in isolation. `_is_market_request(...)`-style unit coverage
lives in `test_market_intent.py`; this file exists because G4 was a routing defect and a routing
defect has to be disproved at the router.
"""

from __future__ import annotations

from unittest import mock

import pytest

from apps.vool_agent import VoolAgent

SOURCE_CONTEXT = {"surface": "openclaw", "platform": "openclaw"}

# The G4 brief's required non-market controls, verbatim, plus the two substring false positives
# found while tracing it ("now" is a substring of "know", which admitted the market lane).
SEMANTIC_REQUESTS = [
    "Explain gold structure.",
    "What is the chemical structure of gold?",
    "Describe the atomic structure of gold.",
    "Why is gold yellow?",
    "How is gold formed?",
    "What properties does gold have?",
    "Compare gold and copper conductivity.",
    "What is gold used for in electronics?",
    "Not price — I mean the chemical structure of gold.",
    "Explain silver structure.",
    "Explain copper conductivity.",
    "Do you know the atomic structure of gold?",
    "Is gold known to conduct electricity?",
]

# The G4 brief's required market controls, verbatim. The repair must not narrow any of these.
MARKET_REQUESTS = [
    "Gold price",
    "Current gold price",
    "What's gold trading at?",
    "Gold market value today",
    "How much is gold right now?",
    "Has gold gone up today?",
    "Current XAU price",
    "BTC price",
    "ETH price",
]


@pytest.fixture
def agent() -> VoolAgent:
    return VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")


def _drive(agent: VoolAgent, text: str) -> tuple[list[tuple[str, str]], bool]:
    """Run the real turn and report (scheduled subtasks, ordinary-lane-was-reached).

    `build_live_data_plan` is wrapped, not replaced: the REAL planner runs and its REAL output is
    recorded, so this observes production scheduling rather than asserting against a double. The
    ordinary frontdoor is stubbed to a canned result purely to stop the turn there -- reaching it
    at all is the signal (the live-data lane declined this turn), and stopping keeps the test off
    the model lane.
    """
    import core.agent_runtime.live_data_plan as live_data_plan_module

    scheduled: list[tuple[str, str]] = []
    real_build = live_data_plan_module.build_live_data_plan

    def recording_build(user_text, **kwargs):
        plan = real_build(user_text, **kwargs)
        if plan is not None:
            scheduled.extend((task.operation, task.entity) for task in plan.subtasks)
        return plan

    reached_ordinary = {"hit": False}

    def stub_frontdoor(*_args, **_kwargs):
        reached_ordinary["hit"] = True
        return {"result": {"response": "ordinary lane", "model_calls": 0, "route": "test:ordinary"}}

    with mock.patch.object(live_data_plan_module, "build_live_data_plan", new=recording_build), \
         mock.patch.object(VoolAgent, "_handle_turn_frontdoor", new=stub_frontdoor):
        agent.run_once(text, source_context=dict(SOURCE_CONTEXT))
    return scheduled, reached_ordinary["hit"]


# ---------------------------------------------------------------------------------------------
# The defect: a semantic request about an asset must not schedule a market lookup.
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("text", SEMANTIC_REQUESTS)
def test_a_semantic_request_about_an_asset_is_not_classified_live_data(text: str) -> None:
    """The FIRST wrong production decision, pinned at its own seam.

    Kept separate from the scheduling test below on purpose. The repair has two gates -- this
    classifier, and a defense-in-depth gate on the plan builder's presence-pass -- and a mutation
    that reverts only ONE of them is absorbed by the other, so a scheduling-only assertion cannot
    tell whether either gate is load-bearing. This test goes red the moment `_live_data_classification`
    treats asset presence as market evidence again, which is exactly the defect G4 reported.
    """
    from core.execution_requirements import requirements_for

    requirements = requirements_for(text)
    assert requirements.answer_mode != "LIVE_DATA", (
        f"{text!r} was classified {requirements.answer_mode} with toolsets "
        f"{requirements.allowed_toolsets!r} -- naming an asset is not a market request"
    )
    assert "market_prices" not in requirements.allowed_toolsets


@pytest.mark.parametrize("text", SEMANTIC_REQUESTS)
def test_a_semantic_request_about_an_asset_schedules_no_market_quote(agent: VoolAgent, text: str) -> None:
    scheduled, reached_ordinary = _drive(agent, text)

    assert not [op for op, _entity in scheduled if op == "market_quote"], (
        f"{text!r} scheduled a market lookup: {scheduled!r} -- naming an asset is not asking for "
        "its price"
    )
    assert reached_ordinary, (
        f"{text!r} was claimed by a live-data lane instead of reaching the ordinary turn"
    )


@pytest.mark.parametrize("text", MARKET_REQUESTS)
def test_a_genuine_market_request_still_schedules_a_market_quote(agent: VoolAgent, text: str) -> None:
    """The other half of the invariant, and the one a careless fix breaks: every market phrasing
    the brief lists must still reach the market lane."""
    scheduled, _reached_ordinary = _drive(agent, text)

    assert [op for op, _entity in scheduled if op == "market_quote"], (
        f"{text!r} scheduled no market lookup: {scheduled!r} -- the repair narrowed a real market "
        "request"
    )


# ---------------------------------------------------------------------------------------------
# Explicit correction, single-turn and across two turns.
# ---------------------------------------------------------------------------------------------


def test_an_explicit_not_price_correction_is_not_read_as_a_price_request(agent: VoolAgent) -> None:
    """The word `price` is present, and the user is using it to say the opposite. A containment
    test cannot tell those apart, which is why the pre-repair code could not."""
    scheduled, reached_ordinary = _drive(agent, "Not price — I mean the chemical structure of gold.")

    assert not [op for op, _entity in scheduled if op == "market_quote"], scheduled
    assert reached_ordinary


def test_a_second_turn_correction_is_not_dragged_back_to_market_by_the_prior_entity(
    agent: VoolAgent,
) -> None:
    """Turn 1 is a real price request; turn 2 explicitly withdraws it. The grounded entity is still
    gold, and that must not be enough to force turn 2 back into the market lane.

    Both turns run through the real `run_once` on the same session, so any follow-up/harvest state
    the first turn leaves behind is genuinely in play for the second -- this is the stickiness the
    brief asks about, not a fresh-process simulation of it.
    """
    session = "g4-two-turn-correction"

    import core.agent_runtime.live_data_plan as live_data_plan_module

    turn_two_scheduled: list[tuple[str, str]] = []
    real_build = live_data_plan_module.build_live_data_plan
    recording_enabled = {"on": False}

    def recording_build(user_text, **kwargs):
        plan = real_build(user_text, **kwargs)
        if plan is not None and recording_enabled["on"]:
            turn_two_scheduled.extend((task.operation, task.entity) for task in plan.subtasks)
        return plan

    def stub_frontdoor(*_args, **_kwargs):
        return {"result": {"response": "ordinary lane", "model_calls": 0, "route": "test:ordinary"}}

    with mock.patch.object(live_data_plan_module, "build_live_data_plan", new=recording_build), \
         mock.patch.object(VoolAgent, "_handle_turn_frontdoor", new=stub_frontdoor):
        agent.run_once(
            "What's the gold price?",
            session_id_override=session,
            source_context=dict(SOURCE_CONTEXT),
        )
        recording_enabled["on"] = True
        agent.run_once(
            "Actually not price — explain its atomic structure.",
            session_id_override=session,
            source_context=dict(SOURCE_CONTEXT),
        )

    assert not [op for op, _entity in turn_two_scheduled if op == "market_quote"], (
        f"the correction turn was forced back into the market lane: {turn_two_scheduled!r}"
    )


# ---------------------------------------------------------------------------------------------
# Generalization: the repair is about ambiguous asset names, not about the word "gold".
# ---------------------------------------------------------------------------------------------


# ---------------------------------------------------------------------------------------------
# Inflected market phrasings, through the real planner.
#
# ARGUS measured these planning a market lookup at the released base and planning NOTHING on the
# first draft of this repair: word-boundary matching had been introduced over a vocabulary that
# listed only singular base forms, so "prices" stopped matching "price". That was a branch-
# introduced false negative, not a G4 behaviour change, and these pin the restoration end to end --
# including the resolved ENTITIES, since a plural request that plans the wrong asset is not fixed.
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected_entities"),
    [
        ("Gold prices", ["Gold"]),
        ("Bitcoin prices", ["Bitcoin"]),
        ("silver prices", ["Silver"]),
        ("btc and eth prices", ["Bitcoin", "Ethereum"]),
        ("btc and eth and sol prices", ["Bitcoin", "Ethereum", "Solana"]),
        ("gold and silver prices", ["Gold", "Silver"]),
        ("gold quotes", ["Gold"]),
        ("gold costs", ["Gold"]),
        ("What are the going rates for gold?", ["Gold"]),
        # natural equivalents of each restored inflection
        ("What are gold prices today?", ["Gold"]),
        ("Show me bitcoin prices", ["Bitcoin"]),
        ("what are the gold rates", ["Gold"]),
        ("gold quotes please", ["Gold"]),
        ("how is gold priced", ["Gold"]),
        ("gold valuations", ["Gold"]),
        # second regression wave: the polarity-prefixed and gradable families, measured by ARGUS
        # as still dead after the plural fix.
        ("Is gold overpriced?", ["Gold"]),
        ("Is gold overvalued?", ["Gold"]),
        ("Is bitcoin overvalued?", ["Bitcoin"]),
        ("Is gold undervalued?", ["Gold"]),
        ("Is gold costlier than silver?", ["Gold", "Silver"]),
        ("the costliest gold", ["Gold"]),
        ("Is gold pricey?", ["Gold"]),
        ("gold mispriced", ["Gold"]),
        ("Is gold worthless?", ["Gold"]),
    ],
)
def test_an_inflected_market_request_still_schedules_the_right_market_quotes(
    agent: VoolAgent, text: str, expected_entities: list[str]
) -> None:
    scheduled, _reached_ordinary = _drive(agent, text)
    entities = sorted(entity for op, entity in scheduled if op == "market_quote")

    assert entities == sorted(expected_entities), (
        f"{text!r} scheduled {entities!r}, expected {sorted(expected_entities)!r}"
    )


def test_the_recency_false_positive_is_now_closed(agent: VoolAgent) -> None:
    """It moved, deliberately, and this records the move the previous expectation asked for.

    This test used to assert the OPPOSITE -- that "Explain gold structure today." routes MARKET --
    and said so explicitly: "the recency false positive changed -- that may be an improvement, but
    it is not what this closure measured, so re-measure against base before updating this
    expectation." It is now measured, and it is an improvement.

    A bare recency marker admitted the market lane through `market_quote_intent_present`, so
    "today" beside "gold" was enough. `core.semantic_claim_authority` asks a narrower question --
    is a market term predicated of THIS mention -- and answers it from
    `core.market_intent.market_term_spans`, which excludes recency for the same reason
    `market_semantics_present` always has: "tonight" is not evidence that the word beside it is an
    asset. Closing this is the same rule that keeps "restaurants near me tonight" ordinary; the two
    cannot be separated, and neither should be.
    """
    scheduled, _reached_ordinary = _drive(agent, "Explain gold structure today.")

    assert not [op for op, _entity in scheduled if op == "market_quote"], (
        "a recency marker alone is authorizing an asset mention again -- that is the "
        "'restaurants near me tonight' hijack wearing a different noun"
    )
    # The control: the same noun with real market evidence bound to it still routes market.
    priced, _ = _drive(agent, "Gold price today.")
    assert [op for op, _entity in priced if op == "market_quote"], (
        "closing the recency false positive must not cost a genuine market request"
    )


@pytest.mark.parametrize(
    ("semantic", "market"),
    [
        ("Explain gold structure.", "Gold price"),
        ("Explain silver structure.", "Silver price"),
        # the restored plural must generalize the same way -- a gold-only plural fix fails row 2
        ("Explain gold structure.", "Gold prices"),
        ("Explain silver structure.", "Silver prices"),
    ],
)
def test_the_same_split_holds_for_every_ambiguous_asset_not_just_gold(
    agent: VoolAgent, semantic: str, market: str
) -> None:
    """`silver` is the second commodity this runtime registers under a name that is an ordinary
    English noun first (`_MARKET_QUOTE_TARGETS` registers gold and silver; `oil` and `copper` are
    NOT registered as bare aliases at this base commit, so they cannot demonstrate the split in
    either direction -- see the report's generalization note). A fix special-cased to `gold` passes
    the first row here and fails the second.
    """
    semantic_scheduled, semantic_reached_ordinary = _drive(agent, semantic)
    market_scheduled, _ = _drive(agent, market)

    assert not [op for op, _e in semantic_scheduled if op == "market_quote"], semantic_scheduled
    assert semantic_reached_ordinary
    assert [op for op, _e in market_scheduled if op == "market_quote"], market_scheduled
