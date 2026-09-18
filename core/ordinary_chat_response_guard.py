"""Validate ordinary chat output before it can become conversational memory.

This is intentionally a narrow, structural detector.  It does not prescribe an
answer and it does not constrain explicit creative-media requests.  It only
rejects clearly image-prompt-shaped output from a normal text-chat turn.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

_CAMERA_SIGNALS = re.compile(
    r"\b(?:camera|lens|shot|framing|depth of field|aspect ratio|close-up|wide shot)\b",
    re.IGNORECASE,
)
_STYLE_SIGNALS = re.compile(
    r"\b(?:lighting|colour grade|color grade|cinematic|photorealistic|film grain|bokeh|8k|4k)\b",
    re.IGNORECASE,
)
_SCAFFOLD_SIGNALS = re.compile(
    r"(?:^|\n)\s*(?:subject|camera|lighting|negative prompt|style|composition|tags)\s*:",
    re.IGNORECASE,
)
_PROMPT_DETAIL_SIGNALS = re.compile(
    r"\b(?:35mm|50mm|85mm|film grain|negative prompt|tags?|aspect ratio|"
    r"depth of field|bokeh|color palette|colour palette|shot list)\b",
    re.IGNORECASE,
)
_LIST_LINE_RE = re.compile(r"(?m)^\s*(?:[-*•]|\d+[.)])\s+")
_EXPLANATORY_HEADING_LINE_RE = re.compile(
    r"(?m)^\s*(?:[A-Z][\w-]*(?:\s+[A-Z][\w-]*){0,3})\s*:\s+"
)
_CONTRAST_TOPIC_RE = re.compile(
    r"(?:^|[.!?]\s+)(?P<topic>[A-Z][\w-]*(?:\s+[A-Za-z][\w-]*){0,3}),\s+"
    r"on the other hand\b",
)
_UNREQUESTED_COMPARISON_RE = re.compile(
    r"\b(?:just like|similar to|unlike)\s+(?:a|an|the)?\s*(?P<topic>[A-Za-z][\w-]*)\b",
    re.IGNORECASE,
)
_EXPLICIT_PRIOR_ANSWER_FOLLOW_UP_RE = re.compile(
    r"\b(?:what|which|how)\b[^.!?]{0,80}\b(?:"
    r"you\s+(?:just\s+)?(?:gave|mentioned|said|explained|described)"
    r"|that\s+(?:answer|explanation|reason|detail)"
    r"|(?:there|above|earlier|before))\b",
    re.IGNORECASE,
)
_SCOPED_MEMORY_RECALL_RE = re.compile(
    r"\b(?:"
    r"(?:what|which|where|when)\b[^.!?]{0,100}\b(?:"
    r"identifier|marker|code|label|date|port|number|phrase|value|fact|token"
    r")\b|"
    r"(?:repeat|recall|remember|remind\s+me|tell\s+me|what\s+did\s+i)\b"
    r"[^.!?]{0,100}\b(?:"
    r"identifier|marker|code|label|date|port|number|phrase|value|fact|token"
    r")\b|"
    r"\b(?:current|active|latest|exact)\s+(?:"
    r"identifier|marker|code|label|date|port|number|phrase|value|fact|token"
    r")\b"
    r")",
    re.IGNORECASE,
)
_MEMORY_RECALL_DENIAL_RE = re.compile(
    r"\b(?:old|previous|prior|superseded|outdated)\b|"
    r"\b(?:without|do\s+not|don't|never)\s+repeating?\b|"
    r"\b(?:not|never)\s+(?:repeat|reuse|use)\b",
    re.IGNORECASE,
)
_FORGET_COMMAND_RE = re.compile(
    r"^\s*(?:forget|erase)\s+(?P<target>.+?)\s*$",
    re.IGNORECASE,
)
_CONFIRMATION_LITERAL_RE = re.compile(
    r"\b[A-Za-z][A-Za-z0-9_]*-[A-Za-z0-9_-]*\d[A-Za-z0-9_-]*\b"
)
_TYPED_MEMORY_DECLARATION_RE = re.compile(
    r"\b(?:remember|store|save|note|the)\b"
    r"[^.!?\n]{0,80}?\b(?:identifier|code|marker|label|port|number|phrase|value)\b"
    r"(?:\s+for\b[^:,.!?]{0,40})?\s*(?:is|=|:|was|became|becomes|now)\s*"
    r"(?P<value>[A-Z0-9][A-Za-z0-9_-]*(?:\s+[A-Z0-9][A-Za-z0-9_-]*){0,5}?)"
    r"(?=\s*(?:[.!?]|$))",
    re.IGNORECASE,
)
_OPAQUE_MULTIWORD_VALUE_RE = re.compile(
    r"\b[A-Z]{2,}(?:\s+(?:\d{1,6}|[A-Z]{2,})){1,4}\b"
)
#: A single opaque error/status code token joined by underscores. The multiword pattern above
#: needs spaces and the confirmation pattern needs a hyphen+digit, so ERR_AWS_DENIED, ERR_NZMDFS
#: and SEC_DENIED_0x9F -- the exact literals that leaked across turns on the served surface --
#: matched NEITHER and were never even extracted. Deliberately conservative: a code-y prefix OR a
#: digit after an underscore, so a leaked status code is caught but an ordinary all-caps constant
#: (API_KEY, MAX_SIZE, HTTP_OK, NEW_YORK) is not -- those legitimately appear in code answers.
_OPAQUE_CODE_TOKEN_RE = re.compile(
    r"\b(?:"
    r"(?:ERR|ERROR|SEC|WARN|FATAL|STATUS|CODE|DENIED|FAIL|EXC)_[A-Za-z0-9_]{2,}"
    r"|[A-Z][A-Za-z0-9]*_[A-Za-z0-9]*\d[A-Za-z0-9_]*"
    r")\b"
)
_CONFIRMATION_REQUEST_RE = re.compile(
    r"\b(?:confirm|repeat|echo)\b",
    re.IGNORECASE,
)
_GENERIC_FOLLOW_UP_RE = re.compile(
    r"\b(?:let me know if you need|feel free to ask|if you have any other questions|"
    r"i can also help|would you like me to|don't hesitate to ask|"
    r"do you relate to|does that resonate(?: with you)?|"
    r"how can i (?:help|assist)(?: you)?(?: today)?|"
    r"do you have any (?:specific )?(?:topics?|questions?)(?: in mind)?|"
    r"what (?:would|do) you like to (?:discuss|do|work on|talk about|explore))\b",
    re.IGNORECASE,
)
_ASSISTANCE_REQUEST_RE = re.compile(
    r"\b(?:what|how)\b[^.!?]{0,50}\b(?:help|assist|support|can you do)\b"
    r"|\bwhat can you do\b",
    re.IGNORECASE,
)
_OPEN_EXPLANATION_QUESTION_RE = re.compile(
    r"\b(?:why|how)\b",
    re.IGNORECASE,
)
_QUANTITATIVE_HOW_RE = re.compile(
    r"^\s*how\s+(?:many|much|long|old|often)\b",
    re.IGNORECASE,
)
_EXPLICIT_WORD_SHAPE_RE = re.compile(
    r"\b(?:one|two|three|\d+)\s*[- ]?words?\b",
    re.IGNORECASE,
)
_DETAIL_REQUEST_RE = re.compile(
    r"\b(?:detailed|in detail|step[- ]by[- ]step|steps|list|examples|thorough|"
    r"compare|pros and cons|explain fully|deep dive)\b",
    re.IGNORECASE,
)
#: A stated length or long-form structure is an explicit CONTRACT, and it outranks the brevity
#: default the same way every other stated output contract outranks a heuristic. Measured live
#: 2026-08-15 (MF-12): "write me a 500-word article: age, history, funny facts..." matched no
#: detail keyword, so the 64-word ceiling applied and the runtime shipped TWO sentences with no
#: disclosure. Multi-digit word counts only: "two words"/"one word" is the SHORT exact contract
#: (_EXPLICIT_WORD_SHAPE_RE) and keeps its own handling.
_STATED_LENGTH_REQUEST_RE = re.compile(
    r"\b\d{2,5}\s*[- ]?words?\b"
    r"|\b(?:\d+|two|three|four|five|several|multiple)\s+paragraphs?\b"
    r"|\b(?:an?\s+)?(?:article|essay|blog\s+post|write[- ]?up|long[- ]?form|full\s+(?:report|"
    r"breakdown|explanation|analysis|summary|guide)|comprehensive\b|in[- ]depth)"
    r"|\bat\s+least\s+\d{2,5}\s*(?:words?|characters?|sentences?)\b",
    re.IGNORECASE,
)


def _length_contract_lifts_ceiling(user_text: str) -> bool:
    from core.response_constraints import parse_clause_response_constraint

    constraint = parse_clause_response_constraint(str(user_text or ""))
    # A structured output has its own shape/length authority. Cutting it at an
    # ordinary-prose word boundary can destroy a row or a closing code fence.
    return bool(
        _DETAIL_REQUEST_RE.search(str(user_text or ""))
        or _STATED_LENGTH_REQUEST_RE.search(str(user_text or ""))
        or (constraint is not None and constraint.presentation_format)
    )
_WORD_RE = re.compile(r"\b[\w'-]+\b", re.UNICODE)
_SENTENCE_RE = re.compile(r".+?(?:[.!?]+(?=\s|$)|$)", re.DOTALL)
_DEFAULT_ORDINARY_CHAT_MAX_WORDS = 64


@dataclass(frozen=True)
class OrdinaryChatOutputCheck:
    """A redacted verdict suitable for runtime receipts."""

    allowed: bool
    reasons: tuple[str, ...] = ()
    signal_groups: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reasons": list(self.reasons),
            "signal_groups": list(self.signal_groups),
        }


def ordinary_chat_output_policy(
    *,
    prompt_profile: str,
    output_mode: str,
    creative_medium: str | None = None,
    user_text: str = "",
    prior_turn_literal_hashes: tuple[str, ...] = (),
    forgotten_prior_turn_literal_hashes: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Return server-derived policy metadata without retaining request text."""
    # The prompt profile is downstream inference and can be wrong.  Only an
    # explicit image/video request is allowed to disable this ordinary-chat
    # guard, so a misclassified normal turn cannot leak a director prompt.
    ordinary = (
        str(output_mode or "").strip() in {"plain_text", "text"}
        and str(creative_medium or "").strip().lower() not in {"image", "video"}
    )
    from core.plain_task_routing import ordinary_plain_request_count

    required_parts = ordinary_plain_request_count(user_text)
    return {
        "mode": "ordinary_chat" if ordinary else "not_applicable",
        "prompt_profile": str(prompt_profile or "unknown"),
        "detail_requested": "true" if _length_contract_lifts_ceiling(user_text) else "false",
        "max_words": (
            str(_DEFAULT_ORDINARY_CHAT_MAX_WORDS)
            if ordinary and not _length_contract_lifts_ceiling(user_text)
            else ""
        ),
        "prior_turn_literal_hashes": list(
            dict.fromkeys(
                str(value).strip().lower()
                for value in prior_turn_literal_hashes
                if str(value).strip()
            )
        ),
        "forgotten_prior_turn_literal_hashes": list(
            dict.fromkeys(
                str(value).strip().lower()
                for value in forgotten_prior_turn_literal_hashes
                if str(value).strip()
            )
        ),
        "scoped_memory_recall": _is_scoped_memory_recall_request(user_text),
        "required_numbered_parts": required_parts if required_parts >= 2 else 0,
    }


