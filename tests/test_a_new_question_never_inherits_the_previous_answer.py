"""A new question never inherits the previous live-data answer.

Measured 2026-09-06 in the packaged app's real native window (request
``req:http:67536be2ab574acfb57e2de27fcc7a64``): after "what is gold price now?" the operator typed

    give me comparision review Vw passat vs vw golf, like how long they been maming it,
    total sale, most popuplar regions where sold, engines and so on

and the stored answer was the gold quote again. The current text reached the backend intact; the
first wrong boundary was `core.live_data_continuation._clarification_recovers_the_obligation`:
"they" satisfied the set-grammar gate, "golf" is one edit from the recorded slot "Gold", and the
only new-subject check looked at CAPITALISED words -- so a lower-case car request was rewritten
into the previous gold request and a gold plan was built and answered.

The repair is a bound on the clarification path, not a car or gold word: every content word of a
clarification must be accounted for by a grounded slot (verbatim or typo), scaffolding, set
grammar or dialogue-meta vocabulary. Twelve words of the turn above are none of those, and the
turn stays a statement. The controls below pin what must keep working around that bound.
"""
from __future__ import annotations

import json
import sqlite3
import uuid

import pytest

from core.execution_requirements import requirements_for
from core.live_data_continuation import (
    _content_the_obligation_does_not_hold,
    continuation_inherits_live_data,
)
from core.live_data_plan import build_live_data_plan
from core.runtime_continuity import remember_live_data_obligation

GOLD_QUESTION = "what is gold price now?"
GOLD_ANSWER = (
    "Gold: USD 4,476.60 per troy ounce (24h change: -1.39%). "
    "Source: [Yahoo Finance](https://finance.yahoo.com/quote/GC=F), retrieved 2026-09-04 20:59 UTC."
)
VW_QUESTION = (
    "give me comparision review Vw passat vs vw golf, like how long they been maming it, "
    "total sale, most popuplar regions where sold, engines and so on"
)


def _sid(label: str) -> str:
    return f"openclaw:{label}:{uuid.uuid4().hex}"


def _context(session_id: str, *messages: tuple[str, str]) -> dict:
    return {
        "session_id": session_id,
        "runtime_session_id": session_id,
        "conversation_history": [{"role": role, "content": text} for role, text in messages],
    }


def _gold_thread(label: str) -> str:
    session_id = _sid(label)
    remember_live_data_obligation(
        session_id, operation="market_quote", slots=["Gold"],
        request_text=GOLD_QUESTION, absorbed_text=GOLD_QUESTION,
    )
    return session_id


def _plan_operations(text: str, context: dict) -> list[tuple[str, str]]:
    plan = build_live_data_plan(text, plan_id="p", attempt_id="a", source_context=context)
    if plan is None:
        return []
    return [(task.to_dict().get("operation"), task.to_dict().get("entity")) for task in plan.subtasks]


# ------------------------------------------------------------------------------------ THE DEFECT


def test_a_request_about_new_subjects_never_rebinds_the_previous_market_obligation():
    session_id = _gold_thread("vw")
    context = _context(session_id, ("user", GOLD_QUESTION), ("assistant", GOLD_ANSWER), ("user", VW_QUESTION))

    assert continuation_inherits_live_data(VW_QUESTION, source_context=context) == ""
    assert _plan_operations(VW_QUESTION, context) == [], "a live-data plan was built for a car question"
    assert requirements_for(VW_QUESTION, source_context=context).answer_mode != "LIVE_DATA"
    unexplained = _content_the_obligation_does_not_hold(VW_QUESTION, slots=["Gold"])
    assert {"passat", "engines", "regions", "review"} <= set(unexplained), unexplained


def test_the_same_bound_holds_for_a_weather_obligation():
    """Domain-distance control: a mistyped city inside a lower-case, unrelated request."""
    session_id = _sid("weather")
    remember_live_data_obligation(
        session_id, operation="weather_lookup", slots=["riga"],
        request_text="What is the weather in Riga?", absorbed_text="What is the weather in Riga?",
    )
    request = "give me comparision review rigs vs tallinn trams, how long they been running it, total riders, engines and so on"
    context = _context(session_id, ("user", "What is the weather in Riga?"),
                       ("assistant", "Riga: 14 C. Source: [x](https://example.invalid)"), ("user", request))

    assert continuation_inherits_live_data(request, source_context=context) == ""
    assert _plan_operations(request, context) == []


