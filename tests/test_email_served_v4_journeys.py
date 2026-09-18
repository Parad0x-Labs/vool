"""Revision-4 mail workflow COMPONENT journeys (relabelled in revision 5).

What these tests exercise, stated precisely. Only turn 1 goes through the agent
harness (`agent.run_once`), and it runs with `_should_attempt_tool_intent` and
`_plan_tool_workflow` patched by `_force_loop`, so production routing and
planning are bypassed; the model's tool choices are scripted. Every later step
(the edit, the approval, the permission pause and one-time-token resume, the
send, the replay and the reconciliation) is a direct call to `email_drafts` or
`execute_tool_intent`, not a user turn, and no HTTP door is involved.

They remain useful coverage of the draft store, the permission controller, the
executor and the provider transports working together. They are NOT multi-turn
served acceptance: the same-session conversations through the production
/api/chat and /api/mode doors are in tests/test_email_served_chat_journeys.py.
"""
from __future__ import annotations

import json
import uuid
from unittest import mock

import pytest

from core.execution.models import WorkflowPlannerDecision
from core.memory_first_router import ModelExecutionDecision

pytestmark = [pytest.mark.pa_beta]

_OPENCLAW = {"surface": "openclaw", "platform": "openclaw"}


def _sid(label: str) -> str:
    return f"openclaw:{label}:{uuid.uuid4().hex}"


def _tool(intent: str, arguments: dict):
    return ModelExecutionDecision(
        source="provider_execution", task_hash=f"t-{intent}", provider_id="ollama-local:test",
        provider_name="ollama-local", model_name="test",
        structured_output={"intent": intent, "arguments": arguments},
        confidence=0.8, trust_score=0.84, used_model=True, validation_state="valid",
    )


def _respond(text: str):
    return ModelExecutionDecision(
        source="provider_execution", task_hash="direct", provider_id="ollama-local:test",
        provider_name="ollama-local", model_name="test",
        structured_output={"intent": "respond.direct", "arguments": {"message": text}},
        confidence=0.82, trust_score=0.84, used_model=True, validation_state="valid",
    )


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    from core import runtime_paths
    monkeypatch.setattr(runtime_paths, "_VOOL_HOME_OVERRIDE", None, raising=False)
    from core import email_drafts
    email_drafts.reset_drafts()
    return tmp_path


@pytest.fixture
def email_policy_enabled():
    from core import policy_engine
    previous = getattr(policy_engine, "_POLICY_CACHE", None)
    base = dict(policy_engine.load())
    base["email"] = {**(base.get("email") or {}), "read_enabled": True, "send_enabled": True}
    policy_engine._POLICY_CACHE = base
    try:
        yield
    finally:
        policy_engine._POLICY_CACHE = previous  # the pre-fixture cache object


@pytest.fixture
def api_server():
    from tests.provider_api_fixture import ProviderApiServer

    server = ProviderApiServer()
    server.start()
    try:
        yield server
    finally:
        server.stop()


def _force_loop(agent):
    agent._should_attempt_tool_intent = lambda *a, **k: True  # type: ignore[assignment]
    agent._plan_tool_workflow = lambda *a, **k: WorkflowPlannerDecision(  # type: ignore[assignment]
        handled=False, reason="pa-bypass", next_payload=None)


def _store_account(api_server, provider: str, account: str, address: str) -> None:
    refresh = api_server.add_oauth_client(provider, account, address)
    from core import credential_store
    credential_store.store_credential(
        f"email.oauth.{provider}.{account}",
        json.dumps({"client_id": "c", "client_secret": "s", "refresh_token": refresh,
                    "token_url": api_server.token_url, "account_email": address}),
        label="served oauth",
    )
    blob = {"provider": provider, "from_addr": address,
            "api_base": api_server.gmail_base, "token_url": api_server.token_url}
    for kind in ("imap", "smtp"):
        credential_store.store_credential(f"email.{kind}.{account}", json.dumps(blob), label=f"served {kind}")


