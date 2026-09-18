"""Authoritative typed recall from the visible current-chat transcript."""

from __future__ import annotations

import re
from dataclasses import dataclass

from core.context_history_authority import is_same_chat_history_recall
from core.task_router import evaluate_direct_math_request, evaluate_word_math_request

_MATH_RECALL_SUBJECT_RE = re.compile(
    r"\b(?:math|maths|arithmetic|calculations?|equations?|multiplications?|multiply|multiplied|"
    r"products?|numbers?)\b",
    re.IGNORECASE,
)
_RESPONSE_ENTRY_RE = re.compile(
    r"^\s*(?:\*\*)?(?P<marker>[A-Za-z]|\d{1,2})[).:-](?:\*\*)?\s*(?P<body>\S.*)$"
)
_BULLET_ENTRY_RE = re.compile(r"^\s*[-*\u2022]\s+(?P<body>\S.*)$")
_PART_RE = re.compile(r"\bpart\s+(?P<part>[A-Za-z]|\d{1,2})\b", re.IGNORECASE)
_ORDINAL_PART_RE = re.compile(
    r"\b(?P<part>first|second|third|fourth|fifth|sixth|\d{1,2}(?:st|nd|rd|th))\s+part\b",
    re.IGNORECASE,
)
_ITEM_RE = re.compile(r"\b(?:list\s+)?(?:item|entry|bullet)\s*(?P<part>\d{1,2})\b", re.IGNORECASE)
_FOR_LETTER_RE = re.compile(r"\bfor\s+(?P<part>[A-Za-z])\b", re.IGNORECASE)
_PLURAL_RE = re.compile(r"\b(?:all|list|show|answers|calculations|equations|products)\b", re.IGNORECASE)
_LABEL_RE = re.compile(
    r"^\s*(?P<label>title|heading|headline|name|word|phrase|sentence|line)\s*:\s*(?P<body>\S.*)$",
    re.IGNORECASE,
)
_NAMED_EXTERNAL_ACTOR_RE = re.compile(
    r"\b(?:did|does|has)\s+(?!(?:i|we|you)\b)[A-Z][A-Za-z'-]*"
    r"(?:\s+[A-Z][A-Za-z'-]*){0,2}\b"
)
_TYPO_WORDS = {
    "titl": "title",
    "headng": "heading",
    "sentnce": "sentence",
    "phrse": "phrase",
    "numbr": "number",
}
_KIND_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("title", ("title", "heading", "headline")),
    ("phrase", ("phrase",)),
    ("sentence", ("sentence", "line")),
    ("name", ("name", "called")),
    ("word", ("word",)),
    ("item", ("item", "entry", "bullet")),
    (
        "number",
        (
            "number",
            "value",
            "math",
            "maths",
            "calculation",
            "multiply",
            "multiplication",
            "product",
        ),
    ),
)


@dataclass(frozen=True)
class TranscriptRecall:
    """A value copied from visible current-chat history, or a proved visible absence."""

    response: str
    recall_kind: str
    found: bool
    route_reason: str


def _display_math_answer(answer: str) -> str:
    statement = str(answer or "").strip().rstrip(".")
    if "=" not in statement:
        return statement
    expression, result = statement.split("=", 1)
    expression = re.sub(r"\s*[xX*×]\s*", " × ", expression.strip())
    return f"{expression} = {result.strip()}"


def _completed_visible_exchanges(
    source_context: dict[str, object] | None,
) -> tuple[tuple[str, str], ...]:
    history = [
        item
        for item in list((source_context or {}).get("conversation_history") or [])
        if isinstance(item, dict)
        and str(item.get("role") or "").strip().lower() in {"user", "assistant"}
        and str(item.get("content") or "").strip()
    ]
    exchanges: list[tuple[str, str]] = []
    pending_user = ""
    for item in history:
        role = str(item.get("role") or "").strip().lower()
        content = str(item.get("content") or "").strip()
        if role == "user":
            pending_user = content
        elif pending_user:
            exchanges.append((pending_user, content))
            pending_user = ""
    return tuple(exchanges)


