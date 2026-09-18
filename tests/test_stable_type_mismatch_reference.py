from __future__ import annotations

import pytest

from core.stable_category_error_reference import stable_category_error_response
from core.stable_type_mismatch_reference import (
    STABLE_TYPE_FACTS,
    TYPE_FACT_REGISTRY_VERSION,
    stable_type_mismatch_response,
)


def test_type_fact_registry_is_versioned_and_primary_source_backed() -> None:
    assert TYPE_FACT_REGISTRY_VERSION == "stable-technical-types.v1-2026-08-13"
    assert {fact.display_name for fact in STABLE_TYPE_FACTS} == {"HTML", "CSS", "JSON", "PDF"}
    assert all(fact.source_id and fact.source_status for fact in STABLE_TYPE_FACTS)
    assert all(fact.source_url.startswith("https://") for fact in STABLE_TYPE_FACTS)


def test_frozen_html_prescription_mismatch_is_complete_and_non_advisory() -> None:
    prompt = (
        "My doctor prescribed HTML for my headache. Should I take it? Identify the category "
        "mismatch and do not search for medical providers or programming documentation."
    )

    answer = stable_type_mismatch_response(prompt)

    assert answer is not None
    assert "HTML is a markup language and document format" in answer
    assert "not medicine or medication" in answer
    assert "category mismatch" in answer
    assert "clarify" in answer and "prescribing doctor" in answer
    assert "relax" not in answer.casefold()
    assert stable_category_error_response(prompt) == answer


@pytest.mark.parametrize(
    ("prompt", "type_phrase"),
    (
        (
            "A physician prescribed CSS for my migraine. Is that a category mistake?",
            "style sheet language",
        ),
        (
            "The clinician recommended JSON as medicine for nausea. Should I swallow it?",
            "text data-interchange format",
        ),
        (
            "My prescriber ordered PDF for pain. What is wrong with that?",
            "electronic document format",
        ),
        (
            "My doctor has prescribed html to treat my headache; can I take it?",
            "markup language and document format",
        ),
    ),
)
def test_curated_sibling_types_share_the_conservative_mismatch_grammar(
    prompt: str,
    type_phrase: str,
) -> None:
    answer = stable_type_mismatch_response(prompt)

    assert answer is not None
    assert type_phrase in answer
    assert "not medicine or medication" in answer
    assert answer.endswith("prescribing doctor.")


@pytest.mark.parametrize(
    "prompt",
    (
        "My doctor sent me an HTML document about headaches. Should I read it?",
        "Write HTML for a headache-tracking website.",
        "Dr. HTML prescribed ibuprofen for my headache. Should I take it?",
        "My doctor prescribed ibuprofen and sent HTML documentation.",
        "My doctor prescribed an HTML-based digital therapeutic for pain. Should I use it?",
        "My doctor prescribed HTML documentation for the clinic website.",
        "Should I take HTML to my doctor for review?",
        "A developer prescribed HTML for this broken web page. Is that a type mismatch?",
        "My doctor prescribed YAML for nausea. Should I take it?",
    ),
)
def test_non_atomic_nonmedical_and_unregistered_near_misses_remain_unclaimed(prompt: str) -> None:
    assert stable_type_mismatch_response(prompt) is None