def prior_turn_literal_hashes(
    messages: list[dict[str, Any]] | None,
    *,
    current_user_text: str,
) -> tuple[str, ...]:
    """Return redacted opaque identifiers from prior user turns only.

    The response guard needs to recognize an unrelated identifier without adding
    private prior-turn text to provider manifests or runtime receipts.
    """
    current_literals = {
        _literal_hash(value)
        for value in _guard_literal_values(str(current_user_text or ""))
    }
    hashes: list[str] = []
    for message in list(messages or []):
        if str(message.get("role") or "").strip().lower() != "user":
            continue
        for value in _guard_literal_values(str(message.get("content") or "")):
            digest = _literal_hash(value)
            if digest not in current_literals:
                hashes.append(digest)
    return tuple(dict.fromkeys(hashes))


def forgotten_prior_turn_literal_hashes(
    messages: list[dict[str, Any]] | None,
) -> tuple[str, ...]:
    """Hash literals named by prior forget commands in the current chat."""
    hashes: list[str] = []
    for message in list(messages or []):
        if str(message.get("role") or "").strip().lower() != "user":
            continue
        match = _FORGET_COMMAND_RE.match(str(message.get("content") or ""))
        if not match:
            continue
        target = re.sub(
            r"\s+from\s+(?:this|the\s+current)\s+chat\s*\??$",
            "",
            match.group("target").strip().strip(".!?"),
            flags=re.IGNORECASE,
        )
        for value in _guard_literal_values(target):
            hashes.append(_literal_hash(value))
    return tuple(dict.fromkeys(hashes))


