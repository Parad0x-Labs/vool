"""The turn's judgement of whether a message asks for an added plugin (core.plugin_ask).

The judge is one extra model call at the start of a turn. It must cost nothing when no added plugin brings tools, it
must never fail a turn, and what it said must be read only for the text it judged while the same plugins are added:
a judgement kept after the plugins changed would offer, or withhold, a plugin the person no longer has.
"""
from __future__ import annotations

import pytest

from core import intent_arbiter, plugin_ask

PARCEL = [("parcel-lookup", "Parcel lookup: finds where a parcel is by its tracking code.")]
WORDS = [("word-count", "Word counter: counts the words in a text.")]


@pytest.fixture(autouse=True)
def _clean():
    plugin_ask.forget()
    yield
    plugin_ask.forget()


def _judge(monkeypatch, answer, calls=None):
    def arbitrate(message, plugins, **kwargs):
        if calls is not None:
            calls.append((message, list(plugins), kwargs.get("previous", "")))
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(intent_arbiter, "arbitrate_plugin_ask", arbitrate)


def test_no_added_plugin_with_tools_asks_no_judge(monkeypatch):
    calls: list = []
    monkeypatch.setattr(plugin_ask, "_installed_with_tools", lambda: [])
    _judge(monkeypatch, 1, calls)
    assert plugin_ask.judge_turn("kur dabar yra mano siunta PX-3065-B?", source_context={}) == ()
    assert calls == []
    assert plugin_ask.judgement("kur dabar yra mano siunta PX-3065-B?") is None


def test_the_judged_plugin_is_read_for_the_text_and_its_other_spelling(monkeypatch):
    monkeypatch.setattr(plugin_ask, "_installed_with_tools", lambda: list(PARCEL))
    _judge(monkeypatch, 1)
    typed = "my box PX-3065-B never came, any idea where it's sitting?"
    normalized = "my box px-3065-b never came any idea where its sitting"
    assert plugin_ask.judge_turn(typed, also=normalized, source_context={}) == ("parcel-lookup",)
    assert plugin_ask.judgement(typed) == ("parcel-lookup",)
    assert plugin_ask.judgement(normalized) == ("parcel-lookup",)
    assert plugin_ask.judgement("something never judged") is None


def test_none_of_them_is_a_judgement_too(monkeypatch):
    monkeypatch.setattr(plugin_ask, "_installed_with_tools", lambda: list(PARCEL))
    _judge(monkeypatch, 0)
    assert plugin_ask.judge_turn("what does a tracking number usually look like?", source_context={}) == ()
    assert plugin_ask.judgement("what does a tracking number usually look like?") == ()


@pytest.mark.parametrize("answer", [None, RuntimeError("model gone")])
def test_a_failed_judge_records_nothing_and_never_raises(monkeypatch, answer):
    monkeypatch.setattr(plugin_ask, "_installed_with_tools", lambda: list(PARCEL))
    _judge(monkeypatch, answer)
    assert plugin_ask.judge_turn("where is PX-3065-D", source_context={}) == ()
    assert plugin_ask.judgement("where is PX-3065-D") is None


def test_a_judgement_is_not_read_after_the_plugins_changed(monkeypatch):
    added = list(PARCEL)
    monkeypatch.setattr(plugin_ask, "_installed_with_tools", lambda: list(added))
    _judge(monkeypatch, 1)
    plugin_ask.judge_turn("where is PX-3065-D", source_context={})
    assert plugin_ask.judgement("where is PX-3065-D") == ("parcel-lookup",)
    added[:] = WORDS
    assert plugin_ask.judgement("where is PX-3065-D") is None


def test_a_new_turn_with_the_same_words_is_judged_again(monkeypatch):
    monkeypatch.setattr(plugin_ask, "_installed_with_tools", lambda: list(PARCEL))
    _judge(monkeypatch, 1)
    plugin_ask.judge_turn("and the other one?", source_context={})
    _judge(monkeypatch, None)
    plugin_ask.judge_turn("and the other one?", source_context={})
    assert plugin_ask.judgement("and the other one?") is None


def test_a_short_follow_up_is_judged_with_the_previous_request(monkeypatch):
    calls: list = []
    monkeypatch.setattr(plugin_ask, "_installed_with_tools", lambda: list(PARCEL))
    _judge(monkeypatch, 1, calls)
    history = [
        {"role": "user", "content": "where is PX-3065-D right now"},
        {"role": "assistant", "content": "It is at the pickup point."},
        {"role": "user", "content": "and PX-3065-E?"},
    ]
    plugin_ask.judge_turn("and PX-3065-E?", source_context={"conversation_history": history})
    assert calls[0][2] == "where is PX-3065-D right now"


def test_a_plugin_tool_named_by_its_id_is_settled_by_the_words_without_a_model(monkeypatch):
    """CI on fe98e849: a turn naming a turned-off plugin's tool by its id made a judge call before the lane said the
    plugin is off. The id settles it; the judge is asked only when the words do not name a tool."""
    import random
    import secrets

    from core import plugin_tools, tool_demand_signals

    plugin_id = random.choice(["parcel", "digest", "words"]) + "-" + secrets.token_hex(3)
    tool = random.choice(["lookup", "sha256", "count"])
    intent = f"{plugin_id}.{tool}"
    calls: list = []
    monkeypatch.setattr(plugin_ask, "_installed_with_tools", lambda: [(plugin_id, "A plugin: does one thing."), *PARCEL])
    monkeypatch.setattr(tool_demand_signals, "_plugin_intents", lambda text, registry=None: (intent,) if intent in text.lower() else ())
    monkeypatch.setattr(plugin_tools, "plugin_id_for_intent", lambda name: plugin_id if name == intent else "")
    _judge(monkeypatch, 2, calls)
    lead = random.choice(["Use", "please run", "can you call", "Run"])
    typed = f"{lead} the {intent} tool on this: {secrets.token_hex(6)}"
    assert plugin_ask.judge_turn(typed, source_context={}) == (plugin_id,)
    assert calls == []
    assert plugin_ask.judgement(typed) == (plugin_id,)
    # Without the id the judge still decides.
    plain = f"{random.choice(['where is', 'any news on'])} PX-{secrets.randbelow(9000) + 1000}-B"
    assert plugin_ask.judge_turn(plain, source_context={}) == ("parcel-lookup",)
    assert len(calls) == 1
