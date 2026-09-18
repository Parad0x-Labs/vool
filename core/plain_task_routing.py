from __future__ import annotations

import re

_TRANSLATE_RE = re.compile(r"^\s*translate\b", re.IGNORECASE)
_REWRITE_RE = re.compile(r"^\s*(?:please\s+)?rewrite\b", re.IGNORECASE)
_SUMMARIZE_RE = re.compile(r"^\s*(?:please\s+)?(?:summari[sz]e|write\s+a\s+summary\s+of)\b", re.IGNORECASE)
_SIMPLE_CODE_RE = re.compile(
    r"^\s*(?:please\s+)?write\b.{0,80}\b(?:function|python|javascript|typescript|code)\b",
    re.IGNORECASE,
)
_SIMPLE_EXPLAIN_RE = re.compile(r"^\s*(?:please\s+)?explain\b", re.IGNORECASE)

# Ordinary requests that one answering model can handle together without tools or a decomposition
# pass. This is deliberately narrower than the generic turn planner: it recognizes only direct
# knowledge/text operations, and therefore cannot swallow a file, web, machine, or side-effecting
# request. The boundary matcher only splits outside quotes and only splits a conjunction when what
# follows has its own request head. "salt and pepper" therefore stays one subject, while "explain
# salt and calculate 8 x 7" becomes two requests.
_ORDINARY_REQUEST_HEAD_RE = re.compile(
    r"^(?:(?:please|pls|plz)\s+)?(?:"
    r"answer|calculate|calc|compute|define|describe|explain|give|gimme|list|make|mak|name|provide|"
    r"say|suggest|summari[sz]e|tell|translate|draft|rewrit(?:e)?|reword|write|"
    r"what|what'?s|whats|wht|wat|which|who|why|how|where|when"
    r")\b",
    re.IGNORECASE,
)
_CONNECTOR_REQUEST_RE = re.compile(
    r"\b(?:and|also|plus|then)\b\s*,?\s+(?="
    r"(?:(?:please|pls|plz)\s+)?(?:"
    r"answer|calculate|calc|compute|define|describe|explain|give|gimme|list|make|mak|name|provide|"
    r"say|suggest|summari[sz]e|tell|translate|draft|rewrit(?:e)?|reword|write|"
    r"what|what'?s|whats|wht|wat|which|who|why|how|where|when"
    r")\b)",
    re.IGNORECASE,
)
_INLINE_LIST_MARKER_RE = re.compile(r"(?<!\w)(?:[A-Za-z]|\d{1,2})\s*[).:-]\s+")
_LEADING_CONNECTOR_RE = re.compile(r"^(?:(?:and|also|plus|then)\s*,?\s+)+", re.IGNORECASE)
_RESPONSE_SHAPE_PREFIX_RE = re.compile(
    r"^(?:in\s+(?:(?:one|two|three|\d+)\s+)?(?:plain\s+|short\s+|brief\s+)?"
    r"(?:sentence|sentences|line|lines|words?)|briefly|in\s+brief)\s*,?\s+",
    re.IGNORECASE,
)
_MULTIPART_PREAMBLE_RE = re.compile(
    r"^(?:(?:please|pls|plz)\s+)?(?:"
    r"(?:do|answer|handle)\s+(?:these|them|all|em)"
    r"(?:\s+(?:separately|separate|below|in\s+order|parts?))*"
    r"|(?:three|3)\s+(?:little|lil|small|quick)\s+(?:things|requests?)"
    r"(?:\s+(?:please|pls|plz))?"
    r")\s*:?$",
    re.IGNORECASE,
)
_MULTIPART_PREAMBLE_PREFIX_RE = re.compile(
    r"^(?:(?:please|pls|plz)\s+)?(?:"
    r"(?:do|answer|handle)\s+(?:these|them|all|em)"
    r"(?:\s+(?:separately|separate|below|in\s+order|parts?))*"
    r"|(?:three|3)\s+(?:little|lil|small|quick)\s+(?:things|requests?)"
    r"(?:\s+(?:please|pls|plz))?"
    r")\s*:\s*",
    re.IGNORECASE,
)
_OPERATIONAL_REQUEST_RE = re.compile(
    r"\b(?:search|grep|find)\b[^.!?;\n]{0,80}\b(?:files?|folders?|workspace|repo(?:sitory)?|codebase)\b|"
    r"\b(?:files?|folders?|workspace|repo(?:sitory)?|codebase)\b[^.!?;\n]{0,80}\b(?:search|grep|find)\b|"
    r"\b(?:read|open|inspect|edit|modify|create|delete|remove|rename|move|append|save|run|execute|"
    r"download|upload|send|post|write)\b[^.!?;\n]{0,80}\b(?:file|folder|directory|workspace|repo(?:sitory)?|"
    r"codebase|path|terminal|shell|command|script|readme)\b|"
    r"\b(?:look\s*up|browse|search)\b[^.!?;\n]{0,80}\b(?:web|online|internet|latest|current|news|weather|"
    r"forecast|price|score)\b",
    re.IGNORECASE,
)
_CONCRETE_FILE_OR_PATH_RE = re.compile(
    r"(?:^|[\s('\"`])(?:~|\.{1,2})?/[^\s'\"`,;)]{2,}|"
    r"\b[A-Za-z0-9_.-]+\.(?:py|toml|json|ya?ml|md|txt|ts|tsx|js|jsx|csv|pdf|docx?)\b",
    re.IGNORECASE,
)
_LOCAL_RESOURCE_REQUEST_RE = re.compile(
    r"\b(?:where|which\s+place|what\s+place)\s+(?:can|could|should|would)\s+(?:i|we)\b|"
    r"\b(?:find|locate|recommend|suggest)\b[^.!?;\n]{0,100}"
    r"\b(?:near\s+me|nearby|around\s+me|in\s+my\s+area)\b|"
    r"\b(?:near\s+me|nearby|in\s+my\s+area)\b",
    re.IGNORECASE,
)
_ANSWER_PART_MARKER_RE = re.compile(
    r"(?m)^\s*(?:\*\*)?(?P<marker>[A-Za-z]|\d{1,2})[).:-](?:\*\*)?\s+\S"
)

