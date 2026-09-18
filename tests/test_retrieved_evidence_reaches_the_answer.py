"""Evidence a turn paid for must show up in the answer, or be reported as unusable.

Measured live on c6eed761 (2026-08-14), local AND cloud lanes, same turn: retrieval ran to
completion (`web_calls: 8`, one `vool.web_retrieval_receipt.v1`, both retrieval events in
Activity) and the visible answer was a promise to go and look. The turn was then stamped
`fulfillment_status: fulfilled`, `retryable: false`, so nothing retried and nothing fell back.

These tests assert the INVARIANT -- retrieved content is witnessed in the answer, or the turn is
not fulfilment -- not the sentence that exposed it. Nothing here depends on the words "let me
check", on weather, on the North Sea, or on the number 8. The later generations deliberately drop
the original vocabulary entirely: if the repair only recognises the reported phrasing, the
distant-domain cases below fail.
"""

from __future__ import annotations

import pytest

from core.evidence_binding import (
    content_terms,
    evidence_terms,
    inspect_evidence_binding,
)


def _notes(*summaries: str) -> list[dict]:
    return [
        {
            "summary": summary,
            "result_url": "https://example.invalid/a",
            "origin_domain": "example.invalid",
            "source_profile_label": "example",
        }
        for summary in summaries
    ]


# ---------------------------------------------------------------------------------------------
# G1 -- the original reproduction, as measured
# ---------------------------------------------------------------------------------------------


def test_the_original_promise_with_real_evidence_is_reported_as_discarded() -> None:
    """The exact failure: retrieval returned content, the answer only promised to fetch it."""

    binding = inspect_evidence_binding(
        answer="I can look up the current water temperature in the North Sea for you. Let me check that.",
        notes=_notes("North Sea surface temperature is currently 17.4 degrees Celsius at Dogger Bank."),
        request_text="hi, what is the temperature of water in north sea now?",
    )

    assert binding.has_evidence
    assert binding.evidence_discarded
    assert not binding.grounded


def test_the_cloud_lane_tool_call_narration_is_also_discarded_evidence() -> None:
    """The other lane's shape of the same failure: the search invocation shipped as the answer."""

    binding = inspect_evidence_binding(
        answer='Searching for current North Sea water temperature... (web search for "North Sea current water temperature August 2026")',
        notes=_notes("Buoy readings put the North Sea at 17.4 C, warmest since records began at Dogger Bank."),
        request_text="hi, what is the temperature of water in north sea now?",
    )

    assert binding.evidence_discarded


# ---------------------------------------------------------------------------------------------
# G2 -- clean paraphrases, fresh entities, no original vocabulary
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "answer",
    (
        "I'll pull that figure up for you now.",
        "Give me a moment while I retrieve the latest reading.",
        "One second, fetching that from the source.",
        "Happy to find that out — running a lookup.",
        "Let's see what the current listing says.",
        "I am going to consult the register and report back.",
    ),
)
def test_any_promise_shape_fails_when_evidence_was_retrieved(answer: str) -> None:
    """Six unrelated promise phrasings, none of them special-cased anywhere."""

    binding = inspect_evidence_binding(
        answer=answer,
        notes=_notes("The Bergen ferry terminal reports a departure backlog of 41 vehicles."),
        request_text="how long is the queue at the ferry terminal",
    )

    assert binding.evidence_discarded, binding


def test_a_distant_domain_behaves_identically() -> None:
    """No weather, no location, no live value -- a package version lookup."""

    binding = inspect_evidence_binding(
        answer="Checking the registry for you.",
        notes=_notes("cryptography 46.0.3 was published on 2026-07-02 and supersedes 45.x."),
        request_text="which release of the cryptography package is newest",
    )

    assert binding.evidence_discarded
    assert "46.0.3" in binding.introduced_terms or "46" in binding.introduced_terms


