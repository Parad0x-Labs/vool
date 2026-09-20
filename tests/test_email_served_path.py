"""The SERVED email path: real agent loop -> permission -> executor -> transport -> history.

Modelled on tests/pa_beta_gate/test_tool_workflow.py: the model-side tool CHOICE is scripted
(the deterministic PA tier — no live model), but everything downstream of the choice is the
real product path: the agent turn, the mode-permission controller, the runtime executor, the
credential store, the durable draft store, and the REAL local mail service on the mission's
test ports. The capture service is a real socket peer, not a double.

Proves for the email P0 workflow:
  * the user-visible answer after "draft a reply, do not send" shows the reviewable draft
    and nothing reached the wire;
  * the approved send PAUSES at the permission controller (pending_approval, durable
    request), and resumes only with a resolved one-time token — then exactly one message
    exists at the capture service, with the receipt in history;
  * a MIXED request (summarize unread mail + save a note in an authorized workspace
    folder) drops neither obligation;
  * email disabled by policy is an honest "disabled", never a fake success.
"""
from __future__ import annotations

import json
import uuid
from email.message import EmailMessage
from unittest import mock

import pytest

from core.execution.models import WorkflowPlannerDecision
from core.memory_first_router import ModelExecutionDecision

pytestmark = [pytest.mark.pa_beta]

_OPENCLAW = {"surface": "openclaw", "platform": "openclaw"}
SMTP_PORT = 12461
IMAP_PORT = 0  # ephemeral: 12462-12464 belong to other missions
_ACTIVE: dict[str, int] = {}


def _sid(label: str) -> str:
    return f"openclaw:{label}:{uuid.uuid4().hex}"


def _tool(intent: str, arguments: dict):
    return ModelExecutionDecision(
        source="provider_execution", task_hash=f"t-{intent}", provider_id="ollama-local:test",
        provider_name="ollama-local", model_name="test",
        structured_output={"intent": intent, "arguments": arguments},
        confidence=0.8, trust_score=0.84, used_model=True, validation_state="valid",
    )


def _final(text: str):
    return ModelExecutionDecision(
        source="provider_execution", task_hash="final", provider_id="ollama-local:test",
        provider_name="ollama-local", model_name="test", output_text=text,
        confidence=0.82, trust_score=0.84, used_model=True, validation_state="valid",
    )


def _tracker():
    from core.hive_activity_tracker import HiveActivityTracker, HiveActivityTrackerConfig

    return HiveActivityTracker(config=HiveActivityTrackerConfig(enabled=False, watcher_api_url=None))


def _force_loop(agent):
    agent._should_attempt_tool_intent = lambda *a, **k: True  # type: ignore[assignment]
    agent._plan_tool_workflow = lambda *a, **k: WorkflowPlannerDecision(  # type: ignore[assignment]
        handled=False, reason="pa-bypass", next_payload=None)


@pytest.fixture
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


@pytest.fixture(scope="module")
def _shared_service():
    import socket as _socket

    from tests.local_mail_service import LocalMailService
    with _socket.socket() as probe:
        probe.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", SMTP_PORT))
            smtp = SMTP_PORT
        except OSError:
            smtp = 0  # allocated port busy: ephemeral instead of borrowing another
    service = LocalMailService(smtp_port=smtp, imap_port=IMAP_PORT)
    service.start()
    _ACTIVE["smtp"] = service.smtp_port
    _ACTIVE["imap"] = service.imap_port
    yield service
    service.stop()
    _ACTIVE.clear()


@pytest.fixture()
def mail_service(_shared_service):
    # One real service for the module; each test re-seeds its own user and resets
    # the capture log so assertions stay per-test deterministic.
    _shared_service.store.captured.clear()
    inbox = [
        _mail("Willow Ridge Supply <supply@willowridge.example.test>",
              "Delivery scheduling for this week's fruit order",
              "Could you take delivery Tuesday at 14:00 or Wednesday at 10:00? Please confirm.",
              "<wro-20260910@willowridge.example.test>", "Wed, 10 Sep 2026 09:15:00 +0000"),
        _mail("Willow Ridge Supply <supply@willowridge.example.test>",
              "Re: Delivery scheduling for this week's fruit order",
              "Following up - we still need your slot choice for the fruit delivery.",
              "<wro-20260911@willowridge.example.test>", "Thu, 11 Sep 2026 08:00:00 +0000",
              in_reply_to="<wro-20260910@willowridge.example.test>",
              refs="<wro-20260910@willowridge.example.test>"),
    ]
    _shared_service.store.add_user("orchard@example.test", "pw-orchard", {"INBOX": inbox})
    return _shared_service


def _mail(frm: str, subj: str, body: str, mid: str, date: str,
          in_reply_to: str | None = None, refs: str | None = None) -> bytes:
    m = EmailMessage()
    m["From"] = frm
    m["To"] = "orchard@example.test"
    m["Subject"] = subj
    m["Message-ID"] = mid
    m["Date"] = date
    if in_reply_to:
        m["In-Reply-To"] = in_reply_to
    if refs:
        m["References"] = refs
    m.set_content(body)
    return m.as_bytes()


