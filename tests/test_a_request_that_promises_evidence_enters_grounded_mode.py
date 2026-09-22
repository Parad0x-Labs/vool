"""Grounded-mode detection and the claim contract — protections (B) and (C).

Domain-general by construction. The Corolla and Prius/Passat prompts are acceptance fixtures, not
the subject: the same detection must fire for a software version comparison, a regional revenue
breakdown, or a scientific constant nobody publishes to the requested precision. A rule that only
worked for cars would be the automotive-specific fix the review explicitly ruled out.
"""
from __future__ import annotations

import pytest

from core.agent_runtime.grounded_mode import (
    AnswerMode,
    Claim,
    ClaimState,
    Evidence,
    answer_mode_for,
    citation_supports_claim,
    forbids_inference,
    grounded_mode_enabled,
    render_claims,
)

COROLLA = ("what is the most sold car model ever, corolla or golf? what engine and colour was most "
           "common, in which country did it sell most and in what year? separate verified facts "
           "from unavailable data, and do not guess")
PRIUS_PASSAT = ("which sold more, prius or passat? give verified facts with sources and do not guess "
                "the colour or the country")


# --------------------------------------------------------------------------------------------
# (B) mode selection — the fixtures, and the same shape in other domains
# --------------------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "request_text",
    [
        COROLLA,
        PRIUS_PASSAT,
        # Same shapes, no cars anywhere.
        "compare postgres and mysql adoption with sources, do not guess the install counts",
        "what is stripe's revenue broken down by region? exact figures only",
        "what is the muon g-2 anomaly value? give me the exact figure with citations",
        "which country bought the most steel in 2019 and in what year was the peak",
        "what is the most common configuration shipped? break down by market",
        "what is the current version of kubernetes",
    ],
)
def test_a_request_that_promises_evidence_or_a_breakdown_is_grounded(request_text) -> None:
    assert answer_mode_for(request_text) is AnswerMode.GROUNDED


@pytest.mark.parametrize(
    "request_text",
    [
        "why is the sky blue?",
        "explain how a diesel engine works",
        "what does TCP do",
        "write me a haiku about winter",
        "who wrote the odyssey",
        "",
    ],
)
def test_stable_explanatory_questions_stay_direct(request_text) -> None:
    """The frozen fast path must not pay for this. A mandatory evidence stage on 'why is the sky
    blue' buys latency and a browsing dependency for nothing."""
    assert answer_mode_for(request_text) is AnswerMode.DIRECT


@pytest.mark.parametrize(
    "request_text",
    ["audit this repository for security bugs", "verify every claim in that report claim-by-claim"],
)
def test_audit_shaped_requests_reach_audit_grade(request_text) -> None:
    assert answer_mode_for(request_text) is AnswerMode.AUDIT_GRADE


@pytest.mark.parametrize(
    "request_text, expected",
    [
        (COROLLA, True),
        (PRIUS_PASSAT, True),
        ("do not guess the install counts", True),
        ("no speculation please", True),
        ("Compare currencies and do not invent rates", True),
        ("Don't invent exchange rates for the conversion", True),
        ("Do not invent new architecture", False),
        ('Explain the phrase "do not invent rates"', False),
        ("what is the current version of kubernetes", False),
    ],
)
def test_no_guess_contracts_are_detected(request_text, expected) -> None:
    assert forbids_inference(request_text) is expected


# --------------------------------------------------------------------------------------------
# (C) the claim contract
# --------------------------------------------------------------------------------------------

def test_a_supported_claim_without_evidence_cannot_be_constructed() -> None:
    """The contract is enforced at construction, not trusted. A model promoting its own text to
    SUPPORTED is the Corolla failure in structured form, and it must be impossible rather than
    discouraged."""
    with pytest.raises(ValueError, match="SUPPORTED with no evidence"):
        Claim(attribute="most common colour", value="White", state=ClaimState.SUPPORTED)


def test_unavailable_renders_as_a_real_answer_not_a_blank() -> None:
    """A missing cell is a better answer than an invented one, and it is a SUCCESS not a failure."""
    out = render_claims(
        [Claim(attribute="most common engine", state=ClaimState.UNAVAILABLE)],
        allow_inference=False,
    )
    assert "No authoritative global breakdown found" in out
    assert "most common engine" in out


def test_no_guess_suppresses_inferred_claims_entirely() -> None:
    """A hedged guess is still a guess. Under 'do not guess' an inferred value must not be shown."""
    claims = [
        Claim(attribute="cumulative sales", value="over 34 million",
              state=ClaimState.SUPPORTED,
              evidence=[Evidence("e1", "https://example.test/report", "cumulative sales 34 million")]),
        Claim(attribute="most common colour", value="White", state=ClaimState.INFERRED),
    ]
    strict = render_claims(claims, allow_inference=False)
    assert "White" not in strict, "an inferred value survived a no-guess request"
    assert "Not available" in strict
    permissive = render_claims(claims, allow_inference=True)
    assert "White" in permissive and "Inferred" in permissive


def test_supported_claims_render_with_their_evidence_reference() -> None:
    out = render_claims(
        [Claim(attribute="cumulative sales", value="over 34 million", state=ClaimState.SUPPORTED,
               evidence=[Evidence("e1", "https://example.test/r", "sales")])],
        allow_inference=False,
    )
    assert "over 34 million" in out and "https://example.test/r" in out


def test_conflicting_sources_are_shown_as_conflicting_not_resolved_silently() -> None:
    out = render_claims(
        [Claim(attribute="total sold", value="34M vs 30M", state=ClaimState.CONFLICTING,
               note="manufacturer vs registry", evidence=[Evidence("e1", "https://example.test/a", "x")])],
        allow_inference=False,
    )
    assert "disagree" in out.lower() and "manufacturer vs registry" in out


# --------------------------------------------------------------------------------------------
# (D) citation entailment — entity overlap is not claim support
# --------------------------------------------------------------------------------------------

def test_a_source_about_totals_does_not_support_a_country_breakdown() -> None:
    """The exact Corolla failure: a page mentioning the nameplate was treated as backing a
    top-country claim it never makes."""
    claim = Claim(attribute="top selling country", state=ClaimState.UNAVAILABLE)
    evidence = Evidence("e1", "https://example.test/sales",
                        "Toyota reported more than 50 million cumulative Corolla sales worldwide.")
    assert citation_supports_claim(claim, evidence) is False


def test_a_source_that_speaks_to_the_attribute_does_support_it() -> None:
    claim = Claim(attribute="cumulative sales", state=ClaimState.UNAVAILABLE)
    evidence = Evidence("e1", "https://example.test/sales",
                        "Cumulative sales passed 50 million units in 2021.")
    assert citation_supports_claim(claim, evidence) is True


def test_an_empty_passage_never_supports_anything() -> None:
    assert citation_supports_claim(Claim(attribute="x"), Evidence("e1", "https://example.test/")) is False


# --------------------------------------------------------------------------------------------
# The flag
# --------------------------------------------------------------------------------------------

def test_the_feature_is_off_unless_the_flag_is_set(monkeypatch) -> None:
    monkeypatch.delenv("VOOL_GROUNDED_MODE", raising=False)
    assert grounded_mode_enabled() is False
    monkeypatch.setenv("VOOL_GROUNDED_MODE", "1")
    assert grounded_mode_enabled() is True
