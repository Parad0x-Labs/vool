"""A reply is not thrown away over one sentence that compares to something never raised.

Measured 2026-11-25 on the iMac (7ce02be05, qwen3:8b, throwaway profile): "hi there" in a new chat with
no earlier turn got "I couldn't produce a normal chat response for that request." Both tries of the
greeting carried one unasked-for comparison (reason unrequested_prior_reference), and the router
replaced the whole reply with the fallback. Now only those sentences go; the rest is published when it
passes the same check.
"""
from __future__ import annotations

from core import ordinary_chat_response_guard as guard
from tests.test_memory_answers_are_not_reread_for_style import _invoke, _policy, _reader_request


def test_the_greeting_survives_without_its_comparison_sentence():
    turn = "hi there"
    draft = "Hi there! Just like sunshine, a hello brightens the day. What can I do for you?"
    request = _reader_request(turn, records=False)
    assert guard.inspect_ordinary_chat_output(draft, _policy(request), current_user_text=turn).reasons == (
        "unrequested_prior_reference",)
    response, calls, _ = _invoke(request, [draft, draft, draft])
    assert calls == 2
    assert "couldn't produce" not in response.output_text
    assert response.output_text.startswith("Hi there!") and "sunshine" not in response.output_text


def test_a_reply_that_is_nothing_but_the_comparison_still_fails_plainly():
    turn = "hi there"
    draft = "Just like sunshine, hello."
    request = _reader_request(turn, records=False)
    response, _calls, _ = _invoke(request, [draft, draft, draft])
    assert response.output_text == guard.ordinary_chat_safe_fallback()


def test_a_comparison_the_person_asked_for_is_kept():
    turn = "is a lime similar to a lemon?"
    answer = "A lime is similar to a lemon, but smaller and more sour."
    request = _reader_request(turn, records=False)
    response, calls, _ = _invoke(request, [answer])
    assert calls == 1 and response.output_text == answer
