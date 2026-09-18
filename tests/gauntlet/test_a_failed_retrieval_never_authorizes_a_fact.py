"""A failed retrieval never authorizes a live fact, a source, or an observation timestamp.

Real conversations through the real `/api/chat` seam (`core.web.api.service.dispatch_post`). Only
the model wire and the outbound network are fixtures -- routing, continuation, the planner, the
live-data runner, the receipt lanes and every honesty validator run for real. See `harness.py`.

Measured at eca76ff9, two turns, both driven here::

    U: what's the weather in Vilnius right now?
    A: Vilnius: Clear, 17 C ... Source: [wttr.in](...), observed 2026-08-07T12:00:00Z.
    U: and Porto?
       -> rebound correctly, planned weather_lookup(Porto), the fetch FAILED,
          Activity recorded `tool_failed`, the receipt said source_count=0
    A: Porto: Cloudy, high 22 C, low 17 C right now. Source: wttr.in,
       observed 2026-08-07T12:00:00Z.

The values were invented, the source was borrowed from the turn before, and so was the timestamp.

Why the assertions are shaped the way they are
----------------------------------------------
Two things are checked on every failing turn, and the first matters as much as the second:

  1. the runtime really ATTEMPTED the lookup for that entity (`weather_requests` / `market_requests`)
     and it really failed. Without this a test passes when the turn was simply routed somewhere
     else, and would keep passing if the whole live-data lane were deleted.
  2. the reply carries none of the material a failed lookup cannot license: the scripted value, the
     scripted source token, the scripted timestamp, and -- separately -- the PREVIOUS turn's real
     reading, which is the stale-substitution half of the same failure.

The fabrication is scripted onto the wire rather than hoped for from a model, because what is under
test is the runtime's authority to ship such a sentence, not any model's willingness to write one.
A model that never fabricates would make this suite vacuous; a scripted one that always does makes
the runtime's decision the only variable.
"""

from __future__ import annotations

import pytest

from tests.gauntlet import harness

pytestmark = pytest.mark.gauntlet


@pytest.fixture(autouse=True)
def _install(monkeypatch):
    harness.install(monkeypatch)


#: A reading no fixture can produce, so its appearance in a reply is proof of invention rather than
#: of a lucky match with something real. `41.7` is not a value any KNOWN_WEATHER city returns.
INVENTED_TEMP = "41.7"
INVENTED_PRICE = "8,317.45"
BORROWED_SOURCE = "wttr.in"
BORROWED_STAMP = "2026-08-07T12:00:00Z"


def weather_fabrication(city: str) -> str:
    return (
        f"{city}: Cloudy, {INVENTED_TEMP} C right now. "
        f"Source: {BORROWED_SOURCE}, observed {BORROWED_STAMP}."
    )


def market_fabrication(asset: str) -> str:
    return (
        f"{asset}: USD {INVENTED_PRICE} right now, up 2.4% today. "
        f"Source: {BORROWED_SOURCE}, retrieved {BORROWED_STAMP}."
    )


def assert_retrieval_was_attempted_and_failed(turn, entity: str, *, market: bool = False) -> None:
    """The turn really tried this lookup and really failed it -- the anti-vacuity half."""
    asked = turn.market_requests if market else turn.weather_requests
    assert entity.lower() in [str(a).strip().lower() for a in asked], (
        f"the runtime never asked the provider for {entity!r}; this turn proves nothing about what "
        f"happens after a FAILED retrieval.{turn.describe()}"
    )
    failed = [
        e for e in turn.activity
        if str(e.get("event_type", "")).lower() == "tool_failed"
    ]
    assert failed, f"expected a recorded tool failure on this turn.{turn.describe()}"


def assert_states_no_reading(turn, *, forbidden_extra: tuple[str, ...] = ()) -> None:
    """No invented value, no borrowed source, no borrowed observation stamp."""
    for token in (INVENTED_TEMP, INVENTED_PRICE, BORROWED_SOURCE, BORROWED_STAMP, *forbidden_extra):
        assert token.lower() not in turn.low, (
            f"a failed retrieval licensed {token!r}.{turn.describe()}"
        )


# ------------------------------------------------------------------------- the measured failure


def test_a_failed_weather_followup_states_no_reading():
    """The reproduction, end to end."""
    harness.SCRIPT.default = weather_fabrication("Porto")
    chat = harness.Conversation("porto")
    first = chat.say("what's the weather in Vilnius right now?")
    assert first.has_number(17.0), f"the control turn must really observe.{first.describe()}"

    second = chat.say("and Porto?")
    assert_retrieval_was_attempted_and_failed(second, "Porto")
    # "17" is the PREVIOUS turn's real reading: the stale-substitution half.
    assert_states_no_reading(second, forbidden_extra=("17 c",))


# ------------------------------------------------------- clean variants: unseen wording, unseen cities


