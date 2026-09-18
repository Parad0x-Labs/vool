from __future__ import annotations

import pytest

from core.response_constraints import (
    check_response_constraint,
    constraint_safe_fallback,
    enforce_response_constraint,
    exact_word_retry_contract,
    exact_word_retry_text,
    formatting_retry_instruction,
    parse_response_constraint,
    short_answer_needs_grounding_retry,
)


@pytest.mark.parametrize(
    ("prompt", "exact_words", "max_words"),
    [
        ("Answer in one word: is it ready?", 1, 1),
        ("Give me a single-word response.", 1, 1),
        ("Exactly one word: calm.", 1, 1),
        ("Answer in two words.", 2, 2),
        ("Give me a three-word mood.", 3, 3),
        ("Exactly four words: describe a clean workspace.", 4, 4),
        ("Put that in exactly three words.", 3, 3),
        ("Can you put that in exactly three words?", 3, 3),
        ("End with one word describing this conversation.", 1, 1),
        ("Answer in exactly six words: identify VOOL and its maker.", 6, 6),
        ("Explain the result in exactly twelve words.", 12, 12),
        ("Explain it in exactly 12 words.", 12, 12),
        ("Use no more than 25 words.", None, 25),
        ("Keep it to 30 words or fewer.", None, 30),
    ],
)
def test_explicit_word_constraints_are_parsed(
    prompt: str,
    exact_words: int | None,
    max_words: int,
) -> None:
    constraint = parse_response_constraint(prompt)

    assert constraint is not None
    assert constraint.exact_words == exact_words
    assert constraint.max_words == max_words


def test_short_sentence_constraint_is_parsed_without_fixed_answer_content() -> None:
    constraint = parse_response_constraint(
        "Confirm it in one short sentence."
    )

    assert constraint is not None
    assert constraint.exact_sentences == 1
    assert constraint.max_sentences == 1


def test_low_information_one_word_open_answer_requests_grounding_retry() -> None:
    constraint = parse_response_constraint(
        "Answer with one word: how can a room feel after rearranging it?"
    )
    assert constraint is not None
    assert short_answer_needs_grounding_retry(
        "Different.",
        constraint,
        "Answer with one word: how can a room feel after rearranging it?",
    )
    assert not short_answer_needs_grounding_retry(
        "Ready.",
        constraint,
        "Is the room ready? Answer with one word.",
    )


@pytest.mark.parametrize(
    "identifier",
    ["MARIGOLD-8342", "MARIGOLD_8342", "BRAMBLE-7A94"],
)
def test_sentence_constraint_survives_user_identifiers(identifier: str) -> None:
    constraint = parse_response_constraint(
        f"Confirm the code {identifier} in one short sentence."
    )

    assert constraint is not None
    assert constraint.exact_sentences == 1


@pytest.mark.parametrize(
    "prompt",
    [
        "One word can change the meaning of this paragraph.",
        "Write a story about exactly twelve birds.",
        "What does max words mean in this editor?",
        "Tell me why concise answers are useful.",
    ],
)
def test_ordinary_prose_does_not_create_a_constraint(prompt: str) -> None:
    assert parse_response_constraint(prompt) is None


@pytest.mark.parametrize(
    "prompt",
    [
        "1. Explain why leaves look green.\n2. Calculate 37 × 24.\n3. Give 5-word title.",
        "Explain ocean blue. Calculate 39 × 24. Give 7-word title.",
    ],
)
def test_part_scoped_title_length_never_truncates_the_whole_multi_part_answer(prompt: str) -> None:
    assert parse_response_constraint(prompt) is None


def test_retry_instruction_contains_shape_not_expected_content() -> None:
    constraint = parse_response_constraint("Reply in exactly 7 words.")
    assert constraint is not None

    instruction = formatting_retry_instruction(constraint)

    assert "exactly 7 word" in instruction
    assert "Answer the original user request" in instruction
    assert "Do not apologize" in instruction


def test_exact_word_retry_contract_requires_the_requested_array_length() -> None:
    constraint = parse_response_constraint("Reply in exactly 6 words.")
    assert constraint is not None

    contract = exact_word_retry_contract(constraint)

    assert contract is not None
    schema = contract["json_schema"]
    words = schema["properties"]["words"]
    assert words["minItems"] == 6
    assert words["maxItems"] == 6
    assert words["items"] == {"type": "string"}
    assert schema["additionalProperties"] is False


def test_exact_word_retry_contract_keeps_short_answers_on_existing_path() -> None:
    constraint = parse_response_constraint("Reply in exactly 3 words.")
    assert constraint is not None

    assert exact_word_retry_contract(constraint) is None


def test_exact_word_retry_text_joins_valid_model_generated_words() -> None:
    constraint = parse_response_constraint("Reply in exactly 6 words.")
    assert constraint is not None

    text = exact_word_retry_text(
        '{"words":["VOOL","is","built","by","Parad0x","Labs"]}',
        constraint,
    )

    assert text == "VOOL is built by Parad0x Labs"


