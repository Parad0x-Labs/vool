from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Any

from core.context_scope import ContextAccessPolicy
from core.memory.revocation import RevokedContentWrite, fenced_writer

# Serializes the per-turn memory write sequence (conversation-log append + trim, and every
# downstream load-modify-rewrite: facts, heuristics, session summary, dialogue). The daemon
# serves turns on a threadpool, so two concurrent turns would otherwise interleave a
# read-all-then-rewrite on the same process-global files and silently drop facts or truncate the
# log. Reentrant so a nested writer that also takes it does not deadlock.
_MEMORY_WRITE_LOCK = threading.RLock()

from core.memory import entries as memory_entries
from core.memory import learning as memory_learning
from core.memory.admission import _APOSTROPHES
from core.memory.command_result import _ForgetOutcomeRegistry
from core.memory.files import (
    MAX_CONVERSATION_LOG_BYTES as _MAX_CONVERSATION_LOG_BYTES,
)
from core.memory.files import (
    append_sequenced_jsonl,
    conversation_log_path,
    memory_entries_path,
    memory_path,
    operator_dense_profile_path,
    session_summaries_path,
    user_heuristics_path,
)
from core.memory.files import (
    ensure_memory_files as _ensure_memory_files,
)
from core.memory.files import (
    trim_jsonl_file as _trim_jsonl_file,
)
from core.memory.files import (
    utcnow as _utcnow,
)
from core.memory.policies import (
    describe_session_memory_policy,
    session_memory_policy,
    set_session_memory_policy,
)
from core.memory.policies import (
    ensure_session_policy_table as _ensure_session_policy_table,
)
from core.memory.policies import (
    parse_session_scope_command as _parse_session_scope_command,
)
from core.privacy_guard import share_scope_label
from storage.dialogue_memory import (
    archive_dialogue_topic,
    get_dialogue_session,
    update_dialogue_session,
)

LOGGER = logging.getLogger(__name__)
_forget_outcomes = _ForgetOutcomeRegistry()

add_memory_fact = memory_entries.add_memory_fact
forget_memory = memory_entries.forget_memory
list_memory_entries = memory_entries.list_memory_entries
load_memory_excerpt = memory_entries.load_memory_excerpt
recent_conversation_events = memory_entries.recent_conversation_events
list_conversation_sessions = memory_entries.list_conversation_sessions
load_session_meta = memory_entries.load_session_meta
set_session_meta = memory_entries.set_session_meta
delete_conversation_session = memory_entries.delete_conversation_session
forget_sessions_memory = memory_entries.forget_sessions_memory
search_relevant_memory = memory_entries.search_relevant_memory
search_session_summaries = memory_entries.search_session_summaries
search_user_heuristics = memory_entries.search_user_heuristics
summarize_memory = memory_entries.summarize_memory
load_operator_dense_profile = memory_learning.load_operator_dense_profile
refresh_operator_dense_profile = memory_learning.refresh_operator_dense_profile

_keyword_tokens = memory_entries.keyword_tokens_filtered
_sanitize_fact = memory_entries.sanitize_fact
_trim_text = memory_entries.trim_text
_normalized_history = memory_learning.normalized_history
_record_assistant_dialogue_turn = memory_learning.record_assistant_dialogue_turn
_auto_capture_memory = memory_learning.auto_capture_memory
_update_user_heuristics = memory_learning.update_user_heuristics
_detect_implicit_feedback = memory_learning.detect_implicit_feedback
_update_session_summary = memory_learning.update_session_summary


def has_relevant_memory_candidate(
    query_text: str,
    *,
    session_id: str,
    access_policy: ContextAccessPolicy | None = None,
    limit: int = 1,
) -> bool:
    """Return whether scoped durable memory can answer part of this query.

    This is intentionally only a routing hint.  It does not return memory text or
    answer the user; the normal provider path still performs context assembly and
    generates the response.  The front door uses it to prevent a generic utility
    fast path from claiming a question whose answer is present in this chat's
    scoped memory.
    """
    try:
        policy = memory_entries.resolve_memory_access_policy(
            access_policy=access_policy,
            chat_id=session_id,
        )
        return bool(
            search_relevant_memory(
                query_text,
                access_policy=policy,
                limit=max(1, int(limit)),
            )
        )
    except (TypeError, ValueError):
        return False

__all__ = [
    "ARTIFACT_KIND_COUNCIL_SUMMARY",
    "TRANSCRIPT_COMMIT_BOUNDARY_KEY",
    "add_memory_fact",
    "append_assistant_artifact_event",
    "append_conversation_event",
    "augment_history_from_session_log",
    "conversation_log_path",
    "describe_session_memory_policy",
    "ensure_memory_files",
    "flush_staged_conversation_events",
    "forget_memory",
    "has_relevant_memory_candidate",
    "has_staged_conversation_event",
    "list_memory_entries",
    "load_memory_excerpt",
    "load_operator_dense_profile",
    "maybe_handle_memory_command",
    "memory_entries_path",
    "memory_lifecycle_snapshot",
    "memory_path",
    "open_transcript_commit_boundary",
    "operator_dense_profile_path",
    "persist_staged_conversation_event",
    "recent_conversation_events",
    "refresh_operator_dense_profile",
    "search_relevant_memory",
    "search_session_summaries",
    "search_user_heuristics",
    "session_memory_policy",
    "session_summaries_path",
    "set_session_memory_policy",
    "summarize_memory",
    "transcript_commit_boundary_open",
    "user_heuristics_path",
]

