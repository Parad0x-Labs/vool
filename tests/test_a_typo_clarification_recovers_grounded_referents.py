"""A typo-ridden clarification still re-asks the obligation whose referents it mistypes.

What this file pins
-------------------
`core.live_data_continuation` resolved follow-ups against the recorded obligation, but only for
turns that were continuation-SHAPED (short, digit-free) and named their subject cleanly enough for
the strict rebind paths. A long clarification whose subjects are MISTYPED satisfied neither half:

    U: Get weather for Kaunas.      -> grounded
    U: What about Tallinn?          -> rebound
    U: Which one is warmer?         -> aggregate
    U: I asked about Kauans and talling weather, ... them two right?
                                    -> the model lane, answered from nothing

recorded as a strict xfail in `tests/gauntlet/test_release_conversation_gauntlet.py` (G1, third
xfail). The repair is `_clarification_recovers_the_obligation`: the turn's tokens are matched
against the OBLIGATION'S OWN SLOTS -- never a gazetteer -- with the same two edit shapes
`core.live_data_plan._is_non_entity_filler` already tolerates for filler words (one
substitution/insertion/deletion, and an equal-length transposition), and the recovered set rebinds
the obligation exactly as any aggregate does.

The bounds are the point, and each has a control below:

* every slot must recover, at least one ONLY through a typo -- a turn whose subjects arrived clean
  carried its content through and stays with the lanes that already own it;
* a closed-class set reference must be present, checked before the database is touched;
* digits never inherit, exactly as at the shape gate;
* a capitalized word that recovers nothing may be a new subject, and rebinding would swallow it.

Fresh vocabulary on purpose: nothing below uses the gauntlet's cities. If the repair only worked on
the exam it was written against, every case here would fail. The market case is the domain-distance
check -- a different recognizer and a closed alias table behind the identical code path.
"""
from __future__ import annotations

import uuid

import pytest

