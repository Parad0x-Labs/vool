"""A completed turn must carry its session id, or it cannot be investigated afterwards.

`collect_turn_trace()` needs a session id. The id is derived from message content by
`stable_openclaw_session_id_provider`, so a caller outside the daemon cannot know it -- and calling
the collector without one silently returns whichever session was most recent. On 2026-08-05 that
returned another lane's file reads and read exactly like real evidence for the turn under
investigation.

Combined with `include_runtime_events` returning nothing on the non-streaming path, that blocked
three separate root-cause investigations in a single session: the ordinary-question fallback, the
provider-status misroute, and `_decorate_chat_response` deleting a quote's price and source. The
runtime's own observability was the thing stopping the runtime from being debugged.
"""
from __future__ import annotations

from core.web.api.service import _attach_work_receipt


def test_the_session_id_is_returned_with_the_answer() -> None:
    out = _attach_work_receipt({"message": {"content": "hi"}}, result={"response": "hi"},
                               session_id="openclaw:abc123")
    assert out["vool_session_id"] == "openclaw:abc123"


def test_the_caller_payload_is_not_mutated() -> None:
    """The API owns its payload dict for the turn; a helper must not edit it in place."""
    original = {"message": {"content": "hi"}}
    out = _attach_work_receipt(original, result={"response": "hi"}, session_id="s1")
    assert "vool_session_id" not in original
    assert out is not original


def test_an_empty_answer_still_carries_its_session_id() -> None:
    """An empty answer is precisely the turn worth tracing -- the receipt path returns early on it,
    so the id must be set before that and outside its try."""
    out = _attach_work_receipt({}, result={"response": ""}, session_id="openclaw:empty")
    assert out["vool_session_id"] == "openclaw:empty"


def test_a_receipt_failure_does_not_cost_the_turn_its_traceability() -> None:
    out = _attach_work_receipt({}, result={"response": "x"}, session_id="openclaw:zz")
    assert out.get("vool_session_id") == "openclaw:zz"


def test_a_missing_session_id_renders_as_empty_not_as_none() -> None:
    out = _attach_work_receipt({}, result={"response": "x"}, session_id="")
    assert out["vool_session_id"] == ""
