from core.ordinary_chat_response_guard import (
    constrain_ordinary_chat_output,
    forgotten_prior_turn_literal_hashes,
    inspect_ordinary_chat_output,
    ordinary_chat_output_policy,
    prior_turn_literal_hashes,
    remove_unrequested_prior_turn_literals,
    remove_unsolicited_generic_follow_up,
)

MULTI_PART_PROMPT = (
    "Explain ocean blue. Calculate 39 × 24. Give 7-word title."
)


def test_multi_part_policy_requires_every_numbered_answer() -> None:
    policy = ordinary_chat_output_policy(
        prompt_profile="plain_task_minimal",
        output_mode="plain_text",
        user_text=MULTI_PART_PROMPT,
    )

    assert policy["required_numbered_parts"] == 3
    partial = inspect_ordinary_chat_output(
        "1. Water absorbs red wavelengths more strongly, leaving blue light visible.",
        policy,
        current_user_text=MULTI_PART_PROMPT,
    )
    complete = inspect_ordinary_chat_output(
        "1. Water scatters and reflects more blue light.\n2. 39 × 24 = 936.\n3. Blue Depths Shape Our Living World",
        policy,
        current_user_text=MULTI_PART_PROMPT,
    )

    assert partial.allowed is False
    assert partial.reasons == ("missing_requested_parts",)
    assert complete.allowed is True
from core.response_language_policy import (
    check_response_language,
    response_language_policy_for_text,
)


def test_ordinary_chat_rejects_image_prompt_shaped_output() -> None:
    policy = ordinary_chat_output_policy(
        prompt_profile="chat_minimal",
        output_mode="plain_text",
    )

    result = inspect_ordinary_chat_output(
        "Subject: a library card. Camera: wide shot, 35mm lens. "
        "Lighting: cinematic blue hour with film grain.",
        policy,
    )

    assert result.allowed is False
    assert set(result.signal_groups) == {
        "camera_or_composition",
        "visual_style",
        "prompt_scaffold",
    }


def test_ordinary_chat_does_not_treat_a_code_word_as_an_image_prompt_bypass() -> None:
    policy = ordinary_chat_output_policy(
        prompt_profile="chat_minimal",
        output_mode="plain_text",
    )

    result = inspect_ordinary_chat_output(
        "Subject: a library code. Camera: wide shot, 35mm lens. "
        "Lighting: cinematic blue hour with film grain.",
        policy,
    )

    assert result.allowed is False
    assert result.reasons == ("image_prompt_shaped_output",)


def test_ordinary_chat_rejects_an_unrequested_contrast_topic() -> None:
    policy = ordinary_chat_output_policy(
        prompt_profile="chat_minimal",
        output_mode="plain_text",
    )

    result = inspect_ordinary_chat_output(
        "File names identify files. Indexes, on the other hand, speed up lookup.",
        policy,
        current_user_text="Explain why file names matter.",
    )

    assert result.allowed is False
    assert result.reasons == ("unrequested_contrast_topic",)


def test_ordinary_chat_allows_a_contrast_topic_the_user_requested() -> None:
    policy = ordinary_chat_output_policy(
        prompt_profile="chat_minimal",
        output_mode="plain_text",
    )

    result = inspect_ordinary_chat_output(
        "File names identify files. Indexes, on the other hand, speed up lookup.",
        policy,
        current_user_text="Compare file names with indexes.",
    )

    assert result.allowed is True


def test_ordinary_chat_rejects_an_unrequested_prior_turn_comparison() -> None:
    policy = ordinary_chat_output_policy(
        prompt_profile="chat_minimal",
        output_mode="plain_text",
    )

    result = inspect_ordinary_chat_output(
        "File names identify files. Just like indexes, they make lookup easier.",
        policy,
        current_user_text="Explain why file names matter.",
    )

    assert result.allowed is False
    assert result.reasons == ("unrequested_prior_reference",)