def _literal_hash(value: str) -> str:
    return hashlib.sha256(str(value or "").strip().casefold().encode("utf-8")).hexdigest()


def _guard_literal_values(text: str) -> tuple[str, ...]:
    values = list(_CONFIRMATION_LITERAL_RE.findall(str(text or "")))
    values.extend(
        match.group("value").strip()
        for match in _TYPED_MEMORY_DECLARATION_RE.finditer(str(text or ""))
        if match.group("value").strip()
    )
    values.extend(_OPAQUE_MULTIWORD_VALUE_RE.findall(str(text or "")))
    values.extend(_OPAQUE_CODE_TOKEN_RE.findall(str(text or "")))
    return tuple(dict.fromkeys(values))


def _is_scoped_memory_recall_request(text: str) -> bool:
    """Allow prior-turn literals only for an explicit, typed memory recall."""
    normalized = " ".join(str(text or "").split())
    return bool(
        _SCOPED_MEMORY_RECALL_RE.search(normalized)
        and not _MEMORY_RECALL_DENIAL_RE.search(normalized)
    )


def _max_words(policy: dict[str, Any]) -> int | None:
    try:
        value = int(str(policy.get("max_words") or ""))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _prior_literal_contamination(
    text: str, policy: dict[str, Any], current_user_text: str
) -> OrdinaryChatOutputCheck | None:
    """A prior turn's opaque literal must not answer a later, unrelated turn -- in ANY mode.

    Measured on the served surface, 2026-08-15: an exact-literal turn ("Output exactly
    ERR_AWS_DENIED") contaminated a LATER array-logic puzzle, which answered "ERR_AWS_DENIED". The
    contamination rejection already existed but ran only for `ordinary_chat`; a turn classified
    `research`/`unknown` bypassed it, and the extractor could not even see a single ERR_ token.

    Two exceptions keep it from eating legitimate output: a literal the CURRENT turn itself states
    (its own contract), and an explicit scoped recall ("what did you say the code was?").
    """

    rendered = str(text or "").strip()
    if not rendered or "```" in rendered:
        return None
    normalized_user_text = " ".join(str(current_user_text or "").lower().split())
    explicit_memory_recall = (
        _is_scoped_memory_recall_request(normalized_user_text)
        if normalized_user_text
        else bool(policy.get("scoped_memory_recall"))
    )
    prior_hashes = {
        str(v).strip().lower()
        for v in list(policy.get("prior_turn_literal_hashes") or [])
        if str(v).strip()
    }
    forgotten_hashes = {
        str(v).strip().lower()
        for v in list(policy.get("forgotten_prior_turn_literal_hashes") or [])
        if str(v).strip()
    }
    # A literal the current turn ITSELF states is its own, never contamination -- use the full
    # extractor so a current exact-literal contract exempts its own code.
    current_hashes = {_literal_hash(v) for v in _guard_literal_values(current_user_text)}
    rendered_values = _guard_literal_values(rendered)
    for value in rendered_values:
        digest = _literal_hash(value)
        if digest in forgotten_hashes and digest not in current_hashes:
            return OrdinaryChatOutputCheck(
                allowed=False,
                reasons=("forgotten_memory_literal",),
                signal_groups=("forgotten_memory",),
            )
    if prior_hashes and not explicit_memory_recall:
        for value in rendered_values:
            digest = _literal_hash(value)
            if digest in prior_hashes and digest not in current_hashes:
                return OrdinaryChatOutputCheck(
                    allowed=False,
                    reasons=("unrequested_prior_turn_literal",),
                    signal_groups=("prior_turn_identifier",),
                )
    return None


