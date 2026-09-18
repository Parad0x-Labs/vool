"""Served natural first-turn mailbox requests, through the whole email workflow (revision 6, Gate A).

Two same-session conversations open with the two wordings the revision-5 final review reproduced as
unreachable -- the runtime offered them no email read tool -- and continue through open -> draft ->
review -> approval -> send -> receipt -> reconciliation -> repeated send. Each ends with a tool
prohibition in the wording revision 5 did not read as one ("Without using any tools, ..."), which must
run no tool at all.

* ORIGINAL (Microsoft Graph): "Pull up the most recent email from the kiln repair shop." -- the served
  stage-48 wording.
* NOVEL (Gmail, different mailbox data and wording): "Search my mail for the Harbor Paper invoice."

WHAT EXECUTES and WHAT IS SIMULATED are exactly as in tests/test_email_served_chat_journeys.py: production
code from the chat door to the provider HTTP adapters; a certified SCRIPTED model and loopback provider
fixtures, both labelled. The first step's scripted choice (email.read) is only possible when the runtime
offers email.read -- the scripted model cannot call a tool it was not offered, and says so instead.

Fixture note: the Gmail fixture enforces a quoted multi-word `from:` clause by its first word only
(tests/provider_api_fixture.py splits the query on whitespace); the adapter's documented query form is
asserted from the request the fixture received.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs

import pytest

from tests._email_served_conversation import (
    MESSAGE_ID_IN_REPLY,
    NOVEL_GRAPH,
    ORIGINAL_GMAIL,
    ConversationSpec,
    EmailConversation,
    Step,
    TurnRecord,
    _recap,
    plan_answer,
    plan_edit_draft,
    plan_find,
    plan_open_thread,
    plan_reconcile,
    plan_reply_draft,
    plan_resend,
    plan_send,
    plan_show_draft,
)
from tests.test_email_served_chat_journeys import (
    KILN_PARENT,
    KILN_SHOP,
    _assert_cleanup,
    _assert_resend_left_nothing_new,
    _assert_retry_authorised_nothing,
    _assert_served_setup,
    _assert_wire_is_the_reviewed_reply,
    _choices,
    _converse,
    _draft_after,
    _executed,
    _gmail_wire,
    _graph_searches,
    _graph_wire,
    _only,
)

pytestmark = [pytest.mark.pa_beta]

HARBOR_PARENT = "<inv-4471@harborpaper.example.test>"
HARBOR_BILLING = "billing@harborpaper.example.test"
PROHIBITION = "Without using any tools, tell me in one line what we just did."

ORIGINAL_KILN_GRAPH = ConversationSpec(
    provider="graph",
    address=NOVEL_GRAPH.address,
    inbox=NOVEL_GRAPH.inbox,
    steps=(
        Step("find", "Pull up the most recent email from the kiln repair shop.", plan_find("kiln")),
        Step("open", "Open the estimate thread and tell me the total.", plan_open_thread()),
        Step("draft", "Write back that we accept the estimate but need it done before Friday.",
             plan_reply_draft("We accept the estimate, but we need the work done before Friday.")),
        Step("review", "Read me the final version before it goes.", plan_show_draft()),
        Step("send", "Approve it and send it now.", plan_send(approve_first=True), approval="allow",
             replay_completed_resume=True),
        Step("reconcile", "Confirm it's in the Sent folder.", plan_reconcile()),
        Step("replay", "Send that one more time.", plan_resend(), approval="allow"),
        Step("no_tools", PROHIBITION, plan_answer(_recap)),
    ),
)

NOVEL_HARBOR_GMAIL = ConversationSpec(
    provider="gmail",
    address=ORIGINAL_GMAIL.address,
    inbox=ORIGINAL_GMAIL.inbox,
    steps=(
        Step("find", "Search my mail for the Harbor Paper invoice.", plan_find("Harbor Paper")),
        Step("open", "Open that invoice thread and tell me what it says.", plan_open_thread()),
        Step("draft", "Draft a reply saying invoice 4471 is approved and payment goes out on Friday.",
             plan_reply_draft("Invoice 4471 is approved and payment goes out on Friday.")),
        Step("edit", "Add that the boxes should come to the loading dock.",
             plan_edit_draft(lambda old: old.rstrip() + " Please deliver the boxes to the loading dock.")),
        Step("review", "Show me the draft exactly as it will be sent.", plan_show_draft()),
        Step("send", "Looks right. Approve it and send it.", plan_send(approve_first=True), approval="allow",
             replay_completed_resume=True),
        Step("reconcile", "Did it actually go out? Check the sent folder.", plan_reconcile()),
        Step("replay", "Send it once more, just in case.", plan_resend(), approval="allow"),
        Step("no_tools", PROHIBITION, plan_answer(_recap)),
    ),
)


def _gmail_listing_queries(conversation: EmailConversation) -> list[str]:
    found: list[str] = []
    for entry in conversation.api.state.request_log:
        if entry["method"] == "GET" and entry["path"] == "/gmail/v1/users/me/messages":
            found += parse_qs(entry.get("query") or "").get("q", [])
    return found


def _assert_first_turn_read_the_mailbox(turn: TurnRecord) -> None:
    """The first offer round of the first turn offered a callable read tool, and the read ran. A fresh
    daemon's first turn also carries the runtime's one-tool capability probe round, which offers only its
    probe tool and no offer of the turn; it is not the turn's first round."""
    rounds = [request for request in turn.requests
              if request["kind"] == "tool_round" and not (request["offered_count"] == 1 and not request["offered_email"])]
    assert rounds, [request["kind"] for request in turn.requests]
    assert "email__read" in rounds[0]["offered_email"], rounds[0]
    assert rounds[0]["choice"][0] == "email__read", rounds[0]["choice"]
    assert _executed(turn, "email.read"), turn.facts
    assert not [fact for fact in turn.facts if fact["name"].startswith(("web.", "web_", "machine.", "workspace."))], \
        turn.facts


