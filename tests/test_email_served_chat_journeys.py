"""Served email conversations: the real daemon, POST /api/chat and the /api/mode approval door.

Two same-session conversations drive the whole email workflow through production routing, each in
its own daemon home: check -> open thread -> draft -> edit -> review -> send request -> pause ->
operator decision through /api/mode -> resume -> receipt -> reconciliation -> replay. Controls: no send
(hold), no tools, a refusal (the operator denies one send request, and the denied token replayed at
/api/chat authorises nothing; a later request is allowed), and two replays -- the completed approved
resume sent again unchanged (a client retry) and the user asking for the sent reply again, which the
scripted model pursues through the email family. Nothing new may leave on either replay. In the
conversational replay the expansion seats the send in the next round of the same turn, and the draft store
answers with the send it already recorded (revision 6, Gate B).

WHAT EXECUTES -- production code from this checkout: apps.vool_api_server with its /api/chat and
/api/mode doors; shipped routing (VOOL_ALWAYS_ON_CATALOG is removed from the daemon's environment);
the chat-lane policy, planner gate, tool offer and tool loop; the production model certification
door; the mode permission controller and its pending approvals; the draft store; OAuth refresh and
principal binding; the Gmail and Microsoft Graph adapters over HTTP; the execution ledger.

WHAT IS SIMULATED, and labelled wherever it appears: the MODEL -- ScriptedEmailModel in
tests/_email_served_conversation.py, certified first, whose SCRIPTED choices use only what each
request carried -- and the PROVIDERS -- tests/provider_api_fixture.py on loopback. No live mailbox,
credential, model or paid inference is involved.

Each conversation writes its full transcript (every model request the daemon made and what it
carried, approvals, provider requests, ledger facts, process cleanup) before any assertion runs:
into $VOOL_EMAIL_JOURNEY_EVIDENCE_DIR when set, else the test's tmp directory.
"""
from __future__ import annotations

import base64
import json
import os
import re
import time
from email import policy as mail_policy
from email.parser import BytesParser
from email.utils import getaddresses
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import pytest

from tests._email_served_conversation import (
    MESSAGE_ID_IN_REPLY,
    NOVEL_GRAPH,
    ORIGINAL_GMAIL,
    ConversationSpec,
    EmailConversation,
    TurnRecord,
)

pytestmark = [pytest.mark.pa_beta]

ORCHARD_PARENT = "<wro-20260910@willowridge-orchard.example.test>"
ORCHARD_SUPPLIER = "supply@willowridge-orchard.example.test"
KILN_PARENT = "<est-8442@northfield-kiln.example.test>"
KILN_SHOP = "estimates@northfield-kiln.example.test"


# --------------------------------------------------------------------------- running a conversation


def _evidence_path(tmp_path: Path, name: str) -> Path:
    configured = os.environ.get("VOOL_EMAIL_JOURNEY_EVIDENCE_DIR", "").strip()
    folder = Path(configured) if configured else tmp_path
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"served-journey-{name}-{time.strftime('%Y%m%d-%H%M%S')}.json"


def _converse(tmp_path: Path, spec: ConversationSpec, name: str) -> tuple[EmailConversation, dict[str, TurnRecord]]:
    """Run every step of one conversation, writing the transcript after each turn and at the end."""
    conversation = EmailConversation(tmp_path / name, spec, label=name)
    turns: dict[str, TurnRecord] = {}
    evidence = _evidence_path(tmp_path, name)
    try:
        conversation.open()
        for step in spec.steps:
            turns[step.name] = conversation.turn(step)
            evidence.write_text(json.dumps(conversation.to_json(), indent=2, default=str))
    except Exception as exc:
        conversation.error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        conversation.close()
        evidence.write_text(json.dumps(conversation.to_json(), indent=2, default=str))
    return conversation, turns


# --------------------------------------------------------------------------- reading what happened


def _only(items: list) -> Any:
    assert len(items) == 1, items
    return items[0]


