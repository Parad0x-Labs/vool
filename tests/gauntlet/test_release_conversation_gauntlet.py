"""The release conversation gauntlet, executed hermetically.

Every turn below runs in a child process (see `tests/gauntlet/hermetic/`), because
`bootstrap_runtime_services` has no teardown and running it in the parent pytest interpreter
poisoned unrelated suites. Nothing in this file boots the runtime; it asserts on evidence a child
produced and returned as a tagged document.

The assertions themselves are unchanged from the in-process version -- only the arrange step moved.
That was deliberate: rewriting the checks during the migration would make a porting mistake
indistinguishable from a behaviour change.

Classifications are preserved exactly. A `strict=True` xfail here is a MEASURED release defect on
this branch; when production is repaired the test XPASSes, strict turns that into a failure, and the
marker has to be removed by hand. None of them were "fixed" by relaxing an expectation.

That mechanism has since fired for real. On the reliability release composition four of these
XPASSed and were promoted to ordinary tests by hand -- the G4 pair (repaired by SCALPEL d746f6f9)
and the G8 pair (repaired by THREADKEEPER ae52e712). Each promotion removed only the expectation:
the assertions were kept as written or strengthened, never relaxed, and none was converted to a
skip.

It fired a third time on the follow-up-obligation repair, promoting two more: G1's rebinding turn
("What about Tallinn?") and G1's aggregate ("Which one is warmer?"). Both were the same root cause --
a follow-up resolved against the prior TEXT, which offers no slot to put a new subject into -- and
both are repaired by resolving against a recorded semantic obligation instead
(`core.live_data_continuation`).

It fired a fourth time on the typo-recovery and exact-count repairs, promoting G1's THIRD xfail and
both G12 xfails. G1's clarification ("Kauans"/"talling") is repaired by typo-tolerant recovery
against the obligation's OWN SLOTS -- one substitution/insertion/deletion, or an equal-length
transposition, and nothing looser; no gazetteer, and no second recognizer. G12 is repaired by
`core.folder_overview.build_exact_file_facts`: the workspace is walked, every matching file is
read, and the answer carries the exact count and the largest-by-line-count file instead of the
repository tree. Every promotion below removed only the expectation marker: the assertions were
kept as written or strengthened, never relaxed, and none was converted to a skip.

Every remaining xfail below still reproduces.
"""
from __future__ import annotations

import re

import pytest

from tests.gauntlet.hermetic.protocol import EvidenceTurn
from tests.gauntlet.hermetic.runner import group_evidence


def _turn(group: str, key: str) -> EvidenceTurn:
    return EvidenceTurn(group_evidence(group)[key])


def _g1(index: int) -> EvidenceTurn:
    return EvidenceTurn(group_evidence("g1_continuation")["turns"][index])


# ------------------------------------------------------------------ harness self-defence
# If these break, every assertion below is worthless -- a green gauntlet would mean "the fake never
# ran", not "the app is correct". They assert the harness is really in the path.


def test_the_harness_actually_intercepts_the_weather_provider() -> None:
    turn = _turn("harness_self_defence", "weather")
    assert turn.status == 200
    assert turn.weather_requests == ["kaunas"], turn.describe()
    # 18.0 C is fixture-only. Seeing it proves the answer was built from the intercepted lookup.
    assert turn.has_number(18.0), turn.describe()


def test_the_harness_actually_intercepts_the_model_wire() -> None:
    turn = _turn("harness_self_defence", "wire")
    assert "WIRE-REACHED" in turn.reply, turn.describe()


def test_a_conversation_keeps_one_session_across_turns() -> None:
    first = _turn("harness_self_defence", "session_first")
    second = _turn("harness_self_defence", "session_second")
    assert first.session_id and first.session_id == second.session_id, (
        f"turns landed in different sessions: {first.session_id} vs {second.session_id}"
    )


# ------------------------------------------------------------------------------- G1
# Turn 1 grounds Kaunas, turn 2 grounds Tallinn, then a comparison, then the user's own
# typo-ridden restatement. The live failure was that the comparison lost both entities.


