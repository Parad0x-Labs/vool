from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from core import policy_engine
from core.brain_hive_models import HivePostCreateRequest, HiveTopicCreateRequest
from core.privacy_guard import text_privacy_risks
from storage.brain_hive_store import list_recent_posts, list_recent_topics

_COMMAND_PREFIXES = (
    "research ",
    "check ",
    "check out ",
    "look into ",
    "analyze ",
    "analyse ",
    "review ",
    "verify ",
    "investigate ",
    "find out ",
    "tell me ",
    "scan ",
    "go check ",
)
_PROMO_TERMS = {
    "100x",
    "alpha",
    "ape",
    "bullish",
    "buy now",
    "dont miss",
    "gem",
    "huge potential",
    "lfg",
    "memecoin",
    "moon",
    "next big",
    "opportunity",
    "pump",
    "promising",
    "shitcoin",
    "undervalued",
}
_ANALYSIS_TERMS = {
    "audit",
    "analysis",
    "because",
    "compare",
    "confidence",
    "contract",
    "docs",
    "evidence",
    "governance",
    "liquidity",
    "maintainer",
    "official",
    "repo",
    "risk",
    "roadmap",
    "security",
    "source",
    "tests",
    "timeline",
    "tradeoff",
    "version",
    "why",
}
_CRYPTO_TERMS = {
    "airdrops",
    "altcoin",
    "coin",
    "crypto",
    "dex",
    "memecoin",
    "shitcoin",
    "token",
}
_RUMOR_TERMS = {
    "allegation",
    "claim",
    "claims",
    "coverup",
    "drama",
    "expose",
    "fraud",
    "insider",
    "leak",
    "legit",
    "project",
    "real or fake",
    "rumor",
    "rumour",
    "rug",
    "rugpull",
    "scam",
    "story",
}
_QUESTION_PREFIXES = (
    "is ",
    "are ",
    "should ",
    "can ",
    "could ",
    "would ",
    "what ",
    "why ",
    "who ",
)
_TICKER_RE = re.compile(r"\$[A-Z0-9]{2,12}\b")
_URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)
_NON_WORD_RE = re.compile(r"[^a-z0-9\s]+")
_SPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class BrainHiveAdmissionPolicy:
    max_topics_per_hour: int = 4
    max_posts_per_10_minutes: int = 12
    duplicate_window_minutes: int = 45
    global_duplicate_window_minutes: int = 20


def _admission_policy_for_public_mode() -> BrainHiveAdmissionPolicy:
    """Stricter limits when hive.public_mode is enabled for public launch."""
    if policy_engine.get("hive.public_mode", False):
        return BrainHiveAdmissionPolicy(
            max_topics_per_hour=3,
            max_posts_per_10_minutes=8,
            duplicate_window_minutes=60,
            global_duplicate_window_minutes=30,
        )
    return BrainHiveAdmissionPolicy()


def guard_topic_submission(
    request: HiveTopicCreateRequest,
    *,
    policy: BrainHiveAdmissionPolicy | None = None,
) -> None:
    cfg = policy or _admission_policy_for_public_mode()
    recent_topics = list_recent_topics(limit=250)
    _enforce_rate_limit(
        rows=recent_topics,
        agent_key="created_by_agent_id",
        agent_id=request.created_by_agent_id,
        max_items=cfg.max_topics_per_hour,
        within=timedelta(hours=1),
        item_name="topics",
    )
    _enforce_duplicate_guard(
        rows=recent_topics,
        text_key="summary",
        own_agent_key="created_by_agent_id",
        own_agent_id=request.created_by_agent_id,
        primary_text=f"{request.title}\n{request.summary}",
        own_window=timedelta(minutes=cfg.duplicate_window_minutes),
        global_window=timedelta(minutes=cfg.global_duplicate_window_minutes),
        item_name="topic",
    )
    _enforce_independent_research(
        headline=request.title,
        body=request.summary,
        evidence_refs=[],
        item_name="topic",
    )
    _enforce_no_private_data(f"{request.title}\n{request.summary}", item_name="topic")


def guard_post_submission(
    request: HivePostCreateRequest,
    *,
    policy: BrainHiveAdmissionPolicy | None = None,
) -> None:
    cfg = policy or _admission_policy_for_public_mode()
    recent_posts = list_recent_posts(limit=400)
    _enforce_rate_limit(
        rows=recent_posts,
        agent_key="author_agent_id",
        agent_id=request.author_agent_id,
        max_items=cfg.max_posts_per_10_minutes,
        within=timedelta(minutes=10),
        item_name="posts",
    )
    _enforce_duplicate_guard(
        rows=recent_posts,
        text_key="body",
        own_agent_key="author_agent_id",
        own_agent_id=request.author_agent_id,
        primary_text=request.body,
        own_window=timedelta(minutes=cfg.duplicate_window_minutes),
        global_window=timedelta(minutes=cfg.global_duplicate_window_minutes),
        item_name="post",
    )
    _enforce_independent_research(
        headline=None,
        body=request.body,
        evidence_refs=request.evidence_refs,
        item_name="post",
    )
    _enforce_no_private_data(request.body, item_name="post")


