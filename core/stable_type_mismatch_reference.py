"""Curated stable type facts for conservative category-mismatch questions.

The resolver does not diagnose symptoms or recommend treatment.  It recognizes only an explicit
medical-prescription mismatch involving one atomic, versioned technical type, identifies the type,
and directs the user to clarify the intended instruction with the prescriber.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

TYPE_FACT_REGISTRY_VERSION = "stable-technical-types.v1-2026-08-13"


@dataclass(frozen=True)
class StableTypeFact:
    key: str
    display_name: str
    aliases: tuple[str, ...]
    type_description: str
    source_id: str
    source_status: str
    source_url: str


STABLE_TYPE_FACTS = (
    StableTypeFact(
        key="html",
        display_name="HTML",
        aliases=("HTML",),
        type_description="a markup language and document format for web content",
        source_id="ISO/IEC 15445:2000",
        source_status="edition 1; confirmed 2023",
        source_url="https://www.iso.org/standard/27688.html",
    ),
    StableTypeFact(
        key="css",
        display_name="CSS",
        aliases=("CSS",),
        type_description="a style sheet language for styling structured documents",
        source_id="W3C CSS Snapshot 2025",
        source_status="W3C Group Note, 2025",
        source_url="https://www.w3.org/TR/css-2025/",
    ),
    StableTypeFact(
        key="json",
        display_name="JSON",
        aliases=("JSON",),
        type_description="a text data-interchange format",
        source_id="RFC 8259",
        source_status="Internet Standard, 2017",
        source_url="https://www.rfc-editor.org/info/rfc8259",
    ),
    StableTypeFact(
        key="pdf",
        display_name="PDF",
        aliases=("PDF",),
        type_description="an electronic document format",
        source_id="ISO 32000-2:2020",
        source_status="edition 2; confirmed",
        source_url="https://www.iso.org/standard/75839.html",
    ),
)

_FACT_BY_ALIAS = {
    alias.casefold(): fact for fact in STABLE_TYPE_FACTS for alias in fact.aliases
}
_TYPE_ALTERNATION = "|".join(
    sorted((re.escape(alias) for alias in _FACT_BY_ALIAS), key=len, reverse=True)
)
_PRESCRIPTION_RE = re.compile(
    rf"\b(?:my|the|a)?\s*(?:doctor|physician|clinician|prescriber)\s+"
    rf"(?:has\s+)?(?:prescribed|recommended|ordered)\s+(?:me\s+)?"
    rf"(?P<item>{_TYPE_ALTERNATION})\b"
    rf"(?=\s+(?:for|as|to\s+(?:take|treat))\b|\s*[,;.!?])",
    re.IGNORECASE,
)
_MEDICAL_CONTEXT_RE = re.compile(
    r"\b(?:headache|migraine|pain|fever|nausea|cough|infection|rash|symptoms?|illness|"
    r"medicine|medication|pill|dose|treatment)\b",
    re.IGNORECASE,
)
_MISMATCH_QUESTION_RE = re.compile(
    r"\b(?:(?:should|can|could|do)\s+i\s+(?:take|swallow|use)\b|"
    r"(?:category|type)\s+(?:mismatch|mistake|error)\b|"
    r"(?:is|isn't|is\s+not)\s+(?:it|that|this)\s+(?:medicine|medication)\b|"
    r"what(?:'s|\s+is)\s+wrong\s+with\s+that)\b",
    re.IGNORECASE,
)


def stable_type_mismatch_response(user_text: str) -> str | None:
    """Resolve a reviewed technical-object-as-medication category mismatch, or decline."""

    text = str(user_text or "").strip()
    prescription = _PRESCRIPTION_RE.search(text)
    if (
        prescription is None
        or _MEDICAL_CONTEXT_RE.search(text) is None
        or _MISMATCH_QUESTION_RE.search(text) is None
    ):
        return None
    fact = _FACT_BY_ALIAS.get(prescription.group("item").casefold())
    if fact is None:
        return None
    return (
        f"No. {fact.display_name} is {fact.type_description}, not medicine or medication. "
        "That is a category mismatch, not a treatment instruction. "
        "Do not try to take it; clarify the intended medication or instruction with the "
        "prescribing doctor."
    )


__all__ = [
    "STABLE_TYPE_FACTS",
    "TYPE_FACT_REGISTRY_VERSION",
    "StableTypeFact",
    "stable_type_mismatch_response",
]
