"""Served: the account the operator chose is the account every step uses, in one conversation.

Two mailboxes are configured in the daemon's own home -- `work` (Microsoft Graph, the kiln estimate
inbox) and `personal` (Gmail, the orchard delivery inbox) -- and no slot named `default`. The operator
chooses `work` through the Settings door (POST /api/profile/remember, the one Operator Profile authority
the Email settings section writes to), and the conversation then runs:

* ORIGINAL (the chosen account throughout): "Pull up the most recent email from the kiln repair shop."
  with NO account named -> the runtime reads `work` (the Graph fixture records which mailbox each
  credential authenticated) -> open -> reply draft -> exact review -> approve and hold -> [the operator
  re-points the default to `personal`] -> "Send it now." with Allow -> ONE dispatch, from `work`, the
  mailbox the approval reviewed -> receipt -> replay -> reconcile -> repeated send dispatches nothing.
* NOVEL (an explicit choice, a denial, a disconnect, a restart): "check my personal inbox" names the
  account -> the Gmail mailbox is read and `work` is not touched -> a reply drafted from `personal` ->
  Deny (nothing sent; the denied token replayed is refused) -> Allow -> one Gmail dispatch -> the
  `personal` grant is removed from the credential store -> the next `personal` read is refused as
  needs_reconnect, with no provider request and no other mailbox read -> `work` is chosen again through
  the Settings door -> the daemon is restarted -> the chosen default survives: an unnamed read hits `work`.

What executes and what is simulated are exactly as in tests/test_email_served_chat_journeys.py (production
code from the chat door to the provider HTTP adapters; a certified SCRIPTED model and loopback provider
fixtures, both labelled). The scripted model passes an `account` argument only when the user named one.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs
from urllib.request import Request, urlopen

import pytest

from tests import _reader_served_rig as rig
from tests._email_served_conversation import (
    DRAFT_ID,
    NOVEL_GRAPH,
    ORIGINAL_GMAIL,
    ConversationSpec,
    EmailConversation,
    ModelView,
    Step,
    TurnRecord,
    _narrate_state,
    call,
    plan_find,
    plan_open_thread,
    plan_reconcile,
    plan_reply_draft,
    plan_resend,
    plan_send,
    plan_show_draft,
    say,
)
from tests.test_email_served_chat_journeys import (
    _assert_cleanup,
    _assert_resend_left_nothing_new,
    _assert_retry_authorised_nothing,
    _assert_served_setup,
    _draft_after,
    _evidence_path,
    _executed,
    _graph_wire,
)

pytestmark = [pytest.mark.pa_beta]

WORK = NOVEL_GRAPH.address            # studio@clayworks.example.test (Graph)
PERSONAL = ORIGINAL_GMAIL.address     # me@orchard-farm.example.test (Gmail)
ACCOUNTS = {"work": ("graph", WORK), "personal": ("gmail", PERSONAL)}
KILN_SHOP = "estimates@northfield-kiln.example.test"
ORCHARD_SUPPLY = "supply@willowridge-orchard.example.test"


def plan_approve_only() -> Any:
    """Approve the conversation's draft and hold it: the operator asked for the approval, not the send."""
    def plan(view: ModelView):
        approvals = view.results("email.draft.approve")
        if approvals:
            draft = approvals[-1].get("draft") if isinstance(approvals[-1].get("draft"), dict) else {}
            return say(f"Draft {draft.get('draft_id')} is approved and held; nothing has been sent."
                       if approvals[-1].get("ok") else f"The approval was not recorded ({approvals[-1].get('status')}).")
        checked = [item["draft"] for item in view.results("email.draft.get") if isinstance(item.get("draft"), dict)]
        draft_id = view.last_in_history(DRAFT_ID) or (str(checked[-1].get("draft_id") or "") if checked else "")
        if not draft_id:
            return call("email.draft.get")
        version = view.last_in_history(re.escape(draft_id) + r" \(version (\d+)")
        return call("email.draft.approve", draft_id=draft_id, **({"expected_version": int(version)} if version else {}))
    return plan


def plan_send_held() -> Any:
    """Send the approved, held draft (no approval step in this turn); report the outcome or its state."""
    inner = plan_send(approve_first=False)

    def plan(view: ModelView):
        sends = view.results("email.draft.send")
        if not sends and not view.last_in_history(DRAFT_ID):
            checked = [item["draft"] for item in view.results("email.draft.get") if isinstance(item.get("draft"), dict)]
            if checked:
                return say(_narrate_state(checked[-1]))
            return call("email.draft.get")
        return inner(view)
    return plan