@pytest.fixture(autouse=True)
def _accounts(isolated_home, _shared_service):
    # Depends on _shared_service explicitly: this fixture reads the ports the service
    # publishes in _ACTIVE, and without the dependency a test that requests no other
    # fixture (e.g. the pure classification test) ran _accounts before any service
    # existed and died on KeyError: 'imap'.
    from core import credential_store
    credential_store.store_credential(
        "email.imap.default",
        json.dumps({"host": "127.0.0.1", "port": _ACTIVE["imap"], "username": "orchard@example.test",
                    "password": "pw-orchard", "security": "plain"}),
        label="served imap",
    )
    credential_store.store_credential(
        "email.smtp.default",
        json.dumps({"host": "127.0.0.1", "port": _ACTIVE["smtp"], "username": "orchard@example.test",
                    "password": "pw-orchard", "from_addr": "orchard@example.test", "security": "plain"}),
        label="served smtp",
    )


def test_served_draft_flow_shows_review_and_sends_nothing(make_agent, mail_service,
                                                          email_policy_enabled, isolated_home) -> None:
    agent = make_agent()
    _force_loop(agent)
    sid = _sid("email-draft")
    agent.memory_router.resolve_tool_intent = mock.Mock(side_effect=[
        _tool("email.read", {"sender": "willowridge", "unseen_only": True, "limit": 10}),
        _tool("email.open", {"message_id": "<wro-20260911@willowridge.example.test>", "thread": True}),
        _tool("email.draft.save", {
            "to": "Willow Ridge Supply <supply@willowridge.example.test>",
            "subject": "Re: Delivery scheduling for this week's fruit order",
            "body": "Hello,\n\nWednesday at 10:00 works for our delivery.\n\nThank you",
            "in_reply_to": "<wro-20260911@willowridge.example.test>",
            "references": ["<wro-20260910@willowridge.example.test>"],
            "kind": "reply",
        }),
        _tool("respond.direct", {"message": "Drafted a reply choosing Wednesday at 10:00 - "
                                            "review before I send anything."}),
    ])
    agent.memory_router.resolve = mock.Mock(return_value=_final(
        "Drafted a reply choosing Wednesday at 10:00 - review before I send anything."))
    result = agent.run_once(
        "Check unread emails from the orchard supplier, open the delivery thread and draft a "
        "reply choosing Wednesday at 10:00. Do not send it.",
        session_id_override=sid,
        source_context={**_OPENCLAW, "operating_mode": "auto"},
    )
    assert result["mode"] in {"tool_executed", "model"}, result
    response = str(result.get("response") or "")
    # The USER-VISIBLE answer carries the reviewable draft state.
    assert "Wednesday at 10:00" in response
    # "Do not send it": nothing reached the wire, and read-only checks kept flags intact.
    assert not mail_service.captured if hasattr(mail_service, "captured") else not mail_service.store.captured
    assert mail_service.store.seen_flags("orchard@example.test") == [False, False]


def test_served_approved_send_pauses_at_permission_and_resumes(mail_service, email_policy_enabled,
                                                                isolated_home) -> None:
    from core import email_drafts
    from core.tool_intent_executor import execute_tool_intent

    sid = _sid("email-send")
    draft = email_drafts.save_draft(
        to="supply@willowridge.example.test", subject="Re: Delivery scheduling for this week's fruit order",
        body="Hi,\n\nWednesday at 10:00 works for our delivery.\n\nThank you",
        in_reply_to="<wro-20260911@willowridge.example.test>",
        references=["<wro-20260910@willowridge.example.test>"], kind="reply", session_id=sid,
    )
    assert draft.ok
    email_drafts.approve_draft(draft.draft["draft_id"], session_id=sid)

    context = {**_OPENCLAW, "operating_mode": "auto", "runtime_session_id": sid,
               "cancel_turn_id": "turn-send"}
    # 1. The send PAUSES at the permission controller even though the draft is approved:
    #    two independent authorities must both say yes.
    paused = execute_tool_intent({"intent": "email.draft.send", "arguments": {"draft_id": draft.draft["draft_id"]}},
                                 task_id="task-send", session_id=sid, source_context=context, hive_activity_tracker=_tracker())
    assert paused.status == "pending_approval" and paused.mode == "tool_preview"
    assert paused.details.get("executed") is False
    assert not mail_service.store.captured
    # The request is durable: it lives in the pending-approvals mirror.
    from core import runtime_paths
    mirror = json.loads((runtime_paths.active_data_dir() / "pending_approvals.json").read_text())
    approval_id = paused.details["approval_request"]["approval_id"]
    assert approval_id in mirror

    # 2. The user allows it; the one-time token resumes the SAME call and it executes.
    from core.mode_permission_policy import resolve_approval
    resolved = resolve_approval(approval_id, decision="allow")
    assert resolved and resolved["status"] == "approved"
    resumed = execute_tool_intent(
        {"intent": "email.draft.send", "arguments": {"draft_id": draft.draft["draft_id"]}},
        task_id="task-send", session_id=sid, hive_activity_tracker=_tracker(),
        source_context={**context, "mode_approval_token": approval_id},
    )
    assert resumed.ok and resumed.status == "sent", resumed.response_text
    assert len(mail_service.store.captured) == 1
    captured = mail_service.store.captured[0]
    assert captured["recipients"] == ["supply@willowridge.example.test"]
    receipt = resumed.details.get("receipt") or {}
    assert receipt.get("message_id") == captured["message_id"]  # the receipt names what went out

    # 3. The token is one-time: a fresh send attempt pauses again — and the DRAFT-level
    #    idempotency means even a re-approved replay cannot produce a second message.
    again = execute_tool_intent({"intent": "email.draft.send", "arguments": {"draft_id": draft.draft["draft_id"]}},
                                task_id="task-send2", session_id=sid, source_context=context, hive_activity_tracker=_tracker())
    assert again.status in {"pending_approval", "sent"}  # paused at mode gate, or replayed receipt
    assert len(mail_service.store.captured) == 1


