"""An answer the recall lane found in the owner's own saved memory is never held back as an unchecked web answer.

Gate 68 on a35e0e475 (packaged copy, qwen3:8b): with "our plumber is Mrs. Halvorsen, she charges a 45 euro call-out
fee" saved, "how much is the plumber's call-out?" in a new chat was judged a web ask ("web ask verdict: yes"), the
recall lane answered from that row, and the reply was withheld: "I didn't look anything up for this, and I had
nothing to check the answer I wrote against". What the turn did decides it, never the ask's words: the lane that
answered, its found flag, and whether anything was read from the web.
"""
from __future__ import annotations

import pytest

import core.intent_arbiter as intent_arbiter
from core import execution_records, grounding_lifecycle, online_asks, plugin_ask
from core.agent_runtime.cross_chat_recall import LANE_ID

ASKS = ("how much is the plumber's call-out?", "how much does the piano tuner charge now?",
        "how much is the window cleaner these days?")


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    plugin_ask.forget()
    online_asks.forget_web_verdicts()
    monkeypatch.setattr(intent_arbiter, "arbitrate_web_ask", lambda *_a, **_k: "yes")
    yield
    plugin_ask.forget()
    online_asks.forget_web_verdicts()


def _open_turn(name: str, ask: str) -> dict:
    context = {"session_id": f"{name}-s", "turn_id": f"{name}-t", "surface": "api"}
    assert grounding_lifecycle.register_required(
        context, request_text=ask, reason_codes=("current_info_signal:explicit_lookup_request",))
    return context


def _recall(found: bool = True) -> dict:
    return {"response": "Here is what I have saved:\n- our plumber is Mrs. Halvorsen, she charges a 45 euro call-out fee",
            "reason": LANE_ID, "route": LANE_ID, "recall_found": found}


@pytest.mark.parametrize("ask", ASKS)
def test_a_recall_found_in_saved_memory_is_not_judged_as_a_lookup(ask):
    context = _open_turn(f"saved-{ASKS.index(ask)}", ask)
    assert grounding_lifecycle.seal_turn_grounding(context, ask, _recall()) == ""
    assert grounding_lifecycle.lifecycle_for_context(context) is None


def test_a_recall_that_found_nothing_keeps_the_check():
    context = _open_turn("nothing-found", ASKS[0])
    grounding_lifecycle.seal_turn_grounding(context, ASKS[0], _recall(found=False))
    assert grounding_lifecycle.lifecycle_for_context(context) is not None


def test_a_turn_that_read_the_web_keeps_the_check():
    context = _open_turn("read-web", ASKS[0])
    execution_records.record(session_id=context["session_id"], turn_id=context["turn_id"], intent="web.search",
                             arguments={"query": "plumber call-out"}, details={}, ok=True)
    grounding_lifecycle.seal_turn_grounding(context, ASKS[0], _recall())
    assert grounding_lifecycle.lifecycle_for_context(context) is not None


def test_another_lane_s_answer_keeps_the_check():
    context = _open_turn("other-lane", ASKS[0])
    grounding_lifecycle.seal_turn_grounding(
        context, ASKS[0], {"response": "About 60 euros.", "reason": "ordinary_plain_text_chat", "recall_found": True})
    assert grounding_lifecycle.lifecycle_for_context(context) is not None