def test_a_structurally_different_surface_behaves_identically() -> None:
    """Not a question about the world at all -- a repository fact."""

    binding = inspect_evidence_binding(
        answer="Let me open the file and tell you.",
        notes=_notes("The manifest declares eleven maintainers and a Mozilla licence."),
        request_text="how many maintainers does the project list",
    )

    assert binding.evidence_discarded


# ---------------------------------------------------------------------------------------------
# G3 -- sloppy / user-style / formatting variants
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "answer",
    (
        "gimme a sec ill check",
        "SEARCHING...",
        "🔍 looking that up now",
        "ok — one moment pls",
        "**Checking…**",
        "> retrieving, stand by",
    ),
)
def test_sloppy_and_decorated_promises_are_still_discarded_evidence(answer: str) -> None:
    binding = inspect_evidence_binding(
        answer=answer,
        notes=_notes("Tromso recorded a wind speed of 23 knots from the northwest this morning."),
        request_text="whats the wind doing up north",
    )

    assert binding.evidence_discarded, binding


def test_accents_do_not_hide_grounding() -> None:
    """An answer that grounds an accented place name must count as grounded."""

    binding = inspect_evidence_binding(
        answer="Reykjavik is sitting at 10 C right now.",
        notes=_notes("Reykjavík is 10 degrees and cloudy."),
        request_text="how warm is it in the capital",
    )

    assert binding.grounded
    assert not binding.evidence_discarded


# ---------------------------------------------------------------------------------------------
# NEGATIVE CONTROLS -- the check must stay silent where it has no standing
# ---------------------------------------------------------------------------------------------


def test_a_grounded_answer_is_not_flagged() -> None:
    binding = inspect_evidence_binding(
        answer="The North Sea is about 17.4 C at the moment, measured at Dogger Bank.",
        notes=_notes("North Sea surface temperature is currently 17.4 degrees Celsius at Dogger Bank."),
        request_text="hi, what is the temperature of water in north sea now?",
    )

    assert binding.has_evidence
    assert binding.grounded
    assert not binding.evidence_discarded


def test_no_notes_means_nothing_is_provable() -> None:
    """A turn that never retrieved has no discarded evidence -- the check must fail open."""

    binding = inspect_evidence_binding(
        answer="Let me check that.",
        notes=[],
        request_text="anything at all",
    )

    assert not binding.has_evidence
    assert not binding.evidence_discarded


def test_notes_carrying_no_content_mean_nothing_is_provable() -> None:
    """Retrieval that returned only provenance and no findings proves nothing either way."""

    binding = inspect_evidence_binding(
        answer="Let me check that.",
        notes=[{"result_url": "https://example.invalid/x", "origin_domain": "example.invalid"}],
        request_text="anything at all",
    )

    assert not binding.has_evidence
    assert not binding.evidence_discarded


def test_evidence_that_only_echoes_the_request_proves_nothing() -> None:
    """Notes that introduced no term beyond the question cannot witness that they were read."""

    binding = inspect_evidence_binding(
        answer="I'll go and look.",
        notes=_notes("ferry terminal queue"),
        request_text="how long is the ferry terminal queue",
    )

    assert not binding.has_evidence
    assert not binding.evidence_discarded


def test_a_truthful_report_of_unusable_evidence_is_grounded_when_it_names_what_it_found() -> None:
    """Reporting inability while citing what came back still witnesses the evidence."""

    binding = inspect_evidence_binding(
        answer="The only source I reached was a Dogger Bank buoy page that returned no reading, so I can't give you a current figure.",
        notes=_notes("Dogger Bank buoy page returned no reading."),
        request_text="what is the water temperature now",
    )

    assert binding.grounded


# ---------------------------------------------------------------------------------------------
# ADVERSARIAL NEAR-MISSES
# ---------------------------------------------------------------------------------------------


def test_restating_the_question_is_not_grounding() -> None:
    """The load-bearing subtraction: echoing the request must not read as having read the evidence."""

    binding = inspect_evidence_binding(
        answer="You asked about the water temperature in the North Sea right now. Let me check.",
        notes=_notes("North Sea surface temperature is currently 17.4 degrees Celsius at Dogger Bank."),
        request_text="what is the temperature of water in the north sea now",
    )

    assert binding.evidence_discarded, binding.shared_terms


