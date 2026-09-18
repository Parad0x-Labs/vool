"""Typed structure for raw multi-question batches with a trailing response scaffold.

This module recognizes structure only. It never answers a question and never assigns meaning to a
label. A contract exists only when a trailing run of empty response labels can be bound one-to-one
to the same number of preceding marked task rows.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

_SCAFFOLD_RE = re.compile(r"^\s*(?P<label>[A-Za-z]|\d{1,3})\s*[.)\]>:]\s*$")
_MARKED_TASK_RE = re.compile(
    r"^\s*(?:(?P<decorator>[-*+•‣▪▫◦·–—])\s*)?"
    r"(?:(?P<label>[A-Za-z]|\d{1,3})\s*[.)\]>:\-]\s*)?"
    r"(?P<body>\S.*)$"
)
_BATCH_AUTHORITY_RE = re.compile(
    r"\b(?:answer|evaluate|quiz|questions?|problems?|solve|name|true\s+or\s+false|"
    r"yes\s+or\s+no|each\s+question)\b",
    re.IGNORECASE,
)
_SINGLE_WORD_ROWS_RE = re.compile(
    r"\b(?:only\s+using|using|with|in)\s+(?:single|one)[ -]?words?\b"
    r"|\b(?:each|every)\s+(?:answer|response)\s+(?:must\s+be\s+)?(?:a\s+)?single\s+word\b",
    re.IGNORECASE,
)
_ROW_CHOICES_RE = re.compile(
    r"\b(?:tell\s+me\s+if\s+(?:they|each\s+one)\s+(?:is|are)|answer)\s+"
    r"(?P<first>[A-Za-z][A-Za-z'-]{0,30})\s+or\s+"
    r"(?P<second>[A-Za-z][A-Za-z'-]{0,30})"
    r"(?:\s+to\s+each(?:\s+question)?)?\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class StructuredBatchItem:
    output_label: str
    source_label: str | None
    start: int
    end: int
    request_start: int
    request_end: int
    original_text: str
    request_text: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StructuredBatchContract:
    labels: tuple[str, ...]
    items: tuple[StructuredBatchItem, ...]
    per_item_exact_words: int | None = None
    allowed_values: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "labels": list(self.labels),
            "items": [item.to_dict() for item in self.items],
            "per_item_exact_words": self.per_item_exact_words,
            "allowed_values": list(self.allowed_values),
        }


@dataclass(frozen=True)
class StructuredBatchApplication:
    text: str
    compliant: bool
    violations: tuple[str, ...]
    changed: bool = False


@dataclass(frozen=True)
class _Line:
    start: int
    end: int
    text: str


def _source_lines(text: str) -> tuple[_Line, ...]:
    lines: list[_Line] = []
    position = 0
    for raw in text.splitlines(keepends=True):
        end = position + len(raw)
        content_end = end
        while content_end > position and text[content_end - 1] in "\r\n":
            content_end -= 1
        lines.append(_Line(position, content_end, text[position:content_end]))
        position = end
    if position < len(text):
        lines.append(_Line(position, len(text), text[position:]))
    return tuple(lines)


def _task_match(line: _Line) -> re.Match[str] | None:
    match = _MARKED_TASK_RE.match(line.text)
    if match is None or not (match.group("decorator") or match.group("label")):
        return None
    return match


def parse_structured_batch(raw_text: str) -> StructuredBatchContract | None:
    """Bind a trailing empty-label scaffold to the last N preceding marked task rows."""

    source = str(raw_text or "")
    lines = _source_lines(source)
    nonempty = [index for index, line in enumerate(lines) if line.text.strip()]
    if len(nonempty) < 4:
        return None

    scaffold: list[tuple[int, re.Match[str]]] = []
    cursor = len(nonempty) - 1
    while cursor >= 0:
        index = nonempty[cursor]
        match = _SCAFFOLD_RE.match(lines[index].text)
        if match is None:
            break
        scaffold.append((index, match))
        cursor -= 1
    scaffold.reverse()
    if not 2 <= len(scaffold) <= 20:
        return None
    labels = tuple(match.group("label") for _, match in scaffold)
    if len(set(label.casefold() for label in labels)) != len(labels):
        return None

    scaffold_start = lines[scaffold[0][0]].start
    if not _BATCH_AUTHORITY_RE.search(source[:scaffold_start]):
        return None
    marked = [
        (index, match)
        for index in nonempty
        if index < scaffold[0][0] and (match := _task_match(lines[index])) is not None
    ]
    if len(marked) < len(labels):
        return None
    selected = marked[-len(labels):]

    items: list[StructuredBatchItem] = []
    for output_label, (line_index, match) in zip(labels, selected, strict=True):
        line = lines[line_index]
        body_start = line.start + match.start("body")
        body_end = line.start + match.end("body")
        request = source[body_start:body_end].strip()
        if not request or _SCAFFOLD_RE.fullmatch(request):
            return None
        items.append(
            StructuredBatchItem(
                output_label=output_label,
                source_label=match.group("label"),
                start=line.start,
                end=line.end,
                request_start=body_start,
                request_end=body_end,
                original_text=source[line.start:line.end],
                request_text=source[body_start:body_end],
            )
        )

    choice_match = _ROW_CHOICES_RE.search(source[:scaffold_start])
    allowed_values = (
        (choice_match.group("first").casefold(), choice_match.group("second").casefold())
        if choice_match is not None
        else ()
    )
    return StructuredBatchContract(
        labels=labels,
        items=tuple(items),
        per_item_exact_words=1 if _SINGLE_WORD_ROWS_RE.search(source[:scaffold_start]) else None,
        allowed_values=allowed_values,
    )


def structured_batch_guidance(contract: StructuredBatchContract) -> str:
    labels = ", ".join(contract.labels)
    requirements = [
        f"Return exactly {len(contract.labels)} non-empty physical lines",
        f"labelled in this exact order: {labels}",
        "using the form 'LABEL. answer' with exactly one answer on each line",
        "with no introduction, explanation outside those rows, or internal deliberation",
    ]
    if contract.per_item_exact_words is not None:
        requirements.append(f"with exactly {contract.per_item_exact_words} word(s) after every label")
    if contract.allowed_values:
        requirements.append("with each row value chosen from: " + ", ".join(contract.allowed_values))
    return "The current turn is a structured answer batch. " + "; ".join(requirements) + "."


def apply_structured_batch_contract(
    text: str,
    contract: StructuredBatchContract,
) -> StructuredBatchApplication:
    """Validate and canonically bind one answer row to every declared output label."""

    original = str(text or "").strip()
    lines = [line.strip() for line in original.splitlines() if line.strip()]
    violations: list[str] = []
    if len(lines) != len(contract.labels):
        violations.append("row_count")
    bound: list[str] = []
    if not violations:
        for expected, line in zip(contract.labels, lines, strict=True):
            match = re.fullmatch(
                r"(?P<label>[A-Za-z]|\d{1,3})\s*[.)\]>:]\s+(?P<body>\S.*?)\s*",
                line,
            )
            if match is None or match.group("label").casefold() != expected.casefold():
                violations.append("row_labels")
                break
            body = match.group("body").strip()
            if not body:
                violations.append("empty_row")
                break
            embedded_label = re.search(
                r"(?:^|\s)(?:" + "|".join(re.escape(label) for label in contract.labels)
                + r")\s*[.)\]>:]\s+",
                body,
                re.IGNORECASE,
            )
            if embedded_label is not None:
                violations.append("multiple_answers_in_row")
                break
            if contract.per_item_exact_words is not None:
                words = re.findall(r"\b[\w'-]+\b", body, re.UNICODE)
                if len(words) != contract.per_item_exact_words:
                    violations.append("per_item_exact_words")
                    break
            if contract.allowed_values and body.casefold().strip(" .") not in contract.allowed_values:
                violations.append("row_allowed_values")
                break
            bound.append(f"{expected}. {body}")
    final = "" if violations else "\n".join(bound)
    return StructuredBatchApplication(
        text=final,
        compliant=not violations,
        violations=tuple(violations),
        changed=final != original,
    )


__all__ = [
    "StructuredBatchApplication",
    "StructuredBatchContract",
    "StructuredBatchItem",
    "apply_structured_batch_contract",
    "parse_structured_batch",
    "structured_batch_guidance",
]
