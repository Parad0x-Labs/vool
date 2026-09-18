from __future__ import annotations

from types import SimpleNamespace

from core.research_evidence import truthful_evidence_strength


def test_strong_evidence_label_is_demoted_when_the_observed_set_is_weak() -> None:
    result = SimpleNamespace(
        evidence_strength="strong_evidence",
        notes=[{"result_url": "https://example.test/source"}],
        source_domains=["example.test"],
    )

    assert truthful_evidence_strength(result) == "weak"


def test_strong_evidence_requires_multiple_notes_and_domains() -> None:
    result = SimpleNamespace(
        evidence_strength="strong_evidence",
        notes=[
            {"result_url": "https://one.example.test/a"},
            {"result_url": "https://two.example.test/b"},
            {"result_url": "https://three.example.test/c"},
        ],
        source_domains=["one.example.test", "two.example.test"],
    )

    assert truthful_evidence_strength(result) == "strong"
