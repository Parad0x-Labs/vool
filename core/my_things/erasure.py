"""Read-only saved-record scope notice proposed for the memory owner."""
from __future__ import annotations

import unicodedata


def memory_scope_note(target: str) -> str:
    from core.contacts.store import active_contacts
    from core.my_things.store import ThingsStore

    needle = unicodedata.normalize("NFKC", target.strip()).casefold()
    if len(needle) < 2:
        return ""

    def carries(value):
        if isinstance(value, str):
            return needle in unicodedata.normalize("NFKC", value).casefold()
        if isinstance(value, (list, tuple)):
            return any(carries(v) for v in value)
        return False

    # Exact saved text, never fuzzy names, tombstones or file re-reading.
    contacts = sum(any(carries(c.get(k)) for k in ("display_name", "aliases", "notes"))
                   or any(carries(e.get(k)) for e in c["endpoints"] for k in ("value", "canonical", "label"))
                   for c in active_contacts())
    things = len(ThingsStore().matching(target))
    matched = []
    if things:
        matched.append(f"{things} saved thing{'s' if things != 1 else ''} in My things")
    if contacts:
        matched.append(f"{contacts} saved contact{'s' if contacts != 1 else ''} in Contacts")
    if not matched:
        return ""
    return "The target also matches " + " and ".join(matched) + "; review and confirm deletion on those pages to remove these records and their files."
