from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any

from core.context_scope import ContextAccessPolicy

# Serializes the per-turn memory write sequence (conversation-log append + trim, and every
# downstream load-modify-rewrite: facts, heuristics, session summary, dialogue). The daemon
# serves turns on a threadpool, so two concurrent turns would otherwise interleave a
# read-all-then-rewrite on the same process-global files and silently drop facts or truncate the
# log. Reentrant so a nested writer that also takes it does not deadlock.
_MEMORY_WRITE_LOCK = threading.RLock()

from core.memory import entries as memory_entries
from core.memory import learning as memory_learning
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
    r"^(?:(?:this is|in this)\b[^.!?]*(?:[.!?]\s*|,\s*))?"
    r"(?:please\s+)?(?P<verb>remember(?: that)?|note(?: that)?|store(?: this)?|keep|retain|save)\s+"
    r"(?P<fact>.+)$",
    re.IGNORECASE,
)
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
_FORGET_RE = re.compile(r"^(?:forget|erase)\s+(.+)$", re.IGNORECASE)
_FORGET_TARGET_RE = re.compile(
    r"^(?:the\s+)?(?:old\s+|previous\s+|exact\s+)?"
    r"(?:identifier|code|marker|private\s+marker|label|port|number|phrase|value|fact|token)\s+"
    r"(?P<value>.+?)"
    r"(?:\s+from\s+(?:this|the\s+current)\s+chat)?$",
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
        clean = " ".join(str(part or "").split()).strip()
        if index:
            clean = _MEMORY_CONTINUATION_PREFIX_RE.sub("", clean).strip()
        if clean:
            facts.append(clean)
    return facts


def _forget_target_without_preservation_clause(raw_target: str) -> str:
    """Remove a trailing keep-clause without changing the named forget target."""
    target = " ".join(str(raw_target or "").strip().strip(".!?").split())
    return re.sub(
        r",?\s+but\s+keep\b.*$",
        "",
        target,
        flags=re.IGNORECASE,
    ).strip()


def _normalize_forget_target(raw_target: str) -> str:
    """Extract the value from natural forget wording without broad deletion."""
    target = _forget_target_without_preservation_clause(raw_target)
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
        for row in list_memory_entries(access_policy=access_policy, limit=20)
        if subject in str(row.get("fact_key") or "").casefold()
        or subject in str(row.get("text") or "").casefold()
        or subject
        in {
            str(token).casefold()
            for token in row.get("keywords") or []
        }
    ]
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


def _memory_persistence_suppressed(source_context: dict[str, Any] | None) -> bool:
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
    scope = str(ctx.get("memory_scope") or "").strip().lower()
    if scope in {"ephemeral", "audit", "test", "probe", "none"}:
        return True
    if ctx.get("persist_memory") is False or bool(ctx.get("ephemeral_memory")):
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


