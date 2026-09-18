"""A follow-up resolves against the OBLIGATION the conversation opened, not the prior message text.

What this file pins
-------------------
`core.live_data_continuation` used to inherit only when the utterance was empty once continuation
scaffolding was stripped. That was not conservatism, it was the shape of the state: the only thing
available to inherit was a STRING (`prior_live_data_request`), and a string has no slot to put a new
subject into. Three release defects lived in that gap, all recorded as strict xfails in
`tests/gauntlet/test_release_conversation_gauntlet.py` (G1), two of them now promoted.

The state is now a typed obligation -- operation, the entities that filled its slot, the user's own
phrasing, and the raw turns it absorbed -- so exactly three things are expressible, and each is a
rebinding of the SAME obligation rather than a fresh guess:

    RE-ASK     residue empty            -> the obligation again
    REBIND     residue fills the slot   -> that obligation, new subject
    AGGREGATE  residue names the set    -> that obligation, every accumulated subject

Deliberately fresh vocabulary
-----------------------------
Nothing below uses Kaunas, Tallinn or "which one is warmer" -- those are the reported reproduction
and they are covered where they belong, in the gauntlet, through the real `/api/chat` seam. If the
repair only worked on the exam it was written against, every case here would fail. The market cases
are the domain-distance check: a different recognizer, a different entity type, and a closed alias
table instead of an open world, exercised through the identical code path with no market-specific
branch behind it.

Nothing here is mocked. Real obligations go through the real store, and every placehood or asset
question is settled by the same recognizers the plan builder uses.
"""
from __future__ import annotations

import uuid

import pytest

from core.live_data_continuation import (
    continuation_inherits_live_data,
    utterance_carries_no_independent_request,
)
from core.runtime_continuity import remember_live_data_obligation


def _sid(label: str) -> str:
    return f"openclaw:{label}:{uuid.uuid4().hex}"


def _conversation(session_id: str, *messages: tuple[str, str]) -> dict:
    """A source_context shaped exactly like the one the turn path passes."""
    return {
        "session_id": session_id,
        "runtime_session_id": session_id,
        "conversation_history": [{"role": role, "content": text} for role, text in messages],
    }


def _weather_thread(label: str, *, city: str, request: str, follow_up: str) -> dict:
    """One grounded weather turn, then `follow_up` arriving as the newest message."""
    session_id = _sid(label)
    remember_live_data_obligation(
        session_id,
        operation="weather_lookup",
        slots=[city],
        request_text=request,
        absorbed_text=request,
    )
    return _conversation(
        session_id,
        ("user", request),
        ("assistant", f"{city}: Clear, 11 C. Source: [example](https://example.invalid/weather)"),
        ("user", follow_up),
    )


def _market_thread(label: str, *, asset: str, request: str, follow_up: str) -> dict:
    session_id = _sid(label)
    remember_live_data_obligation(
        session_id,
        operation="market_quote",
        slots=[asset],
        request_text=request,
        absorbed_text=request,
    )
    return _conversation(
        session_id,
        ("user", request),
        ("assistant", f"{asset.title()}: $1.00. Source: [example](https://example.invalid/market)"),
        ("user", follow_up),
    )


# ---------------------------------------------------------------------------------------- REBIND
# The turn names a subject for an obligation that is already open. Five clean shapes, five cities,
# no shared sentence skeleton -- the opening, the punctuation and the word order all differ.


@pytest.mark.parametrize(
    "request_text, city, follow_up, expected_city",
    [
        ("What is the current weather in Porto?", "Porto", "What about Bergen?", "Bergen"),
        ("current weather in Osaka right now", "Osaka", "How about Sapporo?", "Sapporo"),
        ("Get me the weather for Lyon.", "Lyon", "and Nantes?", "Nantes"),
        ("weather in Cork please", "Cork", "Galway?", "Galway"),
        ("what's the temperature in Split", "Split", "ok now Rijeka", "Rijeka"),
    ],
)
def test_a_follow_up_naming_a_new_subject_rebinds_the_obligation(
    request_text, city, follow_up, expected_city
):
    context = _weather_thread("rebind", city=city, request=request_text, follow_up=follow_up)
    inherited = continuation_inherits_live_data(follow_up, source_context=context)

    assert inherited, f"{follow_up!r} inherited nothing from an open {city} obligation"
    assert expected_city.lower() in inherited.lower(), inherited
    # The OLD subject must be gone. Inheriting "Porto and Bergen" would answer a question nobody
    # asked and re-spend a lookup, which is a different defect wearing this repair's clothes.
    assert city.lower() not in inherited.lower(), (
        f"the previous subject survived the rebind: {inherited!r}"
    )


