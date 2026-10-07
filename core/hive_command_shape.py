"""Whether a chat message is a Hive command or an ordinary sentence that mentions a hive.

"Hive", "hive mind" and "tasks" are ordinary words. Matching them anywhere in a message answered
beekeeping, team and sci-fi questions with the Hive watcher script and kept them from the model.
A message is a Hive command only when the whole message is the command: one short clause about the
Hive queue, optionally wrapped in courtesy words.
"""

from __future__ import annotations

import re

_LEAD_RE = re.compile(
    r"^(?:(?:hi|hey|ok(?:ay)?|so|yo|please|pls|now|vool)[,.!\s]+|(?:can|could|would)\s+you\s+(?:please\s+)?)+"
)
_TAIL_RE = re.compile(r"(?:[,\s]+(?:please|pls|plz|now|for me|right now|today))+$")
_HIVE_RE = re.compile(r"\b(?:hive mind|brain hive|public hive|hive)\b")
# A hive that belongs to someone, or is one of many, is a real beehive, not the product's queue.
_REAL_HIVE_RE = re.compile(
    r"\b(?:my|our|your|his|her|their|a|each|every|this|that)\s+(?:bee\s*)?hives?\b|\bbee\s*hives?\b|\bbeehives?\b"
    r"|\bbees?\b|\bbeekeep\w*|\bhoney\b|\bqueen\b|\bcolony\b|\bframes?\b|\bwax\b"
)
_TASK_WORD_RE = re.compile(r"\b(?:tasks?|taks)\b")
_MAX_COMMAND_WORDS = 10
# Words that may follow a matched command and keep it a command ("check the hive tasks for me",
# "what is available in hive to help with"). Anything else after the match is a different request:
# "check the hive temperature".
_COMMAND_TAIL_WORDS = frozenset(
    {
        "and", "available", "can", "do", "for", "help", "hive", "in", "it", "let's", "lets", "me",
        "mind", "now", "on", "one", "open", "please", "pls", "queue", "research", "researches",
        "task", "tasks", "to", "today", "we", "what", "with", "work",
    }
)


def hive_command_core(text: str) -> str:
    """The message as a bare command: lead and tail courtesy words and end punctuation removed."""
    core = " ".join(str(text or "").strip().lower().split()).strip(" .!?")
    core = _LEAD_RE.sub("", core)
    return _TAIL_RE.sub("", core).strip(" .!?,")


def is_short_hive_clause(text: str) -> bool:
    """One short clause that names the product Hive and nothing else.

    Used by the keyword fallbacks that turn "any open hive tasks?" into the Hive task listing. A
    second clause or sentence ("we use hive mind tools at work, any risks?"), a long sentence, or a
    hive that belongs to someone ("what tasks should I do in my hive this spring?") is ordinary chat.
    """
    core = hive_command_core(text)
    if not core or not _HIVE_RE.search(core):
        return False
    if re.search(r"[,;:!.]", core):
        return False
    # Two short questions about the Hive queue stay one request ("what's on the hive? can we do some
    # tasks?"); a question mark followed by anything else is a second sentence.
    if "?" in core and not _TASK_WORD_RE.search(core):
        return False
    if _REAL_HIVE_RE.search(core):
        return False
    return len(core.split()) <= _MAX_COMMAND_WORDS


def names_the_product_hive(text: str) -> bool:
    """The message names the Hive, and not someone's own beehive."""
    core = hive_command_core(text)
    return bool(_HIVE_RE.search(core)) and not _REAL_HIVE_RE.search(core)


def matches_hive_command(text: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    """A Hive command pattern claims the message only when the message is that command.

    Either the whole message is the pattern, or the pattern is found inside one short clause that
    names the Hive ("what is available in hive to help with"). A pattern found inside a longer
    sentence, a second clause, or a sentence about a real beehive does not claim it.
    """
    core = hive_command_core(text)
    if not core:
        return False
    if any(pattern.fullmatch(core) for pattern in patterns):
        return True
    if not is_short_hive_clause(core):
        return False
    for pattern in patterns:
        match = pattern.match(core)
        if match and set(core[match.end():].split()) <= _COMMAND_TAIL_WORDS:
            return True
    return False
