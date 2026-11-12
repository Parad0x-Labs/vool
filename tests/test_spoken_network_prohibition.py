"""A person can forbid the network without naming a retrieval verb.

"I'm on a plane", "our wifi is down", "answer from your own knowledge", "external sources are off
limits", "skip the lookup". Measured on the v0.8 line before the spoken recognizer
(core.user_network_directive), 7 of 28 such messages froze a web prohibition, so the turn stayed
free to go online after the person had said no.

The prohibition is frozen at ingress by `prohibitions_from_text`, the reading every whole-turn
lane and every child sub-turn inherits, so the tests below ask that reading. The last two run a
real turn with web lookups switched on and count every outbound socket.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from core.retrieval_constraints import analyze_retrieval_constraints
from core.turn_prohibitions import FAMILY_WEB, prohibitions_from_text
from core.user_network_directive import network_prohibited_by_user
from tests.test_policy_conservation_p0 import _SocketSentinel, _turn

_SPOKEN_PROHIBITIONS = [
    "what's the tallest mountain, don't use the web",
    "no internet for this one please",
    "im on a plane, whats a good podcast",
    "answer from your own knowledge",
    "skip the lookup",
    "our wifi is down, quick q",
    "explain TLS handshakes without searching",
    "zero external calls on this thanks",
    "settle a bet for me: tallest waterfall in Norway. no googling",
    "quick one and please dont look it up: how many bones in a cat",
    "i havent got signal down here, roughly how long does concrete cure",
    "give me your best guess on this, external sources are off limits",
    "we are air gapped in this lab, describe the Krebs cycle",
    "our broadband died. what year did Chernobyl happen",
    "whats a decent chess opening - dont go online for it",
    "answer this one blind",
    "no crawling the web, just reason it out: why is the sky blue",
    "there is zero connectivity on this boat, help me plan meals",
    "the router is dead so nothing online: define entropy",
    "TASK: summarise attached spec\nCONSTRAINTS: offline only\nOUTPUT: bullets",
    "(no internet pls) what's the melting point of tungsten",
    "Ich bin offline gerade - was ist die Hauptstadt von Peru",
    "context: plane, wifi none. question: best way to memorise vocab?",
    "```\nmode=offline\n```\nnow explain vector clocks",
    "first, do NOT browse. second, tell me about CRDTs. third, keep it short",
    "answer using nothing but your own knowledge, and be brief -- what is a semaphore",
    "P.S. we have no network here. Main question: how do heat pumps work?",
    "im in a dead zone. whats the boiling point of ethanol",
    "never search the internet",
    "don't search the web",
    "I'm not sure, just don't search",
    "Don't search for anything, just answer",
    "don't google the answer",
    "without searching for it",
    "don't fetch anything live",
    "don't search google, just answer from memory",
    "i dont want you touching web, just explain redis",
    # Typography that real keyboards produce: a curly apostrophe and a wrapped line.
    "I’m on a plane right now. What is a Merkle tree?",
    "please answer without\nlooking it up: what is a monad",
]

_ORDINARY_TURNS = [
    "search the web for the newest kernel release",
    "look up the weather in Riga",
    "no rush, but can you google who won yesterday",
    "why is my build not working",
    "find me the current price of copper online",
    "can you pull up the latest numpy release notes",
    "i cant remember the release date, could you check for me",
    "search for restaurants near the station",
    "my tests are not passing, here is the traceback",
    "do not round the numbers in the table you generate",
    "never use recursion in the solution please",
    "is there any way to avoid a full table scan here",
    "look this up and tell me who currently holds the record",
    "the deploy is not working, check the status page",
    "no, that's wrong -- try the other approach",
    "without loops, write a sum function",
    "skip the boilerplate and show me the core function",
    # Doubt negates the speaker's certainty, not the network.
    "I'm not sure whether to search, but actually look up the live weather in Porto now.",
    "I don't know how to google this, can you find it",
    # An act given an object of its own excludes that object only.
    "Look up the current weather in Porto, and do not fetch server status metrics.",
    "don't download the attachment, just summarise what I pasted",
    "don't search my files, just answer",
    "don't look up flights, I only want hotels",
    # The "no" belongs to "rush"; the request to search stands.
    "no rush, search the web for the latest kernel",
    # A narrower kind of retrieval, asked for in the same breath.
    "don't search google, use the web",
    # A question about the network is not a statement that it is off.
    "is the internet down for everyone or just me?",
]


@pytest.mark.parametrize("text", _SPOKEN_PROHIBITIONS)
def test_a_spoken_prohibition_freezes_the_web(text):
    frozen = prohibitions_from_text(text)
    assert frozen.prohibits_family(FAMILY_WEB), (text, frozen)
    assert "explicit_retrieval_prohibition" in frozen.reason_codes or (
        "explicit_tool_prohibition" in frozen.reason_codes
    ), frozen.reason_codes


@pytest.mark.parametrize("text", _ORDINARY_TURNS)
def test_an_ordinary_turn_keeps_the_web_open(text):
    frozen = prohibitions_from_text(text)
    assert not frozen.prohibits_family(FAMILY_WEB), (text, frozen)


@pytest.mark.parametrize("text", [
    'My friend texted "I\'m on a plane, answer from memory". What does she mean by that?',
    "The sign said “wifi is down”. Find me a cafe near the station with working wifi.",
])
def test_quoted_words_are_not_an_instruction_to_this_turn(text):
    assert not prohibitions_from_text(text).prohibits_family(FAMILY_WEB), text


def test_a_reported_example_is_not_a_prohibition():
    text = (
        "For example, a user might type im offline so how do I fix my router. "
        "Look up what that error usually means."
    )
    assert network_prohibited_by_user(text)  # the words alone do say it ...
    assert not prohibitions_from_text(text).prohibits_family(FAMILY_WEB)  # ... someone else did


def test_a_narrow_reading_stays_narrow():
    """A prohibition the directive recognizer reads at toolset grain keeps that grain: the spoken
    reading supplies a family only where the directive named none."""
    frozen = prohibitions_from_text("Don't look up market prices. Tell me the current weather in Kaunas.")
    assert frozen.restricts_toolset("market_prices")
    assert not frozen.prohibits_family(FAMILY_WEB)
    assert not frozen.restricts_toolset("weather")


def test_the_retrievable_text_keeps_the_question_and_drops_the_prohibition():
    constraints = analyze_retrieval_constraints("I'm on a plane. What's the tallest mountain in Peru?")
    assert constraints.forbids_external_retrieval
    assert "tallest mountain in Peru" in constraints.eligible_text
    assert "plane" not in constraints.eligible_text


@pytest.mark.parametrize("script", [
    # Every clause holds a spoken prohibition and a format directive. Testing each prohibition
    # against every format span was quadratic: 20000 pairs took 13 s.
    "from core.turn_prohibitions import prohibitions_from_text; "
    "prohibitions_from_text('offline. no markdown. ' * 40000)",
    "from core.turn_prohibitions import prohibitions_from_text; "
    "prohibitions_from_text('offline. for example, you said hi. ' * 25000)",
    "from core.user_network_directive import prohibition_spans; "
    "prohibition_spans('not sure web ' * 60000 + 'no ' * 60000 + 'I\\'m on a plane ' * 30000)",
])
def test_a_long_message_is_read_in_linear_time(script):
    subprocess.run([sys.executable, "-c", script], check=True, timeout=10,
                   env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})


# ---------------------------------------------------------------- the real turn


@pytest.fixture
def workspace(tmp_path):
    return str(tmp_path)


@pytest.fixture
def harness(request):
    from tests.test_turn_attempt_chain import _Harness

    h = _Harness(f"sess-spoken-{request.node.name[:40]}")
    try:
        yield h
    finally:
        h.close()


@pytest.mark.usefixtures("enable_web")
def test_a_turn_told_im_on_a_plane_opens_no_socket(harness, workspace):
    """Web lookups are on and the question wants live data. The person said they are on a
    plane, so the turn must not open a single outbound connection or DNS lookup."""
    with _SocketSentinel() as sentinel:
        _turn(harness, "I'm on a plane. Tell me the current weather in Kaunas.", workspace)
    assert sentinel.attempts == [], sentinel.attempts[:6]


@pytest.mark.usefixtures("enable_web")
def test_the_same_turn_without_the_words_does_reach_for_the_network(harness, workspace):
    """CONTROL: the turn above would have gone online. Without this, a quiet turn would prove
    nothing about the prohibition."""
    with _SocketSentinel() as sentinel:
        _turn(harness, "Tell me the current weather in Kaunas.", workspace)
    assert sentinel.attempts, "the control turn never tried the network, so it proves nothing"
