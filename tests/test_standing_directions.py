from __future__ import annotations

from core.fact_extractor import FactExtractor, _standing_direction_facts
from core.memory_prompt_builder import MemoryPromptBuilder
from core.vool_memory import VoolMemory


def _extractor(memory: VoolMemory) -> FactExtractor:
    # Empty model_client isolates the rule-based capture (no tiny-LLM facts).
    return FactExtractor(memory=memory, model_client=lambda _c: "")


def test_from_now_on_captures_standing_preference(tmp_path) -> None:
    memory = VoolMemory(runtime_home=tmp_path)
    _extractor(memory).run_sync(
        [{"role": "user", "content": "from now on keep replies under three sentences"}]
    )
    assert "under three sentences" in str(memory.block_read("preferences") or "").lower()
    memory.close()


def test_always_and_never_route_to_the_right_blocks(tmp_path) -> None:
    memory = VoolMemory(runtime_home=tmp_path)
    _extractor(memory).run_sync(
        [{"role": "user", "content": "always use British spelling. never use emojis in replies."}]
    )
    prefs = str(memory.block_read("preferences") or "")
    cons = str(memory.block_read("constraints") or "")
    assert "British spelling" in prefs
    assert "Never use emojis" in cons
    memory.close()


def test_avoid_and_dont_go_to_constraints(tmp_path) -> None:
    memory = VoolMemory(runtime_home=tmp_path)
    _extractor(memory).run_sync(
        [{"role": "user", "content": "avoid corporate jargon and don't use bullet points"}]
    )
    cons = str(memory.block_read("constraints") or "")
    assert "Avoid corporate jargon" in cons
    assert "Never use bullet points" in cons
    memory.close()


def test_signature_style_direction_captured(tmp_path) -> None:
    memory = VoolMemory(runtime_home=tmp_path)
    _extractor(memory).run_sync(
        [{"role": "user", "content": "always sign my emails as Parad0x Labs"}]
    )
    assert "sign my emails as Parad0x Labs" in str(memory.block_read("preferences") or "")
    memory.close()


def test_non_directive_always_is_not_captured() -> None:
    # "I always struggle with regex" is not a standing instruction (no action verb after 'always').
    assert _standing_direction_facts("I always struggle with regex and forget the syntax") == []


def test_questions_are_not_captured(tmp_path) -> None:
    memory = VoolMemory(runtime_home=tmp_path)
    _extractor(memory).run_sync([{"role": "user", "content": "should I always use tabs over spaces?"}])
    assert not str(memory.block_read("preferences") or "").strip()
    assert not str(memory.block_read("constraints") or "").strip()
    memory.close()


def test_from_now_on_prohibition_routes_to_constraints() -> None:
    facts = _standing_direction_facts("from now on never mention pricing")
    assert ("constraints", "Never mention pricing") in {(f.block, f.content) for f in facts}


def test_memory_prefix_quarantines_unscoped_standing_directions(tmp_path) -> None:
    memory = VoolMemory(runtime_home=tmp_path)
    memory.block_append("preferences", "Always use British spelling")
    prefix = MemoryPromptBuilder(memory).build_prefix(query="hello")
    assert prefix == ""
    assert "Always use British spelling" in str(memory.block_read("preferences") or "")
    memory.close()
