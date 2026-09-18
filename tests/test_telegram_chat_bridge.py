"""Telegram chat bridge: owner-lock (TOFU + pinned), relay, /start, and message splitting."""
from __future__ import annotations

import json

import relay.bridge_workers.telegram_chat_bridge as tcb


def _bridge(monkeypatch, tmp_path, *, pinned=None):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    if pinned:
        monkeypatch.setenv("VOOL_TELEGRAM_OWNER_CHAT_ID", pinned)
    else:
        monkeypatch.delenv("VOOL_TELEGRAM_OWNER_CHAT_ID", raising=False)
    sent: list = []
    replies: dict = {}

    def tg(method, data=None, *, timeout=30.0):
        sent.append((method, data or {}))
        return {"ok": True, "result": []}

    def vool(text, chat_id):
        return replies.get(text, f"echo:{text}")

    return tcb.TelegramChatBridge(token="T", tg_call=tg, vool_call=vool), sent, replies


def _msg(chat_id, text, update_id=1):
    return {"update_id": update_id, "message": {"text": text, "chat": {"id": chat_id}}}


def _texts(sent):
    return [d.get("text", "") for m, d in sent if m == "sendMessage"]


def test_tofu_pairs_first_chat_then_locks_out_others(monkeypatch, tmp_path) -> None:
    b, sent, _ = _bridge(monkeypatch, tmp_path)
    b.handle_update(_msg(111, "hello", 1))                       # first chat -> becomes owner, gets a reply
    assert b.owner_chat_id == "111"
    assert any("echo:hello" in t for t in _texts(sent))
    assert json.loads((tmp_path / "config" / "telegram_chat_bridge.json").read_text())["owner_chat_id"] == "111"
    sent.clear()
    b.handle_update(_msg(999, "let me in", 2))                   # a different chat -> refused, never relayed
    assert any("Not authorized" in t for t in _texts(sent))
    assert not any("echo:" in t for t in _texts(sent))


def test_pinned_owner_from_env_skips_tofu(monkeypatch, tmp_path) -> None:
    b, sent, _ = _bridge(monkeypatch, tmp_path, pinned="222")
    assert b.owner_chat_id == "222"
    b.handle_update(_msg(111, "hi", 1))                          # not the pinned owner -> refused
    assert any("Not authorized" in t for t in _texts(sent))


def test_relays_owner_message_and_shows_typing(monkeypatch, tmp_path) -> None:
    b, sent, replies = _bridge(monkeypatch, tmp_path, pinned="5")
    replies["what time is it"] = "It is 3pm."
    b.handle_update(_msg(5, "what time is it", 1))
    assert "It is 3pm." in _texts(sent)
    assert any(m == "sendChatAction" for m, _ in sent)           # typing indicator while it thinks


def test_start_command_greets_not_relays(monkeypatch, tmp_path) -> None:
    b, sent, replies = _bridge(monkeypatch, tmp_path, pinned="5")
    replies["/start"] = "SHOULD-NOT-RELAY"
    b.handle_update(_msg(5, "/start", 1))
    joined = " ".join(_texts(sent))
    assert "Paired" in joined and "SHOULD-NOT-RELAY" not in joined


def test_ignores_updates_without_text(monkeypatch, tmp_path) -> None:
    b, sent, _ = _bridge(monkeypatch, tmp_path, pinned="5")
    b.handle_update({"update_id": 1, "message": {"chat": {"id": 5}}})
    assert sent == []


def test_split_respects_telegram_limit_and_strips_local_images() -> None:
    chunks = tcb._split_for_telegram("x" * 9000)
    assert len(chunks) >= 3 and all(len(c) <= tcb._MAX_TG_CHARS for c in chunks)
    stripped = tcb._strip_local_image_refs("here you go\n![a](file:///Users/x/r.png)\nenjoy")
    assert "file://" not in stripped and "here you go" in stripped and "enjoy" in stripped
