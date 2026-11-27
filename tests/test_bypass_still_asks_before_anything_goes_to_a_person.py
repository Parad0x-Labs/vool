"""Bypass keeps the card for anything sent to a person, as it does for money (local-model leak check G3).

Bypass let mail, a reply, Drop and a chat-app message go with no card: a bad model's words, or the person's private data
it had just read, could leave in someone else's inbox unseen. Every other Bypass allowance is unchanged.
All names and values here are invented for this contract.
"""
from __future__ import annotations

import pytest

from core.mode_permission_policy import (
    MODE_PERMISSION_MATRIX,
    OperatingMode,
    PermissionAction,
    PermissionEffect,
    activate_bypass_grant,
    decide_tool_call,
    request_bypass_confirmation,
    reset_mode_permission_state,
    set_active_mode,
)


@pytest.fixture(autouse=True)
def _clean_mode_state():
    reset_mode_permission_state()
    yield
    reset_mode_permission_state()


def _bypass_context() -> dict[str, object]:
    cid = request_bypass_confirmation(session_id="chat-g3", task_id="turn-g3", scope="task", duration_seconds=60)
    grant = activate_bypass_grant(session_id="chat-g3", task_id="turn-g3", scope="task", duration_seconds=60,
                                  confirmation_id=cid)
    set_active_mode("chat-g3", "bypass_permissions", client_turn_id="turn-g3", bypass_token=grant["token"])
    return {"runtime_session_id": "chat-g3", "cancel_turn_id": "turn-g3"}


@pytest.mark.parametrize("intent, args", [
    ("email.send", {"to": "ona@example.invalid", "subject": "the boiler", "body": "filters are in the shed"}),
    ("email.reply", {"message_id": "m-17", "body": "see you on Friday"}),
    ("telegram.send_message", {"chat": "garden club", "text": "the gate code is 4471"}),
])
def test_a_send_to_a_person_asks_even_in_bypass(intent, args):
    decision = decide_tool_call(intent=intent, arguments=args, task_id="turn-g3", source_context=_bypass_context())
    assert decision.effect is PermissionEffect.REQUIRE_APPROVAL, decision.reason
    assert decision.approval_request


def test_the_rest_of_bypass_is_unchanged():
    row = MODE_PERMISSION_MATRIX[OperatingMode.BYPASS_PERMISSIONS]
    assert row[PermissionAction.SEND_EXTERNAL_MESSAGES] is PermissionEffect.REQUIRE_APPROVAL
    assert row[PermissionAction.FINANCIAL_ACTION] is PermissionEffect.REQUIRE_APPROVAL
    for action in (PermissionAction.MODIFY_FILES, PermissionAction.CREATE_FILES, PermissionAction.USE_NETWORK,
                   PermissionAction.RUN_SIDE_EFFECTING_COMMANDS):
        assert row[action] is PermissionEffect.ALLOW
    decision = decide_tool_call(intent="workspace.write_file", arguments={"path": "notes.txt", "content": "x"},
                                task_id="turn-g3", source_context=_bypass_context())
    assert decision.effect is PermissionEffect.ALLOW
