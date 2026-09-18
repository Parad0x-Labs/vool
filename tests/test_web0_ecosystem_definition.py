from __future__ import annotations

from core.canonical_project_knowledge import (
    retrieve_canonical_passages,
)
from core.web0_project_grounding import web0_null_project_response


def test_ecosystem_definitions_use_canonical_retrieval() -> None:
    for q in (
        "what is web0?",
        "who makes web0?",
        "what is the dark null protocol?",
        "what is vool-local?",
        "how does x402 work?",
    ):
        assert web0_null_project_response(q) is None, q
        passages = retrieve_canonical_passages(q)
        assert passages, q
        assert all(passage.source_path for passage in passages)
        assert all(passage.content_hash for passage in passages)


def test_vool_product_identity_uses_canonical_sources() -> None:
    assert web0_null_project_response("What is VOOL?") is None
    assert retrieve_canonical_passages("What is VOOL?")


def test_reading_comprehension_not_hijacked() -> None:
    # A passage that merely mentions .null must be read, not answered with a canned .null blurb.
    passage = (
        'Read this passage and answer. ".null resolution is keyless. A name is hashed '
        'deterministically to an on-chain PDA." Does resolving a name require an API key?'
    )
    assert web0_null_project_response(passage) is None


def test_specific_null_name_is_not_a_definition() -> None:
    assert web0_null_project_response("who owns web0.null?") is None


def test_registration_still_wins_over_definition() -> None:
    r = web0_null_project_response("how do i buy coolname.null?")
    assert r is not None


def test_null_dns_comparison_uses_canonical_sources() -> None:
    query = "Is .null normal DNS? One sentence."
    assert web0_null_project_response(query) is None
    assert retrieve_canonical_passages(query)


def test_null_definitional_forms_use_canonical_sources() -> None:
    for q in ("what is a .null name?", "is .null a domain?", "is .null a TLD?", "what is .null"):
        assert web0_null_project_response(q) is None, q
        assert retrieve_canonical_passages(q), q


def test_specific_null_name_is_not_the_null_concept() -> None:
    # a specific foo.null is a resolve/ownership query, never the generic .null concept blurb
    r = web0_null_project_response("is alice.null mine?")
    assert r is None or r.get("intent") != "web0_null_concept_definition"


# The .null responder must not hijack active-mission recall when a .null domain merely appears as
# one of the mission fields.

def test_mission_recall_with_null_domain_defers_to_mission_recall() -> None:
    # previously returned .null registration guidance; a mission-recall query must win.
    r = web0_null_project_response(
        "Remember current mission: cap 0.037 SOL, domain alice.null, wallet prefix F6Fr2, "
        "Windows only. What is the current mission?"
    )
    assert r is None


def test_active_mission_fields_with_null_domain_defer() -> None:
    r = web0_null_project_response(
        "Active mission: cap 0.05 SOL, domain alice.null, wallet prefix F6Fr2, Windows only. "
        "Summarize the current mission."
    )
    assert r is None


def test_plain_null_registration_and_terminology_still_use_the_null_responder() -> None:
    # a plain "how do I register alice.null?" is still the .null registration workflow
    reg = web0_null_project_response("how do I register alice.null?")
    assert reg is not None and reg["intent"] == "web0_null_named_registration_workflow"
    # Concept wording is model-generated from canonical repository passages.
    query = "In the VOOL/Web0 context, what is a .null name? One sentence."
    assert web0_null_project_response(query) is None
    assert retrieve_canonical_passages(query)


def test_named_null_recall_wins_over_registration_guidance() -> None:
    result = web0_null_project_response(
        "Register alice.null as my project domain, noted. What domain did I ask for?"
    )

    assert result is not None
    assert result["intent"] == "web0_null_name_recall"
    assert "alice.null" in result["response"]
    assert "availability" not in result["response"].lower()


def test_null_name_definition_is_not_a_fixed_answer() -> None:
    query = "In the VOOL/Web0 context, what is a .null name? One sentence."
    assert web0_null_project_response(query) is None
    assert retrieve_canonical_passages(query)


def test_how_to_register_a_null_name_still_gets_registration_not_concept() -> None:
    # a "how do I register" how-to keeps the registration guidance, not the concise concept blurb
    r = web0_null_project_response("how do I register a .null name?")
    assert r is not None and r.get("intent") != "web0_null_concept_definition"


def test_unrelated_question_untouched() -> None:
    assert web0_null_project_response("what is the capital of france?") is None
