"""A phone turn the computer is still answering reaches the phone as "still working", never as an empty answer.

Real-model test 2026-10-28 (qwen3:8b): phone turns that took over 30 seconds came back empty. The phone gave up
at 30 s and its outbox resent the same turn; the chat door, which never runs a turn twice, answered the resend
with an empty "request_in_progress" reply, and the phone took that as the answer.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import core.web.api.service as service_module
from core.web.api import mobile_companion_api as api


def _send(monkeypatch, payload):
    monkeypatch.setattr(service_module, "dispatch_post",
                        lambda **_kw: SimpleNamespace(status=200, body=json.dumps(payload).encode()))
    return api._action_chats_send({"message": "whats on my desktop", "chat_id": "mob-abc"}, runtime=None, model_name="m",
                                  client_host="127.0.0.1", workspace_root_provider=None, service=None,
                                  device={"device_id": "phone:test", "kind": "phone"})


def test_a_turn_still_running_is_pending_not_an_empty_answer(monkeypatch):
    status, result = _send(monkeypatch, {"message": {"role": "assistant", "content": ""}, "done": False,
                                         "done_reason": "request_in_progress",
                                         "vool_response_commit": {"type": "response.in_progress"}})
    assert status == 202 and result["pending"] is True and "reply" not in result


def test_a_finished_turn_is_still_the_answer(monkeypatch):
    status, result = _send(monkeypatch, {"message": {"role": "assistant", "content": "todo.txt"}, "done": True,
                                         "done_reason": "stop"})
    assert status == 200 and result["reply"] == "todo.txt"


def test_the_phone_waits_minutes_for_a_chat_turn_and_seconds_for_the_rest():
    from pathlib import Path

    client = (Path(__file__).resolve().parents[2] / "mobile" / "src" / "protocol" / "client.ts").read_text()
    assert "export const CHAT_TURN_TIMEOUT_MS = 240000;" in client
    assert '...(action === "chats.send" ? { timeoutMs: CHAT_TURN_TIMEOUT_MS } : {}),' in client
    assert "setTimeout(() => controller.abort(), timeoutMs ?? 30000)" in client
