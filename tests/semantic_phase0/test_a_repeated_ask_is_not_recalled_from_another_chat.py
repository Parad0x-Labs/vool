"""Asking the same thing again in a new chat is a new ask, not a recall of the earlier one.

Measured on the v0.8 line (CI #681/#682, the replay corpus): "show me the parse_header function",
its typo twin, "waht do you thnik abuot pair programing" and "waht can you acutally do for me"
went to the model in the first chat and to cross-chat recall in every later one, which quoted the
owner's earlier ASK back as "you mentioned it in a chat". A question typed without its mark or a
request to VOOL states no fact, so it never grounds a recall. Main keeps the model route; so must
this line. The control: a fact the owner stated in another chat is still recalled.
"""
from __future__ import annotations

import pytest

from tests.semantic_phase0._fixtures import (
    block_outbound_network,
    keep_the_checkout_clean,
    make_agent_module,
    pin_the_signing_key_passphrase,
    pin_volatile_machine_observations,
    reseal_network_after_function_fixtures,
)
from tests.semantic_phase0.test_semantic_replay_equivalence import _drive

_REPEATED = (
    "show me the parse_header function",
    "show me the parse_heder function",
    "waht do you thnik abuot pair programing",
    "waht can you acutally do for me",
)


def _route(result: dict) -> str:
    return str((result.get("_semantic_admission") or {}).get("route_id") or "")


@pytest.mark.parametrize("text", _REPEATED)
def test_the_same_ask_in_a_second_chat_keeps_its_route(make_agent_module, text):
    first = _drive(make_agent_module, text, f"repeat-{abs(hash(text))}-a", flag="0")
    second = _drive(make_agent_module, text, f"repeat-{abs(hash(text))}-b", flag="0")
    assert _route(second) == _route(first), (_route(first), _route(second), second.get("response"))
    assert _route(second) != "cross_chat_recall", second.get("response")
    assert not second.get("recall_found"), second.get("response")


def test_a_fact_stated_in_another_chat_is_still_recalled(make_agent_module, monkeypatch):
    """A lasting fact said in passing is kept at the turn's start (the keep step of the memory and context fixes), so
    the second chat finds it in saved memory and the model answers from it; before, it was quoted from the first
    chat's words by cross-chat recall. Either way the owner gets the number back in the reply.

    The fixture's own agent stubs the context loader and renders the CLI planner view, so neither saved memory nor a
    model's words reach a reply there. The second chat is asked on a chat surface with the real context loader, and
    the model is a double that knows only what the turn's context shows it."""
    import dataclasses
    import os
    import re

    _drive(make_agent_module, "my gym locker number is 4127", "repeat-fact-a", flag="0")
    agent = make_agent_module()
    del agent.context_loader.load  # the real loader, which reads saved memory into the model's context
    real_resolve = agent.memory_router.resolve

    def model_reading_the_context(**kwargs):
        decision = real_resolve(**kwargs)
        context = str(kwargs["context_result"].assembled_context())
        held = [m.group(1) for line in context.splitlines() if re.search(r"\blocker\b", line, re.IGNORECASE)
                for m in [re.search(r"\b(\d{3,})\b", line)] if m]
        text = f"Your gym locker number is {held[0]}." if held else "I don't know your gym locker number."
        return dataclasses.replace(decision, source="provider_execution", used_model=True, output_text=text,
                                   validation_state="valid", provider_id="double", model_name="double")

    monkeypatch.setattr(agent.memory_router, "resolve", model_reading_the_context)
    monkeypatch.setitem(os.environ, "VOOL_SEMANTIC_REACH", "0")
    session_id = "repeat-fact-b"
    asked = agent.run_once(
        "what's my gym locker number?",
        session_id_override=session_id,
        source_context={"surface": "openclaw", "platform": "openclaw", "_owner_local": True,
                        "session_id": session_id, "runtime_session_id": session_id},
    )
    assert "4127" in str(asked.get("response") or ""), asked.get("response")
