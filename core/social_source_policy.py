from __future__ import annotations

from dataclasses import asdict, dataclass

from core.source_credibility import evaluate_source_domain


@dataclass(frozen=True)
class SocialSourceVerdict:
    domain: str
    platform: str
    credibility_score: float
    allowed_for_orientation: bool
    allowed_as_primary_evidence: bool
    reason: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


_PLATFORMS = {
    "x.com": "x",
    "twitter.com": "x",
    "facebook.com": "facebook",
    "fb.com": "facebook",
    "instagram.com": "instagram",
    "youtube.com": "youtube",
    "reddit.com": "reddit",
    "tiktok.com": "tiktok",
}


def evaluate_social_source(domain: str | None) -> SocialSourceVerdict:
    credibility = evaluate_source_domain(domain)
    normalized = credibility.domain
    platform = _PLATFORMS.get(normalized, "social" if normalized else "unknown")
    if credibility.blocked:
        return SocialSourceVerdict(
            domain=normalized,
            platform=platform,
            credibility_score=0.0,
            allowed_for_orientation=False,
            allowed_as_primary_evidence=False,
            reason=credibility.reason,
        )

    if platform in {"x", "facebook", "instagram", "tiktok"}:
        return SocialSourceVerdict(
            domain=normalized,
            platform=platform,
            credibility_score=min(0.22, credibility.score),
            allowed_for_orientation=True,
            allowed_as_primary_evidence=False,
            reason="Social platform content is allowed only as low-trust orientation unless corroborated.",
        )
    if platform == "youtube":
        return SocialSourceVerdict(
            domain=normalized,
            platform=platform,
            credibility_score=min(0.28, credibility.score),
            allowed_for_orientation=True,
            allowed_as_primary_evidence=False,
            reason="Video platforms may be useful, but require transcript and corroboration.",
        )
    if platform == "reddit":
        return SocialSourceVerdict(
            domain=normalized,
            platform=platform,
            credibility_score=min(0.34, credibility.score),
            allowed_for_orientation=True,
            allowed_as_primary_evidence=False,
            reason="Community discussion can provide leads, not primary truth.",
        )
    return SocialSourceVerdict(
        domain=normalized,
        platform=platform,
        credibility_score=credibility.score,
        allowed_for_orientation=not credibility.blocked,
        allowed_as_primary_evidence=credibility.score >= 0.7,
        reason=credibility.reason,
    )