def inspect_ordinary_chat_output(
    text: str,
    policy: dict[str, Any] | None,
    *,
    current_user_text: str = "",
) -> OrdinaryChatOutputCheck:
    """Reject strong visual-prompt evidence on an ordinary chat turn.

    Cross-turn literal contamination is checked in EVERY mode via `_prior_literal_contamination`;
    the structure/visual-prompt heuristics below stay ordinary-chat-specific.
    """
    if not isinstance(policy, dict):
        return OrdinaryChatOutputCheck(allowed=True)
    contamination = _prior_literal_contamination(str(text or ""), policy, current_user_text)
    if contamination is not None:
        return contamination
    if policy.get("mode") != "ordinary_chat":
        return OrdinaryChatOutputCheck(allowed=True)

    rendered = str(text or "").strip()
    if not rendered or "```" in rendered:
        return OrdinaryChatOutputCheck(allowed=True)
    normalized_user_text = " ".join(str(current_user_text or "").lower().split())
    required_parts = int(policy.get("required_numbered_parts") or 0)
    if required_parts >= 2:
        from core.plain_task_routing import ordinary_multi_part_answer_complete

        # Prefer the original text when available; the policy count is still a safe fallback for
        # the final display backstop, where request text is intentionally not retained.
        if current_user_text:
            complete = ordinary_multi_part_answer_complete(current_user_text, rendered)
        else:
            from core.plain_task_routing import ordinary_answer_part_indexes

            present = ordinary_answer_part_indexes(rendered)
            complete = all(index in present for index in range(1, required_parts + 1))
        if not complete:
            return OrdinaryChatOutputCheck(
                allowed=False,
                reasons=("missing_requested_parts",),
                signal_groups=("multi_part_completeness",),
            )
    explicit_prior_answer_follow_up = bool(
        _EXPLICIT_PRIOR_ANSWER_FOLLOW_UP_RE.search(normalized_user_text)
    )
    required_literals = (
        tuple(dict.fromkeys(_CONFIRMATION_LITERAL_RE.findall(current_user_text)))
        if _CONFIRMATION_REQUEST_RE.search(current_user_text)
        else ()
    )
    if any(literal not in rendered for literal in required_literals):
        return OrdinaryChatOutputCheck(
            allowed=False,
            reasons=("missing_confirmed_literal",),
            signal_groups=("required_literal",),
        )
    # Prior-turn-literal and forgotten-literal contamination are now handled universally at the
    # top of this function (`_prior_literal_contamination`), for every mode -- not only here.
    for match in _CONTRAST_TOPIC_RE.finditer(rendered):
        topic = " ".join(match.group("topic").lower().split())
        if topic and topic not in normalized_user_text:
            return OrdinaryChatOutputCheck(
                allowed=False,
                reasons=("unrequested_contrast_topic",),
                signal_groups=("contrast_topic",),
            )
    for match in _UNREQUESTED_COMPARISON_RE.finditer(rendered):
        topic = match.group("topic").lower()
        if (
            topic
            and topic not in normalized_user_text
            and not explicit_prior_answer_follow_up
        ):
            return OrdinaryChatOutputCheck(
                allowed=False,
                reasons=("unrequested_prior_reference",),
                signal_groups=("prior_turn_bridge",),
            )
    groups: list[str] = []
    if _CAMERA_SIGNALS.search(rendered):
        groups.append("camera_or_composition")
    if _STYLE_SIGNALS.search(rendered):
        groups.append("visual_style")
    if _SCAFFOLD_SIGNALS.search(rendered):
        groups.append("prompt_scaffold")

    # A scaffold plus one independent class is strong evidence. Without a
    # scaffold, require prompt-specific detail as well; ordinary explanations
    # can legitimately mention both cameras and lighting.
    has_scaffold = "prompt_scaffold" in groups
    has_prompt_detail = bool(_PROMPT_DETAIL_SIGNALS.search(rendered))
    if not (
        (has_scaffold and len(groups) >= 2)
        or (len(groups) >= 2 and has_prompt_detail)
    ):
        word_count = len(_WORD_RE.findall(rendered))
        list_count = len(_LIST_LINE_RE.findall(rendered))
        heading_count = len(_EXPLANATORY_HEADING_LINE_RE.findall(rendered))
        has_generic_tail = bool(_GENERIC_FOLLOW_UP_RE.search(rendered))
        detail_requested = str(policy.get("detail_requested", "false")) == "true"
        # A normal conversational question does not need an unsolicited
        # mini-article. A list with three or more items is strong enough
        # structural evidence once it is long, even when the model omits the
        # usual generic follow-up line. Explicit detail requests stay exempt.
        unsolicited_list_overanswer = (
            not detail_requested
            and word_count >= 120
            and (list_count >= 3 or heading_count >= 3)
        )
        boilerplate_overanswer = (
            not detail_requested
            and word_count >= 180
            and (list_count >= 3 or heading_count >= 3)
            and has_generic_tail
        )
        if not policy.get("brevity_advisory") and (unsolicited_list_overanswer or boilerplate_overanswer):
            overanswer_signals = ["long_response"]
            if list_count >= 3:
                overanswer_signals.append("list_block")
            if heading_count >= 3:
                overanswer_signals.append("heading_block")
            if has_generic_tail:
                overanswer_signals.append("generic_follow_up")
            return OrdinaryChatOutputCheck(
                allowed=False,
                reasons=("boilerplate_overanswer",),
                signal_groups=tuple(overanswer_signals),
            )
        if (
            not policy.get("brevity_advisory")
            and not detail_requested
            and has_generic_tail
            and not _ASSISTANCE_REQUEST_RE.search(current_user_text)
        ):
            return OrdinaryChatOutputCheck(
                allowed=False,
                reasons=("unsolicited_generic_follow_up",),
                signal_groups=("generic_follow_up",),
            )
        max_words = _max_words(policy)
        question_for_underanswer = normalized_user_text
        if ":" in question_for_underanswer:
            question_for_underanswer = question_for_underanswer.rsplit(":", 1)[-1].strip()
        short_open_answer = (
            word_count <= 1
            and bool(_OPEN_EXPLANATION_QUESTION_RE.search(question_for_underanswer))
            and not bool(_QUANTITATIVE_HOW_RE.match(question_for_underanswer))
            and not bool(_EXPLICIT_WORD_SHAPE_RE.search(normalized_user_text))
        )
        if short_open_answer:
            return OrdinaryChatOutputCheck(
                allowed=False,
                reasons=("ordinary_answer_too_short",),
                signal_groups=("underanswer",),
            )
        if not policy.get("brevity_advisory") and max_words is not None and word_count > max_words:
            return OrdinaryChatOutputCheck(
                allowed=False,
                reasons=("ordinary_response_too_long",),
                signal_groups=("response_budget",),
            )
        return OrdinaryChatOutputCheck(allowed=True, signal_groups=tuple(groups))

    return OrdinaryChatOutputCheck(
        allowed=False,
        reasons=("image_prompt_shaped_output",),
        signal_groups=tuple(groups),
    )


