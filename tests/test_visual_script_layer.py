from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.creative_director import build_director_system_prompt, detect_creative_brief
from core.prompt_examples import EXAMPLES
from core.prompt_normalizer import _build_conversational_request
from core.prompt_templates import (
    IMAGE_BLOCK,
    VIDEO_BLOCK,
    blank_template,
    render_block,
)
from core.visual_playbooks import (
    VISUAL_PLAYBOOKS,
    detect_visual_genre,
    visual_playbook_directive,
)


@pytest.mark.parametrize(
    "text, genre",
    [
        ("a dragon and a sorceress casting a spell on a bridge", "fantasy"),
        ("neon hacker in a rainy megacity at night", "cyberpunk"),
        ("a lion hunting on the savanna at dawn", "nature_wildlife"),
        ("a luxury watch product shot on marble", "product_ad"),
        ("an astronaut drifting near a nebula", "space_cosmos"),
        ("a haunted lighthouse, creepy and eerie", "horror"),
    ],
)
def test_detect_visual_genre(text: str, genre: str) -> None:
    assert detect_visual_genre(text) == genre


def test_detect_visual_genre_none_on_plain_text() -> None:
    assert detect_visual_genre("what is the capital of France?") is None


def test_playbooks_are_complete() -> None:
    assert len(VISUAL_PLAYBOOKS) == 22
    for pb in VISUAL_PLAYBOOKS.values():
        assert pb.camera_language.strip()
        assert pb.negative_prompt.strip()
        assert pb.visual_motifs and pb.strong_vocabulary


def test_visual_directive_is_generation_aware() -> None:
    d = visual_playbook_directive("horror")
    assert "Visual grammar for" in d
    assert "Camera:" in d
    assert "Negative prompt (always avoid):" in d
    assert visual_playbook_directive(None) == ""
    assert visual_playbook_directive("not_a_genre") == ""


def test_prompt_templates_have_expected_fields() -> None:
    image_fields = {f.name for f in IMAGE_BLOCK}
    video_fields = {f.name for f in VIDEO_BLOCK}
    assert "Subject" in image_fields and "Negative prompt" in image_fields
    assert "Scene" in video_fields and "Final generation prompt" in video_fields
    assert every_field_has_guidance(IMAGE_BLOCK) and every_field_has_guidance(VIDEO_BLOCK)


def every_field_has_guidance(block) -> bool:
    return all(f.guidance.strip() for f in block)


def test_render_block_skips_empty_and_orders() -> None:
    blank = blank_template("video")
    assert blank.startswith("Scene:")
    rendered = render_block({"Scene": "a ruined bridge", "Camera": "slow push-in"}, kind="video")
    assert "Scene: a ruined bridge" in rendered
    assert "Camera: slow push-in" in rendered
    assert "Character:" not in rendered  # empty fields dropped


def test_example_library_is_usable() -> None:
    assert len(EXAMPLES) == 22
    for ex in EXAMPLES:
        assert ex["elite"].strip() and ex["negative"].strip()
        assert ex["medium"] in {"image", "video"}


def _director_prompt(user_text: str) -> str:
    persona = SimpleNamespace(display_name="VOOL", tone="calm", persona_id="default")
    context_result = SimpleNamespace(report=SimpleNamespace(to_dict=lambda: {}))
    req = _build_conversational_request(
        user_text=user_text,
        persona=persona,
        classification={"task_class": "chat_conversation"},
        context_result=context_result,
        task_kind="conversation",
        output_mode="conversational",
        trace_id="t",
        ambiguity=0.8,
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )
    return req.messages[0].content


def test_media_brief_folds_in_visual_grammar() -> None:
    system = _director_prompt("make a cinematic video prompt of a neon hacker in a rainy megacity")
    assert "prompt director" in system  # still the director prompt
    assert "Visual grammar for" in system  # visual playbook folded in
    assert "Negative prompt (always avoid):" in system  # generation-aware guardrails


def test_build_director_accepts_visual_directive() -> None:
    brief = detect_creative_brief("make an image prompt of a castle")
    out = build_director_system_prompt(brief, visual_directive=" INJECTED_VISUAL_GRAMMAR")
    assert out.endswith(" INJECTED_VISUAL_GRAMMAR")