def test_g1_a_bare_follow_up_grounds_the_new_city() -> None:
    """REPAIRED. 'What about Tallinn?' names no weather, so nothing in the turn's own text could
    route it; it now resolves against the OBLIGATION the previous turn recorded and rebinds that
    obligation's slot to the city this turn names.

    Strengthened on promotion, in the same shape as G4/G8 were: the recorded defect was that the
    follow-up never became a lookup AT ALL, so asserting only the provider call would still pass on
    a turn that asked for Tallinn and then dropped the reading before the user saw it. Plan, provider
    and answer are asserted together -- what was decided, what was fetched, and what arrived."""
    second = _g1(1)
    assert "tallinn" in [w.strip().lower() for w in second.weather_requests], second.describe()
    planned = [entity.lower() for entity in second.planned_entities("weather_lookup")]
    assert planned == ["tallinn"], f"planned weather entities {planned}{second.describe()}"
    # 12.0 C is Tallinn's fixture-only reading. Seeing it proves the answer was built from this
    # turn's intercepted lookup, not from prose about a city.
    assert second.has_number(12.0), second.describe()


def test_g1_the_comparison_resolves_both_grounded_cities() -> None:
    """REPAIRED. 'Which one is warmer?' introduces no entity and cannot be answered from its own
    text; it aggregates over every slot the obligation accumulated.

    Strengthened on promotion: naming the cities is not enough. A turn that merely REPEATED the two
    names out of context would satisfy the original assertion while answering from nothing, which is
    the defect this scenario exists to catch. Both readings must be present, and both must have been
    observed on THIS turn -- a comparison served from a stale reading is the freshness failure
    `tests/test_a_bare_followup_still_asks_the_live_question.py` pins for the bare case."""
    turn = _g1(2)
    assert turn.mentions("kaunas"), turn.describe()
    assert turn.mentions("tallinn"), turn.describe()
    asked = {w.strip().lower() for w in turn.weather_requests}
    assert {"kaunas", "tallinn"} <= asked, f"comparison served without observing: {asked}{turn.describe()}"
    assert turn.has_number(18.0) and turn.has_number(12.0), turn.describe()


def test_g1_a_typo_heavy_clarification_still_recovers_both_referents() -> None:
    """REPAIRED. The user's own misspelled restatement ('Kauans', 'talling') now recovers the two
    grounded referents: the clarification's tokens are matched against the obligation's OWN SLOTS
    with the two tolerated edit shapes (one substitution/insertion/deletion, or an equal-length
    transposition -- 'kauans' for 'kaunas' is a keystroke swap, not a substitution), and the
    recovered set rebinds the obligation exactly as an aggregate does.

    Strengthened on promotion, in the same shape as the G4/G8/G1 promotions: naming the cities is
    not enough -- a turn that merely REPEATED the names out of context would satisfy it while
    answering from nothing, which is the defect this scenario exists to catch. Both readings must
    have been observed on THIS turn and must be present in the answer."""
    turn = _g1(3)
    assert turn.mentions("kaunas") and turn.mentions("tallinn"), turn.describe()
    asked = {w.strip().lower() for w in turn.weather_requests}
    assert {"kaunas", "tallinn"} <= asked, f"clarification served without observing: {asked}{turn.describe()}"
    assert turn.has_number(18.0) and turn.has_number(12.0), turn.describe()


def test_g1_an_obligation_opened_by_one_multipart_turn_still_aggregates() -> None:
    """The other way an obligation acquires several subjects: one turn naming two cities, rather
    than two turns naming one each.

    The multipart lane keeps its own record of what it grounded, and nothing above reaches it --
    measured by deleting that record, which left every other assertion in this file green. Riga and
    Warsaw deliberately appear nowhere else in the gauntlet, so this cannot pass on another
    scenario's state.
    """
    opening = EvidenceTurn(group_evidence("g1_continuation")["multipart_turns"][0])
    compare = EvidenceTurn(group_evidence("g1_continuation")["multipart_turns"][1])

    assert {"riga", "warsaw"} <= {w.strip().lower() for w in opening.weather_requests}, opening.describe()
    asked = {w.strip().lower() for w in compare.weather_requests}
    assert {"riga", "warsaw"} <= asked, f"the comparison observed {asked}{compare.describe()}"
    assert compare.mentions("riga") and compare.mentions("warsaw"), compare.describe()