def test_a_rebind_keeps_the_users_own_phrasing():
    """The inherited request is the user's sentence with one word changed, not a synthesized one.

    This is what lets the rebind face every recognizer the user's own typing would have faced, and
    it is why no request-phrasing template exists in the module.
    """
    context = _weather_thread(
        "phrasing", city="Porto", request="What is the current weather in Porto?",
        follow_up="What about Bergen?",
    )
    assert (
        continuation_inherits_live_data("What about Bergen?", source_context=context)
        == "What is the current weather in Bergen?"
    )


# ------------------------------------------------------------------------------- SLOPPY VARIANTS
# The same intent as a real person types it: filler, missing punctuation, lowercase openings.


@pytest.mark.parametrize(
    "follow_up, expected_city",
    [
        ("what about Bergen pls", "Bergen"),
        ("how about Bergen thx", "Bergen"),
        ("and Bergen", "Bergen"),
        ("ok what about Bergen mate", "Bergen"),
        ("Bergen?", "Bergen"),
    ],
)
def test_sloppy_follow_ups_still_rebind(follow_up, expected_city):
    context = _weather_thread(
        "sloppy", city="Porto", request="What is the current weather in Porto?", follow_up=follow_up
    )
    inherited = continuation_inherits_live_data(follow_up, source_context=context)
    assert expected_city.lower() in inherited.lower(), f"{follow_up!r} -> {inherited!r}"


# -------------------------------------------------------------------------------- CORRECTIONS
# Found by driving an ordinary conversation, not by unit test. A correction is the commonest
# follow-up shape there is, and every one of these produced NOTHING before the fix -- worse, the
# failed correction then read as an unrelated request, so it also killed every turn after it.


@pytest.mark.parametrize(
    "follow_up, expected_city",
    [
        ("no i meant Bergen", "Bergen"),
        ("actually Bergen", "Bergen"),
        ("sorry, Bergen", "Bergen"),
        ("i meant Bergen", "Bergen"),
    ],
)
def test_a_correction_rebinds_to_the_subject_it_corrects_toward(follow_up, expected_city):
    context = _weather_thread(
        "correction", city="Porto", request="What is the current weather in Porto?",
        follow_up=follow_up,
    )
    inherited = continuation_inherits_live_data(follow_up, source_context=context)
    assert expected_city.lower() in inherited.lower(), f"{follow_up!r} -> {inherited!r}"
    assert "porto" not in inherited.lower(), inherited


def test_a_correction_carrying_a_negated_reference_is_not_covered():
    """NAMED LIMITATION, recorded rather than left to be discovered.

    "no not that one, Bergen" carries a negated reference ("not that one") AND a new subject. The
    correction opening strips to "that one, Bergen", whose residue is not a bare proper noun, so no
    rebind happens and the turn reaches the model. That is the safe direction and it is where this
    repair stops: `core.followup_subject_continuity` already treats a negated reference as
    redirecting AWAY from the prior subject, and teaching two modules to disagree about that is a
    worse outcome than one uncovered phrasing. Asserted so the boundary is a decision, not a gap.
    """
    context = _weather_thread(
        "negated", city="Porto", request="What is the current weather in Porto?",
        follow_up="no not that one, Bergen",
    )
    assert continuation_inherits_live_data("no not that one, Bergen", source_context=context) == ""


