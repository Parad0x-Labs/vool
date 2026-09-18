from __future__ import annotations

from core.public_hive import moderation as public_hive_moderation
from core.public_hive import publication as public_hive_publication
from core.public_hive import topic_writes as public_hive_topic_writes
from core.public_hive import writes as public_hive_writes


def test_public_hive_writes_facade_reexports_extracted_modules() -> None:
    assert public_hive_writes.submit_public_moderation_review is public_hive_moderation.submit_public_moderation_review
    assert public_hive_writes.create_public_topic is public_hive_topic_writes.create_public_topic
    assert public_hive_writes.update_public_topic_status is public_hive_topic_writes.update_public_topic_status
    assert public_hive_writes.post_public_topic_progress is public_hive_publication.post_public_topic_progress
    assert public_hive_writes.submit_public_topic_result is public_hive_publication.submit_public_topic_result
    assert public_hive_writes.publish_public_task is public_hive_publication.publish_public_task
