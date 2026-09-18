"""Contact import sources behind one seam (core.contacts.importers.base). Each adapter only reads its source."""
from __future__ import annotations

from typing import Any

from core.contacts.importers.base import ContactSourceAdapter, ImportSourceError, SourceAccount

PROVIDERS: dict[str, str] = {
    "apple_contacts": "Apple Contacts on this Mac",
    "google": "Google Contacts",
    "microsoft": "Microsoft Outlook contacts",
}


def adapter_for(provider: str, *, binding: Any = None, transport: Any = None) -> ContactSourceAdapter:
    """The adapter for one provider. ``binding`` (Apple framework) and ``transport`` (KAS) are injectable for fixtures."""
    if provider == "apple_contacts":
        from core.contacts.importers.apple import AppleContactsAdapter

        return AppleContactsAdapter(binding=binding)
    if provider == "google":
        from core.contacts.importers.google import GoogleContactsAdapter

        return GoogleContactsAdapter(transport=transport)
    if provider == "microsoft":
        from core.contacts.importers.microsoft import MicrosoftContactsAdapter

        return MicrosoftContactsAdapter(transport=transport)
    raise KeyError(provider)


__all__ = ["PROVIDERS", "ContactSourceAdapter", "ImportSourceError", "SourceAccount", "adapter_for"]