def test_a_rebind_clears_every_earlier_subject_not_just_the_last():
    """After an AGGREGATE, the obligation's request text names several subjects. Replacing only the
    most recent one left the earlier ones in place and the next rebind inherited them: "actually
    Tallinn" asked for three cities. Three lookups and a comparison table is not a smaller mistake
    than none -- it spends real requests and answers a question nobody put."""
    session_id = _sid("clear-all")
    for city in ("Porto", "Bergen", "Cork"):
        remember_live_data_obligation(
            session_id, operation="weather_lookup", slots=[city],
            request_text=f"What is the current weather in {city}?",
            absorbed_text=f"what about {city}?",
        )
    # the aggregate turn writes its own multi-subject sentence back as the request text
    remember_live_data_obligation(
        session_id, operation="weather_lookup", slots=["Porto", "Bergen", "Cork"],
        request_text="What is the current weather in Porto and Bergen and Cork?",
        absorbed_text="which one is warmest?",
    )
    context = _conversation(
        session_id,
        ("user", "What is the current weather in Porto?"),
        ("assistant", "Porto: Clear, 19 C. Source: [example](https://example.invalid/weather)"),
        ("user", "which one is warmest?"),
        ("assistant", "Porto is warmest. Source: [example](https://example.invalid/weather)"),
        ("user", "actually Galway"),
    )
    inherited = continuation_inherits_live_data("actually Galway", source_context=context)
    assert "galway" in inherited.lower(), inherited
    for stale in ("porto", "bergen", "cork"):
        assert stale not in inherited.lower(), f"{stale} survived the rebind: {inherited!r}"


def test_a_bare_nudge_re_asks_the_current_binding_not_the_founding_one():
    """"and now?" means "that question again", and after a rebind "that question" is whatever it most
    recently became. Resolving it from the walk-back returned the OLDEST text in the thread, so a
    conversation that had moved from Porto to Galway re-asked Porto."""
    session_id = _sid("current-binding")
    remember_live_data_obligation(
        session_id, operation="weather_lookup", slots=["Porto"],
        request_text="What is the current weather in Porto?",
        absorbed_text="What is the current weather in Porto?",
    )
    remember_live_data_obligation(
        session_id, operation="weather_lookup", slots=["Galway"],
        request_text="What is the current weather in Galway?",
        absorbed_text="actually Galway",
    )
    context = _conversation(
        session_id,
        ("user", "What is the current weather in Porto?"),
        ("assistant", "Porto: Clear, 19 C. Source: [example](https://example.invalid/weather)"),
        ("user", "actually Galway"),
        ("assistant", "Galway: Rain, 13 C. Source: [example](https://example.invalid/weather)"),
        ("user", "and now?"),
    )
    inherited = continuation_inherits_live_data("and now?", source_context=context)
    assert "galway" in inherited.lower(), inherited
    assert "porto" not in inherited.lower(), f"the nudge re-asked the founding subject: {inherited!r}"


def test_an_obligation_stays_reachable_across_several_exchanges():
    """The lookback is counted in USER TURNS, not messages. History interleaves assistant replies, so
    a six-MESSAGE window is three exchanges: the founding request scrolled out after the third
    follow-up and the obligation went dark while its own record still held every subject."""
    session_id = _sid("lookback")
    for city in ("Porto", "Bergen", "Cork", "Galway"):
        remember_live_data_obligation(
            session_id, operation="weather_lookup", slots=[city],
            request_text=f"What is the current weather in {city}?",
            absorbed_text=f"what about {city}?" if city != "Porto" else "What is the current weather in Porto?",
        )
    messages = [("user", "What is the current weather in Porto?"),
                ("assistant", "Porto: Clear, 19 C. Source: [example](https://example.invalid/weather)")]
    for city in ("Bergen", "Cork", "Galway"):
        messages.append(("user", f"what about {city}?"))
        messages.append(("assistant", f"{city}: Rain, 12 C. Source: [example](https://example.invalid/weather)"))
    messages.append(("user", "which one is warmest?"))

    inherited = continuation_inherits_live_data(
        "which one is warmest?", source_context=_conversation(session_id, *messages)
    )
    assert inherited, "the obligation went dark four exchanges in"
    for city in ("Porto", "Bergen", "Cork", "Galway"):
        assert city.lower() in inherited.lower(), f"{city} missing from the aggregate: {inherited!r}"


# ------------------------------------------------------------------------------------- AGGREGATE
# The turn names no new subject and refers to the SET the obligation has accumulated.


