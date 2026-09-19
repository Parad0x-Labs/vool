"""Authority and bounds for provider-bound conversation history.

History admission and history expansion are deliberately different decisions:

* server-resolved scope decides whether any history may be selected;
* every admitted ordinary chat gets the most recent completed user/assistant exchange;
* continuation/relevance is only a hint to expand beyond that adjacency floor.

Keeping this contract in one typed policy prevents a lexical classifier from becoming an access
control gate again.  The classifier never appears in ``scope_allows_transcript`` and cannot turn it
off.  It can only select the already-authorized bounded expansion lane.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

ADJACENCY_FLOOR_MESSAGES = 2
EXPANDED_HISTORY_MESSAGES = 10
HISTORY_MAX_CHARS = 5000
#: The ONE lawful exceedance of ``HISTORY_MAX_CHARS``: a continuation turn that explicitly
#: references a prior artifact (``_mentions_continuation`` in the prompt normalizer) must carry
#: that artifact whole, or the model truthfully answers that it cannot see the code it was told
#: to continue (measured live 2026-09-17: the 19,764-char task board was appended to the history
#: and then removed by the 5,000-char clamp; six consecutive paid turns answered "I don't have
#: access to the previous agent's code"). Bounded at 40,000 so the exception can never become an
#: unbounded prompt: a caller asking for more still gets 40,000, and every non-continuation turn
#: keeps the 5,000-char envelope exactly as before.
CONTINUATION_CARRY_HISTORY_MAX_CHARS = 40_000
AUTHORITATIVE_CORRECTIONS_PREFIX = (
    "Latest explicit user corrections for this chat are authoritative. "
    "They supersede older conflicting statements: "
)

#: The assistant side of an exchange whose published reply may not enter a live prompt.
#:
#: A8 suppression is ROW-scoped, but the semantic unit of a transcript is the EXCHANGE.  Dropping
#: only the assistant row leaves the user's request standing with nothing answering it, and a
#: pending-looking request is what a model acts on: measured live on build 7fe94596, acceptance
#: turn 6 ("who wrote the novel 1984?") was REFUSED, and turn 7 -- an unrelated question -- opened
#: with "The novel *1984* was written by George Orwell."  The runtime published in its own voice
#: the content it had declined to publish one turn earlier, so the contamination does not merely
#: add noise: it DEFEATS the refusal.
#:
#: This module already states history LIFECYCLE and AUTHORITY facts to the model in exactly this
#: way (see ``AUTHORITATIVE_CORRECTIONS_PREFIX``).  The request text itself is deliberately kept --
#: an explicit retry ("try again"), a previous-turn operand, a correction and a standing
#: prohibition all need their referent -- and only its lifecycle is restated: handled, closed, not
#: pending.  It carries no answer content and does not re-quote the refusal, which is what bled.
CLOSED_EXCHANGE_NOTICE = (
    "[Closed exchange: this earlier request was already handled and its reply is not available "
    "in this context. It is not an open request - answer it again only if the user asks again.]"
)


def closed_exchange_marker() -> dict[str, str]:
    """The stand-in that keeps a suppressed exchange a PAIR rather than a dangling request.

    Emitted in the ``assistant`` role because the exchange genuinely is complete -- the assistant
    did reply, and that reply is unavailable here.  Holding the role also makes the existing
    pairing machinery correct for free: ``most_recent_completed_exchange`` sees a completed pair,
    and ``enforce_history_budget`` groups the request and this notice into one semantic unit, so
    the budget can never drop the notice and re-strand the request it closes.
    """

    return {"role": "assistant", "content": CLOSED_EXCHANGE_NOTICE}


def is_closed_exchange_marker(text: Any) -> bool:
    """Whether `text` is this module's own closed-exchange notice.

    Self-recognition against the exported constant, the same device the refusal leads use: the
    marker is runtime status, never answer content, so no caller may treat it as a previous answer
    to re-present, shorten or quote.
    """

    return str(text or "").strip() == CLOSED_EXCHANGE_NOTICE


#: The assistant side of an exchange whose turn ended WITHOUT a model answer -- the runtime
#: published one of its own failure notices instead ("`model` hit its output limit ... Retry the
#: turn", "I couldn't get a usable model response ..."). To the READER that notice is right and
#: stays in the transcript; in a later PROMPT it is a trap. It sits in the assistant role holding
#: the very request it failed to answer, phrased as a pending instruction ("Retry the turn"), so
#: the next question arrives beside what still reads as an open task. Measured live 2026-09-15: a
#: wallet-security review turn truncated at the output limit, and the NEXT, unrelated design
#: question was answered with "Thank you for the detailed task description ... 1. Identify the 7
#: most important security or abuse risks" -- the failed turn's task, served as the current one.
#:
#: Same law as the closed marker above: keep the pair, restate the LIFECYCLE (attempted, never
#: answered, not open) and carry no request echo, which is what bled.
FAILED_EXCHANGE_NOTICE = (
    "[Failed exchange: this earlier request was attempted and never answered - the turn failed "
    "before a usable reply existed, and the runtime told the user to retry. It is not an open "
    "request: answer the user's current message, not this one.]"
)

#: The notice families the runtime itself authors when no model answer existed. Matched on stable
#: lead shapes and signature clauses of core/agent_runtime/memory_runtime.py and
#: core/incomplete_answer.py, never on a model's own wording: a genuine (even partial) answer
#: never begins with these. A partial answer keeps its own text -- only the no-answer notices
#: close the exchange.
_RUNTIME_FAILURE_NOTICE_RE = re.compile(
    r"^\s*(?:i\s+couldn't\s+get\s+a\s+(?:usable|live)\s+model\s+response"
    r"|i\s+found\s+(?:a\s+matching\s+cached\s+answer|relevant\s+local\s+memory)"
    r"|the\s+answer\s+came\s+back\s+cut\s+off"
    r"|\*?`?[\w.:/-]+`?\*?\s+hit\s+its\s+output\s+limit\s+before\s+it\s+completed\s+the\s+answer"
    # The provider-failure surfaces memory_runtime authors when a paid turn never produced a
    # reply (402 payment/no-provider, pinned-model refusal, fallback budget exhaustion). They
    # are notices, not answers, and a wall of them replayed as assistant history taught the
    # next model that every earlier turn had failed (measured live 2026-09-17: twenty
    # consecutive retry turns' 402 notices made claude deny an artifact riding its own prompt).
    r"|usepod\s+answered\s+http\s+\d+"
    r"|`[^`\n]+`\s+was\s+the\s+only\s+model\s+this\s+turn\s+was\s+allowed\s+to\s+use"
    r"|`[^`\n]+`\s+failed,\s+and\s+this\s+turn's\s+fallback\s+time\s+budget\s+ran\s+out"
    r")"
    r"|no\s+cached\s+or\s+remembered\s+text\s+was\s+substituted",
    re.IGNORECASE,
)


def failed_exchange_marker() -> dict[str, str]:
    """The assistant-role stand-in for a turn that ended in a runtime failure notice."""

    return {"role": "assistant", "content": FAILED_EXCHANGE_NOTICE}


def is_failed_exchange_notice(text: Any) -> bool:
    """Whether `text` is this module's own failed-exchange notice (self-recognition)."""

    return str(text or "").strip() == FAILED_EXCHANGE_NOTICE