def test_ordinary_chat_allows_a_comparison_in_an_explicit_prior_answer_follow_up() -> None:
    policy = ordinary_chat_output_policy(
        prompt_profile="chat_minimal",
        output_mode="plain_text",
    )

    result = inspect_ordinary_chat_output(
        "The human part was similar to the personal feel of writing by hand.",
        policy,
        current_user_text="What was the most human reason you gave there?",
    )

    assert result.allowed is True


def test_ordinary_chat_requires_an_explicit_confirmation_identifier() -> None:
    policy = ordinary_chat_output_policy(
        prompt_profile="chat_minimal",
        output_mode="plain_text",
    )

    result = inspect_ordinary_chat_output(
        "Confirmed.",
        policy,
        current_user_text="Confirm the code BRAMBLE-7194 in one short sentence.",
    )

    assert result.allowed is False
    assert result.reasons == ("missing_confirmed_literal",)


def test_ordinary_chat_rejects_an_unrequested_identifier_from_a_prior_turn() -> None:
    current_user_text = "What do I prefer now?"
    policy = ordinary_chat_output_policy(
        prompt_profile="chat_minimal",
        output_mode="plain_text",
        user_text=current_user_text,
        prior_turn_literal_hashes=prior_turn_literal_hashes(
            [
                {"role": "user", "content": "For this chat, the project signal is EMBER-963."},
                {"role": "assistant", "content": "Acknowledged."},
                {"role": "user", "content": current_user_text},
            ],
            current_user_text=current_user_text,
        ),
    )

    result = inspect_ordinary_chat_output(
        "You prefer loose notes. The earlier signal was EMBER-963.",
        policy,
        current_user_text=current_user_text,
    )

    assert result.allowed is False
    assert result.reasons == ("unrequested_prior_turn_literal",)


def test_ordinary_chat_allows_an_identifier_the_current_turn_requests() -> None:
    current_user_text = "What does EMBER-963 mean in this chat?"
    policy = ordinary_chat_output_policy(
        prompt_profile="chat_minimal",
        output_mode="plain_text",
        user_text=current_user_text,
        prior_turn_literal_hashes=prior_turn_literal_hashes(
            [{"role": "user", "content": "EMBER-963 is our project signal."}],
            current_user_text=current_user_text,
        ),
    )

    assert inspect_ordinary_chat_output(
        "EMBER-963 is the project signal you named in this chat.",
        policy,
        current_user_text=current_user_text,
    ).allowed is True


def test_ordinary_chat_allows_a_scoped_identifier_recall_from_the_current_chat() -> None:
    current_user_text = "What exact identifier did I just ask you to remember?"
    policy = ordinary_chat_output_policy(
        prompt_profile="chat_minimal",
        output_mode="plain_text",
        user_text=current_user_text,
        prior_turn_literal_hashes=prior_turn_literal_hashes(
            [
                {
                    "role": "user",
                    "content": "Remember this exact identifier: KAS-ALPHA-8841.",
                }
            ],
            current_user_text=current_user_text,
        ),
    )

    assert policy["scoped_memory_recall"] is True
    assert inspect_ordinary_chat_output(
        "KAS-ALPHA-8841",
        policy,
    ).allowed is True


def test_ordinary_chat_allows_a_current_marker_recall_without_widening_other_answers() -> None:
    current_user_text = "What is the current marker?"
    policy = ordinary_chat_output_policy(
        prompt_profile="chat_minimal",
        output_mode="plain_text",
        user_text=current_user_text,
        prior_turn_literal_hashes=prior_turn_literal_hashes(
            [{"role": "user", "content": "The marker is ORBIT-GAMMA-4408."}],
            current_user_text=current_user_text,
        ),
    )

    assert inspect_ordinary_chat_output(
        "The current marker is ORBIT-GAMMA-4408.",
        policy,
        current_user_text=current_user_text,
    ).allowed is True
    assert inspect_ordinary_chat_output(
        "Your unrelated preference is tea, and the old marker was ORBIT-GAMMA-4408.",
        policy,
        current_user_text="What do I prefer now?",
    ).allowed is False


