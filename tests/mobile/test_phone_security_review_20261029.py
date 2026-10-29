"""Fixes from the independent security review of the phone branch (2026-10-29).

A phone continuing the owner's desktop chat inherited the chat-wide workspace grant, so injected text could
edit files with no approval anywhere; and changing a phone's folders wrote the full folder paths
(/Users/<name>/...) to the receipt log. The paid-keys, approvals-list and "my computer is lost" fixes are
checked in test_phone_full_vool_rights.py, test_mobile_companion_api.py and the lost-computer tests.
"""
from __future__ import annotations

import time
from types import SimpleNamespace

import core.mode_permission_policy as mpp
from core.request_trust import OWNER_LOCAL_KEY, REMOTE_DEVICE_KEY


def test_a_phone_turn_never_inherits_the_chats_standing_workspace_grant(monkeypatch):
    monkeypatch.setitem(mpp._CHAT_WORKSPACE_AUTHORITY, "chat-standing", "tok-standing")
    monkeypatch.setitem(mpp._INTERNAL_AUTHORITY, "tok-standing", {"expires_at": time.time() + 600, "session_id": "chat-standing"})
    owner = {"runtime_session_id": "chat-standing", OWNER_LOCAL_KEY: True}
    phone = {"runtime_session_id": "chat-standing", OWNER_LOCAL_KEY: False, REMOTE_DEVICE_KEY: "dev-1"}
    assert mpp._internal_authority_scope(owner) is not None
    assert mpp._internal_authority_scope(phone) is None
    # A remote turn with no device stamp (a chat app) is no different.
    assert mpp._internal_authority_scope({"runtime_session_id": "chat-standing", OWNER_LOCAL_KEY: False}) is None


def test_the_receipt_counts_a_phones_folders_and_never_names_them(tmp_path, monkeypatch):
    from core.mobile_companion import policy
    from core.wallet import caller_binding

    monkeypatch.setattr(caller_binding, "refusal_reason", lambda _headers: None)
    folder = tmp_path / "rivermaple" / "Documents"
    folder.mkdir(parents=True)
    monkeypatch.setattr(policy, "system_area", lambda _path: False)
    receipts = []
    device = {"device_id": "dev-1", "status": "active", "rules": {}, "notification_state": {"x": 1}}
    store = SimpleNamespace(
        device=lambda _id: dict(device),
        mark_device=lambda _id, **kw: device.update(kw),
        append_receipt=lambda kind, **kw: receipts.append((kind, kw)),
        edit_notifications=lambda *a, **k: None,
    )
    policy.update(SimpleNamespace(store=store), {"device_id": "dev-1", "rules": {"computer_tools": True},
                                                 "file_scope": {"mode": "folders", "folders": [str(folder)]}})
    assert device["file_scope"]["folders"] == [str(folder.resolve())]   # the phone's own record keeps them
    (kind, logged), = [row for row in receipts if row[0] == "device.rules_changed"]
    assert logged["detail"]["file_scope"] == {"mode": "folders", "folders": 1}
    assert "rivermaple" not in repr(receipts)
