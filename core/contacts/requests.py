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


#: A phone number (seven digits or more, spaced or dashed) or an email address: the detail that makes a saved name a contact.
_CONTACT_DETAIL_RE = re.compile(r"(?<![\w.])\+?\d(?:[\s().-]?\d){6,}(?!\w)|\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
#: "save nadia, shes my yoga teacher, 086 555 0166": a save whose object is a NAME followed by who they are and how to
#: reach them (audit N6 K08, answered "Locked in" by the memory lane). The object must be a name, not "this"/"that"/"my
#: ...", and the request must carry a phone number or email, so "save this: call 086 555 0166" stays a note.
_SAVE_NAMED_PERSON_RE = re.compile(
    r"^\W*(?:(?:please|pls|can\s+you|could\s+you)\s+)*(?:save|add|store)\s+"
    r"(?!(?:this|that|it|these|those|the|a|an|my|our|me|us|them|him|her|some|all|money|time|space)\b)"
    r"[A-Za-z][\w'-]*(?:\s+[A-Za-z][\w'-]*)?\s*(?:,|\s+-\s+|\s+(?:who|she|he|they)(?:'s|s)?\b)",
    re.IGNORECASE,
)
#: "nadias got a new number now", "his number changed", "Petra has a new email": a saved person's detail changed
#: (audit N6 K08/K09, answered by the model with no Contacts tool). The person is named or a third-person pronoun;
#: the owner's own number ("I've got a new number", "my number changed") is about the owner, not a contact.
_DETAIL_NOUN = r"(?:(?:phone|mobile|cell|work|home)\s+)?(?:number|phone|mobile|email(?:\s+address)?|e-mail|address)"
_NOT_A_CONTACT_SUBJECT = r"(?<!\bthe )(?<!\ba )(?<!\ban )(?!(?:i|i've|ive|we|we've|you|you've|my|our|your|it|its|this|that|the|there)\b)"
_CONTACT_DETAIL_CHANGE_RE = re.compile(
    r"\b" + _NOT_A_CONTACT_SUBJECT + r"[A-Za-z][\w-]*(?:'s|\u2019s|s)?\s+(?:has\s+)?(?:got(?:ten)?|has|have|gets?)\s+(?:a\s+)?new\s+" + _DETAIL_NOUN + r"\b"
    r"|\b(?:his|her|their|" + _NOT_A_CONTACT_SUBJECT + r"[A-Za-z][\w-]*(?:'s|\u2019s))\s+(?:new\s+)?" + _DETAIL_NOUN
    + r"\s+(?:has\s+|have\s+|just\s+)*(?:changed|is\s+different)\b"
    r"|\bnew\s+" + _DETAIL_NOUN + r"\s+for\s+" + _NOT_A_CONTACT_SUBJECT + r"[A-Za-z][\w-]*\b",
    re.IGNORECASE,
)


def storage_object_is_a_contact(text: str) -> bool:
    """Whether a save/store/keep request puts its object into Contacts, rather than stating something about a contact."""
    words = " ".join(str(text or "").split())
    if _CONTACT_OBJECT_RE.search(words):
        return True
    return bool(_SAVE_NAMED_PERSON_RE.search(words) and _CONTACT_DETAIL_RE.search(words))


def contact_detail_changed(text: str) -> bool:
    """Whether the text tells VOOL a saved person's phone number, email or address changed: a Contacts update to propose."""
    return bool(_CONTACT_DETAIL_CHANGE_RE.search(" ".join(str(text or "").split())))


__all__ = ["contact_detail_changed", "storage_object_is_a_contact"]
