"""Whether a message asks to recall the user's own stored/private memory.

This module is the ONE definition of that question. It lived inside `core/web/api/runtime.py`,
which made it unreachable to the whole-turn arbitration in `core.agent_runtime.answer_coverage`:
the private-memory lane terminated turns with no `claim_may_preempt_turn` check (the only
pre-agent exit without one), and a memory clause read as owed to nobody, so any other lane could
end the turn on top of it. The detector now lives here so both consumers -- the lane and the
arbitration -- read the same predicate, and the family can be registered like every other.

Moved verbatim from `core/web/api/runtime.py`; the behavior is unchanged, including the measured
anchoring rules in `field_is_the_subject` (see its docstring for why broadening is banned).
"""
from __future__ import annotations

import re


def looks_like_private_memory_recall(
    user_text: str,
    *,
    recent_user_texts: tuple[str, ...] | list[str] = (),
) -> bool:
    text = " ".join(str(user_text or "").lower().split())
    if not text:
        return False
    # A question about the user's OWN name is settings recall regardless of phrasing. The marker
    # list below is literal, so it answered "what's my name?" and left "whats my name?", "yo say my
    # name!" and "say my nam pls" to a model that then asked for a name already saved in settings.
    from core.user_identity_authority import classify_identity_question

    if classify_identity_question(text, recent_user_texts=recent_user_texts).asks_user_identity:
        return True
    # Explicit whole-profile recall — unambiguous, safe to claim on their own.
    #
    # "what is my name" / "what's my name" / "who am i" used to sit in this tuple as bare
    # substrings, and that was a second, independent copy of the defect the classifier above was
    # fixed for: `"who am i" in text` is true of "…and who am I meeting?", so even with the
    # classifier correctly declining, the turn still reached the recall path and answered with the
    # whole stored profile. They are gone rather than re-scoped — `classify_identity_question`
    # is checked immediately above and is strictly broader for all three, so nothing is lost.
    profile_markers = (
        "profile recall",
        "personal profile",
        "persistent memory",
        "stored about me",
        "remember about me",
        "what do you remember",
        "who is the user",
    )
    if any(marker in text for marker in profile_markers):
        return True
    # Field-specific recall only counts when paired with a possessive/recall intent, so
    # generic uses of these words (e.g. "set your response style", "the codename is X")
    # no longer hijack the turn away from the model.
    recall_intent = any(
        phrase in text
        for phrase in (
            "what is my",
            "what's my",
            "what are my",
            "do you remember my",
            "do you know my",
            "recall my",
            "my stored",
            "remind me of my",
        )
    )
    if not recall_intent:
        return False
    return any(field_is_the_subject(text, field) for field in PRIVATE_MEMORY_FIELDS)


#: The stored fields this lane holds a value for.
PRIVATE_MEMORY_FIELDS = (
    "answer style",
    "response style",
    "preferred style",
    "project codename",
    "active codename",
    "codename",
    "preference",
)

# `\w+` rather than `\S+` so that "preference?" and "codename." read as "nothing follows".
_FIELD_TAIL_RE = re.compile(r"\s*(\w+)", re.IGNORECASE)


def field_is_the_subject(text: str, field: str) -> bool:
    """True when the stored field is what the question is ABOUT, not a modifier of something else.

    The arm used to be a bare `field in text`, so any occurrence of the word claimed the turn.
    Measured 2026-08-18 over 49 first-person turns that this lane must not take, 5 were claimed and
    every one of them for this reason:

        "what is my preference file in vscode"        -> claimed; answered from the profile store
        "what's my codename column in users.csv"      -> claimed; answered "Your active project
                                                         codename is X" about a CSV column
        "what is my response style config in the api client"
        "what is my code preference for tabs vs spaces in this repo"

    Broadening the lane cannot fix these -- measured across four candidate widenings, the
    false-positive floor stayed at 5/49 in every one, because the cause is the matching and not the
    scope. Anchoring is therefore the change that has to land FIRST.

    The tail test is `fast_paths_utility.SUBJECT_TAIL_WORDS`, the same question that lane asks of
    "my project budget" -- read the word directly after the matched noun; a word that is not a
    copula, determiner, deictic or preposition is a new subject, and the field was describing it.
    """

    from core.agent_runtime.fast_paths_utility import SUBJECT_TAIL_WORDS

    start = 0
    while True:
        found = text.find(field, start)
        if found < 0:
            return False
        end = found + len(field)
        # A plural is the same field: "what are my preferences?" asks for the stored preferences.
        # Requiring a strict whole word broke it, which the control caught.
        if end < len(text) and text[end] == "s":
            end += 1
        # Otherwise the field must be a whole word, so "preference" does not match inside a longer
        # token that happens to contain it.
        after_is_word = end < len(text) and (text[end].isalnum() or text[end] == "_")
        if not after_is_word:
            tail = _FIELD_TAIL_RE.match(text, end)
            if tail is None or tail.group(1).casefold() in SUBJECT_TAIL_WORDS:
                return True
        start = found + 1


__all__ = [
    "PRIVATE_MEMORY_FIELDS",
    "field_is_the_subject",
    "looks_like_private_memory_recall",
]
