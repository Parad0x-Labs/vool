"""Literal source quotation boundaries do not include word apostrophes."""
import pytest

from tests.test_overnight_source_structure import _recall, _store, source_env


@pytest.mark.parametrize("utterance", [
    "I'm tuning the brass gong to 294 Hz.",
    "I’ve tuned the brass gong to 294 Hz.",
    "My studio’s brass gong rings at 294 Hz.",
])
def test_ordinary_apostrophes_keep_exact_reported_source_binding(source_env, utterance):
    from core.context_retrieval import get_last_retrieval_telemetry
    body = "Mira: The studio walls were white. " + utterance
    _store(source_env, "apostrophe-prefix", body, "Recorded.")
    capsule = _recall(source_env, "apostrophe-prefix", "What frequency did the brass gong use?")
    assert "294 Hz" in capsule
    assert 'reported source prefix "Mira:"' in capsule, capsule
    for receipt in get_last_retrieval_telemetry().get("reported_source_prefix_refs", []):
        assert body[receipt["span"]["start"]:receipt["span"]["end"]] == receipt["span"]["text"]

@pytest.mark.parametrize("quotation", [
    "She quoted 'I tuned the brass gong to 294 Hz.'",
    "She quoted ‘I tuned the brass gong to 294 Hz.’",
])
def test_nested_single_quoted_first_person_is_not_bound_to_outer_label(source_env, quotation):
    body = "Mira: The studio walls were white. " + quotation
    _store(source_env, "nested-prefix", body, "Recorded.")
    capsule = _recall(source_env, "nested-prefix", "What frequency did the brass gong use?")
    assert "reported source prefix" not in capsule, capsule
