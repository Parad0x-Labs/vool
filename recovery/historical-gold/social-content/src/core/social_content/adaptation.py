"""Platform adaptation without policy fiction (P1 Checkpoint 4).

A bounded render/validate contract — NOT a per-platform regex pile in hot
files, and NOT an editor: this module adapts STRUCTURE and verifies that
facts, links, opinions and approved meaning survive. It never rewrites text
on its own; over-limit content comes back with typed findings and the
operator/model decides the compression.

- X limits/weighting come from core.x_platform_policy (delegated).
- Non-X limits come from core.social_content.platform_policy (versioned,
  officially sourced; unknown -> validation_unknown, never invented).
- Every clean copy is hash-bound per platform/version by the caller (the
  store) — this module returns the exact bytes it validated.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from core.social_content.platform_policy import (
    VALIDATION_UNKNOWN,
    fact,
    length_limit_with_certainty,
)

_URL_RE = re.compile(r"https?://\S+")


@dataclass
class AdaptationFinding:
    code: str
    severity: str  # failure | warning | unknown
    detail: str = ""


@dataclass
class AdaptationResult:
    platform: str
    body: str
    thread_parts: list[str] = field(default_factory=list)
    findings: list[AdaptationFinding] = field(default_factory=list)
    content_hash: str = ""

    @property
    def clean(self) -> bool:
        return not any(f.severity == "failure" for f in self.findings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "body": self.body,
            "thread_parts": self.thread_parts,
            "content_hash": self.content_hash,
            "clean": self.clean,
            "findings": [{"code": f.code, "severity": f.severity,
                          "detail": f.detail} for f in self.findings],
        }


def _urls_in(text: str) -> set[str]:
    return set(_URL_RE.findall(str(text or "")))


def _effective_length(platform: str, body: str) -> int:
    """Counted length under the platform's own accounting rules."""
    if platform == "x":
        from core.x_platform_policy import weighted_length

        return weighted_length(body)
    if platform == "mastodon":
        # documented default accounting: links count as 23 regardless of length
        total = 0
        parts = _URL_RE.split(body)
        links = _URL_RE.findall(body)
        total += sum(len(p) for p in parts)
        total += 23 * len(links)
        return total
    return len(body)


def split_thread(body: str, limit: int) -> list[str]:
    """Deterministic thread split at sentence boundaries, never mid-URL."""
    sentences = re.split(r"(?<=[.!?])\s+", str(body or "").strip())
    parts: list[str] = []
    current = ""
    for sentence in sentences:
        candidate = f"{current} {sentence}".strip()
        if candidate and len(candidate) > limit and current:
            parts.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts


def adapt(
    *, platform: str, body: str, format: str = "post",
    source_body: str | None = None, intentional_reuse: bool = False,
    other_platform_bodies: dict[str, str] | None = None,
) -> AdaptationResult:
    """Validate (and structurally split, for threads) one platform copy.

    ``source_body`` is the original approved/grounded text: every URL and
    every labelled claim in it must survive in the adapted copy. Meaning
    verification is structural (links + claim surfaces); semantic meaning is
    the operator's review, never this function's guess.
    """
    platform = str(platform or "").strip().lower()
    body = str(body or "")
    result = AdaptationResult(platform=platform, body=body)
    result.content_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()

    limit, certainty = length_limit_with_certainty(platform)
    if certainty == VALIDATION_UNKNOWN:
        result.findings.append(AdaptationFinding(
            VALIDATION_UNKNOWN, "unknown",
            f"{platform}: no officially published limit — length is NOT validated; "
            "do not treat this copy as limit-clean",
        ))
        length = _effective_length(platform, body)
    else:
        assert limit is not None
        length = _effective_length(platform, body)
        if format == "thread":
            result.thread_parts = split_thread(body, limit)
            for i, part in enumerate(result.thread_parts):
                if len(part) > limit:
                    result.findings.append(AdaptationFinding(
                        "thread_part_over_limit", "failure",
                        f"thread part {i + 1} is {len(part)} > {limit}",
                    ))
        elif length > limit:
            result.findings.append(AdaptationFinding(
                "over_limit", "failure",
                f"{platform} body is {length} > documented limit {limit}",
            ))

    if platform == "bluesky":
        # The lexicon limit is graphemes; plain len() is a lower bound only.
        if len(body) > 300:
            result.findings.append(AdaptationFinding(
                "grapheme_accounting_uncertain", "unknown",
                "bluesky counts graphemes; len() is a lower bound — a Unicode-heavy "
                "copy may still exceed 300 graphemes",
            ))

    if source_body is not None:
        missing = _urls_in(source_body) - _urls_in(body)
        if missing:
            result.findings.append(AdaptationFinding(
                "missing_links", "failure",
                f"links from the source did not survive: {sorted(missing)}",
            ))
        for number in sorted(set(re.findall(r"\b\d[\d.,]*\b", source_body))):
            if number not in body and number not in ("0", "1"):
                result.findings.append(AdaptationFinding(
                    "number_changed", "warning",
                    f"source figure {number!r} is absent from the adapted copy — "
                    "verify meaning was preserved",
                ))

    if other_platform_bodies and not intentional_reuse:
        mine = _normalized(body)
        for other_platform, other_body in other_platform_bodies.items():
            if _normalized(other_body) == mine:
                result.findings.append(AdaptationFinding(
                    "repetitive_cross_platform_copy", "warning",
                    f"copy is identical to the {other_platform} version; set "
                    "intentional_reuse if that is deliberate",
                ))

    return result


def _normalized(text: str) -> str:
    return " ".join(str(text or "").lower().split())


def media_brief_from_evidence(evidence_texts: tuple[str, ...]) -> dict[str, Any] | None:
    """A media brief exists only when the operator supplied visual evidence.

    Never fabricate an image or describe visual facts that are not present in
    the evidence. No visual evidence -> None (and the caller says so)."""
    visual = []
    for text in evidence_texts:
        for sentence in re.split(r"(?<=[.!?])\s+", str(text or "")):
            if re.search(r"\b(?:screenshot|chart|graph|image|photo|diagram|figure|"
                         r"mockup|video|clip)\b", sentence, re.I):
                visual.append(" ".join(sentence.split()))
    if not visual:
        return None
    return {
        "basis": "operator-supplied visual evidence only",
        "evidence_sentences": visual[:5],
        "alt_text_guidance": "describe only what the supplied evidence shows",
    }


def x_delegation_note() -> str:
    """X editorial structure/limits/rendering belong to the X Editorial
    authority; the manager delegates via the XDrafT contract."""
    return (
        "X drafting is delegated to skills/x-editorial-studio + core.x_editorial "
        "(XDrafT envelope); this manager never re-implements X limits"
    )


def platform_supports_thread_split(platform: str) -> bool:
    return platform in ("x", "threads")


def unknown_limits_for(platform: str) -> list[dict[str, str]]:
    rows = []
    for f in (globals().get("PLATFORM_FACTS", {}) or {}).get(platform, ()):  # pragma: no cover
        pass
    from core.social_content.platform_policy import PLATFORM_FACTS

    for f in PLATFORM_FACTS.get(platform, ()):
        if f.status == VALIDATION_UNKNOWN:
            rows.append({"key": f.key, "note": f.note})
    return rows
