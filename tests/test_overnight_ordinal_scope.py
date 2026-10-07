"""Ordinal list probes require an ordered collection in the question."""
import pytest

from core.context_retrieval import _query_ordinal_reference


@pytest.mark.parametrize('q,expected',[
    ('What song on the Fifth Album did you say showed their growth?',None),
    ('What did you say about the Third Symphony in our previous discussion?',None),
    ('Which date did you assign to the Second Crossing?',None),
    ('What was the seventh item in the list you gave me?',7),
    ('Remind me of the third eyepiece in my lunar ranking.',3),
    ('What was the 4th instruction in your checklist?',4),
])
def test_named_entities_are_not_list_positions(q,expected):
    assert _query_ordinal_reference(q)==expected