@pytest.mark.parametrize(
    "follow_up",
    ["which one is cheaper?", "compare them", "both please", "which of those is up more"],
)
def test_a_set_reference_aggregates_every_accumulated_subject(follow_up):
    session_id = _sid("aggregate")
    remember_live_data_obligation(
        session_id, operation="market_quote", slots=["bitcoin"],
        request_text="what is the price of bitcoin", absorbed_text="what is the price of bitcoin",
    )
    remember_live_data_obligation(
        session_id, operation="market_quote", slots=["ethereum"],
        request_text="what is the price of ethereum", absorbed_text="and ethereum?",
    )
    context = _conversation(
        session_id,
        ("user", "what is the price of bitcoin"),
        ("assistant", "Bitcoin: $61,000. Source: [example](https://example.invalid/market)"),
        ("user", "and ethereum?"),
        ("assistant", "Ethereum: $2,400. Source: [example](https://example.invalid/market)"),
        ("user", follow_up),
    )
    inherited = continuation_inherits_live_data(follow_up, source_context=context)
    assert "bitcoin" in inherited.lower() and "ethereum" in inherited.lower(), (
        f"{follow_up!r} did not aggregate both grounded assets: {inherited!r}"
    )


def test_a_title_cased_set_reference_still_aggregates():
    """A phone keyboard capitalises every word, and the weather branch's proof of a proper noun IS
    capitalisation -- so "Which One Is Warmer?" satisfies it and would rebind to a place called
    "Which One Warmer". The closed-class grammar guard is what stops that, and this test is the one
    that makes the guard load-bearing: without it, deleting the guard left the whole family green.
    """
    session_id = _sid("titlecase")
    # Written exactly as the runtime writes it: `request_text` is what the turn RESOLVED to, and
    # `absorbed_text` is what the user actually typed. On the rebinding turn those differ, and that
    # difference is what lets the walk-back tell a continuation from an interruption.
    remember_live_data_obligation(
        session_id, operation="weather_lookup", slots=["Porto"],
        request_text="What is the current weather in Porto?",
        absorbed_text="What is the current weather in Porto?",
    )
    remember_live_data_obligation(
        session_id, operation="weather_lookup", slots=["Bergen"],
        request_text="What is the current weather in Bergen?",
        absorbed_text="What about Bergen?",
    )
    context = _conversation(
        session_id,
        ("user", "What is the current weather in Porto?"),
        ("assistant", "Porto: Clear, 19 C. Source: [example](https://example.invalid/weather)"),
        ("user", "What about Bergen?"),
        ("assistant", "Bergen: Rain, 11 C. Source: [example](https://example.invalid/weather)"),
        ("user", "Which One Is Warmer?"),
    )
    inherited = continuation_inherits_live_data("Which One Is Warmer?", source_context=context)
    assert "porto" in inherited.lower() and "bergen" in inherited.lower(), inherited
    assert "warmer" not in inherited.lower(), f"the question became the subject: {inherited!r}"


def test_a_long_string_of_filler_is_not_a_bare_nudge():
    """The residue SHAPE gate, made load-bearing. Ten scaffolding words carry no request, so without
    a length gate they collapse to an empty residue and silently re-ask the live question -- a turn
    that spends a real lookup on someone changing the subject in a wordy way."""
    # Every token here is in `_SCAFFOLDING`, which is the point: with the length gate disabled the
    # residue collapses to empty and this becomes a bare nudge. An earlier version of this test used
    # "alright", which is NOT in the set, so one surviving content word kept it green against the
    # very mutation it was written to catch.
    filler = "ok so well then yeah now please tell me"
    assert not utterance_carries_no_independent_request(filler)
    context = _weather_thread(
        "filler", city="Porto", request="What is the current weather in Porto?", follow_up=filler
    )
    assert continuation_inherits_live_data(filler, source_context=context) == ""


@pytest.mark.parametrize("follow_up", ["or ethereum?", "solana instead", "ethereum rather"])
def test_a_coordinator_or_correction_opening_still_names_its_subject(follow_up):
    """The other half of the grammar guard's boundary: it must catch questions, not corrections.
    "or ethereum?" and "solana instead" substitute a subject and have to keep rebinding."""
    context = _market_thread(
        "coordinator", asset="bitcoin", request="what is the price of bitcoin", follow_up=follow_up
    )
    inherited = continuation_inherits_live_data(follow_up, source_context=context)
    assert inherited and "bitcoin" not in inherited.lower(), f"{follow_up!r} -> {inherited!r}"