def remove_unrequested_prior_turn_literals(
    text: str,
    policy: dict[str, Any] | None,
    *,
    current_user_text: str = "",
) -> str:
    """Remove only complete sentences that disclose an unrequested prior identifier."""
    if not isinstance(policy, dict):
        return str(text or "").strip()
    prior_literal_hashes = {
        str(value).strip().lower()
        for value in list(policy.get("prior_turn_literal_hashes") or [])
        if str(value).strip()
    }
    if not prior_literal_hashes:
        return str(text or "").strip()
    current_literal_hashes = {
        _literal_hash(value)
        for value in _guard_literal_values(current_user_text)
    }
    kept: list[str] = []
    for sentence in _SENTENCE_RE.findall(str(text or "").strip()):
        sentence_hashes = {
            _literal_hash(value)
            for value in _guard_literal_values(sentence)
        }
        if sentence_hashes & (prior_literal_hashes - current_literal_hashes):
            continue
        if sentence.strip():
            kept.append(sentence)
    return "".join(kept).strip()


def remove_unsolicited_generic_follow_up(text: str) -> str:
    """Remove only generic assistance-offer sentences from an otherwise valid reply."""
    return "".join(
        sentence
        for sentence in _SENTENCE_RE.findall(str(text or "").strip())
        if sentence.strip() and not _GENERIC_FOLLOW_UP_RE.search(sentence)
    ).strip()


