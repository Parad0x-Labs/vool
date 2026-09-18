"""One real served email conversation: daemon, /api/chat, /api/mode approvals, provider fixture.

Everything between the HTTP request and the provider's HTTP API is production code from this
checkout: the chat door, routing with SHIPPED defaults (VOOL_ALWAYS_ON_CATALOG is removed from the
daemon's environment), the chat-lane policy, the planner gate, the tool offer, the tool loop, the mode
permission controller and its pending approvals, the draft store, the OAuth client and the Gmail and
Microsoft Graph adapters. Two things are substituted, and both are labelled wherever they appear:

* SIMULATED MODEL -- :class:`ScriptedEmailModel` answers every model request the daemon makes, after
  passing the production certification door. Its choices are SCRIPTED: each step is chosen only from
  what the runtime delivered in that request -- the conversation history and the same-turn tool
  observations in the prompt, and the tool names the runtime offered. When the runtime offers no email
  tool it cannot call one; when the history carries no id it cannot invent one (it may look the
  session's latest draft up with email.draft.get when that tool is offered). The text it authors for a
  reply or an edit is scripted per step and labelled as such, and so is the one persistent choice: asked
  to send an already-sent reply again, it asks for the email family and sends (plan_resend). The runtime's
  own structured model calls are answered in their documented wire formats: the conductor planner, fact
  extraction, and the single-well-known-answer judge (core.entity_ambiguity), whose SCRIPTED verdict is
  "not ambiguous" -- these conversations only ask about their own history and mailbox.
* SIMULATED PROVIDERS -- tests/provider_api_fixture.py serves strict Gmail and Graph HTTP APIs on
  loopback. No live mailbox, account, credential or paid inference is involved.

Door-level controls run inside a step after the operator's decision, as a client could send them: a
denied approval token replayed at /api/chat (the shipped web client never resends a denial), and a
completed approved resume replayed unchanged (a client retry). Whatever either raises is denied.

Every process started here is stopped in :meth:`EmailConversation.close`, which also reports any
process still running in the daemon's process group or on its port.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from email.utils import parseaddr
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from core.entity_ambiguity import AMBIGUITY_SYSTEM_PROMPT
from tests import _reader_served_rig as rig
from tests.provider_api_fixture import ProviderApiServer

MODEL = "email-journey:stub"
PROVIDER_NAME = "email-journey"
ACCOUNT = "default"
OBSERVATION_HEADER = "Real tool observations from this same turn follow."
PLANNER_WIRE_FORMAT = 'Wire format: return ONLY {"requests": [...]}'
FACT_EXTRACTION_REQUEST = "Extract stable persistent facts"
#: The first line of the runtime's single-well-known-answer judge prompt (core.entity_ambiguity).
ENTITY_AMBIGUITY_JUDGE = AMBIGUITY_SYSTEM_PROMPT.splitlines()[0]
#: The operation the scripted planner names. The conductor catalogue has no email operation, and a
#: name the registry does not serve becomes an UNRESOLVED node instead of vanishing
#: (core/conductor/planner.py), which is what a model with no fitting operation produces.
SCRIPTED_PLANNER_OPERATION = "email_conversation"
DRAFT_ID = r"\b(ed-[0-9a-f]{12})\b"
MESSAGE_ID_IN_REPLY = r"message id ([^\s)]+)\)"

Action = tuple[str, dict[str, Any]]


# --------------------------------------------------------------------------- what a request carried


def _observations(prompt: str) -> list[dict[str, Any]]:
    """The same-turn tool observations the runtime rendered into the prompt, one JSON object a line."""
    if OBSERVATION_HEADER not in prompt:
        return []
    found: list[dict[str, Any]] = []
    for line in prompt.split(OBSERVATION_HEADER, 1)[1].splitlines():
        line = line.strip()
        if line.startswith("- {"):
            with contextlib.suppress(ValueError):
                found.append(json.loads(line[2:]))
    return found


def _final_user_text(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            return rig._all_text([message]).strip()
    return ""


@dataclass(frozen=True)
class ModelView:
    """Everything ONE model request carried: the only input a scripted choice may use."""

    prompt: str
    offered: frozenset[str]
    observed: tuple[dict[str, Any], ...]

    @property
    def history(self) -> str:
        return self.prompt.split(OBSERVATION_HEADER, 1)[0]

    def results(self, intent: str) -> list[dict[str, Any]]:
        return [item for item in self.observed if item.get("intent") == intent]

    def last_in_history(self, pattern: str) -> str:
        found = re.findall(pattern, self.history)
        return str(found[-1]) if found else ""


def call(intent: str, **arguments: Any) -> Action:
    return intent.replace(".", "__"), arguments


def _offers(view: ModelView, intent: str) -> bool:
    return intent.replace(".", "__") in view.offered


def _checked_drafts(view: ModelView) -> list[dict[str, Any]]:
    return [item["draft"] for item in view.results("email.draft.get") if isinstance(item.get("draft"), dict)]


def _conversation_draft_id(view: ModelView) -> str:
    """The draft the conversation last named, else the one this turn looked up."""
    checked = _checked_drafts(view)
    return view.last_in_history(DRAFT_ID) or (str(checked[-1].get("draft_id") or "") if checked else "")


def say(message: str) -> Action:
    return "respond__direct", {"message": message}


def _observation_summary(item: dict[str, Any]) -> dict[str, Any]:
    draft = item.get("draft") if isinstance(item.get("draft"), dict) else {}
    receipt = item.get("receipt") if isinstance(item.get("receipt"), dict) else {}
    return {
        "intent": item.get("intent"), "ok": item.get("ok"), "status": item.get("status"),
        "messages": [m.get("provider_id") for m in item.get("messages") or [] if isinstance(m, dict)],
        "draft": {k: draft.get(k) for k in ("draft_id", "version", "status", "approved")} if draft else None,
        "receipt": {k: receipt.get(k) for k in ("outcome", "message_id", "verified_principal")} if receipt else None,
    }


class ScriptedEmailModel:
    """SIMULATED MODEL with SCRIPTED choices -- see the module docstring for what it may read."""

    def __init__(self) -> None:
        self.plan: Callable[[ModelView], Action] | None = None
        self.requests: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def answer(self, path: str, body: dict[str, Any]) -> dict[str, Any] | str:
        messages = [m for m in (body.get("messages") or []) if isinstance(m, dict)]
        prompt = rig._all_text(messages)
        offered = frozenset(rig._tool_names(body))
        view = ModelView(prompt=prompt, offered=offered, observed=tuple(_observations(prompt)))
        chosen: Action | None = None
        if PLANNER_WIRE_FORMAT in prompt:
            kind = "planner"
            reply: dict[str, Any] | str = json.dumps({"requests": [{
                "request": _final_user_text(messages), "operation": SCRIPTED_PLANNER_OPERATION, "depends_on": []}]})
        elif FACT_EXTRACTION_REQUEST in prompt:
            kind, reply = "fact_extraction", json.dumps({"facts": [{"action": "NOOP"}]})
        elif ENTITY_AMBIGUITY_JUDGE in prompt:
            kind, reply = "entity_ambiguity", json.dumps({"ambiguous": False, "referents": [], "clarification": ""})
        elif self.plan is None:
            kind, reply = "unplanned", "SCRIPT: no plan is active for this request."
        else:
            chosen = self.plan(view)
            name, arguments = chosen
            if offered:
                kind = "tool_round"
                reply = (f"SCRIPT: the runtime did not offer {name}." if name not in offered else {
                    "role": "assistant", "content": "", "tool_calls": [{
                        "id": f"call_{uuid.uuid4().hex[:10]}", "type": "function",
                        "function": {"name": name, "arguments": arguments}}]})
            else:
                kind = "plain_text"
                reply = (str(arguments.get("message") or "") if name == "respond__direct"
                         else f"SCRIPT: the runtime offered no tools, and this step needed {name}.")
        with self._lock:
            self.requests.append({
                "path": path, "kind": kind,
                "offered_email": sorted(name for name in offered if name.startswith("email__")),
                "offered_count": len(offered),
                "observed": [_observation_summary(item) for item in view.observed],
                "choice": list(chosen) if chosen else None,
                "reply": reply if isinstance(reply, str) else "tool_call",
                "history_tail": view.history[-2400:],
            })
        return reply


def _install(provider: rig.CapturingProvider, model: ScriptedEmailModel) -> None:
    handler = provider._server.RequestHandlerClass
    original_get = handler.do_GET

    def do_GET(self) -> None:
        if self.path.startswith("/api/ps"):
            # The scripted model occupies no memory: reported resident, so no model load is attempted.
            return self._send({"models": [{"name": MODEL, "size": 0, "size_vram": 0}]})
        return original_get(self)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
        except ValueError:
            body = {}
        probe = rig._probe_reply(body)
        reply = probe if probe is not None else model.answer(self.path, body)
        name = body.get("model")
        if isinstance(reply, dict):
            message = {"role": "assistant", "content": reply.get("content", ""), "tool_calls": reply.get("tool_calls") or []}
            if self.path.startswith("/v1/"):
                finish = "tool_calls" if message["tool_calls"] else "stop"
                return self._send({"model": name, "choices": [{"index": 0, "finish_reason": finish, "message": message}]})
            return self._send({"model": name, "done": True, "done_reason": "stop", "message": message})
        if self.path.startswith("/v1/"):
            return self._send({"model": name, "choices": [{"index": 0, "finish_reason": "stop",
                                                           "message": {"role": "assistant", "content": reply}}],
                               "usage": {"prompt_tokens": 40, "completion_tokens": 30}})
        return self._send({"model": name, "done": True, "done_reason": "stop",
                           "message": {"role": "assistant", "content": reply}, "prompt_eval_count": 40, "eval_count": 30})

    handler.do_GET = do_GET
    handler.do_POST = do_POST


# --------------------------------------------------------------------------- scripted plans


def _narrate_messages(result: dict[str, Any]) -> str:
    rows = [m for m in result.get("messages") or [] if isinstance(m, dict)]
    if not rows:
        return f"No matching message came back ({result.get('status')})."
    parts = [f"{m.get('from')} - \"{m.get('subject')}\" on {m.get('date')} (message id {m.get('provider_id')})" for m in rows]
    return f"I found {len(rows)} message(s): " + "; ".join(parts) + "."


def _narrate_draft(draft: dict[str, Any]) -> str:
    state = "approved" if draft.get("approved") else "not approved"
    return (f"Draft {draft.get('draft_id')} (version {draft.get('version')}, {state}) from account "
            f"{draft.get('account')} to {', '.join(draft.get('to') or [])}, subject \"{draft.get('subject')}\": "
            f"{draft.get('body')} Nothing has been sent.")


def _exact_draft(draft: dict[str, Any]) -> str:
    state = "approved" if draft.get("approved") else "not approved"
    return (f"Draft {draft.get('draft_id')} (version {draft.get('version')}, {state}), account {draft.get('account')}\n"
            f"To: {', '.join(draft.get('to') or [])}\nSubject: {draft.get('subject')}\n"
            f"In-Reply-To: {draft.get('in_reply_to')}\n\n{draft.get('body')}")


def _narrate_send(result: dict[str, Any]) -> str:
    receipt = result.get("receipt") if isinstance(result.get("receipt"), dict) else {}
    draft = result.get("draft") if isinstance(result.get("draft"), dict) else {}
    if result.get("status") == "already_sent":
        return f"Draft {draft.get('draft_id')} was already sent once, so it was not sent again (already_sent)."
    if result.get("ok") and receipt.get("message_id"):
        return (f"Sent draft {draft.get('draft_id')}: Message-ID {receipt.get('message_id')} to "
                f"{', '.join(receipt.get('to') or [])} from account {receipt.get('account')} (verified mailbox "
                f"{receipt.get('verified_principal')}, outcome {receipt.get('outcome')}).")
    return f"It was not sent: {result.get('status')}. {result.get('response_preview') or ''}".strip()


def _narrate_state(draft: dict[str, Any]) -> str:
    if draft.get("status") == "sent":
        return f"Draft {draft.get('draft_id')} was already sent, so nothing was sent again."
    return f"Draft {draft.get('draft_id')} is {draft.get('status')}; nothing was sent."


def plan_find(sender: str, *, account: str = "") -> Callable[[ModelView], Action]:
    """Search the mailbox by the sender words the user used; report what came back. `account` is the
    account the user NAMED in the request ("my personal inbox"); an unnamed account is left to the
    runtime's selection (the operator's default), which the scripted model never guesses at."""
    def plan(view: ModelView) -> Action:
        reads = view.results("email.read")
        if not reads:
            arguments: dict[str, Any] = {"sender": sender, "limit": 10}
            if account:
                arguments["account"] = account
            return call("email.read", **arguments)
        result = reads[-1]
        if not result.get("ok"):
            return say(f"I could not read the mailbox: {result.get('status')}. {result.get('response_preview') or ''}".strip())
        return say(_narrate_messages(result))
    return plan


def plan_open_thread() -> Callable[[ModelView], Action]:
    """Open the message the conversation last named, as a thread; report what was written."""
    def plan(view: ModelView) -> Action:
        opened = view.results("email.open")
        if opened:
            rows = [m for m in opened[-1].get("messages") or [] if isinstance(m, dict)]
            if not rows:
                return say(f"I could not open that thread ({opened[-1].get('status')}).")
            words = " ".join(f"{m.get('from')} wrote: {m.get('body') or m.get('snippet')}" for m in rows)
            return say(f"The thread has {len(rows)} message(s) (message id {rows[-1].get('provider_id')}). {words}")
        message_id = view.last_in_history(MESSAGE_ID_IN_REPLY)
        if not message_id:
            return say("SCRIPT: no message id was visible in the conversation history.")
        return call("email.open", message_id=message_id, thread=True)
    return plan


def plan_reply_draft(body: str, *, account: str = "") -> Callable[[ModelView], Action]:
    """Draft a reply to the conversation's message; `body` is the scripted model's authored text, and
    `account` the account the user NAMED for it (else the runtime's selection)."""
    def plan(view: ModelView) -> Action:
        saves = view.results("email.draft.save")
        if saves:
            draft = saves[-1].get("draft")
            return say(_narrate_draft(draft) if isinstance(draft, dict) else f"The draft was not saved ({saves[-1].get('status')}).")
        parents = [m for item in view.results("email.open") for m in item.get("messages") or [] if isinstance(m, dict)]
        if not parents:
            message_id = view.last_in_history(MESSAGE_ID_IN_REPLY)
            if not message_id:
                return say("SCRIPT: no message id was visible in the conversation history.")
            return call("email.open", message_id=message_id, **({"account": account} if account else {}))
        parent = parents[-1]
        references = parent.get("references")
        chain = (references.split() if isinstance(references, str) else list(references or []))
        if parent.get("message_id") and parent.get("message_id") not in chain:
            chain.append(parent.get("message_id"))
        recipient = parseaddr(str(parent.get("reply_to") or parent.get("from") or ""))[1]
        arguments: dict[str, Any] = {"kind": "reply", "to": recipient, "subject": parent.get("subject"), "body": body,
                                     "in_reply_to": parent.get("message_id"), "references": chain}
        if account:
            arguments["account"] = account
        if not _offers(view, "email.draft.save"):
            # SCRIPTED PERSISTENCE, as in plan_resend: a session whose last draft was already sent is offered
            # the dispatched stage's seats, so a NEW reply asks for the email family first (the expansion is
            # turn navigation; every later round of the turn offers the family, revision 6 Gate B).
            if _offers(view, "capability.expand_family") and not view.results("capability.expand_family"):
                return call("capability.expand_family", family="email")
            return say("SCRIPT: the runtime offered neither email.draft.save nor a way to ask for the email family.")
        return call("email.draft.save", **arguments)
    return plan


def plan_edit_draft(compose: Callable[[str], str]) -> Callable[[ModelView], Action]:
    """Edit the conversation's draft; `compose(old body)` is the scripted model's authored text."""
    def plan(view: ModelView) -> Action:
        saves = view.results("email.draft.save")
        if saves:
            draft = saves[-1].get("draft")
            return say(_narrate_draft(draft) if isinstance(draft, dict) else f"The edit was not saved ({saves[-1].get('status')}).")
        drafts = [item["draft"] for item in view.results("email.draft.get") if isinstance(item.get("draft"), dict)]
        if not drafts:
            draft_id = view.last_in_history(DRAFT_ID)
            return call("email.draft.get", **({"draft_id": draft_id} if draft_id else {}))
        draft = drafts[-1]
        return call("email.draft.save", draft_id=draft["draft_id"], kind="reply", to=draft["to"], subject=draft["subject"],
                    body=compose(str(draft["body"])), in_reply_to=draft["in_reply_to"], references=draft["references"])
    return plan


def plan_show_draft() -> Callable[[ModelView], Action]:
    def plan(view: ModelView) -> Action:
        drafts = [item["draft"] for item in view.results("email.draft.get") if isinstance(item.get("draft"), dict)]
        if not drafts:
            draft_id = view.last_in_history(DRAFT_ID)
            return call("email.draft.get", **({"draft_id": draft_id} if draft_id else {}))
        return say(_exact_draft(drafts[-1]))
    return plan


def plan_send(*, approve_first: bool) -> Callable[[ModelView], Action]:
    """Approve (when the user just did) and send the conversation's draft; report the outcome. When the
    runtime does not offer the tool the next step needs, look the draft up and report its state."""
    def plan(view: ModelView) -> Action:
        sends = view.results("email.draft.send")
        if sends:
            return say(_narrate_send(sends[-1]))
        draft_id = _conversation_draft_id(view)
        approvals = view.results("email.draft.approve")
        needed = "email.draft.approve" if approve_first and not approvals else "email.draft.send"
        if not draft_id or not _offers(view, needed):
            checked = _checked_drafts(view)
            if checked:
                return say(_narrate_state(checked[-1]))
            if _offers(view, "email.draft.get"):
                return call("email.draft.get", **({"draft_id": draft_id} if draft_id else {}))
            return say(f"SCRIPT: the runtime offered neither {needed} nor email.draft.get.")
        if approve_first and not approvals:
            version = view.last_in_history(re.escape(draft_id) + r" \(version (\d+)")
            return call("email.draft.approve", draft_id=draft_id, **({"expected_version": int(version)} if version else {}))
        if approve_first and not any(item.get("ok") for item in approvals):
            return say(f"The approval was not recorded ({approvals[-1].get('status')}), so nothing was sent.")
        return call("email.draft.send", draft_id=draft_id)
    return plan


def plan_reconcile() -> Callable[[ModelView], Action]:
    def plan(view: ModelView) -> Action:
        done = view.results("email.draft.reconcile")
        if done:
            receipt = done[-1].get("receipt") if isinstance(done[-1].get("receipt"), dict) else {}
            return say(f"Checked the Sent folder: {done[-1].get('status')}. {done[-1].get('response_preview') or ''} "
                       f"{receipt.get('reconciliation_evidence') or ''}".strip())
        draft_id = _conversation_draft_id(view)
        if draft_id and _offers(view, "email.draft.reconcile"):
            return call("email.draft.reconcile", draft_id=draft_id)
        if not draft_id and _offers(view, "email.draft.get") and not view.results("email.draft.get"):
            return call("email.draft.get")
        return say("SCRIPT: no draft to reconcile was visible, and the runtime offered no way to find one.")
    return plan


def plan_resend() -> Callable[[ModelView], Action]:
    """The user asks to send an already-sent reply again. SCRIPTED PERSISTENCE: the model tries to send the
    same draft again -- asking for the email family when the runtime has not offered the send tool -- and
    reports what came back."""
    def plan(view: ModelView) -> Action:
        sends = view.results("email.draft.send")
        if sends:
            return say(_narrate_send(sends[-1]))
        draft_id = _conversation_draft_id(view)
        if not draft_id:
            if _offers(view, "email.draft.get") and not view.results("email.draft.get"):
                return call("email.draft.get")
            return say("SCRIPT: no draft id was visible, and none could be looked up.")
        if _offers(view, "email.draft.send"):
            return call("email.draft.send", draft_id=draft_id)
        if _offers(view, "capability.expand_family") and not view.results("capability.expand_family"):
            return call("capability.expand_family", family="email")
        return say("SCRIPT: the runtime offered no way to send the draft again.")
    return plan


def plan_answer(compose: Callable[[ModelView], str]) -> Callable[[ModelView], Action]:
    """Answer in words only, from the history."""
    def plan(view: ModelView) -> Action:
        return say(compose(view))
    return plan


# --------------------------------------------------------------------------- the conversations


@dataclass(frozen=True)
class Step:
    name: str
    text: str
    plan: Callable[[ModelView], Action]
    #: The operator's decision on a send approval this step raises: "" (none expected), allow, deny.
    approval: str = ""
    #: After a DENY: replay the denied token at /api/chat, as a stale or forged client could.
    replay_denied_token: bool = False
    #: After an ALLOW and its resume: replay that completed resume unchanged, as a client retry could.
    replay_completed_resume: bool = False


@dataclass(frozen=True)
class ConversationSpec:
    provider: str
    address: str
    inbox: tuple[dict[str, Any], ...]
    steps: tuple[Step, ...]


def _hold(view: ModelView) -> str:
    draft_id = view.last_in_history(DRAFT_ID)
    return f"Understood. Draft {draft_id} stays unsent until you tell me to send it."


def _recap(view: ModelView) -> str:
    recipient = view.last_in_history(r"Sent draft \S+ Message-ID \S+ to (\S+@[\w.-]+)")
    return f"We reviewed, approved and sent your reply to {recipient or 'the supplier'}, and it went out once."


ORIGINAL_GMAIL = ConversationSpec(
    provider="gmail",
    address="me@orchard-farm.example.test",
    inbox=(
        {"from": "Willow Ridge Orchard Supply <supply@willowridge-orchard.example.test>", "subject": "Delivery scheduling",
         "body": "Hello,\n\nWe can bring the apple crates on Tuesday at 14:00 or Wednesday at 10:00. Which suits you?\n\nMara",
         "message_id": "<wro-20260910@willowridge-orchard.example.test>", "date": "Wed, 10 Sep 2026 09:15:00 +0000",
         "thread_id": "th-orchard"},
        {"from": "Harbor Paper Co <billing@harborpaper.example.test>", "subject": "Invoice 4471",
         "body": "Invoice 4471 for 60 kraft boxes is attached.", "message_id": "<inv-4471@harborpaper.example.test>",
         "date": "Tue, 09 Sep 2026 16:00:00 +0000", "thread_id": "th-invoice"},
    ),
    steps=(
        Step("check", "Check my email from the orchard supplier.", plan_find("orchard")),
        Step("open", "Open that delivery thread and show me what they wrote.", plan_open_thread()),
        Step("draft", "Draft a reply saying Wednesday at 10:00 works for our delivery.",
             plan_reply_draft("Wednesday at 10:00 works for our delivery.")),
        Step("edit", "Change it to say Wednesday 10:00 is confirmed, and ask them to use the north gate.",
             plan_edit_draft(lambda _old: "Wednesday 10:00 is confirmed. Please use the north gate.")),
        Step("review", "Show me the draft exactly as it will be sent.", plan_show_draft()),
        Step("hold", "Hold off for now, don't send anything yet.", plan_answer(_hold)),
        Step("send", "Looks good. Approve it and send it.", plan_send(approve_first=True), approval="allow",
             replay_completed_resume=True),
        Step("reconcile", "Did it actually go out? Check the sent folder.", plan_reconcile()),
        Step("replay", "Send it again, just to be sure.", plan_resend(), approval="allow"),
        Step("no_tools", "Don't use any tools, just tell me in one line what we were doing.", plan_answer(_recap)),
    ),
)

NOVEL_GRAPH = ConversationSpec(
    provider="graph",
    address="studio@clayworks.example.test",
    inbox=(
        {"from": {"emailAddress": {"name": "Northfield Kiln Repair", "address": "estimates@northfield-kiln.example.test"}},
         "subject": "Estimate 8442 for relining the studio kiln",
         "body": "Hi,\n\nRelining the electric kiln: parts 780 EUR, labour 460 EUR.\nTotal: 1,240 EUR.\nWe can start on Monday.\n\nJonas",
         "message_id": "<est-8442@northfield-kiln.example.test>", "date": "2026-09-11T08:30:00Z"},
        {"from": {"emailAddress": {"name": "Clay Guild", "address": "news@clayguild.example.test"}},
         "subject": "September glaze workshop", "body": "Join the September glaze workshop.",
         "message_id": "<news-0911@clayguild.example.test>", "date": "2026-09-10T18:00:00Z"},
    ),
    steps=(
        Step("find", "Read the latest email from the kiln repair shop.", plan_find("kiln")),
        Step("open", "Open the estimate thread and tell me the total.", plan_open_thread()),
        Step("draft", "Write back that we accept the estimate but need it done before Friday.",
             plan_reply_draft("We accept the estimate, but we need the work done before Friday.")),
        Step("edit", "Add that the kiln is at the north studio.",
             plan_edit_draft(lambda old: old.rstrip() + " The kiln is at the north studio.")),
        Step("review", "Read me the final version before it goes.", plan_show_draft()),
        Step("refuse", "Approve and send it.", plan_send(approve_first=True), approval="deny", replay_denied_token=True),
        Step("send", "I've changed my mind. Send it now.", plan_send(approve_first=True), approval="allow",
             replay_completed_resume=True),
        Step("reconcile", "Confirm it's in the Sent folder.", plan_reconcile()),
        Step("replay", "Send that one more time.", plan_resend(), approval="allow"),
    ),
)

CONVERSATIONS = {"original-gmail": ORIGINAL_GMAIL, "novel-graph": NOVEL_GRAPH}


# --------------------------------------------------------------------------- the served conversation


@dataclass
class TurnRecord:
    name: str
    text: str
    turn_id: str = ""
    reply: str = ""
    requests: list[dict[str, Any]] = field(default_factory=list)
    raised_approvals: list[dict[str, Any]] = field(default_factory=list)
    #: Provider dispatches counted after the first request and BEFORE any operator decision.
    dispatches_before_decision: int = 0
    resolutions: list[dict[str, Any]] = field(default_factory=list)
    resumed_reply: str = ""
    resumed_requests: list[dict[str, Any]] = field(default_factory=list)
    facts: list[dict[str, Any]] = field(default_factory=list)
    #: The durable draft store's rows at the end of the turn (draft id -> row).
    drafts_after: dict[str, Any] = field(default_factory=dict)
    #: Door-level controls run after the decision (kind, token, reply, requests, raised approvals and
    #: their denials, provider dispatches before and after the request).
    controls: list[dict[str, Any]] = field(default_factory=list)
    dispatches: int = 0
    seconds: float = 0.0


def _reply_text(reply: dict[str, Any]) -> str:
    message = reply.get("message") if isinstance(reply.get("message"), dict) else {}
    return str(message.get("content") or reply.get("response") or "")


def _enable_email_policy(home: Path) -> None:
    config = home / "config"
    config.mkdir(parents=True, exist_ok=True)
    base = (rig.REPO_ROOT / "config" / "default_policy.yaml").read_text(encoding="utf-8")
    (config / "default_policy.yaml").write_text(
        base.rstrip("\n") + "\nemail:\n  read_enabled: true\n  send_enabled: true\n", encoding="utf-8")


class EmailConversation:
    """One conversation in its own daemon home, session and provider fixture."""

    def __init__(self, root: Path, spec: ConversationSpec, *, label: str,
                 daemon_env: dict[str, str] | None = None) -> None:
        self.root = Path(root)
        self.daemon_env = dict(daemon_env or {})
        self.home = self.root / "home"
        self.spec = spec
        self.api = ProviderApiServer()
        self.model = ScriptedEmailModel()
        self.provider = rig.CapturingProvider(default="SCRIPT: default reply")
        _install(self.provider, self.model)
        self.daemon: rig.ServedDaemon | None = None
        self.session = rig.canonical_session(f"email-journey-{label}-{uuid.uuid4().hex}")
        self.turns: list[TurnRecord] = []
        self.certification: dict[str, Any] = {}
        self.daemon_catalog_flag: str | None = None
        self.closed: dict[str, Any] = {}
        self.error = ""
        self._opened: list[str] = []

    # -- lifecycle -------------------------------------------------------------------------------

    def open(self) -> EmailConversation:
        self.home.mkdir(parents=True, exist_ok=True)
        self.api.start()
        self._opened.append("api")
        self.provider.__enter__()
        self._opened.append("provider")
        daemon = rig.ServedDaemon(self.home, provider=self.provider, model=MODEL, provider_name=PROVIDER_NAME, env_extra={
            "VOOL_MODEL_LOAD_FLOOR_GB": "0", "VOOL_CREDENTIAL_STORE": "vault", "VOOL_KEY_STORAGE_MODE": "file",
            "VOOL_ALLOW_LIVE_OLLAMA_TESTS": "0",
            # The runtime resolves VOOL_RAW_OLLAMA_API_URL before OLLAMA_HOST: it must name the scripted
            # model too, or an inherited dead or live endpoint decides where model requests go.
            "VOOL_RAW_OLLAMA_API_URL": self.provider.base_url,
            **self.daemon_env,
        })
        base_env = daemon.env

        def shipped_routing_env() -> dict[str, str]:
            env = base_env()
            env.pop("VOOL_ALWAYS_ON_CATALOG", None)  # shipped default: off (core/runtime_flags.py)
            return env

        daemon.env = shipped_routing_env  # type: ignore[method-assign]
        self.daemon = daemon
        self.daemon_catalog_flag = daemon.env().get("VOOL_ALWAYS_ON_CATALOG")
        if self.spec.provider == "gmail":
            self.api.gmail_inbox(self.spec.address, [dict(entry) for entry in self.spec.inbox])
        else:
            self.api.graph_inbox(self.spec.address, [dict(entry) for entry in self.spec.inbox])
        _enable_email_policy(self.home)
        self._seed_account()
        daemon.start(timeout=240)
        self._opened.append("daemon")
        self.certification = daemon.certify(timeout=240)
        return self

    def _seed_account(self) -> None:
        """OAuth credentials through the production credential store, inside the daemon's own home."""
        assert self.daemon is not None
        grant = self.api.add_oauth_client(self.spec.provider, ACCOUNT, self.spec.address)
        handle = {"client_id": "c", "client_secret": "s", "refresh_token": grant,
                  "token_url": self.api.token_url, "account_email": self.spec.address}
        blob = {"provider": self.spec.provider, "from_addr": self.spec.address,
                "api_base": self.api.gmail_base if self.spec.provider == "gmail" else self.api.graph_base,
                "token_url": self.api.token_url}
        script = (
            "from core import credential_store\n"
            f"credential_store.store_credential('email.oauth.{self.spec.provider}.{ACCOUNT}', {json.dumps(json.dumps(handle))}, label='journey oauth')\n"
            "for kind in ('imap', 'smtp'):\n"
            f"    credential_store.store_credential('email.' + kind + '.{ACCOUNT}', {json.dumps(json.dumps(blob))}, label='journey ' + kind)\n"
            "print('seeded')\n"
        )
        done = subprocess.run([sys.executable, "-c", script], cwd=str(rig.REPO_ROOT), env=self.daemon.env(),
                              capture_output=True, text=True, timeout=180)
        if done.returncode != 0:
            raise RuntimeError(f"account seeding failed:\n{done.stdout}\n{done.stderr}")

    def close(self) -> dict[str, Any]:
        outcome: dict[str, Any] = {}
        if self.daemon is not None and self.daemon.process is not None:
            pid, port = self.daemon.process.pid, self.daemon.port
            self.daemon.stop()
            outcome["daemon_exit_code"] = self.daemon.process.returncode
            outcome["leftover_processes"] = _leftover_processes(pid, port)
        if "provider" in self._opened:
            with contextlib.suppress(Exception):
                self.provider.__exit__(None, None, None)
        if "api" in self._opened:
            with contextlib.suppress(Exception):
                self.api.stop()
        self.closed = outcome
        return outcome

    # -- the doors -------------------------------------------------------------------------------

    def approvals(self) -> list[dict[str, Any]]:
        path = self.home / "data" / "pending_approvals.json"
        if not path.is_file():
            return []
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
        entries = list(data.values()) if isinstance(data, dict) else list(data)
        return [entry for entry in entries if isinstance(entry, dict)]

    def resolve(self, approval_id: str, decision: str) -> dict[str, Any]:
        assert self.daemon is not None
        body = json.dumps({"op": "resolve_approval", "session_id": self.session, "approval_id": approval_id,
                           "decision": decision}).encode("utf-8")
        request = Request(f"{self.daemon.base_url}/api/mode", data=body, headers={"Content-Type": "application/json"},
                          method="POST")
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))

    def facts(self) -> list[dict[str, Any]]:
        """This session's execution ledger rows, read-only from the daemon's own database."""
        database = self.home / "data" / "vool_web0_v2.db"
        if not database.is_file():
            return []
        conn = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT fact_id, turn_key, kind, name, ok, status, created_at FROM execution_facts "
                "WHERE session_id = ? ORDER BY created_at", (self.session,)).fetchall()
        except sqlite3.Error:
            rows = []
        finally:
            conn.close()
        keys = ("fact_id", "turn_key", "kind", "name", "ok", "status", "created_at")
        return [dict(zip(keys, row, strict=True)) for row in rows]

    def drafts(self) -> dict[str, Any]:
        found = sorted(self.home.rglob("drafts.json"))
        if not found:
            return {}
        return json.loads(found[0].read_text(encoding="utf-8") or "{}").get("drafts") or {}

    def turn(self, step: Step) -> TurnRecord:
        assert self.daemon is not None
        started = time.monotonic()
        known_facts = {fact["fact_id"] for fact in self.facts()}
        known_approvals = {str(entry.get("approval_id")) for entry in self.approvals()}
        # The chat door binds a raised approval to the request's client turn id (body turn_id ->
        # cancel_turn_id -> the approval's task_id, core/web/api/service.py and
        # core/mode_permission_policy.py), so the resume after a decision carries the SAME turn id.
        record = TurnRecord(name=step.name, text=step.text, turn_id=uuid.uuid4().hex)
        self.model.plan = step.plan
        mark = len(self.model.requests)
        try:
            reply = self.daemon.chat(step.text, session_id=self.session, timeout=300, turn_id=record.turn_id)
            record.reply = _reply_text(reply)
            record.requests = self.model.requests[mark:]
            raised = [entry for entry in self.approvals()
                      if entry.get("status") == "pending" and str(entry.get("approval_id")) not in known_approvals]
            record.raised_approvals = [{k: entry.get(k) for k in ("approval_id", "intent", "status", "session_id", "actions")}
                                       for entry in raised]
            record.dispatches_before_decision = self.api.sent_count(self.spec.provider, self.spec.address)
            if raised and step.approval:
                for entry in raised:
                    record.resolutions.append(self.resolve(str(entry["approval_id"]), step.approval))
                token = str(raised[-1]["approval_id"])
                if step.approval == "allow":
                    # The shipped web client resends an ALLOWED turn with the same turn id and the token
                    # (core/vool_chat_page.py resolvePermission -> resumeApprovedTurn).
                    mark = len(self.model.requests)
                    resumed = self.daemon.chat(step.text, session_id=self.session, timeout=300,
                                               turn_id=record.turn_id, approval_token=token)
                    record.resumed_reply = _reply_text(resumed)
                    record.resumed_requests = self.model.requests[mark:]
                    if step.replay_completed_resume:
                        record.controls.append(self._door_control("completed_resume_replayed", step, record, token))
                elif step.replay_denied_token:
                    # A DENIED turn is not resent by the shipped client (resolvePermission releases the
                    # token); this control presents the denied token anyway.
                    record.controls.append(self._door_control("denied_token_replayed", step, record, token))
        finally:
            self.model.plan = None
            record.facts = [fact for fact in self.facts() if fact["fact_id"] not in known_facts]
            record.drafts_after = self.drafts()
            record.dispatches = self.api.sent_count(self.spec.provider, self.spec.address)
            record.seconds = round(time.monotonic() - started, 1)
            self.turns.append(record)
        return record

    def _door_control(self, kind: str, step: Step, record: TurnRecord, token: str) -> dict[str, Any]:
        """Re-POST the step's turn carrying `token`, then deny everything the request raised."""
        assert self.daemon is not None
        known = {str(entry.get("approval_id")) for entry in self.approvals()}
        before = self.api.sent_count(self.spec.provider, self.spec.address)
        mark = len(self.model.requests)
        reply = self.daemon.chat(step.text, session_id=self.session, timeout=300, turn_id=record.turn_id,
                                 approval_token=token)
        raised = [entry for entry in self.approvals()
                  if entry.get("status") == "pending" and str(entry.get("approval_id")) not in known]
        control: dict[str, Any] = {
            "kind": kind, "approval_token": token, "reply": _reply_text(reply),
            "requests": self.model.requests[mark:],
            "raised_approvals": [{k: entry.get(k) for k in ("approval_id", "intent", "status", "session_id", "actions")}
                                 for entry in raised],
            "dispatches_before": before,
            "dispatches_after_request": self.api.sent_count(self.spec.provider, self.spec.address),
        }
        control["resolutions"] = [self.resolve(str(entry["approval_id"]), "deny") for entry in raised]
        return control

    def to_json(self) -> dict[str, Any]:
        return {
            "model": "SIMULATED (scripted choices, certified through the production door)",
            "providers": "SIMULATED (tests/provider_api_fixture.py on loopback)",
            "provider": self.spec.provider, "address": self.spec.address, "session": self.session,
            "home": str(self.home), "daemon_catalog_flag": self.daemon_catalog_flag,
            "certification": self.certification, "turns": [asdict(turn) for turn in self.turns],
            "fixture_requests": [{k: entry.get(k) for k in ("method", "path", "query", "principal", "status", "content_type")}
                                 for entry in self.api.state.request_log],
            "closed": self.closed, "error": self.error,
        }


def _leftover_processes(pid: int, port: int) -> list[str]:
    """Processes still in the daemon's process group or naming its port (never this process)."""
    listing = subprocess.run(["ps", "-axo", "pid=,pgid=,command="], capture_output=True, text=True, check=False).stdout
    own = os.getpid()
    leftovers = []
    for line in listing.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3 or not parts[0].isdigit() or int(parts[0]) == own:
            continue
        if int(parts[1]) == pid or f"--port {port}" in parts[2]:
            leftovers.append(line.strip())
    return leftovers
