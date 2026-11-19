"""A reply that CITES what is already saved is not a claim that this turn saved something.

Measured on the real app (2026-11-19, the cloud route answered by a second cloud model than the usual one, on the
start commit and on the fix branch alike): asked "who is my physiotherapist?" with the fact saved, the model answered "Your
physiotherapist is Ms Adeyinka, according to what you've saved in my memory." and the delivered reply was "I did
not write anything to memory -- no preference, rule or setting was stored on this turn ...". Five of nineteen plain
recall questions of a sealed set ended that way. The checks that hold a reply to what the turn really wrote read
"saved in my memory" as a finished write; it names where the answer came from.

`without_citations` removes the citing words from a sentence, so a check tests what is left: "I've saved your
preference to memory." keeps its claim, "... based on what I have saved in memory." has none. A sentence that says
the save happened just now is never read as a citation.
"""

from __future__ import annotations

import re

_A = "['’]"
_SAVED = r"(?:saved|stored|recorded|noted|remembered|kept|written|logged|filed|memori[sz]ed)"
#: "... what I saved just now" reports this turn's write, whatever its grammar.
_JUST_NOW_RE = re.compile(r"\b(?:just|right\s+now|this\s+turn|a\s+moment\s+ago|moments?\s+ago)\b", re.IGNORECASE)
#: Where the cited thing is said to be kept; it goes with the citation ("what is saved in memory").
_PLACE = r"(?:\s+(?:in|to|into|on)\s+(?:my\s+|your\s+|our\s+|the\s+)?(?:memory|memories|notes?|records?|file))?"
_CITATION_RE = re.compile(
    r"(?:"
    # the saved thing as the object of a relative clause: "what you've saved in my memory", "everything I have stored"
    r"\b(?:what|whatever|everything|anything|which)\s+(?:you|i|we)(?:" + _A + r"(?:ve|d)|\s+have|\s+had)?\s+"
    r"(?:(?:already|previously|earlier|got)\s+)*" + _SAVED + r"\b"
    # ... or as its subject: "what is saved in memory", "what's stored"
    r"|\b(?:what|whatever|which|everything|anything)(?:\s+(?:is|was|has\s+been)|" + _A + r"s)\s+"
    r"(?:(?:already|currently)\s+)?" + _SAVED + r"\b"
    # a state of having it: "I have that saved", "I've got it stored"
    r"|\b(?:i|we)(?:\s+have|" + _A + r"ve)(?:\s+got)?\s+(?:that|this|it|them|those|these)\s+" + _SAVED + r"\b"
    # a state that lasts, which no write of this turn can be: "still stored in my memory", "already saved"
    r"|\b(?:still|already|currently)\s+" + _SAVED + r"\b"
    r")" + _PLACE
    # the saved thing named as a source: "my saved memory", "your stored notes"
    + r"|\b(?:my|your|our|the)\s+" + _SAVED + r"\s+(?:memory|memories|notes?|facts?|records?|details?|information)\b",
    re.IGNORECASE,
)


#: "... it's saved in my memory from an earlier chat": the sentence itself dates the saving before this turn.
_EARLIER_RE = re.compile(
    r"\b(?:from|in|during|since)\s+(?:an?|our|your|the|that|some)\s+(?:earlier|previous|past|prior|other|last|older)\s+"
    r"(?:chat|chats|conversation|conversations|session|sessions|message|messages|time)\b"
    r"|\b(?:earlier|previously|before\s+now|a\s+while\s+ago|last\s+time)\b",
    re.IGNORECASE,
)
_SAVED_WORD_RE = re.compile(r"\b" + _SAVED + r"\b", re.IGNORECASE)


def without_citations(sentence: str) -> str:
    """The sentence with every citation of already-saved things removed; unchanged when it says "just" now."""
    text = str(sentence or "")
    if _JUST_NOW_RE.search(text):
        return text
    text = _CITATION_RE.sub(" ", text)
    if _EARLIER_RE.search(text):
        text = _SAVED_WORD_RE.sub(" ", text)  # the whole sentence is about an earlier saving
    return text
