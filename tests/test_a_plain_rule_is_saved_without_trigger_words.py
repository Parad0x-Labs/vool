"""Standing instructions said plainly: no "always", no "from now on", any verb.

Two causes, both in how a sentence was judged to be addressed to VOOL:
- the main clause had to start with one of ~40 listed verbs, so "Whenever I paste a stack trace, point out the line
  that failed first" was dropped ("point" was not listed), while "Whenever I send you code, …" passed only because
  "send" inside the condition happened to be listed;
- a rule had to carry a trigger word, so "Keep your answers under five sentences." or "Before you delete anything,
  ask me." said on its own was never saved.
A plain rule is saved only when the whole turn is instructions (with an optional reason about the owner): beside a
task or a question it is about that task. Authored wording only.
"""
from __future__ import annotations

import pytest

from core import standing_instructions as si

OWNER = {"surface": "chat", "_owner_local": True, "workspace_root": "/srv/projects/orchard"}


@pytest.fixture(autouse=True)
def _store(tmp_path, monkeypatch):
    monkeypatch.setattr(si, "_store_path", lambda: tmp_path / "standing_instructions.json")


def _say(text):
    return si.observe_turn(text, OWNER, principal="owner_local", session_id="openclaw:" + "7" * 20)


@pytest.mark.parametrize("text", [
    "Whenever I paste a stack trace, point out the line that failed first.",
    "Whenever you are unsure, say so.",
    "Whenever we talk about money, assume euros.",
    "Every time I share a draft, flag the weakest paragraph.",
    "From now on, double-check any arithmetic before you answer.",
    "Going forward, summarise long documents in three bullets.",
])
def test_a_standing_sentence_with_any_verb_is_saved(text):
    assert _say(text)["saved"] == [text]


@pytest.mark.parametrize("text", [
    "Keep your answers under five sentences.",
    "Use metric units.",
    "Write dates as day, month, year.",
    "Please skip the pleasantries and get to the point.",
    "I prefer bullet points over long paragraphs.",
    "I like my answers short and plain.",
])
def test_a_plain_rule_said_on_its_own_is_saved(text):
    assert _say(text)["saved"] == [text]


@pytest.mark.parametrize("text", [
    "Before you delete anything, ask me.",
    "Before running a migration, show me the plan.",
    "Ask me before you send any email.",
    "Check with me before you install a package.",
    "After you change a file, run the tests.",
])
def test_a_workflow_rule_is_saved(text):
    assert _say(text)["saved"] == [text]


def test_a_plain_rule_keeps_its_reason():
    text = "I read on a small phone screen. Keep answers under five sentences."
    assert _say(text)["saved"] == [text]


# ------------------------------------------------------------------ over-learning: nothing is saved
@pytest.mark.parametrize("text", [
    "What's the tallest mountain in Chile? Keep it short.",
    "Convert 3 cups of flour to grams. Use metric units.",
    "Use metric units for this recipe.",
    "Summarise the attached report in bullet points.",
    "Before you send it, show me the draft.",
    "Before you answer this one, check today's date.",
    "Tables always break on my phone.",
    "My manager prefers bullet points.",
    "Being concise matters in emails.",
    "Whenever it rains, the trains are late.",
    "Whenever my brother visits, we cook pasta.",
    "Should I use metric units?",
    "Keep it short this time.",
])
def test_a_task_a_question_a_one_off_or_a_statement_saves_nothing(text):
    assert _say(text)["saved"] == []
