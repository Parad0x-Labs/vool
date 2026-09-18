from __future__ import annotations

from core.agent_runtime import hive_topic_pending, hive_topic_pending_confirmation, hive_topic_preview_render


def test_hive_topic_pending_surface_exports_stay_available_from_facade() -> None:
    assert hive_topic_pending.parse_hive_create_variant_choice is hive_topic_pending_confirmation.parse_hive_create_variant_choice
    assert hive_topic_pending.format_hive_create_preview is hive_topic_preview_render.format_hive_create_preview


def test_parse_hive_create_variant_choice_accepts_original_draft_reply() -> None:
    assert hive_topic_pending_confirmation.parse_hive_create_variant_choice("send original draft") == "original"