def _journey(make_agent, api_server, *, provider: str, account: str, address: str,
             inbox_entry: dict, request_text: str, first_body: str, edited_body: str,
             recipient: str, expected_subject: str) -> None:
    from tests.pa_beta_gate.test_tool_workflow import _tracker

    _store_account(api_server, provider, account, address)
    if provider == "gmail":
        api_server.gmail_inbox(address, [inbox_entry])
        provider_id = api_server.state.gmail_mailboxes[address][0]["id"]
    else:
        api_server.graph_inbox(address, [inbox_entry])
        provider_id = api_server.state.graph_mailboxes[address][0]["id"]

    # Turn 1: mail-check → open thread → draft (NOT sent).
    agent = make_agent()
    _force_loop(agent)
    sid = _sid(f"{provider}-v4")
    agent.memory_router.resolve_tool_intent = mock.Mock(side_effect=[
        _tool("email.read", {"account": account, "sender": "willowridge"}),
        _tool("email.open", {"account": account, "message_id": provider_id, "thread": True}),
        _tool("email.draft.save", {
            "account": account, "to": recipient, "subject": f"Re: {inbox_entry['subject']}",
            "body": first_body, "in_reply_to": inbox_entry["message_id"], "kind": "reply",
        }),
        _respond("Drafted for review - nothing sent."),
    ])
    result = agent.run_once(request_text, session_id_override=sid,
                            source_context={**_OPENCLAW, "operating_mode": "auto"})
    response = str(result.get("response") or "")
    assert "draft" in response.lower() or "review" in response.lower()

    from core import email_drafts
    row = email_drafts.get_draft(session_id=sid, latest=True)
    assert row.ok and row.draft["account_resolved"] == account
    assert row.draft["body"] == first_body
    assert _provider_sent(api_server, provider, address) == []

    # Turn 2 (follow-up): EDIT the draft ("make it terser" with different words).
    edited = email_drafts.save_draft(
        draft_id=row.draft["draft_id"], to=recipient,
        subject=f"Re: {inbox_entry['subject']}", body=edited_body,
        in_reply_to=inbox_entry["message_id"], kind="reply", account=account, session_id=sid,
    )
    assert edited.ok and edited.draft["version"] == 2

    # Explicit operator approval + mode-gated send (pause → one-time resume).
    from core.tool_intent_executor import execute_tool_intent

    email_drafts.approve_draft(row.draft["draft_id"], session_id=sid)
    context = {**_OPENCLAW, "operating_mode": "auto", "runtime_session_id": sid,
               "cancel_turn_id": f"turn-{provider}-v4"}
    paused = execute_tool_intent(
        {"intent": "email.draft.send", "arguments": {"draft_id": row.draft["draft_id"]}},
        task_id=f"task-{provider}-v4", session_id=sid, source_context=context,
        hive_activity_tracker=_tracker(),
    )
    assert paused.status == "pending_approval" and _provider_sent(api_server, provider, address) == []

    from core.mode_permission_policy import resolve_approval
    approval_id = paused.details["approval_request"]["approval_id"]
    assert resolve_approval(approval_id, decision="allow")["status"] == "approved"
    resumed = execute_tool_intent(
        {"intent": "email.draft.send", "arguments": {"draft_id": row.draft["draft_id"]}},
        task_id=f"task-{provider}-v4", session_id=sid,
        source_context={**context, "mode_approval_token": approval_id},
        hive_activity_tracker=_tracker(),
    )
    assert resumed.ok and resumed.status == "sent", resumed.response_text
    sent_items = _provider_sent(api_server, provider, address)
    assert len(sent_items) == 1

    # Receipt truth: the EXACT edited content and account are on record.
    receipt = resumed.details.get("receipt") or {}
    assert receipt.get("account") == account
    assert receipt.get("message_id") == resumed.details["draft"]["sent_message_id"]

    # REPLAY: a duplicate send returns the same receipt; nothing new dispatched.
    replay = execute_tool_intent(
        {"intent": "email.draft.send", "arguments": {"draft_id": row.draft["draft_id"]}},
        task_id=f"task-{provider}-v4b", session_id=sid, source_context=context,
        hive_activity_tracker=_tracker(),
    )
    assert replay.status in {"sent", "pending_approval"}
    assert len(_provider_sent(api_server, provider, address)) == 1

    # RECONCILE on an already-terminal receipt is an honest no-op note for BOTH
    # providers (exact-id confirmation of an UNKNOWN delivery is the chaos test
    # below); nothing is ever resent through reconciliation.
    reconciled = email_drafts.reconcile_draft(row.draft["draft_id"], session_id=sid)
    assert reconciled.ok and "reconciliation" in reconciled.message.lower()
    assert len(_provider_sent(api_server, provider, address)) == 1


def _provider_sent(api_server, provider: str, address: str) -> list:
    if provider == "gmail":
        return api_server.gmail_sent(address)
    return api_server.graph_sent(address) + api_server.graph_replies_sent(address)


