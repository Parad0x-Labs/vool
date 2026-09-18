from __future__ import annotations

from .bridge_topic_claim_writes import PublicHiveBridgeTopicClaimWritesMixin
from .bridge_topic_lifecycle_writes import PublicHiveBridgeTopicLifecycleWritesMixin
from .bridge_topic_post_writes import PublicHiveBridgeTopicPostWritesMixin


class PublicHiveBridgeTopicWritesMixin(
    PublicHiveBridgeTopicLifecycleWritesMixin,
    PublicHiveBridgeTopicClaimWritesMixin,
    PublicHiveBridgeTopicPostWritesMixin,
):
    pass