# These labels are an upstream semantic verdict, not words guessed from the request. A research
# turn must retain the tool lane even when its related subquestions happen to look like an
# ordinary multipart question grammatically.
_TOOL_REACHABLE_MULTIPART_TASK_CLASSES = frozenset({"research", "chat_research"})

_QUOTE_PAIRS = {'"': '"', "“": "”", "'": "'", "‘": "’"}


def _is_word_apostrophe(text: str, index: int, char: str) -> bool:
    return bool(
        char in {"'", "’"}
        and index > 0
        and index + 1 < len(text)
        and text[index - 1].isalnum()
        and text[index + 1].isalnum()
    )


def _unquoted_match_starts(text: str, pattern: re.Pattern[str]) -> tuple[re.Match[str], ...]:
    """Return regex matches whose start is outside a quoted span."""

    matches = list(pattern.finditer(text))
    if not matches:
        return ()
    accepted: list[re.Match[str]] = []
    closing: str | None = None
    match_index = 0
    for index, char in enumerate(text):
        if closing is not None:
            if char == closing:
                closing = None
            continue
        if char in _QUOTE_PAIRS and not _is_word_apostrophe(text, index, char):
            closing = _QUOTE_PAIRS[char]
            continue
        while match_index < len(matches) and matches[match_index].start() < index:
            match_index += 1
        if match_index < len(matches) and matches[match_index].start() == index:
            accepted.append(matches[match_index])
            match_index += 1
    return tuple(accepted)


def _split_at_matches(text: str, matches: tuple[re.Match[str], ...]) -> list[str]:
    if not matches:
        return [text]
    parts: list[str] = []
    start = 0
    for match in matches:
        prefix = text[start : match.start()].strip()
        if prefix:
            parts.append(prefix)
        start = match.end()
    suffix = text[start:].strip()
    if suffix:
        parts.append(suffix)
    return parts


def _split_unquoted_sentences(text: str) -> list[str]:
    parts: list[str] = []
    start = 0
    closing: str | None = None
    for index, char in enumerate(text):
        if closing is not None:
            if char == closing:
                closing = None
            continue
        if char in _QUOTE_PAIRS and not _is_word_apostrophe(text, index, char):
            closing = _QUOTE_PAIRS[char]
            continue
        if char in ".?!;\n":
            part = text[start : index + (0 if char == "\n" else 1)].strip()
            if part:
                parts.append(part)
            start = index + 1
    suffix = text[start:].strip()
    if suffix:
        parts.append(suffix)
    return parts


def _outside_quoted_spans(text: str) -> str:
    """Text outside quoted examples, preserving spaces so word boundaries remain trustworthy."""

    visible: list[str] = []
    closing: str | None = None
    raw = str(text or "")
    for index, char in enumerate(raw):
        if closing is not None:
            if char == closing:
                closing = None
            visible.append(" ")
            continue
        if char in _QUOTE_PAIRS and not _is_word_apostrophe(raw, index, char):
            closing = _QUOTE_PAIRS[char]
            visible.append(" ")
            continue
        visible.append(char)
    return "".join(visible)


def _normalized_ordinary_clause(text: str) -> str:
    clause = str(text or "").strip(" \t\r\n.!?;:")
    clause = _LEADING_CONNECTOR_RE.sub("", clause).strip()
    clause = _RESPONSE_SHAPE_PREFIX_RE.sub("", clause).strip()
    return clause