def test_naming_the_source_domain_is_not_grounding() -> None:
    """Saying where VOOL was about to look is not evidence that it read anything."""

    binding = inspect_evidence_binding(
        answer="I'll search example.invalid for that.",
        notes=_notes("Kattegat salinity measured 24 practical salinity units on Tuesday."),
        request_text="how salty is that strait",
    )

    assert binding.evidence_discarded


def test_a_single_introduced_number_is_enough_to_count_as_grounded() -> None:
    """The floor is deliberately low: one witnessed term passes. Quality is the model's job."""

    binding = inspect_evidence_binding(
        answer="23 knots.",
        notes=_notes("Tromso recorded a wind speed of 23 knots from the northwest this morning."),
        request_text="whats the wind doing up north",
    )

    assert binding.grounded
    assert "23" in binding.shared_terms


def test_a_short_grounded_answer_is_not_flagged() -> None:
    """`Titan.`-shaped answers are complete; brevity is not discarded evidence."""

    binding = inspect_evidence_binding(
        answer="Bergen.",
        notes=_notes("The largest backlog was recorded at Bergen this week."),
        request_text="which terminal had the worst delay",
    )

    assert binding.grounded


# ---------------------------------------------------------------------------------------------
# Term extraction contracts the rules above depend on
# ---------------------------------------------------------------------------------------------


def test_numbers_of_any_length_are_terms_but_closed_class_words_are_not() -> None:
    terms = content_terms("It is 8 degrees and the wind is at 23 knots")

    assert {"8", "23", "degrees", "wind", "knots"} <= terms
    assert not ({"it", "is", "and", "the", "at"} & terms)


def test_evidence_terms_ignore_url_and_provenance_fields() -> None:
    terms = evidence_terms(
        [
            {
                "summary": "Salinity was 24 units.",
                "result_url": "https://kattegat-observatory.invalid/readings",
                "origin_domain": "kattegat-observatory.invalid",
                "source_profile_label": "kattegat observatory",
            }
        ]
    )

    assert "salinity" in terms and "24" in terms
    assert "kattegat" not in terms, "provenance fields must not supply grounding terms"


# ---------------------------------------------------------------------------------------------
# The translation into fulfilment truth -- the seam that stamped `fulfilled` over a discarded fetch
# ---------------------------------------------------------------------------------------------


def _control(**overrides) -> dict:
    control = {
        "final_ui": {
            "answer_completeness": {
                "degenerate": False,
                "has_content": True,
                "incomplete": False,
                "reasons": [],
            }
        },
        "fallback_applied": False,
    }
    control.update(overrides)
    return control


def test_discarded_evidence_is_not_reported_as_a_fulfilled_turn() -> None:
    """The measured failure: shape said complete, so the turn claimed fulfilment over a lost fetch."""

    from core.runtime_task_outcome import output_validation_outcome

    outcome = output_validation_outcome(
        _control(evidence_binding={"has_evidence": True, "grounded": False, "evidence_discarded": True})
    )

    assert outcome is not None, "a discarded fetch must not fall through as fulfilled"
    assert outcome["fulfillment_status"] == "partially_fulfilled"
    assert outcome["retryable"] is True
    assert outcome["failure_codes"] == ["evidence_binding:retrieved_evidence_absent_from_answer"]


def test_an_explicit_claim_of_fulfilment_cannot_override_discarded_evidence() -> None:
    """A lane asserting success over evidence the answer never carried is the untruth being stopped."""

    from core.runtime_task_outcome import output_validation_outcome

    outcome = output_validation_outcome(
        _control(
            evidence_binding={"has_evidence": True, "grounded": False, "evidence_discarded": True},
            fulfillment_outcome={"fulfillment_status": "fulfilled", "retryable": False},
        )
    )

    assert outcome["fulfillment_status"] == "partially_fulfilled"


