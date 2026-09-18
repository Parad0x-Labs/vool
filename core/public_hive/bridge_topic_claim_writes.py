from __future__ import annotations

from typing import Any

from . import writes as public_hive_writes


class PublicHiveBridgeTopicClaimWritesMixin:
    def claim_public_topic(
        self,
        *,
        topic_id: str,
        note: str | None = None,
        capability_tags: list[str] | None = None,
        status: str = "active",
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return public_hive_writes.claim_public_topic(
            self,
            topic_id=topic_id,
            note=note,
            capability_tags=capability_tags,
            status=status,
            idempotency_key=idempotency_key,
        )
