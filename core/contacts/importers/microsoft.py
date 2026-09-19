"""Microsoft Outlook / Microsoft 365 contacts, read through Microsoft Graph with the account's credential binding.

Facts this adapter is built on (Microsoft documentation, checked 2026-09-15):

* ``GET https://graph.microsoft.com/v1.0/me/contacts`` with ``$select``; ``@odata.nextLink`` is followed as returned.
  ``Prefer: IdType="ImmutableId"`` keeps a contact's id stable when it moves between folders.
* Delegated permission ``Contacts.Read``. A missing grant answers 403 ``ErrorAccessDenied``; an expired token answers 401
  ``InvalidAuthenticationToken``; throttling answers 429 with ``Retry-After``. Errors are classified by status and
  ``error.code``, never by message text.
* ``emailAddresses`` is ``[{name, address}]``; ``imAddresses`` are strings (often SIP addresses of Teams users);
  ``mobilePhone``, ``businessPhones`` and ``homePhones`` carry the numbers.

This first import reads the default contacts folder (``/me/contacts``); other contact folders are not read.
"""
from __future__ import annotations

import contextlib
import urllib.parse
from typing import Any

from core.contacts.importers.base import (
    REASON_BINDING_REQUIRED,
    REASON_NEEDS_RECONNECT,
    REASON_PROVIDER_UNAVAILABLE,
    REASON_RATE_LIMITED,
    REASON_READ_FAILED,
    REASON_SCOPE_MISSING,
    RECOVERY,
    ImportedEntry,
    ImportedPerson,
    ImportSourceError,
    SourceAccount,
)
from core.contacts.importers.google import retry_after, transport_error_reason

API_HOST = "graph.microsoft.com"
CONTACTS_URL = f"https://{API_HOST}/v1.0/me/contacts"
SELECT = "id,displayName,givenName,surname,companyName,emailAddresses,imAddresses,mobilePhone,businessPhones,homePhones"
PAGE_SIZE = 100
MAX_PAGES = 500


class MicrosoftContactsAdapter:
    provider = "microsoft"
    label = "Microsoft Outlook contacts"
    needs_binding = True
    #: the credential binding providers an import may name: a Microsoft Outlook contacts connection only
    binding_providers = ("microsoft_contacts",)

    def __init__(self, transport: Any = None) -> None:
        self._transport = transport

    def _send(self, request: Any) -> Any:
        from core.kas.contract import TransportDeniedError, TransportUnknownError

        transport = self._transport
        if transport is None:
            from core.kas.transport import build_transport

            transport = build_transport(provider_id="microsoft_contacts", allowed_hosts=(API_HOST,))
        try:
            return transport(request)
        except (TransportDeniedError, TransportUnknownError) as exc:
            raise ImportSourceError(transport_error_reason(exc), str(getattr(exc, "reason", "") or exc)) from None

    def authorization(self, account: SourceAccount) -> dict[str, Any]:
        if not account.auth_binding:
            return {"state": "binding_required", "reason": REASON_BINDING_REQUIRED, "recovery": RECOVERY[REASON_BINDING_REQUIRED]}
        return {"state": "ready", "reason": "", "recovery": ""}

    def _error(self, response: Any, partial: tuple[ImportedPerson, ...]) -> ImportSourceError:
        status = int(response.status)
        code = ""
        with contextlib.suppress(Exception):
            code = str(((response.json() or {}).get("error") or {}).get("code") or "")
        if status == 401:
            reason = REASON_NEEDS_RECONNECT
        elif status == 403:
            reason = REASON_SCOPE_MISSING
        elif status == 429:
            reason = REASON_RATE_LIMITED
        elif status >= 500:
            reason = REASON_PROVIDER_UNAVAILABLE
        else:
            reason = REASON_READ_FAILED
        return ImportSourceError(reason, f"Microsoft Graph answered {status} {code}".strip(), retry_after=retry_after(response.headers), partial=partial)

    @staticmethod
    def person(record: dict[str, Any], *, account_label: str = "") -> ImportedPerson:
        entries: list[ImportedEntry] = []
        for item in record.get("emailAddresses") or []:
            if isinstance(item, dict) and item.get("address"):
                entries.append(ImportedEntry(kind="email", value=str(item["address"])))
        if record.get("mobilePhone"):
            entries.append(ImportedEntry(kind="phone", value=str(record["mobilePhone"]), label="mobile"))
        for label, key in (("work", "businessPhones"), ("home", "homePhones")):
            for number in record.get(key) or []:
                if number:
                    entries.append(ImportedEntry(kind="phone", value=str(number), label=label))
        for address in record.get("imAddresses") or []:
            text = str(address or "").strip()
            if text.lower().startswith("sip:"):
                text = text[4:]
            if text:
                entries.append(ImportedEntry(kind="messaging", value=text, channel="teams", provider_account=account_label or "Microsoft 365"))
        name = str(record.get("displayName") or "").strip() or " ".join(p for p in (str(record.get("givenName") or ""), str(record.get("surname") or "")) if p).strip()
        organization = str(record.get("companyName") or "")
        return ImportedPerson(source_ref=str(record.get("id") or ""), display_name=name or organization, entries=tuple(entries), organization=organization)

    def read(self, account: SourceAccount, *, limit: int) -> tuple[ImportedPerson, ...]:
        from core.kas.contract import KasRequest

        if not account.auth_binding:
            raise ImportSourceError(REASON_BINDING_REQUIRED)
        people: list[ImportedPerson] = []
        url = f"{CONTACTS_URL}?{urllib.parse.urlencode({'$select': SELECT, '$top': str(PAGE_SIZE)})}"
        for _page in range(MAX_PAGES):
            request = KasRequest(method="GET", url=url, purpose="contacts.import.microsoft", headers={"Prefer": 'IdType="ImmutableId"'},
                                 auth=account.auth_binding, timeout=20.0)
            response = self._send(request)
            if not response.ok:
                raise self._error(response, tuple(people))
            try:
                payload = response.json() or {}
            except Exception:
                raise ImportSourceError(REASON_READ_FAILED, "Graph answer was not JSON", partial=tuple(people)) from None
            for record in payload.get("value") or []:
                if isinstance(record, dict):
                    people.append(self.person(record, account_label=account.account_label))
            url = str(payload.get("@odata.nextLink") or "")
            if not url or len(people) >= limit:
                break
        return tuple(people[:limit])


__all__ = ["MicrosoftContactsAdapter"]
