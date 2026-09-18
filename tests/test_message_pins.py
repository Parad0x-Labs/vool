"""Per-chat pinned messages: pin (idempotent by content), list newest-first, unpin, drop-on-delete."""
from __future__ import annotations

import pytest

from core import message_pins as mp
from core import runtime_paths


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path):
    runtime_paths.configure_runtime_home(tmp_path / "home")
    yield
    runtime_paths.configure_runtime_home(None)


def test_pin_list_unpin_roundtrip() -> None:
    sid = "openclaw:aaaa1111bbbb2222cccc"
    assert mp.list_pins(sid) == []
    ok, pin = mp.pin_message(sid, "assistant", "  the answer is 42  ")
    assert ok and pin["role"] == "assistant" and pin["text"] == "the answer is 42"  # trimmed
    assert mp.is_pinned(sid, "assistant", "the answer is 42") is True
    pins = mp.list_pins(sid)
    assert len(pins) == 1 and pins[0]["id"] == pin["id"]
    assert mp.unpin_message(sid, pin["id"]) is True
    assert mp.list_pins(sid) == []
    assert mp.unpin_message(sid, pin["id"]) is False  # already gone


def test_pin_is_idempotent_by_content() -> None:
    sid = "openclaw:dddd"
    mp.pin_message(sid, "user", "same text")
    mp.pin_message(sid, "user", "same text")  # second pin of identical content -> no duplicate
    assert len(mp.list_pins(sid)) == 1
    mp.pin_message(sid, "assistant", "same text")  # different role -> distinct pin
    assert len(mp.list_pins(sid)) == 2


def test_pin_rejects_empty_and_scopes_per_chat() -> None:
    ok, _ = mp.pin_message("openclaw:eeee", "assistant", "   ")
    assert ok is False
    mp.pin_message("openclaw:aa", "user", "chat A pin")
    mp.pin_message("openclaw:bb", "user", "chat B pin")
    assert [p["text"] for p in mp.list_pins("openclaw:aa")] == ["chat A pin"]  # no cross-chat bleed
    assert [p["text"] for p in mp.list_pins("openclaw:bb")] == ["chat B pin"]


def test_drop_session_pins_on_delete() -> None:
    sid = "openclaw:ffff"
    mp.pin_message(sid, "assistant", "one")
    mp.pin_message(sid, "assistant", "two")
    assert len(mp.list_pins(sid)) == 2
    assert mp.drop_session_pins(sid) is True
    assert mp.list_pins(sid) == []
    assert mp.drop_session_pins(sid) is False
