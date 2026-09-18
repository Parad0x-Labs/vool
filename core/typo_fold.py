"""One closed typo budget for every recognizer that keys on a fixed word: the demand grain's
head list, the machine recognizers' vocabularies.

`near_miss` is the budget: an adjacent transposition, or a single inserted/deleted character.
Pure SUBSTITUTION is excluded, and that exclusion is load-bearing rather than fussy: it is the
edit that turns ordinary words into heads ("came" is one substitution from "name"), and admitting
it would split clauses the suite freezes as indivisible. Transpositions and dropped letters are
what mistyping actually produces. Measured on the operator's own text: "waht is the wether in
berling now" (2026-09-05) and "what is the alrgest single file on my machine?" (2026-09-07) --
the first minted no demand, the second matched no machine recognizer and fell to a model lane.

`fold_near_miss_tokens` spends the budget on a recognizer's OWN vocabulary before its patterns
run: a token that is exactly one vocabulary word's near miss becomes that word; an ambiguous or
exact token is left alone. Nothing here consults a family or a topic list.
"""
from __future__ import annotations

import re
from collections.abc import Iterable

_TOKEN_RE = re.compile(r"[A-Za-z]+")


def near_miss(token: str, head: str) -> bool:
    """One typo away: an adjacent transposition, or a single inserted/deleted character."""
    if abs(len(token) - len(head)) > 1:
        return False
    if len(token) == len(head):
        diff = [
            index
            for index, (left, right) in enumerate(zip(token, head, strict=True))
            if left != right
        ]
        if len(diff) != 2 or diff[1] != diff[0] + 1:
            return False
        first, second = diff
        return token[first] == head[second] and token[second] == head[first]
    shorter, longer = (token, head) if len(token) < len(head) else (head, token)
    return any(
        longer[:index] + longer[index + 1 :] == shorter for index in range(len(longer))
    )


def fold_near_miss_tokens(lowered: str, vocabulary: Iterable[str], *, min_len: int = 4) -> str:
    """`lowered` with each token that is exactly ONE vocabulary word's near miss replaced by that
    word. Tokens shorter than `min_len`, tokens already in the vocabulary, and tokens near several
    vocabulary words are left as written."""
    words = tuple(w for w in {str(v).strip().lower() for v in vocabulary} if len(w) >= min_len)
    if not words:
        return lowered

    def _fold(match: re.Match[str]) -> str:
        token = match.group(0)
        lowered = token.lower()
        if len(lowered) < min_len or lowered in words:
            return token
        hits = [word for word in words if near_miss(lowered, word)]
        if len(hits) != 1:
            return token
        # The token's own capitalisation survives the fold ("Wether" -> "Weather"): a place name's
        # casing downstream is read as evidence, and a half-matched token ("Weather" read as "W" +
        # "eather", measured 2026-09-07) must never be produced -- hence the case-aware tokenizer.
        word = hits[0]
        if token.isupper():
            return word.upper()
        if token[:1].isupper():
            return word[:1].upper() + word[1:]
        return word

    return _TOKEN_RE.sub(_fold, lowered)


__all__ = ["fold_near_miss_tokens", "near_miss"]