def test_exact_word_retry_text_strips_only_edge_punctuation() -> None:
    constraint = parse_response_constraint("Reply in exactly 6 words.")
    assert constraint is not None

    text = exact_word_retry_text(
        '{"words":["VOOL","is","built","by","Parad0x","Labs."]}',
        constraint,
    )

    assert text == "VOOL is built by Parad0x Labs"


@pytest.mark.parametrize(
    "payload",
    [
        "not-json",
        '{"words":["too","few"]}',
        '{"words":["VOOL","is","built","by","Parad0x","Labs today"]}',
        '{"words":["VOOL","is","built","by","Parad0x","Labs"],"extra":true}',
    ],
)
def test_exact_word_retry_text_rejects_invalid_provider_output(
    payload: str,
) -> None:
    constraint = parse_response_constraint("Reply in exactly 6 words.")
    assert constraint is not None

    assert exact_word_retry_text(payload, constraint) is None


def test_structural_bound_enforces_one_word_without_canned_content() -> None:
    constraint = parse_response_constraint("Answer in one word.")
    assert constraint is not None

    result = enforce_response_constraint(
        "Ready, with one remaining caveat.",
        constraint,
    )

    assert result.text == "Ready"
    assert result.compliant is True
    assert result.structurally_trimmed is True
    assert check_response_constraint(result.text, constraint).compliant


def test_exact_two_words_compresses_a_model_generated_coordinate_phrase() -> None:
    constraint = parse_response_constraint("Answer in exactly two words.")
    assert constraint is not None

    result = enforce_response_constraint("Clean and organized.", constraint)

    assert result.text == "Clean, organized"
    assert result.compliant is True
    assert result.structurally_trimmed is True


def test_exact_shortfall_is_reported_not_padded_with_invented_words() -> None:
    constraint = parse_response_constraint("Reply in exactly 4 words.")
    assert constraint is not None

    result = enforce_response_constraint("Not yet.", constraint)

    assert result.text == "Not yet."
    assert result.compliant is False
    assert "exact_words" in result.violations


@pytest.mark.parametrize("text", ["Clear and", "Choose the", "Ready because"])
def test_exact_short_answers_reject_high_confidence_incomplete_fragments(
    text: str,
) -> None:
    constraint = parse_response_constraint("Answer in exactly two words.")
    assert constraint is not None

    check = check_response_constraint(text, constraint)

    assert check.word_count == 2
    assert check.compliant is False
    assert check.violations == ("incomplete_fragment",)


@pytest.mark.parametrize("text", ["I couldn't", "I can't", "Unable to"])
def test_exact_short_answers_reject_clipped_refusals(text: str) -> None:
    constraint = parse_response_constraint("Answer in exactly two words.")
    assert constraint is not None

    check = check_response_constraint(text, constraint)

    assert check.compliant is False
    assert check.violations == ("non_answer_refusal",)


def test_exact_two_word_fallback_keeps_the_requested_shape() -> None:
    constraint = parse_response_constraint("Answer in exactly two words.")
    assert constraint is not None

    fallback = constraint_safe_fallback(constraint)

    assert fallback == "No answer."
    assert check_response_constraint(fallback, constraint).compliant is True


def test_exact_four_word_fallback_keeps_the_requested_shape() -> None:
    constraint = parse_response_constraint(
        "Exactly four words: describe a clean workspace."
    )
    assert constraint is not None

    fallback = constraint_safe_fallback(constraint)

    assert fallback == "No usable answer available."
    assert check_response_constraint(fallback, constraint).compliant is True


def test_exact_six_word_fallback_keeps_shape_without_answer_content() -> None:
    constraint = parse_response_constraint("Answer in exactly six words.")
    assert constraint is not None

    fallback = constraint_safe_fallback(constraint)

    assert check_response_constraint(fallback, constraint).compliant is True
    assert "vool" not in fallback.casefold()
    assert "parad0x" not in fallback.casefold()


@pytest.mark.parametrize("word_count", range(1, 21))
def test_exact_word_fallback_is_structurally_compliant(word_count: int) -> None:
    constraint = parse_response_constraint(
        f"Answer in exactly {word_count} words."
    )
    assert constraint is not None

    fallback = constraint_safe_fallback(constraint)

    assert check_response_constraint(fallback, constraint).compliant is True


@pytest.mark.parametrize("text", ["Ready", "No", "New York", "Yes or no"])
def test_short_constraint_preserves_complete_answers_and_fragments(text: str) -> None:
    prompt = {
        "Ready": "Answer in one word.",
        "No": "Answer in one word.",
        "New York": "Answer in exactly two words.",
        "Yes or no": "Answer in exactly three words.",
    }[text]
    constraint = parse_response_constraint(prompt)
    assert constraint is not None

    check = check_response_constraint(text, constraint)

    assert check.compliant is True
