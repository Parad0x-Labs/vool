"""A told date that the file-find question confirms, beside a keep reading, is kept when the message tells a fact.

iMac gate59 on 033c90daa (qwen3:8b): "the garage moved my MOT to the 19th at quarter past eight" was picked Find, its
own question confirmed it, and the reply was the machine's specs; a neighbour's birthday told in Lithuanian listed a
Desktop that was not there. Nothing was kept. The memory question read both as a keep: the two readings disagree, and
the told-as-fact question settles it, as for Reminders and Calendar. A real find ask (told reads it no) keeps Find.
"""
from __future__ import annotations

import pytest

from core import intent_arbiter, plugin_ask


@pytest.fixture
def judged(monkeypatch):
    def setup(*, kind: str, told: str | None):
        monkeypatch.setattr(intent_arbiter, "arbitrate_memory_kind", lambda *_a, **_k: kind)
        monkeypatch.setattr(intent_arbiter, "arbitrate_told_as_fact", lambda *_a, **_k: told)
        monkeypatch.setattr(intent_arbiter, "arbitrate_worth_recalling", lambda *_a, **_k: "yes")
        monkeypatch.setattr(intent_arbiter, "arbitrate_forget_order", lambda *_a, **_k: "no")
        monkeypatch.setattr(plugin_ask, "_has_pending_reminders", lambda: False)
        monkeypatch.setattr(plugin_ask, "_continues_a_contact_save", lambda *_a, **_k: False)

    return setup


@pytest.mark.parametrize("pick", [("find",), ("read",), ("find", "read")])
@pytest.mark.parametrize(
    "text",
    ["the garage moved my MOT to the 19th at quarter past eight", "beje, kaimynės Rūtos jubiliejus bus rugpjūčio 2 d."],
)
def test_a_told_fact_picked_as_a_file_look_up_is_kept(text, pick, judged):
    judged(kind="keep", told="yes")
    assert plugin_ask._memory_kind_confirmed(text, pick, {}, "chat-1") == ("memory_keep",)


@pytest.mark.parametrize("told", ["no", None])
def test_a_find_ask_keeps_its_find(told, judged):
    judged(kind="keep", told=told)
    assert plugin_ask._memory_kind_confirmed("find the scan of my lease, I think I kept it", ("find",), {}, "c") == ("find",)


def test_a_find_without_a_keep_reading_is_never_asked_the_told_question(judged, monkeypatch):
    judged(kind="none", told="yes")
    asked: list[str] = []
    monkeypatch.setattr(intent_arbiter, "arbitrate_told_as_fact", lambda *_a, **_k: asked.append("told") or "yes")
    assert plugin_ask._memory_kind_confirmed("where is my kettle warranty pdf", ("find",), {}, "c") == ("find",)
    assert asked == []
