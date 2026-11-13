"""A long message reaches the model once, not once as typed and again as its retained document.

A message of DOCUMENT_THRESHOLD_CHARS or more is retained as a pasted document
(`chat_attachments.retain_inline_message`) so later turns of the chat can read it. On its own turn
that document was also rendered onto the same user message, so the paste travelled twice on every
model call of the turn. Measured in the skills A/B round 7: 20,489 characters of request for a
6,260-byte paste. Measured served on bde8106c with a 6,312-byte paste ("Turn this into an X
article:" + the article, x-article-builder installed): two copies in both calls, 24,549 and 19,455
characters.
"""
from __future__ import annotations

import pytest

from core import chat_attachments as ca
from core import runtime_paths

SESSION = "openclaw:paste-once-aaaaaaaaaaaaaa"
ARTICLE = "".join(
    f"Week {i}: neighbours borrowed drills and ladders from the Alder Row tool library; the deposit stayed at 10 euros.\n"
    for i in range(80)
)
MESSAGE = "Turn this into an X article:\n\n" + ARTICLE


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    yield
    runtime_paths.configure_runtime_home(None)


def _turn_entries(turn_id: str) -> list[dict]:
    return ca.model_attachments_from_source_context({"runtime_session_id": SESSION, "attachment_turn_id": turn_id})


def _rendered(user_text: str, entries: list[dict]) -> tuple[str, list[dict]]:
    messages, receipts = ca.apply_to_provider_messages(
        [{"role": "user", "content": user_text}], entries, supports_images=False
    )
    content = messages[-1]["content"]
    text = "\n".join(p.get("text", "") for p in content) if isinstance(content, list) else str(content)
    return text, receipts


def test_the_pasted_message_travels_once_on_its_own_turn() -> None:
    assert len(MESSAGE) >= ca.DOCUMENT_THRESHOLD_CHARS
    assert ca.retain_inline_message(session_id=SESSION, turn_id="turn-1", text=MESSAGE) is not None
    text, receipts = _rendered(MESSAGE, _turn_entries("turn-1"))
    assert text.count("Week 41: neighbours borrowed") == 1
    assert "[Pasted document:" not in text
    assert text.startswith("Turn this into an X article:")
    assert [r["reason"] for r in receipts] == ["in_the_message"]


def test_a_message_that_does_not_hold_the_document_still_gets_it() -> None:
    # A planned sub-turn asks about the paste in its own words: the document is its only copy.
    ca.retain_inline_message(session_id=SESSION, turn_id="turn-1", text=MESSAGE)
    text, receipts = _rendered("Write the headline for the X article.", _turn_entries("turn-1"))
    assert "[Pasted document:" in text and text.count("Week 41: neighbours borrowed") == 1
    assert [r["reason"] for r in receipts] == ["inlined"]


def test_a_later_turn_is_still_handed_the_paste_as_earlier_material() -> None:
    ca.retain_inline_message(session_id=SESSION, turn_id="turn-1", text=MESSAGE)
    ca.release_turn(session_id=SESSION, turn_id="turn-1", outcomes=None)
    text, receipts = _rendered("make it shorter", _turn_entries("turn-2"))
    assert "[Document pasted earlier in this chat:" in text and "Week 41: neighbours borrowed" in text
    assert receipts[0]["reason"] == "carried"