def test_an_aggregate_needs_more_than_one_subject_to_aggregate():
    """A set reference over a single-subject obligation is not a set. Falling back to the ordinary
    re-ask is right; inventing a second subject to compare against would not be."""
    context = _market_thread(
        "lonely-set", asset="bitcoin", request="what is the price of bitcoin",
        follow_up="which one is cheaper?",
    )
    assert continuation_inherits_live_data("which one is cheaper?", source_context=context) == ""


# ------------------------------------------------------------------- DOMAIN DISTANCE: the market
# Same code path, different recognizer, different entity type, no market-specific branch behind it.


@pytest.mark.parametrize(
    "follow_up, expected_asset",
    [
        ("and ethereum?", "ethereum"),
        ("what about solana", "solana"),
        ("how about dogecoin?", "dogecoin"),
    ],
)
def test_the_same_rule_rebinds_a_market_obligation(follow_up, expected_asset):
    context = _market_thread(
        "market", asset="bitcoin", request="what is the price of bitcoin", follow_up=follow_up
    )
    inherited = continuation_inherits_live_data(follow_up, source_context=context)
    assert expected_asset in inherited.lower(), f"{follow_up!r} -> {inherited!r}"
    assert "bitcoin" not in inherited.lower(), inherited


def test_a_market_obligation_will_not_rebind_to_a_place():
    """The adversarial near-miss: a follow-up shaped EXACTLY like the weather rebind, arriving on a
    market obligation. "Bergen" is a proper noun and would satisfy the weather branch outright; the
    alias table is what refuses it here, so the two operations cannot borrow each other's subjects."""
    context = _market_thread(
        "wrong-type", asset="bitcoin", request="what is the price of bitcoin",
        follow_up="What about Bergen?",
    )
    assert continuation_inherits_live_data("What about Bergen?", source_context=context) == ""


# ------------------------------------------------------------------------------ NEGATIVE CONTROLS
# What must NOT happen. Every one of these was reachable by a looser version of this repair.


def test_a_new_unrelated_request_inherits_nothing():
    context = _weather_thread(
        "unrelated", city="Porto", request="What is the current weather in Porto?",
        follow_up="Explain how a TLS handshake works.",
    )
    assert continuation_inherits_live_data(
        "Explain how a TLS handshake works.", source_context=context
    ) == ""


def test_an_obligation_interrupted_by_another_request_is_no_longer_on_the_table():
    """The break-inheritance rule, from the walk-back side. The obligation record still EXISTS --
    this proves the record alone is not what keeps a thread alive, which is the failure mode a
    persisted obligation invites."""
    session_id = _sid("interrupted")
    remember_live_data_obligation(
        session_id, operation="weather_lookup", slots=["Porto"],
        request_text="What is the current weather in Porto?",
        absorbed_text="What is the current weather in Porto?",
    )
    context = _conversation(
        session_id,
        ("user", "What is the current weather in Porto?"),
        ("assistant", "Porto: Clear, 19 C. Source: [example](https://example.invalid/weather)"),
        ("user", "Explain how a TLS handshake works."),
        ("assistant", "A TLS handshake negotiates a shared secret."),
        ("user", "What about Bergen?"),
    )
    assert continuation_inherits_live_data("What about Bergen?", source_context=context) == ""


def test_a_bare_nudge_after_an_unrelated_turn_does_not_resurrect_the_obligation():
    session_id = _sid("nudge-after")
    remember_live_data_obligation(
        session_id, operation="weather_lookup", slots=["Porto"],
        request_text="What is the current weather in Porto?",
        absorbed_text="What is the current weather in Porto?",
    )
    context = _conversation(
        session_id,
        ("user", "What is the current weather in Porto?"),
        ("assistant", "Porto: Clear, 19 C. Source: [example](https://example.invalid/weather)"),
        ("user", "Explain how a TLS handshake works."),
        ("assistant", "A TLS handshake negotiates a shared secret."),
        ("user", "and?"),
    )
    assert continuation_inherits_live_data("and?", source_context=context) == ""


