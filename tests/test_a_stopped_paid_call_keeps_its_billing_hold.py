"""A paid call the person stops ends at once and keeps its billing hold, with one receipt.

The money rule for a call that left for the wire and was then stopped: the provider may have
billed it and nothing says for how much, so the reservation is held as ``billing_ambiguous``
(never released as unspent, never settled as known), it is the one terminal receipt after the
reservation, and the call is never sent a second time. Before the stop reached the transfer, the
same call ran on until the provider answered and only then raised ``model_call_cancelled``; the
ledger recorded exactly this, a minute later.

NO REAL CALL IS MADE. The real Anthropic BYOK lane is registered from a dummy key and its POST goes
through the real provider transfer (worker process, cancel option and all) to a loopback server
that holds the reply, so only the destination differs from a live call.
"""
from __future__ import annotations

import json
import threading
import time
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
def holding_provider():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.server.posts += 1
            self.server.release.wait(30)
            body = {"choices": [{"message": {"content": "cloud answer"}}], "usage": {"prompt_tokens": 11, "completion_tokens": 7}}
            try:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(body).encode())
            except (BrokenPipeError, ConnectionResetError):
                pass

    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    http.release = threading.Event()
    http.posts = 0
    thread = threading.Thread(target=http.serve_forever)
    thread.start()
    yield http
    http.release.set()
    http.shutdown()
    http.server_close()
    thread.join()


def test_a_paid_call_stopped_in_flight_ends_at_once_and_is_held_as_billing_ambiguous(
    anthropic_lane, holding_provider, monkeypatch
) -> None:
    from core.runtime_task_events import (
        new_runtime_event_stream_id,
        register_runtime_event_sink,
        unregister_runtime_event_sink,
    )

    real_post = provider_http.post
    sent: list[str] = []

    def to_loopback(url, **options):
        sent.append(str(url))
        return real_post(f"http://127.0.0.1:{holding_provider.server_port}/v1/chat/completions", **options)

    monkeypatch.setattr("adapters.openai_compatible_adapter.requests.post", to_loopback)
    _set_policy(mode="off", daily_cap=25)
    events: list[dict] = []
    stream_id = new_runtime_event_stream_id()
    register_runtime_event_sink(stream_id, lambda event: events.append(dict(event)))
    cancel = threading.Event()
    stopped: list[float] = []

    def press_stop() -> None:
        while holding_provider.posts == 0:
            time.sleep(0.02)
        time.sleep(0.3)
        cancel.set()
        stopped.append(time.monotonic())
        # Whatever the runtime does, the provider answers 3 s after the stop.
        threading.Timer(3.0, holding_provider.release.set).start()

    threading.Thread(target=press_stop, daemon=True).start()
    try:
        decision = _run_turn(
            source_context={
                "_owner_local": True,
                "requested_model": anthropic_lane.provider_id,
                "runtime_event_stream_id": stream_id,
                "runtime_session_id": "paid-answer-stopped",
                "cancel_event": cancel,
            },
            allow_paid_fallback=False,
            posts=None,
        )
    finally:
        unregister_runtime_event_sink(stream_id)
    ended = time.monotonic()

    assert decision.used_model is False
    assert len(sent) == 1 and holding_provider.posts == 1, "a stopped paid call is never sent again"
    assert stopped and ended - stopped[0] < 2.0, f"the stopped paid call ran on for {ended - stopped[0]:.1f}s"
    paid = [
        event
        for event in events
        if event.get("event_type")
        in {"paid_call.reserved", "paid_call.settled", "paid_call.released", "paid_call.billing_ambiguous"}
    ]
    assert [event["event_type"] for event in paid] == ["paid_call.reserved", "paid_call.billing_ambiguous"]
    reserved, held = paid
    assert reserved["model_call_id"] == held["model_call_id"]
    assert reserved["reservation_id"] == held["reservation_id"]
    assert held["actual_usd"] is None
    assert held["held_usd"] == reserved["reserved_usd"] and held["held_usd"] > 0
    assert held["spend_status"] == "billing_ambiguous"
    assert not [event for event in events if event.get("event_type") == "model.call_completed"]

    from core.model_spend_ledger import get_spend_reservation

    ledger = get_spend_reservation(str(reserved["model_call_id"]))
    assert ledger is not None and str(ledger.status) == "billing_ambiguous"
    assert float(ledger.reserved_usd) == float(reserved["reserved_usd"])
