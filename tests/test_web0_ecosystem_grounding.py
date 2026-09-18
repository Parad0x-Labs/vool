from __future__ import annotations

from core.canonical_project_knowledge import (
    canonical_context_items,
    retrieve_canonical_passages,
)


def test_each_supported_ecosystem_topic_retrieves_canonical_sources() -> None:
    for question in (
        "what is web0",
        "who makes web0",
        "explain the dark null protocol",
        "what is nulla-local",  # legacy spelling: exercises the legacy-name normalizer
        "how does x402 work",
        "what is dna-x402",
        "how do i register a name.null",
    ):
        passages = retrieve_canonical_passages(question)
        assert passages, question
        assert all(passage.source_path for passage in passages)
        assert all(len(passage.content_hash) == 64 for passage in passages)


def test_context_items_carry_scope_and_provenance() -> None:
    items = canonical_context_items("how does x402 work")

    assert items
    for item in items:
        assert item.source_type == "canonical_document"
        assert item.metadata["scope"] == "project"
        assert item.metadata["source_class"] == "canonical"
        assert item.metadata["status"] == "active"
        assert item.metadata["source_path"]
        assert item.metadata["content_hash"]
        assert item.provenance["content_hash"] == item.metadata["content_hash"]


def test_unrelated_topics_inject_no_grounding() -> None:
    assert retrieve_canonical_passages("what is the capital of france") == ()


def test_vool_identity_is_canonically_grounded() -> None:
    passages = retrieve_canonical_passages("what is VOOL?")
    assert passages
    assert any(item.source_path == "README.md" for item in passages)
