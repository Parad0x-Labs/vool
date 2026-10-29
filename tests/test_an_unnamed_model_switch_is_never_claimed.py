"""Asked to switch to a faster model without naming one, VOOL says what is true and changes nothing.

Measured 2026-10-28 on qwen3:8b, the phone and the PC alike: "youre kinda slow today, can you switch to a faster
smaller model?" named no model, so the switch command fell through and the model answered that it had switched.
Nothing had changed. Served over /api/chat on one isolated daemon whose stub model makes that same claim.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.agent_runtime.fast_command_surface import (
    GENERIC_MODEL_CHANGE_REPLY,
    maybe_handle_generic_model_change_ask,
)
from tests._blackbox_served_rig import ServedDaemon, run_in_home
from tests._served_sufficiency_provider import SEED
from tests.test_private_chat_served_workflow import _certify, _Provider, _stream

REPO = Path(__file__).resolve().parents[1]
MODEL = "stub-private:4b"
FALSE_CLAIM = "Done. I've switched to a faster, smaller model for you."


@pytest.mark.parametrize(
    "said",
    [
        "youre kinda slow today, can you switch to a faster smaller model?",
        "switch to a smaller model",
        "could you use a quicker model please",
        "lets try a different model",
        "i want you to change to a bigger, smarter model",
    ],
)
def test_a_request_to_change_the_model_gets_the_truth(said):
    answer = maybe_handle_generic_model_change_ask(said)
    assert answer is not None and answer["advice_only"] and not answer["success"]
    assert answer["response"] == GENERIC_MODEL_CHANGE_REPLY


@pytest.mark.parametrize(
    "said",
    [
        "should i switch to a smaller model for coding?",
        "what is a smaller model good for?",
        "explain why a smaller model is faster",
        "can you write a script that switches my app to a smaller model?",
        "can you help me switch my bot to a different model",
        "my son wants a bigger model car, can you suggest one",
    ],
)
def test_other_talk_about_models_is_left_alone(said):
    assert maybe_handle_generic_model_change_ask(said) is None


def test_the_served_reply_never_claims_a_switch(tmp_path):
    home = tmp_path / "home"
    provider = _Provider({MODEL: FALSE_CLAIM})
    with provider:
        run_in_home(home, SEED.format(root=REPO, base_url=provider.base_url, models=[(MODEL, {})]))
        with ServedDaemon(home, env_extra={"VOOL_MODEL_LOAD_FLOOR_GB": "0"}) as daemon:
            assert _certify(daemon).get("state") == "verified"
            said, _ = _stream(daemon, "youre kinda slow today, can you switch to a faster smaller model?",
                              "openclaw:" + "5a" * 10)
            assert "switched to" not in said.lower(), said
            assert said.startswith("I can't change the model myself"), said


def test_a_second_ask_beside_it_is_not_swallowed():
    assert maybe_handle_generic_model_change_ask("switch to a faster model and explain entropy briefly") is None
    assert maybe_handle_generic_model_change_ask("can you switch to a smaller model? whats the weather in Riga") is None
