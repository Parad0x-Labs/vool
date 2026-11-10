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
    if intent not in RECIPIENT_INTENTS:
        return EmailArgumentVerdict(arguments=args, dropped_account=dropped)
    addresses = [
        match
        for entry in _recipient_entries(args)
        for match in _ADDRESS_RE.findall(entry)
    ]
    if not addresses:
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
