"""A chat reply that also claims a save loses that sentence, never the whole reply.

iMac gate59 on 033c90daa (qwen3:8b): "repotted all the herbs on the balcony this morning, the mint was a jungle" was
judged plain chat (no tool, no memory ask). The model's draft also said it was saved, and the whole reply became the
notice "I did not write anything to memory -- no preference, rule or setting was stored on this turn ...". With the
judge's chat reading, the false sentence goes and the reply's own words stay. A message that asked for a save, one
nothing judged, and a reply that is only the claim still get the notice. Wordings invented for this contract.
"""
from __future__ import annotations

import pytest

from core import plugin_ask
from core.agent_runtime import action_honesty_validator as validator

OWNER = {"_owner_local": True}
SMALL_TALK = "spent the whole afternoon clearing the shed, found my old bike under a tarp"


@pytest.fixture(autouse=True)
def clean_judgements():
    plugin_ask.forget()
    yield
    plugin_ask.forget()


def _check(said: str, user_input: str) -> str:
    return validator.enforce_final_action_honesty(
        {"response": said}, user_input=user_input, session_id="chat", source_context=dict(OWNER))["response"]


@pytest.mark.parametrize(
    ("said", "left"),
    [
        ("What a find! I've saved that to memory. Is the bike still rideable?", "What a find! Is the bike still rideable?"),
        ("Sounds like a productive afternoon.\nStored it in memory.\nWill you fix the bike up?",
         "Sounds like a productive afternoon.\nWill you fix the bike up?"),
    ],
)
def test_a_reply_to_plain_chat_keeps_its_own_words(said, left):
    plugin_ask._record_builtins(SMALL_TALK, ())
    assert _check(said, SMALL_TALK) == left


def test_a_reply_that_is_only_the_claim_still_gets_the_notice():
    plugin_ask._record_builtins(SMALL_TALK, ())
    assert _check("I've saved that to memory.", SMALL_TALK) == validator._HONEST_NO_PERSISTENCE_RESPONSE


def test_a_message_that_asked_for_a_save_still_gets_the_notice():
    asked = "remember that the bike lock code is the same as the shed"
    plugin_ask._record_builtins(asked, ("memory_keep",))
    assert _check("Got it! I've saved that to memory.", asked) == validator._HONEST_NO_PERSISTENCE_RESPONSE


def test_a_message_nothing_judged_still_gets_the_notice():
    assert _check("What a find! I've saved that to memory.", SMALL_TALK) == validator._HONEST_NO_PERSISTENCE_RESPONSE
