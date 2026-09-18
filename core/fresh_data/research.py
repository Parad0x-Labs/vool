"""Field-level research completeness: verified values survive missing siblings.

This is deliberately a coverage compiler, not a prose judge.  The caller names the fields the
request requires and supplies evidence objects.  Every required field then appears exactly once as
verified or unavailable; one missing field never discards the verified remainder.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol


class ResearchFieldStatus(str, Enum):
    VERIFIED = "verified"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class ResearchFieldEvidence:
    field: str
    value: str
    source: str
    source_url: str = ""
    retrieved_at: str = ""


@dataclass(frozen=True)
class ResearchFieldCoverage:
    field: str
    status: ResearchFieldStatus
    value: str = ""
    source: str = ""
    source_url: str = ""
    retrieved_at: str = ""
    failure_reason: str = ""

    def to_dict(self) -> dict[str, str]:
        return {**self.__dict__, "status": self.status.value}


@dataclass(frozen=True)
class ResearchCoverageReport:
    subject: str
    fields: tuple[ResearchFieldCoverage, ...]
    retrieved_at: str

    @property
    def verified_count(self) -> int:
        return sum(item.status is ResearchFieldStatus.VERIFIED for item in self.fields)

    @property
    def unavailable_count(self) -> int:
        return len(self.fields) - self.verified_count

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "fields": [item.to_dict() for item in self.fields],
            "verified_count": self.verified_count,
            "unavailable_count": self.unavailable_count,
            "retrieved_at": self.retrieved_at,
        }


class ResearchProvider(Protocol):
    name: str

    def research_fields(
        self,
        subject: str,
        fields: Sequence[str],
        *,
        timeout_s: float = 8.0,
    ) -> Sequence[ResearchFieldEvidence]: ...


_RESEARCH_FRAME_RE = re.compile(
    r"^(?:research|look\s+up|find\s+out|check)\s+(?P<subject>.+?)\s+"
    r"(?:and\s+)?(?:tell|give|report|provide|return|list|identify|find)\s+(?:me\s+)?"
    r"(?:its|the)?\s*(?P<fields>.+)$",
    re.IGNORECASE,
)
_QUESTION_RESEARCH_FRAME_RE = re.compile(
    r"^what\s+is\s+(?P<subject>.+?)\s+"
    r"(?P<first_field>model|make|engine|colour|color|trim|year)\s*,\s*"
    r"(?:what|which)\s+(?P<fields>.+)$",
    re.IGNORECASE,
)
_FIELD_PREFIX_RE = re.compile(r"^(?:its|the|a|an|what|which)\s+", re.IGNORECASE)


@dataclass(frozen=True)
class StructuredResearchRequest:
    subject: str
    fields: tuple[str, ...]


def _split_fields(text: str) -> tuple[str, ...]:
    normalized = re.sub(r"\s+(?:and|&|plus)\s+", ",", str(text or ""), flags=re.IGNORECASE)
    fields: list[str] = []
    for raw in normalized.split(","):
        field = _FIELD_PREFIX_RE.sub("", raw.strip(" .;:-"), count=1).strip()
        if not field or len(field.split()) > 4:
            continue
        key = field.casefold()
        if key not in {item.casefold() for item in fields}:
            fields.append(field)
    return tuple(fields)


def parse_structured_research_request(text: str) -> StructuredResearchRequest | None:
    clean = " ".join(str(text or "").strip().strip(".?!").split())
    match = _RESEARCH_FRAME_RE.match(clean)
    if match:
        subject = match.group("subject").strip(" ,;:-")
        fields = _split_fields(match.group("fields"))
    else:
        question_match = _QUESTION_RESEARCH_FRAME_RE.match(clean)
        if not question_match:
            return None
        subject = question_match.group("subject").strip(" ,;:-")
        fields = _split_fields(
            f"{question_match.group('first_field')}, {question_match.group('fields')}"
        )
    if not subject or len(fields) < 2:
        return None
    return StructuredResearchRequest(subject=subject, fields=fields)


def _field_key(value: str) -> str:
    """Comparison identity for field labels, including harmless spelling variants."""

    key = " ".join(str(value or "").casefold().split())
    return {"colour": "color"}.get(key, key)


def compile_research_coverage(
    subject: str,
    required_fields: Sequence[str],
    evidence: Sequence[ResearchFieldEvidence],
    *,
    retrieved_at: str = "",
    unavailable_reason: str = "no verified evidence was returned for this field",
) -> ResearchCoverageReport:
    now = retrieved_at or datetime.now(timezone.utc).isoformat()
    by_field: dict[str, ResearchFieldEvidence] = {}
    for item in evidence:
        key = _field_key(item.field)
        if key and str(item.value or "").strip() and str(item.source or "").strip():
            by_field.setdefault(key, item)
    coverage: list[ResearchFieldCoverage] = []
    seen: set[str] = set()
    for raw_field in required_fields:
        field = " ".join(str(raw_field or "").split())
        key = _field_key(field)
        if not field or key in seen:
            continue
        seen.add(key)
        item = by_field.get(key)
        if item is None:
            coverage.append(
                ResearchFieldCoverage(
                    field=field,
                    status=ResearchFieldStatus.UNAVAILABLE,
                    retrieved_at=now,
                    failure_reason=unavailable_reason,
                )
            )
        else:
            coverage.append(
                ResearchFieldCoverage(
                    field=field,
                    status=ResearchFieldStatus.VERIFIED,
                    value=str(item.value).strip(),
                    source=str(item.source).strip(),
                    source_url=str(item.source_url or "").strip(),
                    retrieved_at=str(item.retrieved_at or now),
                )
            )
    return ResearchCoverageReport(
        subject=str(subject).strip(),
        fields=tuple(coverage),
        retrieved_at=now,
    )


def run_structured_research(
    request: StructuredResearchRequest,
    *,
    provider: ResearchProvider | Callable[..., Sequence[ResearchFieldEvidence]] | None,
    timeout_s: float = 8.0,
) -> ResearchCoverageReport:
    if provider is None:
        return compile_research_coverage(
            request.subject,
            request.fields,
            (),
            unavailable_reason="no structured research provider is configured",
        )
    try:
        if hasattr(provider, "research_fields"):
            evidence = provider.research_fields(
                request.subject,
                request.fields,
                timeout_s=timeout_s,
            )
        else:
            evidence = provider(request.subject, request.fields, timeout_s=timeout_s)
        return compile_research_coverage(request.subject, request.fields, tuple(evidence or ()))
    except Exception as exc:
        return compile_research_coverage(
            request.subject,
            request.fields,
            (),
            unavailable_reason=f"research provider failed: {type(exc).__name__}: {exc}"[:200],
        )


def render_research_coverage(report: ResearchCoverageReport) -> str:
    lines = [f"{report.subject} — requested field coverage:"]
    for item in report.fields:
        if item.status is ResearchFieldStatus.VERIFIED:
            source = f" (source: {item.source})" if item.source else ""
            lines.append(f"- {item.field}: {item.value}{source}")
        else:
            lines.append(f"- {item.field}: unavailable — {item.failure_reason}")
    return "\n".join(lines)


__all__ = [
    "ResearchCoverageReport",
    "ResearchFieldCoverage",
    "ResearchFieldEvidence",
    "ResearchFieldStatus",
    "ResearchProvider",
    "StructuredResearchRequest",
    "compile_research_coverage",
    "parse_structured_research_request",
    "render_research_coverage",
    "run_structured_research",
]
