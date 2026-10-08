"""Conditional procedure amendments preserve explicitly unchanged base steps."""
from datetime import datetime, timezone

import pytest

from core.temporal_selection import AsOfIntent, TemporalCandidate, apply_temporal_selection

UTC=timezone.utc
BASE = 'I run the Fern-22 paper-marbling batch. The ordinary procedure is ordered: first cut two sheets, then stamp F22 on their backs, then soak both sheets for 12 minutes, then hang them vertically, and finally attach a blue tag. Do not swap the stamping and soaking steps.'
EXCEPTION = 'Late exception to Fern-22: if ragged edges are found immediately after the 12-minute soak, trim the ragged edges before hanging, hang the sheets horizontally instead of vertically, and finish with a green tag instead of the blue tag. The cutting, stamping, and 12-minute soak still happen in their original order. Do not discard the sheets just because the edges are ragged.'
QUESTIONS = ['For Fern-22, no ragged edges appear after soaking. What is the full procedure in the correct order, including the duration, hanging direction, and tag color?', 'For Fern-22, ragged edges are found immediately after soaking. Give the full ordered procedure that now applies. What gets replaced, and should the sheets be discarded?']

def _candidate(key, body, seq):
    return TemporalCandidate(key=key, body=body, role="user", authority="observed-user-statement",
        statement_at=datetime(2025,2,seq+2,tzinfo=UTC).timestamp(),seq=seq)

def _select(base, amendment, question):
    return apply_temporal_selection([_candidate("base",base,1),_candidate("amendment",amendment,3)],
        intent=AsOfIntent(),question=question,now_utc=datetime(2025,3,1,tzinfo=UTC))

@pytest.mark.parametrize("question",QUESTIONS)
def test_exposed_fern_complete_procedure_keeps_base_and_conditional_exception(question):
    verdicts=_select(BASE,EXCEPTION,question)
    assert verdicts["base"].eligible
    assert verdicts["base"].reason=="eligible-coexists"
    assert verdicts["amendment"].eligible

@pytest.mark.parametrize("question",[
    "Give the full ordered procedure for the Larch-41 batch; the condition is unknown.",
    "What are all the Larch-41 steps in order if the edges are torn?",
])
def test_conditional_preservation_is_about_linked_steps_and_not_fixture_vocabulary(question):
    base="The Larch-41 procedure is ordered: first wash two tiles, then stamp L41, then dry for 9 minutes, then stack upright, and finally use a yellow label."
    amendment="Exception to Larch-41: if the edges are torn after drying, trim them, stack flat instead of upright, and use a red label instead of yellow. Washing, stamping, and the 9-minute drying still happen in the original order."
    verdicts=_select(base,amendment,question)
    assert verdicts["base"].eligible and verdicts["amendment"].eligible
    assert verdicts["base"].reason=="eligible-coexists"

@pytest.mark.parametrize("question",[
    "What is the current tag color for Fern-22?",
    "Which hanging direction is now used for Fern-22?",
])
def test_narrow_current_value_request_is_not_forced_to_include_all_base_steps(question):
    verdicts=_select(BASE,EXCEPTION,question)
    assert not verdicts["base"].eligible

def test_unconditional_procedure_replacement_still_supersedes_base():
    changed="Correction to Fern-22: the full procedure is now wash the sheets, dry for 4 minutes, hang horizontally instead of vertically, and finish with a green tag instead of blue. The cutting, stamping, and 12-minute soak are replaced."
    verdicts=_select(BASE,changed,QUESTIONS[1])
    assert not verdicts["base"].eligible
    assert verdicts["amendment"].eligible

def test_unrelated_conditional_with_original_order_words_cannot_preserve_base():
    changed="Correction to Fern-22: use 5 sheets and soak them for 4 minutes instead of 12. If the crate breaks, the label and packing still happen in their original order."
    verdicts=_select(BASE,changed,QUESTIONS[1])
    assert not verdicts["base"].eligible

def test_later_unconditional_correction_overrides_conditional_exception():
    changed="Disregard that exception. Final correction to Fern-22: use 5 sheets and soak for 4 minutes instead of 12, and the green tag is replaced with black."
    verdicts=apply_temporal_selection([_candidate("base",BASE,1),_candidate("exception",EXCEPTION,3),
        _candidate("final",changed,5)],intent=AsOfIntent(),question=QUESTIONS[1],
        now_utc=datetime(2025,3,1,tzinfo=UTC))
    assert not verdicts["base"].eligible
    assert not verdicts["exception"].eligible
    assert verdicts["final"].eligible
