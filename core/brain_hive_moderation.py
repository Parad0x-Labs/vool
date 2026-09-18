from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from core.brain_hive_models import HivePostCreateRequest, HiveTopicCreateRequest
from core.source_credibility import evaluate_source_domain
from storage.brain_hive_moderation_store import list_moderation_events

_URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)
_ALL_CAPS_RE = re.compile(r"\b[A-Z]{5,}\b")
_EMPHASIS_RE = re.compile(r"[!]{2,}")
_TICKER_RE = re.compile(r"\$[A-Z0-9]{2,12}\b")
_PROMO_TERMS = {"100x", "buy now", "gem", "huge potential", "moon", "next big", "promising", "pump", "undervalued"}
_RUMOR_TERMS = {"allegation", "claim", "claims", "fraud", "insider", "leak", "legit", "project", "rumor", "rumour", "rug", "rugpull", "scam", "story"}
_ANALYSIS_TERMS = {"analysis", "audit", "compare", "docs", "evidence", "official", "repo", "risk", "security", "source", "tests", "tradeoff", "why"}
_QUESTION_PREFIXES = ("is ", "are ", "should ", "can ", "could ", "would ", "what ", "why ", "who ")


@dataclass(frozen=True)
class ModerationDecision:
    state: str
    score: float
    reasons: list[str]
    metadata: dict[str, object]


def moderate_topic_submission(request: HiveTopicCreateRequest) -> ModerationDecision:
    score = 0.0
    reasons: list[str] = []
    combined = f"{request.title}\n{request.summary}"
    lowered = combined.lower()
    tickers = len(_TICKER_RE.findall(combined))
    promo_hits = sum(1 for term in _PROMO_TERMS if term in lowered)
    rumor_hits = sum(1 for term in _RUMOR_TERMS if term in lowered)
    analysis_hits = sum(1 for term in _ANALYSIS_TERMS if term in lowered)
    question_like = "?" in combined or lowered.strip().startswith(_QUESTION_PREFIXES)
    topic_urls = _extract_urls(combined)
    if tickers >= 3:
        score += 0.45
        reasons.append("topic carries excessive ticker density")
    if _ALL_CAPS_RE.search(combined):
        score += 0.15
        reasons.append("topic uses shouty all-caps phrasing")
    if _EMPHASIS_RE.search(combined):
        score += 0.15
        reasons.append("topic uses hype-style punctuation")
    if promo_hits > 0 and analysis_hits == 0:
        score += 0.30
        reasons.append("topic uses promotional framing without analysis")
    if rumor_hits > 0 and analysis_hits == 0 and not topic_urls:
        score += 0.35
        reasons.append("topic frames rumor or project claims without evidence")
    if question_like and (rumor_hits > 0 or promo_hits > 0 or tickers > 0) and analysis_hits == 0:
        score += 0.20
        reasons.append("topic asks for verdict-style judgment without analysis")
    blocked_domains, low_trust_domains, social_domains = _domain_buckets(topic_urls)
    if blocked_domains:
        score = 1.0
        reasons.append(f"topic cites blocked domains: {', '.join(sorted(blocked_domains))}")
    else:
        if low_trust_domains:
            score += min(0.4, 0.12 * len(low_trust_domains))
            reasons.append(f"topic relies on low-trust domains: {', '.join(sorted(low_trust_domains))}")
        if social_domains:
            score += min(0.4, 0.20 * len(social_domains))
            reasons.append(f"topic relies on social-media evidence: {', '.join(sorted(social_domains))}")
        if social_domains and rumor_hits > 0:
            score += 0.20
            reasons.append("topic mixes rumor or project framing with social-media sourcing")
    repeat_penalty = _repeat_offender_penalty(request.created_by_agent_id)
    if repeat_penalty:
        score += repeat_penalty
        reasons.append("agent has recent moderation history")
    state = _state_for_score(score)
    return ModerationDecision(
        state=state,
        score=min(score, 1.0),
        reasons=reasons or ["no moderation concerns"],
        metadata={
            "ticker_hits": tickers,
            "blocked_domains": sorted(blocked_domains),
            "low_trust_domains": sorted(low_trust_domains),
            "social_domains": sorted(social_domains),
        },
    )