def test_ordinary_chat_rejects_a_superseded_literal_when_the_prompt_forbids_repeating_it() -> None:
    current_user_text = "What was the old code? Answer without repeating a superseded value."
    policy = ordinary_chat_output_policy(
        prompt_profile="chat_minimal",
        output_mode="plain_text",
        user_text=current_user_text,
        prior_turn_literal_hashes=prior_turn_literal_hashes(
            [{"role": "user", "content": "The code was BAY 13 EAST."}],
            current_user_text=current_user_text,
        ),
    )

    assert policy["scoped_memory_recall"] is False
    assert inspect_ordinary_chat_output(
        "The old code was BAY 13 EAST.",
        policy,
    ).allowed is False


def test_ordinary_chat_rejects_a_literal_after_a_prior_forget_command() -> None:
    current_user_text = "Do you still have the exact identifier from this chat? Answer honestly."
    messages = [
        {
            "role": "user",
            "content": "Remember this exact identifier: KAS-ALPHA-8841.",
        },
        {
            "role": "user",
            "content": "Forget the identifier KAS-ALPHA-8841 from this chat.",
        },
    ]
    policy = ordinary_chat_output_policy(
        prompt_profile="chat_minimal",
        output_mode="plain_text",
        user_text=current_user_text,
        prior_turn_literal_hashes=prior_turn_literal_hashes(
            messages,
            current_user_text=current_user_text,
        ),
        forgotten_prior_turn_literal_hashes=forgotten_prior_turn_literal_hashes(messages),
    )

    assert policy["forgotten_prior_turn_literal_hashes"]
    result = inspect_ordinary_chat_output(
        "The forgotten identifier was KAS-ALPHA-8841.",
        policy,
        current_user_text=current_user_text,
    )

    assert result.allowed is False
    assert result.reasons == ("forgotten_memory_literal",)


def test_ordinary_chat_removes_only_the_sentence_with_an_unrequested_prior_identifier() -> None:
    current_user_text = "What do I prefer now?"
    policy = ordinary_chat_output_policy(
        prompt_profile="chat_minimal",
        output_mode="plain_text",
        prior_turn_literal_hashes=prior_turn_literal_hashes(
            [{"role": "user", "content": "The project signal is QUARTZ-581."}],
            current_user_text=current_user_text,
        ),
    )

    assert remove_unrequested_prior_turn_literals(
        "You prefer loose notes. The project signal is QUARTZ-581.",
        policy,
        current_user_text=current_user_text,
    ) == "You prefer loose notes."


def test_ordinary_chat_rejects_an_unsolicited_generic_follow_up() -> None:
    policy = ordinary_chat_output_policy(
        prompt_profile="chat_minimal",
        output_mode="plain_text",
    )

    result = inspect_ordinary_chat_output(
        "You prefer loose notes. How can I assist you today?",
        policy,
        current_user_text="What do I prefer now?",
    )

    assert result.allowed is False
    assert result.reasons == ("unsolicited_generic_follow_up",)
    assert remove_unsolicited_generic_follow_up(
        "You prefer loose notes. How can I assist you today?"
    ) == "You prefer loose notes."


def test_ordinary_chat_rejects_an_unsolicited_generic_topic_question() -> None:
    policy = ordinary_chat_output_policy(
        prompt_profile="chat_minimal",
        output_mode="plain_text",
    )

    result = inspect_ordinary_chat_output(
        "For this chat, we will use a short notebook. Do you have any specific topics or questions in mind?",
        policy,
        current_user_text="I prefer a short notebook after all.",
    )

    assert result.reasons == ("unsolicited_generic_follow_up",)
    assert remove_unsolicited_generic_follow_up(
        "For this chat, we will use a short notebook. Do you have any specific topics or questions in mind?"
    ) == "For this chat, we will use a short notebook."


