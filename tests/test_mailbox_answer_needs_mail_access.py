"""A question about the user's received mail, on a runtime that cannot read any mailbox (audit 2026-10-29).

With no mail account configured, "did i get any new emails today?" was answered "No new emails received
today. - Checked email inbox for today's messages." and "any messages from the bank in my inbox?" with
"No messages from the bank found in your inbox. - Checked the inbox". No tool was offered (the email lane
is off without an account) and none ran: the model invented the check. The final honesty gate decides
from the REQUEST (the typed mailbox recognizer the email.read seat uses) and the runtime's mail access,
never from the reply's wording, so every phrasing of the fabricated check gets the same honest answer.
"""
from __future__ import annotations

import json

import pytest

from core.agent_runtime.action_honesty_validator import enforce_final_action_honesty

FABRICATED = (
    ("did i get any new emails today?",
     "No new emails received today.\n- Checked email inbox for today's messages.\n- No unread or new emails found."),
    ("any messages from the bank in my inbox?",
     "No messages from the bank found in your inbox.\n- Checked the inbox for messages from the bank.\n"
     "- No messages were found."),
    # Other wordings of the same invention: the gate does not read the reply's sentences.
    ("Has Northfield Kiln Repair emailed me since Monday?",
     "Yes, they sent an estimate on Tuesday for $420."),
    ("Anything unread in our inbox from the glaze supplier?", "Your inbox is all caught up!"),
)
NOT_A_MAILBOX_REQUEST = (
    ("How do I stop emails from going to spam?", "Mark them as not spam and add the sender to contacts."),
    ("Write me a sample cold outreach email template for freelance clients.", "Subject: Quick idea\n\nHi ..."),
    ("What does the inbox zero method mean?", "Inbox zero means keeping your inbox empty."),
    ("Without using any tools, what is in my inbox?", "I can't see your inbox without tools."),
)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    from core import policy_engine, runtime_paths

    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    previous = policy_engine._POLICY_CACHE
    policy_engine._POLICY_CACHE = None
    try:
        yield
    finally:
        policy_engine._POLICY_CACHE = previous
        runtime_paths.configure_runtime_home(None)


def _email_lane(enabled: bool) -> None:
    from core import policy_engine

    policy = dict(policy_engine.load(force_reload=True))
    policy["email"] = {**(policy.get("email") or {}), "read_enabled": enabled, "send_enabled": enabled}
    policy_engine._POLICY_CACHE = policy


def _store_imap(account: str = "default") -> None:
    from core import credential_store

    blob = {"host": "imap.example.com", "port": 993, "username": "me@example.com", "password": "pw",
            "from_addr": "me@example.com"}
    credential_store.store_credential(f"email.imap.{account}", json.dumps(blob), label="test imap")


def _guard(prompt: str, response: str, *, source_context: dict | None = None, **fields):
    return enforce_final_action_honesty(
        {"response": response, "confidence": 0.8, **fields},
        user_input=prompt,
        effective_input=prompt,
        session_id="mailbox-access-session",
        source_context=source_context or {"surface": "api", "allow_remote_fetch": False},
    )


@pytest.mark.parametrize(("prompt", "reply"), FABRICATED)
def test_no_account_an_invented_inbox_check_becomes_the_honest_answer(prompt: str, reply: str) -> None:
    _email_lane(False)
    result = _guard(prompt, reply)
    response = result["response"]
    assert "No email account is connected" in response and "Settings > Email accounts" in response
    assert "Checked" not in response and reply not in response
    validator = result["action_honesty_validator"]
    assert validator["reason"] == "mailbox_request_without_mail_access"
    assert validator["mail_access_gap"] == "no_account"


def test_no_account_with_the_email_lane_on_is_still_no_account() -> None:
    _email_lane(True)
    result = _guard(*FABRICATED[0])
    assert result["action_honesty_validator"]["mail_access_gap"] == "no_account"


def test_an_account_with_reading_switched_off_says_so() -> None:
    _store_imap()
    _email_lane(False)
    result = _guard(*FABRICATED[1])
    assert "Reading email is switched off" in result["response"]
    assert result["action_honesty_validator"]["mail_access_gap"] == "reading_off"


def test_a_readable_account_leaves_the_reply_to_the_email_tools() -> None:
    _store_imap()
    _email_lane(True)
    prompt, reply = FABRICATED[0]
    result = _guard(prompt, reply)
    assert result["response"] == reply
    assert "action_honesty_validator" not in result


@pytest.mark.parametrize(("prompt", "reply"), NOT_A_MAILBOX_REQUEST)
def test_talk_about_email_is_not_a_mailbox_request(prompt: str, reply: str) -> None:
    _email_lane(False)
    result = _guard(prompt, reply)
    assert result["response"] == reply
    assert "action_honesty_validator" not in result


def test_an_email_tool_result_is_its_own_grounding() -> None:
    _email_lane(False)
    reply = "No IMAP credentials for account 'default'. Add your email account in Settings."
    result = _guard(FABRICATED[0][0], reply, intent="email.read")
    assert result["response"] == reply
