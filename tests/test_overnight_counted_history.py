"""Counted prior-output sets retain each name and its predicate."""
import pytest

from core.context_retrieval import _query_shape, _structured_source_windows


@pytest.mark.parametrize('q',[
    'Remind me of the two studios you mentioned that offer apprenticeships like Northfield.',
    'Which were the three projects you recommended for beginners?',
    'What were both courses you suggested for evening study?',
])
def test_counted_prior_output_is_an_enumeration(q):
    assert _query_shape(q)=='enumeration'


def test_counted_names_pack_complete_label_predicates_without_biography():
    q='What were the two studios you mentioned that offer apprenticeships like Northfield?'
    one='1. Aster Studio offers paid apprenticeships to young sculptors.'
    two='2. Copper Studio offers apprenticeships in metal casting.'
    body='Here are two studios:\n'+one+' Its founder trained abroad. It moved across town.\n'+two+' The building opened decades ago. It later added a courtyard.'
    windows=_structured_source_windows(q,body)
    text='\n'.join(w['text'] for w in windows)
    assert one in text and two in text,text
    assert 'founder' not in text and 'courtyard' not in text,text
    assert all(body[w['start']:w['end']]==w['text'] for w in windows)


def test_later_negative_qualifier_cannot_be_windowed_away():
    q='What were the two studios you mentioned that offer apprenticeships?'
    body='1. Aster Studio offers apprenticeships. However, these are not paid.\n2. Copper Studio offers apprenticeships. Only current students can enroll.'
    windows=_structured_source_windows(q,body)
    text='\n'.join(w['text'] for w in windows)
    assert 'not paid' in text and 'Only current students' in text,text

@pytest.mark.parametrize('q',[
    'What was the price of the two courses I bought?',
    'What did you say about the Third Symphony?',
])
def test_counts_inside_single_attributes_do_not_create_set_requests(q):
    assert _query_shape(q)=='single'
