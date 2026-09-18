from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.creative_director import (
    CREATIVE_DIRECTOR_PROFILE,
    CreativeBrief,
    build_director_system_prompt,
    detect_creative_brief,
    detect_prose_request,
)
from core.prompt_normalizer import _build_conversational_request
from core.writing_craft import GENRE_CRAFT, detect_genre

# A long pasted "script" with no media/enhance/want cue and no code — exercises rule R5.
_PASTED_SCRIPT = (
    "Here is the opening script. A ruined marble bridge hangs above an endless storm valley, "
    "its broken arches wrapped in glowing blue vines and drifting golden ash. Far below, clouds "
    "churn like a black ocean, flashing with silent violet lightning. On the bridge stands a young "
    "sorceress in a torn silver cloak, breathing hard, one hand gripping a cracked crystal staff."
)


@pytest.mark.parametrize(
    "text, medium",
    [
        ("make a cinematic video prompt of a dragon and a sorceress on a bridge", "video"),
        ("detailed image prompt of a neon cyberpunk city at night", "image"),
        ("prompt: a lone astronaut standing on a red desert planet", "video"),
        ("image: a snarling wolf under a blood moon", "image"),
        ("video: a girl and a dragon on a collapsing bridge", "video"),
        ("I want a cinematic scene of a dragon and a girl on a bridge", "video"),
        ("can you expand this prompt and make it super detailed: a knight in a storm", "video"),
        ("a picture of a fox in a snowy forest, make it super detailed", "image"),
        ("write a veo prompt: a whale swimming through a city of clouds", "video"),
        (_PASTED_SCRIPT, "video"),
    ],
)
def test_detect_fires_and_resolves_medium(text: str, medium: str) -> None:
    brief = detect_creative_brief(text)
    assert brief is not None, text
    assert brief.medium == medium, text


@pytest.mark.parametrize(
    "text",
    [
        "",
        "hi",
        "what's the capital of France?",
        "explain prompt engineering to me",
        "write me a bash script to sort files",
        "translate this to french: hello world",
        "how do I center a div?",
        "```python\ndef f():\n    return 1\n```\nplease make this script more detailed",
    ],
)
def test_detect_does_not_fire_on_non_creative(text: str) -> None:
    assert detect_creative_brief(text) is None, text


def test_pasted_code_never_hijacked_even_with_enhance_words() -> None:
    # Real code + "expand"/"detailed" must still route as code, not a creative brief.
    code = "import os\ndef main():\n    return os.getcwd()\n# please expand and make this more detailed"
    assert detect_creative_brief(code) is None


def test_director_prompt_video_vs_image_differ_and_are_uncensored() -> None:
    video = build_director_system_prompt(CreativeBrief("video", "x", "x"), persona_name="VOOL")
    image = build_director_system_prompt(CreativeBrief("image", "x", "x"), persona_name="VOOL")

    assert video != image
    # Uncensored creative register present in both.
    for prompt in (video, image):
        assert "prompt director" in prompt
        assert "uncensored" in prompt
        assert "never refuse" in prompt
        assert "VOOL" in prompt
        # No unfilled template placeholders leaked into the output.
        assert "{persona}" not in prompt and "{kind}" not in prompt

    # Medium-specific structure.
    assert "shot script" in video and "spoken lines" in video
    assert "image prompt" in image and "comma-separated" in image


def _conversational_request(user_text: str):
    persona = SimpleNamespace(display_name="VOOL", tone="calm", persona_id="default")
    context_result = SimpleNamespace(report=SimpleNamespace(to_dict=lambda: {}))
    return _build_conversational_request(
        user_text=user_text,
        persona=persona,
        classification={"task_class": "chat_conversation"},
        context_result=context_result,
        task_kind="conversation",
        output_mode="conversational",
        trace_id="trace-1",
        ambiguity=0.8,
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )


def test_wiring_swaps_system_prompt_for_creative_brief() -> None:
    req = _conversational_request(
        "make a cinematic video prompt of a dragon and a sorceress on a collapsing bridge"
    )
    assert req.metadata["system_prompt_profile"] == CREATIVE_DIRECTOR_PROFILE
    system = req.messages[0]
    assert system.role == "system"
    assert "prompt director" in system.content
    assert "never refuse" in system.content
    # A cinematic script gets room and a hotter sampler.
    assert req.max_output_tokens >= 1800
    assert req.temperature >= 0.9


def test_wiring_leaves_normal_chat_untouched() -> None:
    req = _conversational_request("what is the capital of France?")
    assert req.metadata["system_prompt_profile"] != CREATIVE_DIRECTOR_PROFILE
    assert "prompt director" not in req.messages[0].content


