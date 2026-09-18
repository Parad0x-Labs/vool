from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core import policy_engine


@dataclass(frozen=True)
class CuriosityConfig:
    enabled: bool
    mode: str
    auto_execute_task_classes: tuple[str, ...]
    max_topics_per_task: int
    max_queries_per_topic: int
    max_snippets_per_query: int
    prefer_metadata_first: bool
    allow_news_pulse: bool
    news_max_topics_per_task: int
    technical_max_topics_per_task: int
    min_interest_score: float
    min_understanding_confidence: float
    skip_if_retrieval_confidence_at_least: float
    max_total_roam_seconds: int
    auto_promote_to_canonical: bool


@dataclass(frozen=True)
class CuriosityDecision:
    enabled: bool
    auto_execute: bool
    reason: str


def load_curiosity_config() -> CuriosityConfig:
    return CuriosityConfig(
        enabled=bool(policy_engine.get("curiosity.enabled", True)),
        mode=str(policy_engine.get("curiosity.mode", "bounded_auto") or "bounded_auto"),
        auto_execute_task_classes=tuple(policy_engine.get("curiosity.auto_execute_task_classes", ["research", "system_design"])),
        max_topics_per_task=max(1, int(policy_engine.get("curiosity.max_topics_per_task", 2))),
        max_queries_per_topic=max(1, int(policy_engine.get("curiosity.max_queries_per_topic", 2))),
        max_snippets_per_query=max(1, int(policy_engine.get("curiosity.max_snippets_per_query", 3))),
        prefer_metadata_first=bool(policy_engine.get("curiosity.prefer_metadata_first", True)),
        allow_news_pulse=bool(policy_engine.get("curiosity.allow_news_pulse", True)),
        news_max_topics_per_task=max(1, int(policy_engine.get("curiosity.news_max_topics_per_task", 1))),
        technical_max_topics_per_task=max(1, int(policy_engine.get("curiosity.technical_max_topics_per_task", 2))),
        min_interest_score=float(policy_engine.get("curiosity.min_interest_score", 0.56)),
        min_understanding_confidence=float(policy_engine.get("curiosity.min_understanding_confidence", 0.50)),
        skip_if_retrieval_confidence_at_least=float(policy_engine.get("curiosity.skip_if_retrieval_confidence_at_least", 0.84)),
        max_total_roam_seconds=max(1, int(policy_engine.get("curiosity.max_total_roam_seconds", 8))),
        auto_promote_to_canonical=bool(policy_engine.get("curiosity.auto_promote_to_canonical", False)),
    )


def curiosity_decision(
    *,
    config: CuriosityConfig,
    task_class: str,
    understanding_confidence: float,
    retrieval_confidence_score: float,
    interest_score: float,
) -> CuriosityDecision:
    if not config.enabled:
        return CuriosityDecision(False, False, "curiosity_disabled")
    if understanding_confidence < config.min_understanding_confidence:
        return CuriosityDecision(False, False, "input_too_ambiguous")
    if interest_score < config.min_interest_score:
        return CuriosityDecision(False, False, "interest_score_too_low")
    auto_execute_class = task_class in set(config.auto_execute_task_classes)
    if retrieval_confidence_score >= config.skip_if_retrieval_confidence_at_least and not auto_execute_class:
        return CuriosityDecision(True, False, "memory_already_strong")

    auto_execute = config.mode == "bounded_auto" and auto_execute_class
    return CuriosityDecision(True, auto_execute, "bounded_curiosity_allowed")


def bounded_auto_enabled(config: CuriosityConfig) -> bool:
    return config.enabled and config.mode == "bounded_auto"


def source_kind_limit(config: CuriosityConfig, topic_kind: str) -> int:
    if topic_kind == "news":
        return config.news_max_topics_per_task
    return config.technical_max_topics_per_task


def policy_snapshot(config: CuriosityConfig) -> dict[str, Any]:
    return {
        "enabled": config.enabled,
        "mode": config.mode,
        "auto_execute_task_classes": list(config.auto_execute_task_classes),
        "max_topics_per_task": config.max_topics_per_task,
        "max_queries_per_topic": config.max_queries_per_topic,
        "max_snippets_per_query": config.max_snippets_per_query,
        "prefer_metadata_first": config.prefer_metadata_first,
        "allow_news_pulse": config.allow_news_pulse,
        "auto_promote_to_canonical": config.auto_promote_to_canonical,
    }
