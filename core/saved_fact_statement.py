"""A sentence that says a fact IS saved is judged by whether a saved fact holds it.

Measured on the real app (cloud route, the head-to-head's scenarios run on the fix branch): asked "so who is my
doctor?", the model answered "Your doctor is Dr Whitcombe — that's saved in your memory." and the reply delivered was
"I did not write anything to memory -- no preference, rule or setting was stored on this turn ..."; asked "where is my
shed?", it answered "It's the green one by the apple tree — that's how you've got it noted." and the gate cut that
sentence and put "Nothing from this message was saved to memory" under what was left. Both facts were saved.

The checks that hold a reply to its save claims read such a sentence as a report of a write made on this turn, and
this turn wrote nothing. `core.saved_fact_citation` set aside the wordings of a citation seen until then; a list of
wordings meets a new one sooner or later. What the sentence says is checkable: the fact it is about is in saved
memory, or it is not. When a saved row this chat may read holds it, the sentence is true whichever turn wrote the row,
and nothing false is claimed. When no row holds it, the sentence is judged exactly as before.

"Holds it" is read on words, with no model call. One saved row shares with the sentence two content words or a value
(a name, a number), holds EVERY value the sentence states, and leaves at most one content word in three of the
sentence unaccounted for (one at least: "doctor" for a row that says "gp"). A sentence with fewer than two content
words of its own ("That's saved in your memory.") is read with the sentence just before it, which it points back to.
"Your doctor is Dr Whitley — that's saved" is not held by a row about Dr Whitcombe, "I've saved your appointment with
Dr Whitcombe on the 14th" is not held by a row that has no 14th, and "it's the blue one by the plum tree — that's how
you've got it noted" is not held by a row about the green one by the apple tree.
"""

from __future__ import annotations

import re
from typing import Any

#: The words of saving itself: they are what the sentence claims, not what it is about.
_SAVING_WORDS_RE = re.compile(
    r"\b(?:sav(?:e|ed|es|ing)|stor(?:e|ed|es|ing)|record(?:ed|s|ing)?|not(?:e|ed|es|ing)|remember(?:ed|s|ing)?|kept|keep(?:s|ing)?"
    r"|logg?(?:ed|s|ing)?|fil(?:e|ed|es|ing)|memor(?:y|ies|i[sz]ed?)|notes?|records?)\b",
    re.IGNORECASE,
)
_WORD_RE = re.compile(r"[A-Za-z0-9]+(?:[:.][0-9]+)*")
_SENTENCE_START_RE = re.compile(r"(?:^|[.!?—:;\n]\s*)$")


def _stems(text: str) -> tuple[set[str], set[str]]:
    """(content stems, value stems) of a text: a value is a token with a digit or a word written with a capital
    letter where no sentence begins."""
    from core.chat_housekeeping import STOPWORDS
    from core.memory.entries import _match_stem

    body = _SAVING_WORDS_RE.sub(" ", str(text or "").replace("’", "'"))
    content: set[str] = set()
    values: set[str] = set()
    for match in _WORD_RE.finditer(body):
        word = match.group(0)
        low = word.lower()
        if low in STOPWORDS or (len(low) < 2 and not low.isdigit()):
            continue
        stem = _match_stem(low)
        content.add(stem)
        capital = word[:1].isupper() and not _SENTENCE_START_RE.search(body[: match.start()])
        if capital or any(ch.isdigit() for ch in word):
            values.add(stem)
    return content, values


def a_saved_fact_holds_it(sentence: str, before: str = "", *, session_id: str, source_context: Any = None) -> bool:
    """Whether a saved row this chat may read holds what ``sentence`` (with the sentence ``before`` it) is about.
    False when the store cannot be read: the sentence is then judged as it always was."""
    try:
        from core.context_scope import ContextAccessPolicy
        from core.memory.entries import search_relevant_memory

        text = str(sentence or "").strip()
        said, said_values = _stems(text)
        if len(said) < 2 and str(before or "").strip():
            text = f"{str(before).strip()} {text}"
            said, said_values = _stems(text)
        if not said:
            return False
        context = source_context if isinstance(source_context, dict) else None
        chat = str(session_id or (context or {}).get("runtime_session_id") or (context or {}).get("session_id")
                   or (context or {}).get("chat_id") or "")
        if not chat:
            return False
        policy = ContextAccessPolicy.for_request(session_id=chat, source_context=context)
        for row in search_relevant_memory(text, access_policy=policy, limit=12):
            if str(row.get("category") or "fact") != "fact":
                continue
            held, _row_values = _stems(str(row.get("text") or ""))
            shared = said & held
            if (
                said_values <= held
                and (len(shared) >= 2 or shared & said_values)
                and len(said - held) <= max(1, len(said) // 3)
            ):
                return True
        return False
    except Exception:
        return False


__all__ = ["a_saved_fact_holds_it"]