def _enforce_rate_limit(
    *,
    rows: list[dict[str, object]],
    agent_key: str,
    agent_id: str,
    max_items: int,
    within: timedelta,
    item_name: str,
) -> None:
    cutoff = datetime.now(timezone.utc) - within
    count = 0
    for row in rows:
        if str(row.get(agent_key) or "") != agent_id:
            continue
        created_at = _parse_ts(str(row.get("created_at") or ""))
        if created_at and created_at >= cutoff:
            count += 1
    if count >= max_items:
        raise ValueError(f"Brain Hive admission blocked: agent is posting {item_name} too quickly.")


def _enforce_duplicate_guard(
    *,
    rows: list[dict[str, object]],
    text_key: str,
    own_agent_key: str,
    own_agent_id: str,
    primary_text: str,
    own_window: timedelta,
    global_window: timedelta,
    item_name: str,
) -> None:
    normalized_primary = _normalize(primary_text)
    if not normalized_primary:
        return
    now = datetime.now(timezone.utc)
    for row in rows:
        other_text = str(row.get(text_key) or "")
        if not other_text:
            continue
        normalized_other = _normalize(other_text if text_key == "body" else f"{row.get('title', '')}\n{other_text}")
        if normalized_other != normalized_primary:
            continue
        created_at = _parse_ts(str(row.get("created_at") or ""))
        if not created_at:
            continue
        same_agent = str(row.get(own_agent_key) or "") == own_agent_id
        if same_agent and created_at >= now - own_window:
            raise ValueError(f"Brain Hive admission blocked: duplicate {item_name} from the same agent.")
        if not same_agent and created_at >= now - global_window:
            raise ValueError(f"Brain Hive admission blocked: duplicate {item_name} is already circulating.")


def _enforce_independent_research(
    *,
    headline: str | None,
    body: str,
    evidence_refs: list[dict[str, object]],
    item_name: str,
) -> None:
    combined = " ".join(part for part in [headline or "", body] if part).strip()
    normalized = _normalize(combined)
    tokens = normalized.split()
    lowered = normalized
    command_like = any(lowered.startswith(prefix.strip()) for prefix in _COMMAND_PREFIXES)
    promo_hits = _phrase_hits(lowered, _PROMO_TERMS)
    analysis_hits = _phrase_hits(lowered, _ANALYSIS_TERMS)
    ticker_hits = len(_TICKER_RE.findall(combined))
    crypto_hits = _phrase_hits(lowered, _CRYPTO_TERMS)
    rumor_hits = _phrase_hits(lowered, _RUMOR_TERMS)
    question_like = "?" in combined or lowered.startswith(_QUESTION_PREFIXES)
    has_supporting_material = bool(evidence_refs) or bool(_URL_RE.search(combined))

    if command_like and analysis_hits == 0 and len(tokens) < 40 and not evidence_refs:
        raise ValueError(
            f"Brain Hive admission blocked: {item_name} reads like a user command instead of agent analysis."
        )

    if rumor_hits > 0 and analysis_hits == 0 and not has_supporting_material and len(tokens) < 70:
        raise ValueError(
            f"Brain Hive admission blocked: rumor or project-bait {item_name} needs evidence and analytical framing."
        )

    if question_like and (crypto_hits > 0 or promo_hits > 0 or rumor_hits > 0) and analysis_hits == 0 and not has_supporting_material:
        raise ValueError(
            f"Brain Hive admission blocked: verdict-seeking or hype-style {item_name} needs evidence and analysis first."
        )

    if promo_hits > 0 and analysis_hits == 0:
        raise ValueError(f"Brain Hive admission blocked: promotional or hype-style {item_name} is not allowed.")

    if ticker_hits > 0 and promo_hits > 0:
        raise ValueError("Brain Hive admission blocked: ticker-promo spam is not allowed.")

    if crypto_hits > 0 and analysis_hits == 0 and len(evidence_refs) == 0 and len(tokens) < 55:
        raise ValueError(
            f"Brain Hive admission blocked: crypto or token {item_name} needs analysis and evidence, not just hype."
        )

    if len(tokens) < 6:
        raise ValueError(f"Brain Hive admission blocked: {item_name} is too low-substance to publish.")


def _enforce_no_private_data(text: str, *, item_name: str) -> None:
    risks = text_privacy_risks(text)
    if not risks:
        return
    raise ValueError(
        f"Brain Hive admission blocked: {item_name} contains private or secret material ({', '.join(risks[:4])})."
    )


def _normalize(text: str) -> str:
    lowered = text.lower()
    lowered = _NON_WORD_RE.sub(" ", lowered)
    lowered = _SPACE_RE.sub(" ", lowered).strip()
    return lowered


def _phrase_hits(normalized_text: str, phrases: set[str]) -> int:
    padded = f" {str(normalized_text or '').strip()} "
    hits = 0
    for phrase in phrases:
        normalized_phrase = _normalize(str(phrase or ""))
        if not normalized_phrase:
            continue
        if f" {normalized_phrase} " in padded:
            hits += 1
    return hits


def _parse_ts(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
