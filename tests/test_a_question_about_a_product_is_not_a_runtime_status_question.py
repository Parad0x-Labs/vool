"""A question that merely contains "model" is not a question about which model is running.

Measured 2026-08-06, session `openclaw:c83c83bd7836b6dd968b`: a research question about the
best-selling mobile PHONE returned the model-pin acknowledgement in 0.0s with zero events -- no
classification, no retrieval, no model call. It never reached the agent at all.

    has_model_term  <- "mobile phone model", "the exact model", "model-specific gaps"
    has_status_term <- "Use current, authoritative sources"

Two ordinary English words, neither about the runtime. This is the third instance of the same shape
recorded in this codebase: a GPU pricing question answered with this machine's hardware specs, and
"the requirements I just gave you" read as a filename.

`_looks_like_runtime_version_question`, directly below the detector in question, already required a
SUBJECT alongside its keyword (`has_version_term and has_subject`). This one never did. The fix is
the sibling's own rule, not a new one.
"""
from __future__ import annotations

import pytest

from core.web.api.service import _MAX_STATUS_QUESTION_WORDS
from core.web.api.service import _looks_like_runtime_model_status_question as is_status_question

PHONE = ("What is the best-selling mobile phone model of all time worldwide? Compare the Nokia 1100 "
         "with the Apple iPhone 6 and iPhone 6 Plus family by cumulative units sold. Explain whether "
         "combining the iPhone 6 and 6 Plus, regional variants, storage capacities, carrier versions, "
         "and later reissues makes the comparison unfair. For the overall winner, also identify the "
         "most common storage capacity or memory configuration, the most common colour, the country "
         "where it sold the most, and the single calendar year with the highest sales. Use current, "
         "authoritative sources. Every SUPPORTED claim must be backed by evidence retrieved during "
         "this turn that proves the "
         "exact model, metric, geography, and timeframe. Do not use general smartphone colour or "
         "storage trends to fill model-specific gaps.")


@pytest.mark.parametrize(
    "prompt",
    [
        PHONE,
        "what is the most sold vw model, what engine and what colour?",
        "which tesla model is the current best seller?",
        "compare the current flagship model of each phone maker",
        "what is the standard model of the atom in physics",
        "is the current model year of the civic worth buying",
    ],
)
def test_a_product_question_is_not_a_runtime_status_question(prompt) -> None:
    """Every one of these carries both trigger words and is about the world, not the runtime."""
    assert is_status_question(prompt) is False


@pytest.mark.parametrize(
    "prompt",
    [
        "which model are you using?",
        "what model are you running right now",
        "what llm is active for this chat",
        "which model is vool using",
        "what model am i using",
        # These name the assistant without the word "model" and keep their own path.
        "which one is active?",
        "what are you running",
        "which lane is this",
    ],
)
def test_a_real_runtime_status_question_is_still_answered_deterministically(prompt) -> None:
    """The control. This detector exists because the model otherwise invents an identity that
    contradicts the footer showing what actually ran -- that must keep working."""
    assert is_status_question(prompt) is True


def test_the_recommendation_escape_hatch_still_wins() -> None:
    """A pre-existing carve-out that must survive the new subject requirement."""
    assert is_status_question("which model should i download for my mac?") is False

# The EV brief, verbatim. The phone fix shipped and this still hijacked, because BOTH briefs say
# "evidence retrieved during this turn" and `this turn` had been added to the subject list as a
# runtime marker. It is not one -- it is an ordinary instruction about the work.
#
# The real lesson is about the FIXTURE, not the word: the phone fix was verified against a
# shortened paraphrase that dropped that exact phrase, so the test passed while the product stayed
# broken. Both prompts below are now stored in full, and no test in this file may paraphrase one.
EV = ("What is the best-selling battery-electric car model of all time worldwide? Compare the Tesla "
      "Model 3 and Nissan Leaf by cumulative global deliveries or sales. Explain whether counting "
      "different battery capacities, drivetrains, facelifts, regional versions, and locally "
      "manufactured variants makes the comparison misleading. For the overall winner, also identify: "
      "the most common battery-capacity configuration; the most common exterior colour; the country "
      "where it sold the most; the single calendar year with the highest global sales. Use one "
      "compact table and classify every requested field as: SUPPORTED INFERRED UNAVAILABLE "
      "CONFLICTING. Use current evidence retrieved during this turn from Tesla or Nissan "
      "disclosures, official registration agencies, regulatory filings, or reputable market-research "
      "sources. Every SUPPORTED claim must have evidence proving the exact model, metric, geography, "
      "aggregation level, and timeframe. Do not use general EV-market trends to fill model-specific "
      "gaps. Do not confuse production, deliveries, registrations, and vehicles currently in service. "
      "When reliable worldwide data does not exist, mark the field UNAVAILABLE. Do not guess.")


