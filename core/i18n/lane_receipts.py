"""The memory lane's own replies in the language the person wrote in.

The memory lane answers keeps, recalls and forgets itself, in fixed English words ("Locked in. I'll remember that.",
"You told me: ...", "Forget applied. Removed 2 memory entries."). Measured on 02ff11496 (fresh audit, local
qwen3:8b): Lithuanian and German keeps, forgets and recalls were all answered with those English lines, and a German
recall came back under "You told me:". The words of the person's message decide: a message with exclusive evidence of
a language, or whose words VOOL's catalogs place in one (core.i18n.message_language), gets that language's catalog
words; otherwise the app language of the turn (core.i18n.turn_locale). The saved facts themselves stay in the words
they were told in.
"""
from __future__ import annotations

import re

#: The lane's fixed English replies and the catalog key of each.
_EXACT_KEYS = {
    "Locked in. I’ll remember that.": "memory.kept",
    "I already had that in memory.": "memory.already_kept",
    "I have no record of that; it was forgotten.": "memory.recall_forgotten",
    "I don't have that saved. If you tell me, I'll remember it.": "memory.recall_nothing",
    "Okay, I won't keep that.": "memory.keep_not_wanted",
    "That was already forgotten.": "memory.already_forgotten",
}
_FORGET_APPLIED_RE = re.compile(r"^Forget applied\. Removed (?P<count>\d+) memory entr(?:y|ies)\.$")
_TOLD_HEAD = "You told me:"


def reply_locale(user_text: str) -> str:
    """The language a VOOL-composed memory reply to ``user_text`` is given in: the message's own language when its
    words tell it (exclusive letters first, then VOOL's catalogs, core.i18n.message_language), else the app language
    of the turn."""
    from core.i18n.catalog import catalog_for
    from core.i18n.message_language import message_language
    from core.i18n.turn_locale import turn_locale
    from core.response_language_policy import evidenced_input_language

    text = str(user_text or "")
    try:
        said = evidenced_input_language(text) or message_language(text)
    except Exception:
        said = ""
    if said == "en":
        return "en"
    if said and "memory.kept" in catalog_for(said).locale_keys:
        return said
    return turn_locale() or "en"


def in_the_persons_language(reply: str, user_text: str) -> str:
    """``reply`` with the memory lane's fixed English words in the person's language; any other text unchanged."""
    text = str(reply or "")
    locale = reply_locale(user_text)
    if not text or locale == "en":
        return text
    from core.i18n.catalog import catalog_for

    catalog = catalog_for(locale)

    def said(key: str, english: str, **fields: str) -> str:
        if key not in catalog.locale_keys:
            return english
        return catalog.format(key, **fields) if fields else catalog.text(key)

    key = _EXACT_KEYS.get(text.strip())
    if key:
        return said(key, text)
    match = _FORGET_APPLIED_RE.match(text.strip())
    if match:
        return said("memory.forget_applied", text, count=match.group("count"))
    if text.startswith(_TOLD_HEAD):
        return said("memory.you_told_me", _TOLD_HEAD) + text[len(_TOLD_HEAD):]
    return text


def every_kept_receipt() -> set[str]:
    """The keep receipts in every shipped language (a receipt is never a fact to forget or keep)."""
    from pathlib import Path

    from core.i18n.catalog import catalog_for

    texts = {english for english, key in _EXACT_KEYS.items() if key in {"memory.kept", "memory.already_kept"}}
    for path in (Path(__file__).resolve().parent.parent / "i18n" / "catalogs").glob("*.json"):
        catalog = catalog_for(path.stem)
        texts.update(catalog.text(key) for key in ("memory.kept", "memory.already_kept") if key in catalog.locale_keys)
    return texts


__all__ = ["every_kept_receipt", "in_the_persons_language", "reply_locale"]