def test_a_previous_turn_recorded_late_cannot_make_the_new_question_inherit():
    """Delayed completion of the previous turn changes nothing: the bound is on the TEXT."""
    session_id = _sid("late")
    context = _context(session_id, ("user", GOLD_QUESTION), ("assistant", GOLD_ANSWER), ("user", VW_QUESTION))
    assert continuation_inherits_live_data(VW_QUESTION, source_context=context) == ""
    remember_live_data_obligation(
        session_id, operation="market_quote", slots=["Gold"],
        request_text=GOLD_QUESTION, absorbed_text=GOLD_QUESTION,
    )
    assert continuation_inherits_live_data(VW_QUESTION, source_context=context) == ""
    # ...while a genuine bare re-ask recorded late still finds its obligation.
    nudge = _context(session_id, ("user", GOLD_QUESTION), ("assistant", GOLD_ANSWER), ("user", "and now?"))
    assert continuation_inherits_live_data("and now?", source_context=nudge) == GOLD_QUESTION


def test_a_fresh_session_with_the_same_words_inherits_nothing():
    session_id = _sid("fresh")
    context = _context(session_id, ("user", VW_QUESTION))
    assert continuation_inherits_live_data(VW_QUESTION, source_context=context) == ""
    assert _plan_operations(VW_QUESTION, context) == []


# ------------------------------------------------------------------------------------- CONTROLS


@pytest.mark.parametrize("nudge", ["and now?", "what about now?", "and?"])
def test_a_genuine_follow_up_still_re_asks_the_live_question(nudge: str):
    session_id = _gold_thread("nudge")
    context = _context(session_id, ("user", GOLD_QUESTION), ("assistant", GOLD_ANSWER), ("user", nudge))
    assert continuation_inherits_live_data(nudge, source_context=context) == GOLD_QUESTION
    assert _plan_operations(nudge, context) == [("market_quote", "Gold")]


@pytest.mark.parametrize("rebind", ["what about silver?", "and silver?"])
def test_a_follow_up_naming_a_new_asset_rebinds_to_that_asset(rebind: str):
    session_id = _gold_thread("rebind")
    context = _context(session_id, ("user", GOLD_QUESTION), ("assistant", GOLD_ANSWER), ("user", rebind))
    inherited = continuation_inherits_live_data(rebind, source_context=context)
    assert "silver" in inherited.lower() and "gold" not in inherited.lower(), inherited
    assert _plan_operations(rebind, context) == [("market_quote", "Silver")]


def test_an_explicit_comparison_of_old_and_new_subjects_is_its_own_request():
    session_id = _gold_thread("compare")
    request = "compare gold vs silver price"
    context = _context(session_id, ("user", GOLD_QUESTION), ("assistant", GOLD_ANSWER), ("user", request))
    assert continuation_inherits_live_data(request, source_context=context) == ""
    assert _plan_operations(request, context) == [("market_quote", "Gold"), ("market_quote", "Silver")]


def test_an_unrelated_turn_ends_the_thread_for_the_nudge_after_it():
    session_id = _gold_thread("ended")
    context = _context(
        session_id, ("user", GOLD_QUESTION), ("assistant", GOLD_ANSWER),
        ("user", VW_QUESTION), ("assistant", "a car answer"), ("user", "and now?"),
    )
    assert continuation_inherits_live_data("and now?", source_context=context) == ""


WEATHER_QUESTION = "What is the weather in Riga and Vilnius?"
WEATHER_ANSWER = "Riga: 14 C. Vilnius: 12 C. Source: [Open-Meteo](https://example.invalid/w)"


def _two_city_thread(label: str) -> str:
    session_id = _sid(label)
    remember_live_data_obligation(
        session_id, operation="weather_lookup", slots=["Riga", "Vilnius"],
        request_text=WEATHER_QUESTION, absorbed_text=WEATHER_QUESTION,
    )
    return session_id


@pytest.mark.parametrize(
    "question",
    ["are they good cars?", "do they sell tires?", "which one should i buy?", "are they open today?",
     "how long have they existed"],
)
def test_a_short_unrelated_question_with_set_grammar_does_not_re_ask_the_set(question: str):
    """The sibling boundary: the short-turn aggregate branch accepted ANY residue once "they" or
    "which one" was present. Measured after a two-city weather thread, each of these re-asked the
    weather for both cities."""
    session_id = _two_city_thread("short-unrelated")
    context = _context(session_id, ("user", WEATHER_QUESTION), ("assistant", WEATHER_ANSWER), ("user", question))
    assert continuation_inherits_live_data(question, source_context=context) == "", question


