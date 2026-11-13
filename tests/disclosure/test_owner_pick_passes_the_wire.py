"""The owner's own pick of a paid model is sent through the disclosure boundary with cloud help off.

The router honors an explicit pick from the owner's own session as a deliberate choice (it reserves
the call; core.paid_call_reservation), whatever the cloud-help mode says. The disclosure boundary
re-reads the authorities at dispatch, and it read only "cloud help on", so the picked model was
refused with "Cloud models are off for this turn" on a fresh install. The reservation for THIS call
is now an authority there too; local-only still refuses, and a call without a standing reservation
still needs cloud help on.

NO REAL CALL IS MADE: the real Anthropic BYOK lane is registered from a dummy key and its transfer is
pointed at a loopback server only after the boundary has checked the destination.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from core import provider_http
from tests.test_owner_pick_paid_reservation import (  # fixtures, used by name
    _isolated_runtime,
    _run_turn,
    _set_policy,
    anthropic_lane,
)


@pytest.fixture
def provider(monkeypatch):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.server.posts += 1
            body = {"choices": [{"message": {"content": "cloud answer"}}], "usage": {"prompt_tokens": 11, "completion_tokens": 7}}
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(body).encode())

    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    http.posts = 0
    thread = threading.Thread(target=http.serve_forever)
    thread.start()

    class ToLoopback(provider_http._Transfer):
        def __init__(self, method, url, options, cancel_check):
            if str(url).startswith("https://api.anthropic.com/"):
                url = f"http://127.0.0.1:{http.server_port}/v1/chat/completions"
            super().__init__(method, url, options, cancel_check)

    monkeypatch.setattr(provider_http, "_Transfer", ToLoopback)
    yield http
    http.shutdown()
    http.server_close()
    thread.join()


def _picked(lane):
    return {"_owner_local": True, "requested_model": lane.provider_id}


def test_the_owners_pick_is_sent_with_cloud_help_off(anthropic_lane, provider) -> None:
    _set_policy(mode="off", free_cloud_enabled=False)
    decision = _run_turn(source_context=_picked(anthropic_lane), allow_paid_fallback=False, posts=None)
    assert provider.posts == 1, decision.details.get("reason")
    assert decision.used_model and decision.output_text == "cloud answer"


def test_local_only_still_refuses_the_owners_pick(anthropic_lane, provider) -> None:
    from core.policy_engine import set_operator_policy_values

    _set_policy(mode="off", free_cloud_enabled=False)
    set_operator_policy_values({"system.local_only_mode": True})
    try:
        decision = _run_turn(source_context=_picked(anthropic_lane), allow_paid_fallback=False, posts=None)
    finally:
        set_operator_policy_values({"system.local_only_mode": False})
    assert provider.posts == 0
    assert not decision.used_model


def test_only_a_standing_reservation_counts() -> None:
    from core.disclosure.wire import _paid_call_still_reserved

    assert _paid_call_still_reserved("") is False
    assert _paid_call_still_reserved("model-call-that-was-never-reserved") is False


def _reserve(model_call_id, model_id):
    from core.model_spend_ledger import SpendLimits, reserve_spend

    reserve_spend(model_call_id=model_call_id, task_id="task-bind", subtask_id="owner_explicit_model_pick:bind",
                  model_id=model_id, maximum_usd=0.25, limits=SpendLimits(per_call_usd=1.0, per_task_usd=1.0, daily_usd=5.0, monthly_usd=20.0))


def test_a_reservation_never_carries_another_model_or_host(anthropic_lane) -> None:
    """Security review of 7d45a5a8: the wire's exception binds the reserved model and the reserved lane's host."""
    from types import SimpleNamespace

    from core.disclosure.wire import _owner_paid_call, _paid_call_still_reserved

    model = anthropic_lane.model_name
    host_url = str(anthropic_lane.runtime_config["base_url"]).rstrip("/") + "/v1/chat/completions"
    _reserve("model-call-bind", model)
    request = SimpleNamespace(model_call_id="model-call-bind")
    lane = anthropic_lane.provider_id
    assert _owner_paid_call(request, {"model": model}, url=host_url, provider_id=lane) == "model-call-bind"
    # Another model on the same reservation.
    assert _owner_paid_call(request, {"model": "claude-opus-other"}, url=host_url, provider_id=lane) == ""
    # Another host for the same model and reservation.
    assert _owner_paid_call(request, {"model": model}, url="https://elsewhere.invalid/v1/chat/completions", provider_id=lane) == ""
    # Another lane named for the same model.
    assert _owner_paid_call(request, {"model": model}, url=host_url, provider_id="someone-else:" + model) == ""
    # At dispatch the body's model must still be the reserved one.
    assert _paid_call_still_reserved("model-call-bind", model=model) is True
    assert _paid_call_still_reserved("model-call-bind", model="claude-opus-other") is False
