from __future__ import annotations

from typing import Any


def evidence_strength(item: dict[str, Any]) -> float:
    credibility = float(dict(item.get("credibility") or {}).get("score") or 0.0)
    social = dict(item.get("social_policy") or {})
    trust = credibility
    if social and not bool(social.get("allowed_as_primary_evidence", False)):
        trust = min(trust, float(social.get("credibility_score") or trust))

    media_kind = str(item.get("media_kind") or "text")
    if media_kind == "image":
        trust += 0.08 if item.get("analysis_text") else -0.06
    elif media_kind == "video":
        trust += 0.05 if item.get("transcript") else -0.10
    elif media_kind == "social_post":
        trust -= 0.08

    if item.get("blocked"):
        return 0.0
    return max(0.0, min(1.0, trust))


def rank_media_evidence(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = []
    for item in items:
        enriched = dict(item)
        enriched["evidence_strength"] = evidence_strength(item)
        ranked.append(enriched)
    ranked.sort(key=lambda item: (float(item.get("evidence_strength") or 0.0), str(item.get("media_kind") or "")), reverse=True)
    return ranked
