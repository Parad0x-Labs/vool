"""An invented past-event time does not ship as a recall.

Measured live on the served path (2026-09-29 demo: real API, real local model,
provider tap): after the clock misroute was repaired, "What time did I reach
the clinic on Monday?" was answered "you reached the clinic at 8:30 AM" and,
on a second run, "at 9:45 AM" -- with no clinic record anywhere in the turn's
material. The instruction-level abstention law was necessary and not
sufficient; this gate is the enforcement: a stated past time/date/duration the
request's own evidence never carried is withdrawn and named honestly.

REGRESSION rows restate the live-measured shapes. FRESH ACCEPTANCE rows were
authored with different wording, facts, domains and dates and frozen before
their first execution.
"""

from core.model_output_guard import (
    replace_unsupported_past_time_claims,
    stated_past_time_claims,
    unverified_past_time_notice,
)

_NOTICE_LEAD = unverified_past_time_notice("").split(",")[0]


def _replace(answer: str, question: str, evidence: list[str] | None = None) -> str:
    return replace_unsupported_past_time_claims(answer, question=question, evidence_texts=evidence)


# ---------------------------------------------------------------- regression


def test_clinic_answer_with_invented_time_is_withdrawn() -> None:
    # The live-measured shape: history exists (the purchase note), no clinic time.
    question = "What time did I reach the clinic on Monday?"
    answer = "Based on the information provided, you reached the clinic at 8:30 AM on Monday. Is there anything else you need help with?"
    evidence = [
        "user: Quick note for your memory: I paid 25,000 yen for the headphones when I bought them in March.",
        "assistant: Got it! So you paid 25,000 yen for the headphones you bought in March.",
    ]
    delivered = _replace(answer, question, evidence)
    assert "8:30 AM" not in delivered
    assert _NOTICE_LEAD in delivered


def test_second_measured_variant_is_withdrawn() -> None:
    question = "What time did I reach the clinic on Monday?"
    answer = "You reached the clinic at 9:45 AM on Monday."
    assert "9:45" not in _replace(answer, question, ["user: hello", "assistant: hi"])
    assert _replace(answer, question, ["user: hello"]).startswith(_NOTICE_LEAD)


def test_supported_time_from_history_ships_unchanged() -> None:
    question = "What time did I reach the clinic on Monday?"
    answer = "You reached the clinic at 9:00 AM on Monday."
    evidence = ["user: Just so you know, I got to the clinic at 9:00 AM on Monday."]
    assert _replace(answer, question, evidence) == answer


# ------------------------------------------------------- positive controls


def test_present_time_question_is_not_gated() -> None:
    # The gate's jurisdiction is past-anchored questions; "now" questions keep
    # the live-value guards' jurisdiction. The call below passes a present
    # question with an evidence-less time: the function is textual and only
    # convicts on values, so this control pins the QUESTION-side exemption at
    # the wiring level -- asserted here by the empty-claims verdict the seam
    # checks before calling the rewriter.
    from core.temporal_question_scope import question_time_scope

    assert question_time_scope("What time is it now?").asks_past is False


def test_question_premise_cannot_authorize_an_asserted_duration() -> None:
    question = "How long did the renovation take, 6 months?"
    answer = "The renovation took 6 months."
    # A question proposes a value; it does not establish that the event had it.
    assert "d6month" in stated_past_time_claims(answer, question=question, evidence_texts=[])


def test_current_year_event_requires_event_evidence() -> None:
    question = "When did I file the tax return?"
    answer = "You filed the return earlier in 2026."
    claims = stated_past_time_claims(answer, question=question, evidence_texts=[], current_year=2026)
    assert "y2026" in claims


# ---------------------------------------------------------- fresh acceptance


def test_fresh_ferry_time_invention_is_withdrawn() -> None:
    question = "When did the Nida ferry leave last Thursday?"
    answer = "The ferry left at 14:10 that afternoon."
    delivered = _replace(answer, question, ["user: planning a trip", "assistant: have fun"])
    assert "14:10" not in delivered
    assert _NOTICE_LEAD in delivered


def test_fresh_choir_year_invention_is_withdrawn() -> None:
    question = "Which year did I join the choir?"
    answer = "You joined the choir in 2016."
    delivered = _replace(answer, question, ["user: hi"])
    assert "2016" not in delivered
    assert _NOTICE_LEAD in delivered


def test_fresh_stint_duration_invention_is_withdrawn() -> None:
    question = "How long had you been running the backup before the disk failed?"
    answer = "I had been running it for 4 months before the failure."
    delivered = _replace(answer, question, [])
    assert "4 months" not in delivered
    assert _NOTICE_LEAD in delivered


