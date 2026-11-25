"""Beside a picked tool, the memory question waits as long as the tool ask did, so a recall is not lost to a timeout.

Intel smoke of 174b25378 (qwen3:8b on a CPU): "What is Ilona's favourite tree?" was judged Reminders and "What is my
locker number?" Contacts by the tool judgement; the memory question beside each was cut at the usual 15 s and gave
nothing, so the tool stood alone. The first ended in an approval card, the second ran past 15 minutes. The run before,
the same question answered in 11 s and both were recalls. No model and no network.
"""
from __future__ import annotations

import pytest

import core.intent_arbiter as intent_arbiter
import core.plugin_ask as plugin_ask


@pytest.mark.parametrize("keys,patient", [(("reminders",), True), (("contacts", "find"), True), ((), False),
                                          (("memory_recall",), False)])
def test_the_memory_question_is_patient_only_beside_a_picked_tool(keys, patient, monkeypatch) -> None:
    seen: list[dict] = []

    def followup(*_a, **kw):
        seen.append(kw)
        return "recall"

    monkeypatch.setattr(intent_arbiter, "_keep_judge_model", lambda: "qwen3:8b")
    monkeypatch.setattr(intent_arbiter, "arbitrate_followup", followup)
    monkeypatch.setattr(intent_arbiter, "arbitrate_forget_order", lambda *_a, **_k: None)
    monkeypatch.setattr(intent_arbiter, "arbitrate_told_as_fact", lambda *_a, **_k: None)

    picked = plugin_ask._memory_kind_confirmed("What is Ilona's favourite tree?", keys, {}, "chat-1")

    assert seen and bool(seen[0].get("patient")) is patient, seen
    assert patient is False or seen[0]["timeout_s"] > intent_arbiter._TIMEOUT_S
    # The recall the memory question names stands beside the picked tool.
    assert "memory_recall" in picked
