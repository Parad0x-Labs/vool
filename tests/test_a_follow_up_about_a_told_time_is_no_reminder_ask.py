"""A follow-up that asks again about a told fact is a recall, not a reminder ask, when the recall judge names the fact.

iMac gate59 on 033c90daa (qwen3:8b): after "when is my brother's thesis defence?" was answered from memory, "and at
what time again?" was picked Reminders, its own question read yes, the memory question read recall, and the turn
ended at an agenda approval card. The recall judge named nothing for the bare follow-up; read beside the message
before it, it names the defence. Wordings invented for this contract.
"""
from __future__ import annotations

import pytest

from core import intent_arbiter, plugin_ask

EARLIER = "when is my cousin's graduation again?"
CONTEXT = {"conversation_history": [{"role": "user", "content": EARLIER}, {"role": "assistant", "content": "On 4 June."}]}


@pytest.fixture
def judged(monkeypatch):
    seen: list[str] = []

    def setup(*, names_with_earlier: str, pending: bool = False):
        monkeypatch.setattr(intent_arbiter, "arbitrate_memory_kind", lambda *_a, **_k: "recall")
        monkeypatch.setattr(intent_arbiter, "arbitrate_reminder_ask", lambda *_a, **_k: "yes")
        monkeypatch.setattr(intent_arbiter, "arbitrate_told_as_fact", lambda *_a, **_k: "no")
        monkeypatch.setattr(intent_arbiter, "arbitrate_forget_order", lambda *_a, **_k: "no")
        monkeypatch.setattr(plugin_ask, "_has_pending_reminders", lambda: pending)
        monkeypatch.setattr(plugin_ask, "_judged_a_clock_ask", lambda _t: False)

        def names(text, _ctx, _sid):
            seen.append(text)
            return names_with_earlier if EARLIER in text else ""

        monkeypatch.setattr(plugin_ask, "_recall_names", names)
        return seen

    return setup


@pytest.mark.parametrize("pending", [False, True])
@pytest.mark.parametrize("text", ["and at what time again?", "o kelintą valandą?", "und um wie viel Uhr?"])
def test_a_follow_up_naming_a_told_fact_goes_to_memory(text, pending, judged):
    seen = judged(names_with_earlier="fact", pending=pending)
    assert plugin_ask._memory_kind_confirmed(text, ("reminders",), dict(CONTEXT), "chat") == ("memory_recall",)
    assert seen[0] == text  # the message alone is read first


@pytest.mark.parametrize("names", ["reminder", ""])
def test_a_follow_up_about_a_reminder_or_nothing_held_keeps_the_reminders(names, judged):
    judged(names_with_earlier=names)
    got = plugin_ask._memory_kind_confirmed("and at what time again?", ("reminders",), dict(CONTEXT), "chat")
    assert got[0] == "reminders"


@pytest.mark.parametrize("names", ["fact", ""])
def test_a_calendar_pick_on_such_a_follow_up_is_settled_the_same_way(names, judged, monkeypatch):
    """iMac gate60 on a7b1b9e5e (qwen3:8b): "and what time again?" after a recalled dentist check-up was picked Calendar
    and answered "No calendar is configured"."""
    judged(names_with_earlier=names)
    monkeypatch.setattr(intent_arbiter, "arbitrate_calendar_ask", lambda *_a, **_k: "yes")
    got = plugin_ask._memory_kind_confirmed("and what time again?", ("calendar",), dict(CONTEXT), "chat")
    assert got == (("memory_recall",) if names == "fact" else ("calendar", "memory_recall"))