def ordinary_chat_retry_instruction(policy: dict[str, Any] | None = None) -> str:
    """One neutral repair instruction; it contains no expected answer text."""
    budget = _max_words(dict(policy or {}))
    budget_instruction = (
        f" Keep the reply to at most {budget} words."
        if budget is not None
        else ""
    )
    required_parts = int(dict(policy or {}).get("required_numbered_parts") or 0)
    parts_instruction = (
        f" Answer all {required_parts} requested parts and number them 1 through {required_parts}; "
        "do not omit or merge a part."
        if required_parts >= 2
        else ""
    )
    return (
        "Answer only the user's current request as normal, directly relevant text. "
        "Do not introduce an analogy, comparison, or identifier from an earlier turn unless the user asks for it. "
        "Preserve every explicitly requested identifier exactly. "
        "Avoid generic lists and an unsolicited follow-up offer when the user did not ask for detail. "
        "Do not write an image, "
        "video, scene, camera, lighting, style, or prompt-description block unless the user "
        "explicitly asked for creative media prompting."
        + parts_instruction
        + budget_instruction
    )


def constrain_ordinary_chat_output(
    text: str,
    policy: dict[str, Any] | None,
) -> str:
    """Trim only a normal-chat overrun after its one model rewrite."""
    rendered = str(text or "").strip()
    max_words = _max_words(dict(policy or {}))
    words = list(_WORD_RE.finditer(rendered))
    if max_words is None or len(words) <= max_words:
        return rendered
    bounded = rendered[: words[max_words - 1].end()].rstrip(" \t\r\n,;:-")
    cut = _last_sentence_end(bounded)
    return bounded[:cut].rstrip() or bounded