ORIGINAL_STEPS = (
    Step("find", "Pull up the most recent email from the kiln repair shop.", plan_find("kiln")),
    Step("open", "Open the estimate thread and tell me the total.", plan_open_thread()),
    Step("draft", "Write back that we accept the estimate but need it done before Friday.",
         plan_reply_draft("We accept the estimate, but we need the work done before Friday.")),
    Step("review", "Show me the draft exactly as it will be sent, and which account it goes from.", plan_show_draft()),
    Step("approve", "Approve it, but hold the send for now.", plan_approve_only()),
    # <- the operator re-points the default to `personal` here (the test does it through the Settings door)
    Step("send", "Send it now.", plan_send_held(), approval="allow", replay_completed_resume=True),
    Step("reconcile", "Confirm it's in the Sent folder.", plan_reconcile()),
    Step("replay", "Send that one more time.", plan_resend(), approval="allow"),
)
NOVEL_STEPS = (
    Step("personal_find", "Now check my personal inbox for the orchard supplier.", plan_find("orchard", account="personal")),
    Step("personal_draft", "Reply from my personal account that Wednesday at 10:00 works for our delivery.",
         plan_reply_draft("Wednesday at 10:00 works for our delivery.", account="personal")),
    Step("personal_refuse", "Approve and send it.", plan_send(approve_first=True), approval="deny", replay_denied_token=True),
    Step("personal_send", "I've changed my mind. Send it now.", plan_send(approve_first=True), approval="allow"),
    # <- the test removes the personal grant here
    Step("disconnected", "Check my personal inbox again for anything new from the orchard supplier.",
         plan_find("orchard", account="personal")),
    # <- the test chooses `work` again and restarts the daemon here
    Step("after_restart", "Pull up the latest message from the kiln repair shop.", plan_find("kiln")),
)
SPEC = ConversationSpec(provider="graph", address=WORK, inbox=NOVEL_GRAPH.inbox, steps=ORIGINAL_STEPS + NOVEL_STEPS)