def assistant_text_is_runtime_failure_notice(text: Any) -> bool:
    """Whether an assistant row is a runtime-authored NO-ANSWER notice rather than an answer.

    Recognizes this runtime's own failure surfaces only. Fail-open by design: anything the
    recognizer does not know -- including a truncated-but-real partial answer, an honest denial a
    guard rewrote the reply into, or any model wording -- stays in the prompt as history.
    """

    return bool(_RUNTIME_FAILURE_NOTICE_RE.search(str(text or "").strip()))

_SAME_CHAT_HISTORY_RECALL_RE = re.compile(
    r"^\s*(?:can|could|would\s+you\s+)?(?:"
    r"what|which|list|show|repeat|recall|remind|summarize|tell\s+me"
    r")\b[^\n]{0,240}\b(?:"
    r"earlier|before|above|history|this\s+(?:chat|conversation)|"
    r"(?:did|have)\s+(?:we|i|you)\s+(?:ask|answer|say|calculate)"
    r")\b",
    re.IGNORECASE,
)
_DIRECT_TRANSCRIPT_RECALL_HEAD_RE = re.compile(
    r"^(?:(?:and|also|ok|okay|so|then)\s+)*(?:"
    r"what|which|show|list|repeat|recall|remind|tell\s+me|part\s+[a-z0-9]+|"
    r"(?:list\s+)?item\s+\d+"
    r")\b",
    re.IGNORECASE,
)
_DIRECT_TRANSCRIPT_SUBJECT_RE = re.compile(
    r"\b(?:title|heading|headline|name|word|phrase|sentence|line|number|value|result|answer|"
    r"math|maths|calculation|multiply|multiplication|product|part|(?:list\s+)?item|entry|bullet)\b",
    re.IGNORECASE,
)
_DIRECT_TRANSCRIPT_REFERENCE_RE = re.compile(
    r"\b(?:earlier|before|above|previous|last\s+(?:answer|reply|response|message)|just\s+now|"
    r"this\s+(?:chat|conversation)|from\s+(?:the\s+)?(?:chat|conversation))\b|"
    r"\byou\b[^.!?]{0,60}\b(?:gave|give|said|say|wrote|write|used|use|suggested|called|got|get|answered)\b",
    re.IGNORECASE,
)
_DIRECT_RECALL_TYPO_WORDS = {
    "wht": "what",
    "waht": "what",
    "wat": "what",
    "titl": "title",
    "headng": "heading",
    "sentnce": "sentence",
    "phrse": "phrase",
    "numbr": "number",
    "frm": "from",
    "b4": "before",
    "u": "you",
    "gve": "gave",
}