_REMEMBER_RE = re.compile(r"^(?:remember(?: that)?|note(?: that)?|store(?: this)?)\s+(.+)$", re.IGNORECASE)
_MEMORY_CAPTURE_RE = re.compile(
    # The lead-in's words ("This is important," / "In this chat,") stay on one line: crossing a line break they took
    # a comma inside quoted material for their end and read the material's verb as the command's.
    r"^(?:(?:this is|in this)\b[^.!?\n]*(?:[.!?]\s*|,\s*))?"
    r"(?:please\s+)?(?P<verb>remember(?: that)?|note(?: that)?|store(?: this)?|keep|retain|save)\s+"
    r"(?P<fact>.+)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class _MaterialCapture:
    """A capture command on its first line with the material under it: the verb and the fact's opening are read from
    that line alone, so nothing inside the material can be taken for the command; the material follows the fact."""

    verb: str
    fact: str

    def group(self, name: str) -> str:
        return {"verb": self.verb, "fact": self.fact}[name]


def _memory_capture_match(text: str) -> re.Match[str] | _MaterialCapture | None:
    """The capture command, on one line -- or on its first line followed only by the material it names.

    "Remember this template:" with a fenced or quoted block under it is one command: the block is what to remember
    (and admission keeps it source material, never the owner's profile). Any other later line is a separate turn
    for the model, so a multi-line message is only a capture when everything after its first line is material.
    """
    match = _MEMORY_CAPTURE_RE.match(text)
    if match is not None or "\n" not in text:
        return match
    first, rest = text.split("\n", 1)
    head = _MEMORY_CAPTURE_RE.match(first.strip())
    if head is None or not rest.strip():
        return None
    from core.memory.admission import classify_user_text

    origin = classify_user_text(rest)
    if not origin.has_source_material or origin.authored_text.strip():
        return None
    return _MaterialCapture(verb=head.group("verb"), fact=head.group("fact") + "\n" + rest)


_MEMORY_CAPTURE_TRAILER_RE = re.compile(
    r"\s+reply\s+with\s+(?:only\s+)?stored\.?$",
    re.IGNORECASE,
)
_CORRECTION_RE = re.compile(
    r"^(?:correction|update|replace)\b(?P<body>.+)$",
    re.IGNORECASE,
)
_CORRECTION_VALUE_RE = re.compile(
    r"\b(?:"
    r"\d{4}-\d{2}-\d{2}"
    r"|\d+(?:\.\d+)?\s+[A-Z]{2,}"
    r"|[A-Z][A-Z0-9]*(?:[-_][A-Z0-9]+)+(?:\s+[A-Z0-9]+)*"
    r"|[A-Z]{2,}(?:\s+[A-Z0-9]+){1,}"
    r")\b"
)
_NON_MEMORY_CAPTURE_RE = re.compile(
    r"^(?:your|my)\s+(?:next\s+)?(?:answer|answers|reply|replies|response|responses)\b"
    r"|^(?:this|the)\s+(?:chat|conversation)\s+(?:stay|remain|be)\b",
    re.IGNORECASE,
)
_WORKSPACE_SAVE_RE = re.compile(
    r"^(?:save|store|keep|retain)\b.*\b(?:workspace|file|folder|directory|path|disk)\b",
    re.IGNORECASE,
)
#: Capture verbs that take an OBJECT. "remember that ..." and "note that ..." carry a statement --
#: which may itself mention a note or a meeting and is still the thing to remember -- while
#: "save/store/keep/retain X" puts X somewhere, and X can be an effect another lane owns
#: (`_WORKSPACE_SAVE_RE` above is that rule for files).
_OBJECT_CAPTURE_VERBS = frozenset({"save", "store", "store this", "keep", "retain"})
_PROFILE_MEMORY_MARKERS = (
    "my name is",
    "call me",
    "i go by",
    "my preferred",
    "my preference",
    "i prefer",
    "my timezone",
    "my time zone",
    "my language",
    "my pronouns",
    "my role is",
    "my job is",
    "my location is",
    "i live in",
)
# A "remember X ... <question>" message also asks for an answer; do not stop at "Locked in" —
# defer so the turn answers it (mission/values are tracked separately by active-mission slots).
_ANSWER_REQUEST_RE = re.compile(
    r"\b(what(?:'s| is| are)?|which|how many|summari[sz]e|\blist\b|tell me|show me|"
    r"write (?:the|out|down)|give me|recall)\b",
    re.IGNORECASE,
)
# "Remember when we said we would never use jQuery again?" is a reminiscence -- an invitation to
# recall something together -- and it was stored as a fact and answered "Locked in. I'll remember
# that." in 0.2s on the deployed build 2026-07-30.
#
# The verb is the same in both readings; the MOOD is not. "Remember X" is an imperative that names
# a proposition to store. A reminiscence is a question: it either opens with an interrogative after
# the verb ("remember WHEN we said", "remember HOW hard that was") or is simply asked as one.
# Neither shape hands over anything storable, so neither can be a storage instruction.
_REMINISCENCE_OPENER_RE = re.compile(
    r"^(?:when|how|why|where|who|whom|whether|if|what|which|time\s+(?:we|you|i|they|it)\b)\b",
    re.IGNORECASE,
)
# "forget it" / "forget about it" is the idiom for "never mind", not an erase command: "forget
# about it, tell me a joke instead" used to run a memory erase and answer "Forget applied".
_FORGET_RE = re.compile(r"^(?:forget|erase)\s+(?!(?:about\s+)?it\b)(.+)$", re.IGNORECASE)
# "Forget A. Forget B." and "Forget A and forget B" name two targets. Only an explicit forget verb after a
# sentence end or an "and"/"then" starts the next one; "Forget salt and pepper" stays one target.
_FORGET_NEXT_TARGET_RE = re.compile(
    r"(?:[.!?;]+\s+|,?\s+(?:and|then)\s+)(?:(?:and|also|then|please)\s+)*(?=(?:forget|erase)\s+\S)",
    re.IGNORECASE,
)
# What a forget may report as removed memory entries when no entry or semantic record matched: copies VOOL
# derived from what it held, and carriers the assistant reads back (context pages, chat titles, local tasks).
# The chat's own transcript and run history are scrubbed of the words as well, but those rows only record that
# the words were said -- an earlier turn, or the previous forget command itself -- and are no memory the user
# stored (see _FORGET_CHAT_HISTORY_STORES).
_FORGET_MEMORY_DERIVATIVE_STORES = (
    "structured_summaries", "session_summaries", "memory_receipts", "memory_fts", "source_fts",
    "dialogue_topic_archives", "learning_shards", "user_heuristics", "operator_dense_profile", "context_pages",
    "chat_session_metadata", "chat_documents",
)
_FORGET_CHAT_HISTORY_STORES = ("conversation_log", "dialogue_derivatives")
_FORGET_TARGET_RE = re.compile(
    r"^(?:the\s+)?(?:old\s+|previous\s+|exact\s+)?"
    r"(?:identifier|code|marker|private\s+marker|label|port|number|phrase|value|fact|token)\s+"
    r"(?P<value>.+?)"
    r"(?:\s+from\s+(?:this|the\s+current)\s+chat)?$",
    re.IGNORECASE,
)
# "Forget storage label MINT-84" / "forget my locker code 5906": a described identifier. Only a single value of
# four or more characters carrying a digit is a candidate: a sentence ("forget that the code changed", "forget the
# gate code is QX-4471") is never one, and a short number ("forget Bob's code 42") keeps its description. The
# value alone becomes the target only where nothing else this chat remembers holds it (`_described_forget_value`).
_FORGET_DESCRIBED_VALUE_RE = re.compile(
    r"^(?:the\s+|my\s+)?(?:old\s+|previous\s+|exact\s+)?(?:[a-z][a-z'-]*\s+){0,2}"
    r"(?:identifier|code|marker|label|port|number|phrase|value|token)\s+"
    r"(?P<value>(?=\S{4})[^\s\d]*\d\S*)"
    r"(?:\s+from\s+(?:this|the\s+current)\s+chat)?$",
    re.IGNORECASE,
)
#: An apostrophe as any keyboard types it: "I’ve" and "it’s" from a phone are "I've" and "it's" (review of 1033851).
_FORGET_APOSTROPHE = "['" + "".join(_APOSTROPHES) + "]"
_FORGET_TOLD_YOU_ABOUT_RE = re.compile(
    r"^(?:what|everything|anything|all)\s+(?:that\s+)?i(?:" + _FORGET_APOSTROPHE + r"ve|\s+have)?\s+"
    r"(?:told\s+you|said(?:\s+to\s+you)?|shared(?:\s+with\s+you)?|gave\s+you)\s+about\s+(?=\S)",
    re.IGNORECASE,
)
_REFERENTIAL_FORGET_RE = re.compile(
    r"^(?:the\s+)?(?:old\s+|previous\s+|exact\s+)?"
    r"(?:(?:[a-z0-9][a-z0-9_-]*\s+){0,4})?"
    r"(?P<subject>identifier|code|marker|route|label|port|budget|phrase|note|date)\s+"
    r"(?:i\s+asked\s+you\s+to\s+(?:remember|retain|store|save)|i\s+(?:gave|told)\s+you)"
    r"(?:\s+in\s+this\s+chat)?$",
    re.IGNORECASE,
)
_MEMORY_CONTINUATION_RE = re.compile(
    r"\s*[.!?;]\s+(?=(?:also\s+)?(?:remember(?:\s+that)?|note(?:\s+that)?|"
    r"store(?:\s+this)?|keep|retain|save)\s+)",
    re.IGNORECASE,
)
_MEMORY_CONTINUATION_PREFIX_RE = re.compile(
    r"^(?:also\s+)?(?:remember(?:\s+that)?|note(?:\s+that)?|"
    r"store(?:\s+this)?|keep|retain|save)\s+",
    re.IGNORECASE,
)
_SNAPSHOT_GENERIC_OPERATION_TOKENS = {
    "append",
    "back",
    "create",
    "desktop",
    "directory",
    "download",
    "exact",
    "exactly",
    "file",
    "files",
    "folder",
    "inside",
    "make",
    "path",
    "paths",
    "read",
    "readback",
    "save",
    "text",
    "whole",
    "workspace",
    "write",
}
_SNAPSHOT_UTILITY_MARKERS = (
    "what time",
    "date and time",
    "what date",
    "what day",
    "weather",
)
_MEMORY_RECALL_QUESTION_STARTS = (
    "what ",
    "which ",
    "where ",
    "who ",
    "do you ",
    "have i ",
    "is ",
    "repeat ",
    "can you ",
    "could you ",
    "summarize ",
    "summarise ",
)
_MEMORY_RECALL_MARKERS = (
    "remember",
    "retain",
    "stored",
    "memory",
    "save",
    "saved",
    "note",
    "active",
    "superseded",
    "obsolete",
    "this chat",
    "this conversation",
    "from this chat",
    "current marker",
    "current code",
    "current date",
    "current budget",
    "exact route",
    "exact identifier",
    "exact code",
    "budget note",
    "keep in mind",
    "asked you",
    "different chat",
    "another chat",
    "previous chat",
    "other chat",
    "past chat",
    "outside this chat",
)
_OTHER_CHAT_MEMORY_MARKERS = (
    "different chat",
    "another chat",
    "previous chat",
    "other chat",
    "past chat",
    "outside this chat",
)


#: The two answers this lane gives when its own lookup found nothing. Named rather than written
#: inline twice, because `maybe_handle_memory_command` has to RECOGNISE them in order to decide
#: whether reporting an absence is this lane's business at all.
_NO_ACTIVE_MEMORY_RECORDED = "No matching active memory is recorded in this chat."
_NO_ACTIVE_REMEMBERED_VALUE = (
    "I don't have an active remembered value matching that request in this chat."
)
_EMPTY_RECALL_ANSWERS = frozenset({_NO_ACTIVE_MEMORY_RECORDED, _NO_ACTIVE_REMEMBERED_VALUE})

# A question about WHERE in the conversation something was said -- "the very FIRST thing I asked you
# to remember", "what did I tell you EARLIEST" -- is a property of the transcript, not of a row in
# this store. This lane's lookup is relevance-ranked and carries no ordering, so it cannot answer an
# ordinal question even when the store is full: it returns whichever row matched best and labels it
# "Active remembered value", which is a different fact wearing the answer's clothes.
#
# Measured 2026-08-17, turn 49 of a 50-turn chat: "What was the very first thing I asked you to
# remember?" was claimed by this lane with model_ran=False and answered "I don't have an active
# remembered value matching that request in this chat" -- while turn 48, which reached the model,
# recited the same facts correctly out of the transcript.
_TRANSCRIPT_ORDINAL_RE = re.compile(
    r"\b(?:"
    r"(?:very\s+)?first|earliest|initial(?:ly)?|original(?:ly)?|second|third"
    r"|at\s+the\s+(?:start|beginning|outset)|to\s+begin\s+with|in\s+order|chronological(?:ly)?"
    r")\b",
    re.IGNORECASE,
)


def _asks_about_transcript_order(text: str) -> bool:
    """True when the question is about WHEN in this chat something was said."""
    return bool(_TRANSCRIPT_ORDINAL_RE.search(" ".join(str(text or "").casefold().split())))


def _memory_recall_empty_result_is_explicit(text: str) -> bool:
    """Only claim an empty memory result when the user asked for an inventory."""
    lowered = " ".join(str(text or "").casefold().split())
    if re.search(
        r"\bcurrent\s+(?:exact\s+)?"
        r"(?:(?:[a-z0-9][a-z0-9_-]*\s+){0,4})?"
        r"(?:identifier|code|marker|route|label|port|budget|phrase|note)\b",
        lowered,
    ):
        # These are typed memory-slot queries.  If the active store has no
        # matching row, falling through to chat history can resurrect a value
        # the user explicitly forgot.
        return True
    return any(
        marker in lowered
        for marker in (
            "fresh chat",
            "fresh conversation",
            "different chat",
            "another chat",
            "previous chat",
            "other chat",
            "past chat",
            "outside this chat",
            "from this chat",
            "in this chat",
            "still active",
            "remains stored",
            "what facts",
            "which exact facts",
            "all the facts",
        )
    )


def _is_a_reminiscence(remainder: str, whole_message: str) -> bool:
    """True when "remember ..." is recalling something, not handing over a fact to store.

    Two tests, and both ask what the sentence IS rather than what it is about: what FOLLOWS the
    verb is an interrogative clause, or the message is a question outright. A storage instruction
    is an imperative in both readings of English, so neither shape can be one.

    The cost of the second test is a "remember that the meeting is at 3?" typed with a trailing
    question mark falling to the model instead of the store -- a slower turn, not a wrong answer.
    The cost of not having it is a question silently entered into the user's memory as a fact.
    """
    if _REMINISCENCE_OPENER_RE.match(" ".join(str(remainder or "").split())):
        return True
    return str(whole_message or "").strip().endswith("?")


def _split_explicit_memory_facts(raw_fact: str) -> list[str]:
    """Split only repeated, explicit memory imperatives into independent facts."""
    parts = _MEMORY_CONTINUATION_RE.split(str(raw_fact or "").strip())
    facts: list[str] = []
    for index, part in enumerate(parts[:4]):
        # Spaces are normalized within each line; line breaks stay, because a fence or a quote block is only
        # recognisable by its lines and admission must still see it as source material, not the owner's words.
        clean = "\n".join(" ".join(line.split()) for line in str(part or "").splitlines() if line.strip()).strip()
        if index:
            clean = _MEMORY_CONTINUATION_PREFIX_RE.sub("", clean).strip()
        if clean:
            facts.append(clean)
    return facts


#: A reason the user appends to a forget ("..., it is wrong", "... it was a typo", "...? its wrong", "... lol") is not
#: part of the named target: left on, it made the whole sentence the keyword, nothing matched, and the reply still
#: said "Forget applied" (served check 2026-10-07).
#: A reason or courtesy the user appends to a forget is not part of the named target (review 2026-10-07, twice):
#: a courtesy word counts only at the very end of the text; a clause word counts only after punctuation or a dash,
#: or after a head of two or more content words; and a head that keeps no content word is no target at all
#: (measured: "Forget my Thanks Giving plans" stripped to "my" and the keyword path removed every entry with "my").
_FORGET_COURTESY_TAIL_RE = re.compile(r"^(?P<head>.+?)[\s,;:!?.]*\b(?:thanks|thank\s+you|please|pls|lol)[\s.!?]*$", re.IGNORECASE)
_FORGET_CLAUSE_WORDS = (r"(?:it(?:" + _FORGET_APOSTROPHE + r"s|\s+is|\s+was)|that(?:" + _FORGET_APOSTROPHE + r"s|\s+is|\s+was)|this\s+(?:is|was)|which\s+(?:is|was)|its|because|since"
                        r"|as\s+it)")
_FORGET_CLAUSE_AFTER_PUNCT_RE = re.compile(r"^(?P<head>.+?)(?:\s*[,;:?!]+\s*|\s+[-\u2013\u2014]\s*)" + _FORGET_CLAUSE_WORDS + r"\b.*$",
                                           re.IGNORECASE)
_FORGET_CLAUSE_AFTER_WORDS_RE = re.compile(r"^(?P<head>.+?)\s+" + _FORGET_CLAUSE_WORDS + r"\b.*$", re.IGNORECASE)
_FORGET_TARGET_STOPWORDS = frozenset({
    "my", "our", "your", "his", "her", "their", "its", "the", "a", "an", "this", "that", "these", "those", "old",
    "new", "previous", "exact", "current", "of", "for", "to", "in", "on", "at", "and", "or", "about", "from", "with",
    "i", "you", "we", "it", "is", "was", "be", "gave", "told",
})


def _forget_content_words(text: str) -> list[str]:
    return [w for w in re.findall(r"[A-Za-z0-9][\w'\-]*", str(text or "")) if w.lower() not in _FORGET_TARGET_STOPWORDS]


def _without_forget_reason_tail(target: str) -> str:
    """The target before an appended courtesy or reason clause; the target itself when the strip would leave no
    content word or would shorten the target's own first content word."""
    original_words = _forget_content_words(target)
    candidate = target
    courtesy = _FORGET_COURTESY_TAIL_RE.match(candidate)
    if courtesy is not None:
        candidate = courtesy.group("head")
    clause = _FORGET_CLAUSE_AFTER_PUNCT_RE.match(candidate)
    if clause is None:
        clause = _FORGET_CLAUSE_AFTER_WORDS_RE.match(candidate)
        if clause is not None and len(_forget_content_words(clause.group("head"))) < 2:
            clause = None
    if clause is not None:
        candidate = clause.group("head")
    head = candidate.strip(" .,!?;:-")
    head_words = _forget_content_words(head)
    if not head_words or (original_words and head_words[0].lower() != original_words[0].lower()):
        return target
    return head


def _forget_target_without_preservation_clause(raw_target: str) -> str:
    """Remove a trailing keep-clause or reason clause without changing the named forget target."""
    target = " ".join(str(raw_target or "").strip().strip(".!?").split())
    target = re.sub(
        r",?\s+but\s+keep\b.*$",
        "",
        target,
        flags=re.IGNORECASE,
    ).strip()
    return _without_forget_reason_tail(target).strip(" .,!?;:")


def _forget_command_arguments(text: str) -> list[str]:
    """The targets one forget command names, in order; [] when the text is no forget command."""
    parsed = _FORGET_RE.match(text)
    if parsed is None:
        return []
    arguments: list[str] = []
    seen: set[str] = set()
    clauses = _FORGET_NEXT_TARGET_RE.split(text)
    if len(clauses) > 1 and _split_inside_quotes(text):
        # The "forget" sits inside a quoted phrase ("Forget the phrase 'stop and forget me'"): one target.
        return [parsed.group(1)]
    for clause in clauses:
        match = _FORGET_RE.match(clause.strip())
        if match is None:
            return [parsed.group(1)]
        key = " ".join(_normalize_forget_target(match.group(1)).casefold().split())
        if key not in seen:
            seen.add(key)
            arguments.append(match.group(1))
    return arguments


_FORGET_QUOTED_SPAN_RE = re.compile(r"""(?<!\w)["\u201c'\u2018].+?["\u201d'\u2019](?!\w)""")


def _split_inside_quotes(text: str) -> bool:
    """True when a target boundary falls inside a quoted phrase. A quote opens only before a word and closes only
    after one, so "the kids' toys" and 5'11" quote nothing."""
    spans = [match.span() for match in _FORGET_QUOTED_SPAN_RE.finditer(text)]
    return any(start < boundary.start() < end for boundary in _FORGET_NEXT_TARGET_RE.finditer(text)
               for start, end in spans)


def _combined_forget_result(results: list[tuple[str, str, dict[str, Any]]]) -> tuple[str, str, dict[str, Any]]:
    """One reply for a forget that named several targets; each target's own outcome is kept."""
    statuses = [status for _, status, _ in results]
    stores: dict[str, Any] = {}
    for _, _, erasures in results:
        for name, result in erasures.get("stores", {}).items():
            # A store is verified for the turn only when it was verified for every target.
            if stores.get(name, {}).get("status") != "incomplete":
                stores[name] = result
    unfinished = [status for status in statuses if status in {"failed", "partial", "not_started", "needs_target"}]
    if unfinished:
        # Something was erased but not everything named: partial, never a bare refusal or failure.
        combined = "partial" if {"applied", "partial"} & set(statuses) else unfinished[0]
    else:
        combined = next((status for status in ("applied", "already_erased", "no_match") if status in statuses),
                        statuses[-1])
    applied = [reply for reply, status, _ in results if status == "applied"]
    if len(applied) == len(results):
        removed = sum(int(re.search(r"Removed (\d+)", reply).group(1)) for reply in applied)
        reply = f"Forget applied. Removed {removed} memory entr{'y' if removed == 1 else 'ies'}."
    else:
        reply = "\n".join(reply for reply, _, _ in results)
    return reply, combined, {"stores": stores}


def _normalize_forget_target(raw_target: str) -> str:
    """Extract the value from natural forget wording without broad deletion."""
    target = _forget_target_without_preservation_clause(raw_target)
    # "Forget what I told you about Ilona's favourite tree" names "Ilona's favourite tree"; the
    # lead-in is how people say it, not part of anything they stored.
    target = _FORGET_TOLD_YOU_ABOUT_RE.sub("", target, count=1)
    match = _FORGET_TARGET_RE.fullmatch(target)
    if match:
        target = match.group("value")
    target = re.sub(
        r"\s+from\s+(?:this|the\s+current)\s+chat$",
        "",
        target,
        flags=re.IGNORECASE,
    )
    return target.strip(" .,!?")


_FORGET_DESCRIPTION_FILLER = frozenset({"the", "my", "old", "previous", "exact"})


def _described_forget_value(
    token: str,
    *,
    access_policy: ContextAccessPolicy,
    chat_id: str,
    runtime_home: str | None,
) -> tuple[str, str]:
    """Whether a described value ("storage label MINT-84") names one memory stored in other words.

    Returns ("value", value) when exactly one active memory holds the value, it also holds every description
    word, and nothing else this chat remembers or said holds the value: forgetting the value then forgets that
    memory and nothing else. Returns ("ambiguous", "") when the value is held elsewhere as well, so forgetting it
    would also erase another memory (review of 9efc9e8: "Forget Bob's locker code 5906." erased Alice's 5906).
    Returns ("", "") when the target is not a described value or the words as typed already match.
    """
    from core.memory.carriers import _QUOTE_MARKS, forget_needle_in

    # A phone keyboard types "Bob’s": the description reads the same as "Bob's" (review of 7e45341).
    typed = token.translate(_QUOTE_MARKS)
    match = _FORGET_DESCRIBED_VALUE_RE.fullmatch(typed)
    if match is None:
        return "", ""
    value = match.group("value").strip(" .,!?")
    described = typed[: match.start("value")].split()
    words = [word for word in described if word.casefold() not in _FORGET_DESCRIPTION_FILLER]
    rows = [
        str(row.get("text") or "")
        for row in memory_entries._load_erasure_rows(memory_entries_path())
        if memory_entries._memory_command_row_allowed(row, access_policy=access_policy)
    ]
    if any(forget_needle_in(text, token) for text in rows):
        return "", ""
    holders = [text for text in rows if forget_needle_in(text, value)]
    if not holders:
        return "", ""
    described_holders = [text for text in holders if all(forget_needle_in(text, word) for word in words)]
    if len(holders) != 1 or len(described_holders) != 1:
        return ("ambiguous", "") if described_holders else ("", "")
    from core.context_retrieval import _open_memory_for_runtime

    memory = _open_memory_for_runtime(runtime_home)
    if memory is None:
        return "ambiguous", ""
    try:
        with memory._lock:
            bodies = [str(row[0]) for row in memory._conn.execute(
                "SELECT body FROM source_occurrences WHERE chat_scope = ? AND status = 'active'", (chat_id,),
            ).fetchall()]
    finally:
        memory.close()
    # What the chat said with the value but without its description is another memory of that value.
    if any(forget_needle_in(body, value) and not all(forget_needle_in(body, word) for word in words)
           for body in bodies):
        return "ambiguous", ""
    return "value", value


def _referential_forget_token(
    raw_target: str,
    *,
    access_policy: ContextAccessPolicy,
) -> tuple[bool, str]:
    """Resolve one explicitly named current-chat memory without broad deletion."""
    target = _forget_target_without_preservation_clause(raw_target)
    match = _REFERENTIAL_FORGET_RE.fullmatch(target)
    if match is None:
        return False, ""
    subject = str(match.group("subject") or "").casefold()
    rows = [
        row
        for row in memory_entries._load_erasure_rows(memory_entries_path())
        if memory_entries._memory_command_row_allowed(row, access_policy=access_policy)
        if subject in str(row.get("fact_key") or "").casefold()
        or subject in str(row.get("text") or "").casefold()
        or subject
        in {
            str(token).casefold()
            for token in row.get("keywords") or []
        }
    ]
    if not rows:
        # Nothing stored under that name: the referential reading resolves nothing, so the keyword
        # path decides (and says so when it removes nothing). "More than one" was never true here.
        return False, ""
    if len(rows) != 1:
        return True, ""
    return True, _memory_recall_value(str(rows[0].get("text") or ""))


def _is_memory_recall_question(text: str) -> bool:
    """Recognize explicit memory questions without routing ordinary chat to a fast path."""
    lowered = " ".join(str(text or "").casefold().split())
    for prefix in (
        "this is a fresh chat. ",
        "this is a completely fresh conversation. ",
    ):
        if lowered.startswith(prefix):
            lowered = lowered.removeprefix(prefix)
            break
    if not lowered.startswith(_MEMORY_RECALL_QUESTION_STARTS):
        return False
    if any(marker in lowered for marker in _MEMORY_RECALL_MARKERS):
        return True
    return bool(
        re.search(
            r"\b(?:my|our|the)\s+current\b.*\b"
            r"(?:identifier|code|marker|route|label|port|budget|phrase|note|date)\b",
            lowered,
        )
    )


def _memory_recall_value(text: str) -> str:
    """Render a stored fact's value while retaining the source text when it has no delimiter."""
    clean = " ".join(str(text or "").strip().strip(".!?").split())
    if ":" in clean:
        value = clean.rsplit(":", 1)[1].strip()
        if value:
            return value
    return clean


def _memory_recall_response(
    query: str,
    *,
    access_policy: ContextAccessPolicy,
) -> str:
    lowered = " ".join(str(query or "").casefold().split())
    if any(marker in lowered for marker in _OTHER_CHAT_MEMORY_MARKERS):
        return (
            "I can only access active memory from this chat; I don't have access to memory from another chat."
        )
    list_request = any(
        marker in lowered
        for marker in (
            "which exact facts",
            "what facts",
            "summarize only the facts",
            "summarise only the facts",
            "all the facts",
        )
    )
    stored_rows = list_memory_entries(access_policy=access_policy, limit=20)
    if list_request:
        rows = stored_rows[:8]
    else:
        stale_request = any(
            marker in lowered
            for marker in (
                "old ",
                "previous ",
                "superseded",
                "obsolete",
                "without repeating",
                "do not repeat",
            )
        ) and not bool(
            re.search(
                r"\b(?:"
                r"(?:not|never|without)\s+(?:the\s+)?(?:old|previous|superseded|obsolete)"
                r"|(?:do\s+not|don't|never)\s+(?:include|mention|repeat|return|show|use)\s+"
                r"(?:the\s+)?(?:old|previous|superseded|obsolete)"
                r"|(?:exclude|omit)\s+(?:the\s+)?(?:old|previous|superseded|obsolete)"
                r")\b",
                lowered,
            )
        )
        subject_markers = (
            "identifier",
            "code",
            "marker",
            "route",
            "label",
            "port",
            "budget",
            "phrase",
            "note",
            "date",
        )
        subject = next(
            (
                marker
                for marker in subject_markers
                if re.search(rf"\b{re.escape(marker)}\b", lowered)
            ),
            "",
        )
        if stale_request:
            rows = []
        elif subject:
            rows = [
                row
                for row in stored_rows
                if subject in str(row.get("fact_key") or "").casefold()
                or subject in str(row.get("text") or "").casefold()
                or subject in {
                    str(token).casefold()
                    for token in row.get("keywords") or []
                }
            ][:4]
        else:
            rows = search_relevant_memory(
                query,
                access_policy=access_policy,
                limit=4,
            )
    values: list[str] = []
    for row in rows:
        value = _memory_recall_value(str(row.get("text") or ""))
        if value and value.casefold() not in {item.casefold() for item in values}:
            values.append(value)

    if not values:
        if any(marker in lowered for marker in ("still active", "do you know", "from this chat")):
            return _NO_ACTIVE_MEMORY_RECORDED
        return _NO_ACTIVE_REMEMBERED_VALUE

    if list_request:
        if "two short sentences" in lowered:
            joined = "; ".join(values[:8])
            return (
                f"Active remembered facts in this chat: {joined}. "
                "No other active remembered facts were found."
            )
        return "Active remembered facts in this chat:\n" + "\n".join(
            f"- {value}" for value in values[:8]
        )
    if len(values) == 1:
        return f"Active remembered value: {values[0]}."
    return "Active remembered values: " + "; ".join(values[:4]) + "."


def _correction_values(body: str) -> list[str]:
    values: list[str] = []
    for match in _CORRECTION_VALUE_RE.finditer(str(body or "")):
        value = " ".join(match.group(0).split()).strip(" .,!?")
        if value and value.casefold() not in {item.casefold() for item in values}:
            values.append(value)
    return values


def ensure_memory_files() -> None:
    _ensure_memory_files(ensure_policy_table=_ensure_session_policy_table)


def _memory_persistence_suppressed(source_context: dict[str, Any] | None, session_id: str | None = None) -> bool:
    """True when this turn must execute but persist NOTHING to the shared memory store.

    Test/audit/eval drivers set source_context['memory_scope']='ephemeral' (or export
    VOOL_EPHEMERAL_MEMORY=1). A capability probe such as "append HELLO2 to vool_audit_probe.txt"
    then runs its real tool but never lands in conversation_log / session_summaries / memory_entries /
    user_heuristics — the stores whose cross-session/global recall would otherwise replay it inside an
    unrelated LIVE turn's prompt. Deliberately NOT keyed on PYTEST_CURRENT_TEST: the memory-pipeline
    unit tests assert that persistence happens.
    """
    import os

    ctx = source_context or {}
    if _forget_outcomes.turn_not_started(ctx):
        return True
    scope = str(ctx.get("memory_scope") or "").strip().lower()
    if scope in {"ephemeral", "audit", "test", "probe", "none"}:
        return True
    if ctx.get("persist_memory") is False or bool(ctx.get("ephemeral_memory")):
        return True
    # Private chat (core.private_chat): the turn was stamped private at the front door, or its chat
    # is private now. A chat whose switch cannot be read counts as private (fail closed).
    from core.private_chat import turn_is_private

    if turn_is_private(ctx, session_id):
        return True
    return str(os.environ.get("VOOL_EPHEMERAL_MEMORY") or "").strip().lower() in {"1", "true", "yes", "on"}


def _current_request_lineage() -> str:
    """A8 payload lineage: the A0 request id bound to the current dispatch, if
    any ('' on legacy/off-HTTP lanes — never fabricated)."""
    try:
        from core.semantic.semantic_admissions import current_request_id

        return str(current_request_id() or "")
    except Exception:
        return ""


def _attachment_receipt_fields(session_id: str, source_context: dict[str, Any] | None) -> dict[str, Any]:
    """`{"attachments": [...]}` for a turn that carried staged attachments; `{}` otherwise.

    Read from the ONE attachment authority by the turn id the ingress stamped, never from anything
    the client sent. An attachment nothing consumed by the time the answer is written is recorded
    as ignored -- there is no later reader.
    """
    turn_id = str((source_context or {}).get("attachment_turn_id") or "").strip()
    if not turn_id:
        return {}
    try:
        from core.chat_attachments import receipt_for_turn

        receipt = receipt_for_turn(session_id=session_id, turn_id=turn_id)
    except Exception:
        return {}
    if not receipt:
        return {}
    return {
        "attachments": [
            {**entry, "outcome": ("ignored" if entry.get("outcome") == "pending" else entry.get("outcome"))}
            for entry in receipt
        ]
    }


# ---- Transcript rows and the response-commit boundary ------------------------------------------
#
# The lane writers (chat surface, fast-command surface, turn reasoning) call
# ``append_conversation_event`` the moment they have text, which is BEFORE ``core.finalization``
# runs the publication and authorship gates and seals the answer. Measured on the served daemon
# (2026-09-06): the transcript held the model's pre-gate draft while the wire served the typed
# refusal, and every downstream memory writer had already mined that draft. A served turn whose
# ingress DECLARES its commit boundary (``source_context["transcript_commit_boundary"]``, stamped by
# the /api/chat door, which always frames the answer through ``finalize_answer``) is therefore STAGED
# here and written once, by the seal, with the committed bytes -- the response.commit boundary is the
# transcript's write boundary. Every other caller (channels, CLI, tests writing rows directly, lanes
# with no request lineage) keeps writing immediately, exactly as before.
_TRANSCRIPT_STAGE_LOCK = threading.Lock()
_STAGED_TRANSCRIPT_EVENTS: dict[str, list[dict[str, Any]]] = {}
#: Request ids whose ingress has declared a commit boundary and has not yet flushed it. A row is
#: staged only while its request's boundary is OPEN: a background writer that reuses a served
#: turn's context after the request ended (a worker thread carrying the copied contextvars) writes
#: at once instead of staging a row nobody would seal or flush. Guarded by
#: tests/test_transcript_persists_at_commit.py (writer outliving its request).
_OPEN_TRANSCRIPT_BOUNDARIES: set[str] = set()
#: A staged row nobody sealed or flushed within this window is written on the next staging call
#: with ``commit_state="unsealed"`` -- a turn that died between staging and commit must not lose
#: the user's message, and must not be recorded as a committed answer either.
_STAGED_TRANSCRIPT_MAX_AGE_S = 900.0
TRANSCRIPT_COMMIT_STATE_FIELD = "commit_state"
#: Stamped on the turn's source_context by an ingress that will seal the answer through
#: ``finalize_answer`` (and flush at request end). Only such turns stage their transcript row.
TRANSCRIPT_COMMIT_BOUNDARY_KEY = "transcript_commit_boundary"
TRANSCRIPT_COMMIT_STATE_COMMITTED = "committed"
TRANSCRIPT_COMMIT_STATE_UNSEALED = "unsealed"
TRANSCRIPT_COMMIT_STATE_NO_ANSWER = "no_answer_terminal"
#: The person stopped the turn before its answer was sealed. The row holds their message and an
#: EMPTY assistant side: no answer was published, so nothing of the turn's own text is kept (not
#: even a partly streamed answer). The page's stopped marker is painted from this state where the
#: transcript is shown (``/api/chat/history``, the export) and a later prompt sees the exchange as
#: closed, never as a reply. Written once, at the stop (``append_conversation_event`` with
#: ``stopped_request_id``).
TRANSCRIPT_COMMIT_STATE_STOPPED = "stopped"
#: The catalog key and English source of the words the page shows for a stopped answer.
_STOPPED_MARKER_KEY = "run.stopped_showing"
_STOPPED_MARKER_EN = "(stopped showing this answer)"
#: Request ids whose turn the person stopped while that request was still open. A stopped turn
#: keeps running until its worker notices the stop; whatever it writes under its request id
#: afterwards is a draft the person never saw and is dropped. Never evicted: an id leaves only at
#: its request-end flush (``flush_staged_conversation_events(..., request_end=True)``), which runs
#: once the turn's worker is done, and moves to ``_TRANSCRIPT_OUTCOMES``.
_LIVE_STOPPED_REQUESTS: set[str] = set()
#: How each ended request's transcript turn ended: through the seal or a flush
#: (``_OUTCOME_RECORDED``) or through a stop (``_OUTCOME_STOPPED``). Request ids are unique per
#: request (a Try again is a new request), so an entry never touches another turn. Bounded: the
#: oldest entries go first. In memory only.
_TRANSCRIPT_OUTCOMES: dict[str, str] = {}
_TRANSCRIPT_OUTCOMES_MAX = 512
_OUTCOME_RECORDED = "recorded"
_OUTCOME_STOPPED = "stopped"
# The seals that hold a claim (``_claim_transcript_seal`` .. ``_end_transcript_seal``), each with
# the Event a stop that lands meanwhile waits on, and that stop's own record. Never evicted: an
# entry lives exactly as long as its seal, so both are bounded by the seals in flight.
_SEALS_IN_FLIGHT: dict[str, threading.Event] = {}
_STOPS_PENDING_SEAL: dict[str, dict[str, Any]] = {}
# What a served turn learns from its exchange (memory extraction, the turn-context page), held
# until the transcript says how the turn ended (``_after_transcript_records``): run once the answer
# is recorded or the request ends without a stop, dropped when the person stopped the turn. An
# entry lives no longer than its request's boundary. Private like the seal pair: it schedules work
# and reads or serves no memory, so it is not part of the module's reader-classified serving API.
_WORK_AFTER_RECORD: dict[str, list[Any]] = {}


def _after_transcript_records(request_id: str, work: Any) -> str:
    """Run ``work`` once ``request_id``'s turn is recorded as answered; never for a stopped turn.

    The turn's worker reaches what it learns from the exchange before the seal may have run (the
    stream seals after the worker returns), so the decision is the transcript's, made once: an
    answer that was recorded runs the work (``"ran"``, now or when it is recorded: ``"held"``), a
    stop drops it (``"dropped"``). A request without an open commit boundary runs it at once.
    """
    key = str(request_id or "")
    if key:
        with _TRANSCRIPT_STAGE_LOCK:
            if _stopped_locked(key):
                return "dropped"
            if _TRANSCRIPT_OUTCOMES.get(key) != _OUTCOME_RECORDED and (
                key in _OPEN_TRANSCRIPT_BOUNDARIES or key in _SEALS_IN_FLIGHT
            ):
                # It runs later, on whichever thread records the answer: under this caller's own
                # context (its request binding and trust), never under the recorder's.
                import contextvars

                caller = contextvars.copy_context()
                _WORK_AFTER_RECORD.setdefault(key, []).append(lambda: caller.run(work))
                return "held"
    _run_held_work([work])
    return "ran"


def _take_held_work_locked(key: str) -> list[Any]:
    """The work held for ``key``: what to run now, or nothing when the turn was stopped."""
    work = _WORK_AFTER_RECORD.pop(key, [])
    return [] if _stopped_locked(key) else work


def _run_held_work(work: list[Any]) -> None:
    for item in work:
        try:
            item()
        except Exception:
            logging.getLogger(__name__).exception("work held for the transcript record failed")


def append_conversation_event(
    *,
    session_id: str,
    user_input: str,
    assistant_output: str,
    source_context: dict[str, Any] | None = None,
    response_class: str | None = None,
    access_policy: ContextAccessPolicy | None = None,
    persist_now: bool = False,
    stopped_request_id: str = "",
) -> str | None:
    """Record one user/assistant exchange in the conversation log and the derived memory stores.

    Under a served turn whose ingress declared its commit boundary (an A0 request id is bound and
    ``source_context["transcript_commit_boundary"]`` is set) the row is staged and written by
    ``core.finalization.finalize_answer`` with the committed bytes -- see the staging note above.
    ``persist_now`` is the explicit opt-out for a caller that owns its own commit semantics.
    ``stopped_request_id`` records that request's turn as stopped by the person: their message
    with an empty assistant side (``assistant_output`` is not stored; see ``_record_stopped_turn``)
    and returns what that stop decided; every other path returns None.
    """
    if _memory_persistence_suppressed(source_context, session_id):
        return
    if stopped_request_id:
        return _record_stopped_turn(
            stopped_request_id, session_id=session_id, user_input=user_input, source_context=source_context
        )
    declared = bool((source_context or {}).get(TRANSCRIPT_COMMIT_BOUNDARY_KEY)) if isinstance(source_context, dict) else False
    request_id = "" if (persist_now or not declared) else _current_request_lineage()
    if request_id:
        staged = _stage_conversation_event(
            request_id,
            {
                "session_id": session_id,
                "user_input": user_input,
                "assistant_output": assistant_output,
                "source_context": source_context,
                "response_class": response_class,
                "access_policy": access_policy,
                "request_id": request_id,
            },
        )
        if staged != "closed":
            # "staged" waits for the seal; "dropped" is a stopped turn's late draft: it neither
            # adds a turn nor replaces the stopped one.
            return
    _write_conversation_event(
        session_id=session_id,
        user_input=user_input,
        assistant_output=assistant_output,
        source_context=source_context,
        response_class=response_class,
        access_policy=access_policy,
    )


def open_transcript_commit_boundary(request_id: str) -> None:
    """The served ingress declares that ``request_id``'s turn will be sealed by ``finalize_answer``
    or flushed at request end. Rows written under the id stage only while this is open."""
    key = str(request_id or "")
    if not key:
        return
    with _TRANSCRIPT_STAGE_LOCK:
        _OPEN_TRANSCRIPT_BOUNDARIES.add(key)


def transcript_commit_boundary_open(request_id: str) -> bool:
    key = str(request_id or "")
    if not key:
        return False
    with _TRANSCRIPT_STAGE_LOCK:
        return key in _OPEN_TRANSCRIPT_BOUNDARIES


def _stopped_locked(key: str) -> bool:
    return key in _LIVE_STOPPED_REQUESTS or _TRANSCRIPT_OUTCOMES.get(key) == _OUTCOME_STOPPED


def _note_transcript_outcome_locked(request_id: str, outcome: str) -> None:
    if outcome != _OUTCOME_STOPPED and _stopped_locked(request_id):
        return  # a stop is final: nothing written later turns it into a recorded answer
    _TRANSCRIPT_OUTCOMES.pop(request_id, None)
    _TRANSCRIPT_OUTCOMES[request_id] = outcome
    while len(_TRANSCRIPT_OUTCOMES) > _TRANSCRIPT_OUTCOMES_MAX:
        del _TRANSCRIPT_OUTCOMES[next(iter(_TRANSCRIPT_OUTCOMES))]


def _claim_transcript_seal(request_id: str) -> bool:
    """The seal's claim on ``request_id``, taken before the commit is built: one linearization point.

    False when the person's stop was recorded first: the turn is stopped and its answer must not be
    committed. True otherwise: from here a stop waits for the seal (see ``_end_transcript_seal``).
    """
    key = str(request_id or "")
    if not key:
        return True
    with _TRANSCRIPT_STAGE_LOCK:
        if _stopped_locked(key):
            return False
        if _TRANSCRIPT_OUTCOMES.get(key) != _OUTCOME_RECORDED:
            _SEALS_IN_FLIGHT.setdefault(key, threading.Event())
        return True


def _end_transcript_seal(request_id: str, *, committed: bool) -> str:
    """Close the seal's claim. ``committed``: the answer was durably bound (it is the turn's record).

    A stop that arrived during the seal is recorded now when the seal failed (``"stopped"``), and
    is otherwise moot: the answer it raced was committed first (``"committed"``).
    """
    key = str(request_id or "")
    if not key:
        return "unknown"
    with _TRANSCRIPT_STAGE_LOCK:
        pending = _STOPS_PENDING_SEAL.pop(key, None)
        sealed = _SEALS_IN_FLIGHT.pop(key, None)
        if sealed is not None and committed:
            _note_transcript_outcome_locked(key, _OUTCOME_RECORDED)
        held = _take_held_work_locked(key) if committed else []
    try:
        if committed:
            verdict = "committed"
        elif pending is not None:
            verdict = _record_stopped_turn(key, **pending)
        else:
            verdict = "released"
    finally:
        if sealed is not None:
            sealed.set()  # a stop waiting on this seal reads the verdict now
    _run_held_work(held)
    return verdict


def _transcript_request_stopped(request_id: str) -> bool:
    """True when the person stopped ``request_id``'s turn and its stopped row was recorded."""
    key = str(request_id or "")
    if not key:
        return False
    with _TRANSCRIPT_STAGE_LOCK:
        return _stopped_locked(key)


def _record_stopped_turn(
    request_id: str,
    *,
    session_id: str,
    user_input: str,
    source_context: dict[str, Any] | None = None,
) -> str:
    """Record a turn the person stopped, once: their message, an empty assistant side, ``stopped``.

    Called when the stop is acknowledged, while the turn's worker may still be running. Drafts the
    turn already staged are discarded (the person never saw them) and every later row under this
    request id is dropped. Returns ``"stopped"`` when this call recorded the turn,
    ``"already_stopped"`` when an earlier call did, ``"already_recorded"`` when the turn had
    already been written through its seal or a flush (that row is the record; nothing is added),
    and ``"unknown"`` without a request id (nothing can be keyed, so nothing is written).

    A stop that lands while the seal holds its claim waits for that seal (bounded by the seal
    itself: its ``finally`` always ends the claim) and then returns the seal's verdict: the answer
    was committed (``"already_recorded"``) or the bind failed and the stop was recorded
    (``"stopped"``).
    """
    key = str(request_id or "")
    if not key:
        return "unknown"
    with _TRANSCRIPT_STAGE_LOCK:
        if _stopped_locked(key):
            return "already_stopped"
        if _TRANSCRIPT_OUTCOMES.get(key) == _OUTCOME_RECORDED:
            return "already_recorded"
        if key in _SEALS_IN_FLIGHT:
            # The seal claimed this turn first and is binding its answer now. The stop waits for
            # that outcome: recorded as stopped if the seal fails, nothing more if it commits.
            _STOPS_PENDING_SEAL[key] = {"session_id": session_id, "user_input": user_input, "source_context": source_context}
            sealed = _SEALS_IN_FLIGHT[key]
        else:
            sealed = None
            if key in _OPEN_TRANSCRIPT_BOUNDARIES:
                # The request is still open: its request-end flush will retire the id.
                _LIVE_STOPPED_REQUESTS.add(key)
            else:
                _note_transcript_outcome_locked(key, _OUTCOME_STOPPED)
            _STAGED_TRANSCRIPT_EVENTS.pop(key, None)
            _OPEN_TRANSCRIPT_BOUNDARIES.discard(key)
            _WORK_AFTER_RECORD.pop(key, None)  # nothing is learned from a turn the person stopped
    if sealed is not None:
        sealed.wait()
        with _TRANSCRIPT_STAGE_LOCK:
            return "stopped" if _stopped_locked(key) else "already_recorded"
    _write_staged_row(
        {
            "session_id": session_id,
            "user_input": user_input,
            "assistant_output": "",
            "source_context": source_context,
            "response_class": None,
            "access_policy": None,
            "request_id": key,
        },
        assistant_text="",
        commit_state=TRANSCRIPT_COMMIT_STATE_STOPPED,
    )
    return "stopped"


def _stopped_marker_texts() -> frozenset[str]:
    """The stopped marker in every app language the page can show it in (exact, stripped)."""
    global _STOPPED_MARKER_CACHE
    if _STOPPED_MARKER_CACHE is None:
        texts = {_STOPPED_MARKER_EN}
        try:
            from core.i18n.catalog import catalog_for
            from core.i18n.locales import ui_catalog_tags

            for tag in ui_catalog_tags():
                text = catalog_for(tag).text(_STOPPED_MARKER_KEY)
                if text and text != _STOPPED_MARKER_KEY:
                    texts.add(text.strip())
        except Exception:
            LOGGER.warning("stopped marker catalog unavailable; English only")
        _STOPPED_MARKER_CACHE = frozenset(texts)
    return _STOPPED_MARKER_CACHE


_STOPPED_MARKER_CACHE: frozenset[str] | None = None


def _is_stopped_marker(text: Any) -> bool:
    """Whether an assistant message is the page's stopped marker (runtime status, not a reply)."""
    return str(text or "").strip() in _stopped_marker_texts()


def _stopped_marker_text(locale: str = "") -> str:
    """The stopped marker in ``locale`` (or the bound turn locale), painted for a stopped row."""
    try:
        from core.i18n.turn_locale import turn_text

        return turn_text(_STOPPED_MARKER_KEY, _STOPPED_MARKER_EN, locale)
    except Exception:
        return _STOPPED_MARKER_EN


def _stage_conversation_event(request_id: str, event: dict[str, Any]) -> str:
    """Stage ``event`` under ``request_id``: ``"staged"``, ``"dropped"`` or ``"closed"``.

    The stop check and the stage happen under one lock acquisition, so a writer racing the stop
    can never stage a draft after the stopped row was written. ``"closed"``: the request's
    boundary is not open (the caller writes the row at once, as a writer outliving its request).
    """
    stale: list[tuple[str, dict[str, Any]]] = []
    held: list[Any] = []
    now = time.monotonic()
    with _TRANSCRIPT_STAGE_LOCK:
        if _stopped_locked(request_id):
            outcome = "dropped"
        elif request_id in _OPEN_TRANSCRIPT_BOUNDARIES:
            _STAGED_TRANSCRIPT_EVENTS.setdefault(request_id, []).append({**event, "staged_at": now})
            outcome = "staged"
        else:
            outcome = "closed"
        for key in list(_STAGED_TRANSCRIPT_EVENTS):
            if key == request_id:
                continue
            rows = _STAGED_TRANSCRIPT_EVENTS[key]
            if rows and now - float(rows[0].get("staged_at") or now) > _STAGED_TRANSCRIPT_MAX_AGE_S:
                if not _stopped_locked(key):
                    stale.extend((key, row) for row in rows)
                del _STAGED_TRANSCRIPT_EVENTS[key]
                _OPEN_TRANSCRIPT_BOUNDARIES.discard(key)
                held.extend(_take_held_work_locked(key))
    for _key, row in stale:
        _write_staged_row(row, assistant_text=None, commit_state=TRANSCRIPT_COMMIT_STATE_UNSEALED)
    _run_held_work(held)
    return outcome


def has_staged_conversation_event(request_id: str) -> bool:
    key = str(request_id or "")
    if not key:
        return False
    with _TRANSCRIPT_STAGE_LOCK:
        return bool(_STAGED_TRANSCRIPT_EVENTS.get(key))


def persist_staged_conversation_event(
    *,
    request_id: str,
    committed_text: str,
    commit_state: str = TRANSCRIPT_COMMIT_STATE_COMMITTED,
    extra_fields: dict[str, Any] | None = None,
) -> bool:
    """Write the oldest row staged under ``request_id`` with the COMMITTED assistant bytes.

    Called by the seal. Returns False when nothing was staged (a lane that wrote immediately, a
    request that produced no transcript row) or when the person stopped the turn (its stopped
    row is the record) -- never an error, so a seal can never fail on it.
    """
    key = str(request_id or "")
    if not key:
        return False
    with _TRANSCRIPT_STAGE_LOCK:
        if _stopped_locked(key):
            _STAGED_TRANSCRIPT_EVENTS.pop(key, None)
            _WORK_AFTER_RECORD.pop(key, None)
            return False
        rows = _STAGED_TRANSCRIPT_EVENTS.get(key)
        if not rows:
            return False
        row = rows.pop(0)
        if not rows:
            del _STAGED_TRANSCRIPT_EVENTS[key]
        _note_transcript_outcome_locked(key, _OUTCOME_RECORDED)
        held = _take_held_work_locked(key)
    _write_staged_row(row, assistant_text=str(committed_text or ""), commit_state=commit_state, extra_fields=extra_fields)
    _run_held_work(held)
    return True


def flush_staged_conversation_events(
    request_id: str,
    *,
    commit_state: str = TRANSCRIPT_COMMIT_STATE_UNSEALED,
    assistant_text: str | None = None,
    request_end: bool = False,
) -> int:
    """Write every row still staged under ``request_id``.

    The request-end fallback: a turn that never reached the seal keeps the user's message and
    the lane's own text, marked ``unsealed`` (no gate rewrote it, so it is exactly what the
    transport had). A typed no-answer terminal passes ``assistant_text=""`` -- no answer was
    committed, and the row says so instead of carrying a draft nobody saw. A turn the person
    stopped writes nothing here (its stopped row is the record). ``request_end`` marks the served
    door's own end-of-request flush, which runs once the turn is done and retires a stopped id.
    """
    key = str(request_id or "")
    if not key:
        return 0
    with _TRANSCRIPT_STAGE_LOCK:
        rows = _STAGED_TRANSCRIPT_EVENTS.pop(key, [])
        _OPEN_TRANSCRIPT_BOUNDARIES.discard(key)
        if _stopped_locked(key):
            rows = []
            if request_end and key in _LIVE_STOPPED_REQUESTS:
                _LIVE_STOPPED_REQUESTS.discard(key)
                _note_transcript_outcome_locked(key, _OUTCOME_STOPPED)
        elif rows:
            _note_transcript_outcome_locked(key, _OUTCOME_RECORDED)
        # The boundary is closed: what the turn held runs now, unless the person stopped it or the
        # turn ended with no answer (nothing was published to learn from).
        held = _take_held_work_locked(key)
        if commit_state == TRANSCRIPT_COMMIT_STATE_NO_ANSWER:
            held = []
    for row in rows:
        _write_staged_row(row, assistant_text=assistant_text, commit_state=commit_state)
    _run_held_work(held)
    return len(rows)


def _write_staged_row(
    row: dict[str, Any],
    *,
    assistant_text: str | None,
    commit_state: str,
    extra_fields: dict[str, Any] | None = None,
) -> None:
    fields = {TRANSCRIPT_COMMIT_STATE_FIELD: str(commit_state or "")}
    if extra_fields:
        fields.update(extra_fields)
    try:
        _write_conversation_event(
            session_id=str(row.get("session_id") or ""),
            user_input=str(row.get("user_input") or ""),
            assistant_output=(
                str(row.get("assistant_output") or "") if assistant_text is None else assistant_text
            ),
            source_context=row.get("source_context"),
            response_class=row.get("response_class"),
            access_policy=row.get("access_policy"),
            request_id_override=str(row.get("request_id") or ""),
            extra_fields=fields,
        )
    except Exception:
        logging.getLogger(__name__).exception("staged transcript row could not be written")


def _write_conversation_event(
    *,
    session_id: str,
    user_input: str,
    assistant_output: str,
    source_context: dict[str, Any] | None = None,
    response_class: str | None = None,
    access_policy: ContextAccessPolicy | None = None,
    request_id_override: str | None = None,
    extra_fields: dict[str, Any] | None = None,
) -> None:
    # Audit/test/eval turns execute fully but must never enter the shared live memory store, or
    # cross-session/global recall will replay them into a real user's later prompt (this is how the
    # "vool_audit_probe.txt / HELLO2" audit strings leaked into a live chat turn).
    if _memory_persistence_suppressed(source_context, session_id):
        return
    ensure_memory_files()
    from core.reply_control_sanitizer import strip_reply_control_tokens
    from core.secret_redaction import redact_secrets
    history = _normalized_history((source_context or {}).get("conversation_history"))
    if access_policy is None:
        resolved_policy = ContextAccessPolicy.for_request(
            session_id=session_id,
            source_context=source_context,
        )
    else:
        resolved_policy = memory_entries.resolve_memory_access_policy(
            access_policy=access_policy,
            chat_id=session_id,
        )
    memory_policy = session_memory_policy(session_id)
    project_id = resolved_policy.project_id
    # Drop any leaked OpenClaw `NO_REPLY` control token so it never re-enters later turns as history
    # noise (a logged bare token was seen mutating a subsequent turn's context).
    assistant_text = strip_reply_control_tokens(str(assistant_output or ""))
    # Redact secrets ONCE and use the masked copies for the log AND every downstream memory writer
    # (facts, heuristics, session summary, dialogue), so a pasted password never persists anywhere.
    redacted_user = redact_secrets(str(user_input or ""))
    redacted_assistant = redact_secrets(assistant_text)
    # Source retention belongs to the selected runtime owner. A refused
    # profile log or summary must not discard the turn's retraction control.
    is_image_turn = False
    try:
        from core.execution.constants import image_generation_intent

        is_image_turn = bool(image_generation_intent(redacted_user))
    except Exception:
        is_image_turn = False
    # Every finalized turn retains its attributed source, including an
    # image brief. A fictional render subject remains evidence of an ask;
    # it is neither mined above nor indexed as a standing user assertion.
    try:
        from core.context_retrieval import store_turn

        semantic_result = store_turn(
            session_id,
            redacted_user,
            redacted_assistant,
            access_policy=resolved_policy,
            source_context=source_context,
            index_user_statements=not is_image_turn,
        )
        if semantic_result.get("status") == "failed":
            LOGGER.warning(
                "semantic memory write rejected for chat %s: %s",
                session_id,
                semantic_result.get("reason", "unknown"),
            )
    except Exception as exc:
        LOGGER.warning(
            "semantic memory write unavailable for chat %s: %s",
            session_id,
            type(exc).__name__,
        )
    payload = {
        "ts": _utcnow(),
        "session_id": session_id,
        # A8 pass-001 payload lineage: the governing A0 request id at write
        # time ('' on legacy lanes — never fabricated). Serve-time gates and
        # erasure traversal bind derivatives back to finalizations via it.
        "request_id": (
            request_id_override if request_id_override is not None else _current_request_lineage()
        ),
        "project_id": project_id,
        "surface": str((source_context or {}).get("surface", "")),
        "platform": str((source_context or {}).get("platform", "")),
        "user": redacted_user[:4000],
        "assistant": redacted_assistant[:8000],
        "history_message_count": len(history),
        "share_scope": memory_policy["share_scope"],
        "realm_label": (
            memory_policy.get("realm_label")
            or share_scope_label(memory_policy["share_scope"])
        ),
        "restricted_terms": list(
            memory_policy.get("restricted_terms") or []
        ),
    }
    # The turn's attachment receipt rides the persisted user turn: names, kinds, sizes and what
    # became of each -- never contents, never a path. The transcript reader hands it back so the
    # chips a reload paints are the ones the runtime actually saw.
    payload.update(_attachment_receipt_fields(session_id, source_context))
    if extra_fields:
        payload.update(extra_fields)
    # Serialize the whole write sequence: the append+trim and every downstream load-modify-rewrite
    # touch process-global shared files, so concurrent turns must not interleave.
    from contextlib import ExitStack

    from core.memory.revocation import writing

    fence = ExitStack()
    try:
        # The serialized writer row defines the carriers. Live routing objects
        # in source_context are not transcript fields and cannot veto a write.
        fence.enter_context(writing(payload, session_id))
    except Exception as exc:
        fence.close()
        LOGGER.warning("conversation persistence refused: %s", type(exc).__name__)
        return
    # A stopped turn has no reply: nothing assistant-side is mined from it (no dialogue reply, no
    # topic closure, no implicit feedback, no answered exchange in the session summary).
    stopped_turn = str((extra_fields or {}).get(TRANSCRIPT_COMMIT_STATE_FIELD) or "") == TRANSCRIPT_COMMIT_STATE_STOPPED
    with fence, _MEMORY_WRITE_LOCK:
        path = conversation_log_path()
        append_sequenced_jsonl(path, payload)
        _trim_jsonl_file(path, max_bytes=_MAX_CONVERSATION_LOG_BYTES)
        if stopped_turn:
            _auto_capture_memory(session_id=session_id, user_input=redacted_user, project_id=project_id)
            _update_user_heuristics(session_id=session_id, user_input=redacted_user, project_id=project_id)
            refresh_operator_dense_profile(session_id=session_id)
            return
        _record_assistant_dialogue_turn(
            session_id=session_id,
            assistant_output=redacted_assistant,
        )
        _close_failed_dialogue_topic_if_needed(
            session_id=session_id,
            user_input=redacted_user,
            assistant_output=redacted_assistant,
            response_class=response_class,
        )
        # An image-generation turn's text describes the RENDER SUBJECT, not the user. Harvesting it
        # as a fact/preference/"recent ask" is what let an image prompt ("Rick and Morty ... 4K")
        # bleed into the operator profile and make the model confabulate the user's name. Log the
        # turn (above) but never mine a render prompt for standing user facts.
        if not is_image_turn:
            _auto_capture_memory(
                session_id=session_id,
                user_input=redacted_user,
                project_id=project_id,
            )
            _update_user_heuristics(
                session_id=session_id,
                user_input=redacted_user,
                project_id=project_id,
            )
            _detect_implicit_feedback(
                session_id=session_id,
                user_input=redacted_user,
                assistant_output=redacted_assistant,
            )
            _update_session_summary(
                session_id=session_id,
                user_input=redacted_user,
                assistant_output=redacted_assistant,
                project_id=project_id,
                access_policy=resolved_policy,
            )
        refresh_operator_dense_profile(session_id=session_id)
        try:
            from core.active_context_capsule import create_shadow_capsule

            create_shadow_capsule(
                chat_id=session_id,
                project_id=project_id,
                user_text=redacted_user,
                request_id=_current_request_lineage(),
            )
        except Exception as exc:
            LOGGER.warning(
                "context capsule shadow update failed for chat %s: %s",
                session_id,
                type(exc).__name__,
            )



#: Assistant-authored artifacts that belong in a chat's TRANSCRIPT but in none of the
#: memory derived from it. The kind is typed so a reader can tell an artifact from a
#: turn without parsing prose.
ARTIFACT_KIND_COUNCIL_SUMMARY = "council_summary"


@fenced_writer
def append_assistant_artifact_event(
    *,
    session_id: str,
    text: str,
    artifact_kind: str,
    artifact: dict[str, Any] | None = None,
) -> bool:
    """Persist ONE assistant-authored artifact into a chat's transcript — and nothing else.

    A council verdict, unlike a turn, has no user side. Routing it through
    :func:`append_conversation_event` would have manufactured one: that function's whole
    downstream — dialogue turns, fact capture, heuristics, implicit feedback, the session
    summary, the dense operator profile and the semantic store — reads the pair as
    something the operator said and the assistant answered. A council summary naming a
    root cause is not the operator's preference, and mining it as one is how an artifact
    becomes a fabricated memory of a conversation that never happened.

    So this is the smallest seam that does the one needed thing: append the row the
    transcript reader serves, with ``user`` empty and a typed ``artifact_kind``, and call
    none of the mining paths. ``recent_conversation_events`` — the reader every recall
    path uses — skips artifact rows unless a caller explicitly asks for them, so the
    non-pollution holds at the READ side too and does not depend on every future writer
    remembering this rule.

    Returns True when a row was written; False when there is no chat to write it to.
    """
    chat_id = str(session_id or "").strip()
    body = str(text or "").strip()
    kind = str(artifact_kind or "").strip()
    if not chat_id or not body or not kind:
        return False
    ensure_memory_files()
    from core.secret_redaction import redact_secrets

    memory_policy = session_memory_policy(chat_id)
    payload = {
        "ts": _utcnow(),
        "session_id": chat_id,
        "request_id": _current_request_lineage(),
        "project_id": ContextAccessPolicy.for_request(session_id=chat_id).project_id,
        "surface": "artifact",
        "platform": "",
        # No user side, ever. An empty string is the truth here; a synthesized prompt
        # ("summarize the council") would be a message the operator never sent.
        "user": "",
        "assistant": redact_secrets(body)[:8000],
        "artifact_kind": kind,
        "artifact": dict(artifact or {}),
        "history_message_count": 0,
        "share_scope": memory_policy["share_scope"],
        "realm_label": (
            memory_policy.get("realm_label")
            or share_scope_label(memory_policy["share_scope"])
        ),
        "restricted_terms": list(memory_policy.get("restricted_terms") or []),
    }
    with _MEMORY_WRITE_LOCK:
        path = conversation_log_path()
        append_sequenced_jsonl(path, payload)
        _trim_jsonl_file(path, max_bytes=_MAX_CONVERSATION_LOG_BYTES)
    return True


def _close_failed_dialogue_topic_if_needed(
    *,
    session_id: str,
    user_input: str,
    assistant_output: str,
    response_class: str | None,
) -> None:
    normalized_session = str(session_id or "").strip()
    normalized_output = " ".join(str(assistant_output or "").split()).strip()
    normalized_class = str(response_class or "").strip().lower()
    if not normalized_session or not normalized_output:
        return
    if not _assistant_failure_closes_topic(normalized_output, response_class=normalized_class):
        return
    state = get_dialogue_session(normalized_session)
    last_subject = str(state.get("last_subject") or "").strip() or None
    topic_hints = [str(item).strip() for item in list(state.get("topic_hints") or []) if str(item).strip()]
    current_user_goal = str(state.get("current_user_goal") or "").strip() or None
    assistant_commitments = [str(item).strip() for item in list(state.get("assistant_commitments") or []) if str(item).strip()]
    unresolved_followups = [str(item).strip() for item in list(state.get("unresolved_followups") or []) if str(item).strip()]
    if not any([last_subject, topic_hints, current_user_goal, assistant_commitments, unresolved_followups]):
        return
    archive_dialogue_topic(
        normalized_session,
        last_subject=last_subject,
        topic_hints=topic_hints,
        current_user_goal=current_user_goal,
        assistant_commitments=assistant_commitments,
        unresolved_followups=unresolved_followups,
        closure_status="unresolved",
        closure_reason="assistant_failure",
        closing_user_input=user_input,
        closing_assistant_output=normalized_output,
    )
    update_dialogue_session(
        normalized_session,
        last_subject=last_subject,
        topic_hints=topic_hints,
        last_intent_mode=str(state.get("last_intent_mode") or "").strip() or None,
        current_user_goal="",
        assistant_commitments=[],
        unresolved_followups=[],
        user_stance=str(state.get("user_stance") or "").strip() or None,
        emotional_tone=str(state.get("emotional_tone") or "").strip() or None,
    )


def _assistant_failure_closes_topic(text: str, *, response_class: str) -> bool:
    if response_class in {"task_failed_user_safe", "system_error_user_safe"}:
        return True
    lowered = str(text or "").strip().lower()
    failure_prefixes = (
        "i couldn't ",
        "i can't ",
        "i cant ",
        "i cannot ",
        "sorry, i couldn't ",
        "sorry, i can't ",
        "sorry, i cant ",
        "sorry, i cannot ",
        "i checked, but i couldn't ",
    )
    return any(lowered.startswith(prefix) for prefix in failure_prefixes)


def _assistant_text_unavailable(assistant_text: str, request_id: str) -> bool:
    """A8 serve-time re-verification: is this assistant payload WITHHELD/ERASED?

    Lineage first (the event's governing request), content-hash verdict second
    (covers legacy pre-lineage turns via the erasure digest tombstone). No
    read-once-trust: the availability verdict is re-read on every hydration.
    """
    try:
        from core.finalization import (
            AVAILABILITY_ERASED,
            AVAILABILITY_WITHHELD,
            get_finalization_by_request_id,
            payload_availability_for_text,
        )
        from core.grounding_publication import is_typed_refusal_text

        # A published grounding REFUSAL is not answer content: it is the runtime saying it
        # declined, and it names the declined request verbatim so the reader knows which ask was
        # refused. That echo is right in front of a reader and wrong in a later prompt, and the
        # harm is the one this gate already exists to prevent -- "the model would re-quote or
        # re-synthesize it". Measured live 2026-09-09 on the built app, three turns, one chat:
        #
        #   U: Compare electric cars and gasoline cars on purchase cost, range and emissions.
        #   A: I can't publish an answer to this ... (request: "Compare electric cars and ...")
        #   U: Answer in exactly 3 bullet points: why is the sky blue?
        #   A: - Electric cars typically have higher purchase costs than gasoline cars ...
        #
        # The next question was answered with the PREVIOUS question's subject, silently. It
        # reproduces only after a refusal: the same question in a fresh session, and after an
        # ANSWERED turn, both answer the sky correctly. The runtime had asked the right question --
        # the runtime events show no rewrite -- so what carried the old request forward was this
        # hydration replaying the refusal, request echo and all, as though it were an answer.
        # A refusal is available to the READER; it is not material for the next prompt.
        if is_typed_refusal_text(assistant_text):
            return True

        if str(request_id or "").strip():
            try:
                row = get_finalization_by_request_id(
                    str(request_id), principal="owner_local"
                )
            except Exception:
                row = None
            if row is not None:
                availability = str(row.get("availability") or "")
                return availability in (AVAILABILITY_WITHHELD, AVAILABILITY_ERASED)
        verdict = payload_availability_for_text(assistant_text)
        return verdict in (AVAILABILITY_WITHHELD, AVAILABILITY_ERASED)
    except Exception:
        # PASS-002 fail-closed with absence discrimination: when an A8 store
        # is HOSTED but cannot answer, uncertainty never resolves into
        # serving governed content (suppress). When no A8 store is hosted
        # here at all, these bytes are ungoverned legacy truth.
        from core.finalization import governance_store_ready

        return governance_store_ready()


def augment_history_from_session_log(
    history: list[dict[str, str]] | None,
    *,
    session_id: str,
    user_text: str,
    limit: int = 6,
) -> list[dict[str, str]]:
    normalized_history = [dict(item) for item in list(history or []) if isinstance(item, dict)]
    from core.memory.revocation import authorize_payload

    current = normalized_history[-1:] if normalized_history and (
        normalized_history[-1].get("role") == "user"
        and str(normalized_history[-1].get("content") or "").strip() == str(user_text or "").strip()
    ) else []
    historical = normalized_history[:-1] if current else normalized_history
    if historical and not authorize_payload(historical, session_id):
        return current
    if len(normalized_history) > 1:
        # A9/A8: client-held transcripts must clear the same erasure gate as hydrated
        # session-log events. An ERASED payload that survives only in the caller's copy
        # otherwise re-enters prompts here and decays erasure back into persisted outputs.
        from core.context_history_authority import (
            assistant_text_is_runtime_failure_notice,
            closed_exchange_marker,
            failed_exchange_marker,
        )

        gated: list[dict[str, str]] = []
        for item in normalized_history:
            content = str(item.get("content") or "")
            if (
                str(item.get("role") or "").strip().lower() == "assistant"
                and _assistant_text_unavailable(content, "")
            ):
                # The payload is suppressed; the exchange is CLOSED, not left open. Dropping this
                # row alone stranded the user's request as an unanswered ask and the model served
                # it on a later turn -- defeating the very refusal that suppressed the payload.
                gated.append(closed_exchange_marker())
                continue
            # A runtime NO-ANSWER notice closes its exchange: in a prompt it holds the failed
            # request open beside the current message (2026-09-15 incident), so the model must
            # see the lifecycle, not the notice.
            if (
                str(item.get("role") or "").strip().lower() == "assistant"
                and assistant_text_is_runtime_failure_notice(content)
            ):
                gated.append(failed_exchange_marker())
                continue
            # A stopped turn's marker (painted by the page or by /api/chat/history) is status, not
            # a reply: the exchange is closed, and the model never reads the marker as its words.
            if str(item.get("role") or "").strip().lower() == "assistant" and _is_stopped_marker(content):
                gated.append(closed_exchange_marker())
                continue
            gated.append(item)
        return gated
    normalized_session = str(session_id or "").strip()
    normalized_user = str(user_text or "").strip()
    if not normalized_session or not normalized_user:
        return normalized_history

    hydrated_history: list[dict[str, str]] = []
    for event in recent_conversation_events(normalized_session, limit=max(1, int(limit))):
        if not isinstance(event, dict):
            continue
        event_user = str(event.get("user") or "").strip()
        event_assistant = str(event.get("assistant") or "").strip()
        if event_user:
            hydrated_history.append({"role": "user", "content": event_user})
        if not event_assistant and str(event.get(TRANSCRIPT_COMMIT_STATE_FIELD) or "") == TRANSCRIPT_COMMIT_STATE_STOPPED:
            # The person stopped this turn: no reply exists, and the request is not left open.
            from core.context_history_authority import closed_exchange_marker

            hydrated_history.append(closed_exchange_marker())
            continue
        if event_assistant:
            # A8 availability gate (R2 resurrection): a WITHHELD/ERASED payload
            # must never re-enter a live prompt — the model would re-quote or
            # re-synthesize it. Lineage (request_id) is authoritative; the
            # content-hash verdict covers legacy unlineaged turns.
            if _assistant_text_unavailable(event_assistant, str(event.get("request_id") or "")):
                from core.context_history_authority import closed_exchange_marker

                hydrated_history.append(closed_exchange_marker())
                continue
            # A runtime NO-ANSWER notice closes its exchange rather than re-entering the next
            # prompt holding the failed request open (2026-09-15 incident).
            from core.context_history_authority import (
                assistant_text_is_runtime_failure_notice,
                failed_exchange_marker,
            )

            if assistant_text_is_runtime_failure_notice(event_assistant):
                hydrated_history.append(failed_exchange_marker())
                continue
            hydrated_history.append({"role": "assistant", "content": event_assistant})

    if not hydrated_history:
        return normalized_history

    if normalized_history:
        last_message = normalized_history[-1]
        if (
            str(last_message.get("role") or "").strip().lower() == "user"
            and str(last_message.get("content") or "").strip() == normalized_user
        ):
            return [*hydrated_history, *normalized_history]
    return [*hydrated_history, {"role": "user", "content": normalized_user}]


def memory_lifecycle_snapshot(
    *,
    session_id: str,
    query_text: str = "",
    topic_hints: list[str] | None = None,
    recent_limit: int = 6,
    memory_limit: int = 4,
    heuristic_limit: int = 4,
    summary_limit: int = 3,
    access_policy: ContextAccessPolicy | None = None,
) -> dict[str, Any]:
    ensure_memory_files()
    normalized_session = str(session_id or "").strip()
    if not normalized_session:
        raise ValueError("session_id is required")
    resolved_policy = memory_entries.resolve_memory_access_policy(
        access_policy=access_policy,
        chat_id=normalized_session,
    )
    recent = recent_conversation_events(normalized_session, limit=max(1, int(recent_limit))) if normalized_session else []
    recent_user_turns = [
        str(item.get("user") or "").strip()
        for item in recent
        if str(item.get("user") or "").strip()
    ]
    inferred_query = str(query_text or "").strip()
    if not inferred_query and recent_user_turns:
        inferred_query = " ".join(recent_user_turns[-2:])
    normalized_topic_hints = [
        str(item).strip()
        for item in list(topic_hints or [])
        if str(item).strip()
    ]
    if not normalized_topic_hints and inferred_query:
        normalized_topic_hints = _keyword_tokens(inferred_query, limit=6)

    skip_durable_selection = _should_skip_durable_snapshot_selection(
        query_text=inferred_query,
        recent_user_turns=recent_user_turns,
    )
    relevant_memory: list[dict[str, Any]] = []
    session_summaries: list[dict[str, Any]] = []
    heuristics: list[dict[str, Any]] = []
    if not skip_durable_selection and inferred_query:
        relevant_memory = [
            row
            for row in search_relevant_memory(
                inferred_query,
                access_policy=resolved_policy,
                topic_hints=normalized_topic_hints,
                limit=max(1, int(memory_limit)),
            )
            if float(row.get("score") or 0.0) >= 0.35
        ]
        session_summaries = [
            row
            for row in search_session_summaries(
                inferred_query,
                access_policy=resolved_policy,
                topic_hints=normalized_topic_hints,
                limit=max(1, int(summary_limit)),
                exclude_session_id=normalized_session or None,
            )
            if float(row.get("score") or 0.0) >= 0.45
        ]
    if not skip_durable_selection and (inferred_query or normalized_topic_hints):
        heuristics = [
            row
            for row in search_user_heuristics(
                inferred_query,
                access_policy=resolved_policy,
                topic_hints=normalized_topic_hints,
                limit=max(1, int(heuristic_limit)),
            )
            if float(row.get("score") or 0.0) >= 0.40
        ]
    dense_profile = (
        dict(load_operator_dense_profile() or {})
        if resolved_policy.allow_user_profile_context
        else {}
    )
    policy = session_memory_policy(normalized_session)

    recent_turns = [
        {
            "ts": str(item.get("ts") or "").strip(),
            "user": _trim_text(str(item.get("user") or ""), 180),
            "assistant": _trim_text(str(item.get("assistant") or ""), 220),
        }
        for item in recent
    ]
    selection_summary = (
        f"query `{_trim_text(inferred_query or 'recent session context', 90)}` selected "
        f"{len(relevant_memory)} durable memory entries, "
        f"{len(session_summaries)} prior session summaries, and "
        f"{len(heuristics)} heuristic signals."
    )
    return {
        "session_id": normalized_session,
        "selection_query": inferred_query,
        "topic_hints": normalized_topic_hints,
        "share_scope": str(policy.get("share_scope") or "local_only"),
        "realm_label": str(policy.get("realm_label") or share_scope_label(policy.get("share_scope"))),
        "restricted_terms": list(policy.get("restricted_terms") or []),
        "recent_conversation_event_count": len(recent),
        "recent_turns": recent_turns,
        "recent_user_turns": recent_user_turns[-3:],
        "relevant_memory_count": len(relevant_memory),
        "relevant_memory": [
            {
                "text": _trim_text(str(item.get("text") or ""), 180),
                "category": str(item.get("category") or "").strip(),
                "score": float(item.get("score") or 0.0),
                "share_scope": str(item.get("share_scope") or "").strip(),
            }
            for item in relevant_memory
        ],
        "session_summary_count": len(session_summaries),
        "session_summaries": [
            {
                "session_id": str(item.get("session_id") or "").strip(),
                "summary": _trim_text(str(item.get("summary") or ""), 220),
                "score": float(item.get("score") or 0.0),
            }
            for item in session_summaries
        ],
        "heuristic_count": len(heuristics),
        "user_heuristics": [
            {
                "category": str(item.get("category") or "").strip(),
                "signal": str(item.get("signal") or "").strip(),
                "text": _trim_text(str(item.get("text") or ""), 140),
                "score": float(item.get("score") or 0.0),
                "mentions": int(item.get("mentions") or 0),
            }
            for item in heuristics
        ],
        "dense_profile": {
            "dense_summary": _trim_text(str(dense_profile.get("dense_summary") or ""), 280),
            "response_style": list(dense_profile.get("response_style") or []),
            "source_preferences": list(dense_profile.get("source_preferences") or []),
            "preferred_stacks": list(dense_profile.get("preferred_stacks") or []),
            "active_projects": list(dense_profile.get("active_projects") or []),
            "last_session_id": str(dense_profile.get("last_session_id") or "").strip(),
        },
        "selection_summary": selection_summary,
    }


def _should_skip_durable_snapshot_selection(
    *,
    query_text: str,
    recent_user_turns: list[str],
) -> bool:
    normalized_query = " ".join(str(query_text or "").lower().split())
    if any(marker in normalized_query for marker in _SNAPSHOT_UTILITY_MARKERS):
        return True
    query_tokens = set(_keyword_tokens(normalized_query, limit=8))
    if query_tokens and query_tokens.issubset(_SNAPSHOT_GENERIC_OPERATION_TOKENS):
        return True
    recent_tokens = set(_keyword_tokens(" ".join(recent_user_turns[-3:]), limit=24))
    return bool(
        query_tokens
        and query_tokens.issubset(_SNAPSHOT_GENERIC_OPERATION_TOKENS)
        and recent_tokens & _SNAPSHOT_GENERIC_OPERATION_TOKENS
    )


def _capture_object_is_another_lanes_effect(verb: str, text: str) -> bool:
    """Whether this storage request's OBJECT is an effect the operator lane owns.

    Measured on the served path (revision-5 served journeys, first run): 'save a note to Apple Notes
    titled "Night shift handover" with: ...' was answered "Locked in. I'll remember that." -- no note,
    no delivery attempt, and a memory the user did not ask for -- because this lane runs before the
    operator lane and `save` is one of its capture verbs. `_WORKSPACE_SAVE_RE` already hands file saves
    on; this is the same rule for every effect, decided by the operator lane's OWN parser rather than a
    second vocabulary kept here.

    Two limits keep statements memories. Only the object-taking verbs qualify: "remember that I save a
    note for the landlord every Friday" is a statement about a habit, although its words parse as a note
    save. And only effects count: a storage verb never asks for a read, so "keep in mind I am usually
    free Tuesday afternoon for a slot" stays a memory although the availability reader recognizes its
    words.
    """
    if " ".join(str(verb or "").casefold().split()) not in _OBJECT_CAPTURE_VERBS:
        return False
    from core.operator.models import READ_ONLY_OPERATOR_KINDS
    from core.operator.parser import parse_operator_action_intent

    intent = parse_operator_action_intent(text)
    return intent is not None and intent.kind not in READ_ONLY_OPERATOR_KINDS


def _forget_start_failure(user_text, *, session_id, source_context, started):
    reply = ("Forget could not be verified as fully applied: independent revocation authority is unavailable. Please retry the forget."
             if started else
             "Nothing was forgotten. The forget did not start because its revocation barrier could not be verified. Please try again.")
    parsed = _FORGET_RE.match(user_text)
    return True, _forget_outcomes.record(
        reply, request=user_text, session=session_id or "", source_context=source_context,
        argument=parsed.group(1) if parsed else user_text, status="failed" if started else "not_started",
        stores={"independent_revocation": {"status": "incomplete", "started": bool(started)}},
    )


def _preadmit_forget_turn(user_text, *, session_id, source_context):
    """Persist admission before turn adaptation, checkpoints or profile effects."""
    from contextlib import ExitStack
    from hashlib import sha256
    from pathlib import Path

    from core.memory import revocation
    from core.memory.command_result import _owner_identity, forget_demand_targets, requires_forget_outcome
    from core.runtime_paths import active_vool_home
    from core.within_turn_retraction import intake_request_text

    live_text = intake_request_text(user_text)
    if not requires_forget_outcome({}, user_input=live_text, source_context=source_context):
        return False, None
    owner = dict(request=user_text, session=session_id, source_context=source_context)
    if _forget_outcomes.admission(**owner) is not None:
        return True, None
    identity = _owner_identity(user_text, session_id, source_context)
    if not identity:
        return True, _forget_start_failure(user_text, session_id=session_id, source_context=source_context, started=False)[1]
    runtime_home = str((source_context or {}).get("runtime_home") or "").strip()
    homes = {active_vool_home(), Path(runtime_home).resolve() if runtime_home else active_vool_home()}
    operations = []
    marker = sha256(repr((session_id, sorted(forget_demand_targets(user_text)))).encode("utf-8")).hexdigest()
    try:
        with ExitStack() as locks:
            for home in sorted(homes):
                locks.enter_context(revocation.serialized(home))
            operations = revocation.begin_group(session_id, marker, homes, _kind="admission")
            _forget_outcomes.admit(operations, **owner)
    except Exception as exc:
        if operations:
            _forget_outcomes.admit(operations, **owner)
        return True, _forget_start_failure(
            user_text, session_id=session_id, source_context=source_context,
            started=bool(operations) or getattr(exc, "started", False),
        )[1]
    return True, None


def _finish_forget_turn_admission(result, *, user_text, session_id, source_context):
    """Only the canonical terminal with executor-owned completion retires admission."""
    from contextlib import ExitStack

    from core.memory import revocation
    from core.memory.command_result import verified_forget_outcome

    if (source_context or {}).get("planned_subturn"):
        return result
    owner = dict(request=user_text, session=session_id, source_context=source_context)
    operations = _forget_outcomes.admission(**owner)
    if operations is None:
        return result
    execution = verified_forget_outcome(
        result, user_input=user_text, session_id=session_id, source_context=source_context,
    )
    if execution is None or not _forget_outcomes.admission_ready(**owner):
        return result
    try:
        with ExitStack() as locks:
            for home, _ in sorted(operations):
                locks.enter_context(revocation.serialized(home))
            for home, operation in operations:
                revocation.complete(operation, home)
            _forget_outcomes.release_admission(**owner)
    except Exception:
        _, failure = _forget_start_failure(user_text, session_id=session_id, source_context=source_context, started=True)
        return {**result, "response": failure, "_memory_command_outcome": failure}
    return result


def _command_erasure_fence(fn):
    import functools
    @functools.wraps(fn)
    def wrapped(user_text, **kwargs):
        if not _FORGET_RE.match(str(user_text or "").strip()):
            return fn(user_text, **kwargs)
        from core.memory.revocation import poison, serialized
        context = kwargs.get("source_context") or {}
        home = context.get("runtime_home")
        entered = False
        try:
            from contextlib import ExitStack
            from pathlib import Path

            from core.runtime_paths import active_vool_home
            homes = {active_vool_home(), Path(home).resolve() if home else active_vool_home()}
            with ExitStack() as stack:
                for selected_home in sorted(homes):
                    stack.enter_context(serialized(selected_home))
                entered = True
                return fn(user_text, **kwargs)
        except Exception as exc:
            started = entered and getattr(exc, "started", True)
            if started:
                poison(home)
            return _forget_start_failure(
                user_text, session_id=kwargs.get("session_id"),
                source_context=kwargs.get("source_context"), started=started,
            )
    return wrapped


@_command_erasure_fence
def maybe_handle_memory_command(
    user_text: str,
    *,
    session_id: str | None = None,
    access_policy: ContextAccessPolicy | None = None,
    source_context: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    text = str(user_text or "").strip()
    if not text:
        return False, ""
    lowered = text.lower()
    scope_command = _parse_session_scope_command(text)
    if not (
        scope_command is not None
        or lowered in {"/memory", "what do you remember", "show memory"}
        or _memory_capture_match(text)
        or _CORRECTION_RE.match(text)
        or _FORGET_RE.match(text)
        or _is_memory_recall_question(text)
    ):
        return False, ""
    capture_match = _memory_capture_match(text)
    if capture_match and _ANSWER_REQUEST_RE.search(capture_match.group("fact")):
        return False, ""
    if capture_match:
        # A saved contact is the Contacts owner's object, read by that owner. A "save X as a contact" request
        # must reach the model's contacts tools (bounded by request provenance), never the memory lane -- not
        # even when memory access is unavailable, which used to claim the turn with a refusal instead.
        # Composed with the operator lane's OWN object parser (the calendar/notes chain's law): a storage
        # request whose OBJECT is an operator-lane effect ("save a note to Apple Notes titled ... with: ...")
        # is that lane's turn, and claiming it here answered "Locked in" with no note and no delivery.
        from core.contacts.requests import storage_object_is_a_contact

        if storage_object_is_a_contact(text):
            return False, ""
        if _capture_object_is_another_lanes_effect(capture_match.group("verb"), text):
            return False, ""
    if capture_match or _CORRECTION_RE.match(text):
        # Private chat (core.private_chat): nothing from a private chat is saved, an explicit
        # "remember" included. Said plainly instead of claiming a save that never happens.
        from core.private_chat import REPLY_REMEMBER_REFUSED, localized_reply, turn_is_private

        if turn_is_private(source_context, session_id):
            return True, localized_reply(REPLY_REMEMBER_REFUSED, source_context)
    independent_groups: list[list[Any]] = []
    parsed_forget = _FORGET_RE.match(text)
    forget_arguments = _forget_command_arguments(text)
    if parsed_forget:
        from pathlib import Path

        from core.memory import revocation
        from core.runtime_paths import active_vool_home
        runtime_home = str((source_context or {}).get("runtime_home") or "").strip() or None
        barrier_chat = str(getattr(access_policy, "chat_id", None) or session_id or (source_context or {}).get("chat_id") or "")
        homes = {active_vool_home(), Path(runtime_home).resolve() if runtime_home else active_vool_home()}
        try:
            # Durable before even policy resolution can open an owner database. A later target's barrier starts
            # when that target's erasure does: a pending barrier fences every write carrying its words, so it
            # would refuse the rewrite of the stores the earlier target is erased from.
            independent_groups.append(revocation.begin_group(
                barrier_chat, _normalize_forget_target(forget_arguments[0]), homes,
            ))
        except Exception as exc:
            return _forget_start_failure(
                user_text, session_id=barrier_chat, source_context=source_context,
                started=(any(independent_groups) or getattr(exc, "started", True)
                         or _forget_outcomes.admission(request=user_text, session=barrier_chat, source_context=source_context) is not None),
            )
    try:
        resolved_policy = memory_entries.resolve_memory_access_policy(
            access_policy=access_policy,
            chat_id=session_id,
        )
    except (TypeError, ValueError):
        return True, "I need an active chat before I can access memory."
    resolved_session = resolved_policy.chat_id

    if scope_command is not None:
        action = str(scope_command.get("action") or "")
        if action == "show":
            return True, describe_session_memory_policy(resolved_session)
        result = set_session_memory_policy(
            resolved_session,
            share_scope=str(scope_command.get("share_scope") or "local_only"),
            restricted_terms=list(scope_command.get("restricted_terms") or []),
        )
        scope = result["share_scope"]
        if scope == "hive_mind":
            label = "SHARED PACK"
            scope_note = "Generalized learnings from this session can sync to the mesh after secret screening. Raw chat remains in the PRIVATE VAULT."
        elif scope == "public_knowledge":
            label = "HIVE/PUBLIC COMMONS"
            scope_note = "Generalized learnings from this session can be published as public claims after secret screening. Raw chat remains in the PRIVATE VAULT."
        else:
            label = "PRIVATE VAULT"
            scope_note = "Everything from this session stays on this node unless you reclassify it."
        protected = ""
        if result.get("restricted_terms"):
            protected = " Protected exceptions: " + ", ".join(list(result["restricted_terms"])[:6]) + "."
        stats = (
            f" Existing session shards updated: {int(result.get('updated_shards') or 0)}"
            f", shared now: {int(result.get('registered_shards') or 0)}"
            f", forced local by privacy guard: {int(result.get('blocked_shards') or 0)}."
        )
        return True, f"Session scope set to {label}. {scope_note}{protected}{stats}"
    if lowered in {"/memory", "what do you remember", "show memory"}:
        summary = summarize_memory(
            access_policy=resolved_policy,
            limit=8,
        )
        if not summary:
            return True, "Memory is currently empty. Tell me what to remember."
        return True, "What I remember:\n" + "\n".join(f"- {line}" for line in summary)

    if _is_memory_recall_question(text):
        # An ordinal question is about the transcript, and this lane cannot see the transcript.
        # It is declined before the store is consulted at all, because a relevance-ranked hit
        # dressed up as "Active remembered value" answers a DIFFERENT question than the one asked.
        if _asks_about_transcript_order(text):
            return False, ""
        asked_for_an_inventory = _memory_recall_empty_result_is_explicit(text) or any(
            marker in text.casefold() for marker in _OTHER_CHAT_MEMORY_MARKERS
        )
        response = _memory_recall_response(
            text,
            access_policy=resolved_policy,
        )
        if response in _EMPTY_RECALL_ANSWERS and not asked_for_an_inventory:
            # Reporting an ABSENCE is only this lane's business when the user asked it for an
            # inventory. Otherwise an empty lookup means "this store does not hold it" -- never
            # "you never said it" -- and the thing that was said is in the conversation, which the
            # capsule path and the model can both read and this lane cannot.
            #
            # This subsumes the older empty-store guard: an empty store produces an empty lookup,
            # so the same turns are still declined. What changes is the NON-empty store holding
            # nothing relevant, which used to be answered "I don't have an active remembered value
            # matching that request in this chat" with model_ran=False.
            return False, ""
        return True, response

    correction = _CORRECTION_RE.match(text)
    if correction:
        correction_body = str(correction.group("body") or "").lstrip(" :,;-")
        values = _correction_values(correction_body)
        if values:
            memory_scope = _memory_scope_for_user_fact(
                correction_body,
                policy=resolved_policy,
            )
            correction_subject = next(
                (
                    marker
                    for marker in (
                        "identifier",
                        "code",
                        "marker",
                    "route",
                    "label",
                    "port",
                    "budget",
                    "phrase",
                    "note",
                    "date",
                    )
                    if re.search(
                        rf"\b{re.escape(marker)}\b",
                        correction_body,
                        re.IGNORECASE,
                    )
                ),
                "",
            )
            for old_value in values[1:]:
                try:
                    forget_memory(old_value, access_policy=resolved_policy)
                except memory_entries.MemoryErasureError:
                    return True, (
                        "Correction could not be fully applied: the previous value could not be "
                        "verified as erased from every memory path. Please retry the correction."
                    )
                try:
                    from core.context_retrieval import forget_session_memory

                    forget_session_memory(
                        resolved_session,
                        old_value,
                        access_policy=resolved_policy,
                        source_context=source_context,
                    )
                except Exception as exc:
                    LOGGER.warning("semantic correction cleanup unavailable: %s", type(exc).__name__)
            correction_fact_key = memory_entries.derive_fact_key(
                correction_body,
                category="fact",
            )
            correction_label = correction_subject or "value"
            add_memory_fact(
                f"Current corrected {correction_label}: {values[0]}",
                session_id=resolved_session,
                scope=memory_scope,
                authority="user_correction",
                fact_key=correction_fact_key,
                access_policy=resolved_policy,
            )
            return True, f"Correction applied. Current value: {values[0]}."

    remember = capture_match
    if remember and not _is_a_reminiscence(remember.group("fact"), text):
        verb = str(remember.group("verb") or "").casefold()
        fact = _MEMORY_CAPTURE_TRAILER_RE.sub(
            "",
            remember.group("fact").strip(),
        ).strip()
        if _WORKSPACE_SAVE_RE.match(text):
            return False, ""
        if verb not in {"remember", "remember that", "note", "note that", "store", "store this"} and _NON_MEMORY_CAPTURE_RE.match(fact):
            return False, ""
        if len(fact) < 3:
            return True, "Memory update skipped: fact is too short."
        facts = _split_explicit_memory_facts(fact)
        added_count = 0
        refused = False
        for memory_fact in facts:
            for eligible_fact, eligible_scope, eligible_source in _admitted_remember_facts(
                memory_fact,
                policy=resolved_policy,
            ):
                try:
                    added_count += int(
                        add_memory_fact(
                            " ".join(eligible_fact.split()),
                            session_id=resolved_session,
                            scope=eligible_scope,
                            authority="confirmed_memory",
                            source=eligible_source,
                            access_policy=resolved_policy,
                        )
                    )
                except RevokedContentWrite:
                    # The forget law holds in this chat; it is an answer, not a failed turn (measured 2026-10-14:
                    # saying a forgotten fact again here ended the turn with HTTP 500).
                    refused = True
        if refused:
            kept = "Locked in for the rest. " if added_count else ""
            return True, (kept + "You asked me to forget that in this chat, so I won't keep it here. "
                          "In a new chat I can remember it again.")
        if added_count:
            if (source_context or {}).get("_secret_intake_consumed"):
                # The same reply already says a PIN, card number or password was NOT saved; a bare
                # "I'll remember that" beside it reads as if the secret was. Say what was kept.
                return True, f"Locked in. I’ll remember \"{'; '.join(facts).rstrip('.!?')}\"."
            return True, "Locked in. I’ll remember that."
        return True, "I already had that in memory."

    if parsed_forget:
        def forget_target(argument: str, independent_operations: list[Any]) -> tuple[str, str, dict[str, Any]]:
            """Forget one named target; the reply, its status and the erasure stores."""
            erasures: dict[str, Any] = {}

            def outcome(reply: str, status: str) -> tuple[str, str, dict[str, Any]]:
                return reply, status, erasures

            referential, token = _referential_forget_token(
                argument,
                access_policy=resolved_policy,
            )
            if referential and not token:
                return outcome("Forget command needs the exact value because more than one matching memory is active.", "needs_target")
            if not referential:
                token = _normalize_forget_target(argument)
                try:
                    described, value = _described_forget_value(
                        token, access_policy=resolved_policy, chat_id=resolved_session,
                        runtime_home=str((source_context or {}).get("runtime_home") or "").strip() or None)
                except Exception as exc:
                    LOGGER.warning("described forget target could not be resolved: %s", type(exc).__name__)
                    described, value = "", ""
                if described == "ambiguous":
                    return outcome("Forget command needs the exact value because more than one matching memory is active.", "needs_target")
                if described == "value":
                    token = value
                    referential = True
            if referential and token:
                for home, _op in list(independent_operations):
                    independent_operations.append((home, revocation.begin(resolved_session, token, home)))
            if len(token) < 2:
                return outcome("Forget command skipped: provide a clearer keyword.", "needs_target")
            # Already forgotten (review 2026-10-07, crash-heal path): the canonical row is a tombstone, so the forget
            # removes 0, but the mirror may still carry the plaintext and is healed by this very forget. That outcome
            # is confirmed as "already forgotten", never reported as a no-match and never as "Forget applied".
            from core.memory.entries import _record_erasure_store, _text_is_erased
            from core.memory.files import memory_path

            def _mirror_text() -> str:
                try:
                    return memory_path().read_text(encoding="utf-8")
                except FileNotFoundError:
                    return ""

            operation_stores: list[tuple[str, Any, str]] = []
            owner_stores: dict[str, Any] = {}
            resuming = False
            try:
                from core.context_retrieval import _open_memory, _open_memory_for_runtime

                runtime_home = str((source_context or {}).get("runtime_home") or "").strip() or None
                by_database: dict[str, Any] = {}
                for owner, opener in (
                    ("semantic", lambda: _open_memory_for_runtime(runtime_home)),
                    ("structured", _open_memory),
                ):
                    operation_store = opener()
                    if operation_store is None:
                        raise RuntimeError("erasure operation store is unavailable")
                    database = str(operation_store.db_path.resolve())
                    if database in by_database:
                        if operation_store is not by_database[database]:
                            operation_store.close()
                        owner_stores[owner] = by_database[database]
                        continue
                    by_database[database] = operation_store
                    owner_stores[owner] = operation_store
                    operation_stores.append((owner, operation_store, ""))
                    # Read before this forget opens its own operation: an operation still open is an earlier forget
                    # of these words that did not finish, and this one completes it.
                    resuming = resuming or operation_store._erasure_pending(resolved_session, token)
                    operation_id = operation_store.begin_erasure(resolved_session, token)
                    operation_stores[-1] = (owner, operation_store, operation_id)
            except Exception as exc:
                LOGGER.warning("erasure operation could not start: %s", type(exc).__name__)
                _record_erasure_store(erasures, "erasure_operations", "incomplete")
                for owner, operation_store, _operation_id in operation_stores:
                    _record_erasure_store(erasures, f"{owner}_erasure_operations", "incomplete")
                    try:
                        operation_store.close()
                    except Exception as close_exc:
                        LOGGER.warning("erasure connection close failed: %s", type(close_exc).__name__)
                return outcome("Forget could not be verified as fully applied: erasure operations could not be recorded. Please retry the forget.", "failed")

            removed = 0
            mirror_before = ""
            mirror_after = ""
            already_erased = False
            try:
                mirror_before = _mirror_text().lower()
                already_erased = _text_is_erased(token) or _text_is_erased(token.rstrip(".!?") + ".")
                removed = forget_memory(
                    token,
                    access_policy=resolved_policy,
                    erasures=erasures,
                )
            except Exception as exc:
                if not any(key.endswith("_failures") for key in erasures):
                    _record_erasure_store(erasures, "structured_entries", "incomplete")
                LOGGER.warning("structured memory erasure failed: %s", type(exc).__name__)
            try:
                mirror_after = _mirror_text().lower()
            except Exception as exc:
                _record_erasure_store(erasures, "memory_mirror", "incomplete")
                LOGGER.warning("memory mirror verification failed: %s", type(exc).__name__)
            semantic_removed = 0
            try:
                from core.context_retrieval import forget_session_memory

                semantic_removed = forget_session_memory(
                    resolved_session,
                    token,
                    access_policy=resolved_policy,
                    source_context=source_context,
                    store_status=erasures,
                )
            except Exception as exc:
                LOGGER.warning("semantic memory forget unavailable: %s", type(exc).__name__)
                _record_erasure_store(erasures, "semantic_nodes", "incomplete")
            memory_entries.forget_profile_derivatives(token, access_policy=resolved_policy, erasures=erasures)
            _forget_persisted_copies(token, chat_id=resolved_session, erasures=erasures)
            try:
                from storage.swarm_memory import _forget_sniffed_copies

                _forget_sniffed_copies(token, resolved_session, erasures)
            except Exception as exc:
                LOGGER.warning("swarm dataset erasure failed: %s", type(exc).__name__)
                from core.memory.entries import _record_erasure_store

                _record_erasure_store(erasures, "swarm_sniffed_context", "incomplete")
            from core.turn_context import forget_chat_context_pages

            forget_chat_context_pages(chat_id=resolved_session, token=token, erasures=erasures)
            if resolved_session:
                # A long paste is kept as a document of the chat and carried into every later turn.
                try:
                    from core.chat_attachments import forget_documents_carrying
                    from core.memory.carriers import carries as carries_forget_token

                    erased_documents = forget_documents_carrying(
                        session_id=resolved_session, carries=lambda words: carries_forget_token(words, token))
                    _record_erasure_store(erasures, "chat_documents", "verified", erased_documents)
                except Exception as exc:
                    LOGGER.warning("chat document erasure failed: %s", type(exc).__name__)
                    _record_erasure_store(erasures, "chat_documents", "incomplete")
            else:
                _record_erasure_store(erasures, "chat_documents", "not_applicable")
            from core.memory.run_history import STORE_PREFIX as RUN_HISTORY_PREFIX
            from core.memory.run_history import UNCLASSIFIED_PREFIX, checkpoint_write_ahead_logs, forget_run_history

            # The runtime's record of each turn (events, checkpoints, attempts, obligation sets, finalized
            # answers, notifications) held the forgotten words after "Forget applied" (measured 2026-10-14).
            forget_run_history(token, chat_id=resolved_session, erasures=erasures, command=text)
            for owner, operation_store in owner_stores.items():
                block_status: dict[str, Any] = {}
                try:
                    block_removed = operation_store._erase_forget_blocks(token)
                    operation_store.verify_forget_blocks(token, erasures=block_status)
                    _record_erasure_store(block_status, "memory_blocks", "verified", block_removed)
                except Exception as exc:
                    LOGGER.warning("prompt block verification failed: %s", type(exc).__name__)
                result = block_status.get("stores", {}).get("memory_blocks", {"status": "incomplete"})
                _record_erasure_store(erasures, f"{owner}_memory_blocks", result["status"], int(result.get("removed") or 0))
            for owner, operation_store in owner_stores.items():
                # The erased rows' terms leave the full-text index pages only when its segments merge.
                try:
                    operation_store._compact_full_text_indexes()
                    _record_erasure_store(erasures, f"{owner}_full_text_indexes", "verified")
                except Exception as exc:
                    LOGGER.warning("full-text index compaction failed: %s", type(exc).__name__)
                    _record_erasure_store(erasures, f"{owner}_full_text_indexes", "incomplete")
            required_stores = (
                "structured_entries", "structured_summaries", "memory_mirror", "semantic_nodes",
                "source_occurrences", "memory_receipts", "conversation_log", "revocation_ledger",
                "dialogue_derivatives", "session_summaries", "context_capsule_versions",
                "memory_fts", "source_fts", "source_embeddings",
                "user_heuristics", "operator_dense_profile", "persisted_packet_cache_copies", "packet_receipt_ledgers",
                "context_pages", "chat_session_metadata", "swarm_sniffed_context", "run_history", "chat_documents",
            )
            for store in required_stores:
                if store not in erasures.get("stores", {}):
                    _record_erasure_store(erasures, store, "incomplete")
            for owner, operation_store in owner_stores.items():
                try:
                    if erasures.get("stores", {}).get("revocation_ledger", {}).get("status") != "verified":
                        _record_erasure_store(erasures, f"{owner}_revocation_ledger", "incomplete")
                        continue
                    operation_store.record_revocation(token, chat_id=resolved_session)
                    from core.context_retrieval import _normalize_forget_token as normalize_revocation

                    if normalize_revocation(token) not in operation_store.revoked_tokens(resolved_session, strict=True):
                        raise RuntimeError("owner revocation write could not be verified")
                    _record_erasure_store(erasures, f"{owner}_revocation_ledger", "verified")
                except Exception as exc:
                    _record_erasure_store(erasures, f"{owner}_revocation_ledger", "incomplete")
                    LOGGER.warning("erasure owner revocation failed: %s", type(exc).__name__)
            from core.memory.carriers import verify_forget_stores
            verify_forget_stores(resolved_session, token, owner_memories=owner_stores, erasures=erasures)
            # Last: the erased rows' old page images must not outlive the erasure in the -wal files.
            checkpoint_write_ahead_logs(erasures, memories=by_database.values())
            completed_stores = []
            try:
                if not any(key.endswith("_failures") and value for key, value in erasures.items()):
                    for _owner, operation_store, operation_id in operation_stores:
                        operation_store.complete_erasure(operation_id)
                        completed_stores.append(operation_store)
                    for home, operation_id in independent_operations:
                        revocation.complete(operation_id, home)
                    _record_erasure_store(erasures, "independent_revocation", "verified")
                    for owner in owner_stores:
                        _record_erasure_store(erasures, f"{owner}_erasure_operations", "verified")
                    _record_erasure_store(erasures, "erasure_operations", "verified")
                else:
                    for owner in owner_stores:
                        _record_erasure_store(erasures, f"{owner}_erasure_operations", "incomplete")
                    _record_erasure_store(erasures, "erasure_operations", "incomplete")
            except Exception as exc:
                LOGGER.warning("erasure completion could not be verified: %s", type(exc).__name__)
                for operation_store in completed_stores:
                    try:
                        operation_store.begin_erasure(resolved_session, token)
                    except Exception as restore_exc:
                        LOGGER.warning("erasure owner barrier restore failed: %s", type(restore_exc).__name__)
                for owner in owner_stores:
                    _record_erasure_store(erasures, f"{owner}_erasure_operations", "incomplete")
                _record_erasure_store(erasures, "erasure_operations", "incomplete")
            finally:
                for owner, operation_store, _operation_id in operation_stores:
                    try:
                        operation_store.close()
                    except Exception as exc:
                        LOGGER.warning("verified erasure connection close failed: %s", type(exc).__name__)
                        _record_erasure_store(erasures, f"{owner}_connection_close", "incomplete")
                        try:
                            from core.vool_memory import VoolMemory

                            barrier = VoolMemory(db_path=operation_store.db_path)
                            try:
                                barrier.begin_erasure(resolved_session, token)
                            finally:
                                barrier.close()
                        except Exception as barrier_exc:
                            LOGGER.warning("close failure barrier restore failed: %s", type(barrier_exc).__name__)
                            _record_erasure_store(erasures, f"{owner}_erasure_operations", "incomplete")
            total_removed = max(int(removed), int(semantic_removed),
                                *(int(erasures.get(key) or 0) for key in ("structured_entries", "semantic_nodes", "source_occurrences")))
            if total_removed <= 0:
                total_removed = sum(int(erasures.get(key) or 0) for key in _FORGET_MEMORY_DERIVATIVE_STORES)
                total_removed += sum(int(result.get("removed") or 0)
                                     for name, result in erasures.get("stores", {}).items()
                                     if name.startswith("persisted_") or name.endswith("_memory_blocks"))
            if total_removed <= 0 and resuming:
                # Transcript and run-history rows count only when this forget finishes an earlier, unfinished forget of the
                # same words: that retry removed rows and must not answer "Nothing was removed" (review of
                # 9b78dac). Otherwise the chat's own history -- an earlier turn that quoted the words, the
                # previous forget command itself -- is scrubbed but is no memory: counting it answered
                # "Removed 2 memory entries" when nothing was stored (served 2026-10-19 at 153148eb).
                total_removed = sum(int(erasures.get(key) or 0) for key in _FORGET_CHAT_HISTORY_STORES)
                total_removed += sum(int(result.get("removed") or 0)
                                     for name, result in erasures.get("stores", {}).items()
                                     if name.startswith(RUN_HISTORY_PREFIX))
            incomplete = [store for store, result in erasures.get("stores", {}).items() if result["status"] == "incomplete"]
            failed_stores = [store.replace("_", " ") for store in incomplete]
            if failed_stores:
                # A failed or unopened memory store is not an empty one: the value may still be held and recalled, so the
                # reply never says nothing matched (review 2026-10-08: a denied read answered "found no memory").
                # A chat table no erasure step knows cannot be cleared by a retry; the reply says so instead of asking
                # for one (review of 9b78dac: every retry answered the same).
                unknown = [store.removeprefix(UNCLASSIFIED_PREFIX).replace("_", " ") for store in incomplete
                           if store.startswith(UNCLASSIFIED_PREFIX)]
                # The erasure operation stays open whenever any store failed; it is a consequence, not a cause.
                causes = [store for store in incomplete if not store.endswith("erasure_operations")]
                if unknown and len(unknown) == len(causes):
                    closing = (". A retry will not clear " + ", ".join(unknown)
                               + ": forget has no erasure step for " + ("that table" if len(unknown) == 1 else "those tables") + ".")
                elif unknown:
                    closing = (". Please retry the forget; a retry will not clear " + ", ".join(unknown)
                               + ": forget has no erasure step for " + ("that table" if len(unknown) == 1 else "those tables") + ".")
                else:
                    closing = ". Please retry the forget."
                return outcome("Forget could not be verified as fully applied: "
                              + ", ".join(failed_stores) + " could not be erased and verified on this turn"
                              + (f", after {total_removed} entr{'y was' if total_removed == 1 else 'ies were'} removed" if total_removed > 0 else "")
                              + closing, "partial" if total_removed > 0 else "failed")
            if total_removed <= 0:
                healed = bool(token.lower() in mirror_before and token.lower() not in mirror_after)
                if already_erased or healed:
                    return outcome("That was already forgotten; a stale copy of it has now been cleared as well."
                                   if healed else "That was already forgotten.", "already_erased")
                # Never "Forget applied" over nothing removed (served check 2026-10-07: "Forget the storage
                # unit code I gave you, it is wrong." answered "Removed 0" and 5906 was still served). The
                # command is answered with the truth; the turn is still recorded by the fast path, so the
                # capsule's temporal law withdraws the record it names on the next recall.
                # worded without a storage verb beside "memory": the final claim check reads "no stored memory" as a
                # claim that something was stored, and replaced the forget reply (main's test_a_product_word_in_a_sentence_is_not_a_command.py)
                return outcome(f'Nothing was removed. Forget found no memory in this chat that matched "{token}".', "no_match")

            return outcome(f"Forget applied. Removed {total_removed} memory entr{'y' if total_removed == 1 else 'ies'}.", "applied")

        results = [forget_target(forget_arguments[0], independent_groups[0])]
        for argument in forget_arguments[1:]:
            try:
                operations = revocation.begin_group(barrier_chat, _normalize_forget_target(argument), homes)
            except Exception as exc:
                LOGGER.warning("forget barrier could not start: %s", type(exc).__name__)
                results.append(("Forget could not be verified as fully applied: independent revocation authority is "
                                "unavailable. Please retry the forget.", "failed",
                                {"stores": {"independent_revocation": {"status": "incomplete", "removed": 0}}}))
                continue
            results.append(forget_target(argument, operations))
        if len(results) == 1:
            reply, status, erasures = results[0]
        else:
            for argument, (reply, status, erasures) in zip(forget_arguments, results, strict=True):
                # Each named target is its own result: the turn's admission waits for every one of them.
                _forget_outcomes.record(
                    reply, request=user_text, session=resolved_session, source_context=source_context,
                    argument=argument, status=status, stores=erasures.get("stores", {}),
                )
            reply, status, erasures = _combined_forget_result(results)
        return True, _forget_outcomes.record(
            reply, request=user_text, session=resolved_session,
            source_context=source_context, argument=parsed_forget.group(1), status=status,
            stores=erasures.get("stores", {}),
        )

    return False, ""


def _forget_persisted_copies(token: str, *, chat_id: str, erasures: dict[str, Any]) -> None:
    """Erase the verified database carriers; preserve referenced task identities."""
    from core.memory.entries import _load_erasure_rows, _record_erasure_store
    from core.vool_memory import text_carries_forget_token
    from storage.db import get_connection

    def quoted(name: str) -> str:
        return '"' + name.replace('"', '""') + '"'

    conn = None
    try:
        conn = get_connection()
        names = [str(row[0]) for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name",
        ).fetchall()]
        from core.memory.carriers import (
            _derivative_payload,
            _derivative_scope,
            _derivative_tables,
            carries,
        )

        task_references = any(
            str(foreign_key[2]).casefold() == "local_tasks"
            for table in names for foreign_key in conn.execute(f"PRAGMA foreign_key_list({quoted(table)})")
        )
        for table in sorted(_derivative_tables(names)):
            store = "persisted_" + table
            try:
                schema = conn.execute(f"PRAGMA table_info({quoted(table)})").fetchall()
                identity = [str(row[1]) for row in schema if int(row[5])]
                structured = {str(row[1]) for row in schema
                              if "JSON" in str(row[2]).upper() or str(row[1]).endswith("_json")}
                scope_column, suffix, params = _derivative_scope(schema, chat_id)
                rows = conn.execute(f"SELECT * FROM {quoted(table)}{suffix}", params).fetchall()
                # Decode every row before writing: malformed and opaque carriers
                # remain failures even when an earlier field already matched.
                payloads = [(row, _derivative_payload(row, schema)) for row in rows]
                matched = [(row, payload) for row, payload in payloads if carries(payload, token)]
                if matched and (not scope_column or not identity):
                    _record_erasure_store(erasures, store, "incomplete")
                    continue
                for row, payload in matched:
                    where = " AND ".join(f"{quoted(column)} IS ?" for column in identity)
                    key = tuple(row[column] for column in identity)
                    if table == "local_tasks" and task_references:
                        updates = {}
                        for column, value in payload.items():
                            if not carries(value, token):
                                continue
                            if column in identity:
                                raise ValueError("referenced task identity contains the forget target")
                            if isinstance(value, (dict, list)):
                                updates[column] = "{}" if isinstance(value, dict) else "[]"
                            elif column in structured:
                                updates[column] = "null"
                            elif isinstance(value, str):
                                updates[column] = ""
                            else:
                                raise ValueError("task payload cannot be safely redacted")
                        assignments = ", ".join(f"{quoted(column)} = ?" for column in updates)
                        conn.execute(f"UPDATE {quoted(table)} SET {assignments} WHERE {where}",
                                     (*updates.values(), *key))
                    else:
                        conn.execute(f"DELETE FROM {quoted(table)} WHERE {where}", key)
                conn.commit()
                retained = conn.execute(f"SELECT * FROM {quoted(table)}{suffix}", params).fetchall()
                retained_payloads = [_derivative_payload(row, schema) for row in retained]
                if any(carries(payload, token) for payload in retained_payloads):
                    raise RuntimeError("persisted copy could not be erased and verified")
                _record_erasure_store(erasures, store, "verified", len(matched))
            except Exception:
                conn.rollback()
                _record_erasure_store(erasures, store, "incomplete")
        _record_erasure_store(erasures, "persisted_packet_cache_copies", "incomplete" if any(
            name.startswith("persisted_") and result["status"] == "incomplete"
            for name, result in erasures.get("stores", {}).items()) else "verified")
    except Exception:
        _record_erasure_store(erasures, "persisted_packet_cache_copies", "incomplete")
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                _record_erasure_store(erasures, "persisted_packet_cache_connection", "incomplete")

    try:
        from core.evidence_kernel.receipts import _ledger_dir, _receipt_payload_for_erasure, _sha256

        root = _ledger_dir()
        try:
            kinds = list(root.iterdir())
        except FileNotFoundError:
            kinds = []
        retained = False
        for kind in kinds:
            if not kind.is_dir():
                continue
            rows = _load_erasure_rows(kind / (_sha256(chat_id)[:24] + ".jsonl"))
            # The chain is immutable. Payload-bearing metadata must be named as
            # retained; references and keyed digests are not plaintext copies.
            retained = retained or any(text_carries_forget_token(
                json.dumps(_receipt_payload_for_erasure(row), ensure_ascii=False), token,
            ) for row in rows)
        _record_erasure_store(erasures, "packet_receipt_ledgers", "incomplete" if retained else "verified")
    except Exception:
        _record_erasure_store(erasures, "packet_receipt_ledgers", "incomplete")

    import os
    from pathlib import Path

    from core.runtime_paths import data_path

    roots = [("bootstrap_snapshots", data_path("bootstrap")), ("relay_mirror_snapshots", Path("relay_mirror"))]
    configured = str(os.environ.get("VOOL_MIRROR_DATA_DIR") or "").strip()
    if configured:
        roots.append(("configured_mirror_snapshots", Path(configured)))
    for store, root in roots:
        try:
            try:
                paths = list(root.iterdir())
            except FileNotFoundError:
                _record_erasure_store(erasures, store, "not_applicable")
                continue
            retained = False
            for path in paths:
                if path.suffix.lower() != ".json":
                    continue
                payload = json.loads(path.read_text(encoding="utf-8"))
                retained = retained or text_carries_forget_token(json.dumps(payload, ensure_ascii=False), token)
            # Snapshot membership alone gives no authority to remove another
            # chat's or peer's copy. Readers withhold retained payloads.
            _record_erasure_store(erasures, store, "incomplete" if retained else "verified")
        except Exception:
            _record_erasure_store(erasures, store, "incomplete")
    _record_erasure_store(erasures, "remote_mirror_copies", "incomplete" if str(
        os.environ.get("VOOL_MIRROR_URL") or "").strip() else "not_applicable")


def _memory_scope_for_user_fact(
    fact: str,
    *,
    policy: ContextAccessPolicy,
) -> str:
    """Keep ordinary remembered facts in the chat namespace.

    Private local chats may import confirmed profile preferences, but that does
    not make every user-authored fact a profile preference.  Only explicit
    preference/identity language is eligible for profile scope; all other
    captures stay bound to their originating chat.

    Eligibility is decided on the fact's AUTHORED portion only: an explicitly
    remembered quotation ("Remember this article excerpt: \"My name is Petra.
    …\"") is retained in the chat as source material, and its embedded
    identity/preference markers never promote the quoted speaker into the
    user's profile.  A direct authored declaration ("my preferred name is …")
    keeps its existing profile eligibility.
    """
    if not policy.allow_user_profile_context:
        return "chat"
    from core.memory.admission import classify_user_text

    authored = classify_user_text(fact).authored_text
    if not authored:
        # The entire fact is quoted/fenced source material.
        return "chat"
    lowered = " ".join(authored.casefold().split())
    return "user_profile" if any(marker in lowered for marker in _PROFILE_MEMORY_MARKERS) else "chat"


#: Trailing connective framing that introduces quoted/pasted source material
#: inside an otherwise personal declaration ("…, and this article excerpt:").
#: Stripped only when the authored remainder is an attributable declaration and
#: the quote is stored separately; never used to delete content outright.
_REMEMBERED_SOURCE_FRAMING_TAIL_RE = re.compile(
    r"[,;:]?\s*(?:and\s+|also\s+|plus\s+|with\s+|alongside\s+)?"
    r"(?:this|that|these|those|the\s+(?:following|attached|pasted))?\s*"
    r"(?:[a-z-]+\s+from\s+(?:the|this|that)\s+[a-z-]+"
    r"|article|excerpt|quote|quotation|passage|snippet|post|note|text|"
    r"template|bit|words|lines|phrases|fragments)\b[^a-z0-9]*:?\s*(?:and\s+)?\s*$",
    re.IGNORECASE,
)


def _attributable_declaration(text: str) -> bool:
    """Whether authored text itself asserts a personal declaration/instruction."""
    lowered = " ".join(str(text or "").casefold().split())
    if not lowered:
        return False
    if any(marker in lowered for marker in _PROFILE_MEMORY_MARKERS):
        return True
    from core.memory.learning import extract_memory_candidates

    return bool(extract_memory_candidates(text))


def _admitted_remember_facts(
    fact: str,
    *,
    policy: ContextAccessPolicy,
) -> list[tuple[str, str, str]]:
    """Split one explicitly remembered fact into admissible (content, scope, source).

    The persisted content and its scope must agree: a genuine personal marker
    in the authored portion never authorizes storing the third-party material
    quoted beside it at profile scope.  When the authored portion is an
    unambiguous personal declaration, the declaration is stored through the
    normal scope decision and the quoted source material is retained
    chat-scoped as source.  Mixed content without an attributable declaration
    stays whole and chat-scoped.  Nothing is discarded.
    """
    from core.memory.admission import classify_user_text

    origin = classify_user_text(fact)
    if not origin.has_source_material:
        return [(fact, _memory_scope_for_user_fact(fact, policy=policy), "manual")]
    authored = " ".join(origin.authored_text.split())
    source_material = " ".join(origin.source_text.split())
    if not _attributable_declaration(authored):
        return [(fact, "chat", "manual")]
    attributable = _REMEMBERED_SOURCE_FRAMING_TAIL_RE.sub("", authored).strip() or authored
    pairs = [
        (
            attributable,
            _memory_scope_for_user_fact(attributable, policy=policy),
            "manual",
        )
    ]
    if source_material:
        pairs.append((source_material, "chat", "remembered_source_material"))
    return pairs
