"""The email account authority: which mailbox a turn works in, and what each configured account can do.

An account SLOT is a name (`work`, `personal`, the legacy `default`) with up to three credential-store
entries behind it: `email.smtp.<slot>` (how it sends), `email.imap.<slot>` (how it reads) and, for an
OAuth provider, `email.oauth.<provider>.<slot>` (the grant). The two transport entries of one slot are one
MAILBOX only when their configured identities agree -- a name is not an identity, so a slot whose
IMAP entry names another mailbox than its SMTP entry is refused as a mismatch, never read through.

A SELECTION is one slot with the transports it supports, chosen in this order and never by fallback:

1. an explicit account argument (a tool argument, a draft's pinned account) -- honoured as written: what it
   lacks is reported by the transport that needs it, under that exact name, never by another account;
2. the operator's default binding from the Operator Profile (`default_account.email`, an opaque
   `credential:<name>` reference), resolved with the profile's own scope precedence
   (chat > project > work/personal > global) for the turn in flight;
3. the legacy `default` slot, only when nothing else was ever selected: no binding, and either the
   `default` slot exists or no account is configured at all (the setup guidance then names it);
4. one configured account with no binding is that account; two or more without a binding is a
   question for the operator (`account_required`), not a guess.

Whatever is selected is checked before any transport is opened: a slot lacking the requested
transport is `account_send_only` / `account_read_only`, a provider slot without its grant is
`needs_reconnect`, and identities that disagree are `account_mismatch`. Every refusal carries the
configured accounts that could serve, so the operator can choose. Nothing here reads a secret value
for its own sake: identities come from the non-secret fields of the account blobs, and the listing
never returns passwords, grants or client secrets.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from core import credential_store

TRANSPORTS = ("imap", "smtp")
#: Selection outcomes that are refusals (a transport must not be opened on them).
ACCOUNT_REFUSALS = frozenset({
    "account_required", "account_unavailable", "account_mismatch", "account_send_only", "account_read_only",
    "needs_reconnect",
})
LEGACY_DEFAULT = "default"


@dataclass(frozen=True)
class AccountSelection:
    """One selected mailbox for one transport, or the reason none could be selected."""

    ok: bool
    account: str = ""
    address: str = ""
    identity: str = ""
    provider: str = ""
    #: explicit | chat | project | work | personal | global | only_account | legacy_default
    source: str = ""
    read: bool = False
    send: bool = False
    status: str = "selected"
    message: str = ""
    choices: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "account": self.account, "address": self.address, "identity": self.identity,
                "provider": self.provider, "source": self.source, "read": self.read, "send": self.send,
                "status": self.status, "message": self.message, "choices": list(self.choices)}


@dataclass(frozen=True)
class AccountRecord:
    """What the credential store holds for one slot, without its secrets."""

    account: str
    provider: str
    address: str
    identities: dict[str, str]      # transport -> configured identity (empty when the transport is absent)
    has_grant: bool                 # the OAuth handle is present (provider accounts only; True otherwise)
    error: str = ""                 # a blob that could not be read as an account

    @property
    def read(self) -> bool:
        return bool(self.identities.get("imap"))

    @property
    def send(self) -> bool:
        return bool(self.identities.get("smtp"))

    @property
    def coherent(self) -> bool:
        named = {value for value in self.identities.values() if value}
        return len(named) <= 1

    @property
    def state(self) -> str:
        if self.error:
            return "unreadable"
        if not self.coherent:
            return "mismatch"
        if self.provider in ("gmail", "graph") and not self.has_grant:
            return "needs_reconnect"
        if self.read and not self.send:
            return "read_only"
        if self.send and not self.read:
            return "send_only"
        return "configured"


# ---------------------------------------------------------------------------------------------
# What is configured
# ---------------------------------------------------------------------------------------------


def _load_blob(kind: str, account: str) -> dict[str, Any] | None:
    raw = credential_store.get_credential(f"email.{kind}.{account}")
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except Exception:
        return {"_unreadable": True}
    return data if isinstance(data, dict) else {"_unreadable": True}


def _record(account: str, present: dict[str, bool]) -> AccountRecord:
    from core.email_providers.base import ProviderAccountError, provider_for_account
    from core.email_tools import configured_identity

    identities: dict[str, str] = {}
    provider = ""
    address = ""
    error = ""
    blobs: dict[str, dict[str, Any]] = {}
    for kind in TRANSPORTS:
        if not present.get(kind):
            identities[kind] = ""
            continue
        blob = _load_blob(kind, account)
        if not isinstance(blob, dict) or blob.get("_unreadable"):
            error = f"the {kind} entry of account '{account}' could not be read as an account"
            identities[kind] = ""
            continue
        blobs[kind] = blob
        try:
            blob_provider = provider_for_account(blob)
        except ProviderAccountError as exc:
            error = str(exc)
            identities[kind] = ""
            continue
        if provider and blob_provider != provider:
            # Two transports, two providers: not one mailbox by construction.
            identities[kind] = f"{blob_provider}:" + configured_identity(account, blob)
        else:
            identities[kind] = configured_identity(account, blob)
        provider = provider or blob_provider
        address = address or str(blob.get("from_addr") or blob.get("username") or "").strip().lower()
    has_grant = True
    if provider in ("gmail", "graph"):
        has_grant = credential_store.has_credential(f"email.oauth.{provider}.{account}")
    return AccountRecord(account=account, provider=provider or "imap", address=address, identities=identities,
                         has_grant=has_grant, error=error)


def configured_accounts() -> dict[str, AccountRecord]:
    """Every slot the credential store names (by its transport entries), with what each can do."""
    present: dict[str, dict[str, bool]] = {}
    for row in credential_store.list_credentials():
        name = str(row.get("name") or "")
        for kind in TRANSPORTS:
            prefix = f"email.{kind}."
            if name.startswith(prefix) and len(name) > len(prefix):
                present.setdefault(name[len(prefix):], {})[kind] = True
    return {account: _record(account, flags) for account, flags in sorted(present.items())}


def slot_of_binding(ref: str) -> str:
    """The slot an email default binding names, or '' when it is not an email account reference.

    Accepted spellings: `credential:email.smtp.<slot>`, `credential:email.imap.<slot>`,
    `credential:email.oauth.<provider>.<slot>` (the `credential:` label is optional). Anything else --
    another credential family, a bare secret -- names no email account and is never guessed at."""
    name = str(ref or "").strip()
    if name.lower().startswith("credential:"):
        name = name.split(":", 1)[1]
    for kind in TRANSPORTS:
        prefix = f"email.{kind}."
        if name.startswith(prefix) and len(name) > len(prefix):
            return name[len(prefix):]
    if name.startswith("email.oauth."):
        parts = name.split(".")
        if len(parts) == 4 and parts[3]:
            return parts[3]
    return ""


def default_binding(*, principal: str | None = None, session_id: str = "") -> tuple[str, str, str, str]:
    """(slot, binding_ref, source scope, item id) of the operator's default, or blanks when none is set.

    Read through the ONE Operator Profile authority with its own precedence (chat > project >
    work/personal > global) for the turn in flight, which the front door binds; an explicit principal
    or session wins over the bound turn. Only the credential NAME is read, never its value."""
    from core.email_tools import _profile_scope
    from core.operator_profile import resolve

    who, session, project = _profile_scope(principal, session_id)
    item = resolve(who, "default_account.email", session_id=session, project_id=project, touch=True)
    if item is None or not isinstance(item.value, dict):
        return "", "", "", ""
    ref = str(item.value.get("binding_ref") or "")
    return slot_of_binding(ref), ref, str(item.scope or "global"), str(item.item_id or "")


# ---------------------------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------------------------


def _capable(records: dict[str, AccountRecord], kind: str) -> tuple[str, ...]:
    return tuple(name for name, record in records.items()
                 if record.identities.get(kind) and record.state in ("configured", "read_only", "send_only"))


def _choices_text(choices: tuple[str, ...], kind: str) -> str:
    verb = "read" if kind == "imap" else "send"
    if not choices:
        return f"No configured account can {verb} yet; add one under Settings > Email accounts."
    return f"Accounts that can {verb}: {', '.join(choices)}. Name one, or choose a default under Settings > Email accounts."


def _refusal(status: str, message: str, kind: str, records: dict[str, AccountRecord], *, account: str = "",
             source: str = "") -> AccountSelection:
    choices = tuple(name for name in _capable(records, kind) if name != account)
    return AccountSelection(ok=False, account=account, source=source, status=status,
                            message=f"{message} {_choices_text(choices, kind)}".strip(), choices=choices)


def _check(record: AccountRecord, kind: str, source: str, records: dict[str, AccountRecord]) -> AccountSelection:
    verb = "read" if kind == "imap" else "send"
    name = record.account
    if record.error:
        return _refusal("account_unavailable", f"Account '{name}' cannot be used: {record.error}.", kind, records,
                        account=name, source=source)
    if not record.coherent:
        return _refusal(
            "account_mismatch",
            f"Account '{name}' is not one mailbox: its send entry is configured for "
            f"{record.identities.get('smtp') or 'nothing'} and its read entry for "
            f"{record.identities.get('imap') or 'nothing'}. Nothing was {verb} through it; reconnect the slot so both "
            "name the same mailbox.",
            kind, records, account=name, source=source)
    if not record.identities.get(kind):
        other = "smtp" if kind == "imap" else "imap"
        if record.identities.get(other):
            status = "account_send_only" if kind == "imap" else "account_read_only"
            can = "send" if kind == "imap" else "read"
            return _refusal(status, f"Account '{name}' can only {can}; it has no way to {verb}.", kind, records,
                            account=name, source=source)
        return _refusal("account_unavailable", f"Account '{name}' has no {verb} configuration.", kind, records,
                        account=name, source=source)
    if record.provider in ("gmail", "graph") and not record.has_grant:
        return _refusal(
            "needs_reconnect",
            f"Account '{name}' ({record.address or record.provider}) has no {record.provider} grant stored any more; "
            "reconnect it under Settings > Email accounts. Nothing was read or sent through another account.",
            kind, records, account=name, source=source)
    return AccountSelection(ok=True, account=name, address=record.address, identity=record.identities.get(kind, ""),
                            provider=record.provider, source=source, read=record.read, send=record.send,
                            status="selected", message=f"Using account '{name}' ({record.address or record.provider}).")


def select_account(kind: str, *, account: str = "", principal: str | None = None,
                   session_id: str = "") -> AccountSelection:
    """The one mailbox this transport should use, or the reason none can be chosen (see the module doc)."""
    kind = str(kind or "smtp").strip().lower()
    if kind not in TRANSPORTS:
        kind = "smtp"
    explicit = str(account or "").strip()
    records = configured_accounts()
    if explicit:
        # An explicit choice is honoured AS NAMED: it is never redirected, and what it lacks is reported
        # by the transport that needs it (`needs_credentials` for that exact name, a provider's own typed
        # refusal), with the configured choices. The one refusal here is a name whose two entries are
        # not one mailbox -- opening either would be a silent cross-account read or send.
        record = records.get(explicit)
        if record is None:
            return AccountSelection(ok=True, account=explicit, source="explicit",
                                    message=f"Using account '{explicit}', which is not configured yet.",
                                    choices=_capable(records, kind))
        if not record.coherent:
            return _check(record, kind, "explicit", records)
        return AccountSelection(ok=True, account=explicit, address=record.address,
                                identity=record.identities.get(kind, ""), provider=record.provider,
                                source="explicit", read=record.read, send=record.send, status="selected",
                                message=f"Using account '{explicit}' ({record.address or record.provider}).",
                                choices=_capable(records, kind))
    slot, ref, scope, _item = default_binding(principal=principal, session_id=session_id)
    if ref:
        if not slot:
            return _refusal("account_unavailable",
                            f"The default email account preference ({ref}) does not name an email account.",
                            kind, records, source=scope)
        record = records.get(slot)
        if record is None:
            return _refusal("account_unavailable",
                            f"The default email account '{slot}' ({ref}) is no longer configured.", kind, records,
                            account=slot, source=scope)
        return _check(record, kind, scope, records)
    if LEGACY_DEFAULT in records:
        return _check(records[LEGACY_DEFAULT], kind, "legacy_default", records)
    if not records:
        return AccountSelection(ok=True, account=LEGACY_DEFAULT, source="legacy_default",
                                message="No email account is configured yet.")
    if len(records) == 1:
        (_name, record), = records.items()
        return _check(record, kind, "only_account", records)
    return _refusal("account_required",
                    "Several email accounts are configured and none is chosen as the default.", kind, records)


# ---------------------------------------------------------------------------------------------
# The operator's view
# ---------------------------------------------------------------------------------------------


def list_accounts() -> list[dict[str, Any]]:
    """The configured accounts as rows: name, provider, address, capabilities, state, default flag."""
    return accounts_snapshot()["accounts"]


def accounts_snapshot(*, principal: str | None = None, session_id: str = "") -> dict[str, Any]:
    """Every configured account with its identity, capabilities and state; the chosen default and where
    it comes from; and what a read and a send would select right now. No secret is included."""
    records = configured_accounts()
    slot, ref, scope, item_id = default_binding(principal=principal, session_id=session_id)
    rows: list[dict[str, Any]] = []
    for name, record in records.items():
        rows.append({
            "account": name,
            "provider": record.provider,
            "address": record.address,
            "read": record.read,
            "send": record.send,
            "state": record.state,
            "is_default": bool(slot) and slot == name,
            # The binding reference the Settings page writes to choose this account (a NAME, not a value).
            "binding_ref": ("credential:email.smtp." + name) if record.send else ("credential:email.imap." + name),
            "error": record.error,
        })
    selections = {kind: select_account(kind, principal=principal, session_id=session_id).as_dict() for kind in TRANSPORTS}
    return {
        "accounts": rows,
        "default": {"account": slot, "binding_ref": ref, "source": scope, "item_id": item_id,
                    "configured": bool(slot) and slot in records},
        "selection": selections,
        "legacy_default_configured": LEGACY_DEFAULT in records,
    }


__all__ = [
    "ACCOUNT_REFUSALS",
    "AccountRecord",
    "AccountSelection",
    "accounts_snapshot",
    "configured_accounts",
    "default_binding",
    "list_accounts",
    "select_account",
    "slot_of_binding",
]
