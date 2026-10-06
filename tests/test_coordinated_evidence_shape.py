"""A multi-field ask cannot establish all-source absence from scalar vocabulary. Contributor: sls_0x."""
import pytest
from core.context_retrieval import _query_shape
from core.plain_task_routing import scalar_answer_covers_requested_shape

@pytest.mark.parametrize('question',[
 "I'm checking Cora's original gear label against her April 1 intake note. Which interface, sample rate, and case color did she report then?",
 "Which connector, operating voltage, and enclosure finish did I record?",
 "The workshop notes are on the desk. What width and finish did I select for the cabinet?",
 "What serial code, position, and cover did I choose for the instrument?",
])
def test_coordinated_requested_fields_are_a_facet_composition(question):
 assert _query_shape(question)=='facet'
 assert not scalar_answer_covers_requested_shape(question)

@pytest.mark.parametrize('question',[
 'What is the pool water temperature?',
 'What code did I set for heating and cooling systems?',
 'What code for heating and cooling systems did I choose?',
 'The sample says "What code and position did I choose?". What code did I set?',
 'What code did I set?',
])
def test_scalar_attribute_or_quoted_object_context_is_not_promoted(question):
 assert _query_shape(question)=='single'
 assert scalar_answer_covers_requested_shape(question)

def test_unknown_or_operational_shapes_gain_no_scalar_authority():
 assert not scalar_answer_covers_requested_shape('Please review this.')
 assert not scalar_answer_covers_requested_shape('What code did I set? Save it to a file.')

def test_unknown_shapes_are_not_falsely_classified_as_field_composition():
 assert _query_shape('Please review this.')=='single'
 assert _query_shape('Return exactly one valid JSON object with the sole key "water temperature" for the willow bench.')=='single'