@pytest.mark.parametrize(
    ("opening", "followup", "city"),
    [
        ("what's the weather in Vilnius right now?", "and Porto?", "Porto"),
        ("what is the weather in Riga right now?", "What about Bergen?", "Bergen"),
        ("tell me the weather in Warsaw right now", "Now Osaka?", "Osaka"),
        ("what's the current weather in Kaunas?", "and Reykjavik?", "Reykjavik"),
        ("current weather in Tallinn?", "How about Cusco?", "Cusco"),
        ("what's the temperature in Vilnius right now?", "Antananarivo?", "Antananarivo"),
    ],
)
def test_a_failed_followup_states_no_reading_whatever_the_wording(opening, followup, city):
    """Six unseen phrasings and six unseen cities -- none of them the reported one."""
    harness.SCRIPT.default = weather_fabrication(city)
    chat = harness.Conversation("clean")
    chat.say(opening)
    turn = chat.say(followup)
    assert_retrieval_was_attempted_and_failed(turn, city)
    assert_states_no_reading(turn)


# ------------------------------------------------ sloppy variants: what a real user actually types


@pytest.mark.parametrize(
    ("opening", "followup", "city"),
    [
        ("Weather in Kaunas.", "and Porto", "Porto"),
        ("weather for Vilnius now", "Osaka thx", "Osaka"),
        ("current weather in Tallinn?", "   Cusco   ", "Cusco"),
        ("Get weather for Tallinn.", "Bergen pls", "Bergen"),
        ("weather in Warsaw right now?", "and Reykjavik???", "Reykjavik"),
        ("what's the temperature in Vilnius right now?", "Antananarivo pls thx", "Antananarivo"),
    ],
)
def test_a_sloppy_followup_never_gets_a_reading_by_either_road(opening, followup, city):
    """The follow-up as a real user types it: no question mark, politeness filler, `???`, stray space.

    These turns take one of TWO roads, and the point of the test is that both end in the same place:

      * the obligation rebinds, the lookup runs and FAILS  -> an attempt, which licenses nothing;
      * the obligation does not rebind and nothing is fetched at all -> no evidence whatsoever.

    Asserting "the lookup was attempted" here would encode the first road as the correct one and
    quietly fail whenever a phrasing takes the second. Three of these six take the second road today
    for a reason outside this repair's scope, recorded rather than absorbed: `core.input_normalizer`
    rewrites `pls` to `please`, and `core.live_data_continuation.prior_live_data_request` compares
    that NORMALIZED current text against the RAW conversation history -- so the turn fails to
    recognise its own echo, treats it as an unrelated request, and hits the break-inheritance rule
    against itself. `thx` is not rewritten, which is exactly why `Osaka thx` rebinds and `Bergen pls`
    does not. That gap costs a deterministic lookup; it must never cost the truth, and what this
    test pins is that it does not.

    Vacuity is defended by the sabotage suite, not by an attempt assertion: with the evidence guard
    disabled these replies ship the fabrication verbatim, and each of these cases turns red.
    """
    harness.SCRIPT.default = weather_fabrication(city)
    chat = harness.Conversation("sloppy")
    opened = chat.say(opening)
    assert opened.weather_requests, (
        f"the opening must ground, or the follow-up has no obligation to bind to.{opened.describe()}"
    )
    turn = chat.say(followup)
    assert_states_no_reading(turn)


# ------------------------------------- a claimed source is not evidence of having reached it


def test_naming_a_source_does_not_license_a_reading_the_turn_never_took():
    """The zero-evidence half: no retrieval ran at all, and the answer names an instrument.

    Measured at eca76ff9: `Bergen: Cloudy, 41.7 C right now. Source: wttr.in, observed ...` shipped
    from a turn with no receipts of any kind, while the SAME sentence without its source line was
    convicted -- so a fabricated attribution was worth more to a fabricated reading than naming
    nobody. Driven here through a follow-up the continuation lane declines to rebind (an
    all-lowercase subject is a named limitation in `core.live_data_continuation._slot_filler_for`),
    which is exactly how a live question reaches the model with nothing fetched.
    """
    harness.SCRIPT.default = weather_fabrication("Bergen")
    chat = harness.Conversation("attributed")
    chat.say("what's the weather in Vilnius right now?")
    turn = chat.say("and bergen?")
    assert turn.weather_requests == [], (
        f"this scenario needs a turn where NOTHING was fetched.{turn.describe()}"
    )
    assert_states_no_reading(turn, forbidden_extra=("17 c",))


# ------------------------------------------------------------------- the rest of the mandated scope


def test_a_first_turn_failed_lookup_states_no_reading():
    """No prior turn to inherit from -- the failure must still not authorize a value."""
    harness.SCRIPT.default = weather_fabrication("Porto")
    turn = harness.Conversation("first").say("what's the weather in Porto right now?")
    assert_retrieval_was_attempted_and_failed(turn, "Porto")
    assert_states_no_reading(turn)


