from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from core.execution import hive_tools as extracted_hive_tools
from core.hive_activity_tracker import HiveActivityTracker, HiveActivityTrackerConfig
from core.tool_intent_executor import _execute_hive_list_available, _execute_hive_tool, _failed_hive_execution


def test_hive_failed_execution_facade_matches_extracted_helper() -> None:
    result = {"status": "rejected", "error": "nope"}
    assert _failed_hive_execution("hive.submit_result", result, "fallback") == extracted_hive_tools.failed_hive_execution(
        "hive.submit_result",
        result,
        "fallback",
    )


def test_hive_tool_facade_matches_extracted_module_for_submit_result() -> None:
    tracker = HiveActivityTracker(config=HiveActivityTrackerConfig(enabled=False, watcher_api_url=None))
    bridge = mock.Mock()
    bridge.write_enabled.return_value = True
    bridge.submit_public_topic_result.return_value = {
        "ok": True,
        "status": "result_submitted",
        "topic_id": "topic-1234567890abcdef",
        "post_id": "post-123",
    }

    result = _execute_hive_tool(
        "hive.submit_result",
        {
            "topic_id": "topic-1234567890abcdef",
            "body": "Done. Real event stream is live.",
            "result_status": "solved",
            "claim_id": "claim-123",
        },
        hive_activity_tracker=tracker,
        public_hive_bridge=bridge,
    )

    expected = extracted_hive_tools.execute_hive_tool(
        "hive.submit_result",
        {
            "topic_id": "topic-1234567890abcdef",
            "body": "Done. Real event stream is live.",
            "result_status": "solved",
            "claim_id": "claim-123",
        },
        hive_activity_tracker=tracker,
        public_hive_bridge=bridge,
        unsupported_execution_for_intent_fn=lambda intent, *, status, **_kwargs: None,
        capability_gap_for_intent_fn=lambda _intent: {"support_level": "unsupported"},
        render_capability_truth_response_fn=lambda report: str(report),
        research_topic_from_signal_fn=lambda *_args, **_kwargs: None,
        audit_log_fn=lambda *_args, **_kwargs: None,
        get_local_peer_id_fn=lambda: "peer-123",
        get_profile_fn=lambda _peer_id: None,
        update_profile_fn=lambda _peer_id, **_kwargs: None,
    )
    assert result == expected


def test_hive_list_available_facade_matches_extracted_module() -> None:
    tracker = HiveActivityTracker(config=HiveActivityTrackerConfig(enabled=True, watcher_api_url="https://watch.example.test/api/dashboard"))
    dashboard = {"topics": [{"topic_id": "topic-1", "title": "Research packet topic", "status": "open"}]}
    with mock.patch.object(tracker, "fetch_dashboard", return_value=dashboard), mock.patch.object(
        tracker,
        "_available_topics",
        return_value=dashboard["topics"],
    ):
        result = _execute_hive_list_available(tracker, {"limit": 1}, public_hive_bridge=None)
        expected = extracted_hive_tools.execute_hive_list_available(
            tracker,
            {"limit": 1},
            public_hive_bridge=None,
            capability_gap_for_intent_fn=lambda _intent: {"support_level": "unsupported"},
            render_capability_truth_response_fn=lambda report: str(report),
        )
    assert result == expected


def test_hive_tool_facade_matches_extracted_module_for_voolbook_profile() -> None:
    tracker = HiveActivityTracker(config=HiveActivityTrackerConfig(enabled=False, watcher_api_url=None))
    profile = SimpleNamespace(
        handle="@vool",
        display_name="VOOL",
        bio="Local-first runtime",
        post_count=3,
        claim_count=2,
        glory_score=7.5,
        status="active",
    )
    with mock.patch("network.signer.get_local_peer_id", return_value="peer-123"), mock.patch(
        "core.voolbook_identity.get_profile",
        return_value=profile,
    ):
        result = _execute_hive_tool(
            "voolbook.get_profile",
            {},
            hive_activity_tracker=tracker,
            public_hive_bridge=mock.Mock(write_enabled=lambda: True),
        )

    expected = extracted_hive_tools.execute_hive_tool(
        "voolbook.get_profile",
        {},
        hive_activity_tracker=tracker,
        public_hive_bridge=mock.Mock(write_enabled=lambda: True),
        unsupported_execution_for_intent_fn=lambda intent, *, status, **_kwargs: None,
        capability_gap_for_intent_fn=lambda _intent: {"support_level": "unsupported"},
        render_capability_truth_response_fn=lambda report: str(report),
        research_topic_from_signal_fn=lambda *_args, **_kwargs: None,
        audit_log_fn=lambda *_args, **_kwargs: None,
        get_local_peer_id_fn=lambda: "peer-123",
        get_profile_fn=lambda _peer_id: profile,
        update_profile_fn=lambda _peer_id, **_kwargs: None,
    )
    assert result == expected
