"""Google Contacts, read through the People API with the account's own credential binding (KAS transport).

Facts this adapter is built on (Google documentation, checked 2026-09-15):

* ``GET https://people.googleapis.com/v1/people/me/connections`` with ``personFields`` (names, emailAddresses,
  phoneNumbers, imClients, organizations, metadata); ``pageSize`` up to 1000; ``nextPageToken`` continues a read.
* Read scope ``contacts.readonly`` (or ``contacts``). A token without it answers 403 with ErrorInfo reason
  ``ACCESS_TOKEN_SCOPE_INSUFFICIENT``; an expired or revoked token answers 401; throttling answers 429.
* Errors are classified by status and ErrorInfo reason, never by message text.

The KAS transport attaches the credential named by the binding and pins the host; the adapter never holds a secret. A
failure part-way through a read raises with what was read so far, and the import owner never applies a partial read.
"""
from __future__ import annotations

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

API_HOST = "people.googleapis.com"
CONNECTIONS_URL = f"https://{API_HOST}/v1/people/me/connections"
PERSON_FIELDS = "names,emailAddresses,phoneNumbers,imClients,organizations,metadata"
PAGE_SIZE = 500
MAX_PAGES = 200
_IM = {"telegram": "telegram", "signal": "signal", "whatsapp": "whatsapp", "slack": "slack", "discord": "discord", "matrix": "matrix"}
_BINDING_REASONS = {"unknown_credential_binding", "credential_binding_not_verified", "credential_unavailable"}


def transport_error_reason(exc: BaseException) -> str:
    reason = str(getattr(exc, "reason", "") or "")
    if reason in _BINDING_REASONS:
        return REASON_BINDING_REQUIRED
    return REASON_PROVIDER_UNAVAILABLE


def retry_after(headers: Any) -> int | None:
    for key, value in dict(headers or {}).items():
        if str(key).lower() == "retry-after":
            try:
                return max(0, int(str(value).strip()))
            except ValueError:
                return None
    return None


class GoogleContactsAdapter:
    provider = "google"
    label = "Google Contacts"
    needs_binding = True
    #: the credential binding providers an import may name: a Google Contacts connection only, so a key stored for another
    #: service is never attached to a People API request
    binding_providers = ("google_contacts",)

    def __init__(self, transport: Any = None) -> None:
        self._transport = transport

    def _send(self, request: Any) -> Any:
        from core.kas.contract import TransportDeniedError, TransportUnknownError

        transport = self._transport
        if transport is None:
            from core.kas.transport import build_transport

            transport = build_transport(provider_id="google_contacts", allowed_hosts=(API_HOST,))
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
        reason_code, status_name = "", ""
        try:
            error = (response.json() or {}).get("error") or {}
            status_name = str(error.get("status") or "")
            reason_code = next((str(d.get("reason")) for d in error.get("details") or [] if isinstance(d, dict) and d.get("reason")), "")
        except Exception:
            pass
        if status == 401:
            reason = REASON_NEEDS_RECONNECT
        elif status == 403 and (reason_code == "ACCESS_TOKEN_SCOPE_INSUFFICIENT" or status_name == "PERMISSION_DENIED"):
            reason = REASON_SCOPE_MISSING
        elif status == 429:
            reason = REASON_RATE_LIMITED
        elif status >= 500:
            reason = REASON_PROVIDER_UNAVAILABLE
        else:
            reason = REASON_READ_FAILED
        return ImportSourceError(reason, f"People API answered {status} {reason_code or status_name}".strip(), retry_after=retry_after(response.headers), partial=partial)

    @staticmethod
    def person(record: dict[str, Any]) -> ImportedPerson:
        names = [n for n in record.get("names") or [] if isinstance(n, dict)]
        primary = next((n for n in names if (n.get("metadata") or {}).get("primary")), names[0] if names else {})
        organizations = [o for o in record.get("organizations") or [] if isinstance(o, dict)]
        organization = str((organizations[0] if organizations else {}).get("name") or "")
        entries: list[ImportedEntry] = []
        for item in record.get("emailAddresses") or []:
            if isinstance(item, dict) and item.get("value"):
                entries.append(ImportedEntry(kind="email", value=str(item["value"]), label=str(item.get("formattedType") or item.get("type") or "")))
        for item in record.get("phoneNumbers") or []:
            if isinstance(item, dict) and (item.get("canonicalForm") or item.get("value")):
                entries.append(ImportedEntry(kind="phone", value=str(item.get("canonicalForm") or item.get("value")), label=str(item.get("formattedType") or item.get("type") or "")))
        for item in record.get("imClients") or []:
            if isinstance(item, dict) and item.get("username"):
                protocol = str(item.get("protocol") or "").strip()
                channel = _IM.get(protocol.casefold(), "other")
                entries.append(ImportedEntry(kind="messaging", value=str(item["username"]), label=str(item.get("formattedType") or item.get("type") or ""), channel=channel,
                                             provider_account="" if channel != "other" else str(item.get("formattedProtocol") or protocol or "Google")))
        return ImportedPerson(source_ref=str(record.get("resourceName") or ""), display_name=str(primary.get("displayName") or organization or ""),
                              entries=tuple(entries), organization=organization)

    def read(self, account: SourceAccount, *, limit: int) -> tuple[ImportedPerson, ...]:
        from core.kas.contract import KasRequest

        if not account.auth_binding:
            raise ImportSourceError(REASON_BINDING_REQUIRED)
        people: list[ImportedPerson] = []
        token = ""
        for _page in range(MAX_PAGES):
            query = {"personFields": PERSON_FIELDS, "pageSize": str(PAGE_SIZE)}
            if token:
                query["pageToken"] = token
            request = KasRequest(method="GET", url=f"{CONNECTIONS_URL}?{urllib.parse.urlencode(query)}", purpose="contacts.import.google",
                                 auth=account.auth_binding, timeout=20.0)
            response = self._send(request)
            if not response.ok:
                raise self._error(response, tuple(people))
            try:
                payload = response.json() or {}
            except Exception:
                raise ImportSourceError(REASON_READ_FAILED, "People API answer was not JSON", partial=tuple(people)) from None
            for record in payload.get("connections") or []:
                if isinstance(record, dict):
                    people.append(self.person(record))
            token = str(payload.get("nextPageToken") or "")
            if not token or len(people) >= limit:
                break
        return tuple(people[:limit])


__all__ = ["GoogleContactsAdapter", "retry_after", "transport_error_reason"]
