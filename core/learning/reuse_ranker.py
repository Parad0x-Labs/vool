from __future__ import annotations

from .procedure_shards import ProcedureShardV1

#: Tokens too common to carry relevance. Found live: a debugging shard whose step text contains
#: "apply THE code patch" matched the query "write a haiku about THE sea" on the single token
#: "the", and an irrelevant creative task consumed a coding lesson. A shared stopword is not a
#: shared subject.
_STOPWORDS = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "if", "in", "into",
        "is", "it", "its", "no", "not", "of", "on", "or", "such", "that", "the", "their",
        "then", "there", "these", "they", "this", "to", "was", "will", "with", "you", "your",
    }
)


def normalize_guidance_tokens(text: str) -> list[str]:
    """Lowercased alnum tokens with common stopwords removed — the shared notion of 'meaningful
    guidance text' used for relevance scoring and for lesson-signature normalization."""
    return sorted(_meaningful_tokens(text))


def _meaningful_tokens(text: str) -> set[str]:
    raw = "".join(ch if ch.isalnum() else " " for ch in str(text or "").lower()).split()
    return {token for token in raw if token and token not in _STOPWORDS}


def rank_reusable_procedures(
    *,
    task_class: str,
    query_text: str,
    procedures: list[ProcedureShardV1],
    limit: int = 5,
) -> list[ProcedureShardV1]:
    """Rank PROMOTED, unexpired, locally-scoped procedures for a matching turn.

    Eligibility is checked before any scoring: a candidate (one verified execution is not
    learning), a demoted lesson (operator correction or unverified-reuse streak) and an expired
    one (nobody re-verified it inside the TTL) are invisible to reuse no matter how well their
    text matches. What a rankable shard contributes is descriptive only -- the router projects
    id/title/class/shareability/counts into envelope inputs; shard text never reaches model
    constraints, permissions or policy.
    """
    eligible = [shard for shard in procedures if shard.eligible_for_reuse()]
    query_tokens = _meaningful_tokens(query_text)
    scored: list[tuple[float, ProcedureShardV1]] = []
    for shard in eligible:
        score = 0.0
        class_match = shard.task_class == task_class
        if class_match:
            score += 5.0
        haystack = " ".join([shard.title, *shard.preconditions, *shard.steps])
        overlap = len(query_tokens & _meaningful_tokens(haystack))
        score += float(overlap)
        if overlap == 0:
            # Relevance gate: without ONE shared content token there is no relevance. The class
            # alone is a category, not a match — holding the gate only for class-mismatched
            # shards (the holdout's finding) let every same-class turn consume the lesson
            # whatever its words, so a debugging-classified chat about lunch received repair
            # guidance. Promoted + class + content overlap is the admission law.
            continue
        score += min(float(shard.reuse_count or 0), 10.0) * 0.1
        score += min(float(shard.verified_reuse_count or 0), 5.0) * 0.5
        if shard.shareability in {"local_only", "trusted_hive"}:
            score += 0.25
        if score > 0:
            scored.append((score, shard))
    scored.sort(key=lambda item: (item[0], item[1].created_at, item[1].procedure_id), reverse=True)
    return [item[1] for item in scored[: max(1, int(limit or 5))]]
