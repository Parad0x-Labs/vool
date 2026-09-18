"""A current reading may be stated only when this turn actually observed one.

Measured live on c6eed761 (2026-08-14): turns that needed a live value, reached the model with no
evidence, and got confident prose back -- "The current temperature in Tromso is 9 C" (the fetch a
turn earlier had said 10 C), "10 C. According to wttr.in, as of 08:30 AM" (never contacted),
"around 18-20 C" for the North Sea on a turn with web_calls: 0.

`core.live_data_continuation` closed the route those arrived through. This family pins the CLASS:
any path that leaves a live question with the model and no observation.

The invariant is a conjunction -- the turn required a current observation, the turn observed
nothing, and the answer states an observation anyway. The first conjunct is what keeps every static
and historical fact safe, and it is read from the REQUEST authority, never from the answer's
wording. Nothing here depends on weather, on Tromso, or on the word "current" appearing in a reply.
"""

from __future__ import annotations

import pytest

from core.unsourced_current_claim import (
    answer_asserts_a_measured_value,
    answer_attributes_a_source,
    inspect_unsourced_current_claim,
    turn_has_current_evidence,
    unverified_current_answer,
)


def _verdict(answer: str, *, requires_current: bool = True, notes=None, context=None):
    return inspect_unsourced_current_claim(
        answer=answer,
        requires_current=requires_current,
        notes=notes or [],
        source_context=context or {},
    )


# ---------------------------------------------------------------------------------------------
# G1 -- the measured reproductions
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "answer",
    (
        "The current temperature in Tromso is 9 C with light drizzle.",
        "10 C. According to the weather report from wttr.in, as of 08:30 AM today.",
        "The current water temperature in the North Sea is around 18-20°C (64-68°F).",
    ),
)
def test_the_measured_fabrications_are_all_unsupported(answer: str) -> None:
    assert _verdict(answer).unsupported, answer


# ---------------------------------------------------------------------------------------------
# CLEAN -- the same invariant across unrelated live domains
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "answer",
    (
        "Bitcoin is trading at $64,102 right now.",                      # price
        "The euro is at 1.09 USD at the moment.",                        # exchange rate
        "It is 23 knots from the northwest.",                            # wind / condition
        "United are up 2 goals with twenty minutes left.",               # sports
        "The cluster is running at 93% memory utilisation.",             # system status
        "Rainfall is 12 mm so far today.",                               # another measure
    ),
)
def test_an_unsupported_current_value_in_any_domain_is_caught(answer: str) -> None:
    assert _verdict(answer).unsupported, answer


def test_a_fabricated_attribution_alone_is_enough() -> None:
    """No number at all -- claiming WHERE it came from is a claim the turn cannot support."""

    verdict = _verdict("According to wttr.in, conditions are unchanged.")

    assert verdict.attributes_source
    assert verdict.unsupported


@pytest.mark.parametrize(
    "answer",
    (
        "Per https://api.example.invalid/latest, the reading holds steady.",
        "See [the exchange feed](https://feed.example.invalid) for the figure I used.",
        "Data from openweather.org confirms it.",
    ),
)
def test_attribution_shapes_are_caught_structurally(answer: str) -> None:
    assert _verdict(answer).unsupported, answer


# ---------------------------------------------------------------------------------------------
# NEGATIVE CONTROLS -- the guard must be silent wherever it has no standing
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "answer",
    (
        "Water freezes at 0°C at standard pressure.",
        "The speed of light is 299,792,458 m/s.",
        "A standard mandolin has 8 strings.",
        "The Krakatoa eruption occurred in 1883.",
        "Absolute zero is -273.15 C.",
        "One euro was worth about 1.18 USD in 2021, per the historical series you asked about.",
    ),
)
def test_a_turn_that_needs_no_current_observation_is_never_touched(answer: str) -> None:
    """The load-bearing early return: static and historical facts leave untouched, numbers and all."""

    assert not _verdict(answer, requires_current=False).unsupported, answer


def test_a_live_turn_with_retrieval_evidence_is_not_flagged() -> None:
    verdict = _verdict(
        "Tromso: Light drizzle, 10 C. Source: [wttr.in](https://wttr.in/tromso).",
        notes=[{"summary": "Tromso is 10 C and drizzling."}],
    )

    assert verdict.has_evidence
    assert not verdict.unsupported


def test_a_live_turn_with_a_deterministic_tool_observation_is_not_flagged() -> None:
    """Evidence is not only web retrieval -- a local tool observation is an observation.

    P1 turn-evidence isolation: the observation counts for the TURN THAT MADE IT. The record
    carries its turn id and the predicate is asked about that same turn. A caller that arrives
    without a turn identity grounds nothing from the session's records -- that session-wide
    fallback was the borrow-another-turn's-evidence defect this law closes.
    """

    from core import execution_records

    session = "evidence-session-tool"
    execution_records.clear(session)
    try:
        execution_records.record(
            session_id=session,
            intent="machine.disk_usage",
            arguments={},
            observation={"ok": True, "status": "executed"},
            turn_id="evidence-session-tool-turn",
        )
        assert turn_has_current_evidence(
            session_id=session, turn_id="evidence-session-tool-turn"
        )
        assert not turn_has_current_evidence(session_id=session, turn_id="")
        assert not turn_has_current_evidence(session_id=session, turn_id="some-other-turn")
    finally:
        execution_records.clear(session)


