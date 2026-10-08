"""Structured collection selection keeps requested headings and later facets."""
import pytest

from core.context_retrieval import _evidence_clause_windows, _structured_source_windows


def test_more_specific_collection_heading_wins_over_generic_shared_noun():
    body="Desk equipment kit:\n- stapler\n- ruler\n\nBotanical survey equipment kit:\n- specimen sleeve\n- hand lens\n- grid card\n- plant press\n- label spool"
    query="How many parts were in the botanical survey equipment kit you listed?"
    windows=_evidence_clause_windows(query,body)
    text="\n".join(w["text"] for w in windows)
    assert all(v in text for v in ['specimen sleeve','hand lens','grid card','plant press','label spool']),text
    assert 'Botanical survey equipment kit:' in text
    for w in windows:assert body[w['start']:w['end']]==w['text']

@pytest.mark.parametrize('tail',[
    'Use depends on the steward signing the register.',
    'Use requires an annual inspection certificate.',
    'Use is suspended during the nesting season.',
])
def test_explicitly_requested_later_facet_cannot_be_shortened_away(tail):
    body='1. Cartographer surveys the inlet. '+tail+'\n2. Archivist records tidal observations. '+tail
    query='What were the two cartographer and archivist roles you listed, including their use conditions?'
    windows=_structured_source_windows(query,body)
    text='\n'.join(w['text'] for w in windows)
    assert text.count(tail)==2,text
    for w in windows:assert body[w['start']:w['end']]==w['text']


def test_requested_rules_do_not_need_to_repeat_the_source_nouns():
    query='What were the two keeper and surveyor roles you listed, including their entry rules?'
    body='1. Keeper maintains the reserve. Participation depends on an induction course.\n2. Surveyor maps the moor. Participation requires a current pass.'
    windows=_structured_source_windows(query,body)
    text='\n'.join(w['text'] for w in windows)
    assert 'Participation depends on an induction course.' in text,text
    assert 'Participation requires a current pass.' in text,text
