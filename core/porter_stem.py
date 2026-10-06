"""Porter stemming (1980), faithful to the reference C implementation
(``porter1.c``) that SQLite's FTS5 ``tokenize='porter unicode61'`` translates —
including its two departures (``bli``->``ble``, ``logi``->``log``) and its
length<=2 early exit, and WITHOUT the dying/lying/tying special case.

The scoped BM25 leg of ``VoolMemory`` ranks Python-tokenized text with exact
token equality; the FTS5 index it mirrors stems both sides with this algorithm,
so the two lexical legs diverged (measured 2026-09-27: the query terms
``guests``/``park`` scored nothing against document tokens ``guest``/
``parking`` and a stored fact became unreachable by hybrid search). Tokenizing
with this same stemmer restores a single stemming authority for both legs.
Equivalence with FTS5 is pinned by tests/test_porter_stem_equivalence.py.
"""
from __future__ import annotations

from functools import lru_cache

_VOWELS = frozenset("aeiou")


def _is_consonant(word: str, i: int) -> bool:
    ch = word[i]
    if ch in _VOWELS:
        return False
    if ch == "y":
        return True if i == 0 else not _is_consonant(word, i - 1)
    return True


def _measure(word: str, j: int) -> int:
    """Number of VC sequences in word[0..j] (the Porter measure m)."""
    n = 0
    i = 0
    length = len(word)
    while True:
        if i > j or i >= length:
            return n
        if not _is_consonant(word, i):
            break
        i += 1
    i += 1
    while True:
        while True:
            if i > j or i >= length:
                return n
            if _is_consonant(word, i):
                break
            i += 1
        i += 1
        n += 1
        while True:
            if i > j or i >= length:
                return n
            if not _is_consonant(word, i):
                break
            i += 1
        i += 1


def _ends(word: str, suffix: str, k: int) -> int:
    """If word[0..k] ends with *suffix*, return the last index of the stem
    (k - len(suffix)); otherwise -1."""
    length = len(suffix)
    if length > k + 1:
        return -1
    if word[k - length + 1 : k + 1] != suffix:
        return -1
    return k - length


def _has_vowel_in_stem(word: str, j: int) -> bool:
    return any(not _is_consonant(word, i) for i in range(min(j, len(word) - 1) + 1))


def _is_double_consonant(word: str, j: int) -> bool:
    if j < 1:
        return False
    return word[j] == word[j - 1] and _is_consonant(word, j)


def _cvc(word: str, i: int) -> bool:
    if i < 2 or not _is_consonant(word, i) or _is_consonant(word, i - 1) or not _is_consonant(word, i - 2):
        return False
    return word[i] not in "wxy"


def _step1ab(b: str) -> str:
    k = len(b) - 1
    if b[k] == "s":
        j = _ends(b, "sses", k)
        if j >= 0:
            b = b[: k - 1]
            k -= 2
        else:
            j = _ends(b, "ies", k)
            if j >= 0:
                b = b[: j + 1] + "i"
                k = j + 1
            elif b[k - 1] != "s":
                b = b[:k]
                k -= 1
    j = _ends(b, "eed", k)
    if j >= 0:
        if _measure(b, j) > 0:
            b = b[:k]
            k -= 1
    else:
        j = _ends(b, "ed", k)
        ended_ed = j >= 0
        if not ended_ed:
            j = _ends(b, "ing", k)
        if j >= 0 and _has_vowel_in_stem(b, j):
            k = j
            b = b[: k + 1]
            j2 = _ends(b, "at", k)
            if j2 >= 0:
                b = b[: j2 + 1] + "ate"
                k = j2 + 3
            else:
                j2 = _ends(b, "bl", k)
                if j2 >= 0:
                    b = b[: j2 + 1] + "ble"
                    k = j2 + 3
                else:
                    j2 = _ends(b, "iz", k)
                    if j2 >= 0:
                        b = b[: j2 + 1] + "ize"
                        k = j2 + 3
                    elif _is_double_consonant(b, k):
                        if b[k] not in "lsz":
                            b = b[:k]
                            k -= 1
                        # a doubled l/s/z keeps both letters (porter re-adds
                        # the removed duplicate for exactly these three)
                    elif _measure(b, k) == 1 and _cvc(b, k):
                        b = b[: k + 1] + "e"
                        k += 1
    return b


