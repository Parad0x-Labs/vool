"""A forget does not stop the turn that made it, nor any turn whose model call already went out.

Mac run of b43e6aced (local qwen3:8b, real app from source): "Paršau pamiršti informaciją apie valtį." erased the boat's
rows (receipt "applied"), and the reply published was "Cancelled before publication: this turn was stopped while it
was working". Nobody pressed stop. The turn's own judge call had left a preview of its request in the cache; the
forget moved the chat's erasure epoch; the page's next poll of the pending previews found that preview out of date and
stopped its turn, as if the chat had been closed.

A sent request whose chat only had something forgotten since is dropped from the cache (its copy may hold what was
forgotten), and its turn keeps working. A request still waiting on the owner, and a chat that closed, still stop it.
"""
from __future__ import annotations

import random
import threading

import pytest

from core.disclosure import scope, wire


@pytest.fixture(autouse=True)
def _clean_cache():
    yield
    with wire._LOCK:
        wire._PREVIEWS.clear()
        wire._CANCELLED.clear()


def _preview(chat: str, turn: str, *, sent: bool, version: str) -> tuple[wire.Preview, threading.Event]:
    turn_event = threading.Event()
    item = wire.Preview(wire._home(), chat, turn, "ollama-local", "http://127.0.0.1:9/api/chat",
                        b'{"messages":[{"role":"user","content":"mano valtis melyna"}]}', ())
    item.cancel_event, item.sent, item.scope_version = turn_event, sent, version
    with wire._LOCK:
        wire._PREVIEWS[item.preview_id] = item
    return item, turn_event


def _epoch_moves(monkeypatch, rng):
    monkeypatch.setattr(scope, "current_version", lambda _chat, _project: f"epoch-{rng.randrange(2, 10**6)}")


@pytest.mark.parametrize("seed", range(3))
def test_a_forget_after_the_call_went_out_drops_the_copy_and_leaves_the_turn_running(monkeypatch, seed):
    rng = random.Random(f"own-forget-{seed}")
    chat, turn = f"chat-{rng.randrange(10**6)}", f"turn-{rng.randrange(10**6)}"
    item, turn_event = _preview(chat, turn, sent=True, version="epoch-1")
    _epoch_moves(monkeypatch, rng)
    listed = wire.previews(chat_id=chat) if rng.random() < 0.5 else wire.previews()
    assert listed == []
    assert not turn_event.is_set()
    assert item.preview_id not in wire._PREVIEWS and item.body == b""
    assert (wire._home(), chat, turn) not in wire._CANCELLED
    with pytest.raises(KeyError):
        wire.get_preview(item.preview_id)
    assert not turn_event.is_set()


def test_a_request_still_waiting_on_the_owner_is_stopped_by_a_forget(monkeypatch):
    _item, turn_event = _preview("chat-waiting", "turn-waiting", sent=False, version="epoch-1")
    _epoch_moves(monkeypatch, random.Random("waiting"))
    assert wire.previews(chat_id="chat-waiting") == []
    assert turn_event.is_set()


def test_a_closed_chat_still_stops_a_sent_turn(monkeypatch):
    _item, turn_event = _preview("chat-gone", "turn-gone", sent=True, version="epoch-1")

    def closed(_chat, _project):
        raise ValueError("This chat changed. Nothing was sent.")

    monkeypatch.setattr(scope, "current_version", closed)
    assert wire.previews(chat_id="chat-gone") == []
    assert turn_event.is_set()
