"""Apple Contacts on this Mac, read through the macOS Contacts framework (CNContactStore via PyObjC).

Facts this adapter is built on (Apple documentation, checked 2026-09-15):

* CNAuthorizationStatus on macOS is notDetermined (0), restricted (1), denied (2) or authorized (3). ``limited`` is an
  iOS 18 value with no macOS entry, and is treated as not authorized here.
* A process whose app bundle lacks NSContactsUsageDescription is denied without a prompt; TCC attributes the request to
  the responsible app (for a daemon started from Terminal, Terminal). The packaged VOOL app must carry the usage string.
* Only the keys fetched are readable. The note key needs a separate entitlement and is never fetched.

The framework binding is injected (``binding``) so the whole read path runs against a fixture store in tests; without an
injected binding the adapter imports ``Contacts`` from pyobjc-framework-Contacts and reports ``os_binding_unavailable``
when that module is absent. Access is requested only inside an owner-started import, never at startup.
"""
from __future__ import annotations

import threading
from typing import Any

from core.contacts.importers.base import (
    REASON_OS_BINDING_UNAVAILABLE,
    REASON_PERMISSION_DENIED,
    REASON_PERMISSION_NOT_DETERMINED,
    REASON_PERMISSION_RESTRICTED,
    REASON_READ_FAILED,
    RECOVERY,
    ImportedEntry,
    ImportedPerson,
    ImportSourceError,
    SourceAccount,
)

STATUS_NOT_DETERMINED = 0
STATUS_RESTRICTED = 1
STATUS_DENIED = 2
STATUS_AUTHORIZED = 3
_ENTITY_CONTACTS = 0  # CNEntityTypeContacts
_ACCESS_WAIT_SECONDS = 120.0

_IM_SERVICES = {"telegram": "telegram", "signal": "signal", "whatsapp": "whatsapp", "slack": "slack", "discord": "discord", "matrix": "matrix"}


def _load_binding() -> Any:
    try:
        import Contacts  # type: ignore[import-not-found]
    except Exception:
        return None
    return Contacts


class AppleContactsAdapter:
    provider = "apple_contacts"
    label = "Apple Contacts on this Mac"
    needs_binding = False

    def __init__(self, binding: Any = None) -> None:
        self._binding = binding

    def _framework(self) -> Any:
        framework = self._binding if self._binding is not None else _load_binding()
        if framework is None:
            raise ImportSourceError(REASON_OS_BINDING_UNAVAILABLE, "the pyobjc Contacts module is not installed in this runtime")
        return framework

    def _status(self, framework: Any) -> int:
        return int(framework.CNContactStore.authorizationStatusForEntityType_(_ENTITY_CONTACTS))

    def authorization(self, account: SourceAccount) -> dict[str, Any]:
        try:
            framework = self._framework()
        except ImportSourceError as exc:
            return {"state": "unavailable", "reason": exc.reason, "recovery": exc.recovery}
        status = self._status(framework)
        state = {STATUS_AUTHORIZED: "authorized", STATUS_DENIED: "denied", STATUS_RESTRICTED: "restricted"}.get(status, "not_determined")
        reason = {"denied": REASON_PERMISSION_DENIED, "restricted": REASON_PERMISSION_RESTRICTED, "not_determined": REASON_PERMISSION_NOT_DETERMINED}.get(state)
        return {"state": state, "reason": reason or "", "recovery": RECOVERY.get(reason, "") if reason else ""}

    def _request_access(self, framework: Any, store: Any) -> bool:
        done = threading.Event()
        outcome: dict[str, Any] = {"granted": False, "error": None}

        def handler(granted: Any, error: Any) -> None:
            outcome["granted"] = bool(granted)
            outcome["error"] = error
            done.set()

        store.requestAccessForEntityType_completionHandler_(_ENTITY_CONTACTS, handler)
        done.wait(_ACCESS_WAIT_SECONDS)
        return bool(outcome["granted"])

    def read(self, account: SourceAccount, *, limit: int) -> tuple[ImportedPerson, ...]:
        framework = self._framework()
        status = self._status(framework)
        if status == STATUS_RESTRICTED:
            raise ImportSourceError(REASON_PERMISSION_RESTRICTED)
        if status == STATUS_DENIED:
            raise ImportSourceError(REASON_PERMISSION_DENIED)
        store = framework.CNContactStore.alloc().init()
        if status != STATUS_AUTHORIZED and not self._request_access(framework, store):
            # a declined prompt (or a bundle without the usage string, which macOS declines silently) reads as denied
            raise ImportSourceError(REASON_PERMISSION_DENIED if self._status(framework) == STATUS_DENIED else REASON_PERMISSION_NOT_DETERMINED)
        keys = [framework.CNContactIdentifierKey, framework.CNContactGivenNameKey, framework.CNContactFamilyNameKey, framework.CNContactOrganizationNameKey,
                framework.CNContactEmailAddressesKey, framework.CNContactPhoneNumbersKey, framework.CNContactInstantMessageAddressesKey]
        request = framework.CNContactFetchRequest.alloc().initWithKeysToFetch_(keys)
        people: list[ImportedPerson] = []

        def label_of(labeled: Any) -> str:
            raw = labeled.label()
            if not raw:
                return ""
            try:
                return str(framework.CNLabeledValue.localizedStringForLabel_(raw))
            except Exception:
                return str(raw)

        def visit(contact: Any, stop: Any) -> None:
            if len(people) >= limit:
                try:
                    stop[0] = True
                except Exception:
                    pass
                return
            entries: list[ImportedEntry] = []
            for labeled in contact.emailAddresses() or []:
                entries.append(ImportedEntry(kind="email", value=str(labeled.value()), label=label_of(labeled)))
            for labeled in contact.phoneNumbers() or []:
                entries.append(ImportedEntry(kind="phone", value=str(labeled.value().stringValue()), label=label_of(labeled)))
            for labeled in contact.instantMessageAddresses() or []:
                address = labeled.value()
                service = str(address.service() or "").strip()
                channel = _IM_SERVICES.get(service.casefold(), "other")
                entries.append(ImportedEntry(kind="messaging", value=str(address.username()), label=label_of(labeled), channel=channel,
                                             provider_account="" if channel != "other" else (service or "Apple Contacts")))
            name = " ".join(part for part in (str(contact.givenName() or ""), str(contact.familyName() or "")) if part).strip()
            organization = str(contact.organizationName() or "")
            people.append(ImportedPerson(source_ref=str(contact.identifier()), display_name=name or organization, entries=tuple(entries), organization=organization))

        ok, error = store.enumerateContactsWithFetchRequest_error_usingBlock_(request, None, visit)
        if not ok:
            raise ImportSourceError(REASON_READ_FAILED, str(error or "enumeration failed"), partial=tuple(people))
        return tuple(people)


__all__ = ["AppleContactsAdapter"]
