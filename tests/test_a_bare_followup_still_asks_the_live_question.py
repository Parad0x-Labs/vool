"""A nudge after a live-data answer re-asks it; it never licenses an invented current value.

Measured live on c6eed761 (2026-08-14), Local Only and Auto both. Turn 1 answered correctly through
`deterministic:live_data_typed_plan` with `web_calls=1` and one receipt. Turn 2 -- carrying no
request of its own -- routed to `model_minimal` with `web_calls=0`, `receipts=0`, and answered
anyway, three different ways:

    "and?"           -> "I can't access live data or the internet"   (a capability lie)
    "well?"          -> "The current temperature in Tromso is 9 C"   (the fetch had said 10 C)
    "what about now" -> "10 C. According to wttr.in, as of 08:30 AM" (a source never contacted)

These tests assert the invariant -- a bare continuation inherits the live request, and a follow-up
that introduces content does not silently inherit anything -- not the tokens that exposed it. The
later families drop weather, drop Tromso, and drop every follow-up word used in the reproduction.
"""

from __future__ import annotations

import pytest

from core.live_data_continuation import (
    continuation_inherits_live_data,
    prior_live_data_request,
    utterance_carries_no_independent_request,
)

_WEATHER = "what is the current air temperature in Tromso right now?"
_ANSWERED = "Tromso: Light drizzle, 10 C. Source: [wttr.in](https://wttr.in/tromso)."


def _history(*turns: tuple[str, str]) -> dict:
    return {"conversation_history": [{"role": role, "content": content} for role, content in turns]}


# ---------------------------------------------------------------------------------------------
# G1 -- the measured reproduction
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("followup", ("and?", "well?", "what about now"))
def test_the_three_measured_followups_inherit_the_live_request(followup: str) -> None:
    inherited = continuation_inherits_live_data(
        followup, source_context=_history(("user", _WEATHER), ("assistant", _ANSWERED))
    )

    assert inherited == _WEATHER


# ---------------------------------------------------------------------------------------------
# G2 -- clean variants: follow-up shapes never used in the reproduction
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "followup",
    ("so?", "current?", "temp?", "pls?", "any update", "still?", "and now", "?"),
)
def test_unseen_bare_followups_inherit_the_same_request(followup: str) -> None:
    inherited = continuation_inherits_live_data(
        followup, source_context=_history(("user", _WEATHER), ("assistant", _ANSWERED))
    )

    assert inherited == _WEATHER, followup


def test_a_distant_live_domain_inherits_identically() -> None:
    """No weather anywhere: a market request, nudged."""

    asked = "what is the current price of bitcoin"
    inherited = continuation_inherits_live_data(
        "well?",
        source_context=_history(("user", asked), ("assistant", "Bitcoin is $64,102. Source: [x](https://x.invalid)")),
    )

    assert inherited == asked


# ---------------------------------------------------------------------------------------------
# G3 -- sloppy / user-style
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "followup",
    ("k", "hmm", "wat", "and??", "so...", "ok so", "thx and?", "update?", "and u?"),
)
def test_sloppy_nudges_are_still_bare(followup: str) -> None:
    assert utterance_carries_no_independent_request(followup), followup


@pytest.mark.parametrize("ambiguous", ("n?", "yea?"))
def test_an_unrecognised_fragment_declines_rather_than_guessing(ambiguous: str) -> None:
    """Deliberate: a fragment the scaffolding set does not know keeps today's behaviour.

    Growing that set until every possible keystroke is covered is the unbounded list this module
    exists to avoid. Declining costs the turn nothing it has now; a wrong inheritance would re-run
    a live fetch the user did not ask for.
    """

    assert not utterance_carries_no_independent_request(ambiguous)


# ---------------------------------------------------------------------------------------------
# NEGATIVE CONTROLS -- inheritance must not reach where it has no standing
# ---------------------------------------------------------------------------------------------


def test_a_genuinely_new_topic_inherits_nothing() -> None:
    assert (
        continuation_inherits_live_data(
            "how do I bake sourdough bread",
            source_context=_history(("user", _WEATHER), ("assistant", _ANSWERED)),
        )
        == ""
    )