class TwoAccountConversation(EmailConversation):
    """The served conversation with two configured accounts and no `default` slot."""

    def open(self) -> TwoAccountConversation:
        self.api.gmail_inbox(PERSONAL, [dict(entry) for entry in ORIGINAL_GMAIL.inbox])
        super().open()
        return self

    def _seed_account(self) -> None:
        assert self.daemon is not None
        lines = ["from core import credential_store"]
        for name, (provider, address) in ACCOUNTS.items():
            grant = self.api.add_oauth_client(provider, name, address)
            handle = {"client_id": "c", "client_secret": "s", "refresh_token": grant,
                      "token_url": self.api.token_url, "account_email": address}
            blob = {"provider": provider, "from_addr": address, "token_url": self.api.token_url,
                    "api_base": self.api.gmail_base if provider == "gmail" else self.api.graph_base}
            lines.append(f"credential_store.store_credential('email.oauth.{provider}.{name}', {json.dumps(json.dumps(handle))}, label='{name} oauth')")
            lines.append("for kind in ('imap', 'smtp'):")
            lines.append(f"    credential_store.store_credential('email.' + kind + '.{name}', {json.dumps(json.dumps(blob))}, label='{name} ' + kind)")
        lines.append("print('seeded')")
        self.run_in_home("\n".join(lines) + "\n")

    def run_in_home(self, script: str) -> str:
        """Run a Python script against the daemon's own home (its credential store), as setup does."""
        assert self.daemon is not None
        done = subprocess.run([sys.executable, "-c", script], cwd=str(rig.REPO_ROOT), env=self.daemon.env(),
                              capture_output=True, text=True, timeout=180)
        if done.returncode != 0:
            raise RuntimeError(f"in-home script failed:\n{done.stdout}\n{done.stderr}")
        return done.stdout

    def _json(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        assert self.daemon is not None
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = Request(f"{self.daemon.base_url}{path}", data=data, method=method,
                          headers={"Content-Type": "application/json"} if data is not None else {})
        with urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8") or "{}")

    def choose_default(self, account: str) -> dict[str, Any]:
        """The Settings door: the Email section writes the default through POST /api/profile/remember."""
        reply = self._json("POST", "/api/profile/remember", {
            "category": "default_account.email", "value": f"credential:email.smtp.{account}", "scope": "global", "replace": True,
        })
        assert reply.get("ok"), reply
        return self.accounts()

    def accounts(self) -> dict[str, Any]:
        return self._json("GET", "/api/email/accounts")

    def restart(self) -> None:
        assert self.daemon is not None
        self.daemon.stop()
        self.daemon.start(timeout=240)
        self.certification = self.daemon.certify(timeout=240)


def _principals(conversation: EmailConversation, *, since: int) -> list[str]:
    """Which mailbox each authenticated provider READ after request index `since` hit."""
    return [str(entry.get("principal") or "") for entry in conversation.api.state.request_log[since:]
            if entry["method"] == "GET" and ("/messages" in entry["path"] or "/mailFolders" in entry["path"])]


def _gmail_listing_queries(conversation: EmailConversation, *, since: int) -> list[str]:
    found: list[str] = []
    for entry in conversation.api.state.request_log[since:]:
        if entry["method"] == "GET" and entry["path"] == "/gmail/v1/users/me/messages":
            found += parse_qs(entry.get("query") or "").get("q", [])
    return found


def _newest_draft(turn: TurnRecord) -> dict[str, Any]:
    """The draft this turn worked on: the newest row of the session's draft store (two drafts exist once the
    personal reply is saved beside the sent work reply)."""
    rows = list(turn.drafts_after.values())
    assert rows, turn.drafts_after
    return max(rows, key=lambda row: float(row.get("created_at") or 0))


def _read_choice(turn: TurnRecord) -> dict[str, Any]:
    rounds = [request for request in turn.requests if request["kind"] == "tool_round" and request["choice"]
              and request["choice"][0] == "email__read"]
    assert rounds, [request.get("choice") for request in turn.requests]
    return rounds[0]["choice"][1]


def test_served_the_chosen_account_is_used_throughout_and_an_explicit_choice_a_denial_a_disconnect_and_a_restart_behave(tmp_path: Path) -> None:
    conversation = TwoAccountConversation(tmp_path / "two-accounts", SPEC, label="two-accounts")
    evidence = _evidence_path(tmp_path, "two-accounts")
    turns: dict[str, TurnRecord] = {}
    marks: dict[str, int] = {}
    snapshots: dict[str, Any] = {}

    def run(step: Step) -> TurnRecord:
        marks[step.name] = len(conversation.api.state.request_log)
        turns[step.name] = conversation.turn(step)
        evidence.write_text(json.dumps({**conversation.to_json(), "account_snapshots": snapshots}, indent=2, default=str))
        return turns[step.name]

    try:
        conversation.open()
        _assert_served_setup(conversation)
        steps = {step.name: step for step in SPEC.steps}

        # -- choose `work` through the Settings door -------------------------------------------
        snapshots["before_choice"] = conversation.accounts()
        assert {row["account"] for row in snapshots["before_choice"]["accounts"]} == {"work", "personal"}
        assert not snapshots["before_choice"]["default"]["account"]
        assert snapshots["before_choice"]["selection"]["imap"]["status"] == "account_required"
        snapshots["after_choice"] = conversation.choose_default("work")
        assert snapshots["after_choice"]["default"]["account"] == "work"
        assert snapshots["after_choice"]["selection"]["imap"]["account"] == "work"
        assert snapshots["after_choice"]["selection"]["smtp"]["account"] == "work"
        assert "password" not in json.dumps(snapshots) and "refresh_token" not in json.dumps(snapshots)

        # -- ORIGINAL: read, open, draft, review, approve, re-point, send once, reconcile, replay ---
        find = run(steps["find"])
        assert "account" not in _read_choice(find), _read_choice(find)
        assert _executed(find, "email.read"), find.facts
        assert set(_principals(conversation, since=marks["find"])) == {WORK}, conversation.api.state.request_log[marks["find"]:]
        assert "kiln" in find.reply.lower() and KILN_SHOP in find.reply, find.reply

        opened = run(steps["open"])
        assert _executed(opened, "email.open") and "1,240" in opened.reply, opened.reply
        assert set(_principals(conversation, since=marks["open"])) == {WORK}

        drafted = run(steps["draft"])
        draft = _draft_after(drafted)
        assert draft["account_resolved"] == "work" and draft["status"] == "draft", draft
        assert draft["to"] == [KILN_SHOP] and draft["in_reply_to"] == "<est-8442@northfield-kiln.example.test>", draft

        review = run(steps["review"])
        assert "account work" in review.reply and "before Friday" in review.reply, review.reply

        approved = run(steps["approve"])
        assert _executed(approved, "email.draft.approve") and approved.raised_approvals == [], approved.facts
        assert _draft_after(approved)["status"] == "approved"
        assert conversation.api.sent_count("graph", WORK) == 0

        # The operator re-points the default to `personal` AFTER the approval: the approved draft stays
        # bound to the mailbox it was reviewed for.
        snapshots["repointed"] = conversation.choose_default("personal")
        assert snapshots["repointed"]["selection"]["smtp"]["account"] == "personal"

        sent = run(steps["send"])
        assert [approval["intent"] for approval in sent.raised_approvals] == ["email.draft.send"], sent.raised_approvals
        assert sent.dispatches_before_decision == 0
        sent_draft = _draft_after(sent)
        assert sent_draft["status"] == "sent" and sent_draft["account_resolved"] == "work", sent_draft
        assert sent_draft["receipt"]["account"] == "work" and sent_draft["receipt"]["verified_principal"] == WORK, sent_draft["receipt"]
        assert conversation.api.sent_count("graph", WORK) == 1 and conversation.api.sent_count("gmail", PERSONAL) == 0
        wire = _graph_wire(conversation)
        assert len(wire) == 1
        _assert_retry_authorised_nothing(sent, sent_draft)

        reconciled = run(steps["reconcile"])
        assert _executed(reconciled, "email.draft.reconcile"), reconciled.facts
        assert conversation.api.sent_count("graph", WORK) == 1

        replayed = run(steps["replay"])
        _assert_resend_left_nothing_new(replayed, sent_draft, 1)
        assert conversation.api.sent_count("graph", WORK) == 1 and conversation.api.sent_count("gmail", PERSONAL) == 0

        # -- NOVEL: an explicit choice reads the other mailbox and nothing else -------------------
        personal_find = run(steps["personal_find"])
        assert _read_choice(personal_find).get("account") == "personal"
        assert set(_principals(conversation, since=marks["personal_find"])) == {PERSONAL}, \
            conversation.api.state.request_log[marks["personal_find"]:]
        assert any("orchard" in query.lower() for query in _gmail_listing_queries(conversation, since=marks["personal_find"]))
        assert ORCHARD_SUPPLY in personal_find.reply, personal_find.reply

        personal_draft = run(steps["personal_draft"])
        second = _newest_draft(personal_draft)
        assert second["account_resolved"] == "personal" and second["to"] == [ORCHARD_SUPPLY], second
        assert second["draft_id"] != sent_draft["draft_id"]

        refused = run(steps["personal_refuse"])
        assert [approval["intent"] for approval in refused.raised_approvals] == ["email.draft.send"]
        assert refused.resolutions and all((r.get("approval") or {}).get("status") == "denied" for r in refused.resolutions), refused.resolutions
        assert conversation.api.sent_count("gmail", PERSONAL) == 0 and conversation.api.sent_count("graph", WORK) == 1
        (denied_control,) = refused.controls
        # The counter is the work mailbox's (the spec's provider): the replayed denied token dispatched nothing
        # there either, and every approval it raised was denied.
        assert denied_control["dispatches_after_request"] == denied_control["dispatches_before"] == 1, denied_control
        assert all((r.get("approval") or {}).get("status") == "denied" for r in denied_control["resolutions"]), denied_control

        personal_sent = run(steps["personal_send"])
        assert conversation.api.sent_count("gmail", PERSONAL) == 1 and conversation.api.sent_count("graph", WORK) == 1
        third = _newest_draft(personal_sent)
        assert third["status"] == "sent" and third["receipt"]["verified_principal"] == PERSONAL, third

        # -- the personal grant is gone: refused as needs_reconnect, no other mailbox read ----------
        conversation.run_in_home("from core import credential_store\n"
                                 "print(credential_store.delete_credential('email.oauth.gmail.personal'))\n")
        snapshots["disconnected"] = conversation.accounts()
        personal_row = next(row for row in snapshots["disconnected"]["accounts"] if row["account"] == "personal")
        assert personal_row["state"] == "needs_reconnect", personal_row
        disconnected = run(steps["disconnected"])
        assert _principals(conversation, since=marks["disconnected"]) == [], conversation.api.state.request_log[marks["disconnected"]:]
        read_facts = [fact for fact in disconnected.facts if fact["kind"] == "tool" and fact["name"] == "email.read"]
        # The adapter's own typed refusal for a provider slot without its grant (needs_setup: "no OAuth connection
        # yet ... connect it through email account setup"); the listing calls the same state needs_reconnect.
        assert read_facts and all(fact["status"] in {"needs_setup", "needs_reconnect"} for fact in read_facts), disconnected.facts
        assert "connect" in disconnected.reply.lower() and "personal" in disconnected.reply, disconnected.reply

        # -- `work` is chosen again, the daemon restarts, and the choice survives -------------------
        snapshots["rechosen"] = conversation.choose_default("work")
        conversation.restart()
        _assert_served_setup(conversation)
        snapshots["after_restart"] = conversation.accounts()
        assert snapshots["after_restart"]["default"]["account"] == "work"
        after = run(steps["after_restart"])
        assert "account" not in _read_choice(after)
        assert set(_principals(conversation, since=marks["after_restart"])) == {WORK}
        assert KILN_SHOP in after.reply, after.reply
        assert conversation.api.sent_count("graph", WORK) == 1 and conversation.api.sent_count("gmail", PERSONAL) == 1
    except Exception as exc:
        conversation.error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        conversation.close()
        evidence.write_text(json.dumps({**conversation.to_json(), "account_snapshots": snapshots}, indent=2, default=str))
    _assert_cleanup(conversation)
