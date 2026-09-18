"""Council seat dispatch laws: plan mode, the spend wall, and receipt counting.

The HTTP seam is faked at ``urllib.request.urlopen``; everything on this side of it —
pin negotiation, refusal typing, stream parsing — is the real module under test.
"""

from __future__ import annotations

import io
import json

import pytest

from core.council import dispatch
from core.council.orchestrator import Seat


class _Response(io.BytesIO):
    def __init__(self, payload: bytes, status: int = 200):
        super().__init__(payload)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeHTTP:
    """Routes urlopen calls by path; records every request body."""

    def __init__(self, routes):
        self.routes = routes
        self.requests: list[tuple[str, dict]] = []

    def __call__(self, request, timeout=None):
        path = request.full_url.split("127.0.0.1:9")[-1]
        path = "/" + path.split("/", 1)[1] if "/" in path else path
        body = json.loads(request.data.decode("utf-8")) if request.data else {}
        self.requests.append((path, body))
        handler = self.routes[path]
        result = handler(body) if callable(handler) else handler
        status, payload = result
        if status >= 400:
            import urllib.error

            raise urllib.error.HTTPError(
                request.full_url, status, "err", {}, io.BytesIO(json.dumps(payload).encode())
            )
        if isinstance(payload, bytes):
            return _Response(payload, status)
        return _Response(json.dumps(payload).encode("utf-8"), status)


def _stream(chunks, events=()):
    lines = []
    for event_type in events:
        # The REAL wire shape: the served stream passes events through
        # `build_task_event`, which renames the raw `event_type` to `type`. Feeding the
        # raw key here once let the reader read the wrong field and count zero receipts
        # on every live turn, so the fixture carries the shape the server actually sends.
        lines.append(json.dumps({"vool_event": {"type": event_type, "raw_type": event_type}}))
    for index, chunk in enumerate(chunks):
        lines.append(json.dumps({
            "message": {"content": chunk},
            "done": index == len(chunks) - 1,
        }))
    return ("\n".join(lines) + "\n").encode("utf-8")


BASE = "http://127.0.0.1:9999"


def test_seat_turn_runs_in_plan_mode_with_own_session(monkeypatch):
    fake = FakeHTTP({
        "/api/chat": (200, _stream(["report text"], events=["tool.started", "tool.completed", "tool.completed"])),
    })
    monkeypatch.setattr("urllib.request.urlopen", fake)
    turn = dispatch.live_seat_turn_factory(BASE)
    seat = Seat("s1", "builder", "", True)
    result = turn(seat, "investigate this", 1, "council-abc123")
    assert result["text"] == "report text"
    assert result["receipt_count"] == 2, "receipts count tool.completed events, not starts"
    path, body = fake.requests[0]
    assert path == "/api/chat"
    assert body["mode"] == "plan", "seats investigate read-only — plan mode is the law"
    assert body["model"] == "vool" and body["model_selection"] == "auto"
    assert body["session_id"].startswith("openclaw:"), "each seat runs in its own real chat session"
    assert result["session_id"] == body["session_id"]


def test_cloud_seat_pins_within_existing_acceptance_only(monkeypatch):
    pin_calls = []

    def _pin(body):
        pin_calls.append(body)
        if body.get("confirm_paid"):
            return (200, {"ok": True})
        return (409, {"ok": False, "code": "paid_model_confirm_required"})

    fake = FakeHTTP({
        "/api/cloud/model": _pin,
        "/api/chat": (200, _stream(["cloud report"])),
    })
    monkeypatch.setattr("urllib.request.urlopen", fake)
    monkeypatch.setattr(dispatch, "_acceptance_recorded", lambda model: True)
    turn = dispatch.live_seat_turn_factory(BASE)
    result = turn(Seat("s2", "falsifier", "vendor/cheap-flash", True), "p", 1, "run1")
    assert result["text"] == "cloud report"
    assert pin_calls == [
        {"model": "vendor/cheap-flash"},
        {"model": "vendor/cheap-flash", "confirm_paid": True},
    ], "confirm_paid may be sent ONLY after the bare pin was gated AND acceptance exists"


def test_unaccepted_paid_model_fails_typed_and_never_confirms(monkeypatch):
    fake = FakeHTTP({
        "/api/cloud/model": (409, {"ok": False, "code": "paid_model_confirm_required"}),
    })
    monkeypatch.setattr("urllib.request.urlopen", fake)
    monkeypatch.setattr(dispatch, "_acceptance_recorded", lambda model: False)
    turn = dispatch.live_seat_turn_factory(BASE)
    with pytest.raises(dispatch.SeatDispatchError, match="model_not_accepted"):
        turn(Seat("s2", "falsifier", "vendor/never-accepted", True), "p", 1, "run1")
    assert all(not body.get("confirm_paid") for _, body in fake.requests), (
        "the council must NEVER send confirm_paid for a model the operator did not accept"
    )


def test_price_rise_gate_is_an_operator_decision_not_a_council_one(monkeypatch):
    fake = FakeHTTP({
        "/api/cloud/model": (409, {"ok": False, "code": "price_above_accepted", "error": "price rose"}),
    })
    monkeypatch.setattr("urllib.request.urlopen", fake)
    monkeypatch.setattr(dispatch, "_acceptance_recorded", lambda model: True)
    turn = dispatch.live_seat_turn_factory(BASE)
    with pytest.raises(dispatch.SeatDispatchError, match="model_gated"):
        turn(Seat("s2", "falsifier", "vendor/rose", True), "p", 1, "run1")
    assert all(not body.get("confirm_paid") for _, body in fake.requests), (
        "a risen price is re-decided by the operator, never re-confirmed by the council"
    )


def test_empty_seat_answer_is_a_failure_not_a_report(monkeypatch):
    fake = FakeHTTP({"/api/chat": (200, _stream([""]))})
    monkeypatch.setattr("urllib.request.urlopen", fake)
    turn = dispatch.live_seat_turn_factory(BASE)
    with pytest.raises(dispatch.SeatDispatchError, match="no text"):
        turn(Seat("s1", "builder", "", True), "p", 1, "run1")


def test_seat_session_ids_are_stable_and_distinct():
    seat_a = Seat("s1", "builder", "", True)
    seat_b = Seat("s2", "falsifier", "", True)
    a = dispatch.seat_session_id("council-abc123", seat_a)
    assert a == dispatch.seat_session_id("council-abc123", seat_a), "same run+seat, same session"
    assert a != dispatch.seat_session_id("council-abc123", seat_b)
    assert a != dispatch.seat_session_id("council-zzz999", seat_a)