@pytest.mark.parametrize("follow_up", ["and the humidity?", "and the wind?", "what about tomorrow"])
def test_an_attribute_word_is_not_a_new_subject(follow_up):
    """The case the module's original docstring named as its reason to decline everything. It still
    declines -- an attribute is not a place, and rebinding to a lookup for somewhere called
    "humidity" would be a fabricated subject rather than a missing one."""
    context = _weather_thread(
        "attribute", city="Porto", request="What is the current weather in Porto?",
        follow_up=follow_up,
    )
    assert continuation_inherits_live_data(follow_up, source_context=context) == ""


def test_a_lowercase_unknown_noun_does_not_rebind_the_obligation():
    """The NAMED LIMITATION, pinned rather than left as an accident.

    There is no local place authority -- `tools/web/web_research` says so in its own source -- so an
    all-lowercase bare noun cannot be told from an attribute or a stray word, and the turn keeps the
    behaviour it had before this repair. Recorded as a test so that if a gazetteer ever arrives, the
    person adding it is told exactly which promise changes.
    """
    context = _weather_thread(
        "lowercase", city="Porto", request="What is the current weather in Porto?",
        follow_up="what about bergen?",
    )
    assert continuation_inherits_live_data("what about bergen?", source_context=context) == ""


def test_a_digit_bearing_follow_up_never_inherits():
    """Pre-existing rule, kept: "and 3 days?" changes the request rather than rebinding it."""
    context = _weather_thread(
        "digits", city="Porto", request="What is the current weather in Porto?",
        follow_up="and 3 days?",
    )
    assert continuation_inherits_live_data("and 3 days?", source_context=context) == ""
    assert not utterance_carries_no_independent_request("and 3 days?")


def test_no_obligation_means_no_rebind_however_well_shaped_the_follow_up():
    """A perfectly shaped rebinding turn on a conversation that never opened an obligation.

    This is the control that makes every REBIND assertion above mean something: they pass because an
    obligation was recorded, not because "What about X?" is treated as a weather request.
    """
    session_id = _sid("no-obligation")
    context = _conversation(
        session_id,
        ("user", "Tell me about the history of Porto."),
        ("assistant", "Porto grew around the Douro estuary."),
        ("user", "What about Bergen?"),
    )
    assert continuation_inherits_live_data("What about Bergen?", source_context=context) == ""


def test_one_chats_obligation_never_reaches_another_chat():
    """State ownership: the record is keyed by session, and a persisted store is exactly the kind of
    state that leaks across chats when it is not. Chat B never opened an obligation, so a follow-up
    there inherits nothing even while chat A's identical follow-up is still working."""
    chat_a, chat_b = _sid("chat-a"), _sid("chat-b")
    remember_live_data_obligation(
        chat_a, operation="weather_lookup", slots=["Porto"],
        request_text="What is the current weather in Porto?",
        absorbed_text="What is the current weather in Porto?",
    )
    history = (
        ("user", "What is the current weather in Porto?"),
        ("assistant", "Porto: Clear, 19 C. Source: [example](https://example.invalid/weather)"),
        ("user", "and Bergen?"),
    )
    assert continuation_inherits_live_data(
        "and Bergen?", source_context=_conversation(chat_b, *history)
    ) == ""
    # The control: the SAME follow-up, on the chat that actually owns the obligation, still works.
    assert "bergen" in continuation_inherits_live_data(
        "and Bergen?", source_context=_conversation(chat_a, *history)
    ).lower()


def test_an_empty_context_inherits_nothing():
    assert continuation_inherits_live_data("What about Bergen?", source_context={}) == ""
    assert continuation_inherits_live_data("What about Bergen?", source_context=None) == ""


# ----------------------------------------------------------------------------------- RECALL
# A recall is ABOUT the obligation, so it must not end it. Answering a recall from prior evidence is
# right; letting it terminate the thread reopens fabricated freshness one turn later.


