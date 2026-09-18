from __future__ import annotations

from typing import Any

from . import reads as public_hive_reads


class PublicHiveBridgeTopicReadsMixin:
    def list_public_topics(
        self,
        *,
        limit: int = 24,
        statuses: tuple[str, ...] = ("open", "researching", "disputed", "partial", "needs_improvement"),
    ) -> list[dict[str, Any]]:
        return public_hive_reads.list_public_topics(self, limit=limit, statuses=statuses)

    def get_public_topic(
        self,
        topic_id: str,
        *,
        include_flagged: bool = True,
    ) -> dict[str, Any] | None:
        return public_hive_reads.get_public_topic(self, topic_id, include_flagged=include_flagged)

    def list_public_research_queue(self, *, limit: int = 24) -> list[dict[str, Any]]:
        return public_hive_reads.list_public_research_queue(self, limit=limit)

    def get_public_research_packet(self, topic_id: str) -> dict[str, Any]:
        return public_hive_reads.get_public_research_packet(self, topic_id)

    def _build_research_queue_fallback(self, *, limit: int) -> list[dict[str, Any]]:
        return public_hive_reads.build_research_queue_fallback(self, limit=limit)

    def _build_research_packet_fallback(self, topic_id: str) -> dict[str, Any]:
        return public_hive_reads.build_research_packet_fallback(self, topic_id)

    def _overlay_research_queue_truth(self, rows: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
        return public_hive_reads.overlay_research_queue_truth(self, rows, limit=limit)

    def _overlay_research_packet_truth(self, topic_id: str, direct_packet: dict[str, Any]) -> dict[str, Any]:
        return public_hive_reads.overlay_research_packet_truth(self, topic_id, direct_packet)

    def _get_public_topic(self, topic_id: str) -> dict[str, Any]:
        return public_hive_reads.get_public_topic_raw(self, topic_id)

    def _list_public_topic_posts(self, topic_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
        return public_hive_reads.list_public_topic_posts(self, topic_id, limit=limit)

    def _list_public_topic_claims(self, topic_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
        return public_hive_reads.list_public_topic_claims(self, topic_id, limit=limit)

    def search_public_artifacts(
        self,
        *,
        query_text: str,
        topic_id: str | None = None,
        limit: int = 24,
    ) -> list[dict[str, Any]]:
        return public_hive_reads.search_public_artifacts(self, query_text=query_text, topic_id=topic_id, limit=limit)
