"""Whether a request's own words withhold the approval they carry.

Measured 2026-09-15 at cbe05fa3 (the approval-words repair): `core.operator.parser._approval_requested` counted an
approval word whole in the request's own words and never read what the request said about it. 'delete my Apple note
"Plan", actually don't do it' and 'do not proceed, delete my apple note "Plan" later' parsed as approved. So did "don't
clean all temp files", 'yes, don't delete my apple note "Plan"', a trailing "never mind" and every deferral ("later",
"tomorrow", "once I confirm"). "don't approve move <id>" also selected the move kind. The Notes delete skips its
confirmation on that reading, and the move, cleanup and calendar-draft handlers run the session's pending action on it.

The reading fails closed. An approval stands only when nothing the request says could withhold it:

* negated: a negation (not, no, never, without, a n't form, cancel, stop, skip...) reaches the approval word, the action
  the request names, or a value it names ('yes, delete my apple note, not "Plan"');
* refused: a negation reaches nothing it could be about ("no, go ahead", "cancel that", "don't."), so it refuses the
  request itself;
* doubted: a negation reaches an affirmation ("not sure", "not ok");
* deferred: the request names a later time, a condition or a wait ("later", "tomorrow", "at 6pm", "in 20 minutes",
  "once", "if", "until", "hold off"), or a negation reaches immediacy ("not now", "no rush");
* retracted: the approval or the action comes before a retraction ("do it. never mind").

A negation reaches the words after it up to the end of its clause: punctuation, a dash, or "but"/"however"/"instead".
So "no problem, go ahead" and "yes, don't ask again" approve: each negation's reach ends before the approval and holds
words about something else. Without the comma, "no problem go ahead" reaches the approval and asks again; in doubt, the
reading asks.

Values are never read here. The caller passes the request's own words (`core.operator.parser.request_own_words`), where
titles, folder and account names, quoted names, paths, and the time a calendar request takes are already masked. The
times a request names are found by the time authority (`core.operator.when.time_expression_spans`).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from core.operator.when import time_expression_spans
from core.typo_fold import near_miss

__all__ = ["approval_withheld"]

_TOKEN_RE = re.compile(
    r"(?P<value>\x00+)"
    r"|(?P<word>[A-Za-z0-9]+(?:['’][A-Za-z]+)*)"
    r"|(?P<stop>\.\.+|…|--+|[–—]|(?<!\S)-(?!\S)|[,;:.!?()\[\]\n\r])"
)

#: Words that negate what follows them in their clause: negators, and verbs that refuse what they govern.
_NEGATIONS = frozenset({
    "not", "no", "never", "nor", "neither", "none", "nothing", "nobody", "nowhere", "without", "cannot", "nope", "nah",
    "stop", "cancel", "abort", "halt", "refuse", "reject", "decline", "deny", "avoid", "skip", "forget",
})
#: Every n't form, with or without its apostrophe ("don't", "dont", "don’t", "can't", "wont").
_CONTRACTED_NEGATION_RE = re.compile(
    r"(?:do|does|did|is|are|was|were|have|has|had|ca|can|could|would|should|must|need|might|dare|wo|ai|sha)n['’]?t"
)
#: Words that open a clause of their own, so a negation's reach ends before them ("don't ask, but go ahead").
_CONTRASTS = frozenset({"but", "however", "instead", "though", "although"})
#: Words that say nothing a negation could be about on their own: pronouns, determiners, prepositions, auxiliaries and
#: politeness. A negation whose reach holds only these refuses the request itself ("cancel that", "not at all").
_EMPTY_WORDS = frozenset({
    "it", "its", "that", "this", "these", "those", "them", "they", "one", "ones", "so", "any", "anything", "something",
    "everything", "all", "at", "do", "does", "did", "doing", "done", "be", "is", "are", "was", "were", "the", "a", "an",
    "of", "to", "for", "on", "in", "with", "by", "from", "about", "again", "please", "pls", "just", "really", "ever",
    "even", "me", "you", "i", "we", "us", "my", "your", "our", "way", "like", "quite", "exactly", "thanks", "thx",
})
#: A negation that reaches an affirmation doubts the approval ("not sure", "not ok").
_AFFIRMATIONS = frozenset({
    "yes", "yeah", "yep", "yup", "sure", "ok", "okay", "alright", "fine", "certain", "right", "correct", "good", "happy",
    "confident", "agree", "agreed", "approved", "convinced",
})
#: A negation that reaches immediacy defers the approval ("not now", "no rush").
_IMMEDIACY = frozenset({
    "now", "today", "immediately", "instantly", "asap", "rush", "hurry", "urgent", "urgently", "fast", "quick", "quickly",
    "straightaway",
})
#: Words that defer an action or make it conditional, wherever they stand in the request.
_DEFERRALS = frozenset({
    "later", "afterwards", "afterward", "eventually", "someday", "sometime", "soon", "tomorrow", "tonight", "tmrw", "tmr",
    "yet", "next", "wait", "hold", "pause", "postpone", "delay", "defer", "after", "once", "when", "whenever", "until",
    "till", "til", "unless", "if", "before", "weekend",
})
#: A delay the time authority reads only with a number ("in 20 minutes"), said with an article or a vague count.
_VAGUE_DELAY_RE = re.compile(
    r"\bin\s+(?:a|an|one|a\s+few|a\s+couple(?:\s+of)?)\s+"
    r"(?:bit|while|moment|sec|secs|second|seconds|min|mins|minute|minutes|hour|hours|day|days|week|weeks)\b",
    re.IGNORECASE,
)
#: What comes before a retraction is withdrawn ("do it. never mind").
_RETRACTION_RE = re.compile(
    r"\b(?:never\s*mind|nvm|scratch\s+that|belay\s+that|changed\s+my\s+mind|second\s+thoughts?)\b", re.IGNORECASE,
)

#: Only the longer withholding words are typo-folded: a short one sits one typo from ordinary words ("not" -> "note",
#: "hold" -> "old", "once" -> "one"), and folding it would withhold every request holding them.
_FOLD_MIN_LEN = 6
_FOLDABLE = tuple(sorted(word for word in _NEGATIONS | _DEFERRALS if len(word) >= _FOLD_MIN_LEN))


@dataclass(frozen=True)
class _Token:
    kind: str  # "value" (a masked value), "word" or "stop" (a clause boundary)
    word: str  # lowercased and typo-folded; "" for a value or a stop
    start: int


def _fold(word: str) -> str:
    """`word`, or the one longer withholding word it is a single typo of ("tomorow" -> "tomorrow")."""
    if len(word) < _FOLD_MIN_LEN or word in _NEGATIONS or word in _DEFERRALS:
        return word
    hits = [head for head in _FOLDABLE if near_miss(word, head)]
    return hits[0] if len(hits) == 1 else word


def _tokens(text: str) -> list[_Token]:
    return [
        _Token(match.lastgroup or "", _fold(match.group(0).lower()) if match.lastgroup == "word" else "", match.start())
        for match in _TOKEN_RE.finditer(text)
    ]


def _is_negation(word: str) -> bool:
    return word in _NEGATIONS or _CONTRACTED_NEGATION_RE.fullmatch(word) is not None


def _reach(tokens: list[_Token], index: int) -> list[_Token]:
    """What the negation at `index` governs: the tokens after it, up to the end of its clause."""
    reach: list[_Token] = []
    for token in tokens[index + 1:]:
        if token.kind == "stop" or token.word in _CONTRASTS:
            break
        reach.append(token)
    return reach


def approval_withheld(own_words: str, *, approvals: re.Pattern[str], actions: re.Pattern[str] | None = None) -> str:
    """Why the request's own words withhold the approval they carry, or "" when nothing withholds it.

    The reasons are "negated", "refused", "doubted", "deferred" and "retracted" (see the module docstring). `own_words` is
    the request with its values masked. `approvals` matches the approval words the caller reads; `actions` matches the words
    that name the action an approval would run, taken from the caller's own recognizer.
    """
    text = str(own_words or "")
    governed = {match.start() for match in approvals.finditer(text)}
    if actions is not None:
        governed |= {match.start() for match in actions.finditer(text)}
    tokens = _tokens(text)
    for index, token in enumerate(tokens):
        if token.kind != "word" or not _is_negation(token.word):
            continue
        reach = _reach(tokens, index)
        if any(item.kind == "value" or item.start in governed for item in reach):
            return "negated"
        words = [item.word for item in reach]
        if all(word in _EMPTY_WORDS for word in words):
            return "refused"
        if any(word in _AFFIRMATIONS for word in words):
            return "doubted"
        if any(word in _IMMEDIACY for word in words):
            return "deferred"
    if any(token.word in _DEFERRALS for token in tokens) or _VAGUE_DELAY_RE.search(text) or time_expression_spans(text):
        return "deferred"
    retractions = [match.start() for match in _RETRACTION_RE.finditer(text)]
    if retractions and any(start < retractions[-1] for start in governed):
        return "retracted"
    return ""
