"""One slow judgement never leaves the turn's tool judge silent.

Real-app gate on the installed 3dfb2f34 (2026-11-22, local qwen3:8b, fresh home): the tool-ask judge answered for the
first ten minutes, then said "None in 2 ms" on almost every turn. One judgement call had timed out while Ollama was
reloading models, and the module's single breaker then failed every later judgement, on every model, for five
minutes. "i need a remidner to buy oat milk tomorrow at 3pm" and "create a contact for ... with phone ..." were
offered no tool, and the log never said why.

Now the breaker is kept per model, the tool ask is asked even while its model's breaker is open, and the log names
why a tool ask went unjudged. Model names and wordings are built from parts at random.
"""
from __future__ import annotations

import logging
import random
from unittest import mock

import pytest
import requests

from core import intent_arbiter as arbiter
from core import plugin_ask
from core.request_trust import OWNER_LOCAL_KEY

_RNG = random.Random(20261122)
_SIZES = ("4b", "8b", "14b", "9b")
_FAMILIES = ("qwen3", "qwen3.5", "llama3.1", "gemma3")
_HELPERS = ("qwen3:0.6b", "qwen3.5:0.8b", "gemma3:1b")


def _reply(content: str):
    reply = mock.Mock()
    reply.raise_for_status = mock.Mock()
    reply.json.return_value = {"message": {"content": content}}
    return reply


@pytest.fixture()
def models(monkeypatch):
    chat = f"{_RNG.choice(_FAMILIES)}:{_RNG.choice(_SIZES)}"
    helper = _RNG.choice(_HELPERS)
    monkeypatch.setenv("VOOL_INTENT_ARBITER", "1")
    monkeypatch.setenv("VOOL_ARBITER_MODEL", helper)
    monkeypatch.setenv("VOOL_KEEP_JUDGE_MODEL", chat)
    monkeypatch.setattr(arbiter, "_local_models_disabled", lambda: False)
    arbiter.reset_breaker()
    plugin_ask.forget()
    yield chat, helper
    arbiter.reset_breaker()
    plugin_ask.forget()


def test_a_timeout_of_one_model_leaves_the_other_models_judgements_asked(models):
    chat, helper = models
    with mock.patch("requests.post", side_effect=requests.exceptions.ReadTimeout("loading")):
        assert arbiter.arbitrate_followup("show my documents", "blocked", "nice one") is None
    assert arbiter._breaker_open(helper) and not arbiter._breaker_open(chat)
    with mock.patch("requests.post", return_value=_reply('{"answer": "yes"}')) as post:
        assert arbiter.arbitrate_keep_ask("hang on to this for me", "the spare key is in the blue pot") == "yes"
    assert post.call_args.kwargs["json"]["model"] == chat


def test_the_tool_ask_is_asked_while_its_own_models_breaker_is_open(models):
    chat, _helper = models
    with mock.patch("requests.post", side_effect=requests.exceptions.ReadTimeout("busy")):
        assert arbiter.arbitrate_keep_ask("note to self", "the boiler code is on the side") is None
    assert arbiter._breaker_open(chat)
    # An optional judgement stays quiet while the breaker is open...
    with mock.patch("requests.post", side_effect=AssertionError("must not be called")):
        assert arbiter.arbitrate_keep_ask("note to self", "the boiler code is on the side") is None
    # ...the tool ask, which the turn cannot do without, is still put to the model, and its answer closes the breaker.
    choices = [description for _key, description, _seats in plugin_ask.BUILTIN_CHOICES]
    with mock.patch("requests.post", return_value=_reply('{"answer": [1]}')) as post:
        assert arbiter.arbitrate_plugin_ask("i need a remidner for the oat milk at 3", choices) == (1,)
    assert post.call_args.kwargs["json"]["model"] == chat
    assert not arbiter._breaker_open(chat)


def test_an_unjudged_tool_ask_says_why_in_the_log(models, monkeypatch, caplog):
    monkeypatch.setattr(plugin_ask, "_installed_with_tools", lambda: [])
    owner = {OWNER_LOCAL_KEY: True, "surface": "openclaw", "platform": "openclaw", "session_id": "chat-slow-judge"}
    with mock.patch("requests.post", side_effect=requests.exceptions.ReadTimeout("busy")), caplog.at_level(
        logging.WARNING, logger=plugin_ask._log.name
    ):
        plugin_ask.judge_turn("Primink man rytoj nupirkti pieno", source_context=owner, session_id="chat-slow-judge")
    unjudged = [record.getMessage() for record in caplog.records if "not judged" in record.getMessage()]
    assert unjudged and "request:ReadTimeout" in unjudged[0], unjudged
