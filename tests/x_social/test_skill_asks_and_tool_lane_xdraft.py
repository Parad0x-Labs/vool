"""Two ways a person asks a skill that the real-ask rule (R3) did not hear, and one edge the tool lane skipped.

Found while fixing the X social reds on the v0.8 line:

1. "Use press-pr-manager to research ..." is an imperative, but "use" was not one of the ask
   verbs, so naming a skill this way seated nothing.
2. A typed rating that opens the turn ("9/10 \"draft\" ...", the grammar core.x_feedback reads)
   was not an ask either, so the rating tool was never offered for it.
3. When a matched skill hands an X editorial turn to the tool lane, the draft arrives as
   respond.direct and skipped the XDraft validation the chat lane runs (C20). The served proof is
   tests/x_social/test_editorial_matrix.py[installed]; here the loop's own edge is pinned.

Narration stays narration: "I use ..." and a score inside a sentence are still not asks.
"""
from __future__ import annotations

import json

import pytest

from core.tool_offer_assembly import _turn_asks_for_something


@pytest.mark.parametrize("text", [
    "Use press-pr-manager to research recent coverage of checkpoint recovery tools for an Orchard pitch.",
    "use the meeting-follow-up skill on these notes",
    '9/10 "A concrete checkpoint note." orchard social-manager',
    'Rating: 7/10 "Our tea brews in two minutes."',
    'I rate it 8/10 "Short and clear."',
])
def test_these_turns_ask(text: str) -> None:
    assert _turn_asks_for_something(text)


@pytest.mark.parametrize("text", [
    "I use press-pr-manager every week for our pitches",
    "We used social-manager last month",
    "my last post got 9/10 from the team",
    "the 10/10 review went out this morning",
])
def test_narration_is_still_not_an_ask(text: str) -> None:
    assert not _turn_asks_for_something(text)


class _Loop:
    """The facade method alone, with its event sink captured."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def _emit_runtime_event(self, source_context, *, event_type, message, **details):
        self.events.append({"event_type": event_type, **details})


def _method():
    from core.agent_runtime.research_tool_loop_facade import ResearchToolLoopFacadeMixin

    return ResearchToolLoopFacadeMixin._x_editorial_direct_message


def test_a_tool_lane_xdraft_is_validated_and_served_as_its_copy(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    draft = {"mode": "post", "audience": "office workers", "purpose": "show a simple tea break",
             "thesis": "tea fits a short break", "title": "", "hook": "",
             "posts": ["Our tea brews in two minutes."], "sections": [],
             "media_notes": [], "links": [], "cta": ""}
    envelope = "===XDRAFT===\n" + json.dumps(draft) + "\n===XDRAFT-END==="
    loop = _Loop()
    out = _method()(loop, envelope, user_text="Write an X post from these notes: Our tea brews in two minutes.",
                    source_context={"surface": "desktop"})
    assert out.strip() == "Our tea brews in two minutes."
    assert [e["event_type"] for e in loop.events] == ["x_editorial_validation"]
    assert loop.events[0]["clean"] is True


def test_an_unsupported_number_in_a_tool_lane_xdraft_is_withheld(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    draft = {"mode": "post", "audience": "office workers", "purpose": "tea", "thesis": "tea",
             "title": "", "hook": "", "posts": ["Our tea cut stress by 87% for 900000 people."],
             "sections": [], "media_notes": [], "links": [], "cta": ""}
    envelope = "===XDRAFT===\n" + json.dumps(draft) + "\n===XDRAFT-END==="
    loop = _Loop()
    out = _method()(loop, envelope, user_text="Write an X post about our tea.", source_context={})
    # The same withheld rendering the chat lane serves: the findings are named, no clean copy.
    assert "copy-ready withheld" in out
    assert loop.events and loop.events[0]["clean"] is False


def test_a_reply_without_an_envelope_is_untouched() -> None:
    loop = _Loop()
    assert _method()(loop, "Here is your plan.", user_text="plan", source_context={}) == "Here is your plan."
    assert loop.events == []
