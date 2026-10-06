"""Profile heuristic meaning/polarity regression (2026-09-26).

The extractor used to fire on bare marker presence anywhere in the turn:
weather "clear" became a response-style preference, a negated GitHub ban
became a standing GitHub preference, motion "go" became the Go stack, a
one-task inspection became an enduring source rule, and "do not just do it"
became hands-off autonomy.  These cases pin the admission contract:
affirmative user-owned predicate, correct target object, preserved polarity,
durable (not one-task) scope.  Negated preferences are withheld, never
inverted — the store has no durable-negative heuristic representation.
"""
from __future__ import annotations

import pytest

from core.memory.learning import extract_user_heuristic_candidates as candidates


def signals(text: str) -> list[str]:
    return sorted(str(item["heuristic_id"]) for item in candidates(text))


# --- the five exposed false positives --------------------------------------

def test_weather_preference_is_not_response_style() -> None:
    assert signals("I prefer clear skies for evening walks.") == []


def test_negated_source_ban_is_not_a_positive_source_preference() -> None:
    assert signals("Do not use GitHub repositories as sources.") == []


def test_motion_go_is_not_the_go_stack() -> None:
    assert signals("Create a packing list before we go.") == []


def test_one_task_inspection_is_not_a_standing_source_preference() -> None:
    assert signals("Please inspect this GitHub repository for this task.") == []


def test_negated_do_not_just_do_it_is_not_hands_off() -> None:
    assert signals("Do not just do it; ask me before making changes.") == []


# --- preserved positive behavior -------------------------------------------

@pytest.mark.parametrize(
    "text,expected",
    [
        ("I prefer concise answers.", ["response_style:concise_direct"]),
        ("Keep recipe answers brief and direct while we bake.", ["response_style:concise_direct"]),
        ("Keep the answers brief while we triage this incident.", ["response_style:concise_direct"]),
        ("Keep answers concise and direct in code reviews, please.", ["response_style:concise_direct"]),
        ("Be honest with me in code reviews.", ["response_style:brutal_honest"]),
        ("no fluff in the answers please", ["response_style:brutal_honest"]),
        ("Use official docs as sources.", ["source_preference:official_docs"]),
        ("I prefer official documentation.", ["source_preference:official_docs"]),
        ("Use GitHub repos as references.", ["source_preference:github_repos"]),
        ("Write it in Rust.", ["preferred_stack:rust"]),
        ("We build our internal tooling in Rust.", ["preferred_stack:rust"]),
        ("I prefer Go for the API layer.", ["preferred_stack:go"]),
        ("I'm building a discord bot.", ["project_focus:discord_bot"]),
        ("Don't ask me before making changes; just proceed.", ["autonomy_preference:hands_off"]),
    ],
)
def test_affirmative_correctly_targeted_preferences_are_kept(text: str, expected: list[str]) -> None:
    assert signals(text) == sorted(expected)


# --- polarity, scope and target controls ------------------------------------

@pytest.mark.parametrize(
    "text",
    [
        "Never use GitHub repositories as sources.",
        "Please use Python just for this task.",
        "Honestly, please inspect the report.",
        "The telegram bot crashed again today.",
        "clear skies and honest people make the walk nice",
        "I don't want brief answers.",
        "Stop writing concise summaries.",
    ],
)
def test_negated_task_local_or_untargeted_text_creates_no_heuristics(text: str) -> None:
    assert signals(text) == []


def test_marker_in_one_clause_cannot_borrow_a_predicate_from_another() -> None:
    # "github" sits in a reporting clause with no source-usage predicate of
    # its own; the imperative in the first clause must not lend it authority.
    assert signals("Fix the parser. The blog mentioned GitHub once.") == []

# --- review-1 regressions: autonomy polarity and subject/object binding ------


@pytest.mark.parametrize(
    "text",
    [
        "Never act without my approval.",
        "Do not proceed without my sign-off.",
        "Never ship anything without my confirmation.",
    ],
)
def test_approval_required_requests_never_store_hands_off(text: str) -> None:
    # a negated action verb carrying the without-frame DEMANDS approval —
    # the opposite of the hands-off preference
    assert signals(text) == []


@pytest.mark.parametrize(
    "text",
    [
        "Act without asking for confirmation.",
        "Proceed without my approval.",
        "You can continue without checking with me.",
    ],
)
def test_genuine_low_friction_directives_still_store_hands_off(text: str) -> None:
    assert signals(text) == ["autonomy_preference:hands_off"]


def test_physical_clearance_is_not_response_style() -> None:
    assert signals("Please keep the freezer clear of ice.") == []


def test_third_party_habit_is_not_user_preference() -> None:
    assert signals("My colleague always wants concise answers.") == []


def test_standing_imperative_still_confers_authority() -> None:
    # "always" before a bare imperative verb form is still the user's
    # standing directive; only finite third-person verbs lose authority
    assert signals("Always cite official documentation.") == ["source_preference:official_docs"]


def test_genuine_keep_answer_style_still_stores() -> None:
    assert signals("Please keep your answers concise.") == ["response_style:concise_direct"]
