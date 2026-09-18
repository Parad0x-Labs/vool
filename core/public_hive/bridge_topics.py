from __future__ import annotations

from .bridge_topic_publication import PublicHiveBridgeTopicPublicationMixin
from .bridge_topic_reads import PublicHiveBridgeTopicReadsMixin
from .bridge_topic_reviews import PublicHiveBridgeTopicReviewsMixin
from .bridge_topic_writes import PublicHiveBridgeTopicWritesMixin


class PublicHiveBridgeTopicsMixin(
    PublicHiveBridgeTopicReadsMixin,
    PublicHiveBridgeTopicReviewsMixin,
    PublicHiveBridgeTopicWritesMixin,
    PublicHiveBridgeTopicPublicationMixin,
):
    pass
