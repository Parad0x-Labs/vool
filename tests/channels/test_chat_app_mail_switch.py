"""Mail from a chat app is its own switch: off by default, read and search when on, sending asks on the computer.

The owner's choice, 2026-11-27 (v0.8.1 list item 46): "how many unread emails?" from Telegram got a folder or
computer-only refusal that no switch could answer. Reading mail from a chat app puts the mail's text in that
chat service, so it is a switch of its own, 30 days at a time like the other strong rights; a send or reply is
never covered by it, and a draft's approval stays the owner's own act on the computer.
"""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from core import request_trust as rt

_REMOTE = {rt.HTTP_INGRESS_KEY: True, rt.OWNER_LOCAL_KEY: False, rt.REMOTE_DEVICE_KEY: "bot:telegram"}


class _Store:
    def __init__(self, device):
        self.record = device

    def device(self, device_id):
        return dict(self.record) if device_id == self.record["device_id"] else None


@pytest.fixture
def bot(monkeypatch):
    device = {"device_id": "bot:telegram", "status": "active", "kind": "bot", "platform": "telegram",
              "binding": {"state": "bound"}, "sealed_protocol": True, "rules": {"chat": True}, "right_expiry": {}}
    import core.mobile_companion.listener as listener
    import core.web.api.mobile_companion_api as api

    monkeypatch.setattr(api, "mobile_companion_service", lambda: SimpleNamespace(store=_Store(device)))
    monkeypatch.setattr(listener, "load_settings", lambda: {"enabled": True})
    return device


def _on(device, days=30):
    device["rules"]["mail"] = True
    device["right_expiry"]["mail"] = time.time() + days * 86400 - 60


READS = ("email.read", "email.open", "email.draft.get")
SENDS = ("email.send", "email.reply", "email.draft.send")


def test_off_by_default_and_said_as_the_mail_switch(bot):
    for intent in (*READS, *SENDS):
        assert rt.remote_turn_tool_refusal(intent, _REMOTE), intent
        said = rt.remote_turn_refusal_text(intent, _REMOTE)
        assert "Read my mail" in said and "folder" not in said.lower(), (intent, said)


def test_on_it_reads_without_asking_and_sends_still_ask(bot):
    _on(bot)
    for intent in READS:
        assert rt.remote_turn_tool_refusal(intent, _REMOTE) == "", intent
        assert rt.remote_standing_grant(intent, ("read_files",), _REMOTE) == "mail", intent
    for intent in SENDS:
        # Opened, so the turn can stop for the owner's OK on the computer; never covered by the switch.
        assert rt.remote_turn_tool_refusal(intent, _REMOTE) == "", intent
        assert rt.remote_standing_grant(intent, ("external_messages",), _REMOTE) == "", intent


def test_a_drafts_approval_is_never_a_chat_apps(bot):
    _on(bot)
    assert rt.remote_turn_tool_refusal("email.draft.approve", _REMOTE)


def test_the_switch_ends_on_its_own_and_is_never_open_ended(bot):
    bot["rules"]["mail"] = True
    bot["right_expiry"]["mail"] = time.time() - 5
    assert rt.remote_turn_tool_refusal("email.read", _REMOTE)
    bot["right_expiry"]["mail"] = 0  # a strong right with no end is no right
    assert rt.remote_turn_tool_refusal("email.read", _REMOTE)
    bot["right_expiry"]["mail"] = time.time() + 90 * 86400
    assert rt.remote_turn_tool_refusal("email.read", _REMOTE)


def test_the_mail_switch_opens_nothing_but_mail(bot):
    _on(bot)
    for intent in ("operator.schedule_reminder", "machine.read_file", "contacts.search", "web.search"):
        assert rt.remote_turn_tool_refusal(intent, _REMOTE), intent


def test_a_phone_is_not_given_mail_by_a_chat_app_switch(monkeypatch, bot):
    phone = {**_REMOTE, rt.REMOTE_DEVICE_KEY: "phone:abc"}
    _on(bot)
    assert rt.chat_app_mail_allowed(phone, "email.read") is False


def test_the_settings_page_offers_the_switch_with_its_warning():
    from core.vool_settings_page import render_vool_settings_html

    page = render_vool_settings_html()
    assert "dataset.right='mail'" in page and "phone.bot_mail_help" in page and "passes through {app}" in page