@pytest.mark.parametrize(
    "aggregate",
    ["which one is warmer?", "compare them", "both please", "which of those is up more", "which one is warmest?",
     "which is cheaper today?"],
)
def test_a_genuine_aggregate_still_re_asks_the_whole_set(aggregate: str):
    session_id = _two_city_thread("aggregate")
    context = _context(session_id, ("user", WEATHER_QUESTION), ("assistant", WEATHER_ANSWER), ("user", aggregate))
    inherited = continuation_inherits_live_data(aggregate, source_context=context)
    assert "riga" in inherited.lower() and "vilnius" in inherited.lower(), (aggregate, inherited)


def test_the_gauntlets_typo_clarification_still_recovers_both_cities():
    """The bound must not undo the repair it sits beside (gauntlet G1, fourth turn)."""
    session_id = _sid("g1")
    remember_live_data_obligation(
        session_id, operation="weather_lookup", slots=["kaunas", "tallinn"],
        request_text="Get weather for Kaunas and Tallinn.", absorbed_text="Get weather for Kaunas.",
    )
    clarification = "I asked about Kauans and talling weather, last question was follow up for them two right?"
    context = _context(session_id, ("user", "Get weather for Kaunas."), ("user", "What about Tallinn?"),
                       ("user", "Which one is warmer?"), ("user", clarification))
    inherited = continuation_inherits_live_data(clarification, source_context=context)
    assert "kaunas" in inherited.lower() and "tallinn" in inherited.lower(), inherited


# ------------------------------------------------------------------------------- SERVED, REAL DOOR


FIXTURE_REPLY = "FIXTURE-REPLY: the scripted provider answered the current question."


def _events_for_turn(home, session_id: str, turn_id: str) -> list[str]:
    with sqlite3.connect(home / "data" / "vool_web0_v2.db") as conn:
        rows = conn.execute(
            "SELECT event_type, details_json FROM runtime_session_events WHERE session_id = ? ORDER BY seq",
            (session_id,),
        ).fetchall()
    return [event_type for event_type, details in rows if turn_id in str(details or "")]


def test_served_gold_then_car_comparison_answers_the_car_question(tmp_path):
    """Real daemon, real /api/chat, real market lane for the gold turn, distinguishable fixture
    for the model lane. Skips -- never passes -- when the gold quote cannot be fetched here."""
    from tests import _reader_served_rig as rig

    with rig.CapturingProvider(default=FIXTURE_REPLY) as provider:
        daemon = rig.ServedDaemon(tmp_path / "home", provider=provider,
                                  env_extra={"VOOL_INSTALL_PROFILE": "hybrid-fallback"})
        try:
            daemon.start(timeout=120)
            certification = daemon.certify(timeout=120)
            assert certification.get("state") == "verified", certification
            session_a = rig.canonical_session("topic-change-a")
            gold = json.dumps(daemon.chat(GOLD_QUESTION, session_id=session_a, turn_id="gold-turn"))
            if "troy ounce" not in gold and "Gold" not in gold:
                pytest.skip(f"the live gold quote could not be fetched here; nothing to inherit: {gold[:300]}")
            provider.reset()
            car = json.dumps(daemon.chat(VW_QUESTION, session_id=session_a, turn_id="car-turn"))
            assert "troy ounce" not in car and "Gold:" not in car, car[:600]
            assert any("passat" in payload.lower() for payload in provider.payloads()), \
                "the model was never asked the current question"
            assert "live_data_plan_created" not in _events_for_turn(daemon.home, session_a, "car-turn")
            provider.reset()
            after = json.dumps(daemon.chat("and now?", session_id=session_a, turn_id="nudge-turn"))
            assert "troy ounce" not in after and "Gold:" not in after, after[:600]
            session_b = rig.canonical_session("topic-change-b")
            provider.reset()
            fresh = json.dumps(daemon.chat(VW_QUESTION, session_id=session_b, turn_id="fresh-turn"))
            assert "troy ounce" not in fresh and "Gold:" not in fresh, fresh[:600]
            assert any("passat" in payload.lower() for payload in provider.payloads())
            print("SERVED_CAR_ANSWER", car[:700], flush=True)
        finally:
            daemon.stop()