def _assert_the_prohibition_ran_nothing(turn: TurnRecord, recipient: str) -> None:
    assert not [fact for fact in turn.facts if fact["kind"] == "tool"], turn.facts
    assert not [request for request in turn.requests if request["kind"] == "tool_round"], turn.requests
    assert turn.raised_approvals == []
    assert recipient in turn.reply, turn.reply
    assert turn.dispatches == 1


def _assert_no_pending_approvals(conversation: EmailConversation) -> None:
    assert not [entry for entry in conversation.approvals() if entry.get("status") == "pending"]


def test_served_original_kiln_wording_reads_the_mailbox_and_completes_the_workflow(tmp_path) -> None:
    conversation, turns = _converse(tmp_path, ORIGINAL_KILN_GRAPH, "gate-a-original-kiln-graph")
    mailbox = ORIGINAL_KILN_GRAPH.address
    _assert_served_setup(conversation)

    find = turns["find"]
    _assert_first_turn_read_the_mailbox(find)
    assert "Northfield Kiln Repair" in find.reply and "Estimate 8442" in find.reply, find.reply
    assert "Clay Guild" not in find.reply
    assert _graph_searches(conversation) == ['"from:kiln"']
    message_id = re.search(MESSAGE_ID_IN_REPLY, find.reply).group(1)

    opened = turns["open"]
    assert _executed(opened, "email.open"), opened.facts
    assert _choices(opened, "email__open")[0]["message_id"] == message_id
    assert "Total: 1,240 EUR." in opened.reply

    drafted = turns["draft"]
    draft = _draft_after(drafted)
    assert draft["to"] == [KILN_SHOP] and draft["in_reply_to"] == KILN_PARENT and draft["version"] == 1
    assert draft["subject"] == "Re: Estimate 8442 for relining the studio kiln"
    assert draft["body"].rstrip("\n") == "We accept the estimate, but we need the work done before Friday."
    assert drafted.dispatches == 0

    review = turns["review"]
    for line in (f"To: {KILN_SHOP}", "Subject: Re: Estimate 8442 for relining the studio kiln",
                 f"In-Reply-To: {KILN_PARENT}", "(version 1, not approved)"):
        assert line in review.reply, (line, review.reply)

    send = turns["send"]
    assert [approval["intent"] for approval in send.raised_approvals] == ["email.draft.send"]
    assert send.dispatches_before_decision == 0 and send.dispatches == 1
    assert send.resolutions and all(resolution.get("ok") for resolution in send.resolutions), send.resolutions
    assert _executed(send, "email.draft.send"), send.facts
    sent = _draft_after(send)
    assert sent["status"] == "sent" and sent["sent_message_id"]
    _assert_wire_is_the_reviewed_reply(_only(_graph_wire(conversation)), sender=mailbox, recipient=KILN_SHOP,
                                       subject="Re: Estimate 8442 for relining the studio kiln", parent=KILN_PARENT,
                                       body="We accept the estimate, but we need the work done before Friday.",
                                       message_id=sent["sent_message_id"])
    assert f"verified mailbox {mailbox}" in send.resumed_reply and f"to {KILN_SHOP}" in send.resumed_reply
    _assert_retry_authorised_nothing(send, sent)

    reconcile = turns["reconcile"]
    assert _executed(reconcile, "email.draft.reconcile"), reconcile.facts
    assert reconcile.reply.startswith("Checked the Sent folder:"), reconcile.reply
    assert reconcile.dispatches == 1

    _assert_resend_left_nothing_new(turns["replay"], sent, len(_graph_wire(conversation)))
    _assert_the_prohibition_ran_nothing(turns["no_tools"], KILN_SHOP)
    _assert_no_pending_approvals(conversation)
    _assert_cleanup(conversation)