def test_served_mixed_request_keeps_both_obligations(make_agent, mail_service, email_policy_enabled,
                                                     isolated_home, tmp_path) -> None:
    agent = make_agent()
    _force_loop(agent)
    sid = _sid("email-mixed")
    workspace = tmp_path / "authorized-notes"
    workspace.mkdir()
    agent.memory_router.resolve_tool_intent = mock.Mock(side_effect=[
        _tool("email.read", {"sender": "willowridge", "unseen_only": True, "limit": 10}),
        _tool("workspace.write_file", {
            "path": "orchard-delivery-note.txt",
            "content": "Unread from Willow Ridge Supply (2): delivery slot choice pending - "
                       "they offered Tuesday 14:00 or Wednesday 10:00.\n",
        }),
        _tool("respond.direct", {"message": "Two unread messages from Willow Ridge Supply about "
                                            "the delivery slot (Tuesday 14:00 or Wednesday 10:00); "
                                            "I saved a note in your workspace."}),
    ])
    agent.memory_router.resolve = mock.Mock(return_value=_final(
        "Two unread messages from Willow Ridge Supply about the delivery slot "
        "(Tuesday 14:00 or Wednesday 10:00); I saved a note in your workspace."))
    result = agent.run_once(
        "Check my unread orchard supplier mail, summarize it, and save the summary to orchard-delivery-note.txt in the workspace.",
        session_id_override=sid,
        source_context={**_OPENCLAW, "workspace": str(workspace), "operating_mode": "auto"},
    )
    response = str(result.get("response") or "")
    # Obligation 1: the mail summary is in the user-visible answer.
    assert "Willow Ridge" in response and ("Tuesday" in response or "Wednesday" in response)
    # Obligation 2: the note is a real file with the summarized facts.
    note = workspace / "orchard-delivery-note.txt"
    assert note.exists()
    assert "Tuesday 14:00" in note.read_text(encoding="utf-8")
    # The mixed request still never sent anything.
    assert not mail_service.store.captured


def test_served_email_disabled_by_policy_is_honest(isolated_home, mail_service) -> None:
    from core.tool_intent_executor import execute_tool_intent

    context = {**_OPENCLAW, "operating_mode": "auto", "runtime_session_id": _sid("disabled")}
    result = execute_tool_intent({"intent": "email.read", "arguments": {"sender": "willowridge"}},
                                 task_id="t", session_id="s", source_context=context, hive_activity_tracker=_tracker())
    assert result.handled and not result.ok and result.status == "disabled"
    assert "disabled" in (result.response_text or "").lower()


def test_permission_classification_of_new_email_intents() -> None:
    from core.mode_permission_policy import PermissionAction, actions_for_tool

    assert set(actions_for_tool("email.open")) == {
        PermissionAction.USE_NETWORK, PermissionAction.ACCESS_EXTERNAL_PROVIDERS,
    }
    assert set(actions_for_tool("email.draft.reconcile")) == {
        PermissionAction.USE_NETWORK, PermissionAction.ACCESS_EXTERNAL_PROVIDERS,
    }
    assert set(actions_for_tool("email.draft.send")) == {
        PermissionAction.USE_NETWORK, PermissionAction.SEND_EXTERNAL_MESSAGES,
    }
    # History: draft state records used to resolve to NO permission action. A read_only
    # contract that resolves to nothing is unauditable — the mode matrix sees no action to
    # reason about — so their contracts now declare `read_files` (local reviewable state;
    # the send, the only effect, is separately classified above). Pinned together with
    # tests/test_read_only_tools_are_not_denied_by_mode.py.
    assert set(actions_for_tool("email.draft.save")) == {PermissionAction.READ_FILES}
    assert set(actions_for_tool("email.draft.approve")) == {PermissionAction.READ_FILES}

