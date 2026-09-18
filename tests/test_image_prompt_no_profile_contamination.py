"""An image-generation prompt describes the RENDER SUBJECT, not the user — it must never be
harvested into the user profile / heuristics / session summaries. This is the fix for the
confabulation loop where an image prompt ("Rick and Morty ... 4K") became a "standing user fact"
and the local model invented the user's name from it.
"""
from __future__ import annotations

from core import persistent_memory
from core.bootstrap_context import _continuity_lines
from core.memory.files import session_summaries_path, user_heuristics_path

_IMG = "generate a colourful 4K picture of Rick and Morty cartoon characters at a stock exchange"
_SID = "openclaw:imgcontamtest0001x"


def _harvested_text() -> str:
    parts = []
    for path in (user_heuristics_path(), session_summaries_path()):
        if path.exists():
            parts.append(path.read_text(encoding="utf-8"))
    return " ".join(parts).lower()


def test_image_prompt_is_not_harvested_as_a_user_fact():
    persistent_memory.append_conversation_event(
        session_id=_SID, user_input=_IMG, assistant_output="Here's your local render.", source_context={},
    )
    text = _harvested_text()
    assert "rick" not in text and "morty" not in text, "image subject leaked into the user profile"


def test_a_normal_turn_still_harvests(monkeypatch):
    # Contrast: a genuine preference statement DOES get harvested, so the guard is image-specific.
    persistent_memory.append_conversation_event(
        session_id=_SID, user_input="i always want you to use strong github repos as references",
        assistant_output="Understood.", source_context={},
    )
    assert "github" in _harvested_text()


def test_owner_name_anchor_grounds_or_forbids_guessing(monkeypatch):
    from tests.operator_profile_rig import set_owner_preferred_name

    # The anchor reads the saved name through core.user_identity_authority, which resolves it
    # from the Operator Profile -- the one authority for it.
    # with a configured handle -> address as that, ignore names in image prompts
    set_owner_preferred_name("LOOP")
    try:
        line = _continuity_lines({})[0]
        assert 'Address the user as "LOOP"' in line and "image prompts" in line
        # with no handle -> explicitly forbid inventing one
        set_owner_preferred_name("")
        line2 = _continuity_lines({})[0]
        assert "not known" in line2 and "Do NOT invent" in line2
    finally:
        set_owner_preferred_name("")