def test_g1_a_lost_comparison_never_invents_a_third_city() -> None:
    """Whatever else the comparison turn does, it must not fabricate places.

    This one passes on the release and is a regression lock: degrading to "I cannot tell" is an
    acceptable failure, answering about Vilnius is not.
    """
    turn = _g1(2)
    invented = [
        city
        for city in ("vilnius", "riga", "warsaw", "helsinki", "berlin")
        if city in turn.low
    ]
    assert not invented, f"invented locations {invented}{turn.describe()}"
    for asked in turn.weather_requests:
        assert asked.strip().lower() in {"kaunas", "tallinn"}, turn.describe()


# ------------------------------------------------------------------------------- G3
# A specialist turn must release the lane. The live failure was a weather turn making the NEXT,
# unrelated turn answer badly.


def test_g3_a_general_question_works_on_its_own() -> None:
    turn = _turn("g3_g4_domain", "rsa_alone")
    assert turn.status == 200
    assert "GENERAL-ANSWER" in turn.reply, turn.describe()
    assert turn.weather_requests == [], turn.describe()


def test_g3_a_general_question_survives_a_preceding_live_data_turn() -> None:
    turn = _turn("g3_g4_domain", "rsa_after")
    assert "GENERAL-ANSWER" in turn.reply, turn.describe()
    assert "couldn't produce" not in turn.low, turn.describe()


def test_g3_an_unrelated_turn_does_not_re_run_a_weather_lookup() -> None:
    turn = _turn("g3_g4_domain", "rsa_after")
    assert turn.weather_requests == [], (
        f"an unrelated turn re-ran a weather lookup: {turn.weather_requests}{turn.describe()}"
    )


# ------------------------------------------------------------------------------- G4
# MEASURED on the branch that recorded this, once the market wire was actually instrumented:
#   "Explain gold structure."  -> planned [('market_quote', 'Gold')], provider asked for 'gold'
# An earlier version asserted `market_requests == []` while NOTHING ever appended to that list, so
# it passed vacuously and the lexical GOLD=>MARKET mutation survived it. The list is real, which is
# why the defect was visible at all.
#
# REPAIRED, and the two tests below are now ordinary passing tests.
# Attribution, established by isolation rather than assumed: at the gauntlet branch tip alone both
# reproduce (2 xfailed); merging SCALPEL d746f6f9 ("agent/scalpel-g4-gold-semantic-routing", which
# adds core/market_intent.py) and NOTHING else flips exactly this pair to XPASS(strict) while the
# G8 pair below keeps reproducing. The repair is semantic qualification of the lexical token, so
# an asset word in a non-market question no longer plans a quote.


def test_g4_gold_structure_is_not_a_market_lookup() -> None:
    turn = _turn("g3_g4_domain", "gold_structure")
    assert turn.market_requests == [], turn.describe()
    # Strengthened on promotion: the recorded defect was BOTH halves -- the planner building a
    # market_quote subtask AND the provider being asked. Asserting only the provider call would let
    # a planned-but-unexecuted quote pass, which is the same defect one step earlier.
    assert turn.planned_entities("market_quote") == [], turn.describe()


def test_g4_an_explicit_not_price_correction_stays_out_of_the_market_lane() -> None:
    """The worse half of the original defect: an EXPLICIT correction did not release the market
    reading. 'Not price - I mean the chemical structure of gold.' still planned market_quote/Gold
    and still asked the provider for 'gold'. It now does neither."""
    turn = _turn("g3_g4_domain", "gold_correction")
    assert turn.market_requests == [], turn.describe()
    assert turn.planned_entities("market_quote") == [], turn.describe()


def test_g4_an_ordinary_word_is_not_dragged_into_the_market_lane() -> None:
    """The boundary that still holds, so the market lane has a live guard rather than none."""
    turn = _turn("g3_g4_domain", "water")
    assert turn.market_requests == [], turn.describe()
    assert turn.planned_entities("market_quote") == [], turn.describe()


