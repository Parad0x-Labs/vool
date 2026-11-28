"""Where what the person typed is cut into sentences: one rule for every memory reader."""
from __future__ import annotations

import re


class SentenceSplitter:
    """Sentences of what the person typed, cut at ". ", "! ", "? ", "; " and line breaks, but never at the dot of a
    word of one or two characters or a short capitalised word: that is a title, an abbreviation, an initial or a day
    number ("Dr. Albrecht", "Hr. Brandt", "ul. Długa", "J. Smith", "am 5. Mai"), in any language that writes them so.

    Measured on 02ff11496 (fresh audit, German): "Mein Zahnarzt heißt Dr. Albrecht und die Praxis ist in der
    Lindenstraße" was saved cut at "Dr.", and the recall answered "Albrecht und die Praxis ...". A sentence that ends
    in a short capitalised word ("... ist Tom. Er ...") stays joined to the next one: one fact kept whole is read back
    right, one cut in two is not.

    The keep step reads a message by the same rule (`core.memory.everyday_remember`, cut at ". ", "! " and "? "
    only). Mac gate 65 on b259d3e55: "my dentist is Dr. Okafor, her clinic is on Linden Street next to the bakery"
    was kept in passing as "my dentist is Dr." because that step still cut at every dot, and each later recall
    answered "my dentist is Dr."."""

    def __init__(self, marks: str = ".!?;") -> None:
        self._CUT_RE = re.compile(rf"(?<=[{re.escape(marks)}])\s+|\n+")

    _LAST_WORD_RE = re.compile(r"(\w+)\.$")

    def _before_an_abbreviation_dot(self, head: str) -> bool:
        """A word of one or two characters ("J.", "Dr.", "ul.", "5." as in "5. Mai"), or of three or four letters
        starting with a capital ("Hrn.", "Prof."), right before the dot."""
        match = self._LAST_WORD_RE.search(head)
        if not match:
            return False
        word = match.group(1)
        return len(word) <= 2 or (len(word) <= 4 and word.isalpha() and word[0].isupper())

    def split(self, text: str) -> list[str]:
        raw = str(text or "")
        parts: list[str] = []
        start = 0
        for cut in self._CUT_RE.finditer(raw):
            head = raw[start:cut.start()]
            if "\n" not in cut.group(0) and head.endswith(".") and self._before_an_abbreviation_dot(head):
                continue
            parts.append(head)
            start = cut.end()
        parts.append(raw[start:])
        return parts
