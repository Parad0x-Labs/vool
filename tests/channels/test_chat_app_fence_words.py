"""What a chat app's files right opens, and the words it reads when something stays shut.

A chat app holds files in folders the owner chose, or in all of them ("All my folders", sls 2026-11-27);
either way only file tools open, and an ask no folder could answer (mail, apps, commands) is said as
what it is, never as "change your folders" (Telegram 2026-11-27: "how many unread emails?" got the
folder list).
"""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from core import request_trust as rt

_REMOTE = {rt.HTTP_INGRESS_KEY: True, rt.OWNER_LOCAL_KEY: False, rt.REMOTE_DEVICE_KEY: "bot:telegram"}


@pytest.fixture(params=[{"mode": "all"}, {"mode": "folders", "folders": ["/home/person/Downloads"]}],
                ids=["all", "folders"])
def bot(request, monkeypatch):
    device = {"device_id": "bot:telegram", "kind": "bot", "platform": "telegram", "status": "active",
              "binding": {"state": "bound", "channel_id": "444", "user_id": "111"},
              "rules": {"chat": True, "computer_tools": True},
              "right_expiry": {"computer_tools": time.time() + 3600}, "file_scope": request.param}
    import core.web.api.mobile_companion_api as api

    store = SimpleNamespace(device=lambda device_id: dict(device) if device_id == device["device_id"] else None)
    monkeypatch.setattr(api, "mobile_companion_service", lambda: SimpleNamespace(store=store))
    return device


def test_only_file_tools_open_whatever_folders_it_holds(bot):
    assert rt.remote_turn_has_grant(_REMOTE, rt.REMOTE_GRANT_COMPUTER)
    assert rt.remote_turn_tool_refusal("machine.list_directory", _REMOTE) == ""
    assert rt.remote_turn_tool_refusal("machine.send_to_phone", _REMOTE) == ""
    for shut in ("email.read", "email.send", "operator.list_processes", "operator.read", "operator.command", "web.search",
                 "wallet.propose"):
        assert rt.remote_turn_tool_refusal(shut, _REMOTE), shut
    expected = None if bot["file_scope"]["mode"] == "all" else ("/home/person/Downloads",)
    assert rt.remote_turn_folders(_REMOTE) == expected


def test_a_mail_or_app_ask_is_never_told_to_change_folders(bot):
    for intent, expected in (("email.read", rt.CHAT_APP_MAIL_TEXT), ("email.send", rt.CHAT_APP_MAIL_TEXT),
                             ("operator.list_processes", rt.CHAT_APP_COMPUTER_ONLY_TEXT),
                             ("operator.read", rt.CHAT_APP_COMPUTER_ONLY_TEXT)):
        said = rt.remote_turn_refusal_text(intent, _REMOTE)
        assert said == expected and "folder" not in said.lower(), (intent, said)


def test_all_folders_needs_the_owners_files_switch(bot):
    bot["rules"] = {"chat": True, "computer_tools": False}
    assert not rt.remote_turn_has_grant(_REMOTE, rt.REMOTE_GRANT_COMPUTER)
    assert rt.remote_turn_tool_refusal("machine.list_directory", _REMOTE) == "owner_local_required"


def test_a_scope_that_is_not_all_or_folders_opens_nothing(bot):
    bot["file_scope"] = {"mode": "all", "folders": ["/"]}
    assert not rt.remote_turn_has_grant(_REMOTE, rt.REMOTE_GRANT_COMPUTER)
    bot["file_scope"] = {"mode": "everything"}
    assert not rt.remote_turn_has_grant(_REMOTE, rt.REMOTE_GRANT_COMPUTER)
