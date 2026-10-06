"""Owner-review authority-repair pins (paired300-authority-repair-20260930).

The record law in `core.unsourced_current_claim.recorded_state_retention`
gained four authority rules the owner review measured as missing; this file
pins each MECHANISM on new worlds so the frozen owner file stays regression
evidence only:

1. support binding — a value from another subject never authorizes the claim,
   and a shared unit is not subject identity;
2. negation/retraction — an explicitly negated value is never positive
   evidence and never stays the current value;
3. correction authority — a same-day correction lowering a maximum replaces
   the numerical-extremum fallback;
4. provenance recency — only canonical provenance dates order records; dates
   in fact prose do not; and an improved "best" duration survives without a
   direction synonym.

Proof level: the owning law directly, plus the final-response seam for the
binding rule. Offline, no provider.
"""
from __future__ import annotations

from core.agent_runtime.response import _validate_final_chat_output
from core.unsourced_current_claim import (
    _line_provenance_date,
    recorded_state_retention,
)


def _wrap(lines: list[str]) -> str:
    return (
        "<retrieved_context>\nDistilled local facts. Answer from these exact facts only.\n"
        + "\n".join(lines)
        + "\n</retrieved_context>"
    )


# ── 1. support binding ────────────────────────────────────────────────────────


def test_shared_unit_is_not_subject_identity() -> None:
    capsule = _wrap(
        [
            "- user said: My heaviest crate of quinces is 31 kilograms. (stated: 2026-09-11)",
            "- user said: My heaviest trailer load of mulch is 82 kilograms. (stated: 2026-09-14)",
        ]
    )
    assert (
        recorded_state_retention(
            "Your current heaviest crate of quinces is 82 kilograms.", capsule
        )
        is False
    )


def test_support_line_must_share_the_claim_subject_through_the_seam() -> None:
    context = {
        "surface": "openclaw",
        "platform": "openclaw",
        "chat_id": "authority-bind",
        "conversation_history": [
            {"role": "user", "content": "What is my current heaviest crate of quinces?"}
        ],
        "admitted_capsule_evidence": {
            "text": _wrap(
                [
                    "- user said: My heaviest crate of quinces is 31 kilograms. "
                    "(stated: 2026-09-11)",
                    "- user said: My heaviest trailer load of mulch is 82 kilograms. "
                    "(stated: 2026-09-14)",
                ]
            ),
            "chat_id": "authority-bind",
            "source": "canonical_runtime_transcript",
        },
    }
    delivered = _validate_final_chat_output(
        "Your current heaviest crate of quinces is 82 kilograms.",
        source_context=context,
    )
    assert "82 kilograms" not in delivered
    assert "not going to state" in delivered


# ── 2. negation / retraction ─────────────────────────────────────────────────


def test_negated_value_is_never_positive_evidence_even_after_a_later_positive_line() -> None:
    # The negation still governs even when the digits also occur in an earlier
    # affirmative line: a retraction withdraws the value from the record.
    capsule = _wrap(
        [
            "- user said: My longest dusk paddle was 18 kilometres. (stated: 2026-09-03)",
            "- user said: My longest dusk paddle is not 18 kilometres anymore; "
            "the record is 23 kilometres. (stated: 2026-09-19)",
        ]
    )
    assert (
        recorded_state_retention(
            "Your current longest dusk paddle is 18 kilometres.", capsule
        )
        is False
    )


def test_negated_value_cannot_remain_the_current_record() -> None:
    # Retraction also removes the value from the extremum pool: the corrected
    # value becomes the record even though it is numerically smaller.
    capsule = _wrap(
        [
            "- user said: My heaviest pumpkin weighs 40 kilograms. (recorded: 2026-09-30)",
            "- user said: Correction: my heaviest pumpkin weighs 36 kilograms, "
            "not 40 kilograms. (recorded: 2026-09-30)",
        ]
    )
    assert (
        recorded_state_retention(
            "Your current heaviest pumpkin weighs 36 kilograms.", capsule
        )
        is True
    )
    assert (
        recorded_state_retention(
            "Your current heaviest pumpkin weighs 40 kilograms.", capsule
        )
        is False
    )


# ── 3. correction authority over the numerical extremum ──────────────────────


def test_same_day_correction_lowering_a_maximum_controls() -> None:
    capsule = _wrap(
        [
            "- user said: My highest labyrinth walk score is 88 points. (stated: 2026-09-21)",
            "- user said: Correction: my highest labyrinth walk score is 83 points, "
            "not 88 points. (stated: 2026-09-21)",
        ]
    )
    assert (
        recorded_state_retention(
            "Your current highest labyrinth walk score is 83 points.", capsule
        )
        is True
    )
    assert (
        recorded_state_retention(
            "Your current highest labyrinth walk score is 88 points.", capsule
        )
        is False
    )


# ── 4. provenance recency + best-duration direction ──────────────────────────


def test_prose_date_is_not_statement_provenance() -> None:
    line = (
        "- user said: My longest fence row is 39 metres; I plan its rebuild on "
        "2029-05-01. (stated: 2026-09-05)"
    )
    assert _line_provenance_date(line) == "2026-09-05"
    assert _line_provenance_date("- user said: The fair visits were lovely.") == ""
    assert (
        _line_provenance_date(
            "- user said: Rowed at dawn. (recorded: 2026-09-30)"
        )
        == "2026-09-30"
    )


def test_prose_deadline_cannot_make_a_stale_record_current() -> None:
    capsule = _wrap(
        [
            "- user said: My longest fence row is 39 metres; I plan its rebuild on "
            "2029-05-01. (stated: 2026-09-05)",
            "- user said: My longest fence row is now 41 metres. (stated: 2026-09-24)",
        ]
    )
    assert (
        recorded_state_retention(
            "Your current longest fence row is 39 metres.", capsule
        )
        is False
    )
    assert (
        recorded_state_retention(
            "Your current longest fence row is 41 metres.", capsule
        )
        is True
    )


def test_improved_best_duration_survives_without_a_direction_synonym() -> None:
    capsule = _wrap(
        [
            "- user said: My best crest-to-creek run time is 52 minutes. (stated: 2026-09-26)",
            "- user said: My best crest-to-creek run time is now 47 minutes. (stated: 2026-09-26)",
        ]
    )
    assert (
        recorded_state_retention(
            "Your current best crest-to-creek run time is 47 minutes.", capsule
        )
        is True
    )
    assert (
        recorded_state_retention(
            "Your current best crest-to-creek run time is 52 minutes.", capsule
        )
        is False
    )


def test_best_duration_stays_higher_better_when_the_sentence_names_direction() -> None:
    # An explicitly higher-is-better duration ("longest") is not flipped by the
    # duration rule: the named direction governs.
    capsule = _wrap(
        [
            "- user said: My longest continuous hike is 9 hours. (recorded: 2026-09-30)",
            "- user said: My longest continuous hike is now 12 hours. (recorded: 2026-09-30)",
        ]
    )
    assert (
        recorded_state_retention(
            "Your current longest continuous hike is 12 hours.", capsule
        )
        is True
    )
    assert (
        recorded_state_retention(
            "Your current longest continuous hike is 9 hours.", capsule
        )
        is False
    )


def test_ambiguous_single_record_without_dates_still_retains() -> None:
    # One undated same-subject record: no competition, the admitted value is
    # the record whatever the direction reading says.
    capsule = _wrap(
        [
            "- user said: My best bread proof is 3 hours. (recorded: 2026-09-30)",
        ]
    )
    assert (
        recorded_state_retention(
            "Your current best bread proof is 3 hours.", capsule
        )
        is True
    )
