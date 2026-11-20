"""A forget of one saved fact says it removed one entry, however often the chat mentioned it.

Measured on the real app (release walk at 36033c05): "remember that my dentist is Dr Okafor", then in another chat
"who is my dentist?", more about the dentist, and "forget my dentist" answered "Forget applied. Removed 3 memory entries." The store held one
fact and erased one row. The count took the largest of every layer's count, and the semantic layer's nodes and the
chat turns that mentioned the fact (its source occurrences) are copies of the one fact, not facts of their own.

Every copy is still erased; only the count changed. The control: a forget that matches no saved fact still says
nothing was removed.
"""
from __future__ import annotations

import os
import re

from tests.semantic_phase0._fixtures import (  # noqa: F401  (pytest fixtures)
    block_outbound_network,
    keep_the_checkout_clean,
    make_agent_module,
    pin_the_signing_key_passphrase,
    pin_volatile_machine_observations,
    reseal_network_after_function_fixtures,
)


def _say(agent, text: str, chat: str) -> str:
    result = agent.run_once(
        text,
        session_id_override=chat,
        source_context={"surface": "openclaw", "platform": "openclaw", "_owner_local": True,
                        "session_id": chat, "runtime_session_id": chat},
    )
    return str(result.get("response") or "")


def test_one_fact_mentioned_in_another_chat_is_one_removed_entry(make_agent_module, monkeypatch):
    monkeypatch.setitem(os.environ, "VOOL_SEMANTIC_REACH", "0")
    agent = make_agent_module()
    _say(agent, "remember that my dentist is Dr Okafor", "forget-count-a")
    _say(agent, "who is my dentist?", "forget-count-b")
    _say(agent, "is my dentist open on Saturdays?", "forget-count-b")
    reply = _say(agent, "forget my dentist", "forget-count-b")
    assert re.search(r"\bRemoved 1 memory entry\b", reply), reply

    from core.memory.entries import _load_jsonl, memory_entries_path

    assert not [row for row in _load_jsonl(memory_entries_path()) if "okafor" in str(row).lower()]


def test_a_forget_that_matches_nothing_still_removes_nothing(make_agent_module, monkeypatch):
    monkeypatch.setitem(os.environ, "VOOL_SEMANTIC_REACH", "0")
    agent = make_agent_module()
    _say(agent, "remember that my dentist is Dr Okafor", "forget-none-a")
    reply = _say(agent, "forget my plumber", "forget-none-b")
    assert "Removed" not in reply, reply
