from __future__ import annotations

from typing import Any

from . import writes as public_hive_writes


class PublicHiveBridgeTopicPublicationMixin:
    def publish_public_task(
        self,
        *,
        task_id: str,
        task_summary: str,
        task_class: str,
        assistant_response: str,
        topic_tags: list[str] | None = None,
    ) -> dict[str, Any]:
        return public_hive_writes.publish_public_task(
            self,
            task_id=task_id,
            task_summary=task_summary,
            task_class=task_class,
            assistant_response=assistant_response,
            topic_tags=topic_tags,
        )

    def _find_related_topic(
        self,
        *,
        task_summary: str,
        task_class: str,
        topic_tags: list[str],
    ) -> dict[str, Any] | None:
        return public_hive_writes.find_related_topic(
            self,
            task_summary=task_summary,
            task_class=task_class,
            topic_tags=topic_tags,
        )

    def _find_agent_commons_topic(
        self,
        *,
        topic: str,
        topic_kind: str,
        topic_tags: list[str],
    ) -> dict[str, Any] | None:
        return public_hive_writes.find_agent_commons_topic(
            self,
            topic=topic,
            topic_kind=topic_kind,
            topic_tags=topic_tags,
        )
