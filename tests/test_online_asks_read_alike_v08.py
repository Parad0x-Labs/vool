"""Web asks read alike everywhere: public facts that change, and fresh information (v0.8, 2026-11-12).

Red team round 3 (R3-08), desktop, Web lookups off, on the v0.8 line: "is the warsaw zoo open on
mondays in winter?" was answered from memory by the clean loop's brain, "whats the opening hours of the
natural history museum in vienna on sundays" from memory, "whats the cheapest flight from krakow to
rome in january" was asked back "Are you referring to Kraków in Poland or Kraków in another country?",
and "how many people live in tallinn right now" said "That is not mentioned in the records I have".
Skills audit, Web lookups on: "latest OpenClaw release notes" seated no web tool. One reading now
serves the clean loop, the tool-demand authority and the web-off note. The measured wordings come
first, then new ones; the person's own things and questions only their records answer stay where they
were. Contributor: sls_0x.
"""
from __future__ import annotations

import pytest

from apps.vool_agent import VoolAgent
from core import runtime_paths
from core.clean_loop import route
from core.online_asks import asks_for_online_facts
from core.tool_demand_signals import resolve_demand_signals

MEASURED = (
    "is the warsaw zoo open on mondays in winter?",
    "whats the opening hours of the natural history museum in vienna on sundays",
    "whats the cheapest flight from krakow to rome in january",
    "how many people live in tallinn right now",
    "latest OpenClaw release notes",
    # Red team round 3 wordings the line's clean-loop fix (28fc78dd) kept out of the files.
    "how much is a ticket for the louvre these days? going in december",
    "how much r tickets for the champions league final this year",
)
NEW_WORDINGS = (
    "what time does the louvre close today?",
    "is the british museum open on mondays this month",
    "when does the rijksmuseum open on saturdays",
    "are the vienna christmas markets open on boxing day?",
    "is the post office open tomorrow?",
    "how much is a ticket to the vatican museums?",
    "what does a day pass for the paris metro cost?",
    "how much is the train fare from vilnius to riga?",
    "how much is a pint in dublin",
    "what's the entry fee for the colosseum",
    "what's the cheapest way to get from riga to tallinn",
    "how many people are in the queue at the eiffel tower right now",
    "how long is the queue at the louvre right now",
    "what's the current population of lisbon",
    "what time does the last tram leave tonight",
    "when does the next train to vilnius leave?",
    "any news on the artemis mission?",
    "what's the latest version of python?",
    "release notes for firefox 140",
    "what's new in ios 26?",
)
NOT_ONLINE = (
    "when is my flight to Lisbon?",
    "how much did I pay for the boiler service?",
    "when does my flight land?",
    "What are the opening hours of the repair desk?",
    "What are the opening hours of the ferry ticket booth on the east quay?",
    "What were the opening hours of the swimming pool locker on the lower corridor?",
    "open the latest pdf in my downloads",
    "show me the latest screenshot",
    "what was the latest thing we talked about?",
    "summarize the latest email from anna",
    "write release notes for this project, grouped under Added, Changed and Fixed",
    "how much is 15% of 80",
    "is port 8080 open right now",
    "how many processes are running right now",
    "what's the population of tallinn",
    "what time does the meeting start",
    # An example someone might type is not the person's own ask.
    "For instance, someone could ask: is the louvre open on mondays?",
    "An example request would be: how much is a ticket to the vatican museums?",
    "Summarize the following:\nField log, week 41. The weather today was dry, the latest news from the council: "
    "the permit is still pending.",
)

ON = {route.FLAG_ENV: "1"}
OWNER = {"surface": "cli"}


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    runtime_paths.configure_runtime_home(tmp_path / "home")
    yield
    runtime_paths.configure_runtime_home(None)


def _local_brain():
    return lambda messages: '{"tool": "answer", "args": {"text": "typically open on Mondays during winter"}}'


@pytest.mark.parametrize("text", MEASURED + NEW_WORDINGS)
def test_an_online_ask_is_read_alike_by_every_door(text):
    assert asks_for_online_facts(text) is True
    assert route.is_online_ask(text) is True
    assert "web.search" in resolve_demand_signals(text).explicit_intents


@pytest.mark.parametrize("text", NOT_ONLINE)
def test_the_persons_own_things_and_records_are_not_online_asks(text):
    assert asks_for_online_facts(text) is False
    assert "web.search" not in resolve_demand_signals(text).explicit_intents


def test_an_ask_that_forbids_looking_it_up_is_not_an_online_ask():
    assert asks_for_online_facts("don't search online, just tell me: is the warsaw zoo open on mondays?") is False


@pytest.mark.parametrize("text", [*MEASURED, "what time does the louvre close today?",
                                  "how much is a ticket to the vatican museums?",
                                  "how many people are in the queue at the eiffel tower right now",
                                  "any news on the artemis mission?"])
def test_the_clean_loop_leaves_an_online_ask_to_the_normal_pipeline(text):
    decision = route.decide(text, source_context=OWNER, resolve_brain=_local_brain, env=ON)
    assert (decision.admit, decision.reason) == (False, "online_ask")
    # Not even as a short follow-up to a turn the loop just answered.
    route.note_clean_turn("r308-follow-up")
    decision = route.decide(text, source_context=OWNER, session_id="r308-follow-up", resolve_brain=_local_brain, env=ON)
    assert (decision.admit, decision.reason) == (False, "online_ask")


def _turn(text, session):
    context = {"surface": "api", "_owner_local": True, "runtime_session_id": session,
               "operating_mode": "auto", "requested_model": "unavailable:online-asks"}
    return VoolAgent(backend_name="test-backend", device="online-asks").run_once(
        text, session_id_override=session, source_context=context,
    )


@pytest.mark.parametrize("text", [*MEASURED, "what time does the louvre close today?",
                                  "how much is a ticket to the vatican museums?",
                                  "when does the next train to vilnius leave?"])
def test_with_web_lookups_off_an_online_ask_names_the_switch(text):
    result = _turn(text, "openclaw:8234567890abcdef0001")
    response = str(result.get("response") or "").lower()
    assert "web lookups are off" in response and "settings" in response, response
    assert "kraków in poland" not in response and "not mentioned in the records" not in response, response
    assert (result.get("model_execution") or {}).get("source") == "fast_path", result


@pytest.mark.parametrize("text", ["What are the opening hours of the repair desk?", "what's the population of tallinn"])
def test_a_question_that_is_not_an_online_ask_is_not_refused_as_one(text):
    result = _turn(text, "openclaw:8234567890abcdef0002")
    assert "web lookups are off, so i can't look this up" not in str(result.get("response") or "").lower(), result


@pytest.mark.parametrize("text", ["whats the cheapest flight from krakow to rome in january",
                                  "is the warsaw zoo open on mondays in winter?"])
def test_an_online_ask_is_never_adjudicated_for_entity_ambiguity(text):
    from core.entity_ambiguity import single_plain_know_question

    assert single_plain_know_question(text) is False


@pytest.mark.parametrize("text", ["what time does the louvre close today?", "what time does the last tram leave tonight"])
def test_the_clock_does_not_answer_when_something_closes_or_leaves(text):
    from core.agent_runtime.fast_paths_utility import date_time_fast_path

    assert date_time_fast_path(None, text, source_surface="api") is None
    assert "Current time in Paris" in str(date_time_fast_path(None, "what time is it in paris", source_surface="api"))
