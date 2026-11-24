"""A chat-app turn that stops for an approval is approved on the computer only, then finished in the chat app.

The approval and its answer are the real store's (core.mode_permission_policy). The model turn that asks
for it is stood in for at the one seam the worker calls, because no model runs here; what is under test
is the worker: it says the turn waits for the computer, holds it, and after the owner's answer runs it
again under that approval (allowed) or says it did not run (denied, expired).
"""
import time

import pytest


@pytest.fixture
def worker(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    import core.mode_permission_policy as mpp
    from core.mobile_companion import bot_runtime
    from core.web.api import mobile_companion_api

    mobile_companion_api.reset_mobile_companion_service()
    store = mobile_companion_api.mobile_companion_service().store
    store.put_device({
        "device_id": "bot:telegram", "kind": "bot", "platform": "telegram", "name": "Telegram", "status": "active",
        "connection_id": "c1", "sealed_protocol": 2,
        "rules": {**bot_runtime.defaults(), "chat": True}, "right_expiry": {}, "payment_cap": {},
        "binding": {"state": "bound", "channel_id": "444", "user_id": "111"}, "last_update": 0,
    })
    sent, calls = [], []
    monkeypatch.setattr(bot_runtime.ManagedBot, "send", lambda self, channel, text, right="chat": sent.append((channel, text)))
    monkeypatch.setattr(bot_runtime.ManagedBot, "deliver_files", lambda self, channel: None)
    monkeypatch.setattr(bot_runtime, "turn_notes", lambda platform, result, locale="en": [])

    def fake_send(params, *, approval_token="", resume_turn_id="", **_kw):
        calls.append({"params": dict(params), "approval_token": approval_token, "resume_turn_id": resume_turn_id})
        turn = "mob-" + params["idempotency_key"][:40]
        if not approval_token:
            # The model loop asked for an action that needs the owner's OK: the real store records it.
            with mpp._LOCK:
                mpp._APPROVALS["appr-" + turn[-8:]] = {
                    "approval_id": "appr-" + turn[-8:], "task_id": turn, "session_id": "openclaw:" + "a" * 20,
                    "intent": "operator.schedule_calendar_event", "action_plain": "add “Dentist” to your calendar",
                    "status": "pending", "expires_at": time.time() + 600, "remote_device": "bot:telegram",
                }
            return 200, {"reply": "I need your OK for that.", "turn_id": turn, "chat_id": params["chat_id"]}
        return 200, {"reply": "Added Dentist to your calendar.", "turn_id": turn, "chat_id": params["chat_id"]}

    monkeypatch.setattr(mobile_companion_api, "_action_chats_send", fake_send)
    bot = bot_runtime.provider_class("telegram")(runtime=type("R", (), {"runtime_model_tag": "m"})(), token="t" * 46)
    yield bot, sent, calls, mpp
    with mpp._LOCK:
        for key in [k for k in mpp._APPROVALS if k.startswith("appr-")]:
            mpp._APPROVALS.pop(key, None)
    mobile_companion_api.reset_mobile_companion_service()


def held(bot):
    return bot.device().get("bot_held_turns") or []


def test_allowed_on_the_computer_the_turn_finishes_in_the_chat_app(worker):
    bot, sent, calls, mpp = worker
    bot.consume("444", "111", "put the dentist in my calendar for friday 3pm", 7)
    assert "needs your OK on your computer" in sent[-1][1] and "Dentist" in sent[-1][1], sent
    assert len(held(bot)) == 1
    approval_id = held(bot)[0]["approval_id"]
    bot._resume_approved()  # still waiting: nothing runs
    assert len(calls) == 1 and len(held(bot)) == 1
    assert mpp.resolve_approval(approval_id, decision="allow") is not None
    bot._resume_approved()
    assert calls[-1]["approval_token"] == approval_id
    assert calls[-1]["resume_turn_id"] == held_turn(calls)  # the turn that asked, not a new one
    assert calls[-1]["params"]["idempotency_key"] != calls[0]["params"]["idempotency_key"]
    assert sent[-1] == ("444", "Added Dentist to your calendar.")
    assert held(bot) == []
    bot._resume_approved()
    assert len(calls) == 2  # once


def held_turn(calls):
    return "mob-" + calls[0]["params"]["idempotency_key"][:40]


@pytest.mark.parametrize("ending", ["deny", "expire"])
def test_denied_or_expired_says_so_and_runs_nothing(worker, ending):
    bot, sent, calls, mpp = worker
    bot.consume("444", "111", "add a calendar entry for the dentist", 8)
    approval_id = held(bot)[0]["approval_id"]
    if ending == "deny":
        assert mpp.resolve_approval(approval_id, decision="deny") is not None
    else:
        with mpp._LOCK:
            mpp._APPROVALS[approval_id]["expires_at"] = time.time() - 1
    bot._resume_approved()
    assert len(calls) == 1
    assert ("said no" if ending == "deny" else "expired") in sent[-1][1]
    assert held(bot) == []


def test_the_owners_page_leaves_a_chat_app_turn_to_the_app(worker):
    bot, sent, calls, mpp = worker
    bot.consume("444", "111", "schedule the dentist on friday", 9)
    rows = mpp.pending_approvals_for_session("openclaw:" + "a" * 20)
    assert rows and rows[0]["asked_from"] == "telegram"
