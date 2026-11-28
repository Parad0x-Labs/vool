"""A message the turn's judge settled as one recall, with no tool ask beside it, is not cut into demand parts.

iMac gate58 on 9a267b88d (qwen3:8b): "remind me, when is <name>'s wedding?" had its Reminders pick turned down by its own
question and was read as a recall, yet the demand seam cut it into "remind me" and the question, and the reply ended in
"I could not answer this part of your message: - remind me (I could not read a date or time in that ...)". Wordings
here are invented for this contract.
"""
from __future__ import annotations

import pytest

from core import plugin_ask
from core.agent_runtime import demand_ownership


@pytest.fixture(autouse=True)
def _clean():
    plugin_ask.forget()
    yield
    plugin_ask.forget()


@pytest.fixture
def agent(runtime_storage_reset, monkeypatch, tmp_path):
    from core.agent_runtime.agent import VoolAgent

    monkeypatch.setenv("VOOL_WORKSPACE_ROOT", str(tmp_path))
    return VoolAgent(backend_name="test-backend", device="judged-recall-whole", persona_id="default")


def _seam_calls(monkeypatch) -> list[str]:
    calls: list[str] = []
    real = demand_ownership.demand_coverage

    def coverage(text, *a, **k):
        calls.append(text)
        return real(text, *a, **k)

    monkeypatch.setattr(demand_ownership, "demand_coverage", coverage)
    return calls


@pytest.mark.parametrize("text", ["remind me, when is Odeta's christening?", "remind me, where did I park the van?",
                                  "primink, kada Lino gimtadienis?"])
def test_a_judged_recall_with_no_tool_ask_stays_whole(text, agent, monkeypatch):
    plugin_ask._record_builtins(text, ("memory_recall",))
    calls = _seam_calls(monkeypatch)
    assert agent._maybe_answer_demand_owned_turn(effective_input=text, session_id="c1", source_context={}) is None
    assert calls == []


def test_a_recall_beside_a_tool_ask_is_still_offered_to_the_seam(agent, monkeypatch):
    text = "what's my locker number, and remind me at 6 to call the garage"
    plugin_ask._record_builtins(text, ("memory_recall", "reminders"))
    calls = _seam_calls(monkeypatch)
    agent._maybe_answer_demand_owned_turn(effective_input=text, session_id="c1", source_context={})
    assert calls == [text]