def test_an_explicit_failure_still_wins_over_the_evidence_check() -> None:
    """Negative control: a real recorded failure must not be softened into 'partial'."""

    from core.runtime_task_outcome import output_validation_outcome

    outcome = output_validation_outcome(
        _control(
            evidence_binding={"has_evidence": True, "grounded": False, "evidence_discarded": True},
            fulfillment_outcome={
                "fulfillment_status": "failed",
                "failure_stage": "provider",
                "failure_codes": ["provider_timeout"],
                "retryable": True,
            },
        )
    )

    assert outcome["fulfillment_status"] == "failed"
    assert outcome["failure_codes"] == ["provider_timeout"]


def test_a_grounded_turn_is_left_exactly_as_it_was() -> None:
    """Negative control: the check adds nothing when the evidence reached the answer."""

    from core.runtime_task_outcome import output_validation_outcome

    assert (
        output_validation_outcome(
            _control(evidence_binding={"has_evidence": True, "grounded": True, "evidence_discarded": False})
        )
        is None
    )


def test_a_turn_that_retrieved_nothing_is_left_exactly_as_it_was() -> None:
    """Negative control: no evidence, no verdict -- the check must not invent a failure."""

    from core.runtime_task_outcome import output_validation_outcome

    assert output_validation_outcome(_control()) is None
    assert (
        output_validation_outcome(
            _control(evidence_binding={"has_evidence": False, "grounded": True, "evidence_discarded": False})
        )
        is None
    )


def test_the_terminal_classifier_reports_the_partial_rather_than_success() -> None:
    """End of the chain: the turn trace itself must stop saying 'fulfilled'."""

    from core.runtime_task_outcome import terminal_fulfillment_outcome

    outcome = terminal_fulfillment_outcome(
        {"response": "I can look that up for you. Let me check."},
        source_context={
            "response_control": _control(
                evidence_binding={"has_evidence": True, "grounded": False, "evidence_discarded": True}
            )
        },
    )

    assert outcome.fulfillment_status.value == "partially_fulfilled"
    assert outcome.retryable is True


# ---------------------------------------------------------------------------------------------
# The substitution: what the user is shown when the model dropped the evidence
# ---------------------------------------------------------------------------------------------


def test_the_grounded_report_carries_only_retrieved_content_and_its_source() -> None:
    from core.evidence_binding import compose_grounded_report

    report = compose_grounded_report(
        [
            {
                "summary": "Dogger Bank buoy reports 17.4 C surface temperature.",
                "result_url": "https://buoys.invalid/dogger",
                "origin_domain": "buoys.invalid",
            }
        ]
    )

    assert "17.4" in report
    assert "Source: [buoys.invalid](https://buoys.invalid/dogger)." in report



def test_notes_without_content_compose_nothing_rather_than_an_empty_shell() -> None:
    """Negative control: no findings means no substitution, never a hollow 'here is what I found'."""

    from core.evidence_binding import compose_grounded_report

    assert compose_grounded_report([{"result_url": "https://x.invalid", "origin_domain": "x.invalid"}]) == ""
    assert compose_grounded_report([]) == ""
    assert compose_grounded_report(None) == ""


def test_the_report_is_bounded_so_one_turn_cannot_dump_a_whole_result_set() -> None:
    from core.evidence_binding import compose_grounded_report

    report = compose_grounded_report(_notes(*[f"Finding number {n} was recorded." for n in range(9)]))

    assert report.count("- ") == 3


# ---------------------------------------------------------------------------------------------
# The guard that stops this repair from becoming a truthfulness regression
# ---------------------------------------------------------------------------------------------



def test_the_binding_check_itself_still_flags_the_promise_case() -> None:
    """Negative control on the guard: it must narrow the substitution, not disable the check.

    A turn that is NOT a runtime degradation and drops real findings is still reported as discarded.
    """

    binding = inspect_evidence_binding(
        answer="I'll go and look that up.",
        notes=_notes("Bot API 9.4 shipped on 2026-07-02 with a new business-message field."),
        request_text="latest telegram bot api updates",
    )

    assert binding.evidence_discarded