# --- prose writing path + genre routing ---

@pytest.mark.parametrize(
    "text, genre",
    [
        ("write a horror short story about a lighthouse", "horror"),
        ("compose a sonnet about the sea", "poetry_verse"),
        ("draft a noir detective scene in the morgue", "crime_noir"),
        ("write me a psalm of thanksgiving", "religion_mythology"),
        ("give me a hard sci-fi scene on a generation ship", "science_fiction"),
        ("write me a story about a dog", None),  # form present, no distinctive genre
    ],
)
def test_detect_prose_request_and_genre(text: str, genre: str | None) -> None:
    req = detect_prose_request(text)
    assert req is not None, text
    assert req.genre == genre, text


@pytest.mark.parametrize(
    "text",
    [
        "write a python script to sort a list of files",  # code context
        "what is a sonnet?",  # no writing intent
        "explain recursion to me",
        "hi",
    ],
)
def test_detect_prose_request_does_not_fire(text: str) -> None:
    assert detect_prose_request(text) is None, text


def test_wiring_prose_request_uses_genre_craft() -> None:
    req = _conversational_request("write a horror short story about an old lighthouse keeper")
    assert req.metadata["system_prompt_profile"] == CREATIVE_DIRECTOR_PROFILE
    assert req.metadata["chat_truth_prompt"]["creative_genre"] == "horror"
    system = req.messages[0].content
    # Craft core + genre craft + uncensored clause all present.
    assert "professional" in system
    assert "never refuse" in system
    assert GENRE_CRAFT["horror"].craft_directive[:30] in system
    assert req.max_output_tokens >= 1800


def test_media_prompt_folds_in_detected_genre() -> None:
    # A horror-flavored video prompt should carry the horror register into the director prompt.
    brief = detect_creative_brief("make a cinematic horror video prompt of a haunted lighthouse")
    assert brief is not None and brief.medium == "video"
    genre = detect_genre("make a cinematic horror video prompt of a haunted lighthouse")
    assert genre == "horror"
    system = build_director_system_prompt(brief, genre_key=genre)
    assert "Bring the craft of" in system
    assert "shot script" in system  # still the video director spec


# --- polished communication (the daily-PA transfer) ---

@pytest.mark.parametrize(
    "text",
    [
        "draft an email to the team about the launch",
        "write a reply to this email",
        "write a linkedin post about our release",
        "draft an investor update for Q3",
        "polish this email before I send it",
    ],
)
def test_detect_comms_request_fires(text: str) -> None:
    from core.creative_director import detect_comms_request
    assert detect_comms_request(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "send an email to bob",                          # a tool action, not drafting
        "what is email marketing",                       # no write intent
        "write a python script for email validation",    # code context
        "write a story about a dog",                     # fiction, not comms
        "hi",
    ],
)
def test_detect_comms_request_does_not_fire(text: str) -> None:
    from core.creative_director import detect_comms_request
    assert detect_comms_request(text) is False


def test_polished_prompt_uses_craft_core_not_uncensored() -> None:
    from core.writing_craft import build_polished_writing_prompt
    p = build_polished_writing_prompt()
    assert "professional communicator" in p
    assert "strong, purposeful close" in p
    assert "never refuse" not in p  # not the uncensored-creative clause


def test_wiring_comms_routes_to_polished_and_stays_professional() -> None:
    comms = _conversational_request("draft an email to the team about the launch")
    assert comms.metadata["system_prompt_profile"] == CREATIVE_DIRECTOR_PROFILE
    assert comms.metadata["chat_truth_prompt"]["creative_comms"] is True
    assert "professional communicator" in comms.messages[0].content
    # comms stays at the professional temperature; a fiction turn gets the hotter creative sampler.
    fiction = _conversational_request("write a horror short story about a lighthouse")
    assert comms.temperature < fiction.temperature


def test_comms_does_not_hijack_a_tool_turn() -> None:
    # A structured/tool output_mode must not be captured by comms routing (email.send stays a tool).
    persona = SimpleNamespace(display_name="VOOL", tone="calm", persona_id="default")
    context_result = SimpleNamespace(report=SimpleNamespace(to_dict=lambda: {}))
    req = _build_conversational_request(
        user_text="draft an email to the team about the launch",
        persona=persona,
        classification={"task_class": "chat_conversation"},
        context_result=context_result,
        task_kind="conversation",
        output_mode="tool_intent",
        trace_id="t",
        ambiguity=0.8,
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )
    assert req.metadata["chat_truth_prompt"]["creative_comms"] is False
