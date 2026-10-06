"""Elapsed-time development: both source-dated endpoints, no synthetic dates."""
import pytest
from core.context_retrieval import _query_shape, _multi_record_eligible
from tests.test_overnight_source_structure import source_env, _store, _recall


@pytest.mark.parametrize("question", [
    "How many days did it take me to finish The Jade Orchard?",
    "How long did I spend building the Red Cabinet?",
    "How many weeks elapsed between starting and finishing my bronze casting?",
])
def test_elapsed_time_requires_multiple_evidence_records(question):
    assert _query_shape(question) == "duration"
    assert _multi_record_eligible(question)


def test_shared_request_frame_cannot_supersede_duration_start(source_env):
    _store(source_env, "elapsed-book",
           "I'm looking for some book recommendations. I've been enjoying fiction, "
           "and I just started 'The Jade Orchard' by Elena Vale today.",
           "That novel has an interesting setting.", 1704067200)
    _store(source_env, "elapsed-book",
           "I'm looking for some book recommendations. I just finished 'The Jade Orchard' "
           "by Elena Vale today, and I am in the mood for something similar.",
           "You might enjoy another novel.", 1704931200)
    _store(source_env, "elapsed-book",
           "I'm actually currently reading 'The Bronze Lake' and enjoying it. "
           "I'm hoping to finish it by the end of the month.",
           "Enjoy the book.", 1705017600)
    capsule = _recall(source_env, "elapsed-book",
                      "How many days did it take me to finish The Jade Orchard by Elena Vale?")
    assert "just started 'The Jade Orchard'" in capsule, capsule
    assert "just finished 'The Jade Orchard'" in capsule, capsule
    assert "2024-01-01" in capsule and "2024-01-11" in capsule, capsule


@pytest.mark.parametrize("question", [
    "When does the morning shuttle leave?",
    "How many days are in my current warranty?",
    "What is the current delivery estimate?",
    "How long will the battery last?",
])
def test_duration_does_not_broaden_current_or_forecast_asks(question):
    assert _query_shape(question) != "duration"