def moderate_post_submission(request: HivePostCreateRequest) -> ModerationDecision:
    score = 0.0
    reasons: list[str] = []
    body = request.body
    lowered = body.lower()
    urls = _extract_urls(body)
    promo_hits = sum(1 for term in _PROMO_TERMS if term in lowered)
    rumor_hits = sum(1 for term in _RUMOR_TERMS if term in lowered)
    analysis_hits = sum(1 for term in _ANALYSIS_TERMS if term in lowered)
    question_like = "?" in body or lowered.strip().startswith(_QUESTION_PREFIXES)
    referenced_domains = set()
    for item in request.evidence_refs:
        value = str(item.get("value") or item.get("url") or "").strip()
        if value.startswith("http://") or value.startswith("https://"):
            urls.append(value)
    blocked_domains, low_trust_domains, social_domains = _domain_buckets(urls, referenced_domains=referenced_domains)
    if blocked_domains:
        score = 1.0
        reasons.append(f"post cites blocked domains: {', '.join(sorted(blocked_domains))}")
    else:
        if low_trust_domains:
            score += min(0.5, 0.15 * len(low_trust_domains))
            reasons.append(f"post relies on low-trust domains: {', '.join(sorted(low_trust_domains))}")
        if social_domains:
            score += min(0.45, 0.25 * len(social_domains))
            reasons.append(f"post relies on social-media evidence: {', '.join(sorted(social_domains))}")
        if len(referenced_domains) >= 8:
            score += 0.20
            reasons.append("post references an unusually broad link set")
        if _ALL_CAPS_RE.search(body):
            score += 0.10
            reasons.append("post uses shouty all-caps phrasing")
        if _EMPHASIS_RE.search(body):
            score += 0.10
            reasons.append("post uses hype-style punctuation")
        if len(_TICKER_RE.findall(body)) >= 3:
            score += 0.35
            reasons.append("post carries excessive ticker density")
        if promo_hits > 0 and analysis_hits == 0:
            score += 0.20
            reasons.append("post uses promotional framing without analysis")
        if rumor_hits > 0 and analysis_hits == 0 and not referenced_domains:
            score += 0.30
            reasons.append("post makes rumor or project claims without evidence")
        if question_like and (rumor_hits > 0 or promo_hits > 0 or len(_TICKER_RE.findall(body)) > 0) and analysis_hits == 0:
            score += 0.18
            reasons.append("post asks for verdict-style judgment without analysis")
        if social_domains and rumor_hits > 0:
            score += 0.20
            reasons.append("post mixes rumor claims with social-media sourcing")
        repeat_penalty = _repeat_offender_penalty(request.author_agent_id)
        if repeat_penalty:
            score += repeat_penalty
            reasons.append("agent has recent moderation history")
    state = _state_for_score(score)
    return ModerationDecision(
        state=state,
        score=min(score, 1.0),
        reasons=reasons or ["no moderation concerns"],
        metadata={
            "referenced_domains": sorted(referenced_domains),
            "blocked_domains": sorted(blocked_domains),
            "low_trust_domains": sorted(low_trust_domains),
            "social_domains": sorted(social_domains),
        },
    )


def _extract_urls(text: str) -> list[str]:
    return [match.group(0) for match in _URL_RE.finditer(text or "")]


def _domain_buckets(
    urls: list[str],
    *,
    referenced_domains: set[str] | None = None,
) -> tuple[list[str], list[str], list[str]]:
    seen = referenced_domains if referenced_domains is not None else set()
    blocked_domains: list[str] = []
    low_trust_domains: list[str] = []
    social_domains: list[str] = []
    for url in urls:
        parsed = urlparse(url)
        domain = (parsed.hostname or "").lower().strip()
        if not domain or domain in seen:
            continue
        seen.add(domain)
        verdict = evaluate_source_domain(domain)
        if verdict.blocked:
            blocked_domains.append(domain)
        elif verdict.score < 0.35:
            low_trust_domains.append(domain)
            if verdict.category == "social":
                social_domains.append(domain)
    return blocked_domains, low_trust_domains, social_domains


def _repeat_offender_penalty(agent_id: str) -> float:
    events = list_moderation_events(agent_id=agent_id, limit=10)
    flagged = [
        event
        for event in events
        if str(event.get("moderation_state") or "") in {"review_required", "quarantined"}
    ]
    if not flagged:
        return 0.0
    return min(0.25, 0.08 * len(flagged))


def _state_for_score(score: float) -> str:
    if score >= 0.85:
        return "quarantined"
    if score >= 0.35:
        return "review_required"
    return "approved"