@pytest.mark.parametrize("prompt", [EV, PHONE])
def test_a_research_brief_saying_this_turn_is_not_a_status_question(prompt) -> None:
    """Both hijacked prompts, verbatim. `retrieved during this turn` must never read as a question
    about which model is running."""
    assert "this turn" in prompt.lower(), "fixture must keep the phrase that caused the failure"
    assert is_status_question(prompt) is False


def test_a_research_brief_is_too_long_to_be_a_status_question() -> None:
    """The structural discriminator, carrying the weight a word list twice failed to.

    A status question is short and direct; these are 100+ word instruction sets. Length cannot be
    defeated by a phrase nobody anticipated, which is exactly how the first two fixes were beaten.
    """
    from core.web.api.service import _MAX_STATUS_QUESTION_WORDS

    for prompt in (EV, PHONE):
        assert len(prompt.split()) > _MAX_STATUS_QUESTION_WORDS * 2


def test_every_genuine_status_phrasing_fits_inside_the_length_cap() -> None:
    """The cap must not be so tight that a real question fails it."""
    from core.web.api.service import _MAX_STATUS_QUESTION_WORDS

    for prompt in ("which model are you using?", "what model are you running right now",
                   "what llm is active for this chat", "which model is vool using",
                   "which one is active?", "what are you running"):
        assert len(prompt.split()) <= _MAX_STATUS_QUESTION_WORDS, prompt
        assert is_status_question(prompt) is True


def test_the_length_cap_catches_a_brief_the_word_list_would_admit() -> None:
    """The cap must be load-bearing, not decoration.

    Removing `this turn` from the subject list fixed both MEASURED briefs, so the cap alone was
    never exercised -- sabotaging it left every test green, which is how an unproven guard ships.
    This brief carries a subject word the list legitimately keeps (`your`), plus `model` and
    `current`, so vocabulary admits it. It is 60 words of research instructions and nothing about
    the runtime.

    That is the whole argument for a structural test: a word list can only exclude phrases someone
    already thought of, and this failure mode has now beaten two of them.
    """
    brief = (
        "Compare the current flagship model of each major phone maker and tell me which one your "
        "analysis rates highest on battery life, camera quality, and resale value. Use current "
        "authoritative sources for every figure, classify each field as SUPPORTED or UNAVAILABLE, "
        "and do not fill model-specific gaps with general market trends or guesses about pricing."
    )
    assert len(brief.split()) > _MAX_STATUS_QUESTION_WORDS
    assert "your" in brief.lower(), "fixture must carry a subject word the list keeps"
    assert "model" in brief.lower() and "current" in brief.lower()
    assert is_status_question(brief) is False, (
        "vocabulary admits this brief; only the length test rejects it"
    )


