"""A right the owner switched on for a phone is the owner's answer: the phone's turn is not stopped
for a prompt only the computer shows.

sls 2026-11-23: the phone held "Use tools on my computer" with every file, asked how many unread
emails there were, and got an empty bubble while "read your email - Allow once / Deny" waited on the
Mac. "Once it is given for mobile or socials it should use those permissions instead of asking again
on the desktop." What no right covers (money, secrets, VOOL's own settings, unknown effects) still asks.
"""
from __future__ import annotations

import time

import pytest

from core import request_trust as rt
from tests.mobile.test_phone_quick_setup import home, phone

_REMOTE = {rt.HTTP_INGRESS_KEY: True, rt.OWNER_LOCAL_KEY: False}


@pytest.fixture
def manual(monkeypatch, tmp_path):
    import core.mode_permission_policy as mpp

    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "vool"))
    monkeypatch.setattr(mpp, "_persist_pending_approvals_locked", lambda: None)
    monkeypatch.setattr(mpp, "_ensure_approvals_restored", lambda: None)
    monkeypatch.setattr(mpp, "_APPROVALS", {})
    return mpp


def _effect(mpp, intent, arguments, context):
    return mpp.preview_tool_call(intent=intent, arguments=arguments, task_id="mob-1",
                                 source_context={"session_id": "s", **context})


def test_a_tool_the_phone_right_covers_runs_without_a_prompt(phone, manual, home):
    context = {**_REMOTE, rt.REMOTE_DEVICE_KEY: "phone:abc"}
    target = {"path": str(home / "Documents" / "plan.txt"), "content": "x"}
    # The owner's own session in Manual mode is asked, as before.
    assert _effect(manual, "workspace.write_file", target, {}).effect.value == "require_approval"
    allowed = _effect(manual, "workspace.write_file", target, context)
    assert allowed.effect.value == "allow", allowed
    assert not allowed.would_request_approval


def test_without_the_right_the_phone_is_not_let_through(phone, manual, home):
    phone["rules"]["computer_tools"] = False
    context = {**_REMOTE, rt.REMOTE_DEVICE_KEY: "phone:abc"}
    target = {"path": str(home / "Documents" / "plan.txt"), "content": "x"}
    assert _effect(manual, "workspace.write_file", target, context).effect.value != "allow"


def test_an_expired_right_is_not_a_grant(phone, manual, home):
    phone["right_expiry"]["computer_tools"] = time.time() - 5
    context = {**_REMOTE, rt.REMOTE_DEVICE_KEY: "phone:abc"}
    target = {"path": str(home / "Documents" / "plan.txt"), "content": "x"}
    assert _effect(manual, "workspace.write_file", target, context).effect.value != "allow"


@pytest.mark.parametrize("actions", [("financial_or_paid_actions",), ("access_secrets",), ("change_settings",),
                                     ("unknown_side_effect",), ("write_workspace", "change_provider_configuration")])
def test_money_secrets_settings_and_unknown_effects_always_ask(phone, actions):
    context = {**_REMOTE, rt.REMOTE_DEVICE_KEY: "phone:abc"}
    assert rt.remote_standing_grant("workspace.write_file", actions, context) == ""


def test_the_owners_own_session_is_not_a_remote_grant(phone):
    assert rt.remote_standing_grant("workspace.write_file", ("write_workspace",), {}) == ""
    assert rt.remote_standing_grant("workspace.write_file", ("write_workspace",),
                                    {rt.OWNER_LOCAL_KEY: True, rt.REMOTE_DEVICE_KEY: "phone:abc"}) == ""


def test_look_things_up_online_answers_its_own_lookups(phone):
    phone["rules"] = {"computer_tools": False, "paid_services": True}
    phone["right_expiry"] = {"paid_services": time.time() + 3600}
    context = {**_REMOTE, rt.REMOTE_DEVICE_KEY: "phone:abc"}
    assert rt.remote_standing_grant("web.search", ("network_access",), context) == rt.REMOTE_GRANT_PAID
    # It does not open the computer's tools.
    assert rt.remote_standing_grant("workspace.write_file", ("write_workspace",), context) == ""


def test_reading_email_from_a_phone_with_its_tools_on_is_not_asked_again(phone, manual):
    # The measured case: "how many unread emails" from the phone, Manual mode on the computer.
    context = {**_REMOTE, rt.REMOTE_DEVICE_KEY: "phone:abc"}
    decided = _effect(manual, "email.read", {"unseen_only": True}, context)
    assert decided.effect.value == "allow", decided
    assert _effect(manual, "email.read", {"unseen_only": True}, {}).effect.value == "require_approval"
