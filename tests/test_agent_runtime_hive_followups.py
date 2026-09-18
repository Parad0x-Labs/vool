from __future__ import annotations

from unittest import mock

from apps.vool_agent import VoolAgent
from core.agent_runtime import hive_followups


def _build_agent() -> VoolAgent:
    return VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")


def test_maybe_handle_hive_frontdoor_facade_delegates_to_extracted_module() -> None:
    agent = _build_agent()

    with mock.patch(
        "core.agent_runtime.hive_followups.maybe_handle_hive_frontdoor",
        return_value=({"response": "hive"}, {"title": "topic"}, False),
    ) as maybe_handle_hive_frontdoor:
        result = agent._maybe_handle_hive_frontdoor(
            raw_user_input="show me the hive tasks",
            effective_input="show me the hive tasks",
            session_id="hive-session",
            source_context={"surface": "openclaw"},
        )

    assert result == ({"response": "hive"}, {"title": "topic"}, False)
    maybe_handle_hive_frontdoor.assert_called_once_with(
        agent,
        raw_user_input="show me the hive tasks",
        effective_input="show me the hive tasks",
        session_id="hive-session",
        source_context={"surface": "openclaw"},
    )


def test_maybe_handle_hive_frontdoor_uses_app_level_review_override() -> None:
    agent = _build_agent()

    with mock.patch.object(
        agent,
        "_maybe_handle_hive_review_command",
        return_value={"response": "review reply"},
    ) as maybe_handle_hive_review_command:
        result, draft, pending = hive_followups.maybe_handle_hive_frontdoor(
            agent,
            raw_user_input="check review queue",
            effective_input="check review queue",
            session_id="hive-session",
            source_context={"surface": "openclaw"},
        )

    assert result == {"response": "review reply"}
    assert draft is None
    assert pending is False
    maybe_handle_hive_review_command.assert_called_once_with(
        "check review queue",
        session_id="hive-session",
        source_context={"surface": "openclaw"},
    )


def test_extract_hive_topic_hint_facade_matches_extracted_module() -> None:
    agent = _build_agent()
    text = "is #deadbeef done yet?"

    assert agent._extract_hive_topic_hint(text) == hive_followups.extract_hive_topic_hint(text)


def test_parse_hive_review_action_facade_matches_extracted_module() -> None:
    agent = _build_agent()
    text = "approve post post-abcdef12"

    assert agent._parse_hive_review_action(text) == hive_followups.parse_hive_review_action(text)


def test_maybe_handle_hive_status_followup_uses_app_level_session_state_override() -> None:
    agent = _build_agent()

    packet = {
        "topic": {"title": "OpenClaw integration audit", "status": "researching"},
        "execution_state": {"execution_state": "claimed", "active_claim_count": 1, "artifact_count": 2},
        "counts": {"post_count": 1, "active_claim_count": 1},
        "posts": [{"post_kind": "note", "body": "First bounded pass landed."}],
    }

    with mock.patch("core.agent_runtime.agent.session_hive_state", return_value={"watched_topic_ids": ["deadbeef-topic-id"]}) as session_hive_state_mock, mock.patch.object(
        agent.public_hive_bridge,
        "enabled",
        return_value=True,
    ), mock.patch.object(
        agent.public_hive_bridge,
        "get_public_research_packet",
        return_value=packet,
    ), mock.patch.object(
        agent,
        "_fast_path_result",
        return_value={"response": "status reply"},
    ) as fast_path_result:
        result = agent._maybe_handle_hive_status_followup(
            "is it complete?",
            session_id="hive-status-session",
            source_context=None,
        )

    assert result == {"response": "status reply"}
    session_hive_state_mock.assert_called_once_with("hive-status-session")
    fast_path_result.assert_called_once()
    assert fast_path_result.call_args.kwargs["reason"] == "hive_status_followup"


def test_maybe_handle_hive_research_followup_facade_delegates_to_extracted_module() -> None:
    agent = _build_agent()

    with mock.patch(
        "core.agent_runtime.hive_followups.maybe_handle_hive_research_followup",
        return_value={"response": "research reply"},
    ) as maybe_handle_hive_research_followup:
        result = agent._maybe_handle_hive_research_followup(
            "do it",
            session_id="hive-research-session",
            source_context={"surface": "openclaw"},
        )

    assert result == {"response": "research reply"}
    maybe_handle_hive_research_followup.assert_called_once()
