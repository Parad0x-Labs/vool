"""A preview growing old is not a stop: the cache sweep must never cancel the live turn that made the call.

A slow local model (or the next tool step) can keep a turn running more than the preview cache's lifetime after its
first provider call. Any later call, from that turn or another chat, sweeps the cache. The sweep used to set the
turn's own cancel Event, so a turn nobody stopped ended as "Cancelled before publication". A real stop (closing the
chat) still stops it, and a deadline that runs out is a timeout, never the user-stop code.
"""
from __future__ import annotations

import threading
import time

import pytest

from adapters.base_adapter import ModelRequest
from core import runtime_active_clock
from core.disclosure import wire
from core.memory_first_router import _cancel_check
from core.provider_call_deadline import PROVIDER_DEADLINE_KEY, ProviderCallDeadlineExceededError


def _preview(chat: str, turn: str, *, sent: bool) -> tuple[wire.Preview, threading.Event]:
    turn_event = threading.Event()
    item = wire.Preview(wire._home(), chat, turn, "ollama-local", "http://127.0.0.1:9/api/chat", b'{"m":1}', ())
    item.cancel_event, item.sent = turn_event, sent
    with wire._LOCK:
        wire._PREVIEWS[item.preview_id] = item
    return item, turn_event


@pytest.fixture(autouse=True)
def _clean_cache():
    yield
    with wire._LOCK:
        wire._PREVIEWS.clear()
        wire._CANCELLED.clear()


def test_an_old_sent_preview_expires_without_cancelling_its_live_turn():
    item, turn_event = _preview("chat-slow", "turn-slow", sent=True)
    item.created = time.monotonic() - (wire._TTL + 1)
    with wire._LOCK:
        wire._prune()  # what the next provider call, from any chat, runs first
    assert not turn_event.is_set()
    assert item.preview_id not in wire._PREVIEWS
    assert item.body == b"" and item.output_text == ""  # the cached request is still dropped
    assert (wire._home(), "chat-slow", "turn-slow") not in wire._CANCELLED


def test_a_preview_still_waiting_on_the_owner_is_left_to_its_own_wait():
    item, turn_event = _preview("chat-wait", "turn-wait", sent=False)
    item.created = time.monotonic() - (wire._TTL + 1)
    with wire._LOCK:
        wire._prune()
    assert not turn_event.is_set()
    assert item.preview_id in wire._PREVIEWS


def test_closing_the_chat_still_stops_its_turn():
    _, turn_event = _preview("chat-closed", "turn-closed", sent=True)
    wire.close_chat("chat-closed")
    assert turn_event.is_set()


def _request(*, person_stopped: bool) -> ModelRequest:
    marker = threading.Event()
    if person_stopped:
        marker.set()
    expired = runtime_active_clock.monotonic() - 1
    context = {"cancel_event": marker, PROVIDER_DEADLINE_KEY: expired}
    return ModelRequest(task_kind="chat", prompt="x", metadata={PROVIDER_DEADLINE_KEY: expired},
                        cancel_check=_cancel_check(context))


def test_an_expired_deadline_ends_the_call_as_a_timeout_not_a_stop():
    request = _request(person_stopped=False)
    assert request.is_cancelled()
    with pytest.raises(ProviderCallDeadlineExceededError):
        request.raise_if_cancelled()


def test_a_person_stop_stays_a_stop_after_the_deadline_too():
    request = _request(person_stopped=True)
    with pytest.raises(RuntimeError, match=r"^model_call_cancelled$"):
        request.raise_if_cancelled()