def _last_sentence_end(text: str) -> int:
    """Offset just past the last real sentence terminator in `text`, or 0 if there is none.

    Two bugs lived in the previous form, and both reached users on the served surface. It collected
    sentences with `_SENTENCE_RE`, stripped each one, and rejoined them with `" "`::

        1.8L to 2.4L   ->   1. 8L to 2. 4L

    `_SENTENCE_RE` splits on any `.`, so the decimal point ended a "sentence"; stripping and
    rejoining then inserted a space that was never in the text. Measured live: "Here are the top 5
    best-selling cars ... 1. Toyota Corolla: ~186 mph (300 km/h), 1. 8L to 2. 4L", every engine size
    mangled. The rejoin also flattened newlines, so a numbered list arrived as one paragraph.

    Cutting the ORIGINAL text at an offset fixes both: nothing is re-assembled, so no space can be
    introduced and the list keeps its line breaks.

    A terminator is any run of `.!?` that is neither of the two things that merely look like one:

      * a decimal point -- digits on both sides (`1.8`);
      * a list marker -- digits at the start of a line (`4. Ford Focus`), which is what left an
        answer ending on a bare "3." after the word cap landed there.
    """

    last = 0
    for match in _TERMINATOR_RUN_RE.finditer(text):
        if _is_decimal_point(text, match) or _is_list_marker(text, match):
            continue
        last = match.end()
    return last


_TERMINATOR_RUN_RE = re.compile(r"[.!?]+")


def _is_decimal_point(text: str, match: re.Match[str]) -> bool:
    return (
        match.group(0) == "."
        and match.start() > 0
        and text[match.start() - 1].isdigit()
        and match.end() < len(text)
        and text[match.end()].isdigit()
    )


def _is_list_marker(text: str, match: re.Match[str]) -> bool:
    if match.group(0) != ".":
        return False
    index = match.start()
    if index == 0 or not text[index - 1].isdigit():
        return False
    while index > 0 and text[index - 1].isdigit():
        index -= 1
    while index > 0 and text[index - 1] in " \t":
        index -= 1
    return index == 0 or text[index - 1] == "\n"


def recover_bounded_ordinary_chat_output(
    text: str,
    policy: dict[str, Any] | None,
    *,
    current_user_text: str = "",
) -> str:
    """Remove a recognized unsolicited tail, never answer-bearing structure.

    A length/style check cannot establish that a prefix satisfies the request.
    Lists, headings and later sentences can contain the entire answer. If the
    remaining provider text still fails the guard, return no recovery and let
    the caller publish its typed failure, rather than certify a discarded task.
    """

    bounded = remove_unsolicited_generic_follow_up(str(text or "").strip())
    if len(_WORD_RE.findall(bounded)) < 3:
        return ""
    check = inspect_ordinary_chat_output(
        bounded,
        policy,
        current_user_text=current_user_text,
    )
    return bounded if check.allowed else ""


def ordinary_chat_safe_fallback() -> str:
    return "I couldn't produce a normal chat response for that request. Please try again."


def ordinary_chat_overanswer_failure(model_name: str = "") -> str:
    """Typed terminal message when neither same-provider rewrite nor local bounding is safe."""

    model = str(model_name or "").strip()
    subject = f"The pinned model `{model}`" if model else "The selected model"
    return (
        f"{subject} returned an overlong response that could not be safely condensed within the "
        "answer budget. No alternate model, paid call, or tool was used."
    )
