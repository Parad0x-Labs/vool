from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class SourceCredibilityVerdict:
    domain: str
    score: float
    category: str
    blocked: bool
    reason: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


_HIGH_TRUST: dict[str, tuple[float, str]] = {
    "docs.python.org": (0.92, "primary_technical"),
    "developer.mozilla.org": (0.91, "primary_technical"),
    "developer.apple.com": (0.90, "primary_technical"),
    "developer.android.com": (0.90, "primary_technical"),
    "web.dev": (0.86, "primary_technical"),
    "core.telegram.org": (0.90, "primary_platform"),
    "discord.com": (0.87, "primary_platform"),
    "github.com": (0.70, "repo_reference"),
    "material.io": (0.80, "design_guidance"),
    "reuters.com": (0.82, "wire_news"),
    "apnews.com": (0.80, "wire_news"),
    "bbc.com": (0.72, "broadcaster_news"),
    "cnn.com": (0.58, "broadcaster_news"),
    "wikipedia.org": (0.55, "orientation"),
}

_BLOCKED: dict[str, tuple[str, str]] = {
    "consent.google.com": ("interstitial", "Consent interstitial; not a usable source."),
    "rt.com": ("state_propaganda", "Blocked state-propaganda source."),
    "sputniknews.com": ("state_propaganda", "Blocked state-propaganda source."),
    "infowars.com": ("conspiracy", "Blocked conspiracy source."),
    "oann.com": ("hyperpartisan", "Blocked hyperpartisan source."),
    "thegatewaypundit.com": ("hyperpartisan", "Blocked hyperpartisan source."),
    "breitbart.com": ("hyperpartisan", "Blocked hyperpartisan source."),
    "newsmax.com": ("hyperpartisan", "Blocked hyperpartisan source."),
}

_LOW_TRUST: dict[str, tuple[float, str, str]] = {
    "reddit.com": (0.34, "community", "Community source; useful for leads, not authority."),
    "medium.com": (0.36, "blog", "Blog platform; credibility varies by author."),
    "substack.com": (0.34, "blog", "Newsletter platform; credibility varies by author."),
    "x.com": (0.18, "social", "Social media source; do not treat as authoritative."),
    "twitter.com": (0.18, "social", "Social media source; do not treat as authoritative."),
    "youtube.com": (0.24, "video", "Video platform; credibility depends on source."),
}


def evaluate_source_domain(domain: str | None) -> SourceCredibilityVerdict:
    normalized = _normalize_domain(domain)
    if not normalized:
        return SourceCredibilityVerdict("", 0.20, "unknown", False, "Missing source domain.")

    blocked = _lookup_suffix(_BLOCKED, normalized)
    if blocked:
        category, reason = blocked
        return SourceCredibilityVerdict(normalized, 0.0, category, True, reason)

    high = _lookup_suffix(_HIGH_TRUST, normalized)
    if high:
        score, category = high
        return SourceCredibilityVerdict(normalized, score, category, False, "Trusted curated source class.")

    low = _lookup_suffix(_LOW_TRUST, normalized)
    if low:
        score, category, reason = low
        return SourceCredibilityVerdict(normalized, score, category, False, reason)

    return SourceCredibilityVerdict(normalized, 0.42, "unknown_web", False, "Unknown domain; treat cautiously and require corroboration.")


def is_domain_allowed(domain: str | None, *, allow_domains: tuple[str, ...] = (), deny_domains: tuple[str, ...] = ()) -> bool:
    normalized = _normalize_domain(domain)
    if not normalized:
        return False
    if _matches_any(normalized, deny_domains):
        return False
    if allow_domains and not _matches_any(normalized, allow_domains):
        return False
    verdict = evaluate_source_domain(normalized)
    return not verdict.blocked


def _normalize_domain(domain: str | None) -> str:
    value = (domain or "").strip().lower()
    if value.startswith("www."):
        value = value[4:]
    return value


def _matches_any(domain: str, patterns: tuple[str, ...]) -> bool:
    for pattern in patterns:
        normalized = _normalize_domain(pattern)
        if domain == normalized or domain.endswith(f".{normalized}"):
            return True
    return False


def _lookup_suffix(table: dict[str, tuple], domain: str) -> tuple | None:
    for pattern, payload in table.items():
        normalized = _normalize_domain(pattern)
        if domain == normalized or domain.endswith(f".{normalized}"):
            return payload
    return None
