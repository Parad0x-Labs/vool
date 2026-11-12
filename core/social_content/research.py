"""Evidence-led research and idea engine for the Social Content Manager.

Deterministic, hermetic and authority-respecting:

- sources arrive as typed records (operator-supplied or produced by the
  canonical research lanes); this module NEVER fetches anything itself;
- social-source trust follows ``core.social_source_policy`` — social posts are
  low-trust orientation and can never be primary evidence unless corroborated
  through independent external news;
- claims extracted from evidence are labelled ``supported`` / ``opinion`` /
  ``unknown``; an unknown current claim may appear only as a marked question
  or opinion, never as clean publishable fact;
- ideas are deduplicated by signature against the campaign store (active and
  archived);
- ``NO_POST`` is returned when evidence is weak, repetitive or unsafe — a
  first-class successful outcome, not an error.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable
from urllib.parse import urlparse

from core.social_source_policy import evaluate_social_source

SOCIAL_SOURCE_CLASSES = frozenset({"personal_feed", "platform_trending", "x_search", "named_account"})
EXTERNAL_CLASS = "external_news"

# Two independent external news sources, or one external + explicit operator
# evidence, corroborate a current claim. One social post never does.
_CORROBORATION_MIN_EXTERNAL = 2


@dataclass(frozen=True)
class ResearchSource:
    source_id: str
    source_class: str
    text: str
    link: str = ""
    domain: str = ""
    observed_at: float = 0.0
    operator_evidence: bool = False  # evergreen operator-supplied material

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id, "source_class": self.source_class,
            "text": self.text, "link": self.link, "domain": self.domain,
            "observed_at": self.observed_at, "operator_evidence": self.operator_evidence,
        }


@dataclass(frozen=True)
class LabelledClaim:
    text: str
    status: str  # supported | opinion | unknown
    evidence_ids: tuple[str, ...] = ()
    contradiction_ids: tuple[str, ...] = ()


@dataclass
class ResearchOutcome:
    claims: list[LabelledClaim] = field(default_factory=list)
    corroborated_external: list[str] = field(default_factory=list)
    social_only_rumors: list[str] = field(default_factory=list)
    unknown_current_claims: list[str] = field(default_factory=list)
    contradictions: list[tuple[str, str]] = field(default_factory=list)
    evergreen_operator_evidence: list[str] = field(default_factory=list)
    stale_source_ids: list[str] = field(default_factory=list)
    usable_evidence_ids: tuple[str, ...] = ()
    decision: str = "NO_POST"  # NO_POST | ideas_possible
    reason: str = ""
    source_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "claims": [{"text": c.text, "status": c.status,
                        "evidence_ids": list(c.evidence_ids)} for c in self.claims],
            "corroborated_external": self.corroborated_external,
            "social_only_rumors": self.social_only_rumors,
            "unknown_current_claims": self.unknown_current_claims,
            "contradictions": [list(c) for c in self.contradictions],
            "evergreen_operator_evidence": self.evergreen_operator_evidence,
            "stale_source_ids": self.stale_source_ids,
            "usable_evidence_ids": list(self.usable_evidence_ids),
            "decision": self.decision,
            "reason": self.reason,
            "source_ids": list(self.source_ids),
        }


_CLAIM_NUMBER_RE = re.compile(r"\b\d[\d.,]*\s*\w*\b")
_CLAIM_SUPERLATIVE_RE = re.compile(
    r"\b(?:biggest|fastest|first|only|largest|most|best|worst|never|always)\b", re.I
)
_OPINION_RE = re.compile(
    r"\b(?:I think|we believe|in my opinion|arguably|probably|seems)\b", re.I
)


def _domain_of(link: str) -> str:
    try:
        netloc = urlparse(str(link or "")).netloc
        return netloc.lower().removeprefix("www.")
    except ValueError:
        return ""


# Known social platforms, derived from the ONE authority (core.social_source_policy
# maps each host to its platform label). No duplicated taxonomy: if the authority
# learns a new platform, this set follows.
_SOCIAL_PLATFORM_LABELS = frozenset({
    "x", "twitter", "facebook", "instagram", "tiktok", "youtube", "reddit",
})


def _is_social_domain(domain: str) -> bool:
    """A KNOWN social platform host — trust follows core.social_source_policy
    (orientation-only). An unknown external domain is not 'social'; it is simply
    uncorroborated until a second source arrives."""
    if not domain:
        return False
    verdict = evaluate_social_source(domain.lower())
    return str(getattr(verdict, "platform", "") or "") in _SOCIAL_PLATFORM_LABELS


def extract_claims(text: str) -> list[str]:
    """Deterministic surface claim candidates: numbers and superlatives."""
    value = str(text or "")
    found = {m.group(0).strip(" .,") for m in _CLAIM_NUMBER_RE.finditer(value)}
    for m in _CLAIM_SUPERLATIVE_RE.finditer(value):
        start, end = max(0, m.start() - 60), min(len(value), m.end() + 60)
        found.add(" ".join(value[start:end].split()))
    return sorted(f for f in found if len(f) > 2)[:20]


STALE_AFTER_SECONDS = 7 * 24 * 3600.0


def research(sources: Iterable[ResearchSource], *, now: float = 0.0,
             stale_after: float = STALE_AFTER_SECONDS) -> ResearchOutcome:
    """Label claims and decide whether evidenced ideas are possible at all.

    The trust ladder (per core.social_source_policy and the corroboration law):
    social text (any social source class or social platform domain) is
    orientation-only; a current claim is *supported* only with two independent
    external news sources, or one external news source plus explicit operator
    evidence; everything else current is *unknown*; opinion markers are
    *opinion* regardless of source.
    """
    outcome = ResearchOutcome()
    srcs = list(sources)
    outcome.source_ids = tuple(s.source_id for s in srcs)

    # Staleness: old snapshots cannot be presented as current evidence. Stale
    # external sources are flagged and excluded from corroboration.
    stale: list[str] = []
    fresh: list[ResearchSource] = []
    for src in srcs:
        age = max(0.0, now - src.observed_at) if now and src.observed_at else 0.0
        if age > stale_after:
            stale.append(src.source_id)
        else:
            fresh.append(src)
    outcome.stale_source_ids = stale
    srcs = fresh

    external_texts: list[ResearchSource] = []
    for src in srcs:
        if src.operator_evidence:
            outcome.evergreen_operator_evidence.append(src.text)
        is_social = src.source_class in SOCIAL_SOURCE_CLASSES or _is_social_domain(
            src.domain or _domain_of(src.link)
        )
        if src.source_class == EXTERNAL_CLASS and not _is_social_domain(src.domain or _domain_of(src.link)):
            external_texts.append(src)
        if is_social:
            outcome.social_only_rumors.append(src.source_id)

    # claim labelling per source
    for src in srcs:
        for claim in extract_claims(src.text):
            if _OPINION_RE.search(claim):
                outcome.claims.append(LabelledClaim(text=claim, status="opinion",
                                                    evidence_ids=(src.source_id,)))
    # corroborated external claims: same claim surface in >=2 INDEPENDENT
    # (distinct-domain) external sources
    for claim, witnesses in _shared_claims(external_texts):
        outcome.corroborated_external.append(claim)
        outcome.claims.append(LabelledClaim(text=claim, status="supported",
                                            evidence_ids=tuple(w.source_id for w in witnesses)))
    has_external = bool(external_texts)
    has_operator = any(s.operator_evidence for s in srcs)
    outcome.usable_evidence_ids = tuple(
        s.source_id for s in srcs
        if s.source_class == EXTERNAL_CLASS and not _is_social_domain(
            s.domain or _domain_of(s.link))
    ) + tuple(s.source_id for s in srcs if s.operator_evidence)
    if not (has_external or has_operator):
        outcome.decision = "NO_POST"
        outcome.reason = (
            "no external news or operator evidence in the source set — "
            "social-only material cannot ground clean copy"
        )
    else:
        outcome.decision = "ideas_possible"
        outcome.reason = f"{len(external_texts)} external source(s), operator evidence={has_operator}"
    return outcome


def _shared_claims(external: list[ResearchSource]) -> list[tuple[str, list[ResearchSource]]]:
    """Claims shared by >=2 sources on DISTINCT domains, with their witnesses.
    Same-domain syndication never corroborates itself."""
    out: list[tuple[str, list[ResearchSource]]] = []
    all_claims: set[str] = set()
    for src in external:
        all_claims |= set(extract_claims(src.text))
    for claim in sorted(all_claims):
        witnesses = [
            s for s in external
            if claim in set(extract_claims(s.text))
            and (s.domain or _domain_of(s.link))
        ]
        domains = {(w.domain or _domain_of(w.link)).lower() for w in witnesses}
        if len(domains) >= _CORROBORATION_MIN_EXTERNAL:
            out.append((claim, witnesses))
    return out


def detect_contradictions(sources: Iterable[ResearchSource]) -> list[tuple[str, str]]:
    """Pairs of external sources whose numbers for the same noun disagree."""
    srcs = [s for s in sources if s.source_class == EXTERNAL_CLASS]
    out: list[tuple[str, str]] = []
    for i, a in enumerate(srcs):
        for b in srcs[i + 1:]:
            for noun in _shared_nouns(a.text, b.text):
                na, nb = _number_near(a.text, noun), _number_near(b.text, noun)
                if na and nb and na != nb:
                    out.append((a.source_id, b.source_id))
                    break
    return out


def _shared_nouns(a: str, b: str) -> list[str]:
    wa = {w.lower().strip(".,") for w in a.split() if len(w) > 5}
    wb = {w.lower().strip(".,") for w in b.split() if len(w) > 5}
    return sorted(wa & wb)


def _number_near(text: str, noun: str) -> str | None:
    idx = text.lower().find(noun)
    if idx < 0:
        return None
    window = text[max(0, idx - 40):idx + 40]
    m = _CLAIM_NUMBER_RE.search(window)
    return m.group(0) if m else None


# ---------------------------------------------------------------- ideas


DEFAULT_IDEA_COUNT = 3


@dataclass(frozen=True)
class IdeaCandidate:
    angle: str
    why_now: str
    target_reader: str
    evidence_ids: tuple[str, ...] = ()


def evaluate_ideas(
    candidates: Iterable[IdeaCandidate], research_outcome: ResearchOutcome,
    *, existing_signatures: Iterable[str] = (),
    requested_count: int = DEFAULT_IDEA_COUNT,
) -> dict[str, Any]:
    """Deterministically filter, label and deduplicate idea candidates.

    Rules: an idea needs at least one evidence id that is NOT social-only
    rumour; duplicates (same dedup signature, active or archived) are rejected
    as duplicates; when nothing survives, the outcome is an honest NO_POST.
    """
    existing = set(existing_signatures)
    from storage.social_content_store import idea_dedup_signature

    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    usable_ids = set(research_outcome.usable_evidence_ids)
    for cand in candidates:
        sig = idea_dedup_signature(cand.angle, cand.target_reader, cand.evidence_ids)
        usable = [e for e in cand.evidence_ids if e in usable_ids]
        if not usable:
            rejected.append({
                "angle": cand.angle, "reason": "weak_evidence",
                "detail": "evidence is social-only orientation; cannot ground a clean idea",
            })
            continue
        if sig in existing:
            rejected.append({
                "angle": cand.angle, "reason": "duplicate",
                "detail": f"dedup signature {sig[:12]} already recorded",
            })
            continue
        accepted.append({
            "angle": cand.angle, "why_now": cand.why_now,
            "target_reader": cand.target_reader,
            "evidence_ids": list(cand.evidence_ids),
            "dedup_signature": sig,
        })
        existing.add(sig)
    count = max(1, int(requested_count or DEFAULT_IDEA_COUNT))
    if not accepted:
        return {
            "decision": "NO_POST",
            "reason": "no distinct evidenced idea survived dedup and evidence laddering",
            "accepted": [], "rejected": rejected,
        }
    return {
        "decision": "ideas",
        "accepted": accepted[:count],
        "rejected": rejected,
        "truncated": len(accepted) > count,
    }


# ---------------------------------------------------------------- anchor lines


PRESERVATION_MODES = ("verbatim", "meaning_locked", "inspiration_only")


def resolve_preservation_mode(line: str, user_text: str) -> str:
    """Default an explicitly quoted 'use this exact line' request to verbatim;
    otherwise choose the least destructive reasonable mode and let the caller
    disclose it. Deterministic on (line, user_text)."""
    quoted = line.strip() in user_text
    explicit_exact = quoted and bool(re.search(
        r"\b(?:exact(?:ly)?|verbatim|word[ -]for[ -]word|byte[ -]for[ -]byte|"
        r"this exact line|use this line)\b", user_text, re.I,
    ))
    if explicit_exact:
        return "verbatim"
    if quoted:
        return "meaning_locked"
    return "inspiration_only"


def verbatim_preserved(body: str, anchors: Iterable[dict[str, str]]) -> tuple[bool, str]:
    """Every verbatim anchor must appear byte-for-byte in the rendered body."""
    for anchor in anchors:
        if str(anchor.get("preservation_mode", "")) == "verbatim":
            line = str(anchor.get("line", ""))
            if line and line not in str(body or ""):
                return False, f"verbatim anchor not byte-exact: {line!r}"
    return True, ""