def _tool_facts(turn: TurnRecord, name: str) -> list[dict[str, Any]]:
    return [fact for fact in turn.facts if fact["kind"] == "tool" and fact["name"] == name]


def _executed(turn: TurnRecord, name: str) -> list[dict[str, Any]]:
    return [fact for fact in _tool_facts(turn, name) if fact["ok"]]


def _choices(turn: TurnRecord, tool: str) -> list[dict[str, Any]]:
    """The arguments of every call the model made to `tool` during the turn, resume included."""
    return [request["choice"][1] for request in turn.requests + turn.resumed_requests
            if request["kind"] == "tool_round" and request["choice"] and request["choice"][0] == tool]


def _draft_after(turn: TurnRecord) -> dict[str, Any]:
    return _only(list(turn.drafts_after.values()))


def _gmail_wire(conversation: EmailConversation) -> list[tuple[Any, dict[str, Any]]]:
    sent = []
    for payload in conversation.api.gmail_sent(conversation.spec.address):
        raw = str(payload.get("raw") or "")
        mime = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
        sent.append((BytesParser(policy=mail_policy.default).parsebytes(mime), payload))
    return sent


def _graph_wire(conversation: EmailConversation) -> list[Any]:
    return [BytesParser(policy=mail_policy.default).parsebytes(record["raw_mime"])
            for record in conversation.api.graph_sent(conversation.spec.address)
            if record.get("representation") == "mime"]


def _assert_wire_is_the_reviewed_reply(wire: Any, *, sender: str, recipient: str, subject: str,
                                       parent: str, body: str, message_id: str) -> None:
    assert [mailbox.addr_spec for mailbox in wire["From"].addresses] == [sender]
    assert [address for _name, address in getaddresses([str(wire["To"])])] == [recipient]
    assert str(wire["Subject"]) == subject
    assert str(wire["In-Reply-To"]) == parent and parent in str(wire["References"]).split()
    assert wire.get_body(preferencelist=("plain",)).get_content().rstrip("\n") == body
    assert str(wire["Message-ID"]) == message_id


def _assert_served_setup(conversation: EmailConversation) -> None:
    assert conversation.error == "", conversation.error
    assert conversation.certification.get("state") == "verified", conversation.certification
    assert conversation.certification.get("successful") is True, conversation.certification
    assert conversation.daemon_catalog_flag is None  # shipped routing: the always-on catalog is off


def _assert_cleanup(conversation: EmailConversation) -> None:
    assert conversation.closed.get("daemon_exit_code") is not None, conversation.closed
    assert conversation.closed.get("leftover_processes") == [], conversation.closed


def _graph_searches(conversation: EmailConversation) -> list[str]:
    """The $search value of every Graph message listing the provider received."""
    found: list[str] = []
    for entry in conversation.api.state.request_log:
        if entry["method"] == "GET" and entry["path"] == "/v1.0/me/messages":
            found += parse_qs(entry.get("query") or "").get("$search", [])
    return found


def _denied_everything(control: dict[str, Any]) -> bool:
    return all((resolution.get("approval") or {}).get("status") == "denied" for resolution in control["resolutions"])


def _assert_retry_authorised_nothing(turn: TurnRecord, sent: dict[str, Any]) -> None:
    """REPLAY AT THE DOOR: the completed approved resume sent again unchanged leaves nothing new."""
    (retry,) = turn.controls
    assert retry["kind"] == "completed_resume_replayed", retry["kind"]
    assert retry["dispatches_before"] == retry["dispatches_after_request"] == 1, retry
    assert all(approval["intent"] == "email.draft.send" for approval in retry["raised_approvals"]), retry
    assert _denied_everything(retry), retry["resolutions"]
    for mentioned in re.findall(r"Message-ID (<[^>]+>)", retry["reply"]):
        assert mentioned == sent["sent_message_id"], retry["reply"]