# ------------------------------------------------------------------------------- G5
# The headline completeness failure: one clause succeeded, the other vanished.


def test_g5_arithmetic_is_not_swallowed_by_a_successful_weather_lookup() -> None:
    """REPAIRED (2026-09-18, the conductor fan-out rescue law in `reduce_bound_plan`): a
    succeeded sibling node no longer rescues its fan-out siblings' non-execution, so the
    arithmetic clause is accounted and composed beside the weather readings. Promoted from
    xfail(strict) once it XPASSed on that repair."""
    turn = _turn("g5_g8_g9_weather", "mixed")
    assert turn.has_number(3973), turn.describe()


def test_g5_the_weather_half_really_did_succeed() -> None:
    """The control that makes the xfail above mean something.

    If the weather half had ALSO failed, the missing arithmetic would prove nothing -- the turn
    would simply have collapsed. Both cities really are looked up; only the arithmetic vanishes.
    """
    turn = _turn("g5_g8_g9_weather", "mixed")
    asked = {w.strip().lower() for w in turn.weather_requests}
    assert {"kaunas", "tallinn"} <= asked, turn.describe()


def test_g5_every_requested_clause_is_represented() -> None:
    """REPAIRED (same fan-out rescue law as the arithmetic case above): every clause of a
    multi-clause turn is accounted for on its own realization, so one successful branch can no
    longer stand in for the turn. Promoted from xfail(strict) once it XPASSed on that repair."""
    turn = _turn("g5_g8_g9_weather", "mixed")
    missing = []
    if not turn.has_number(3973):
        missing.append("3973")
    for needed in ("kaunas", "tallinn"):
        if needed not in turn.low:
            missing.append(needed)
    assert not missing, f"requested clauses missing from the answer: {missing}{turn.describe()}"


# ------------------------------------------------------------------------------- G8
# "Check the current weather in Kaunas and summarize it." -> Kaunas AND "Summarize It".
#
# REPAIRED, and the two tests below are now ordinary passing tests.
# Attribution, established by isolation rather than assumed: at the gauntlet branch tip alone both
# reproduce (2 xfailed); merging THREADKEEPER ae52e712
# ("fix/live-data-live-regressions-20260807") and NOTHING else flips exactly this pair to
# XPASS(strict) while the G4 pair above keeps reproducing. A trailing instruction is no longer
# read as a second location, so no phantom entity is planned or sent.


def test_g8_only_the_real_city_is_sent_to_the_weather_provider() -> None:
    turn = _turn("g5_g8_g9_weather", "phantom")
    asked = [w.strip().lower() for w in turn.weather_requests]
    assert asked == ["kaunas"], f"provider asked for {asked}{turn.describe()}"


def test_g8_a_phantom_entity_never_reaches_the_user_as_a_real_place() -> None:
    turn = _turn("g5_g8_g9_weather", "phantom")
    planned = [entity.lower() for entity in turn.planned_entities("weather_lookup")]
    assert planned == ["kaunas"], f"planned weather entities {planned}{turn.describe()}"


# ------------------------------------------------------------------------------- G9
# An abstract request ("8 European capitals") must not become one fake location.


def test_g9_an_abstract_weather_request_never_becomes_one_fake_location() -> None:
    turn = _turn("g5_g8_g9_weather", "abstract")
    for asked in turn.weather_requests:
        cleaned = asked.strip().lower()
        assert len(cleaned.split()) <= 3, f"the instruction became a location: {asked!r}{turn.describe()}"
        assert "rank" not in cleaned and "compare" not in cleaned, turn.describe()


def test_g9_an_unresolved_weather_request_does_not_fabricate_readings() -> None:
    """Deferring is acceptable; inventing observations is not."""
    turn = _turn("g5_g8_g9_weather", "abstract")
    known = set(group_evidence("g5_g8_g9_weather")["known_cities"])
    for asked in turn.weather_requests:
        assert asked.strip().lower() in known or not turn.has_number(18.0), turn.describe()


