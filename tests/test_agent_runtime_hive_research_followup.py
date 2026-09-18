from __future__ import annotations

from apps.vool_agent import VoolAgent
from core.agent_runtime import hive_followups
from core.agent_runtime.hive_research_followup import (
    extract_hive_topic_hint,
    looks_like_hive_research_followup,
    maybe_handle_hive_research_followup,
    maybe_handle_hive_status_followup,
)


def _build_agent() -> VoolAgent:
    return VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")


def test_hive_research_followup_compat_exports_stay_available_from_hive_followups() -> None:
    assert hive_followups.maybe_handle_hive_research_followup is maybe_handle_hive_research_followup
    assert hive_followups.maybe_handle_hive_status_followup is maybe_handle_hive_status_followup
    assert hive_followups.extract_hive_topic_hint is extract_hive_topic_hint


def test_looks_like_hive_research_followup_accepts_short_hint_and_delivery_phrase() -> None:
    agent = _build_agent()

    assert extract_hive_topic_hint("deliver it to hive #deadbeef") == "deadbeef"
    assert looks_like_hive_research_followup(
        agent,
        "deliver it to hive #deadbeef",
        topic_hint="deadbeef",
        has_pending_topics=False,
        shown_titles=[],
        history_has_task_list=False,
    )
