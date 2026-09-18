"""What a request says about the Contacts owner's objects, for lanes that answer before the model does.

A front-door lane that stores or answers deterministically must not claim a request whose object is a saved contact: that is
a Contacts write, made by the model with the contacts.* tools and bounded by request provenance. This module is the one place
that shape is read, so such a lane asks here instead of keeping its own list of words.
"""
from __future__ import annotations

import re

#: "save Alex Chen as a contact", "store Zoë in my address book", "keep this in my contacts", "save contact Priya Nair".
#: A statement that merely mentions a contact ("keep in mind my contact at the bank is Priya") names no Contacts object.
#: The verb is required as a WHOLE WORD governing the object phrase, because the request form is the law: a
#: hypothetical design review says "risks introduced by adding wallet addresses to contacts" -- the gerund "adding"
#: and the bare preposition "to contacts" are prose ABOUT contacts, not a save/store demand on them, and claiming
#: such a turn seated the contacts lane (and suppressed its independent live-data clause) without any storage verb.
_CONTACT_OBJECT_RE = re.compile(
    r"\b(?:save|store|keep|retain|add|put|record|register)\b[^.?!]{0,80}?\bas\s+(?:(?:a|an|my|new|another|the)\s+)*contacts?\b"
    r"|\b(?:save|store|keep|retain|add|put|record|register)\b[^.?!]{0,80}?\b(?:to|in|into|on|onto)\s+(?:(?:my|the|our)\s+)?(?:contacts|contact\s+list|address\s+book)\b"
    r"|^(?:please\s+)?(?:save|store|keep|retain|create|add)\s+(?:(?:a|an|the|new|this)\s+)*contact\b"
    r"(?![-\s]+(?:form|page|support|us)\b)",
    re.IGNORECASE,
)


def storage_object_is_a_contact(text: str) -> bool:
    """Whether a save/store/keep request puts its object into Contacts, rather than stating something about a contact."""
    return bool(_CONTACT_OBJECT_RE.search(" ".join(str(text or "").split())))


__all__ = ["storage_object_is_a_contact"]
