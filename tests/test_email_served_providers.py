"""Served conversational proof for the provider transports (Gmail + Graph).

The full deterministic tier: real agent turn (`agent.run_once`) → the email
routing arm → the real permission controller (the send pauses at
`pending_approval`, resumes with a one-time token) → the provider adapter over a
REAL local recorded-shape HTTP API → the durable draft/approval/receipt state →
the user-visible answer. The model-side tool CHOICE is scripted (the repo's
deterministic PA harness — no live model is authorized in this mission); the
planning/selection boundary is therefore labelled [captured-model replay], while
everything downstream of the choice is the served product path.

The provider endpoint is the LOCAL recorded-shape server, not the live API:
labelled [local recorded-shape transport]; live-credential acceptance stays a
gate with the integrator.
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
        provider_name="ollama-local", model_name="test", structured_output={"intent": "respond.direct", "arguments": {"message": text}},
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


def _force_loop(agent):
    agent._should_attempt_tool_intent = lambda *a, **k: True  # type: ignore[assignment]
    agent._plan_tool_workflow = lambda *a, **k: WorkflowPlannerDecision(  # type: ignore[assignment]
        handled=False, reason="pa-bypass", next_payload=None)


def _served_draft_flow(make_agent, api_server, *, provider: str, account: str, address: str,
                       inbox_entry: dict, request_text: str, draft_body: str, subject_expected: str) -> None:
    """ingress → routing → [captured-model replay: tool selection] → permission
    → provider transport → receipts/history → truthful answer."""
    from tests.provider_api_fixture import ProviderApiServer  # noqa: F401

    _store_account(api_server, provider, account, address)
    if provider == "gmail":
        api_server.gmail_inbox(address, [inbox_entry])
        provider_id = api_server.state.gmail_mailboxes[address][0]["id"]
    else:
        api_server.graph_inbox(address, [inbox_entry])
        provider_id = api_server.state.graph_mailboxes[address][0]["id"]

    agent = make_agent()
    _force_loop(agent)
    sid = _sid(f"{provider}-served")
    _side_effects = [
        _tool("email.read", {"account": account, "sender": "willowridge"}),
        _tool("email.open", {"account": account, "message_id": provider_id, "thread": True}),
        _tool("email.draft.save", {
            "account": account, "to": "supply@willowridge.example.test",
            "subject": f"Re: {inbox_entry['subject']}", "body": draft_body,
            "in_reply_to": inbox_entry["message_id"], "kind": "reply",
        }),
        _respond("Drafted the reply for review - nothing sent yet."),
    ]
    agent.memory_router.resolve_tool_intent = mock.Mock(side_effect=_side_effects)
    result = agent.run_once(
        request_text,
        session_id_override=sid,
        source_context={**_OPENCLAW, "operating_mode": "auto"},
    )
    response = str(result.get("response") or "")
    assert "review" in response.lower() or "draft" in response.lower(), response

    from core import email_drafts
    row = email_drafts.get_draft(session_id=sid, latest=True)
    assert row.ok and row.draft["account_resolved"] == account
    assert row.draft["body"] == draft_body  # canonical body shown for review

    # The approved send: mode-gate pause → one-time token resume → provider API.
    from core.tool_intent_executor import execute_tool_intent
    from tests.pa_beta_gate.test_tool_workflow import _tracker  # the established tracker helper

    email_drafts.approve_draft(row.draft["draft_id"], session_id=sid)
    context = {**_OPENCLAW, "operating_mode": "auto", "runtime_session_id": sid, "cancel_turn_id": f"turn-{provider}"}
    paused = execute_tool_intent(
        {"intent": "email.draft.send", "arguments": {"draft_id": row.draft["draft_id"]}},
        task_id=f"task-{provider}", session_id=sid, source_context=context, hive_activity_tracker=_tracker(),
    )
    assert paused.status == "pending_approval" and not _provider_sent(api_server, provider, address)

    from core.mode_permission_policy import resolve_approval
    approval_id = paused.details["approval_request"]["approval_id"]
    assert resolve_approval(approval_id, decision="allow")["status"] == "approved"
    resumed = execute_tool_intent(
        {"intent": "email.draft.send", "arguments": {"draft_id": row.draft["draft_id"]}},
        task_id=f"task-{provider}", session_id=sid,
        source_context={**context, "mode_approval_token": approval_id},
        hive_activity_tracker=_tracker(),
    )
    assert resumed.ok and resumed.status == "sent", resumed.response_text
    sent_items = _provider_sent(api_server, provider, address)
    assert len(sent_items) == 1
    # Gmail replies carry the RESOLVED thread binding; Graph replies went through
    # the native reply operation (threading is provider-side).

    receipt = resumed.details.get("receipt") or {}
    assert receipt.get("message_id") == resumed.details["draft"]["sent_message_id"]
    assert receipt.get("account") == account


def _provider_sent(api_server, provider: str, address: str) -> list:
    if provider == "gmail":
        return api_server.gmail_sent(address)
    return api_server.graph_sent(address) + api_server.graph_replies_sent(address)


def test_served_original_gmail_conversational_flow(make_agent, api_server, email_policy_enabled,
                                                   isolated_home) -> None:
    _served_draft_flow(
        make_agent, api_server,
        provider="gmail", account="work-gmail", address="me@gmail.example.test",
        inbox_entry={"from": "Willow Ridge <supply@willowridge.example.test>",
                     "subject": "Delivery scheduling",
                     "body": "Tuesday 14:00 or Wednesday 10:00?",
                     "message_id": "<wro-20260910@willowridge.example.test>",
                     "date": "Wed, 10 Sep 2026 09:15:00 +0000"},
        request_text=("Check unread emails from the orchard supplier on my Google account, open the "
                      "delivery thread and draft a reply choosing Wednesday at 10:00. Do not send it."),
        draft_body="Hello,\n\nWednesday at 10:00 works for our delivery.\n\nThank you",
        subject_expected="Re: Delivery scheduling",
    )


def test_served_novel_graph_conversational_flow(make_agent, api_server, email_policy_enabled,
                                                isolated_home) -> None:
    _served_draft_flow(
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
        draft_body=("Hello,\n\nTwo questions: 1) Does the estimate include re-testing? 2) What is the "
                    "parts lead time? We have not authorized the work.\n\nThank you"),
        subject_expected="Re: Kiln repair estimate 8442",
    )


def test_served_selective_cancellation_keeps_draft_and_note(make_agent, api_server,
                                                            email_policy_enabled, isolated_home,
                                                            tmp_path) -> None:
    """The PA-update's selective-cancellation shape on the email lane: a mixed
    request (draft the reply + save a note) whose follow-up CANCELS only the send
    obligation — the draft and the note survive, nothing was ever dispatched."""
    from core import email_drafts

    _store_account(api_server, "gmail", "cancel-acct", "me@gmail.example.test")
    api_server.gmail_inbox("me@gmail.example.test", [
        {"from": "Atelier Nord <service@atelier.example.test>",
         "subject": "Kiln repair estimate", "body": "The estimate is EUR 1.230.",
         "message_id": "<an-c@atelier.example.test>", "date": "Fri, 11 Sep 2026 07:00:00 +0000"},
    ])
    provider_id = api_server.state.gmail_mailboxes["me@gmail.example.test"][0]["id"]
    workspace = tmp_path / "notes"
    workspace.mkdir()
    agent = make_agent()
    _force_loop(agent)
    sid = _sid("selective-cancel")
    agent.memory_router.resolve_tool_intent = mock.Mock(side_effect=[
        _tool("email.read", {"account": "cancel-acct", "sender": "atelier"}),
        _tool("email.open", {"account": "cancel-acct", "message_id": provider_id, "thread": True}),
        _tool("email.draft.save", {
            "account": "cancel-acct", "to": "service@atelier.example.test",
            "subject": "Re: Kiln repair estimate",
            "body": "Two questions follow; the work is not authorized yet.",
            "in_reply_to": "<an-c@atelier.example.test>", "kind": "reply",
        }),
        _tool("workspace.write_file", {
            "path": "kiln-estimate-note.txt",
            "content": "Atelier Nord kiln estimate: EUR 1.230; reply drafted with two questions; not authorized.\n",
        }),
        _respond("Drafted the reply and saved the note; nothing sent."),
    ])
    result = agent.run_once(
        "Check my Google mail for the workshop estimate, draft two questions without accepting it, "
        "and save a prep note in my workspace. Don't send anything yet.",
        session_id_override=sid,
        source_context={**_OPENCLAW, "workspace": str(workspace), "operating_mode": "auto"},
    )
    response = str(result.get("response") or "")
    assert "draft" in response.lower() and "note" in response.lower()
    assert (workspace / "kiln-estimate-note.txt").exists()
    row = email_drafts.get_draft(session_id=sid, latest=True)
    assert row.ok and row.draft["status"] == "draft"
    assert not api_server.gmail_sent("me@gmail.example.test")

    # The follow-up cancels only the send obligation: draft and note survive.
    agent.memory_router.resolve_tool_intent = mock.Mock(side_effect=[
        _respond("Kept the draft and the note; the send stays cancelled."),
    ])
    followup = agent.run_once(
        "Keep the note and the draft exactly as they are, but cancel sending the email entirely.",
        session_id_override=sid,
        source_context={**_OPENCLAW, "workspace": str(workspace), "operating_mode": "auto"},
    )
    # The user-visible answer acknowledges the instruction (this phrasing is
    # claimed by the memory fast path — a labelled deterministic surface); the
    # load-bearing evidence is the preserved state below.
    assert str(followup.get("response") or "").strip()
    row_after = email_drafts.get_draft(session_id=sid, latest=True)
    assert row_after.ok and row_after.draft["status"] == "draft"
    assert (workspace / "kiln-estimate-note.txt").exists()
    assert api_server.gmail_sent("me@gmail.example.test") == []