def _response_entries(text: str) -> dict[int, str]:
    entries: dict[int, str] = {}
    next_bullet = 1
    for line in str(text or "").splitlines():
        marker_match = _RESPONSE_ENTRY_RE.match(line)
        if marker_match:
            marker = marker_match.group("marker")
            index = int(marker) if marker.isdigit() else ord(marker.upper()) - ord("A") + 1
            entries[index] = marker_match.group("body").strip()
            next_bullet = max(next_bullet, index + 1)
            continue
        bullet_match = _BULLET_ENTRY_RE.match(line)
        if bullet_match:
            while next_bullet in entries:
                next_bullet += 1
            entries[next_bullet] = bullet_match.group("body").strip()
            next_bullet += 1
    return entries


def _recall_words(text: str) -> tuple[str, ...]:
    return tuple(
        _TYPO_WORDS.get(word.casefold(), word.casefold())
        for word in re.findall(r"[A-Za-z0-9']+", str(text or ""))
    )


def _recall_kind(text: str) -> str:
    words = set(_recall_words(text))
    for kind, aliases in _KIND_ALIASES:
        if words.intersection(aliases):
            return kind
    return ""


def _part_number(value: str) -> int | None:
    normalized = str(value or "").strip().casefold()
    ordinals = {
        "first": 1,
        "second": 2,
        "third": 3,
        "fourth": 4,
        "fifth": 5,
        "sixth": 6,
    }
    if normalized in ordinals:
        return ordinals[normalized]
    if len(normalized) == 1 and normalized.isalpha():
        return ord(normalized.upper()) - ord("A") + 1
    digits = re.match(r"(?P<number>\d{1,2})", normalized)
    return int(digits.group("number")) if digits else None


def _requested_part_number(text: str, *, recall_kind: str) -> int | None:
    for pattern in (_PART_RE, _ORDINAL_PART_RE, _ITEM_RE):
        match = pattern.search(str(text or ""))
        if match:
            return _part_number(match.group("part"))
    if recall_kind == "title":
        match = _FOR_LETTER_RE.search(str(text or ""))
        if match:
            return _part_number(match.group("part"))
    return None


def _request_names_kind(text: str, kind: str) -> bool:
    words = set(_recall_words(text))
    aliases = next((values for candidate, values in _KIND_ALIASES if candidate == kind), ())
    return bool(words.intersection(aliases))


def _clean_recalled_value(value: str, kind: str) -> str:
    rendered = str(value or "").strip()
    label = _LABEL_RE.match(rendered)
    if label and _recall_kind(label.group("label")) == kind:
        return label.group("body").strip()
    return rendered


def _visible_math_answers(exchanges: tuple[tuple[str, str], ...]) -> tuple[str, ...]:
    from core.plain_task_routing import ordinary_plain_requests

    recalled: list[str] = []
    seen: set[str] = set()
    for question, reply in exchanges:
        requests = ordinary_plain_requests(question) or (question,)
        entries = _response_entries(reply)
        for index, request in enumerate(requests, start=1):
            answer = evaluate_direct_math_request(request) or evaluate_word_math_request(request)
            if not answer:
                continue
            expected_result = str(answer).rstrip(".").rsplit("=", 1)[-1].strip()
            visible_answer = entries.get(index, reply)
            if not re.search(rf"(?<!\d){re.escape(expected_result)}(?!\d)", visible_answer):
                continue
            displayed = _display_math_answer(answer)
            if displayed and displayed not in seen:
                seen.add(displayed)
                recalled.append(displayed)
    return tuple(recalled)


