from __future__ import annotations

from core.agent_runtime.hive_topic_draft_builder import (
    build_hive_create_pending_variants as draft_build_hive_create_pending_variants,
)
from core.agent_runtime.hive_topic_draft_builder import (
    normalize_hive_create_variant as draft_normalize_hive_create_variant,
)
from core.agent_runtime.hive_topic_draft_duplicate_detection import (
    check_hive_duplicate as draft_check_hive_duplicate,
)
from core.agent_runtime.hive_topic_draft_intents import (
    looks_like_hive_topic_create_request as draft_looks_like_hive_topic_create_request,
)
from core.agent_runtime.hive_topic_draft_intents import (
    looks_like_hive_topic_drafting_request as draft_looks_like_hive_topic_drafting_request,
)
from core.agent_runtime.hive_topic_draft_intents import (
    wants_hive_create_auto_start as draft_wants_hive_create_auto_start,
)

check_hive_duplicate = draft_check_hive_duplicate
build_hive_create_pending_variants = draft_build_hive_create_pending_variants
normalize_hive_create_variant = draft_normalize_hive_create_variant
wants_hive_create_auto_start = draft_wants_hive_create_auto_start
looks_like_hive_topic_create_request = draft_looks_like_hive_topic_create_request
looks_like_hive_topic_drafting_request = draft_looks_like_hive_topic_drafting_request