from core.live_data_continuation import (
    _clarification_recovers_the_obligation,
    _is_typo_of,
    continuation_inherits_live_data,
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


def _grounded_thread(label: str, *, operation: str, request: str, slots: list[str], clarification: str) -> dict:
    session_id = _sid(label)
    remember_live_data_obligation(
        session_id,
        operation=operation,
        slots=slots,
        request_text=request,
        absorbed_text=request,
    )
    return _conversation(
        session_id,
        ("user", request),
        ("assistant", "grounded answer. Source: [example](https://example.invalid/x)"),
        ("user", clarification),
    )


# ------------------------------------------------------------------------------------------ POSITIVE


def test_a_two_subject_clarification_with_both_names_mistyped_recovers_the_set():
    context = _grounded_thread(
        "both-typo",
        operation="weather_lookup",
        request="Get weather for Porto and Bergen.",
        slots=["porto", "bergen"],
        clarification="I asked about Portos and bergenn weather, them two right?",
    )
    inherited = continuation_inherits_live_data(
        "I asked about Portos and bergenn weather, them two right?", source_context=context
    )

    assert inherited, "a clarification mistyping both subjects recovered nothing"
    assert "porto" in inherited.lower() and "bergen" in inherited.lower(), inherited
    assert "weather" in inherited.lower(), inherited


def test_one_clean_name_and_one_mistyped_name_still_recovers():
    """Only ONE subject needs recovering: the other spelling arrived intact."""
    clarification = "I asked about Porto and bergenn weather, them two right?"
    context = _grounded_thread(
        "one-typo",
        operation="weather_lookup",
        request="Get weather for Porto and Bergen.",
        slots=["porto", "bergen"],
        clarification=clarification,
    )

    inherited = continuation_inherits_live_data(clarification, source_context=context)

    assert "porto" in inherited.lower() and "bergen" in inherited.lower(), inherited


def test_a_transposition_typo_recovers_the_subject():
    """'Kauans' for 'Kaunas' is a keystroke swap, not a substitution -- the same shape, new word.

    Plain edit distance scores a transposition as two substitutions, which is why the anagram
    comparison exists at all.
    """
    clarification = "I asked about Tkyoo weather earlier, both times right?"
    context = _grounded_thread(
        "transposed",
        operation="weather_lookup",
        request="What is the current weather in Tokyo?",
        slots=["tokyo"],
        clarification=clarification,
    )

    inherited = continuation_inherits_live_data(clarification, source_context=context)

    assert "tokyo" in inherited.lower(), inherited


def test_a_single_subject_clarification_rebinds_that_subject():
    clarification = "I asked about Osakaa earlier, both times right?"
    context = _grounded_thread(
        "single",
        operation="weather_lookup",
        request="What is the current weather in Osaka?",
        slots=["osaka"],
        clarification=clarification,
    )

    inherited = continuation_inherits_live_data(clarification, source_context=context)

    assert "osaka" in inherited.lower(), inherited


def test_the_market_obligation_recovers_mistyped_assets_the_same_way():
    """Domain-distance control: a closed alias table behind the identical code path."""
    clarification = "I asked about cardano and sollana prices, them two right?"
    context = _grounded_thread(
        "market",
        operation="market_quote",
        request="What are the prices of cardano and solana?",
        slots=["cardano", "solana"],
        clarification=clarification,
    )

    inherited = continuation_inherits_live_data(clarification, source_context=context)

    assert "cardano" in inherited.lower() and "solana" in inherited.lower(), inherited


def test_the_recovery_helper_matches_the_two_tolerated_edit_shapes_only():
    assert _is_typo_of("kauans", "kaunas")  # transposition
    assert _is_typo_of("talling", "tallinn")  # one substitution
    assert _is_typo_of("osakaa", "osaka")  # one insertion
    assert _is_typo_of("osaka", "osaka")  # exact
    # A different word is not a typo of the subject, however the edit budget is counted.
    assert not _is_typo_of("portals", "porto")
    assert not _is_typo_of("because", "kaunas")
    assert not _is_typo_of("two", "osaka")  # too short to mean anything


# ------------------------------------------------------------------------------------------ NEGATIVE


def test_a_clarification_with_cleanly_typed_subjects_does_not_inherit():
    """The bound that keeps this lane from swallowing fresh requests.

    "Booking flights to Porto and Bergen tomorrow, them two cities" names the old subjects CLEANLY
    and means something new. Broken spelling is the only evidence that the user is pointing BACK at
    the grounded referents; without it, the turn keeps today's behaviour.
    """
    for clarification in (
        "I asked about Porto and Bergen weather, them two right?",
        "Booking flights to Porto and Bergen tomorrow, them two cities",
    ):
        context = _grounded_thread(
            "clean",
            operation="weather_lookup",
            request="Get weather for Porto and Bergen.",
            slots=["porto", "bergen"],
            clarification=clarification,
        )
        assert continuation_inherits_live_data(clarification, source_context=context) == "", (
            f"a clean-named turn inherited the obligation: {clarification!r}"
        )


def test_a_clarification_carrying_digits_never_inherits():
    clarification = "I asked about Portos and bergenn 2 days ago, them two right?"
    context = _grounded_thread(
        "digits",
        operation="weather_lookup",
        request="Get weather for Porto and Bergen.",
        slots=["porto", "bergen"],
        clarification=clarification,
    )

    assert continuation_inherits_live_data(clarification, source_context=context) == ""


def test_a_new_subject_named_alongside_the_typos_stops_the_inheritance():
    """'Oslo' may be a fresh referent; rebinding would silently swallow it."""
    clarification = "I asked about Portos and bergenn weather, plus Oslo, them three right?"
    context = _grounded_thread(
        "new-subject",
        operation="weather_lookup",
        request="Get weather for Porto and Bergen.",
        slots=["porto", "bergen"],
        clarification=clarification,
    )

    assert continuation_inherits_live_data(clarification, source_context=context) == ""


def test_a_clarification_without_a_set_reference_never_reaches_the_store():
    clarification = "I asked about Portos and bergenn weather earlier today, right?"
    context = _grounded_thread(
        "no-set-ref",
        operation="weather_lookup",
        request="Get weather for Porto and Bergen.",
        slots=["porto", "bergen"],
        clarification=clarification,
    )

    assert continuation_inherits_live_data(clarification, source_context=context) == ""


def test_a_clarification_about_unrelated_subjects_does_not_inherit():
    clarification = "I asked about Lyon and Nantes weather, them two right?"
    context = _grounded_thread(
        "unrelated",
        operation="weather_lookup",
        request="Get weather for Porto and Bergen.",
        slots=["porto", "bergen"],
        clarification=clarification,
    )

    assert continuation_inherits_live_data(clarification, source_context=context) == ""


def test_a_partial_recovery_of_a_larger_set_declines():
    """The clarification recovers the WHOLE current binding or nothing.

    A three-subject obligation asked about by naming two of them is not a restatement the lane can
    size: guessing which subset the user meant is exactly the invention the obligation exists to
    prevent.
    """
    clarification = "I asked about Portos and bergenn weather, them two right?"
    context = _grounded_thread(
        "partial",
        operation="weather_lookup",
        request="Get weather for Porto and Bergen.",
        slots=["porto", "bergen", "osaka"],
        clarification=clarification,
    )

    assert continuation_inherits_live_data(clarification, source_context=context) == ""


# ---------------------------------------------------------------------------------------- ADVERSARIAL


def test_a_clarification_with_no_recorded_obligation_stays_a_statement():
    session_id = _sid("no-obligation")
    context = _conversation(
        session_id,
        ("user", "Get weather for Porto and Bergen."),
        ("user", "I asked about Portos and bergenn weather, them two right?"),
    )

    assert (
        _clarification_recovers_the_obligation(
            "I asked about Portos and bergenn weather, them two right?", source_context=context
        )
        == ""
    )


def test_a_long_unrelated_statement_still_exits_before_the_database():
    """The shape gate's cost discipline survives: an ordinary long turn inherits nothing.

    The turn below carries no set reference, so the clarification path must decline from the regex
    alone -- touching the obligation store at all is asserted to be a defect.
    """
    from unittest import mock

    statement = (
        "Write a poem about the sea and the mountains and the quiet roads between the villages"
    )
    context = _grounded_thread(
        "ordinary",
        operation="weather_lookup",
        request="Get weather for Porto and Bergen.",
        slots=["porto", "bergen"],
        clarification=statement,
    )

    with mock.patch(
        "core.runtime_continuity.recall_live_data_obligation",
        side_effect=AssertionError("obligation store read for a turn with no set reference"),
    ):
        assert continuation_inherits_live_data(statement, source_context=context) == ""


def test_the_aggregate_rebinding_is_a_request_the_weather_recognizers_still_accept():
    """The inherited text faces the same recognizers any user-typed request would.

    If the rebind produced something the live-data lane could not classify, the repair would trade
    an unmentioned referent for an unanswerable request.
    """
    from core.execution_requirements import _live_data_classification

    clarification = "I asked about Portos and bergenn weather, them two right?"
    context = _grounded_thread(
        "classifiable",
        operation="weather_lookup",
        request="Get weather for Porto and Bergen.",
        slots=["porto", "bergen"],
        clarification=clarification,
    )

    inherited = continuation_inherits_live_data(clarification, source_context=context)

    assert inherited, "nothing inherited"
    assert _live_data_classification(inherited) is not None, (
        f"the recovered request fell out of the live-data lane: {inherited!r}"
    )


@pytest.mark.parametrize(
    "clarification",
    [
        "I asked about Portos and bergenn weather, them two right?",
        "so them two, Portos and bergenn, that was the weather question right?",
    ],
)
def test_second_subject_order_and_lead_in_do_not_matter(clarification: str):
    context = _grounded_thread(
        "shape",
        operation="weather_lookup",
        request="Get weather for Porto and Bergen.",
        slots=["porto", "bergen"],
        clarification=clarification,
    )

    inherited = continuation_inherits_live_data(clarification, source_context=context)

    assert inherited, f"{clarification!r} recovered nothing"
    assert "porto" in inherited.lower() and "bergen" in inherited.lower(), inherited