def test_a_mixed_turn_binds_each_reading_to_its_own_entity():
    """One entity resolved, one failed: the failure may not ride on its sibling's evidence."""
    harness.SCRIPT.default = "Vilnius 17 C and Porto 22 C right now. Source: wttr.in."
    turn = harness.Conversation("mixed").say("weather in Vilnius and Porto right now?")
    assert "vilnius" in turn.low and turn.has_number(17.0), (
        f"the resolved entity must still be answered.{turn.describe()}"
    )
    assert "porto" in turn.low, f"the failed entity must not vanish.{turn.describe()}"
    porto_row = [line for line in turn.reply.splitlines() if "porto" in line.lower()]
    assert porto_row, turn.describe()
    assert "unavailable" in porto_row[0].lower(), (
        f"the failed entity must be marked unavailable in its own row.{turn.describe()}"
    )
    assert "22" not in porto_row[0], (
        f"the failed entity was given a reading.{turn.describe()}"
    )


def test_a_failed_market_followup_states_no_price():
    """The same invariant on a different operation, with no weather vocabulary anywhere near it."""
    harness.SCRIPT.default = market_fabrication("Gold")
    chat = harness.Conversation("market")
    first = chat.say("what is the current bitcoin price?")
    assert first.has_number(61000.0), f"the control turn must really observe.{first.describe()}"

    turn = chat.say("and gold?")
    assert_retrieval_was_attempted_and_failed(turn, "gold", market=True)
    assert_states_no_reading(turn, forbidden_extra=("61,000",))


def test_a_fresh_request_after_a_success_does_not_inherit_the_earlier_evidence():
    """Cross-turn staleness: an independent later request whose own lookup fails."""
    harness.SCRIPT.default = weather_fabrication("Porto")
    chat = harness.Conversation("stale")
    first = chat.say("what's the weather in Warsaw right now?")
    assert first.has_number(21.0), first.describe()
    turn = chat.say("what's the weather in Porto right now?")
    assert_retrieval_was_attempted_and_failed(turn, "Porto")
    # 21 C and the fixture source both belong to the Warsaw turn and to no other.
    assert_states_no_reading(turn, forbidden_extra=("21 c", "example.invalid"))


# ----------------------------------------------------------------------------- negative controls


def test_a_successful_followup_still_reports_its_reading():
    """The repair must not cost a turn that really observed its answer."""
    chat = harness.Conversation("control-ok")
    chat.say("what's the weather in Kaunas right now?")
    turn = chat.say("and Riga?")
    assert turn.has_number(14.0), f"a real observation was withheld.{turn.describe()}"
    assert "riga" in turn.low
    assert "example.invalid" in turn.low, (
        f"a real source attribution was stripped.{turn.describe()}"
    )


def test_a_static_fact_needing_no_observation_is_untouched():
    """A measured value on a turn that required no reading must survive unchanged."""
    harness.SCRIPT.default = "Water freezes at 0 C at standard atmospheric pressure."
    turn = harness.Conversation("control-static").say("at what temperature does water freeze?")
    assert "0 c" in turn.low, f"a static fact was withdrawn.{turn.describe()}"
    assert turn.weather_requests == [] and turn.market_requests == []


def test_a_successful_multi_entity_turn_keeps_both_readings():
    harness.SCRIPT.default = "unused"
    turn = harness.Conversation("control-multi").say("weather in Vilnius and Riga right now?")
    assert turn.has_number(17.0) and turn.has_number(14.0), (
        f"a fully observed turn lost a reading.{turn.describe()}"
    )
    assert "unavailable" not in turn.low, turn.describe()


def test_an_ordinary_non_live_turn_is_not_disturbed():
    harness.SCRIPT.default = "A list comprehension builds a list in one expression."
    turn = harness.Conversation("control-plain").say("what is a python list comprehension?")
    assert "list" in turn.low and turn.reply.strip()
    assert turn.weather_requests == [] and turn.market_requests == []


# --------------------------------------------------------------------------- adversarial near-miss


def test_a_failed_lookup_does_not_mangle_an_answer_that_claims_nothing():
    """The over-fire direction: a truthful non-answer on a failed turn must be left alone.

    The guard withdraws answers that state readings the turn cannot support. An answer that states
    no reading has nothing to withdraw, and replacing it would be this repair inventing its own
    defect -- the failure mode that matters most once a guard starts firing more often.
    """
    harness.SCRIPT.default = (
        "I could not reach the weather service for that location. "
        "You could try a different spelling of the city name."
    )
    chat = harness.Conversation("nearmiss")
    chat.say("what's the weather in Vilnius right now?")
    turn = chat.say("and Porto?")
    assert_retrieval_was_attempted_and_failed(turn, "Porto")
    assert turn.reply.strip(), f"a truthful non-answer was emptied.{turn.describe()}"
    assert_states_no_reading(turn, forbidden_extra=("17 c",))