def _assert_resend_left_nothing_new(turn: TurnRecord, sent: dict[str, Any], wire_count: int) -> None:
    """REPLAY IN CONVERSATION: asked to send the sent reply again. The dispatched stage seats no send, so
    the scripted model asks for the email family, and the NEXT round of the same turn offers the send: the
    expansion is turn navigation that every later round's offer is built from (core.tool_offer_state,
    revision 6 Gate B). The controller still asks, the operator allows, and the draft store returns the
    send it already recorded -- the first receipt -- without dispatching again. The resumed request opens
    its own navigation scope, so the paused request's expansion does not carry into it.

    Migrated in revision 6. This used to accept a second outcome too: the served loop never reseated the
    expansion, no send was attempted, and the model said it could not send. That outcome now fails. The
    expansion round is found rather than assumed to be the first: a scripted model whose view carries no
    draft id looks the draft up first (plan_resend), and no round before the expansion may offer the send.
    """
    rounds = [request for request in turn.requests if request["kind"] == "tool_round"]
    expansion = next((index for index, request in enumerate(rounds)
                      if request["choice"] and request["choice"][0] == "capability__expand_family"), None)
    assert expansion is not None, [request["choice"] for request in rounds]
    assert all("email__draft__send" not in request["offered_email"] for request in rounds[:expansion + 1]), \
        rounds[:expansion + 1]
    assert _executed(turn, "capability.expand_family"), turn.facts
    following = rounds[expansion + 1:]
    assert following and "email__draft__send" in following[0]["offered_email"], following[:1]
    assert following[0]["choice"][0] == "email__draft__send", following[0]["choice"]
    assert [approval["intent"] for approval in turn.raised_approvals] == ["email.draft.send"], turn.raised_approvals
    assert turn.resolutions and all(resolution.get("ok") for resolution in turn.resolutions), turn.resolutions
    assert _tool_facts(turn, "email.draft.send"), turn.facts
    assert turn.dispatches == 1 and wire_count == 1
    after = _draft_after(turn)
    assert after["status"] == "sent" and after["sent_message_id"] == sent["sent_message_id"]
    mentioned = re.findall(r"Message-ID (<[^>]+>)", turn.reply + turn.resumed_reply)
    assert mentioned and set(mentioned) == {sent["sent_message_id"]}, (turn.reply, turn.resumed_reply)
    resumed_rounds = [request for request in turn.resumed_requests if request["kind"] == "tool_round"]
    assert all("email__draft__send" not in request["offered_email"] for request in resumed_rounds), resumed_rounds


# --------------------------------------------------------------------------- the conversations