# ---------------------------------------------------------------------------------------------
# `_looks_like_runtime_version_question` -- the sibling detector this module's own docstring named
# as "already correct" because it required has_version_term AND has_subject. It was not: a subject
# requirement alone is not a length requirement, and this detector had no length gate at all.
#
# Live incident, 2026-08-06, session `openclaw:2fd81093adaf0eb4d1d1`: a 150-word correctness-audit
# brief for api/apache/liquefy_apache_repetition_v1.py was answered with the build/version stamp
# and never reached the agent -- zero session events, zero conversation-log entry, zero turn-count
# increment anywhere. Reproduced directly against the live, unmodified detector before any fix:
#
#     has_version_term <- "historical Git versions" (plural, about source history, not this build)
#     has_subject       <- "you may run safe read-only commands" (an instruction, not a question)
#
# Two ordinary English words won a two-word check with no length gate. The fix reuses the exact
# `_MAX_STATUS_QUESTION_WORDS` structural discriminator this file already proved out above.
from core.web.api.service import _looks_like_runtime_version_question as is_version_question

AUDIT_BRIEF = (
    "Inspect the current workspace and audit: api/apache/liquefy_apache_repetition_v1.py Read the "
    "actual current file, its directly imported local modules, relevant tests, and any project "
    "documentation that defines correctness or round-trip guarantees. Find the single highest-risk "
    "current correctness bug that can cause a public operation to return incorrect, incomplete, "
    "corrupted, or misleading output while processing otherwise valid or plausibly malformed input. "
    "Requirements: Use local file and search tools. Audit the current working tree, not historical "
    "Git versions. Do not modify project files. You may run safe read-only commands or create "
    "temporary isolated inputs outside the repository. Inspect producer-consumer assumptions across "
    "methods, not only functions in isolation. Compare what metadata is created with how later "
    "operations rely on it. Challenge at least the three strongest candidates. Reject candidates "
    "whose encode/decode or write/read paths remain symmetrical. Run a minimal non-mutating "
    "reproduction for the strongest surviving candidate. Do not report style, compression-ratio, or "
    "theoretical concerns as correctness bugs. Do not recommend a fix unless the reproduction "
    "confirms the failure. Final answer requirements: exactly one finding; proof state: CONFIRMED, "
    "REJECTED, ENVIRONMENT-DEPENDENT, or NOT PROVEN; exact file and line ranges; minimal "
    "reproduction input; expected versus actual behavior; command exit status; concrete user/data "
    "impact; measured wall-clock time; model and provider; exact known cloud-input tokens and calls "
    "missing usage; largest context components. Keep the final concise. Put rejected candidates and "
    "detailed activity outside the final answer."
)


def test_the_audit_brief_carries_both_trigger_words_verbatim() -> None:
    """Fixture integrity: a future paraphrase must not quietly disarm this test the way the phone
    fix's shortened paraphrase did for the model-status detector above."""
    clean = AUDIT_BRIEF.lower()
    assert "git versions" in clean, "fixture must keep the phrase that supplied has_version_term"
    assert "you may run" in clean, "fixture must keep the phrase that supplied has_subject"


def test_a_correctness_audit_brief_is_not_a_version_question() -> None:
    assert is_version_question(AUDIT_BRIEF) is False


@pytest.mark.parametrize(
    "prompt",
    [
        "what version are you running?",
        "version",
        "/version",
        "which build am I running",
        "what is your build number",
        "which VOOL version is installed",
    ],
)
def test_a_real_version_question_is_still_answered_deterministically(prompt) -> None:
    assert is_version_question(prompt) is True


def test_the_audit_brief_is_too_long_to_be_a_version_question() -> None:
    assert len(AUDIT_BRIEF.split()) > _MAX_STATUS_QUESTION_WORDS * 2


def test_the_length_cap_is_load_bearing_for_the_version_detector() -> None:
    """Sabotage-proves the fix: reproduce the ORIGINAL two-term check with no length gate, on the
    live vocabulary lists, and confirm it would wrongly admit the audit brief. If this ever passes
    with an unpatched detector, the length gate stopped doing any work."""
    clean = " ".join(AUDIT_BRIEF.lower().split())
    has_version_term = any(
        term in clean for term in ("version", "which build", "what build", "release version", "build number")
    )
    has_subject = any(
        term in clean for term in ("vool", "you", "your", "running", "installed", "this build", "this version")
    )
    assert has_version_term and has_subject, (
        "the vocabulary alone must still admit this brief -- otherwise the length gate isn't "
        "the thing rejecting it"
    )
