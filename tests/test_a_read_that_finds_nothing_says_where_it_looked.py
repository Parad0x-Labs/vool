"""A read that finds nothing at its target ends the turn on what it looked for, not a generic line.

Measured in the skills A/B on 11eef8dc (2026-11-13): asks that named a PDF, a press release and a
test file that were not there each ended on "I wasn't able to turn that into a completed action.
Tell me the specific thing you want done or remembered and I'll take another pass at it." The read
tool had answered plainly ("There is no file at `contract.pdf` ... I also looked for a file like
`contract.pdf` in ~/Downloads ... and found none."), but the executor handed the turn's reply an
empty user-safe text, so the failure reply fell back to the generic line and blamed the person.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.agent_runtime.hive_topic_facade import HiveTopicFacadeMixin
from core.hive_activity_tracker import HiveActivityTracker, HiveActivityTrackerConfig
from core.tool_intent_executor import execute_tool_intent

GENERIC = "I wasn't able to turn that into a completed action"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "vool"))
    (tmp_path / "Downloads").mkdir()
    workspace = tmp_path / "project"
    (workspace / "tests").mkdir(parents=True)
    monkeypatch.setenv("VOOL_WORKSPACE_ROOT", str(workspace))
    return workspace


def _run(intent: str, arguments: dict, workspace) -> object:
    context = {
        "surface": "api", "platform": "api", "_owner_local": True, "_http_ingress": True,
        "runtime_session_id": "s-read-miss", "turn_id": "t-read-miss", "action_policy": "allowed",
        "workspace_root": str(workspace),
    }
    return execute_tool_intent(
        {"intent": intent, "arguments": arguments},
        task_id="t-read-miss",
        session_id="s-read-miss",
        source_context=context,
        hive_activity_tracker=HiveActivityTracker(
            config=HiveActivityTrackerConfig(enabled=False, watcher_api_url=None)
        ),
    )


def _reply(execution) -> str:
    agent = SimpleNamespace(
        _session_hive_state=lambda _sid: {},
        _interaction_pending_topic_ids=lambda _state: [],
    )
    return HiveTopicFacadeMixin._tool_failure_user_message(
        agent, execution=execution, effective_input="", session_id="s-read-miss"
    )


@pytest.mark.parametrize(
    ("intent", "arguments", "named"),
    [
        ("workspace.read_file", {"path": "Downloads/contract.pdf"}, "contract.pdf"),
        ("workspace.read_file", {"path": "tests/test_app.py"}, "test_app.py"),
        ("machine.read_file", {"path": "~/Downloads/press-release.md"}, "press-release.md"),
    ],
)
def test_a_missing_read_target_is_named_in_the_reply(home, intent, arguments, named) -> None:
    execution = _run(intent, arguments, home)
    assert execution.ok is False and execution.status == "not_found"
    reply = _reply(execution)
    assert GENERIC not in reply
    assert named in reply
    assert reply == execution.response_text.strip()


def test_a_folder_given_to_a_file_read_says_so(home) -> None:
    execution = _run("workspace.read_file", {"path": "tests"}, home)
    assert execution.ok is False and execution.status == "invalid_target_type"
    reply = _reply(execution)
    assert GENERIC not in reply and "tests" in reply

