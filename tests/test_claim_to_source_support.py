"""Claim-to-source support: one shared word grounds nothing, and claims stand alone.

The defect this closes, measured on a2308a26 (browser drive, committed at
`validation-logs/live-search-ui-proof-20260901/B_r1_open_defect_result.json`): a turn asking for
recent Rust news retrieved three real, dated rows and shipped four invented headlines, and
`inspect_evidence_binding` said ``grounded=True`` because a fabricated headline and a real one both
contained "released". One boolean, satisfied by one generic verb, laundered every unsupported claim
in the reply at once.

The repair is per-claim, per-source support (`core.claim_support.match_claims`), composed into
`inspect_evidence_binding`: the answer is segmented into independently testable claims, each claim
is matched only against bound source CONTENT, support requires structure appropriate to the claim
(value/version/date, quoted span, named entity, or two distinctive terms for anchor-less prose),
and ``grounded`` is True only when every claim is covered. Receipts are not sources; a supported
claim cannot ground a fabricated sibling; uncertainty reads as unsupported/uncertain, never as
guessed support.

Every fixture here was RED at a2308a26 (module absent / verdict inverted) except the
negative controls, which pin behaviour that must not change.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

from core.claim_support import (
    GENERIC_SUPPORT_TERMS,
    match_claims,
    segment_claims,
)
from core.evidence_binding import inspect_evidence_binding

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures" / "claim_support_incident"

RUST_REQUEST = "show me recent news coverage about the Rust programming language"

RUST_NOTES = [
    {
        "summary": "Phoronix | 2026-08-31 | Rust Coreutils 0.11 Released With Debug Helper Messages",
        "result_title": "Rust Coreutils 0.11 Released",
        "result_url": "https://www.phoronix.com/news/rust-coreutils-0-11",
        "origin_domain": "phoronix.com",
        "search_provider": "google_news_rss",
        "source_type": "web_derived",
    },
    {
        "summary": "InfoWorld | 2026-08-28 | Rust language adds algebraic floating-point methods",
        "result_title": "Rust adds algebraic floating-point methods",
        "result_url": "https://www.infoworld.com/article/rust-algebraic-floats",
        "origin_domain": "infoworld.com",
        "search_provider": "google_news_rss",
        "source_type": "web_derived",
    },
]


# ---------------------------------------------------------------------------------------------
# 1 -- one incidental generic word grounds nothing
# ---------------------------------------------------------------------------------------------


def test_a_music_album_note_does_not_support_a_software_claim() -> None:
    """The shared word is "released"; the source is about an album, the claim about a compiler."""

    binding = inspect_evidence_binding(
        answer="Rust 1.64 was released with improved performance and safety features.",
        notes=[
            {
                "summary": "Pitchfork | 2026-08-30 | Indie band Glasshouse released their third studio album",
                "result_title": "Glasshouse released third album",
                "result_url": "https://pitchfork.com/glasshouse",
                "origin_domain": "pitchfork.com",
                "source_type": "web_derived",
            }
        ],
        request_text="show me recent news coverage about Rust",
    )

    assert "released" in binding.shared_terms, "the lure must actually be present"
    assert binding.grounded is False, binding
    assert binding.claim_support is not None
    assert binding.claim_support.coverage in {"none", "partial"}
    assert not binding.claim_support.supported_claims


def test_a_generic_word_alone_never_supports_even_in_title_case() -> None:
    """Title-case fabrications capitalize "Released"; the generic-word filter is what stops the
    capitalized form from being read as a named entity and matched against a real headline."""

    binding = inspect_evidence_binding(
        answer="Major Rust Update Released - TechCrunch",
        notes=RUST_NOTES,
        request_text=RUST_REQUEST,
    )

    assert binding.grounded is False, binding
    assert not binding.claim_support.supported_claims
    assert "released" in GENERIC_SUPPORT_TERMS, "the filter this test exercises"


# ---------------------------------------------------------------------------------------------
# 2 -- the committed fabrication fixture, replayed from the repository
# ---------------------------------------------------------------------------------------------


def _committed_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _notes_from_committed_news_rows(committed_answer: str) -> list[dict]:
    """Rebuild retrieval notes from the committed real rows: `date | [domain](url): title`."""

    notes: list[dict] = []
    for line in committed_answer.splitlines():
        match = re.match(
            r"^-\s+(\d{4}-\d{2}-\d{2})\s+\|\s+\[([^\]]+)\]\(([^)]+)\):\s+(.+)$", line.strip()
        )
        if not match:
            continue
        day, domain, url, title = match.groups()
        notes.append(
            {
                "summary": f"{domain} | {day} | {title}",
                "result_title": title,
                "result_url": url,
                "origin_domain": domain,
                "search_provider": "google_news_rss",
                "source_type": "web_derived",
            }
        )
    return notes


def test_the_committed_rust_fabrication_fixture_is_unsupported() -> None:
    """B_r1_open_defect: the served fabricated headlines, replayed against the real retrieval rows
    committed by the same browser drive (B_news1). Every fabricated claim must lack support."""

    defect = _committed_fixture("B_r1_open_defect_result.json")
    news = _committed_fixture("B_news1_result.json")
    notes = _notes_from_committed_news_rows(str(news["committed_answer"]))
    assert len(notes) == 3, "the committed drive recorded three real rows"

    binding = inspect_evidence_binding(
        answer=str(defect["committed_answer"]),
        notes=notes,
        request_text=str(defect["question"]),
    )

    assert binding.has_evidence is True
    assert binding.grounded is False, binding
    assert not binding.claim_support.supported_claims
    assert binding.claim_support.unsupported_claims, "the fabricated headlines must be named"
    assert binding.evidence_discarded is True


def test_the_committed_real_answer_is_fully_supported_by_its_own_rows() -> None:
    """Control on the same committed drive: the fast-path answer that actually rendered the
    retrieved rows must read as grounded, claim by claim."""

    news = _committed_fixture("B_news1_result.json")
    notes = _notes_from_committed_news_rows(str(news["committed_answer"]))

    binding = inspect_evidence_binding(
        answer=str(news["committed_answer"]),
        notes=notes,
        request_text=str(news["question"]),
    )

    assert binding.grounded is True, binding.claim_support.as_dict()
    assert binding.claim_support.coverage == "full"
    assert not binding.claim_support.unsupported_claims


def test_the_production_caller_passes_exactly_this_seam() -> None:
    """Deterministic wiring check: `turn_reasoning` calls inspect_evidence_binding with the final
    response, the turn's web notes and the effective input -- the kwargs replayed above. (Driving
    the full `run_once` would collide with the retrieval-ordering lane that owns that file; this
    pins the seam shape so the replay above and production stay the same call.)"""

    source = (REPO / "core" / "agent_runtime" / "turn_reasoning.py").read_text(encoding="utf-8")
    call = next(
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", getattr(node.func, "attr", "")) == "inspect_evidence_binding"
    )
    keywords = {kw.arg for kw in call.keywords}

    assert {"answer", "notes", "request_text"} <= keywords, keywords


# ---------------------------------------------------------------------------------------------
# 3 -- per-claim isolation: a supported claim cannot ground a fabricated sibling
# ---------------------------------------------------------------------------------------------


def test_one_supported_plus_one_fabricated_identifies_exactly_the_fabrication() -> None:
    binding = inspect_evidence_binding(
        answer=(
            "Rust Coreutils 0.11 was released with debug helper messages (Phoronix). "
            "Also, Rust 2.0 shipped today with a garbage collector."
        ),
        notes=RUST_NOTES,
        request_text="recent Rust news",
    )

    support = binding.claim_support
    assert support.coverage == "partial", support.as_dict()
    assert binding.grounded is False

    supported = support.supported_claims
    unsupported = support.unsupported_claims
    assert len(supported) == 1 and "0.11" in supported[0].text
    assert supported[0].supporting_sources == ("note-1",)
    assert len(unsupported) == 1 and "2.0" in unsupported[0].text
    assert not unsupported[0].supporting_sources


def test_two_claims_map_independently_to_their_two_sources() -> None:
    binding = inspect_evidence_binding(
        answer=(
            "Coreutils 0.11 added debug helper messages, and the language gained "
            "algebraic floating-point methods."
        ),
        notes=RUST_NOTES,
        request_text="recent Rust news",
    )

    support = binding.claim_support
    assert support.coverage == "full", support.as_dict()
    assert binding.grounded is True
    by_text = {claim.text: claim.supporting_sources for claim in support.supported_claims}
    assert len(by_text) == 2
    coreutils_claim = next(text for text in by_text if "0.11" in text)
    floats_claim = next(text for text in by_text if "algebraic" in text)
    assert by_text[coreutils_claim] == ("note-1",)
    assert by_text[floats_claim] == ("note-2",)


# ---------------------------------------------------------------------------------------------
# 4 -- structural mismatches fail
# ---------------------------------------------------------------------------------------------

PRICE_NOTE = [
    {
        "summary": "CoinDesk | 2026-09-01 | Bitcoin hovers near $64,000 as ETF inflows resume",
        "result_title": "Bitcoin hovers near $64,000",
        "result_url": "https://www.coindesk.com/markets/btc",
        "origin_domain": "coindesk.com",
        "source_type": "web_derived",
    }
]


def test_a_price_mismatch_is_unsupported() -> None:
    binding = inspect_evidence_binding(
        answer="Bitcoin is trading at $35,200.",
        notes=PRICE_NOTE,
        request_text="what is the price of bitcoin",
    )

    assert binding.grounded is False
    claim = binding.claim_support.unsupported_claims[0]
    assert "value_mismatch" in claim.reasons, claim


def test_a_matching_value_on_the_wrong_entity_is_unsupported() -> None:
    """$64,000 IS in the source -- but the claim pins it to Ethereum, which the source never
    mentions. A coinciding number on a foreign entity is not support."""

    binding = inspect_evidence_binding(
        answer="Ethereum is trading at $64,000.",
        notes=PRICE_NOTE,
        request_text="what is the price of bitcoin",
    )

    assert binding.grounded is False
    claim = binding.claim_support.unsupported_claims[0]
    assert "entity_absent_from_source" in claim.reasons, claim


def test_the_asked_entity_with_the_sourced_value_is_supported() -> None:
    """Control: restating the asked entity with the value the source carries passes."""

    binding = inspect_evidence_binding(
        answer="Bitcoin is trading near $64,000.",
        notes=PRICE_NOTE,
        request_text="what is the price of bitcoin",
    )

    assert binding.grounded is True, binding.claim_support.as_dict()


# ---------------------------------------------------------------------------------------------
# 5 -- currency: a stale source cannot witness "today"
# ---------------------------------------------------------------------------------------------

STALE_NOTE = [
    {
        "summary": "TechDaily | 2024-05-02 | Zephyr shipped release 2.1 alongside developer tools",
        "result_title": "Zephyr shipped release 2.1",
        "origin_domain": "techdaily.invalid",
        "source_type": "web_derived",
    }
]


def test_a_matching_entity_with_a_stale_date_fails_a_current_claim() -> None:
    binding = inspect_evidence_binding(
        answer="Zephyr shipped a new release today.",
        notes=STALE_NOTE,
        request_text="what's the latest on that RTOS",
        as_of="2026-09-01",
    )

    assert binding.grounded is False
    claim = binding.claim_support.unsupported_claims[0]
    assert "stale_source_for_current_claim" in claim.reasons, claim


def test_the_same_claim_over_a_fresh_source_is_supported() -> None:
    fresh = [dict(STALE_NOTE[0], summary=STALE_NOTE[0]["summary"].replace("2024-05-02", "2026-08-30"))]
    binding = inspect_evidence_binding(
        answer="Zephyr shipped a new release today.",
        notes=fresh,
        request_text="what's the latest on that RTOS",
        as_of="2026-09-01",
    )

    assert binding.grounded is True, binding.claim_support.as_dict()


# ---------------------------------------------------------------------------------------------
# 6 -- quotes and paraphrases
# ---------------------------------------------------------------------------------------------


def test_an_exact_quoted_source_fact_passes() -> None:
    binding = inspect_evidence_binding(
        answer='Phoronix reports "Rust Coreutils 0.11 Released With Debug Helper Messages".',
        notes=RUST_NOTES,
        request_text="recent Rust news",
    )

    assert binding.grounded is True
    claim = binding.claim_support.supported_claims[0]
    assert "quote_match" in claim.reasons or "value_match" in claim.reasons, claim
    assert claim.supporting_sources == ("note-1",)


def test_a_paraphrased_but_structurally_equivalent_fact_passes() -> None:
    binding = inspect_evidence_binding(
        answer="The Coreutils project put out version 0.11, which ships handy debugging helpers.",
        notes=RUST_NOTES,
        request_text="recent Rust news",
    )

    assert binding.grounded is True, binding.claim_support.as_dict()
    assert binding.claim_support.supported_claims[0].supporting_sources == ("note-1",)


def test_an_invented_quote_is_unsupported() -> None:
    binding = inspect_evidence_binding(
        answer='TechCrunch reports "Rust 1.64 Brings Performance Improvements and Stability Enhancements".',
        notes=RUST_NOTES,
        request_text="recent Rust news",
    )

    assert binding.grounded is False
    assert not binding.claim_support.supported_claims


# ---------------------------------------------------------------------------------------------
# 7 -- vacuous answers and receipt-only turns
# ---------------------------------------------------------------------------------------------


def test_an_empty_answer_over_real_evidence_is_not_grounded() -> None:
    binding = inspect_evidence_binding(answer="", notes=RUST_NOTES, request_text=RUST_REQUEST)

    assert binding.grounded is False
    assert binding.claim_support.coverage == "no_claims"
    assert binding.evidence_discarded is True


def test_a_contentless_pleasantry_over_real_evidence_is_not_grounded() -> None:
    binding = inspect_evidence_binding(
        answer="Sure! Happy to help.", notes=RUST_NOTES, request_text=RUST_REQUEST
    )

    assert binding.grounded is False
    assert not binding.claim_support.supported_claims


def test_a_receipt_with_zero_bound_notes_supports_nothing() -> None:
    """A successful retrieval receipt is provenance, not content. With no content-bearing bound
    notes, nothing can be supported -- whatever the receipt says."""

    receipt_shaped_notes = [
        {
            "schema": "vool.web_retrieval_receipt.v1",
            "status": "available",
            "lifecycle": "succeeded",
            "source_count": 3,
            "search_provider": "google_news_rss",
            "result_url": "https://news.google.com/rss",
            "origin_domain": "news.google.com",
        }
    ]
    binding = inspect_evidence_binding(
        answer="Rust Coreutils 0.11 was released this week.",
        notes=receipt_shaped_notes,
        request_text=RUST_REQUEST,
    )

    assert binding.claim_support.coverage == "no_sources"
    assert binding.claim_support.source_ids == ()
    assert not binding.claim_support.supported_claims
    assert binding.grounded is False
    # No content means nothing is provable either way -- the discard verdict must not fire.
    assert binding.has_evidence is False
    assert binding.evidence_discarded is False


# ---------------------------------------------------------------------------------------------
# 8 -- the verdict the runtime records
# ---------------------------------------------------------------------------------------------


def test_the_recorded_payload_carries_the_per_claim_map() -> None:
    """`turn_reasoning` stamps `as_dict()` into the returned payload; the trace must carry the
    per-claim map, not only the boolean it replaces."""

    payload = inspect_evidence_binding(
        answer=(
            "Rust Coreutils 0.11 was released with debug helper messages. "
            "Also, Rust 2.0 shipped today with a garbage collector."
        ),
        notes=RUST_NOTES,
        request_text="recent Rust news",
    ).as_dict()

    assert payload["coverage"] == "partial"
    support = payload["claim_support"]
    assert support["supported_claim_ids"] and support["unsupported_claim_ids"]
    assert set(support["source_ids"]) == {"note-1", "note-2"}
    claims = {claim["claim_id"]: claim for claim in support["claims"]}
    for claim_id in support["unsupported_claim_ids"]:
        assert claims[claim_id]["supporting_sources"] == []


def test_the_map_is_deterministic() -> None:
    kwargs = dict(
        answer=(
            "Coreutils 0.11 added debug helper messages, and the language gained "
            "algebraic floating-point methods."
        ),
        notes=RUST_NOTES,
        request_text="recent Rust news",
    )

    first = inspect_evidence_binding(**kwargs).as_dict()
    second = inspect_evidence_binding(**kwargs).as_dict()

    assert first == second


# ---------------------------------------------------------------------------------------------
# 9 -- segmentation contracts the rules above depend on
# ---------------------------------------------------------------------------------------------


def test_numbered_headline_lines_are_separate_claims() -> None:
    segments = segment_claims(
        "Sure! Here are some recent headlines:\n\n"
        '1. "Rust 1.64 Released" - TechCrunch\n'
        '2. "Firefox Uses Rust" - Hacker News'
    )

    # The lead-in line ends with a colon: it introduces content rather than asserting it, and the
    # greeting rides the same line, so both drop. Only the two headline rows remain testable.
    assert len(segments) == 2
    assert "TechCrunch" in segments[0] and "Hacker News" in segments[1]


def test_coordinated_clauses_split_into_independent_claims() -> None:
    segments = segment_claims("Coreutils 0.11 shipped, and the language gained float methods.")

    assert len(segments) == 2


def test_the_support_map_is_computable_without_the_binding_wrapper() -> None:
    """`match_claims` is the swappable unit: callable on its own, no EvidenceBinding required."""

    support = match_claims(
        answer="Rust Coreutils 0.11 was released with debug helper messages.",
        notes=RUST_NOTES,
        request_text="recent Rust news",
    )

    assert support.coverage == "full"
    assert support.supported_claims[0].supporting_sources == ("note-1",)
