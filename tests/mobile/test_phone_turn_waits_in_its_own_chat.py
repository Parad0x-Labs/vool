"""A phone turn that still stops for the owner's OK says so on the phone, and the phone's Allow finishes it.

What no standing right covers can still stop a turn for an approval. Before, the phone got the turn's
words and an empty bubble while the question waited on the computer's screen only (2026-11-23).
"""
from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace

import pytest

from tests.mobile.test_phone_quick_setup import home, phone  # noqa: F401 (fixtures)


class _Store:
    def __init__(self, device):
        self.record = device
        self.receipts = []

    def device(self, device_id):
        return dict(self.record) if device_id == self.record["device_id"] else None

    def mark_device(self, device_id, **fields):
        self.record.update(fields)
        return dict(self.record)

    def append_receipt(self, *a, **k):
        self.receipts.append((a, k))

    def remember_turn(self, *a, **k):
        return None


@pytest.fixture
def rig(phone, monkeypatch, tmp_path):
    import core.mode_permission_policy as mpp
    import core.runtime_task_events as events
    import core.web.api.mobile_companion_api as api
    import core.web.api.service as service_mod

    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "vool"))
    monkeypatch.setattr(mpp, "_persist_pending_approvals_locked", lambda: None)
    monkeypatch.setattr(mpp, "_ensure_approvals_restored", lambda: None)
    monkeypatch.setattr(mpp, "_APPROVALS", {})
    monkeypatch.setattr(events, "emit_runtime_event", lambda *a, **k: None)
    phone["rules"]["history"] = True
    store = _Store(phone)
    service = SimpleNamespace(store=store, dispatch_lock=threading.Lock(), utcnow=lambda: __import__("datetime").datetime.now())
    monkeypatch.setattr(api, "mobile_companion_service", lambda: service)
    bodies, finished = [], threading.Event()

    def dispatch_post(*, path, body, request_id, **_kw):
        bodies.append(dict(body))
        if body.get("approval_token"):
            finished.set()
            return SimpleNamespace(status=200, body=json.dumps({"message": {"content": "You have 3 unread emails."}}).encode())
        # The turn stops for an action no right covers: the real store records the question.
        mpp._APPROVALS["appr-1"] = {"approval_id": "appr-1", "task_id": request_id, "intent": "sandbox.run_command",
                                    "action_plain": "run a command on your computer", "status": "pending",
                                    "expires_at": time.time() + 600, "session_id": "s", "remote_device": "phone:abc",
                                    "scope_options": ["once"], "actions": ["write_workspace"]}
        return SimpleNamespace(status=200, body=json.dumps({"message": {"content": "Manual mode requires approval for this exact action."}}).encode())

    monkeypatch.setattr(service_mod, "dispatch_post", dispatch_post)
    return SimpleNamespace(api=api, service=service, store=store, bodies=bodies, finished=finished, mpp=mpp)


def _send(rig, phone_device):
    return rig.api._action_chats_send({"chat_id": "mob-chat", "message": "count my unread mail", "idempotency_key": "k" * 32},
                                      runtime=None, model_name="m", client_host="phone", workspace_root_provider=lambda: "/tmp",
                                      service=rig.service, device=phone_device)


def test_the_phone_gets_the_question_and_its_allow_finishes_the_turn(rig, phone, monkeypatch):
    import core.request_trust as rt

    monkeypatch.setattr(rt, "remote_turn_has_grant", lambda sc, grant: True)
    monkeypatch.setattr(rt, "_remote_intent_grant", lambda intent: rt.REMOTE_GRANT_COMPUTER)
    status, result = _send(rig, phone)
    assert status == 200, result
    question = result.get("approval")
    assert question and question["action_plain"] == "run a command on your computer", result
    assert question["phone_may_answer"] is True
    assert [row["approval_id"] for row in rig.store.record["phone_held_turns"]] == ["appr-1"]
    status, answer = rig.api._action_approvals_resolve(
        {"approval_id": "appr-1", "decision": "allow", "scope": "once"}, service=rig.service, device=phone,
        runtime=object(), model_name="m", client_host="phone", workspace_root_provider=lambda: "/tmp")
    assert status == 200, answer
    assert answer["resumed"]["chat_id"] == "mob-chat"
    assert rig.finished.wait(10), rig.bodies
    resumed = rig.bodies[-1]
    assert resumed["approval_token"] == "appr-1" and resumed["turn_id"] == result["turn_id"]
    assert resumed["messages"][0]["content"] == "count my unread mail"
    assert rig.store.record["phone_held_turns"] == []


def test_a_deny_runs_nothing(rig, phone, monkeypatch):
    import core.request_trust as rt

    monkeypatch.setattr(rt, "remote_turn_has_grant", lambda sc, grant: True)
    monkeypatch.setattr(rt, "_remote_intent_grant", lambda intent: rt.REMOTE_GRANT_COMPUTER)
    _send(rig, phone)
    status, answer = rig.api._action_approvals_resolve(
        {"approval_id": "appr-1", "decision": "deny", "scope": "once"}, service=rig.service, device=phone,
        runtime=object(), model_name="m", client_host="phone", workspace_root_provider=lambda: "/tmp")
    assert status == 200 and "resumed" not in answer
    assert not rig.finished.wait(1)
    assert rig.store.record["phone_held_turns"] == []


def test_a_question_only_the_computer_answers_says_so(rig, phone, monkeypatch):
    phone["rules"]["computer_tools"] = False
    status, result = _send(rig, phone)
    # Without the right that covers it, the phone may not answer: it is told where.
    assert result["approval"]["phone_may_answer"] is False