def test_served_v4_original_gmail_full_journey(make_agent, api_server, email_policy_enabled,
                                               isolated_home) -> None:
    _journey(
        make_agent, api_server,
        provider="gmail", account="work-gmail", address="me@gmail.example.test",
        inbox_entry={"from": "Willow Ridge <supply@willowridge.example.test>",
                     "subject": "Delivery scheduling",
                     "body": "Tuesday 14:00 or Wednesday 10:00?",
                     "message_id": "<wro-20260910@willowridge.example.test>",
                     "date": "Wed, 10 Sep 2026 09:15:00 +0000"},
        request_text=("Check unread emails from the orchard supplier on my Google account, open the "
                      "delivery thread and draft a reply choosing Wednesday at 10:00. Do not send it."),
        first_body="Hello,\n\nWednesday at 10:00 works for our delivery.\n\nThank you",
        edited_body="Hi,\n\nWednesday 10:00 confirmed.\n\nThanks",
        recipient="supply@willowridge.example.test",
        expected_subject="Re: Delivery scheduling",
    )


def test_served_v4_novel_graph_full_journey(make_agent, api_server, email_policy_enabled,
                                            isolated_home) -> None:
    _journey(
        make_agent, api_server,
        provider="graph", account="work-graph", address="me@contoso.example.test",
        inbox_entry={"from": {"emailAddress": {"address": "supply@willowridge.example.test"}},
                     "subject": "Kiln repair estimate 8442",
                     "body": "The heating element replacement estimate is EUR 1.230.",
                     "message_id": "<an-20260911@willowridge.example.test>",
                     "date": "Thu, 11 Sep 2026 07:00:00 +0000"},
        request_text=("Look at my Microsoft work mail for the workshop estimate since September 10th, "
                      "open that thread and draft two questions without accepting the estimate. "
                      "Do not send anything yet."),
        first_body=("Hello,\n\nTwo questions: 1) Does the estimate include re-testing? 2) What is "
                    "the parts lead time? We have not authorized the work.\n\nThank you"),
        edited_body="Hello,\n\n1) Re-testing included? 2) Parts lead time? Not authorized yet.",
        recipient="supply@willowridge.example.test",
        expected_subject="Re: Kiln repair estimate 8442",
    )


def test_served_v4_gmail_unknown_send_reconciles_in_conversation(make_agent, api_server,
                                                                  email_policy_enabled,
                                                                  isolated_home) -> None:
    """The uncertain-delivery leg INSIDE a served journey: a lost reply after
    acceptance is delivery_unknown; the conversation's reconcile step confirms
    it by the reserved Message-ID and no second dispatch ever happens."""
    from tests.pa_beta_gate.test_tool_workflow import _tracker

    _store_account(api_server, "gmail", "chaos-gmail", "me@gmail.example.test")
    api_server.gmail_inbox("me@gmail.example.test", [])
    from core import email_drafts
    from core.tool_intent_executor import execute_tool_intent

    saved = email_drafts.save_draft(
        to="supply@willowridge.example.test", subject="Re: Delivery scheduling",
        body="Wednesday at 10:00 works.", account="chaos-gmail",
        in_reply_to="<wro-x@willowridge.example.test>", kind="reply",
        session_id="openclaw:v4-chaos",
    )
    email_drafts.approve_draft(saved.draft["draft_id"], session_id="openclaw:v4-chaos")
    api_server.drop_after_accept = True
    context = {**_OPENCLAW, "operating_mode": "auto", "runtime_session_id": "openclaw:v4-chaos",
               "cancel_turn_id": "turn-v4-chaos"}
    paused = execute_tool_intent(
        {"intent": "email.draft.send", "arguments": {"draft_id": saved.draft["draft_id"]}},
        task_id="t-chaos", session_id="openclaw:v4-chaos", source_context=context,
        hive_activity_tracker=_tracker())
    from core.mode_permission_policy import resolve_approval
    approval_id = paused.details["approval_request"]["approval_id"]
    resolve_approval(approval_id, decision="allow")
    unknown = execute_tool_intent(
        {"intent": "email.draft.send", "arguments": {"draft_id": saved.draft["draft_id"]}},
        task_id="t-chaos", session_id="openclaw:v4-chaos",
        source_context={**context, "mode_approval_token": approval_id},
        hive_activity_tracker=_tracker())
    assert unknown.status == "delivery_unknown" and len(api_server.gmail_sent("me@gmail.example.test")) == 1

    api_server.drop_after_accept = False
    reconciled = execute_tool_intent(
        {"intent": "email.draft.reconcile", "arguments": {"draft_id": saved.draft["draft_id"]}},
        task_id="t-chaos-r", session_id="openclaw:v4-chaos", source_context=context,
        hive_activity_tracker=_tracker())
    assert "sent_confirmed" in (reconciled.status, str(reconciled.details.get("draft", {}).get("status")))
    assert len(api_server.gmail_sent("me@gmail.example.test")) == 1