def append_conversation_event(
    *,
    session_id: str,
    user_input: str,
    assistant_output: str,
    source_context: dict[str, Any] | None = None,
    response_class: str | None = None,
    access_policy: ContextAccessPolicy | None = None,
    persist_now: bool = False,
) -> None:
    """Record one user/assistant exchange in the conversation log and the derived memory stores.

    Under a served turn whose ingress declared its commit boundary (an A0 request id is bound and
    ``source_context["transcript_commit_boundary"]`` is set) the row is staged and written by
    ``core.finalization.finalize_answer`` with the committed bytes -- see the staging note above.
    ``persist_now`` is the explicit opt-out for a caller that owns its own commit semantics.
    """
    if _memory_persistence_suppressed(source_context):
        return
    declared = bool((source_context or {}).get(TRANSCRIPT_COMMIT_BOUNDARY_KEY)) if isinstance(source_context, dict) else False
    request_id = "" if (persist_now or not declared) else _current_request_lineage()
    if request_id and transcript_commit_boundary_open(request_id):
        _stage_conversation_event(
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


def _stage_conversation_event(request_id: str, event: dict[str, Any]) -> None:
    stale: list[tuple[str, dict[str, Any]]] = []
    now = time.monotonic()
    with _TRANSCRIPT_STAGE_LOCK:
        _STAGED_TRANSCRIPT_EVENTS.setdefault(request_id, []).append({**event, "staged_at": now})
        for key in list(_STAGED_TRANSCRIPT_EVENTS):
            if key == request_id:
                continue
            rows = _STAGED_TRANSCRIPT_EVENTS[key]
            if rows and now - float(rows[0].get("staged_at") or now) > _STAGED_TRANSCRIPT_MAX_AGE_S:
                stale.extend((key, row) for row in rows)
                del _STAGED_TRANSCRIPT_EVENTS[key]
                _OPEN_TRANSCRIPT_BOUNDARIES.discard(key)
    for _key, row in stale:
        _write_staged_row(row, assistant_text=None, commit_state=TRANSCRIPT_COMMIT_STATE_UNSEALED)


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
    request that produced no transcript row) -- never an error, so a seal can never fail on it.
    """
    key = str(request_id or "")
    if not key:
        return False
    with _TRANSCRIPT_STAGE_LOCK:
        rows = _STAGED_TRANSCRIPT_EVENTS.get(key)
        if not rows:
            return False
        row = rows.pop(0)
        if not rows:
            del _STAGED_TRANSCRIPT_EVENTS[key]
    _write_staged_row(row, assistant_text=str(committed_text or ""), commit_state=commit_state, extra_fields=extra_fields)
    return True


def flush_staged_conversation_events(
    request_id: str,
    *,
    commit_state: str = TRANSCRIPT_COMMIT_STATE_UNSEALED,
    assistant_text: str | None = None,
) -> int:
    """Write every row still staged under ``request_id``.

    The request-end fallback: a turn that never reached the seal keeps the user's message and
    the lane's own text, marked ``unsealed`` (no gate rewrote it, so it is exactly what the
    transport had). A typed no-answer terminal passes ``assistant_text=""`` -- no answer was
    committed, and the row says so instead of carrying a draft nobody saw.
    """
    key = str(request_id or "")
    if not key:
        return 0
    with _TRANSCRIPT_STAGE_LOCK:
        rows = _STAGED_TRANSCRIPT_EVENTS.pop(key, [])
        _OPEN_TRANSCRIPT_BOUNDARIES.discard(key)
    for row in rows:
        _write_staged_row(row, assistant_text=assistant_text, commit_state=commit_state)
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
    if _memory_persistence_suppressed(source_context):
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
    with _MEMORY_WRITE_LOCK:
        path = conversation_log_path()
        append_sequenced_jsonl(path, payload)
        _trim_jsonl_file(path, max_bytes=_MAX_CONVERSATION_LOG_BYTES)
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
        is_image_turn = False
        try:
            from core.execution.constants import image_generation_intent

            is_image_turn = bool(image_generation_intent(redacted_user))
        except Exception:
            is_image_turn = False
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
        if not is_image_turn:
            # This is the single finalized-turn seam for every route, including
            # fast paths.  Keeping semantic persistence here prevents the
            # model-wording chat surface from becoming an accidental special
            # case and carries the same server-resolved policy/runtime home to
            # both write and later retrieval.
            try:
                from core.context_retrieval import store_turn

                semantic_result = store_turn(
                    session_id,
                    redacted_user,
                    redacted_assistant,
                    access_policy=resolved_policy,
                    source_context=source_context,
                )
                if semantic_result.get("status") == "failed":
                    LOGGER.warning(
                        "semantic memory write rejected for chat %s: %s",
                        session_id,
                        semantic_result.get("reason", "unknown"),
                    )
            except Exception as exc:
                # Semantic memory is fail-soft for the response path, but the
                # failure must be visible to diagnostics rather than silently
                # making the round trip appear successful.
                LOGGER.warning(
                    "semantic memory write unavailable for chat %s: %s",
                    session_id,
                    type(exc).__name__,
                )


#: Assistant-authored artifacts that belong in a chat's TRANSCRIPT but in none of the
#: memory derived from it. The kind is typed so a reader can tell an artifact from a
#: turn without parsing prose.
ARTIFACT_KIND_COUNCIL_SUMMARY = "council_summary"


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
        or _MEMORY_CAPTURE_RE.match(text)
        or _CORRECTION_RE.match(text)
        or _FORGET_RE.match(text)
        or _is_memory_recall_question(text)
    ):
        return False, ""
    capture_match = _MEMORY_CAPTURE_RE.match(text)
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
        for memory_fact in facts:
            memory_scope = _memory_scope_for_user_fact(
                memory_fact,
                policy=resolved_policy,
            )
            added_count += int(
                add_memory_fact(
                    memory_fact,
                    session_id=resolved_session,
                    scope=memory_scope,
                    authority="confirmed_memory",
                    access_policy=resolved_policy,
                )
            )
        if added_count:
            return True, "Locked in. I’ll remember that."
        return True, "I already had that in memory."

    forget = _FORGET_RE.match(text)
    if forget:
        referential, token = _referential_forget_token(
            forget.group(1),
            access_policy=resolved_policy,
        )
        if referential and not token:
            return True, "Forget command needs the exact value because more than one matching memory is active."
        if not referential:
            token = _normalize_forget_target(forget.group(1))
        if len(token) < 2:
            return True, "Forget command skipped: provide a clearer keyword."
        try:
            removed = forget_memory(
                token,
                access_policy=resolved_policy,
            )
        except memory_entries.MemoryErasureError:
            # P0 success-after-verification law: an erasure that could not be
            # verified absent from every model-visible memory path is never
            # acknowledged as applied. The durable tombstone has landed; the
            # retry heals whatever path failed verification.
            return True, (
                "Forget could not be verified as fully applied — the entry was not confirmed "
                "erased on every memory path. Please retry the forget."
            )
        semantic_removed = 0
        try:
            from core.context_retrieval import forget_session_memory

            semantic_removed = forget_session_memory(
                resolved_session,
                token,
                access_policy=resolved_policy,
                source_context=source_context,
            )
        except Exception as exc:
            LOGGER.warning("semantic memory forget unavailable: %s", type(exc).__name__)
        total_removed = max(int(removed), int(semantic_removed))
        return True, f"Forget applied. Removed {total_removed} memory entr{'y' if total_removed == 1 else 'ies'}."

    return False, ""


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
    """
    if not policy.allow_user_profile_context:
        return "chat"
    lowered = " ".join(str(fact or "").casefold().split())
    return "user_profile" if any(marker in lowered for marker in _PROFILE_MEMORY_MARKERS) else "chat"