def ordinary_plain_requests(text: str) -> tuple[str, ...]:
    """Return every request when the whole turn is a small, non-operational text task.

    An operational fragment invalidates the whole ordinary lane. That is load-bearing: a message
    containing "search the files" plus two prose transformations must still reach workspace tools,
    not become a no-tools prompt merely because two clauses happen to be ordinary in isolation.
    """

    raw = str(text or "").strip()
    if not raw or _OPERATIONAL_REQUEST_RE.search(raw) or _CONCRETE_FILE_OR_PATH_RE.search(raw):
        return ()
    raw = _MULTIPART_PREAMBLE_PREFIX_RE.sub("", raw, count=1).strip()

    requests: list[str] = []
    for sentence in _split_unquoted_sentences(raw):
        listed = _split_at_matches(
            sentence,
            _unquoted_match_starts(sentence, _INLINE_LIST_MARKER_RE),
        )
        for listed_part in listed:
            connected = _split_at_matches(
                listed_part,
                _unquoted_match_starts(listed_part, _CONNECTOR_REQUEST_RE),
            )
            for part in connected:
                clause = _normalized_ordinary_clause(part)
                if not clause or _MULTIPART_PREAMBLE_RE.fullmatch(clause):
                    continue
                if not _ORDINARY_REQUEST_HEAD_RE.match(clause):
                    return ()
                requests.append(clause)
    return tuple(requests)


def ordinary_plain_request_count(text: str) -> int:
    """Count independent, non-operational requests in a small plain-text turn.

    The count is only a routing/prompt-shape signal.  It does not split or execute the turn, so a
    three-part explanation/calculation/title prompt still makes exactly one answering-model call.
    """

    return len(ordinary_plain_requests(text))


def multipart_task_class_requires_tool_reachability(task_class: str) -> bool:
    """Whether an upstream task verdict forbids demotion to ordinary no-tool chat."""

    return str(task_class or "").strip().lower() in _TOOL_REACHABLE_MULTIPART_TASK_CLASSES


def multipart_has_non_plain_request(text: str) -> bool:
    """Whether any unquoted part needs information or action beyond one plain model answer.

    This is deliberately about capability shape, not named entities or reported prompts. Explicit
    workspace/web/action forms are already owned by the operational and path recognizers. Current
    real-world facts reuse the runtime's shared recency authority, while local resource questions
    are recognized by their place-seeking frame (``where can I ...`` / ``... near me``), not by a
    vocabulary of businesses, cities, or services.
    """

    visible = " ".join(_outside_quoted_spans(text).strip().split())
    if not visible:
        return False
    if _OPERATIONAL_REQUEST_RE.search(visible) or _CONCRETE_FILE_OR_PATH_RE.search(visible):
        return True

    from core.task_router import looks_like_live_recency_lookup

    return bool(
        looks_like_live_recency_lookup(visible)
        or _LOCAL_RESOURCE_REQUEST_RE.search(visible)
    )


def is_ordinary_multi_part_plain_task(text: str, *, task_class: str = "") -> bool:
    """True when every part belongs in one complete, tool-free model answer."""

    if multipart_task_class_requires_tool_reachability(task_class):
        return False
    return ordinary_plain_request_count(text) >= 2 and not multipart_has_non_plain_request(text)


def ordinary_multi_part_answer_complete(user_text: str, answer_text: str) -> bool:
    """Whether a multi-part plain answer visibly accounts for every requested part.

    Multi-part minimal prompts require numbered answers. That gives the runtime a deterministic
    completeness check instead of pretending it can infer semantic coverage from fluent prose.
    """

    required = ordinary_plain_request_count(user_text)
    if required < 2:
        return True
    present = ordinary_answer_part_indexes(answer_text)
    return all(index in present for index in range(1, required + 1))


def ordinary_answer_part_indexes(answer_text: str) -> frozenset[int]:
    """Sequential answer indexes rendered with either numeric or alphabetic labels."""

    present: set[int] = set()
    for match in _ANSWER_PART_MARKER_RE.finditer(str(answer_text or "")):
        marker = match.group("marker")
        present.add(int(marker) if marker.isdigit() else ord(marker.upper()) - ord("A") + 1)
    return frozenset(present)


def plain_task_kind(text: str) -> str:
    """Return the plain model task kind for one-shot text tasks.

    These are user-facing generation tasks, not operational workflows. Keeping this
    classifier small prevents normal copy/translation/explanation prompts from
    inheriting tool doctrine, action routing, or unrelated session context.
    """
    raw = " ".join(str(text or "").strip().split())
    if not raw:
        return ""
    if is_ordinary_multi_part_plain_task(text):
        return "multi_part_qa"
    if _TRANSLATE_RE.search(raw):
        return "translation"
    if _REWRITE_RE.search(raw):
        return "rewrite"
    if _SUMMARIZE_RE.search(raw):
        return "summary"
    if _SIMPLE_CODE_RE.search(raw):
        return "simple_code"
    if _SIMPLE_EXPLAIN_RE.search(raw):
        return "explanation"
    return ""


def is_plain_task(text: str) -> bool:
    return bool(plain_task_kind(text))


__all__ = [
    "is_ordinary_multi_part_plain_task",
    "is_plain_task",
    "multipart_has_non_plain_request",
    "multipart_task_class_requires_tool_reachability",
    "ordinary_answer_part_indexes",
    "ordinary_multi_part_answer_complete",
    "ordinary_plain_request_count",
    "ordinary_plain_requests",
    "plain_task_kind",
]