def test_ordinary_chat_rejects_an_unsolicited_generic_closing_question() -> None:
    policy = ordinary_chat_output_policy(
        prompt_profile="chat_minimal",
        output_mode="plain_text",
    )

    result = inspect_ordinary_chat_output(
        "For this chat, I will use loose notes. What would you like to discuss or do?",
        policy,
        current_user_text="For this chat, I prefer loose notes to notebooks.",
    )

    assert result.reasons == ("unsolicited_generic_follow_up",)
    assert remove_unsolicited_generic_follow_up(
        "For this chat, I will use loose notes. What would you like to discuss or do?"
    ) == "For this chat, I will use loose notes."


def test_ordinary_chat_rejects_an_unsolicited_personal_closing_question() -> None:
    policy = ordinary_chat_output_policy(
        prompt_profile="chat_minimal",
        output_mode="plain_text",
    )

    result = inspect_ordinary_chat_output(
        "Early mornings can be quieter and easier to focus in. Do you relate to any of these reasons?",
        policy,
        current_user_text="Why do some people enjoy early mornings?",
    )

    assert result.reasons == ("unsolicited_generic_follow_up",)
    assert remove_unsolicited_generic_follow_up(
        "Early mornings can be quieter and easier to focus in. Do you relate to any of these reasons?"
    ) == "Early mornings can be quieter and easier to focus in."


def test_ordinary_chat_allows_assistance_language_when_the_user_asks_for_it() -> None:
    policy = ordinary_chat_output_policy(
        prompt_profile="chat_minimal",
        output_mode="plain_text",
    )

    assert inspect_ordinary_chat_output(
        "I can help you organize notes and keep a short index.",
        policy,
        current_user_text="How can you help me organize notes?",
    ).allowed is True


def test_ordinary_chat_rejects_a_one_word_answer_to_an_open_question() -> None:
    policy = ordinary_chat_output_policy(
        prompt_profile="chat_minimal",
        output_mode="plain_text",
        user_text="Why might someone keep a paper notebook even when they use a phone?",
    )

    result = inspect_ordinary_chat_output(
        "Portability.",
        policy,
        current_user_text="Why might someone keep a paper notebook even when they use a phone?",
    )

    assert result.allowed is False
    assert result.reasons == ("ordinary_answer_too_short",)


def test_ordinary_chat_uses_a_budget_without_detail_request() -> None:
    policy = ordinary_chat_output_policy(
        prompt_profile="chat_minimal",
        output_mode="plain_text",
        user_text="Why do file names matter?",
    )
    text = " ".join(["useful"] * 65) + "."

    result = inspect_ordinary_chat_output(text, policy)

    assert result.allowed is False
    assert result.reasons == ("ordinary_response_too_long",)


def test_ordinary_chat_policy_covers_the_live_text_output_mode() -> None:
    policy = ordinary_chat_output_policy(
        prompt_profile="chat_minimal",
        output_mode="text",
        user_text="Why do file names matter?",
    )

    assert policy["mode"] == "ordinary_chat"
    assert policy["max_words"] == "64"


def test_ordinary_chat_budget_preserves_requested_detail() -> None:
    policy = ordinary_chat_output_policy(
        prompt_profile="chat_minimal",
        output_mode="plain_text",
        user_text="Explain this in detail with examples.",
    )

    assert policy["max_words"] == ""


def test_ordinary_chat_budget_trims_at_a_sentence_boundary() -> None:
    policy = {"mode": "ordinary_chat", "max_words": "5"}

    result = constrain_ordinary_chat_output(
        "One two three. Four five six seven.",
        policy,
    )

    assert result == "One two three."


def test_ordinary_chat_allows_normal_code_explanation_and_explicit_creative_mode() -> None:
    ordinary_policy = ordinary_chat_output_policy(
        prompt_profile="chat_minimal",
        output_mode="plain_text",
    )
    text = "Use a camera object in the code, then set the lighting flag to false."

    assert inspect_ordinary_chat_output(text, ordinary_policy).allowed is True
    assert inspect_ordinary_chat_output(
        "Camera: wide shot. Lighting: cinematic blue hour.",
        ordinary_chat_output_policy(
            prompt_profile="creative_director",
            output_mode="plain_text",
            creative_medium="image",
        ),
    ).allowed is True