def test_user_supplied_material_counts_as_evidence() -> None:
    assert turn_has_current_evidence(source_context={"attachments": [{"name": "reading.csv"}]})


def test_a_turn_with_no_measured_value_and_no_source_is_not_flagged() -> None:
    """Truthfully reporting inability is exactly the behaviour wanted; it must not be caught."""

    verdict = _verdict("I could not obtain a current reading for this on this turn.")

    assert verdict.requires_current
    assert not verdict.has_evidence
    assert not verdict.unsupported


def test_counts_are_not_measurements() -> None:
    """"3 files", "eleven maintainers" are quantities, not readings."""

    assert not answer_asserts_a_measured_value("There are 3 files and eleven maintainers.")
    assert not answer_asserts_a_measured_value("I found 12 matches.")


# ---------------------------------------------------------------------------------------------
# ADVERSARIAL
# ---------------------------------------------------------------------------------------------


def test_a_source_url_in_the_prior_transcript_is_not_evidence_for_this_turn() -> None:
    """The whole point: the previous turn's receipt does not license this turn's attribution."""

    context = {
        "conversation_history": [
            {"role": "assistant", "content": "Tromso: 10 C. Source: [wttr.in](https://wttr.in/tromso)."}
        ]
    }

    assert not turn_has_current_evidence(source_context=context)
    assert _verdict("Still 10 C according to wttr.in.", context=context).unsupported


def test_an_answer_mixing_a_static_fact_with_an_unsupported_reading_is_withdrawn() -> None:
    """Stated plainly in the module: the whole answer goes rather than shipping the wrong half."""

    verdict = _verdict(
        "Water freezes at 0°C at standard pressure, and right now Tromso is sitting at 9 C."
    )

    assert verdict.unsupported


def test_evidence_from_another_session_does_not_count() -> None:
    from core import execution_records

    other = "evidence-session-other"
    execution_records.clear(other)
    try:
        execution_records.record(
            session_id=other,
            intent="machine.disk_usage",
            arguments={},
            observation={"ok": True, "status": "executed"},
        )
        assert not turn_has_current_evidence(session_id="evidence-session-mine")
    finally:
        execution_records.clear(other)


def test_notes_that_carry_no_findings_are_not_evidence() -> None:
    assert not turn_has_current_evidence(notes=[{"result_url": "https://x.invalid"}])


def test_the_replacement_answer_names_no_value_and_promises_nothing() -> None:
    text = unverified_current_answer("what is the current air temperature in Tromso right now?")

    assert not answer_asserts_a_measured_value(text)
    assert not answer_attributes_a_source(text)
    assert "Tromso" in text


def test_the_replacement_answer_survives_an_empty_request() -> None:
    text = unverified_current_answer("")

    assert text.strip()
    assert not answer_asserts_a_measured_value(text)


# ---------------------------------------------------------------------------------------------
# SLOPPY request shapes -- these must reach the guard by REQUIRING current information
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "followup", ("now?", "rn?", "current pls", "and now", "latest?", "still?", "update?")
)
def test_sloppy_nudges_after_a_live_turn_still_require_current_information(followup: str) -> None:
    from core.execution_requirements import requirements_for

    context = {
        "conversation_history": [
            {"role": "user", "content": "what is the current price of bitcoin"},
            {"role": "assistant", "content": "Bitcoin is $64,102."},
        ]
    }

    assert requirements_for(followup, source_context=context).current_information_required, followup


# ---------------------------------------------------------------------------------------------
# The operator web boundary the typed live-data lane never consulted
# ---------------------------------------------------------------------------------------------
def test_an_explicit_remote_fetch_veto_is_recognised() -> None:
    """`allow_remote_fetch: false` is the per-request form of the same operator veto."""

    from core.remote_fetch_policy import explicit_remote_fetch_disabled

    assert explicit_remote_fetch_disabled({"allow_remote_fetch": False})
    assert not explicit_remote_fetch_disabled({"allow_remote_fetch": True})
    assert not explicit_remote_fetch_disabled({}), "absence is the trusted-surface default, not a veto"


@pytest.mark.parametrize(
    "answer",
    (
        "The ISS is orbiting at 402 km altitude.",
        "The reservoir is holding 1.4 million litres.",
        "Throughput is running at 240 MB per second.",
        "The line frequency is sitting at 49.8 Hz.",
        "Pressure at the wellhead is 212 bar.",
    ),
)
def test_si_units_outside_the_original_domains_are_recognised(answer: str) -> None:
    """Found while proving this module: "402 kilometers" went unmatched.

    Grounding the unit set in SI/ISO rather than in the domains of the reported failure is what
    makes these pass without anyone having listed altitudes, reservoirs or line frequency.
    """

    assert _verdict(answer).unsupported, answer


def test_the_unit_set_still_does_not_turn_counts_into_measurements() -> None:
    """The extension must not swallow the negative control it was built around."""

    assert not answer_asserts_a_measured_value("There are 3 files and eleven maintainers.")
    assert not answer_asserts_a_measured_value("I reviewed 42 pull requests.")