def test_served_original_gmail_conversation_runs_the_whole_email_workflow(tmp_path) -> None:
    """ORIGINAL (Gmail): the mailbox check through reconciliation and replay in ONE served session,
    with the no-send (hold) and no-tools controls."""
    conversation, turns = _converse(tmp_path, ORIGINAL_GMAIL, "original-gmail")
    mailbox = ORIGINAL_GMAIL.address
    _assert_served_setup(conversation)

    check = turns["check"]
    assert _executed(check, "email.read"), check.facts
    assert "Willow Ridge Orchard Supply" in check.reply and "Delivery scheduling" in check.reply
    assert "Harbor Paper" not in check.reply  # the matched message, not the whole inbox
    message_id = re.search(MESSAGE_ID_IN_REPLY, check.reply).group(1)

    opened = turns["open"]  # a follow-up naming no mailbox: email tools offered, id taken from history
    assert _executed(opened, "email.open"), opened.facts
    assert _choices(opened, "email__open")[0]["message_id"] == message_id
    assert "Tuesday at 14:00 or Wednesday at 10:00" in opened.reply

    drafted = turns["draft"]
    assert _executed(drafted, "email.draft.save"), drafted.facts
    first = _draft_after(drafted)
    assert first["status"] == "draft" and first["version"] == 1 and first["account_resolved"] == "default"
    assert first["to"] == [ORCHARD_SUPPLIER] and first["subject"] == "Re: Delivery scheduling"
    assert first["in_reply_to"] == ORCHARD_PARENT
    assert first["body"].rstrip("\n") == "Wednesday at 10:00 works for our delivery."
    assert drafted.dispatches == 0

    edited = turns["edit"]  # the model re-saves the recipient list it was shown
    assert _executed(edited, "email.draft.save"), edited.facts
    assert not [fact for fact in edited.facts if fact["status"] == "provider_did_not_answer"], edited.facts
    second = _draft_after(edited)
    assert second["version"] == 2 and second["status"] == "draft"
    assert second["body"].rstrip("\n") == "Wednesday 10:00 is confirmed. Please use the north gate."
    assert edited.dispatches == 0

    review = turns["review"]
    assert _executed(review, "email.draft.get"), review.facts
    for line in (f"To: {ORCHARD_SUPPLIER}", "Subject: Re: Delivery scheduling", f"In-Reply-To: {ORCHARD_PARENT}",
                 "Wednesday 10:00 is confirmed. Please use the north gate.", "(version 2, not approved)"):
        assert line in review.reply, (line, review.reply)

    hold = turns["hold"]  # NO-SEND CONTROL
    assert hold.raised_approvals == [] and hold.dispatches == 0
    assert not [fact for fact in hold.facts if fact["kind"] == "tool"], hold.facts
    assert _draft_after(hold)["status"] == "draft"

    send = turns["send"]
    assert [approval["intent"] for approval in send.raised_approvals] == ["email.draft.send"]
    assert send.dispatches_before_decision == 0  # paused before anything left
    assert send.resolutions and all(resolution.get("ok") for resolution in send.resolutions), send.resolutions
    assert _executed(send, "email.draft.approve") and _executed(send, "email.draft.send"), send.facts
    assert send.dispatches == 1
    wire, payload = _only(_gmail_wire(conversation))
    sent = _draft_after(send)
    assert sent["status"] == "sent" and sent["sent_message_id"]
    _assert_wire_is_the_reviewed_reply(wire, sender=mailbox, recipient=ORCHARD_SUPPLIER,
                                       subject="Re: Delivery scheduling", parent=ORCHARD_PARENT,
                                       body="Wednesday 10:00 is confirmed. Please use the north gate.",
                                       message_id=sent["sent_message_id"])
    assert payload.get("threadId") == "th-orchard"  # threaded into the parent's provider thread
    assert f"Message-ID {sent['sent_message_id']}" in send.resumed_reply
    assert f"verified mailbox {mailbox}" in send.resumed_reply
    _assert_retry_authorised_nothing(send, sent)

    reconcile = turns["reconcile"]  # "check the sent folder" is the mailbox, not a disk search
    assert _executed(reconcile, "email.draft.reconcile"), reconcile.facts
    assert not [fact for fact in reconcile.facts if fact["name"].startswith("machine.")], reconcile.facts
    assert reconcile.reply.startswith("Checked the Sent folder:"), reconcile.reply
    assert reconcile.dispatches == 1

    _assert_resend_left_nothing_new(turns["replay"], sent, len(_gmail_wire(conversation)))

    no_tools = turns["no_tools"]  # NO-TOOLS CONTROL
    assert not [fact for fact in no_tools.facts if fact["kind"] == "tool"], no_tools.facts
    assert ORCHARD_SUPPLIER in no_tools.reply, no_tools.reply
    assert no_tools.dispatches == 1

    assert not [entry for entry in conversation.approvals() if entry.get("status") == "pending"]
    _assert_cleanup(conversation)


