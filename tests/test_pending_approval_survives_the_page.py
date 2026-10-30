"""A pending approval can be answered after the page that saw it is gone.

Measured on 1cbf433 (fresh-user test, 2026-10-29): in Manual mode a file action said "needs your
approval", but after a reload, or for a turn the page did not send itself, no Allow existed
anywhere; the bell offered only Dismiss, and the chat showed "This chat was interrupted when VOOL
stopped" although VOOL never stopped. The prompt reached the page only on the turn's own stream.

The store has always kept the prompt durably. These tests pin its per-chat read and the owner-local
door the page rebuilds the permission bar from.
"""

from __future__ import annotations

import json

import pytest

from core import runtime_paths
from core.mode_permission_policy import (
    PermissionEffect,
    decide_tool_call,
    pending_approvals_for_session,
    reset_mode_permission_state,
    resolve_approval,
    set_active_mode,
)

_ASKED = "make a folder in Documents called Taxes"


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    runtime_paths.configure_runtime_home(tmp_path / "home")
    reset_mode_permission_state()
    yield
    reset_mode_permission_state()
    runtime_paths.configure_runtime_home(None)


def _ask(session: str = "chat-a", turn: str = "turn-a", text: str = _ASKED) -> dict:
    set_active_mode(session, "manual", client_turn_id=turn)
    decision = decide_tool_call(
        intent="machine.ensure_directory",
        arguments={"path": "~/Documents/Taxes"},
        task_id=turn,
        source_context={
            "runtime_session_id": session,
            "operating_mode": "manual",
            "cancel_turn_id": turn,
            "turn_request": {"user_text": text},
        },
    )
    assert decision.effect is not PermissionEffect.ALLOW
    assert decision.approval_request
    return decision.approval_request


def test_the_chat_reads_back_the_prompt_it_was_shown() -> None:
    shown = _ask()

    rows = pending_approvals_for_session("chat-a")

    assert [row["approval_id"] for row in rows] == [shown["approval_id"]]
    row = rows[0]
    # The same prompt the stream carried, plus the message that asked (what Allow re-sends).
    for key in ("action", "affected_resources", "scope_options", "intent", "task_id", "expires_at"):
        assert row[key] == shown[key], key
    assert row["request_text"] == _ASKED
    # Grant bookkeeping never leaves the store.
    for secret in ("fingerprint", "mode", "mode_revision", "actions", "batch_fingerprints", "session_id"):
        assert secret not in row


def test_another_chats_prompt_is_not_shown_here() -> None:
    _ask(session="chat-b", turn="turn-b")

    assert pending_approvals_for_session("chat-a") == []
    assert pending_approvals_for_session("") == []


def test_an_answered_prompt_is_gone_either_way() -> None:
    allowed = _ask(turn="turn-1")
    denied = _ask(turn="turn-2", text="make a folder in Documents called Bills")

    assert resolve_approval(allowed["approval_id"], decision="allow") is not None
    assert resolve_approval(denied["approval_id"], decision="deny") is not None

    assert pending_approvals_for_session("chat-a") == []


def test_an_expired_prompt_is_not_offered(monkeypatch) -> None:
    import core.mode_permission_policy as mpp

    shown = _ask()
    real_time = mpp.time.time
    monkeypatch.setattr(mpp.time, "time", lambda: real_time() + 86400 + 60)

    assert pending_approvals_for_session("chat-a") == []
    assert resolve_approval(shown["approval_id"], decision="allow") is None


def test_the_prompt_survives_a_restart() -> None:
    import core.mode_permission_policy as mpp

    shown = _ask()
    # A restart: memory is empty, the durable mirror is what is left.
    mpp._APPROVALS.clear()
    mpp._PERSISTED_APPROVALS_RESTORED = False

    rows = pending_approvals_for_session("chat-a")

    assert [row["approval_id"] for row in rows] == [shown["approval_id"]]
    assert rows[0]["request_text"] == _ASKED


def _get(session: str, host: str):
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get

    res = dispatch_get(
        path="/api/chat/approvals",
        query={"session": [session]},
        runtime=RuntimeServices(display_name="N"),
        model_name="vool",
        client_host=host,
    )
    return res.status, json.loads(res.body.decode("utf-8"))


def test_the_door_serves_the_owner_only() -> None:
    shown = _ask()

    status, payload = _get("chat-a", "127.0.0.1")
    assert status == 200
    assert [row["approval_id"] for row in payload["approvals"]] == [shown["approval_id"]]

    status, payload = _get("chat-a", "203.0.113.9")
    assert status == 403
    assert "approvals" not in payload


def test_the_door_answers_empty_for_a_chat_with_nothing_waiting() -> None:
    status, payload = _get("chat-none", "127.0.0.1")
    assert status == 200
    assert payload["approvals"] == []


def test_the_asking_message_is_kept_masked_like_the_transcript() -> None:
    """The page finds the asking message in the masked transcript, and the prompt is mirrored to
    disk: a card number typed into the request is never stored in the clear."""
    from core.secret_redaction import redact_secrets

    typed = "make a folder in Documents called Taxes, my card is 4111 1111 1111 1111"
    _ask(text=typed)

    row = pending_approvals_for_session("chat-a")[0]
    assert row["request_text"] == redact_secrets(typed)
    assert "4111 1111 1111 1111" not in row["request_text"]
    import os
    from pathlib import Path

    for path in Path(os.environ["VOOL_HOME"]).rglob("pending_approvals.json"):
        assert "4111 1111 1111 1111" not in path.read_text()
