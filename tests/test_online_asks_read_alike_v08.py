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
    "what's the latest on the rail strike in france?",
    "look up when the ikea in krakow opens tomorrow",
    "find the cheapest flights to oslo next week",
    "remind me what time the louvre closes today",
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
    # "Latest" of something nobody publishes (measured 2026-11-12: the stack question seated a web
    # search beside a reminder and the reminder turn failed).
    "Remind me to review the orchard inventory in 60 seconds. Separately, explain why a stack removes its "
    "newest item first.",
    "what's the newest item in the queue?",
    "show me the latest test results",
    'Traceback (most recent call last):\n  File "app.py", line 3, in <module>',
    # A reminder the person sets, and a file with prices in its name.
    "remind me to call the bank when it opens tomorrow",
    "remind me tomorrow at 9 to check if the post office is open",
    "open the ticket prices spreadsheet",
    "pull up the flight prices csv",
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


# The local file lane read "open" as "open a file" and the words after it as a file's name: "is the
# post office open tomorrow?" was answered with a disk search for a file named "tomorrow" (measured
# 2026-11-12 on the newest candidate, 28fc78dd, Web lookups off; on a Mac the reply is "I couldn't find
# a file matching 'tomorrow'"), and "find the cheapest flights to oslo next week" with a search for a
# file named "cheapest flights".
OPEN_SAID_OF_A_PLACE = (
    "is the post office open tomorrow?",
    "is the bank open tomorrow?",
    "is the library open tomorrow morning?",
    "when does the bank open tomorrow?",
    "will the museum open tomorrow after the strike?",
    "are the shops open today in krakow?",
    "what's open late tonight near the main station in krakow?",
    "is the ikea in krakow open now?",
    "does the pool open tomorrow at 7?",
    "can you check if the post office is open tomorrow",
)
FOUND_ON_THE_WEB = (
    "find the cheapest flights to oslo next week",
    "pull up the current price of a eurostar ticket to paris",
)
FILES_TO_OPEN = (
    ("open the plumber invoice", ("", ("plumber", "invoice"), "")),
    ("can you open the bank csv", ("csv", ("bank",), "")),
    ("could you please open my tax return pdf", ("pdf", ("tax", "return"), "")),
    ("is there a way to open the plumber invoice?", ("", ("plumber", "invoice"), "")),
    ("let's open the plumber invoice", ("", ("plumber", "invoice"), "")),
    ("would you open the school play video", ("video", ("school", "play"), "")),
    ("open the open day flyer", ("", ("open", "day", "flyer"), "")),
    ("pull up the flight prices csv", ("csv", ("flight", "prices"), "")),
    ("open the ticket prices spreadsheet", ("", ("ticket", "prices", "spreadsheet"), "")),
    ("show me the late fee invoice", ("", ("late", "fee", "invoice"), "")),
)


@pytest.mark.parametrize("text", [*OPEN_SAID_OF_A_PLACE, *FOUND_ON_THE_WEB, "add a reminder: pharmacy is open tomorrow until 8"])
def test_open_said_of_a_place_or_a_fact_on_the_web_is_not_a_file_to_open(text):
    from core.execution.constants import machine_present_file_intent

    assert machine_present_file_intent(text) is None


@pytest.mark.parametrize(("text", "expected"), FILES_TO_OPEN)
def test_a_file_the_person_asks_to_open_is_still_looked_up_by_its_name(text, expected):
    from core.execution.constants import machine_present_file_intent

    ask = machine_present_file_intent(text)
    assert ask is not None
    assert (str(ask.kind), tuple(ask.words), str(ask.place)) == expected
    assert asks_for_online_facts(text) is False


@pytest.mark.parametrize("text", [*OPEN_SAID_OF_A_PLACE[:5], *FOUND_ON_THE_WEB])
def test_with_web_lookups_off_open_said_of_a_place_names_the_switch_not_a_file(text):
    result = _turn(text, "openclaw:8234567890abcdef0003")
    response = str(result.get("response") or "").lower()
    assert "web lookups are off" in response and "settings" in response, response
    assert "local folders" not in response and "find a file" not in response, response
    assert (result.get("model_execution") or {}).get("source") == "fast_path", result


@pytest.mark.parametrize("text", ["remind me to call the bank when it opens tomorrow",
                                  "remind me tomorrow at 9 to check if the post office is open"])
def test_a_reminder_about_a_place_is_not_refused_as_an_online_ask(text):
    result = _turn(text, "openclaw:8234567890abcdef0004")
    assert "web lookups are off, so i can't look this up" not in str(result.get("response") or "").lower(), result


# The other side of the same reading: the requirement authority reads "right now" or "release notes"
# in any sentence, so "is port 8080 open right now" (which reached the model once the file lane let go
# of it) and "write release notes for this project" came back "from what I already know and may be out
# of date" with Web lookups off. No web page answers a question about this computer or this project.
@pytest.mark.parametrize("text", ["is port 8080 open right now",
                                  "how many processes are running right now",
                                  "write release notes for this project, grouped under Added, Changed and Fixed"])
def test_a_question_about_this_computer_or_project_gets_no_web_off_line(text):
    from core.online_asks import asks_about_this_computer_or_project

    assert asks_about_this_computer_or_project(text) is True
    result = _turn(text, "openclaw:8234567890abcdef0005")
    assert "web lookups are off" not in str(result.get("response") or "").lower(), result


@pytest.mark.parametrize("text", ["how long is the drive from krakow to warsaw right now",
                                  "who won the match last night? put it in my notes",
                                  "what time does the louvre close today?",
                                  "is the post office open tomorrow?"])
def test_a_question_about_the_world_is_not_about_this_computer(text):
    from core.online_asks import asks_about_this_computer_or_project

    assert asks_about_this_computer_or_project(text) is False
