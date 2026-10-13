"""Whether a message asks to recall the user's own stored/private memory.

This module is the ONE definition of that question. It lived inside `core/web/api/runtime.py`,
which made it unreachable to the whole-turn arbitration in `core.agent_runtime.answer_coverage`:
the private-memory lane terminated turns with no `claim_may_preempt_turn` check (the only
pre-agent exit without one), and a memory clause read as owed to nobody, so any other lane could
end the turn on top of it. The detector now lives here so both consumers -- the lane and the
arbitration -- read the same predicate, and the family can be registered like every other.

The detector and formatter share complete subject/field intent; memory vocabulary alone never
grants whole-profile authority. The legacy field-tail helper remains available to its callers.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class _RecallSubject:
    subject: tuple[str, ...]
    relation: tuple[str, ...] = ()


_RECALL_SUBJECT_FRAMES = (
    re.compile(r"^(?:what|when|where|who|which|how)(?:'s|s|\s+(?:is|are|was|were))\s+(.+)$"),
    re.compile(r"^what\s+do\s+you\s+(?:remember|know)\s+(?:about|of)\s+(.+)$"),
    re.compile(r"^(?:(?:do|can|could)\s+you\s+)?(?:remember|recall|know)\s+(?:about\s+)?(.+)$"),
    re.compile(r"^(?:(?:can|could)\s+you\s+)?remind\s+me\s+(?:of|about)\s+(.+)$"),
)
_SUBJECT_WORD = re.compile(r"[^\W_]+(?:'[^\W_]+)*", re.UNICODE)


def _recall_subject(text: str) -> _RecallSubject | None:
    """Complete noun-phrase identity, with possessive/of relations kept typed.

    A forgotten actor grants recall provenance for its own relations, never
    a longer actor whose spelling merely contains it. Unknown text shapes
    abstain; a privacy carrier's substring erase law is not a subject parser.
    """
    value = unicodedata.normalize("NFKC", str(text or "")).casefold().replace("’", "'")
    value = " ".join(value.split()).strip(" .!?;")
    if not value or re.search(r"[^\w\s'\-_]", value):
        return None

    def words(part: str) -> tuple[str, ...]:
        return tuple(_SUBJECT_WORD.findall(part))

    possessive = re.fullmatch(r"(.+?)'s\s+(.+)", value)
    of_relation = re.fullmatch(r"(.+?)\s+of\s+(.+)", value)
    if possessive or of_relation:
        actor, relation = (
            possessive.groups() if possessive else tuple(reversed(of_relation.groups()))
        )
        relation = re.sub(r"^(?:the|a|an)\s+", "", relation)
        subject_words, relation_words = words(actor), words(relation)
        return _RecallSubject(subject_words, relation_words) if subject_words and relation_words else None
    subject_words = words(value)
    return _RecallSubject(subject_words) if subject_words else None


def _question_subject(text: str) -> _RecallSubject | None:
    value = unicodedata.normalize("NFKC", str(text or "")).casefold().replace("’", "'")
    value = " ".join(value.split()).strip(" .!?;")
    value = re.sub(r"^(?:(?:please|also|and)\s+)+", "", value)
    for frame in _RECALL_SUBJECT_FRAMES:
        match = frame.fullmatch(value)
        if match:
            return _recall_subject(match.group(1))
    return None


def revoked_memory_recall_clauses(
    user_text: str,
    *,
    revoked_tokens: tuple[str, ...] | list[str],
) -> tuple[str, ...]:
    """Read questions whose subject the scoped forget authority revoked.

    The tokens must come from a completed, authorized erasure read. Text
    alone cannot decide that a person's fact belongs to private memory;
    the persisted revocation supplies that provenance. Commands and quoted
    data do not become recall requests merely by repeating a forgotten word.
    """
    if not revoked_tokens:
        return ()
    from core.agent_runtime.answer_coverage import demand_units, turn_slices
    from core.turn_ir import ClauseKind, parse_turn_ir

    targets = tuple(subject for token in revoked_tokens if (subject := _recall_subject(token)) is not None)
    claimed: list[str] = []
    for item in turn_slices(user_text):
        clauses = parse_turn_ir(item.text).clauses
        if len(clauses) != 1 or clauses[0].kind not in {ClauseKind.KNOW, ClauseKind.RECALL}:
            continue
        requested = _question_subject(clauses[0].request_text)
        if requested and any(
            target.subject == requested.subject
            and (not target.relation or target.relation == requested.relation)
            for target in targets
        ):
            # Arbitration is needed only after a revoked subject matches. In
            # particular, unrelated FX or tool requests must never run another
            # lane's admission probe while checking for a memory absence.
            units = demand_units(item.text)
            if units is not None and len(units) == 1:
                claimed.append(item.text)
    return tuple(claimed)


@dataclass(frozen=True)
class PrivateMemoryRecallIntent:
    whole_profile: bool = False
    fields: tuple[str, ...] = ()


_PROFILE_SUBJECTS = frozenset({
    ("me",), ("my", "profile"), ("my", "personal", "profile"), ("my", "user", "profile"),
})
_PROFILE_FIELD_SUBJECTS = {
    ("name",): ("name",),
    ("answer", "style"): ("answer_style",),
    ("response", "style"): ("answer_style",),
    ("preferred", "style"): ("answer_style",),
    ("preferred", "answer", "style"): ("answer_style",),
    ("project", "codename"): ("project_codename",),
    ("active", "codename"): ("project_codename",),
    ("codename",): ("project_codename",),
    ("preference",): ("answer_style", "constraints"),
    ("preferences",): ("answer_style", "constraints"),
    ("constraints",): ("constraints",),
}
_PROFILE_READ_FRAME = re.compile(r"^(?:please\s+)?(?:show|list|summarize|summarise)\s+(.+)$")
_PROFILE_STORED_FRAME = re.compile(r"^what\s+(?:is\s+(?:stored|saved)|have\s+you\s+(?:stored|saved))\s+about\s+(.+)$")
_PROFILE_FIELD_COPULA_FRAME = re.compile(r"^what(?:'s|s|\s+(?:is|are))\s+(.+)$")
_PROFILE_RECALL_FRAME = re.compile(r"^(?:personal\s+)?profile\s+recall\s*:\s*\S", re.IGNORECASE)


def private_memory_recall_intent(user_text: str) -> PrivateMemoryRecallIntent:
    """The complete requested subject owns profile or field recall authority.

    Memory wording alone names an operation, never its private subject. A
    third-party fact or a field used as a file modifier cannot claim a user
    profile merely by containing remember/profile/style vocabulary.
    """
    from core.user_identity_authority import classify_identity_question

    if classify_identity_question(user_text).asks_user_identity:
        return PrivateMemoryRecallIntent(fields=("name",))
    text = unicodedata.normalize("NFKC", str(user_text or "")).casefold().replace("’", "'")
    text = " ".join(text.split()).strip(" .!?;")
    if _PROFILE_RECALL_FRAME.match(text):
        return PrivateMemoryRecallIntent(whole_profile=True)
    subject = None
    # Recall/show/storage operations ask for the contents. A location or
    # timing question about the profile is not permission to dump it.
    for frame in (_PROFILE_FIELD_COPULA_FRAME, *_RECALL_SUBJECT_FRAMES[1:], _PROFILE_READ_FRAME, _PROFILE_STORED_FRAME):
        match = frame.fullmatch(text)
        if match:
            subject = _recall_subject(match.group(1))
            break
    if subject is None or subject.relation:
        return PrivateMemoryRecallIntent()
    if subject.subject in _PROFILE_SUBJECTS:
        return PrivateMemoryRecallIntent(whole_profile=True)
    if subject.subject[:1] == ("my",):
        fields = tuple(dict.fromkeys(
            field
            for words, targets in _PROFILE_FIELD_SUBJECTS.items()
            if subject.subject[1:len(words) + 1] == words
            and field_is_the_subject(text, " ".join(words))
            for field in targets
        ))
        return PrivateMemoryRecallIntent(fields=fields)
    return PrivateMemoryRecallIntent()


def looks_like_private_memory_recall(
    user_text: str,
    *,
    recent_user_texts: tuple[str, ...] | list[str] = (),
) -> bool:
    from core.user_identity_authority import classify_identity_question

    if classify_identity_question(user_text, recent_user_texts=recent_user_texts).asks_user_identity:
        return True
    intent = private_memory_recall_intent(user_text)
    return intent.whole_profile or bool(intent.fields)


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
    "private_memory_recall_intent",
    "revoked_memory_recall_clauses",
]