def _visible_text_answer(
    user_input: str,
    *,
    kind: str,
    exchanges: tuple[tuple[str, str], ...],
) -> str:
    from core.plain_task_routing import ordinary_plain_requests

    requested_part = _requested_part_number(user_input, recall_kind=kind)
    for question, reply in reversed(exchanges):
        entries = _response_entries(reply)
        requests = ordinary_plain_requests(question)

        if requested_part is not None and requested_part in entries:
            # List items name the answer list itself. Other kinds name a request part; if that
            # request is available it must agree with the subject, so "part C date" cannot return a
            # title merely because both happen to be third.
            if (
                kind != "item"
                and requests
                and requested_part <= len(requests)
                and not _request_names_kind(requests[requested_part - 1], kind)
            ):
                continue
            return _clean_recalled_value(entries[requested_part], kind)

        if kind == "item":
            continue

        for index, request in enumerate(requests, start=1):
            if not _request_names_kind(request, kind):
                continue
            if index in entries:
                return _clean_recalled_value(entries[index], kind)
            if len(requests) == 1 and not entries:
                return _clean_recalled_value(reply, kind)

        for line in reply.splitlines():
            label = _LABEL_RE.match(line)
            if label and _recall_kind(label.group("label")) == kind:
                return label.group("body").strip()
    return ""


def same_chat_transcript_recall_fast_path(
    user_input: str,
    *,
    source_surface: str,
    source_context: dict[str, object] | None,
) -> TranscriptRecall | None:
    """Recall a typed value from completed visible exchanges without a provider or tool.

    The browser's current-chat transcript has already been namespace-bound at ingress. Arithmetic
    is re-evaluated but returned only when the visible reply contained that result; text is copied
    from a labelled answer part or from the response to a matching typed request. A typed request
    with no matching value returns a proved visible absence instead of asking a model to guess.
    """

    if source_surface not in {"channel", "openclaw", "api"}:
        return None
    if not is_same_chat_history_recall(user_input):
        return None
    # "Earlier" can describe a book, film, or outside person just as easily as this transcript.
    # A named third-party subject keeps that ambiguous question in normal conversation instead of
    # turning a stale local title into a purported answer about the outside subject.
    if _NAMED_EXTERNAL_ACTOR_RE.search(str(user_input or "")):
        return None
    exchanges = _completed_visible_exchanges(source_context)
    if not exchanges:
        return None

    kind = _recall_kind(user_input)
    if kind == "number" or _MATH_RECALL_SUBJECT_RE.search(str(user_input or "")):
        math_answers = _visible_math_answers(exchanges)
        if math_answers:
            response = "\n".join(math_answers) if _PLURAL_RE.search(str(user_input or "")) else math_answers[-1]
            return TranscriptRecall(
                response=response,
                recall_kind="number",
                found=True,
                route_reason="authoritative_visible_math_history",
            )

    if not kind:
        return None
    recalled = _visible_text_answer(
        user_input,
        kind=kind,
        exchanges=exchanges,
    )
    if recalled:
        return TranscriptRecall(
            response=recalled,
            recall_kind=kind,
            found=True,
            route_reason="authoritative_visible_text_history",
        )
    label = "list item" if kind == "item" else kind
    return TranscriptRecall(
        response=f"That {label} does not appear in the visible history of this chat.",
        recall_kind=kind,
        found=False,
        route_reason="authoritative_visible_history_absence",
    )


def same_chat_math_recall_fast_path(
    user_input: str,
    *,
    source_surface: str,
    source_context: dict[str, object] | None,
) -> str | None:
    """Compatibility entry point for callers that only accept visible arithmetic recall."""

    recalled = same_chat_transcript_recall_fast_path(
        user_input,
        source_surface=source_surface,
        source_context=source_context,
    )
    if recalled is None or recalled.recall_kind != "number" or not recalled.found:
        return None
    return recalled.response


__all__ = [
    "TranscriptRecall",
    "same_chat_math_recall_fast_path",
    "same_chat_transcript_recall_fast_path",
]