@dataclass(frozen=True)
class ContextHistorySelection:
    transcript_allowed: bool
    max_messages: int
    max_chars: int
    authority_reason: str
    expansion_reason: str

    @property
    def expands_beyond_adjacency(self) -> bool:
        return self.transcript_allowed and self.max_messages > ADJACENCY_FLOOR_MESSAGES


def is_same_chat_history_recall(text: str) -> bool:
    """Recognize a direct request to inspect earlier turns in the current chat.

    This is deliberately anchored at the start.  Quoted examples such as ``the fixture says
    \"what was my previous question?\"`` remain ordinary content instead of gaining transcript
    expansion authority.
    """

    raw = str(text or "").strip()
    # A quoted/code example is content to discuss, not authority to inspect private transcript.
    if not raw or raw.startswith(('"', "'", "`", "“", "‘")):
        return False
    normalized = " ".join(raw.split())
    if _SAME_CHAT_HISTORY_RECALL_RE.search(normalized):
        return True
    typo_folded = " ".join(
        _DIRECT_RECALL_TYPO_WORDS.get(word.casefold(), word.casefold())
        for word in re.findall(r"[A-Za-z0-9']+", normalized)
    )
    return bool(
        _DIRECT_TRANSCRIPT_RECALL_HEAD_RE.search(typo_folded)
        and _DIRECT_TRANSCRIPT_SUBJECT_RE.search(typo_folded)
        and _DIRECT_TRANSCRIPT_REFERENCE_RE.search(typo_folded)
    )


def select_history_policy(
    *,
    scope_allows_transcript: bool,
    expansion_hint: bool | None,
    authority_reason: str,
    requested_max_messages: int = EXPANDED_HISTORY_MESSAGES,
    requested_max_chars: int = HISTORY_MAX_CHARS,
    explicit_zero_history: bool = False,
) -> ContextHistorySelection:
    """Resolve scope authority first, then a bounded expansion shape.

    ``None`` preserves broad-history behavior for legacy internal callers that have no typed hint.
    Real chat ingress stamps a boolean.  A false hint selects adjacency; it never revokes scope.
    """

    max_chars = max(0, min(int(requested_max_chars), HISTORY_MAX_CHARS))
    if explicit_zero_history or not scope_allows_transcript:
        return ContextHistorySelection(
            transcript_allowed=False,
            max_messages=0,
            max_chars=max_chars,
            authority_reason=(
                "explicit_zero_history"
                if explicit_zero_history
                else str(authority_reason or "scope_denied")
            ),
            expansion_reason="history_not_admitted",
        )

    expand = expansion_hint is not False
    requested = max(ADJACENCY_FLOOR_MESSAGES, int(requested_max_messages))
    max_messages = (
        min(requested, EXPANDED_HISTORY_MESSAGES)
        if expand
        else ADJACENCY_FLOOR_MESSAGES
    )
    return ContextHistorySelection(
        transcript_allowed=True,
        max_messages=max_messages,
        max_chars=max_chars,
        authority_reason=str(authority_reason or "authorized_scope"),
        expansion_reason=(
            "continuation_or_relevance_hint"
            if expand and max_messages > ADJACENCY_FLOOR_MESSAGES
            else "same_chat_adjacency_floor"
        ),
    )


