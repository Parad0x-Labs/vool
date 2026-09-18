from __future__ import annotations

from typing import Any

from . import writes as public_hive_writes


class PublicHiveBridgePresenceCommonsMixin:
    def publish_agent_commons_update(
        self,
        *,
        topic: str,
        topic_kind: str,
        summary: str,
        public_body: str,
        topic_tags: list[str] | None = None,
    ) -> dict[str, Any]:
        return public_hive_writes.publish_agent_commons_update(
            self,
            topic=topic,
            topic_kind=topic_kind,
            summary=summary,
            public_body=public_body,
            topic_tags=topic_tags,
        )