def test_ordinary_chat_allows_conceptual_camera_and_lighting_explanation() -> None:
    policy = ordinary_chat_output_policy(
        prompt_profile="chat_minimal",
        output_mode="plain_text",
    )

    assert inspect_ordinary_chat_output(
        "A camera captures the scene, while lighting affects how bright it appears.",
        policy,
    ).allowed is True


def test_profile_misclassification_does_not_disable_the_ordinary_chat_guard() -> None:
    policy = ordinary_chat_output_policy(
        prompt_profile="creative_director",
        output_mode="plain_text",
        creative_medium=None,
    )

    assert policy["mode"] == "ordinary_chat"


def test_ordinary_chat_rejects_obvious_boilerplate_overanswer() -> None:
    policy = ordinary_chat_output_policy(
        prompt_profile="chat_minimal",
        output_mode="plain_text",
        user_text="Why is a clear label useful?",
    )
    text = """A clear label helps people understand information quickly.

- It improves scanning.
- It reduces confusion.
- It makes later retrieval easier.
- It supports consistent organization.

Feel free to ask if you have any other questions."""

    padded = text + " This explanation also covers general background and practical considerations." * 18
    result = inspect_ordinary_chat_output(padded, policy)

    assert result.allowed is False
    assert result.reasons == ("boilerplate_overanswer",)


def test_ordinary_chat_rejects_long_unsolicited_list_without_generic_tail() -> None:
    policy = ordinary_chat_output_policy(
        prompt_profile="chat_minimal",
        output_mode="plain_text",
        user_text="Why are file names useful?",
    )
    text = """A file name can be helpful for many different reasons.

- It labels the content.
- It makes later retrieval easier.
- It helps sort related material.

""" + "This adds supporting background that is not needed to answer the question. " * 18

    result = inspect_ordinary_chat_output(text, policy)

    assert result.allowed is False
    assert result.reasons == ("boilerplate_overanswer",)


def test_ordinary_chat_rejects_long_unsolicited_heading_block() -> None:
    policy = ordinary_chat_output_policy(
        prompt_profile="chat_minimal",
        output_mode="plain_text",
        user_text="Why are file names useful?",
    )
    text = """A file name can be helpful for many different reasons.

Quick Identification: It makes a file easier to recognize.
Organization: It keeps related material easier to browse.
Searchability: It provides useful terms for later retrieval.

""" + "This adds supporting background that is not needed to answer the question. " * 18

    result = inspect_ordinary_chat_output(text, policy)

    assert result.allowed is False
    assert result.reasons == ("boilerplate_overanswer",)
    assert "heading_block" in result.signal_groups


def test_ordinary_chat_preserves_requested_detail_and_useful_long_text() -> None:
    policy = ordinary_chat_output_policy(
        prompt_profile="chat_minimal",
        output_mode="plain_text",
        user_text="Give me a detailed step-by-step list of ways to organize notes.",
    )
    text = """Here is a detailed approach:

- Group related notes.
- Add useful labels.
- Review stale items.
- Keep a short index.

Feel free to ask if you have any other questions."""
    padded = text + " The tradeoffs depend on the size and purpose of the collection." * 20

    assert inspect_ordinary_chat_output(padded, policy).allowed is True


def test_language_policy_requires_english_only_for_high_confidence_english_turns() -> None:
    policy = response_language_policy_for_text(
        "Please explain why a library index improves search speed."
    ).to_dict()

    assert policy["expected_language"] == "en"
    assert check_response_language("Use an index to reduce the scan.", policy).compliant
    assert not check_response_language("使用索引可以减少扫描。", policy).compliant


def test_language_policy_allows_translation_and_short_or_mixed_inputs() -> None:
    assert response_language_policy_for_text("translate this to Japanese").expected_language == "none"
    assert response_language_policy_for_text("hello").expected_language == "none"
    assert response_language_policy_for_text("こんにちは friend").expected_language == "none"
