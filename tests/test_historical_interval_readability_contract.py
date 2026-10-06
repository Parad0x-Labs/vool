"""Historical interval source readability differs from active-state applicability."""
from datetime import datetime, timezone
import pytest
from core.temporal_selection import AsOfIntent, TemporalCandidate, apply_temporal_selection, resolve_question_as_of
from tests.test_temporal_voolble_asof_contract import SURVEY_FIRST, SURVEY_SECOND
UTC = timezone.utc
JULY = "On 2024-07-10, which of Elena Ruiz and Julian Voss was assigned to the estuary survey? Explain using their recorded intervals rather than assuming that their assignments were shared."
NEGATIVE = "On 2024-07-10, was Rosa Li assigned to the reef survey, and what exact recorded interval supports that answer?"

def _dt(value):
    return datetime.fromisoformat(value).replace(tzinfo=UTC)

def _candidate(key, body, stated="2024-07-01", *, origin="query-leg"):
    return TemporalCandidate(key=key, body=body, role="user", authority="observed-user-statement",
        statement_at=_dt(stated).timestamp(), seq=None, origin=origin)

def _verdicts(records, question=JULY, *, multi_record=True, past_only=True):
    return apply_temporal_selection(records, intent=resolve_question_as_of(question),
        question=question, multi_record=multi_record, past_only=past_only,
        now_utc=_dt("2025-03-01"))

@pytest.mark.parametrize("body", [SURVEY_FIRST,
    "Julian Voss's continuous estuary-survey assignment ran from 2024-01-15 through 2024-10-15 inclusive. Elena Ruiz's first assignment ran from 2024-03-01 through 2024-06-30 inclusive."])
def test_actual_july_multi_actor_source_is_readable_in_either_interval_order(body):
    first=_candidate("first",body)
    later=_candidate("later",SURVEY_SECOND,"2025-03-01")
    verdicts=_verdicts([first,later])
    assert verdicts["first"].eligible
    assert not verdicts["later"].eligible
    assert verdicts["later"].reason == "future-relative-to-as-of"
    assert first.body == body

@pytest.mark.parametrize("body", [
    "Rosa Li's reef-survey assignment ran from 2024-03-01 through 2024-06-30 inclusive.",
    "Rosa Li's first reef-survey assignment ran from 2024-01-01 through 2024-01-31 inclusive. Her second assignment ran from 2024-03-01 through 2024-06-30 inclusive.",
    "Rosa Li's reef-survey assignment ran from 2024-08-01 through 2024-09-30 inclusive.",
])
def test_requested_interval_bounds_survive_for_past_negative_membership(body):
    verdict=_verdicts([_candidate("record",body)],NEGATIVE)["record"]
    assert verdict.eligible

@pytest.mark.parametrize("question,past_only,multi_record", [
    ("As of July 10 2024, how many reef-survey slots were active?",True,True),
    ("What recorded interval is current for the reef survey?",False,True),
    ("What is the reef-survey assignment right now?",False,False),
    ("On 2024-07-10, what was the reef-survey assignment?",True,False),
])
def test_interval_readability_does_not_change_scalar_or_current_applicability(question,past_only,multi_record):
    record=_candidate("record","Rosa Li's reef-survey assignment ran from 2024-03-01 through 2024-06-30 inclusive.")
    verdict=_verdicts([record],question,past_only=past_only,multi_record=multi_record)["record"]
    if question == "What recorded interval is current for the reef survey?":
        # No as-of intent: the existing operand policy is unchanged, not live proof.
        assert verdict.eligible and verdict.reason == "operand-kept"
    else:
        assert not verdict.eligible
        assert verdict.reason in {"window-expired-before-as-of","window-expired"}

def test_historical_interval_exception_never_admits_a_future_statement():
    record=_candidate("record","Rosa Li's reef-survey assignment ran from 2024-03-01 through 2024-06-30 inclusive.","2024-07-11")
    verdict=_verdicts([record],NEGATIVE)["record"]
    assert not verdict.eligible and verdict.reason == "future-relative-to-as-of"

def test_unrelated_chain_episode_does_not_gain_interval_readability():
    record=_candidate("wrong-episode","Kai Poe's glacier survey ran from 2024-03-01 through 2024-06-30 inclusive.",origin="chain")
    verdict=_verdicts([record],NEGATIVE)["wrong-episode"]
    assert not verdict.eligible and verdict.reason == "chain-unlinked"

def test_current_real_replacement_remains_authoritative():
    old=_candidate("old","The reef survey is assigned to Rosa Li from 2024-03-01 through 2024-06-30 inclusive.")
    correction=_candidate("new","Correction: the reef survey is assigned to Kai Poe instead of Rosa Li.","2024-07-02")
    verdicts=_verdicts([old,correction],"Who is assigned to the reef survey now?",multi_record=False,past_only=False)
    assert verdicts["new"].eligible and not verdicts["old"].eligible

def test_interval_evidence_does_not_resurrect_a_known_explicit_retraction():
    old=_candidate("old","Rosa Li's reef-survey assignment ran from 2024-03-01 through 2024-06-30 inclusive.")
    correction=_candidate("new","Scratch that reef-survey assignment: Rosa Li was never assigned; that interval was incorrect.","2024-07-02")
    verdicts=_verdicts([old,correction],NEGATIVE)
    assert not verdicts["old"].eligible
    assert verdicts["new"].eligible

@pytest.mark.parametrize("body", [
    "Rosa Li's reef-survey assignment ran from 2024-02-30 through 2024-06-30 inclusive.",
    "Rosa Li's reef-survey assignment ran from 2024-08-01 through 2024-06-30 inclusive.",
])
def test_invalid_or_reversed_bounds_do_not_gain_history_exception(body):
    assert not _verdicts([_candidate("record",body)],NEGATIVE)["record"].eligible

@pytest.mark.parametrize("body", [
    "Rosa Li's reef-survey assignment ran from 2024-03-01 through 2024-06-30 inclusive.",
    "Rosa Li's reef-survey assignment ran from 2024-03-01 through 2024-06-30 inclusive. Kai Poe's assignment ran from 2024-07-01 through 2024-07-31 inclusive.",
])
def test_plain_asof_counts_do_not_get_historical_interval_exception(body):
    verdict=_verdicts([_candidate("record",body)],"On 2024-07-10, how many reef-survey assignments were active?")["record"]
    assert not verdict.eligible and verdict.reason == "window-expired-before-as-of"
