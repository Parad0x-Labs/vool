"""The temporal reference of a question — one decision, structural constructions only.

REGRESSION rows are the saved failure shapes from the frozen base e821457d
(LongMemEval packet 2026-09-28): the clinic-arrival clock misroute (q051a848)
and the tenure current-reading rewrite (q7db408b).

FRESH ACCEPTANCE rows below were authored with different wording, facts, roles,
dates and domains from every saved example, and frozen before their first
execution. If the module changes after a fresh-case failure, these cases stay
as-is and genuinely new confirmation cases get added instead.
"""

from core.temporal_question_scope import TimeReference, question_time_scope


def _names(text: str) -> tuple[str, ...]:
    return tuple(ref.name for ref in question_time_scope(text).references)


# ---------------------------------------------------------------- regression


def test_clinic_arrival_question_is_past_user_history() -> None:
    scope = question_time_scope("What time did I reach the clinic on Monday?")
    assert scope.asks_past is True
    assert scope.past_subject == "user"
    assert scope.past_only is True


def test_tenure_question_is_past_not_current() -> None:
    # q7db408b: "before I started" anchors the ask to the operator's past.
    scope = question_time_scope(
        "How long have I been working before I started my current job at Google?"
    )
    assert scope.asks_past is True
    assert scope.past_only is True


def test_historical_worth_question_is_past() -> None:
    # q254a2bf class: a value as of a past purchase, not a live rate.
    scope = question_time_scope(
        "How much was 25000 yen worth when I bought the headphones?"
    )
    assert scope.asks_past is True
    assert scope.past_only is True


def test_bare_clock_question_is_not_past() -> None:
    assert _names("What time is it now?") == ("CURRENT",)


# ---------------------------------------------------------- fresh acceptance


def test_fresh_ferry_departure_is_world_past() -> None:
    scope = question_time_scope("What time did the ferry from Nida leave last Thursday?")
    assert scope.asks_past is True
    assert scope.past_subject == "world"
    assert scope.past_only is True


def test_fresh_pet_adoption_is_user_past() -> None:
    scope = question_time_scope("When did we adopt the cat?")
    assert scope.asks_past is True
    assert scope.past_subject == "user"
    assert scope.past_only is True


def test_fresh_lease_renewal_anchor_is_user_past() -> None:
    scope = question_time_scope("What was the rent on the flat in Kaunas before we renewed the lease?")
    assert scope.asks_past is True
    assert scope.past_subject == "user"


def test_fresh_assistant_history_is_assistant_past() -> None:
    scope = question_time_scope("How long had you been running the nightly backup before the disk failed?")
    assert scope.asks_past is True
    assert scope.past_subject == "assistant"


def test_fresh_shift_pattern_is_habitual_not_past_or_current() -> None:
    scope = question_time_scope("What time does Marius usually start his shift?")
    assert scope.asks_habitual is True
    assert scope.past_only is True
    assert scope.asks_past is False


def test_fresh_current_rate_stays_current() -> None:
    assert _names("What's the EUR to USD rate right now?") == ("CURRENT",)


def test_fresh_mixed_question_is_not_past_only() -> None:
    # The past half is answerable from history, but the "today" half keeps its
    # current-observation requirements. Mixed questions must stay mixed.
    scope = question_time_scope("What did we pay for the piano, and what would it fetch today?")
    assert scope.asks_past is True
    assert scope.asks_current is True
    assert scope.past_only is False


def test_fresh_unscoped_question_has_no_references() -> None:
    assert _names("Who painted The Garden of Earthly Delights?") == ()


def test_fresh_as_of_now_is_current_but_as_of_year_is_past() -> None:
    assert TimeReference.CURRENT in question_time_scope("What is our balance as of now?").references
    scope = question_time_scope("What was the workshop turnover as of 2021?")
    assert scope.asks_past is True
    assert scope.past_only is True


def test_fresh_prohibited_current_clause_does_not_create_scope() -> None:
    # The current mention sits in the clause the user ruled out; the ask is
    # about the past purchase.
    scope = question_time_scope("Don't check the current rate - what did I pay for the tiles?")
    assert scope.asks_past is True
    assert scope.asks_current is False
    assert scope.past_only is True


def test_fresh_calendar_year_anchor_is_world_past() -> None:
    scope = question_time_scope("How many tickets did the choir sell in 2018?")
    assert scope.asks_past is True
    assert scope.past_only is True


def test_fresh_present_perfect_duration_is_past() -> None:
    scope = question_time_scope("How long has she been on the waiting list?")
    assert scope.asks_past is True
    assert scope.past_only is True


def test_impersonal_clock_arithmetic_is_not_past() -> None:
    # "what time was it <then>" asks what the CLOCK read at an instant -- the
    # date/time fast path owns that arithmetic. An event's time ("what time
    # was the ferry") is recall. The dummy subject is the whole difference.
    assert _names("what time was it 2h ago in berlin") == ()
    assert _names("What time was it in Denver 2 hrs ago?") == ()
    scope = question_time_scope("What time was the last ferry on Thursday?")
    assert scope.asks_past is True