def is_current_user_message(
    item: dict[str, str],
    *,
    current_user_text: str,
    current_user_raw_text: str = "",
    current_turn_id: str = "",
) -> bool:
    """Match the current USER row by provenance first, with text as a legacy fallback."""

    if item.get("role") != "user":
        return False
    item_turn_id = str(item.get("turn_id") or "").strip()
    if current_turn_id and item_turn_id:
        return item_turn_id == current_turn_id
    item_key = _message_comparison_key(item.get("content"))
    current_keys = {
        key
        for key in (
            _message_comparison_key(current_user_text),
            _message_comparison_key(current_user_raw_text),
        )
        if key
    }
    return bool(item_key and item_key in current_keys)


def _message_comparison_key(value: Any) -> str:
    """Normalize identity only; model-facing content remains byte-for-byte untouched."""

    collapsed = " ".join(str(value or "").split()).strip()
    return unicodedata.normalize("NFC", collapsed)


def most_recent_completed_exchange(
    items: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Return the newest completed user -> assistant pair, ignoring incomplete trailing sends."""

    normalized = [
        {"role": str(item.get("role") or "").strip().lower(), "content": str(item.get("content") or "")}
        for item in list(items or [])
        if isinstance(item, dict)
        and str(item.get("role") or "").strip().lower() in {"user", "assistant"}
        and str(item.get("content") or "").strip()
    ]
    def _pair_ending_at(assistant_index: int) -> list[dict[str, str]]:
        for user_index in range(assistant_index - 1, -1, -1):
            role = normalized[user_index]["role"]
            if role == "assistant":
                break
            if role == "user":
                return [normalized[user_index], normalized[assistant_index]]
        return []

    # A CLOSED exchange is complete but carries no content, so it is not what this adjacency floor
    # exists to hand the model. Preferring it would drop a real previous answer that the current
    # turn still depends on: with "What is 12 times 8?" -> "96", then a refused turn, then "Now
    # divide that by 6", the newest completed pair is the closed one and the operand 96 never
    # reaches the prompt. Measured while adding the closed-exchange notice, which is what made a
    # suppressed exchange "completed" at all.
    fallback: list[dict[str, str]] = []
    for assistant_index in range(len(normalized) - 1, -1, -1):
        if normalized[assistant_index]["role"] != "assistant":
            continue
        pair = _pair_ending_at(assistant_index)
        if not pair:
            continue
        if is_closed_exchange_marker(pair[1]["content"]):
            # Remember it: if the whole window is closed exchanges, returning one still beats
            # returning nothing, because it keeps the request paired instead of dangling and
            # leaves an explicit retry its referent.
            fallback = fallback or pair
            continue
        return pair
    return fallback


def enforce_history_budget(
    items: list[dict[str, Any]],
    *,
    max_messages: int = EXPANDED_HISTORY_MESSAGES,
    max_chars: int = HISTORY_MAX_CHARS,
    continuation_carry: bool = False,
) -> list[dict[str, Any]]:
    """Apply the final provider-neutral history envelope without splitting exchanges.

    Selection, compression, retrieval, and receipt insertion all add semantic units.  Therefore
    none of their intermediate limits is authoritative.  This is the single final pass: it removes
    the oldest optional unit first, protects the newest completed user/assistant exchange while any
    optional unit remains, and drops an over-sized unit whole rather than emitting malformed prose.

    ``continuation_carry`` is the single sanctioned way to exceed ``HISTORY_MAX_CHARS``: the
    caller states that THIS turn references a prior artifact that must ride whole (see
    ``CONTINUATION_CARRY_HISTORY_MAX_CHARS``). The clamp ceiling rises for that turn only; the
    requested ``max_chars`` is still honored below it.

    Character accounting intentionally uses Python code points (``len(str)``), which is the public
    contract.  UTF-8 byte size is a separate transport concern and must not make Unicode consume a
    different history allowance.
    """

    message_limit = max(0, min(int(max_messages), EXPANDED_HISTORY_MESSAGES))
    char_ceiling = (
        CONTINUATION_CARRY_HISTORY_MAX_CHARS if continuation_carry else HISTORY_MAX_CHARS
    )
    char_limit = max(0, min(int(max_chars), char_ceiling))
    normalized = [
        dict(item)
        for item in list(items or [])
        if isinstance(item, dict)
        and str(item.get("role") or "").strip().lower()
        in {"system", "user", "assistant", "context"}
        and str(item.get("content") or "").strip()
    ]
    if not normalized or message_limit == 0 or char_limit == 0:
        return []

    newest_pair: tuple[int, int] | None = None
    for assistant_index in range(len(normalized) - 1, -1, -1):
        if str(normalized[assistant_index].get("role") or "").strip().lower() != "assistant":
            continue
        for user_index in range(assistant_index - 1, -1, -1):
            role = str(normalized[user_index].get("role") or "").strip().lower()
            if role == "assistant":
                break
            if role == "user":
                newest_pair = (user_index, assistant_index)
                break
        if newest_pair is not None:
            break

    protected = set(newest_pair or ())
    units: list[list[tuple[int, dict[str, Any]]]] = []
    index = 0
    while index < len(normalized):
        current = normalized[index]
        current_role = str(current.get("role") or "").strip().lower()
        if (
            current_role == "user"
            and index + 1 < len(normalized)
            and str(normalized[index + 1].get("role") or "").strip().lower()
            == "assistant"
        ):
            units.append([(index, current), (index + 1, normalized[index + 1])])
            index += 2
            continue
        units.append([(index, current)])
        index += 1

    def _over_budget() -> bool:
        flattened = [message for unit in units for _position, message in unit]
        return len(flattened) > message_limit or sum(
            len(str(message.get("content") or "")) for message in flattened
        ) > char_limit

    def _retention_priority(message: dict[str, Any]) -> int:
        try:
            declared = max(0, int(message.get("_history_retention_priority") or 0))
        except (TypeError, ValueError):
            declared = 0
        if str(message.get("content") or "").startswith(AUTHORITATIVE_CORRECTIONS_PREFIX):
            return max(2, declared)
        return declared

    while units and _over_budget():
        removable = [
            (unit_index, unit)
            for unit_index, unit in enumerate(units)
            if not any(position in protected for position, _message in unit)
        ]
        removable_index = (
            min(
                removable,
                key=lambda indexed_unit: (
                    max(
                        _retention_priority(message)
                        for _position, message in indexed_unit[1]
                    ),
                    indexed_unit[0],
                ),
            )[0]
            if removable
            else None
        )
        # If the protected exchange alone cannot fit, remove its whole semantic unit.  A missing
        # pair is preferable to a half-pair or a character-sliced message that changes its meaning.
        if removable_index is None:
            if protected:
                units = [
                    unit
                    for unit in units
                    if not any(position in protected for position, _message in unit)
                ]
                continue
            removable_index = 0
        units.pop(removable_index)

    return [dict(message) for unit in units for _position, message in unit]


def history_authority_for_source(transcript_source: str) -> str:
    """Translate the actual transcript result into truthful model-request telemetry."""

    source = str(transcript_source or "none").strip()
    if source == "scope_denied":
        return "scope_denied"
    if source == "plain_task_no_history":
        return "explicit_zero_history"
    if source in {"structured_dialogue_memory", "client_conversation_history"}:
        return "chat_namespace_active"
    return "no_history_selected"


__all__ = [
    "ADJACENCY_FLOOR_MESSAGES",
    "AUTHORITATIVE_CORRECTIONS_PREFIX",
    "CLOSED_EXCHANGE_NOTICE",
    "CONTINUATION_CARRY_HISTORY_MAX_CHARS",
    "EXPANDED_HISTORY_MESSAGES",
    "HISTORY_MAX_CHARS",
    "ContextHistorySelection",
    "closed_exchange_marker",
    "enforce_history_budget",
    "history_authority_for_source",
    "is_closed_exchange_marker",
    "is_current_user_message",
    "is_same_chat_history_recall",
    "most_recent_completed_exchange",
    "select_history_policy",
]