def test_fresh_supported_date_paraphrase_ships() -> None:
    # Tolerant normalization: evidence "12 March 2021", answer "March 12, 2021".
    question = "When did I buy the ferry ticket?"
    answer = "You bought the ticket on March 12, 2021."
    evidence = ["user: I paid EUR 40 for the ferry ticket on 12 March 2021."]
    assert _replace(answer, question, evidence) == answer


def test_fresh_supported_clock_paraphrase_ships() -> None:
    # Evidence "arrived at 9:00 AM", answer "9 AM" -- hour+meridiem form.
    question = "What time did my parents arrive?"
    answer = "They arrived at 9 AM."
    evidence = ["user: my parents arrived at 9:00 AM sharp."]
    assert _replace(answer, question, evidence) == answer


def test_fresh_monthday_without_year_matches_dated_evidence() -> None:
    question = "When did we sign the lease?"
    answer = "You signed the lease on April 2."
    evidence = ["user: we signed the Kaunas lease on April 2, 2024."]
    assert _replace(answer, question, evidence) == answer


def test_fresh_wrong_year_is_convicted_despite_nearby_right_year() -> None:
    # The evidence carries 2021; the answer claims 2018 -- a near miss is a
    # fabrication, not a paraphrase.
    question = "When did I start the pottery course?"
    answer = "You started the pottery course in 2018."
    evidence = ["user: I started pottery in 2021, remember?"]
    delivered = _replace(answer, question, evidence)
    assert "2018" not in delivered


def test_fresh_sentence_granular_mixed_answer_keeps_clean_half() -> None:
    question = "When did the ferry leave and why was it late?"
    answer = (
        "I don't have the ferry's departure time in what I can see. "
        "The port had posted gale warnings that Thursday, which usually delays the small ferries."
    )
    delivered = _replace(answer, question, ["user: any news about the ferry?"])
    assert "gale warnings" in delivered


def test_fresh_supported_duration_from_history_ships() -> None:
    question = "How long did the visa renewal take?"
    answer = "The renewal took 11 weeks."
    evidence = ["user: the visa renewal finally finished -- 11 weeks end to end."]
    assert _replace(answer, question, evidence) == answer


def test_fresh_computed_date_in_current_year_is_not_exempt() -> None:
    # A full date in the current year is a specific past-day claim; only
    # TODAY'S date is the clock fact's own value. Live-measured: a wrong
    # weekday computation ("Last Thursday was on September 22, 2026" when
    # 2026-09-22 is a Tuesday) must not ride the current-year exemption.
    question = "When did the Nida ferry leave last Thursday?"
    answer = "Last Thursday was on September 22, 2026. I don't have the departure time."
    claims = stated_past_time_claims(answer, question=question, evidence_texts=[], current_year=2026)
    assert "m9:22:2026" in claims


# ---------------------------------------------------------------------------------------------
# The present-year echo is not a substitution ticket (V corpus F15-01, q90 routing)
# ---------------------------------------------------------------------------------------------

def test_current_year_substituting_a_recorded_year_is_withdrawn() -> None:
    """F15-01 delivered surface: the capsule carries the true '9 May 2024' and the
    answer asserts 2026 -- the unconditional clock-echo exemption swallowed exactly
    this substitution on the integrated head (probe S8)."""

    question = "When did the old landmark sound again after the big overhaul?"
    answer = "The old tower sounded again in 2026, right after its overhaul work wrapped up."
    evidence = [
        "user said: The Greely clock tower first chimed again on 9 May 2024 at 15:00 after the restoration."
    ]
    claims = stated_past_time_claims(answer, question=question, evidence_texts=evidence, current_year=2026)
    assert "y2026" in claims
    delivered = _replace(answer, question, evidence)
    assert "2026" not in delivered
    assert _NOTICE_LEAD in delivered


def test_absent_event_year_does_not_promote_runtime_year() -> None:
    """No event record means an event year is unknown, even in the current year.

    This replaces the earlier unsafe exemption. The preserved baseline receipt
    records that the old product and old assertion accepted this invention.
    """

    question = "When did I file the tax return?"
    answer = "You filed the return earlier in 2026."
    claims = stated_past_time_claims(answer, question=question, evidence_texts=[], current_year=2026)
    assert "y2026" in claims


def test_a_question_naming_a_year_also_disarms_the_echo_exemption() -> None:
    question = "When did I file the 2024 return?"
    answer = "You filed it earlier in 2026."
    claims = stated_past_time_claims(answer, question=question, evidence_texts=[], current_year=2026)
    assert "y2026" in claims