# ------------------------------------------------------------------------------- G13
# What the conversation REMEMBERS the user wants, after turns whose prompt is built from evidence.


def _g13(index: int):
    return group_evidence("g13_state_hygiene")["turns"][index]


@pytest.mark.parametrize("index", (0, 1, 2))
def test_g13_the_persisted_goal_is_never_the_runtimes_own_scaffolding(index: int) -> None:
    """REPAIRED. `adapt_user_input` persists `current_user_goal`, and two call sites handed it a
    composed PROMPT rather than an utterance.

    Measured before the repair, on turn 1 of exactly this conversation::

        current_user_goal = 'yes ok my bad, do compare those models Grounding observations for this
        turn. Use them as evidence, not as a template:{"actions_taken":["initial_search",
        "stop_answer" ], "channel": "adaptive_research", ...'

    `core.bootstrap_context` renders that back into later turns as "Current user goal: ...", so the
    runtime's scaffolding was replayed to the model as a statement of what the person wants.
    """
    turn = _g13(index)
    goal = str(turn["persisted_goal"])
    leaked = [
        marker
        for marker in ("Grounding observations", "adaptive_research", "actions_taken", "Reply like a")
        if marker in goal
    ]
    assert not leaked, f"runtime scaffolding {leaked} reached current_user_goal: {goal[:300]!r}"
    assert "{" not in goal and "}" not in goal, f"a JSON payload reached current_user_goal: {goal[:300]!r}"


def test_g13_the_users_own_words_still_reach_the_goal() -> None:
    """The control that stops the assertions above being satisfied by an empty goal.

    A repair that simply stopped recording anything would pass every "no scaffolding" check while
    destroying the state a follow-up resolves against -- strictly worse than the defect.
    """
    goal = str(_g13(0)["persisted_goal"]).lower()
    assert "qwen3" in goal or "local model" in goal, _g13(0)["persisted_goal"]


# ------------------------------------------------------------------------------- G12
# An exact-count request answered with a generic repository tree.
#
# REPAIRED, and the two tests below are now ordinary passing tests. The loose "this project" scope
# arm of the folder-overview fast path used to claim a measurement ask and serve
# `build_folder_overview`'s tree. `core.folder_overview.build_exact_file_facts` now answers it by
# walking the bound workspace and reading every matching file, so the count and the
# largest-by-line-count file are computed, not rendered; the answer states its scope and
# exclusions so the number is reproducible.


def test_g12_an_exact_count_request_is_not_answered_with_a_tree() -> None:
    """REPAIRED. The exact-count ask is resolved by reading the workspace, not by a listing."""
    turn = _turn("g12_project_facts", "exact")
    # The measured reply was a repository tree: "Top level (30 folders, 48 files)" plus a folder
    # listing. An earlier form of this assertion accepted "any number greater than 10", which the
    # tree's own folder/file counts satisfied -- it agreed with the defect instead of naming it.
    tree_markers = [m for m in ("top level", "\U0001F4C1", "folders,") if m in turn.low]
    assert not tree_markers, (
        f"an exact-count request was answered with a workspace tree {tree_markers}{turn.describe()}"
    )


def test_g12_the_three_requested_facts_are_actually_present() -> None:
    """REPAIRED. The count, the largest file's exact path and its line count are all computed from
    disk and present in the answer.

    Strengthened on promotion: the words "line" and ".py" appear in any well-scoped answer, so the
    original phrase checks are satisfied by prose alone. A path-shaped .py token and a number
    directly attached to "lines" are the shape of the FACTS, not the vocabulary around them."""
    turn = _turn("g12_project_facts", "exact")
    missing = []
    if not re.search(r"[a-z0-9_./\-]+\.py\b", turn.low):
        missing.append("a .py path")
    if not re.search(r"\d[\d,]*\s+lines?\b", turn.low):
        missing.append("a line count")
    if not any(word in turn.low for word in ("python file", "python files", ".py files")):
        missing.append("a python-file count")
    assert not missing, f"requested facts absent from the answer: {missing}{turn.describe()}"
