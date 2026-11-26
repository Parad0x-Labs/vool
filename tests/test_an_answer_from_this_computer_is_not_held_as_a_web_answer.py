"""An answer that rests on the person's own files is never held back as an unsupported web answer.

iMac gate35 on 584863ad5 (Offline, qwen3:8b): a find of the person's own bill found it, and the reply was withheld
as "I didn't look anything up for this": a word in the ask had held the turn to a current-information lookup. Two
readings decide it, neither from the ask's words: what the turn did (no web reachable, none read, a file tool ran),
and the turn judge's pick of a computer-file choice (any language). Wordings are built at random.
"""
from __future__ import annotations

import random

import pytest

import core.intent_arbiter as intent_arbiter
from core import execution_records, grounding_lifecycle, online_asks, plugin_ask
from core.turn_contract import TURN_REQUEST_KEY

_THINGS = ("gas bill", "pension letter", "lease scan", "insurance renewal", "dentist invoice")
_FROM = ("my company", "the developer who fixed the roof", "the founder of my gym", "my landlord's company")


class _Turn:
    def __init__(self, turn_id: str) -> None:
        self.turn_id = turn_id


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    plugin_ask.forget()
    online_asks.forget_web_verdicts()
    monkeypatch.setattr(intent_arbiter, "arbitrate_web_ask", lambda *_a, **_k: "yes")
    yield
    plugin_ask.forget()
    online_asks.forget_web_verdicts()


def _ask(seed: int) -> str:
    rng = random.Random(f"own-file-{seed}")
    return f"find the {rng.choice(_THINGS)} from {rng.choice(_FROM)}"


def _open_turn(name: str, *, web: bool, monkeypatch) -> dict:
    import core.remote_fetch_policy as policy

    monkeypatch.setattr(policy, "effective_web_available", lambda *_a, **_k: web)
    context = {"session_id": f"{name}-s", "turn_id": f"{name}-t", "surface": "api"}
    assert grounding_lifecycle.register_required(
        context, request_text=name, reason_codes=("current_info_signal:explicit_lookup_request",))
    return context


def _ran(context: dict, intent: str, *, ok: bool = True) -> None:
    execution_records.record(session_id=context["session_id"], turn_id=context["turn_id"], intent=intent,
                             arguments={"query": "bill"}, details={"matches": ["~/Documents/bill-2026-10.pdf"]}, ok=ok)


@pytest.mark.parametrize("seed", range(4))
def test_a_file_find_with_no_web_reachable_is_not_judged_as_a_lookup(seed, monkeypatch):
    context = _open_turn(f"offline-find-{seed}", web=False, monkeypatch=monkeypatch)
    _ran(context, random.Random(seed).choice(sorted(plugin_ask.computer_file_intents())))
    assert grounding_lifecycle.seal_turn_grounding(context, _ask(seed)) == ""
    assert grounding_lifecycle.lifecycle_for_context(context) is None


def test_with_the_web_reachable_the_lookup_is_still_judged(monkeypatch):
    context = _open_turn("online-find", web=True, monkeypatch=monkeypatch)
    _ran(context, "machine.find_file")
    grounding_lifecycle.seal_turn_grounding(context, _ask(9))
    assert grounding_lifecycle.lifecycle_for_context(context) is not None


@pytest.mark.parametrize("ran", [("web.search", True), ("machine.find_file", False)])
def test_a_web_read_or_a_file_tool_that_failed_keeps_the_check(ran, monkeypatch):
    context = _open_turn(f"kept-{ran[0]}-{ran[1]}", web=False, monkeypatch=monkeypatch)
    if ran[0] == "web.search":
        _ran(context, "machine.find_file")
    _ran(context, ran[0], ok=ran[1])
    grounding_lifecycle.seal_turn_grounding(context, _ask(11))
    assert grounding_lifecycle.lifecycle_for_context(context) is not None


@pytest.mark.parametrize("seed", range(4))
def test_a_judged_file_choice_needs_no_web(seed):
    rng = random.Random(f"pick-{seed}")
    text = rng.choice([_ask(seed), "surask dujų sąskaitą iš spalio", "finde den Brief meiner Firma zur Rente"])
    plugin_ask._record_builtins(text, (rng.choice(sorted(plugin_ask.COMPUTER_FILE_CHOICES)),))
    context = {TURN_REQUEST_KEY: _Turn(f"turn-{seed}")}
    assert online_asks.web_ask_verdict(text, source_context=context) == "no"
    assert online_asks.judged_to_need_no_web(text, context)


def test_a_file_choice_beside_the_web_leaves_the_web_reading_to_decide():
    text = _ask(3)
    plugin_ask._record_builtins(text, ("find", "web"))
    assert online_asks.web_ask_verdict(text, source_context={TURN_REQUEST_KEY: _Turn("turn-web")}) == "yes"
