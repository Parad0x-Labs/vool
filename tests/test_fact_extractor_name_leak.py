from __future__ import annotations

from core.fact_extractor import FACT_EXTRACT_SYSTEM, ExtractedFact, FactExtractor, _fact_grounded
from core.vool_memory import VoolMemory


def test_prompt_example_is_not_a_literal_name() -> None:
    # The few-shot format example used a literal "Name: Loop" that the tiny extractor model copied
    # into essentially every user's profile.
    assert "Name: Loop" not in FACT_EXTRACT_SYSTEM
    low = FACT_EXTRACT_SYSTEM.lower()
    assert "never copy" in low  # anti-copy rule for the placeholders
    assert "never invent or assume a name" in low


def test_a_name_fact_is_admitted_only_when_it_agrees_with_the_profile() -> None:
    """The Operator Profile is the ONE name authority. A model-harvested "Name:" line is admitted
    only when it agrees with the profile's name; with no profile name it is refused even when the
    user said it -- the stated name becomes a profile candidate the user confirms instead."""
    from tests.operator_profile_rig import set_owner_preferred_name

    stated = ExtractedFact(action="ADD", block="user_profile", content="Name: Ada")
    dropped = ExtractedFact(action="ADD", block="user_profile", content="Name: Loop")
    set_owner_preferred_name("")
    try:
        assert not _fact_grounded(stated, "hi, my name is ada")
        assert not _fact_grounded(dropped, "what is the weather today")
        set_owner_preferred_name("Ada")
        assert _fact_grounded(stated, "hi, my name is ada")
        assert not _fact_grounded(dropped, "hi, my name is ada")
    finally:
        set_owner_preferred_name("")
    # non-name profile facts and other blocks are unaffected
    assert _fact_grounded(ExtractedFact(action="ADD", block="preferences", content="Answer style: blunt"), "")
    assert _fact_grounded(ExtractedFact(action="ADD", block="user_profile", content="Role: engineer"), "")


def test_extractor_drops_leaked_name_when_user_never_said_it(tmp_path) -> None:
    memory = VoolMemory(runtime_home=tmp_path)
    extractor = FactExtractor(
        memory=memory,
        model_client=lambda _c: '{"facts":[{"action":"ADD","block":"user_profile","content":"Name: Loop"}]}',
    )
    applied = extractor.run_sync(
        [
            {"role": "user", "content": "what is 2 + 2?"},
            {"role": "assistant", "content": "4."},
        ]
    )
    assert ("user_profile", "Name: Loop") not in {(f.block, f.content) for f in applied}
    assert not str(memory.block_read("user_profile") or "")
    memory.close()


def test_extractor_never_writes_a_name_the_profile_does_not_hold(tmp_path) -> None:
    from tests.operator_profile_rig import set_owner_preferred_name

    set_owner_preferred_name("")
    try:
        memory = VoolMemory(runtime_home=tmp_path)
        extractor = FactExtractor(
            memory=memory,
            model_client=lambda _c: '{"facts":[{"action":"ADD","block":"user_profile","content":"Name: Ada"}]}',
        )
        extractor.run_sync([{"role": "user", "content": "hey, my name is Ada"}])
        assert "Ada" not in str(memory.block_read("user_profile") or "")
        memory.close()
    finally:
        set_owner_preferred_name("")
