from __future__ import annotations

from .bridge_topic_create_writes import PublicHiveBridgeTopicCreateWritesMixin
from .bridge_topic_mutation_writes import PublicHiveBridgeTopicMutationWritesMixin
from .bridge_topic_status_writes import PublicHiveBridgeTopicStatusWritesMixin


class PublicHiveBridgeTopicLifecycleWritesMixin(
    PublicHiveBridgeTopicStatusWritesMixin,
    PublicHiveBridgeTopicMutationWritesMixin,
    PublicHiveBridgeTopicCreateWritesMixin,
):
    pass