def test_a_followup_that_names_something_new_inherits_nothing() -> None:
    """Scoped on purpose: this module declines rather than guessing what the new word replaces."""

    for followup in ("what about Berlin?", "and the humidity?", "what about the wind speed"):
        assert (
            continuation_inherits_live_data(
                followup, source_context=_history(("user", _WEATHER), ("assistant", _ANSWERED))
            )
            == ""
        ), followup


def test_a_followup_carrying_a_number_inherits_nothing() -> None:
    """"and 3 days?" changes the request; inheriting a single-day one would answer the wrong thing."""

    assert not utterance_carries_no_independent_request("and 3 days?")
    assert (
        continuation_inherits_live_data(
            "and 3 days?", source_context=_history(("user", _WEATHER), ("assistant", _ANSWERED))
        )
        == ""
    )


def test_a_nudge_with_no_prior_live_data_inherits_nothing() -> None:
    assert (
        continuation_inherits_live_data(
            "and?",
            source_context=_history(("user", "who wrote Dune"), ("assistant", "Frank Herbert.")),
        )
        == ""
    )


def test_a_nudge_with_no_history_at_all_inherits_nothing() -> None:
    assert continuation_inherits_live_data("and?", source_context={}) == ""
    assert continuation_inherits_live_data("and?", source_context=None) == ""


def test_an_intervening_non_live_request_ends_the_search() -> None:
    """A bare nudge binds to what was JUST asked, not to an older live turn the user moved on from."""

    assert (
        continuation_inherits_live_data(
            "and?",
            source_context=_history(
                ("user", _WEATHER),
                ("assistant", _ANSWERED),
                ("user", "explain how a thermocouple works"),
                ("assistant", "A thermocouple pairs two metals..."),
            ),
        )
        == ""
    )


# ---------------------------------------------------------------------------------------------
# ADVERSARIAL NEAR-MISSES
# ---------------------------------------------------------------------------------------------


def test_a_long_utterance_of_only_scaffolding_words_is_not_treated_as_bare() -> None:
    """A bound on length: past a nudge, the user is saying something, whatever the words are."""

    assert not utterance_carries_no_independent_request(
        "ok so what about the thing you were going to tell me now then"
    )


def test_repeated_nudges_walk_back_to_the_request_they_all_bind_to() -> None:
    inherited = continuation_inherits_live_data(
        "well?",
        source_context=_history(
            ("user", _WEATHER),
            ("assistant", _ANSWERED),
            ("user", "and?"),
            ("assistant", "..."),
        ),
    )

    assert inherited == _WEATHER


def test_an_empty_utterance_is_not_a_continuation() -> None:
    assert not utterance_carries_no_independent_request("")
    assert not utterance_carries_no_independent_request("   ")


def test_prior_live_data_request_reads_the_user_turns_only() -> None:
    """An assistant sentence that happens to look like a request must not become the referent."""

    assert (
        prior_live_data_request(
            _history(("assistant", "what is the current price of bitcoin"), ("user", "hello"))
        )
        == ""
    )


# ---------------------------------------------------------------------------------------------
# The wiring: the requirement itself must change, not just the helper
# ---------------------------------------------------------------------------------------------


def test_the_requirement_for_a_bare_nudge_now_demands_current_information() -> None:
    from core.execution_requirements import requirements_for

    plain = requirements_for("and?", source_context={})
    inherited = requirements_for(
        "and?", source_context=_history(("user", _WEATHER), ("assistant", _ANSWERED))
    )

    assert not plain.current_information_required
    assert inherited.current_information_required
    assert "live_data_continuation_inherited" in inherited.reason_codes


def test_a_new_topic_after_a_live_turn_keeps_its_ordinary_requirement() -> None:
    from core.execution_requirements import requirements_for

    requirement = requirements_for(
        "how do I bake sourdough bread",
        source_context=_history(("user", _WEATHER), ("assistant", _ANSWERED)),
    )

    assert "live_data_continuation_inherited" not in requirement.reason_codes
