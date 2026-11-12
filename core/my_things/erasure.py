"""What chat Forget says about saved household records and contacts.

Saved things and contacts are records the person keeps by hand. Chat Forget does not delete them:
it says how many of them hold the same words and where to delete them, or that a store could not
be checked. A store that cannot be read never blocks forgetting memory. The sentence never repeats
the forgotten words. Matching is ThingsStore's record_text_carries, the memory forget matcher.
"""
from __future__ import annotations

import logging

LOGGER = logging.getLogger(__name__)

UNCHECKED_NOTE = "Forget could not check your saved things in My things or your saved contacts in Contacts for these words."


def _count(word: str, n: int) -> str:
    return f"{n} saved {word}{'s' if n != 1 else ''}"


def memory_scope_note(target: str) -> str:
    from core.my_things.store import record_text_carries

    needle = str(target or "").strip()
    if len(needle) < 2:
        return ""

    def carries(value):
        if isinstance(value, str):
            return record_text_carries(value, needle)
        if isinstance(value, (list, tuple)):
            return any(carries(v) for v in value)
        return False

    found, unchecked = [], []
    try:
        from core.my_things.store import ThingsStore

        things = len(ThingsStore().matching(needle))
        if things:
            found.append(_count("thing", things) + " in My things")
    except Exception as exc:
        LOGGER.warning("saved things could not be checked: %s", type(exc).__name__)
        unchecked.append("your saved things in My things")
    try:
        from core.contacts.store import active_contacts

        # Exact saved text, never fuzzy names, tombstones or file re-reading.
        contacts = sum(any(carries(c.get(k)) for k in ("display_name", "aliases", "notes"))
                       or any(carries(e.get(k)) for e in c["endpoints"] for k in ("value", "canonical", "label"))
                       for c in active_contacts())
        if contacts:
            found.append(_count("contact", contacts) + " in Contacts")
    except Exception as exc:
        LOGGER.warning("saved contacts could not be checked: %s", type(exc).__name__)
        unchecked.append("your saved contacts in Contacts")
    sentences = []
    if found:
        many = len(found) > 1 or not found[0].startswith("1 ")
        sentences.append("The same words are in " + " and ".join(found) + ". Forget keeps saved records; "
                         + ("delete them there to remove them and their files." if many
                            else "delete it there to remove it and its files."))
    if unchecked:
        sentences.append("Forget could not check " + " or ".join(unchecked) + " for these words.")
    return " ".join(sentences)