def _thread_with_a_recall(session_id: str, aside: str) -> dict:
    remember_live_data_obligation(
        session_id, operation="weather_lookup", slots=["Porto"],
        request_text="What is the current weather in Porto?",
        absorbed_text="What is the current weather in Porto?",
    )
    remember_live_data_obligation(
        session_id, operation="weather_lookup", slots=["Bergen"],
        request_text="What is the current weather in Bergen?",
        absorbed_text="and Bergen?",
    )
    return _conversation(
        session_id,
        ("user", "What is the current weather in Porto?"),
        ("assistant", "Porto: Clear, 19 C. Source: [example](https://example.invalid/weather)"),
        ("user", "and Bergen?"),
        ("assistant", "Bergen: Rain, 11 C. Source: [example](https://example.invalid/weather)"),
        ("user", aside),
        ("assistant", "It reported 11 C in Bergen."),
        ("user", "and now?"),
    )


@pytest.mark.parametrize(
    "recall",
    # "Bergen again?" is deliberately NOT here: its residue is a bare proper noun, so it is a REBIND
    # to the subject the obligation already holds, not an aside about it. It fetches on its own turn
    # and is absorbed like any other rebind -- covered above, and a different outcome by design.
    ["what did it say about Bergen", "remind me, Bergen?", "was that Bergen"],
)
def test_a_recall_about_a_known_subject_does_not_end_the_thread(recall):
    """The turn AFTER a recall must still re-ask live. Before this, the recall read as an unrelated
    request, the walk-back stopped on it, and "and now?" fell through to the model holding the
    previous turn's readings -- the fabricated-freshness failure this module exists to prevent,
    reopened one turn later."""
    context = _thread_with_a_recall(_sid("recall"), recall)
    inherited = continuation_inherits_live_data("and now?", source_context=context)
    assert inherited, f"a recall ({recall!r}) ended the thread"
    assert "bergen" in inherited.lower(), inherited


def test_a_real_request_that_merely_mentions_a_known_subject_still_ends_the_thread():
    """The control that keeps the rule above from swallowing topic changes. "book me a flight to
    Bergen next Tuesday please" names a subject the obligation holds, but it is a request of its own
    -- and the walk-back must stop on it exactly as it would on any other."""
    context = _thread_with_a_recall(
        _sid("real-request"), "book me a flight to Bergen next Tuesday please"
    )
    assert continuation_inherits_live_data("and now?", source_context=context) == ""


def test_a_short_turn_that_names_a_new_subject_is_a_rebind_and_not_an_aside():
    """A turn that named something NEW is not an aside about the thread, even when it also mentions
    a subject the thread holds. It is a rebind, and a rebind that succeeded is absorbed on its own
    turn; one that did NOT is a turn the runtime failed to understand, and quietly continuing past
    it would carry the thread over a gap nobody noticed. "Bergen and Lisbon" mentions Bergen, which
    the obligation holds, and Lisbon, which it does not."""
    context = _thread_with_a_recall(_sid("names-new"), "Bergen and Lisbon")
    assert continuation_inherits_live_data("and now?", source_context=context) == ""


def test_an_aside_naming_a_subject_the_obligation_never_held_ends_the_thread():
    """A short turn about something else is still an interruption. Only the obligation's OWN
    subjects buy the step-over, which is what stops this becoming "short turns never interrupt"."""
    context = _thread_with_a_recall(_sid("other-subject"), "what did it say about Lisbon")
    assert continuation_inherits_live_data("and now?", source_context=context) == ""


# ------------------------------------------------------------------------------------- FRESHNESS
# Inheritance produces a REQUEST, never a replayed reading. Nothing here can answer from the record.


def test_what_is_inherited_is_a_request_and_never_an_observation():
    """The obligation stores what to ask, not what was answered. A rebind that returned the prior
    reading would present stale evidence as current -- the exact failure
    `tests/test_a_bare_followup_still_asks_the_live_question.py` exists to forbid, reached through
    rebinding instead of through a bare nudge."""
    context = _weather_thread(
        "freshness", city="Porto", request="What is the current weather in Porto?",
        follow_up="What about Bergen?",
    )
    inherited = continuation_inherits_live_data("What about Bergen?", source_context=context)

    assert inherited
    for stale in ("11 C", "Clear", "example.invalid", "Source:"):
        assert stale not in inherited, f"a prior observation leaked into the inherited request: {inherited!r}"