def _step1c(b: str) -> str:
    k = len(b) - 1
    j = _ends(b, "y", k)
    if j >= 0 and _has_vowel_in_stem(b, j):
        b = b[:k] + "i"
    return b


_STEP2_RULES = (
    ("ational", "ate"), ("tional", "tion"), ("enci", "ence"), ("anci", "ance"),
    ("izer", "ize"), ("bli", "ble"), ("alli", "al"), ("entli", "ent"),
    ("eli", "e"), ("ousli", "ous"), ("ization", "ize"), ("ation", "ate"),
    ("ator", "ate"), ("alism", "al"), ("iveness", "ive"), ("fulness", "ful"),
    ("ousness", "ous"), ("aliti", "al"), ("iviti", "ive"), ("biliti", "ble"),
    ("logi", "log"),
)
_STEP3_RULES = (
    ("icate", "ic"), ("ative", ""), ("alize", "al"), ("iciti", "ic"),
    ("ical", "ic"), ("ful", ""), ("ness", ""),
)
_STEP4_SUFFIXES = (
    "al", "ance", "ence", "er", "ic", "able", "ible", "ant", "ement", "ment",
    "ent", "ion", "ou", "ism", "ate", "iti", "ous", "ive", "ize",
)


def _apply_rules(b: str, rules, *, min_measure: int = 1) -> str:
    """Match the FIRST rule whose suffix ends the word (porter's switch selects
    one candidate per word, ordered), replace it, and keep it only when the
    stem's Porter measure reaches *min_measure* (r() = setto if m > 0)."""
    k = len(b) - 1
    for suffix, repl in rules:
        j = _ends(b, suffix, k)
        if j >= 0:
            if _measure(b, j) >= min_measure:
                return b[: j + 1] + repl
            return b
    return b


def _step4(b: str) -> str:
    k = len(b) - 1
    for suffix in _STEP4_SUFFIXES:
        j = _ends(b, suffix, k)
        if j >= 0:
            if suffix == "ion" and not (j >= 0 and b[j] in "st"):
                return b
            if _measure(b, j) > 1:
                return b[: j + 1]
            return b
    return b


def _step5(b: str) -> str:
    k = len(b) - 1
    if b[k] == "e":
        a = _measure(b, k)
        if a > 1 or (a == 1 and not _cvc(b, k - 1)):
            b = b[:k]
            k -= 1
    if k >= 1 and b[k] == "l" and _is_double_consonant(b, k) and _measure(b, k) > 1:
        b = b[:k]
    return b


@lru_cache(maxsize=65536)
def stem(word: str) -> str:
    """Porter-stem one lowercase alphabetic token (porter1.c behavior).

    Tokens containing digits/underscores or non-ASCII characters are returned
    unchanged: FTS5 unicode61 would split them differently anyway, and the
    scoped leg only needs a consistent single stemming authority."""
    text = str(word or "")
    if len(text) <= 2 or not text.isascii() or not text.isalpha():
        return text
    b = _step1ab(text)
    b = _step1c(b)
    b = _apply_rules(b, _STEP2_RULES)
    b = _apply_rules(b, _STEP3_RULES)
    b = _step4(b)
    b = _step5(b)
    return b


__all__ = ["stem"]


def recall_words(text: str) -> list[str]:
    """Exact words plus explicit internal case boundaries (iPhone -> phone).

    Lowercase substrings are never split: application does not imply cat.
    Retaining originals preserves exact identifiers and existing stemming.
    """
    import re
    words = re.findall(r"[A-Za-z0-9_]{2,}", str(text or ""))
    out = [word.lower() for word in words]
    for word in words:
        parts = re.sub(r"([a-z])([A-Z])", r"\1 \2", word).split()
        if len(parts) > 1:
            out.extend(part.lower() for part in parts if len(part) > 1)
    return out
