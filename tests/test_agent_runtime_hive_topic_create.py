from __future__ import annotations

from types import SimpleNamespace

from core.agent_runtime import hive_topics
from core.agent_runtime.hive_topic_create import (
    build_hive_create_pending_variants,
    check_hive_duplicate,
    clean_hive_title,
    extract_hive_topic_create_draft,
    extract_original_hive_topic_create_draft,
    looks_like_hive_topic_create_request,
    maybe_handle_hive_topic_create_request,
    normalize_hive_create_variant,
    prepare_public_hive_topic_copy,
    shape_public_hive_admission_safe_copy,
    wants_hive_create_auto_start,
)
from core.agent_runtime.hive_topic_drafting import (
    build_hive_create_pending_variants as drafting_build_hive_create_pending_variants,
)
from core.agent_runtime.hive_topic_drafting import (
    check_hive_duplicate as drafting_check_hive_duplicate,
)
from core.agent_runtime.hive_topic_drafting import (
    normalize_hive_create_variant as drafting_normalize_hive_create_variant,
)


def test_hive_topic_create_compat_exports_stay_available_from_hive_topics() -> None:
    assert hive_topics.maybe_handle_hive_topic_create_request is maybe_handle_hive_topic_create_request
    assert hive_topics.prepare_public_hive_topic_copy is prepare_public_hive_topic_copy
    assert hive_topics.build_hive_create_pending_variants is build_hive_create_pending_variants
    assert hive_topics.normalize_hive_create_variant is normalize_hive_create_variant
    assert hive_topics.check_hive_duplicate is check_hive_duplicate


def test_shape_public_hive_admission_safe_copy_reframes_command_like_brief() -> None:
    title, summary, note = shape_public_hive_admission_safe_copy(
        title="research docker health mismatch",
        summary="tell me which route is right and what to do next",
    )

    assert title == "research docker health mismatch"
    assert "Agent analysis brief comparing architecture" in summary
    assert "docker health mismatch" in summary
    assert "Admission:" in note


def _build_drafting_agent() -> SimpleNamespace:
    agent = SimpleNamespace()
    agent._strip_wrapping_quotes = lambda text: str(text).strip().strip('"').strip("'")
    agent._normalize_hive_topic_tag = lambda text: str(text).strip().lower().replace(" ", "-")
    agent._infer_hive_topic_tags = lambda title: ["openclaw", "ux"] if "openclaw" in str(title).lower() else ["general"]
    agent._wants_hive_create_auto_start = wants_hive_create_auto_start
    agent._looks_like_hive_topic_drafting_request = lambda lowered: hive_topics.looks_like_hive_topic_drafting_request(agent, lowered)
    agent._looks_like_hive_topic_create_request = lambda lowered: looks_like_hive_topic_create_request(agent, lowered)
    return agent


def test_extract_hive_topic_create_draft_keeps_structured_fields() -> None:
    agent = _build_drafting_agent()

    result = extract_hive_topic_create_draft(
        agent,
        'create new hive task: Task: Better OpenClaw onboarding Goal: make first run obvious Summary: tighten proof path Topic tags: OpenClaw, onboarding',
    )

    assert result == {
        "title": "Better OpenClaw onboarding",
        "summary": "tighten proof path",
        "topic_tags": ["openclaw", "onboarding"],
        "auto_start_research": False,
    }


def test_extract_hive_topic_create_draft_accepts_add_this_to_hive_prompt() -> None:
    agent = _build_drafting_agent()

    result = extract_hive_topic_create_draft(
        agent,
        "Add this to the Hive mind active tasks. Exact title: Public install proof for local-first agent Hive participation. "
        "Exact summary: Research how local-first agent installs should prove real Hive participation after bootstrap.",
    )

    assert result == {
        "title": "Public install proof for local-first agent Hive participation",
        "summary": "Research how local-first agent installs should prove real Hive participation after bootstrap",
        "topic_tags": ["general"],
        "auto_start_research": False,
    }


def test_extract_original_hive_topic_create_draft_preserves_rawer_title() -> None:
    agent = _build_drafting_agent()

    result = extract_original_hive_topic_create_draft(
        agent,
        'create new hive task: Title: raw shell title Summary: keep raw copy',
    )

    assert result == {
        "title": "raw shell title",
        "summary": "keep raw copy",
        "topic_tags": ["general"],
        "auto_start_research": False,
    }


def test_hive_topic_drafting_helpers_keep_expected_detection_behavior() -> None:
    agent = _build_drafting_agent()

    assert clean_hive_title("create hive task: tighten watcher ux") == "Tighten watcher ux"
    assert wants_hive_create_auto_start("create it and start researching") is True
    assert looks_like_hive_topic_create_request(agent, "create new hive task: fix proof receipts") is True
    assert (
        looks_like_hive_topic_create_request(
            agent,
            "add this to the hive mind active tasks. exact title: public install proof for local-first agent hive participation",
        )
        is True
    )
    assert looks_like_hive_topic_create_request(agent, "show me the open hive tasks") is False


def test_hive_topic_preflight_helpers_delegate_to_drafting_module() -> None:
    assert build_hive_create_pending_variants is drafting_build_hive_create_pending_variants
    assert normalize_hive_create_variant is drafting_normalize_hive_create_variant
    assert check_hive_duplicate is drafting_check_hive_duplicate
