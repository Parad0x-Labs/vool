"""The buffered chat door treats an uncommitted reply the way the streamed door does.

Measured in the skills A/B on 11eef8dc (2026-11-13): "Turn this into an X article", served
buffered, every planned tool failed, and the turn died as a bare HTTP 500 raised by the K-08
verify in `_dispatch_post_inner`. A model-lane result reaches that door admitted but without
`vool_response_commit` (probed on 11eef8dc: commit false, admission true). The streamed lane's
`_k08_verify_immutable` lets the door's honesty application stand for exactly such a result;
the buffered door raised instead, so the same turn was answered streamed and refused buffered.

Now both doors agree: an uncommitted reply is completed at the door (and the replacement is
logged with its reason), while a committed reply keeps the strict verify and still raises.
"""

from __future__ import annotations

import functools
import json
import logging

import pytest

import storage.db as sdb
from core.web.api import service
from core.web.api.runtime import _k08_verify_immutable
from tests.asgi_harness import asgi_request

PROMPT = "Delete the old log files in my project"
CLAIM = "I deleted the old log files in your project."


def _door(result: dict, session: str) -> dict:
    return service._verify_buffered_seal(
        result,
        user_text=PROMPT,
        session_id=session,
        source_context={"surface": "api", "request_id": "req-buffered-door"},
    )


def test_an_admitted_uncommitted_reply_is_completed_not_refused(caplog) -> None:
    # Admitted by the in-turn seam, never committed: the shape the probe saw at this door.
    reply = {"response": CLAIM, "mode": "model", "_semantic_admission": {"admitted": True}}
    with caplog.at_level(logging.WARNING, logger="vool.api"):
        served = _door(reply, "buffered-door-uncommitted")
    # Nothing ran this turn, so the claim is replaced, and the turn is answered, not a 500.
    assert served["response"] != CLAIM
    assert served["action_honesty_validator"]["reason"] == "no_executed_action_receipt"
    assert reply["response"] == CLAIM  # the caller's dict is not mutated
    # The operator log names why the door replaced an admitted reply.
    assert any(
        "buffered door completed an uncommitted reply" in rec.getMessage()
        and "admitted=True" in rec.getMessage()
        and "no_executed_action_receipt" in rec.getMessage()
        for rec in caplog.records
    )
    # The streamed door's rule for the same result: completion stands.
    _k08_verify_immutable(unchanged=False, message="K-08 violation: x", arrived_unsealed=True)


def test_a_committed_reply_that_would_change_still_raises() -> None:
    reply = {
        "response": CLAIM,
        "mode": "model",
        "vool_response_commit": {"canonical_content": CLAIM},
    }
    with pytest.raises(RuntimeError, match="K-08 violation: final-action-honesty"):
        _door(reply, "buffered-door-committed")


def test_a_reply_nothing_would_change_passes_through_both_ways() -> None:
    plain = "Here is a short summary of the article."
    for extra in ({}, {"vool_response_commit": {"canonical_content": plain}}):
        reply = {"response": plain, "mode": "model", **extra}
        served = _door(reply, "buffered-door-plain")
        assert served["response"] == plain
        assert "action_honesty_validator" not in served


@pytest.fixture()
def fresh_store(tmp_path):
    from core.runtime_continuity import configure_runtime_continuity_db_path
    from storage.db import active_default_db_path
    from storage.migrations import run_migrations

    sdb.configure_default_db_path(tmp_path / "buffered-door.db")
    run_migrations()
    configure_runtime_continuity_db_path(active_default_db_path())
    yield
    sdb.configure_default_db_path(None)


def test_the_served_buffered_turn_answers_instead_of_a_500(fresh_store) -> None:
    """The real buffered /api/chat route, with the model call stubbed to the measured shape."""
    from apps.vool_api_server import create_app
    from core.web.api.runtime import RuntimeServices

    def _admitted_uncommitted(*_a, **_k) -> dict:
        return {
            "response": CLAIM,
            "success": True,
            "confidence": 0.8,
            "route_reason": "model_lane",
            "mode": "advice_only",
            "_semantic_admission": {"admitted": True},
        }

    app = create_app(RuntimeServices(display_name="VOOL"))
    app.state.post_dispatcher = functools.partial(
        service.dispatch_post, run_agent_provider=_admitted_uncommitted
    )
    status, _headers, body = asgi_request(
        app,
        method="POST",
        path="/api/chat",
        headers={"Content-Type": "application/json", "X-Request-ID": "req-buffered-door"},
        body=json.dumps({"messages": [{"role": "user", "content": PROMPT}], "stream": False}).encode(),
    )
    assert status == 200, body[:400]
    payload = json.loads(body)
    served = str((payload.get("message") or {}).get("content") or payload.get("response") or "")
    assert served.strip()
    assert CLAIM not in served
