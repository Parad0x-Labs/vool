from __future__ import annotations

from types import SimpleNamespace

from core.agent_runtime import hive_topic_create, hive_topic_public_copy
from core.agent_runtime.hive_topic_public_copy_admission import (
    shape_public_hive_admission_safe_copy as admission_shape_public_hive_admission_safe_copy,
)
from core.agent_runtime.hive_topic_public_copy_guard import (
    prepare_public_hive_topic_copy as guard_prepare_public_hive_topic_copy,
)
from core.agent_runtime.hive_topic_public_copy_privacy import (
    prepare_public_hive_topic_copy as privacy_prepare_public_hive_topic_copy,
)
from core.agent_runtime.hive_topic_public_copy_risks import (
    HIVE_CREATE_HARD_PRIVACY_RISKS as HIVE_CREATE_HARD_PRIVACY_RISKS_MODULE,
)
from core.agent_runtime.hive_topic_public_copy_safety import (
    HIVE_CREATE_HARD_PRIVACY_RISKS,
)
from core.agent_runtime.hive_topic_public_copy_safety import (
    prepare_public_hive_topic_copy as safety_prepare_public_hive_topic_copy,
)
from core.agent_runtime.hive_topic_public_copy_safety import (
    sanitize_public_hive_text as safety_sanitize_public_hive_text,
)
from core.agent_runtime.hive_topic_public_copy_safety import (
    shape_public_hive_admission_safe_copy as safety_shape_public_hive_admission_safe_copy,
)
from core.agent_runtime.hive_topic_public_copy_sanitize import (
    sanitize_public_hive_text as sanitize_sanitize_public_hive_text,
)
from core.agent_runtime.hive_topic_public_copy_tags import (
    infer_hive_topic_tags as tag_infer_hive_topic_tags,
)
from core.agent_runtime.hive_topic_public_copy_transcript import (
    looks_like_raw_chat_transcript as transcript_looks_like_raw_chat_transcript,
)


def _build_agent() -> SimpleNamespace:
    return SimpleNamespace(_normalize_hive_topic_tag=lambda raw: str(raw).strip().lower())


def test_hive_topic_public_copy_exports_stay_available_from_hive_topic_create() -> None:
    assert hive_topic_create.prepare_public_hive_topic_copy is hive_topic_public_copy.prepare_public_hive_topic_copy
    assert hive_topic_create.infer_hive_topic_tags is hive_topic_public_copy.infer_hive_topic_tags
    assert hive_topic_public_copy.prepare_public_hive_topic_copy is privacy_prepare_public_hive_topic_copy
    assert privacy_prepare_public_hive_topic_copy is safety_prepare_public_hive_topic_copy
    assert safety_prepare_public_hive_topic_copy is guard_prepare_public_hive_topic_copy
    assert HIVE_CREATE_HARD_PRIVACY_RISKS is HIVE_CREATE_HARD_PRIVACY_RISKS_MODULE
    assert safety_sanitize_public_hive_text is sanitize_sanitize_public_hive_text
    assert safety_shape_public_hive_admission_safe_copy is admission_shape_public_hive_admission_safe_copy
    assert hive_topic_public_copy.infer_hive_topic_tags is tag_infer_hive_topic_tags


def test_hive_topic_public_copy_privacy_facade_reexports_transcript_helpers() -> None:
    assert hive_topic_public_copy.looks_like_raw_chat_transcript is transcript_looks_like_raw_chat_transcript


def test_prepare_public_hive_topic_copy_blocks_raw_transcripts_without_structured_brief() -> None:
    agent = _build_agent()

    result = hive_topic_public_copy.prepare_public_hive_topic_copy(
        agent,
        raw_input="12:34\nU\ncan you help\n/new\nA",
        title="Research trace capture",
        summary="Dump this whole chat log to Hive",
    )

    assert result["ok"] is False
    assert result["reason"] == "hive_topic_create_transcript_blocked"
    assert "raw chat log/transcript" in str(result["response"])


def test_infer_hive_topic_tags_dedupes_and_normalizes() -> None:
    agent = _build_agent()

    tags = hive_topic_public_copy.infer_hive_topic_tags(
        agent,
        "Research OpenClaw OpenClaw UI for local OS and VM reliability",
    )

    assert tags == ["research", "openclaw", "ui", "local", "os", "vm"]
