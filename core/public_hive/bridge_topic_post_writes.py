from __future__ import annotations

from .bridge_topic_post_progress_writes import PublicHiveBridgeTopicPostProgressWritesMixin
from .bridge_topic_post_result_writes import PublicHiveBridgeTopicPostResultWritesMixin
from .bridge_topic_post_status_writes import PublicHiveBridgeTopicPostStatusWritesMixin


class PublicHiveBridgeTopicPostWritesMixin(
    PublicHiveBridgeTopicPostProgressWritesMixin,
    PublicHiveBridgeTopicPostResultWritesMixin,
    PublicHiveBridgeTopicPostStatusWritesMixin,
):
    pass
