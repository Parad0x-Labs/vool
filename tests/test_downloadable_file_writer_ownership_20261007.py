"""Delivery owns an artifact; a workspace write requires an explicit destination."""
import pytest

from core import tool_intent_executor
from core.deliverables import DOWNLOAD_ONLY_KEY, downloadable_request_without_workspace_target
from core.hive_activity_tracker import HiveActivityTracker, HiveActivityTrackerConfig


@pytest.mark.parametrize("text", [
    "Write a tiny HTML page and matching CSS file, then zip them together. Do not run scripts.",
    "Write a script and zip it up for me.",
    "I need a spreadsheet of monthly subscriptions. Excel please.",
])
def test_downloadable_request_has_its_own_writer(text):
    assert downloadable_request_without_workspace_target(text)

@pytest.mark.parametrize("text", [
    "Explain how to make a PDF accessible.",
    "Write a note in my workspace, then zip it up.",
    "Write a note to my Downloads as a .txt file.",
    "Save a CSV to ./data/month.csv.",
])
def test_advice_and_explicit_destinations_do_not_change_ownership(text):
    assert not downloadable_request_without_workspace_target(text)

@pytest.mark.parametrize("intent", ["workspace.write_file", "machine.write_file"])
def test_wrong_writer_is_refused_before_permission_or_dispatch(intent, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("permission or dispatch was reached")
    monkeypatch.setattr(tool_intent_executor, "_sync_registry_sources", forbidden)
    monkeypatch.setattr(tool_intent_executor, "execute_authorized_runtime_tool", forbidden)
    execution = tool_intent_executor._execute_tool_intent_impl(
        {"intent": intent, "arguments": {"path": "page.html", "content": "hello"}},
        task_id="delivery-boundary", session_id="delivery-boundary",
        source_context={DOWNLOAD_ONLY_KEY: True},
        hive_activity_tracker=HiveActivityTracker(config=HiveActivityTrackerConfig(enabled=False)),
    )
    assert execution.status == "delivery_tool_required"
    assert execution.mode == "refused" and execution.details["executed"] is False
    assert "file.deliver" in execution.response_text