def test_served_novel_graph_conversation_refuses_one_send_and_allows_the_next(tmp_path) -> None:
    """NOVEL (Microsoft Graph, different wording and mailbox): the operator denies the first send
    request -- nothing leaves -- and allows a later one, which leaves once as the reviewed reply."""
    conversation, turns = _converse(tmp_path, NOVEL_GRAPH, "novel-graph")
    mailbox = NOVEL_GRAPH.address
    _assert_served_setup(conversation)

    find = turns["find"]
    assert _executed(find, "email.read"), find.facts
    assert "Northfield Kiln Repair" in find.reply and "Estimate 8442" in find.reply
    assert "Clay Guild" not in find.reply
    assert _graph_searches(conversation) == ['"from:kiln"']  # the documented form, once
    message_id = re.search(MESSAGE_ID_IN_REPLY, find.reply).group(1)

    opened = turns["open"]
    assert _executed(opened, "email.open"), opened.facts
    assert _choices(opened, "email__open")[0]["message_id"] == message_id
    assert "Total: 1,240 EUR." in opened.reply
    assert "The thread has 1 message(s)" in opened.reply and "Clay Guild" not in opened.reply

    drafted = turns["draft"]
    first = _draft_after(drafted)
    assert first["to"] == [KILN_SHOP] and first["subject"] == "Re: Estimate 8442 for relining the studio kiln"
    assert first["in_reply_to"] == KILN_PARENT and first["version"] == 1
    assert first["body"].rstrip("\n") == "We accept the estimate, but we need the work done before Friday."

    edited = turns["edit"]
    second = _draft_after(edited)
    assert second["version"] == 2
    assert second["body"].rstrip("\n") == ("We accept the estimate, but we need the work done before Friday. "
                                           "The kiln is at the north studio.")

    review = turns["review"]
    for line in (f"To: {KILN_SHOP}", "Subject: Re: Estimate 8442 for relining the studio kiln",
                 f"In-Reply-To: {KILN_PARENT}", "The kiln is at the north studio.", "(version 2, not approved)"):
        assert line in review.reply, (line, review.reply)

    refused = turns["refuse"]  # REFUSAL CONTROL: the operator denies
    assert [approval["intent"] for approval in refused.raised_approvals] == ["email.draft.send"]
    assert [(resolution.get("approval") or {}).get("status") for resolution in refused.resolutions] == ["denied"]
    assert refused.resumed_reply == "" and refused.resumed_requests == []  # a denial is not resent
    (replayed,) = refused.controls  # the denied token presented at /api/chat anyway
    assert replayed["kind"] == "denied_token_replayed"
    assert replayed["dispatches_before"] == replayed["dispatches_after_request"] == 0, replayed
    assert [approval["intent"] for approval in replayed["raised_approvals"]] == ["email.draft.send"], replayed
    assert _denied_everything(replayed), replayed["resolutions"]
    assert refused.dispatches_before_decision == 0 and refused.dispatches == 0
    assert not _executed(refused, "email.draft.send"), refused.facts
    assert _draft_after(refused)["status"] != "sent"
    assert "Sent draft" not in refused.reply + replayed["reply"]

    send = turns["send"]
    assert [approval["intent"] for approval in send.raised_approvals] == ["email.draft.send"]
    assert send.dispatches_before_decision == 0 and send.dispatches == 1
    assert _executed(send, "email.draft.send"), send.facts
    sent = _draft_after(send)
    assert sent["status"] == "sent"
    _assert_wire_is_the_reviewed_reply(_only(_graph_wire(conversation)), sender=mailbox, recipient=KILN_SHOP,
                                       subject="Re: Estimate 8442 for relining the studio kiln", parent=KILN_PARENT,
                                       body=("We accept the estimate, but we need the work done before Friday. "
                                             "The kiln is at the north studio."),
                                       message_id=sent["sent_message_id"])
    assert f"verified mailbox {mailbox}" in send.resumed_reply and f"to {KILN_SHOP}" in send.resumed_reply
    _assert_retry_authorised_nothing(send, sent)

    reconcile = turns["reconcile"]  # "confirm it's in the Sent folder" is the mailbox, not a web search
    assert _executed(reconcile, "email.draft.reconcile"), reconcile.facts
    assert not [fact for fact in reconcile.facts if fact["name"].startswith(("machine.", "web.", "web_"))], \
        reconcile.facts
    assert reconcile.reply.startswith("Checked the Sent folder:"), reconcile.reply
    assert reconcile.dispatches == 1

    _assert_resend_left_nothing_new(turns["replay"], sent, len(_graph_wire(conversation)))

    assert not [entry for entry in conversation.approvals() if entry.get("status") == "pending"]
    _assert_cleanup(conversation)
