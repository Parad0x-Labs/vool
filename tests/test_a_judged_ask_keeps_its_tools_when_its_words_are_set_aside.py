"""A message the turn's judge read as asking for one of VOOL's own tools keeps that tool, even when the words readers
set part of it aside as reported speech or a quoted example.

CI on 412af1a79 (served stub, the judge scripted to read the reminder ask): "anything i asked you to nudge me about?"
had "i asked you to nudge me about" set aside as reported speech, the requirements and the tool gate read only
"anything", the reminder pick was never found, and the model was offered no tool. The judgement is keyed by the
message as written, so it is read for the message as written. Wordings and languages are drawn at random.
"""
from __future__ import annotations

import random

import pytest

from core import plugin_ask
from core.execution.planner import should_attempt_tool_intent
from core.execution_requirements import requirements_for
from core.request_trust import OWNER_LOCAL_KEY

OWNER = {OWNER_LOCAL_KEY: True, "surface": "openclaw", "platform": "openclaw", "session_id": "chat-set-aside"}
_ASKS = (
    "anything i asked you to nudge me about?",
    "is there something I asked you to remind me of?",
    "did I ask you to ping me about anything tomorrow?",
    "ar prašiau tavęs man ką nors priminti?",
    "hab ich dich gebeten, mich an etwas zu erinnern?",
)


@pytest.fixture(autouse=True)
def _clean():
    plugin_ask.forget()
    yield
    plugin_ask.forget()


@pytest.mark.parametrize("seed", range(5))
def test_a_judged_reminder_ask_opens_the_reminder_tools(seed):
    text = random.Random(f"set-aside-{seed}").choice(_ASKS)
    plugin_ask._record_builtins(text, ("reminders",))
    required = requirements_for(text, task_class="unknown", source_context=dict(OWNER))
    assert "operator" in required.allowed_toolsets and required.forbids_toolless_lane(), (text, required)
    assert should_attempt_tool_intent(text, task_class="unknown", source_context=dict(OWNER)) is True, text


def test_with_no_judgement_the_words_still_decide():
    # Nothing was judged: the set-aside words are not read as a tool ask on the judge's behalf.
    text = "anything i asked you to nudge me about?"
    required = requirements_for(text, task_class="unknown", source_context=dict(OWNER))
    assert "operator" not in required.allowed_toolsets, required


def test_a_remote_turn_gets_no_operator_tools_from_a_judgement():
    text = "anything i asked you to nudge me about?"
    plugin_ask._record_builtins(text, ("reminders",))
    remote = {OWNER_LOCAL_KEY: False, "surface": "api"}
    assert "operator" not in requirements_for(text, task_class="unknown", source_context=remote).allowed_toolsets