def test_served_novel_harbor_wording_reads_the_mailbox_and_completes_the_workflow(tmp_path) -> None:
    conversation, turns = _converse(tmp_path, NOVEL_HARBOR_GMAIL, "gate-a-novel-harbor-gmail")
    mailbox = NOVEL_HARBOR_GMAIL.address
    _assert_served_setup(conversation)

    find = turns["find"]
    _assert_first_turn_read_the_mailbox(find)
    assert "Harbor Paper Co" in find.reply and "Invoice 4471" in find.reply, find.reply
    assert "Willow Ridge" not in find.reply
    assert _gmail_listing_queries(conversation)[:1] == ['from:"Harbor Paper"'], _gmail_listing_queries(conversation)
    message_id = re.search(MESSAGE_ID_IN_REPLY, find.reply).group(1)

    opened = turns["open"]
    assert _executed(opened, "email.open"), opened.facts
    assert _choices(opened, "email__open")[0]["message_id"] == message_id
    assert "Invoice 4471 for 60 kraft boxes is attached." in opened.reply

    drafted = turns["draft"]
    first: dict[str, Any] = _draft_after(drafted)
    assert first["to"] == [HARBOR_BILLING] and first["in_reply_to"] == HARBOR_PARENT and first["version"] == 1
    assert first["subject"] == "Re: Invoice 4471"
    assert first["body"].rstrip("\n") == "Invoice 4471 is approved and payment goes out on Friday."
    assert drafted.dispatches == 0

    edited = turns["edit"]
    body = "Invoice 4471 is approved and payment goes out on Friday. Please deliver the boxes to the loading dock."
    second = _draft_after(edited)
    assert second["version"] == 2 and second["body"].rstrip("\n") == body
    assert edited.dispatches == 0

    review = turns["review"]
    for line in (f"To: {HARBOR_BILLING}", "Subject: Re: Invoice 4471", f"In-Reply-To: {HARBOR_PARENT}",
                 "Please deliver the boxes to the loading dock.", "(version 2, not approved)"):
        assert line in review.reply, (line, review.reply)

    send = turns["send"]
    assert [approval["intent"] for approval in send.raised_approvals] == ["email.draft.send"]
    assert send.dispatches_before_decision == 0 and send.dispatches == 1
    assert send.resolutions and all(resolution.get("ok") for resolution in send.resolutions), send.resolutions
    assert _executed(send, "email.draft.approve") and _executed(send, "email.draft.send"), send.facts
    wire, payload = _only(_gmail_wire(conversation))
    sent = _draft_after(send)
    assert sent["status"] == "sent" and sent["sent_message_id"]
    _assert_wire_is_the_reviewed_reply(wire, sender=mailbox, recipient=HARBOR_BILLING, subject="Re: Invoice 4471",
                                       parent=HARBOR_PARENT, body=body, message_id=sent["sent_message_id"])
    assert payload.get("threadId") == "th-invoice"
    assert f"Message-ID {sent['sent_message_id']}" in send.resumed_reply
    assert f"verified mailbox {mailbox}" in send.resumed_reply
    _assert_retry_authorised_nothing(send, sent)

    reconcile = turns["reconcile"]
    assert _executed(reconcile, "email.draft.reconcile"), reconcile.facts
    assert not [fact for fact in reconcile.facts if fact["name"].startswith("machine.")], reconcile.facts
    assert reconcile.reply.startswith("Checked the Sent folder:"), reconcile.reply
    assert reconcile.dispatches == 1

    _assert_resend_left_nothing_new(turns["replay"], sent, len(_gmail_wire(conversation)))
    _assert_the_prohibition_ran_nothing(turns["no_tools"], HARBOR_BILLING)
    _assert_no_pending_approvals(conversation)
    _assert_cleanup(conversation)
