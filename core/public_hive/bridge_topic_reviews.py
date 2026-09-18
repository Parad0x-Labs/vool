from __future__ import annotations

from typing import Any

from . import reads as public_hive_reads
from . import writes as public_hive_writes


class PublicHiveBridgeTopicReviewsMixin:
    def list_public_review_queue(self, *, object_type: str | None = None, limit: int = 24) -> list[dict[str, Any]]:
        return public_hive_reads.list_public_review_queue(self, object_type=object_type, limit=limit)

    def submit_public_moderation_review(
        self,
        *,
        object_type: str,
        object_id: str,
        decision: str,
        note: str | None = None,
    ) -> dict[str, Any]:
        return public_hive_writes.submit_public_moderation_review(
            self,
            object_type=object_type,
            object_id=object_id,
            decision=decision,
            note=note,
        )

    def get_public_review_summary(
        self,
        *,
        object_type: str,
        object_id: str,
    ) -> dict[str, Any]:
        return public_hive_reads.get_public_review_summary(self, object_type=object_type, object_id=object_id)
