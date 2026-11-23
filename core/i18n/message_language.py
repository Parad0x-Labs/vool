"""Which of VOOL's app languages a person's message is written in, read from VOOL's own catalogs.

VOOL-composed replies (the memory lane's receipts, its recall header) must come back in the language the person wrote
in. The exclusive-evidence reading (core.response_language_policy.evidenced_input_language) is strict on purpose:
"kokia mano mašina?" carries one Lithuanian letter and "Merk dir bitte, dass mein Fahrrad grün ist" none of the
letters it counts, so both read as no language and were answered in English (fresh audit of 02ff11496).

Each shipped catalog is a few thousand sentences of everyday words in its language. A message's words are counted
against them: a word that only one language's catalog uses (not English's) is a vote for that language. No word lists
are kept here; a new catalog is a new language this reads. Scripts that only one app language writes (kana, hangul,
Hebrew, Arabic, Devanagari) decide on their own; Han without kana is Chinese.
"""
from __future__ import annotations

import functools
import json
import re
from pathlib import Path

_CATALOGS = Path(__file__).resolve().parent / "catalogs"
_WORD_RE = re.compile(r"[^\W\d_]{2,}", re.UNICODE)
_PLACEHOLDER_RE = re.compile(r"\{[^{}]*\}")
_SCRIPTS = (
    ("ja", re.compile(r"[぀-ヿ]")),
    ("ko", re.compile(r"[가-힯ᄀ-ᇿ]")),
    ("he", re.compile(r"[֐-׿]")),
    ("ar", re.compile(r"[؀-ۿݐ-ݿ]")),
    ("hi", re.compile(r"[ऀ-ॿ]")),
    ("zh-Hans", re.compile(r"[㐀-鿿豈-﫿]")),
)
#: Votes a language needs, and its lead over the next one, before a message is read as written in it.
_MIN_VOTES = 2


@functools.lru_cache(maxsize=1)
def _vocabularies() -> dict[str, frozenset[str]]:
    vocab: dict[str, frozenset[str]] = {}
    for path in sorted(_CATALOGS.glob("*.json")):
        try:
            messages = json.loads(path.read_text(encoding="utf-8")).get("messages") or {}
        except (OSError, ValueError):
            continue
        words: set[str] = set()
        for value in messages.values():
            if isinstance(value, str):
                words.update(word.casefold() for word in _WORD_RE.findall(_PLACEHOLDER_RE.sub(" ", value)))
        vocab[path.stem] = frozenset(words)
    return vocab


def message_language(text: str) -> str:
    """The app language ``text`` is written in ("en", "lt", "de", ...), or "" when its words do not tell."""
    clean = str(text or "")
    for locale, script in _SCRIPTS:
        if script.search(clean):
            return locale
    words = [word.casefold() for word in _WORD_RE.findall(clean)]
    if not words:
        return ""
    vocab = _vocabularies()
    english = vocab.get("en", frozenset())
    votes: dict[str, int] = {}
    for locale, known in vocab.items():
        if locale == "en":
            votes[locale] = sum(1 for word in words if word in known)
        else:
            votes[locale] = sum(1 for word in words if word in known and word not in english)
    ranked = sorted(votes.items(), key=lambda item: item[1], reverse=True)
    best, count = ranked[0]
    runner = ranked[1][1] if len(ranked) > 1 else 0
    if best == "en":
        # English words are in every catalog's English loans and brand names too: English needs a clear lead over
        # the best other language's own words.
        other = next((n for loc, n in ranked if loc != "en"), 0)
        return "en" if count >= _MIN_VOTES and count > 2 * other else ""
    return best if count >= _MIN_VOTES and count > runner else ""


__all__ = ["message_language"]
