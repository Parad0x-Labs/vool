"""Server-side checks on the arguments a model proposes for an email tool, before any approval card.

Two owning defects, both measured 2026-11-10 on 0.7.8 c684e66d through the chat page with a
connected test mailbox. "Reply to Ana's dinner email and tell her Friday at 19:30 works" was
drafted and proposed for sending to ``ana@example.com`` -- an address that appeared nowhere: not in
the person's words, not in their contacts, not in any mail read in the chat (Ana's real address was
``ana@vooltest.example``) -- on ``account='your_email_account'``, a name no configured account has.
The Manual-mode card asked "Run email.send" without naming either, so allowing it would have
mailed a guessed stranger had the account resolved.

* ``account``: a value that names no configured account (by slot or address) is the model's
  placeholder, not the person's choice; it is dropped so the default account serves, and that is
  what the approval card and the draft then show. A value naming a configured address is mapped
  to its slot.
* recipients (``to``/``cc``/``bcc``) of ``email.send``, ``email.reply`` and ``email.draft.save``:
  every address must already be grounded -- written by the person in this chat, held by a saved
  contact, or shown in a message header that a read tool returned in this chat. An address that
  is not is refused before anything is drafted or sent, with the recovery the model can take
  (read that sender's mail first, or ask). A bare name is left to the contacts resolver.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

ACCOUNT_INTENTS = frozenset(
    {"email.read", "email.open", "email.send", "email.reply", "email.draft.save"}
)
RECIPIENT_INTENTS = frozenset({"email.send", "email.reply", "email.draft.save"})
#: Tools whose observations show mail the person RECEIVED or sent (headers that can ground an
#: address). The model's own drafts and send attempts never ground an address they introduced.
_READ_INTENTS = frozenset({"email.read", "email.open", "email.thread", "contacts.lookup", "contacts.list"})
_ADDRESS_RE = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
REFUSAL_STATUS = "invalid_arguments"
_LOG = logging.getLogger("vool.email_argument_guard")


@dataclass(frozen=True)
class EmailArgumentVerdict:
    arguments: dict[str, Any]
    refused: bool = False
    status: str = ""
    message: str = ""
    dropped_account: str = ""


def _configured_accounts() -> dict[str, Any]:
    try:
        from core.email_accounts import configured_accounts

        return dict(configured_accounts())
    except Exception:
        return {}


def _normalized_account(value: str) -> tuple[str, bool]:
    """(account to use, dropped?) for a model-proposed account value."""
    wanted = str(value or "").strip()
    if not wanted:
        return "", False
    accounts = _configured_accounts()
    if not accounts:
        # Nothing configured: the tool's own needs_credentials refusal is the honest answer.
        return wanted, False
    if wanted in accounts:
        return wanted, False
    folded = wanted.casefold()
    for slot, record in accounts.items():
        if folded == str(slot).casefold():
            return str(slot), False
        if folded == str(getattr(record, "address", "") or "").casefold():
            return str(slot), False
    return "", True


def _recipient_entries(arguments: dict[str, Any]) -> list[str]:
    entries: list[str] = []
    for key in ("to", "cc", "bcc"):
        value = arguments.get(key)
        if isinstance(value, str):
            entries.extend(part for part in re.split(r"[,;]", value) if part.strip())
        elif isinstance(value, (list, tuple)):
            entries.extend(str(item) for item in value if str(item or "").strip())
    return entries


def _grounding_text(source_context: dict[str, Any] | None, user_text: str) -> str:
    context = dict(source_context or {})
    parts: list[str] = [str(user_text or "")]
    for item in list(context.get("conversation_history") or []):
        if isinstance(item, dict) and str(item.get("role") or "") == "user":
            parts.append(str(item.get("content") or ""))
    for item in list(context.get("runtime_tool_observations") or []):
        if isinstance(item, dict) and str(item.get("intent") or "").strip().lower() in _READ_INTENTS:
            try:
                parts.append(json.dumps(item, ensure_ascii=False, default=str))
            except Exception:
                parts.append(str(item))
    return "\n".join(parts).casefold()


def _contact_addresses() -> set[str]:
    try:
        from core.contacts import store as contacts_store  # type: ignore[attr-defined]

        rows = contacts_store.list_contacts()  # type: ignore[attr-defined]
    except Exception:
        return set()
    found: set[str] = set()
    try:
        found.update(match.casefold() for match in _ADDRESS_RE.findall(json.dumps(rows, default=str)))
    except Exception:
        pass
    return found



def _read_senders(source_context: dict[str, Any] | None) -> list[str]:
    """'Name <address>' strings of the From/Reply-To headers of mail read in this chat."""
    found: list[str] = []
    for item in list((source_context or {}).get("runtime_tool_observations") or []):
        if not isinstance(item, dict) or str(item.get("intent") or "").strip().lower() not in _READ_INTENTS:
            continue
        for message in list(item.get("messages") or []) + ([item.get("message")] if isinstance(item.get("message"), dict) else []):
            if not isinstance(message, dict):
                continue
            for key in ("reply_to", "from"):
                value = str(message.get(key) or "").strip()
                if value and _ADDRESS_RE.search(value):
                    found.append(value)
                    break
    return found


def _resolve_named_recipients(args: dict[str, Any], source_context: dict[str, Any] | None) -> dict[str, Any] | None:
    senders = _read_senders(source_context)
    if not senders:
        _LOG.info("no read senders in this turn to resolve recipient names against")
        return None
    changed = False
    out = dict(args)
    for key in ("to", "cc", "bcc"):
        value = out.get(key)
        entries = [value] if isinstance(value, str) else list(value or [])
        resolved: list[str] = []
        for entry in entries:
            text = str(entry or "").strip()
            if not text or "@" in text:
                resolved.append(text)
                continue
            needle = re.escape(text.casefold())
            # Newest message first (reads list newest first): "reply to the latest kucoin email" means
            # the latest one's address, even when older mail from the same sender used another.
            match = next((
                _ADDRESS_RE.search(sender).group(0).casefold()
                for sender in senders
                if re.search(rf"(?<![\w]){needle}(?![\w])", sender.casefold())
            ), "")
            if match:
                resolved.append(match)
                changed = True
            else:
                _LOG.info("recipient name %r matched none of %d senders read in this turn", text, len(senders))
                resolved.append(text)
        if entries:
            out[key] = resolved if not isinstance(value, str) else ", ".join(resolved)
    return out if changed else None

#: Where a reply's own words start: "... saying thanks", "... and tell them yes".
_REPLY_BODY_RE = re.compile(
    r"\b(?:saying|say|says|that\s+says|telling|tell\s+(?:them|him|her)|to\s+say|with\s+the\s+(?:message|text|words))\b.*$",
    re.IGNORECASE,
)
_UNREAD_WORDS_RE = re.compile(r"\b(?:unread|unseen|new|haven'?t\s+(?:read|opened)|not\s+(?:yet\s+)?read)\b", re.IGNORECASE)


def _grounded_read_filters(args: dict[str, Any], user_text: str) -> dict[str, Any]:
    """Drop read filters the person never asked for.

    Measured 2026-11-11 (battery v10, installed build): "reply to the latest kucoin email saying
    thanks" read with subject='Thank You' and unread-only -- the reply's own words became a
    filter -- and matched nothing, so the reply never happened. A subject filter stands only when
    its words are in what the person asked OUTSIDE the reply's words; unread-only only when they
    said unread/new."""
    asked = _REPLY_BODY_RE.sub(" ", " ".join(user_text.split())).casefold()
    out = dict(args)
    subject = str(out.get("subject") or "").strip()
    if subject:
        words = [w for w in re.findall(r"[\w'-]+", subject.casefold()) if len(w) > 2]
        if not words or not all(w in asked for w in words):
            out.pop("subject", None)
            _LOG.info("email.read: dropped a subject filter the person did not ask for")
    if out.get("unseen_only") and not _UNREAD_WORDS_RE.search(asked):
        out["unseen_only"] = False
        _LOG.info("email.read: dropped an unread-only filter the person did not ask for")
    return out


def guard_email_arguments(
    intent: str,
    arguments: dict[str, Any],
    *,
    source_context: dict[str, Any] | None,
    user_text: str = "",
) -> EmailArgumentVerdict:
    intent = str(intent or "").strip()
    args = dict(arguments or {})
    dropped = ""
    if intent in ACCOUNT_INTENTS and "account" in args:
        account, was_dropped = _normalized_account(str(args.get("account") or ""))
        if was_dropped:
            dropped = str(args.get("account") or "")
            args.pop("account", None)
            # A read whose "account" named no mailbox named the SENDER (measured: "What did KuCoin
            # email me about?" -> account='KuCoin' with no sender filter): search the default
            # account for mail from that name instead of returning every message.
            if intent == "email.read" and not str(args.get("sender") or args.get("from") or "").strip():
                # Only a name the PERSON wrote: a placeholder ('primary', 'your_email_account')
                # is not a sender and must not filter the read down to nothing.
                said = " ".join(str(user_text or "").casefold().split())
                if "@" not in dropped and dropped.strip() and dropped.strip().casefold() in said:
                    args["sender"] = dropped.strip()
        elif account:
            args["account"] = account
    if intent == "email.read" and str(user_text or "").strip():
        args = _grounded_read_filters(args, str(user_text))
    if intent not in RECIPIENT_INTENTS:
        return EmailArgumentVerdict(arguments=args, dropped_account=dropped)
    # A recipient given as a bare NAME ("kucoin") is resolved against the senders of mail read in
    # this chat before anything is drafted or shown: measured 2026-11-11 on the installed build,
    # "reply to the latest kucoin email saying thanks" reached the send card with to='kucoin'.
    # Exactly one matching address resolves it; several or none leave it to the contacts
    # resolver, and a send/reply whose recipient has no address is refused below.
    named = _resolve_named_recipients(args, source_context)
    if named is not None:
        args = named
    addresses = [
        match
        for entry in _recipient_entries(args)
        for match in _ADDRESS_RE.findall(entry)
    ]
    if not addresses:
        if intent in {"email.send", "email.reply"} and _recipient_entries(args):
            message = (
                "The recipient has no email address, so nothing was sent. Read the person's mail first "
                "(email.read with sender set to their name) and reply to that message, or ask the person "
                "for the address."
            )
            return EmailArgumentVerdict(arguments=args, refused=True, status=REFUSAL_STATUS,
                                        message=message, dropped_account=dropped)
        return EmailArgumentVerdict(arguments=args, dropped_account=dropped)
    grounding = _grounding_text(source_context, user_text)
    contacts = _contact_addresses()
    ungrounded = [
        address
        for address in addresses
        if address.casefold() not in grounding and address.casefold() not in contacts
    ]
    if not ungrounded:
        return EmailArgumentVerdict(arguments=args, dropped_account=dropped)
    listed = ", ".join(dict.fromkeys(ungrounded))
    message = (
        f"Recipient address {listed} was not written by the person, is not a saved contact, and "
        "does not appear in any mail read in this chat, so nothing was drafted or sent. Never guess "
        "an address. To reply to someone's mail, read it first (email.read with sender set to their "
        "name) and use the From address that message shows, or reply to it by its message id; if no "
        "such mail exists, ask the person for the address."
    )
    return EmailArgumentVerdict(
        arguments=args, refused=True, status=REFUSAL_STATUS, message=message, dropped_account=dropped,
    )


__all__ = ["EmailArgumentVerdict", "guard_email_arguments", "ACCOUNT_INTENTS", "RECIPIENT_INTENTS"]
