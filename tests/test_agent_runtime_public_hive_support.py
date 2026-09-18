from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from apps.vool_agent import VoolAgent


def _build_agent() -> VoolAgent:
    return VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")


def test_public_transport_mode_uses_cached_presence_context_when_source_missing() -> None:
    agent = _build_agent()
    with agent._public_presence_lock:
        agent._public_presence_source_context = {"surface": "background", "platform": "openclaw"}

    resolved = agent._public_transport_source(None)
    resolved["surface"] = "mutated"

    assert agent._public_presence_source_context == {"surface": "background", "platform": "openclaw"}
    assert agent._public_transport_mode(None) == "background_openclaw"
    assert agent._public_transport_mode({"surface": "api"}) == "api"


def test_public_capabilities_include_workspace_builder_when_writes_are_enabled() -> None:
    agent = _build_agent()

    with mock.patch(
        "core.agent_runtime.public_hive_support.supported_public_capability_tags",
        return_value=["web.live_lookup", "hive.read"],
    ), mock.patch(
        "core.agent_runtime.public_hive_support.runtime_capability_ledger",
        return_value=[{"capability_id": "web.live_lookup", "supported": True}],
    ), mock.patch(
        "core.agent_runtime.public_hive_support.policy_engine.get",
        side_effect=lambda key, default=None: {
            "filesystem.allow_write_workspace": True,
            "execution.allow_sandbox_execution": False,
        }.get(key, default),
    ):
        capabilities = agent._public_capabilities()
        entries = agent._capability_ledger_entries()

    assert "workspace.build_scaffold" in capabilities
    build_entry = next(entry for entry in entries if entry["capability_id"] == "workspace.build_scaffold")
    assert build_entry["supported"] is True
    assert build_entry["support_level"] == "partial"
    assert "verification is limited because sandbox execution is disabled" in build_entry["claim"]


def test_public_capabilities_omit_workspace_builder_when_writes_are_disabled() -> None:
    agent = _build_agent()

    with mock.patch(
        "core.agent_runtime.public_hive_support.supported_public_capability_tags",
        return_value=["web.live_lookup"],
    ), mock.patch(
        "core.agent_runtime.public_hive_support.policy_engine.get",
        side_effect=lambda key, default=None: {
            "filesystem.allow_write_workspace": False,
            "execution.allow_sandbox_execution": True,
        }.get(key, default),
    ):
        capabilities = agent._public_capabilities()
        build_entry = agent._workspace_build_capability_entry()

    assert "workspace.build_scaffold" not in capabilities
    assert build_entry["supported"] is False
    assert build_entry["support_level"] == "unsupported"


def test_public_task_export_failures_are_audited_and_return_none() -> None:
    agent = _build_agent()
    task = type(
        "Task",
        (),
        {
            "task_id": "task-public-1",
            "task_summary": "Research Liquefy-backed proof receipts",
            "share_scope": "public_knowledge",
        },
    )()

    with mock.patch.object(
        agent.public_hive_bridge,
        "publish_public_task",
        side_effect=RuntimeError("bridge down"),
    ), mock.patch("core.agent_runtime.public_hive_support.audit_logger.log") as audit_log:
        result = agent._maybe_publish_public_task(
            task=task,
            classification={"task_class": "research", "topic_hints": ["liquefy", "proof"]},
            assistant_response="Opened a public proof thread.",
            session_id="session-public-export",
        )

    assert result is None
    audit_log.assert_called_once_with(
        "public_hive_task_export_error",
        target_id="task-public-1",
        target_type="task",
        details={
            "error": "bridge down",
            "share_scope": "public_knowledge",
            "session_id": "session-public-export",
        },
    )


def test_maybe_hive_footer_skips_background_and_audits_footer_errors() -> None:
    agent = _build_agent()

    with mock.patch.object(agent.hive_activity_tracker, "build_chat_footer", return_value="footer") as build_footer:
        result = agent._maybe_hive_footer(
            session_id="session-bg",
            source_context={"surface": "background", "platform": "openclaw"},
        )

    assert result == ""
    build_footer.assert_not_called()

    with mock.patch(
        "core.agent_runtime.public_hive_support.load_preferences",
        return_value=SimpleNamespace(hive_followups=True, idle_research_assist=True),
    ), mock.patch.object(
        agent.hive_activity_tracker,
        "build_chat_footer",
        side_effect=RuntimeError("footer failed"),
    ), mock.patch("core.agent_runtime.public_hive_support.audit_logger.log") as audit_log:
        result = agent._maybe_hive_footer(
            session_id="session-openclaw",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    assert result == ""
    audit_log.assert_called_once_with(
        "hive_activity_footer_error",
        target_id="session-openclaw",
        target_type="session",
        details={"error": "footer failed"},
    )
