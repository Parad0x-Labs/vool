"""Round-3 live-feedback regressions.

- A "create an app ... build and verify in the workspace" request must NOT be captured by the
  keyword fast paths (workspace read of a not-yet-created file, or a Hive delete on its "delete"
  command word). Ask/Plan -> honest read-only refusal; Build/Auto -> reaches the builder controller.
- A compound news question keeps only the subject clause ("latest on BTC? trend up or down" -> "BTC").
- A bare trailing drive letter resolves ("biggest single file on G?" -> G:), not the C: default.
"""
from __future__ import annotations

import tempfile

from apps.vool_agent import VoolAgent
from core.agent_runtime.fast_live_info_news_topic import extract_news_topic
from core.agent_runtime.fast_paths_utility import (
    _direct_workspace_read_request,
    looks_like_agentic_build_request,
)
from core.execution.constants import resolve_drive_scope

_TODO = (
    "Create a small Python command-line To-Do app in a new folder called `vool-todo-test`. "
    "Commands: add, list, complete, and delete. Store tasks locally in tasks.json. "
    "Add automated tests with unittest. Do the work yourself: create all files, run the tests, "
    "fix any failures. Build and verify it in the workspace."
)


def test_build_request_detected_but_reads_and_single_file_creates_are_not() -> None:
    assert looks_like_agentic_build_request(_TODO) is True
    assert looks_like_agentic_build_request("read the file config.json") is False
    assert looks_like_agentic_build_request("show me pyproject.toml") is False
    assert looks_like_agentic_build_request("create a file notes.txt with exactly this content: hi") is False


def test_build_request_is_not_a_direct_workspace_read() -> None:
    # It names tasks.json but that is a file to CREATE, not read -- so no "does not exist" read.
    assert _direct_workspace_read_request(_TODO) is None
    # A genuine read still works.
    assert _direct_workspace_read_request("read the file README.md") is not None


def _run(mode: str) -> dict:
    ws = tempfile.mkdtemp(prefix="vool_ws_")
    agent = VoolAgent(backend_name="test-backend", device="openclaw-test", persona_id="default")
    agent.start()
    return agent.run_once(
        _TODO,
        source_context={"workspace": ws, "workspace_root": ws, "operating_mode": mode,
                        "surface": "openclaw", "platform": "openclaw", "_owner_local": True},
    )


def test_todo_build_ask_mode_is_an_honest_read_only_refusal() -> None:
    res = _run("ask")
    resp = str(res.get("response", "")).lower()
    assert "does not exist" not in resp  # never the old confusing tasks.json read error
    assert "read-only" in resp or "build or auto" in resp


def test_todo_build_mode_reaches_the_builder_not_hive_or_read() -> None:
    res = _run("build")
    resp = str(res.get("response", "")).lower()
    route = str(res.get("route") or "")
    # The two wrong routes are gone: no workspace-read "does not exist", no Hive-delete capture.
    assert "does not exist" not in resp
    assert "delete a live hive task" not in resp
    assert "hive_topic_delete" not in route


def test_news_topic_compound_question_keeps_subject_clause() -> None:
    assert extract_news_topic("Hi, whats the latest on BTC? trend up or down?") == "BTC"
    assert extract_news_topic("whats the latest news and trends on BTC?") == "BTC"
    assert extract_news_topic("news about the war in ukraine") == "the war in ukraine"


def test_bare_trailing_drive_letter_resolves() -> None:
    assert resolve_drive_scope("biggest single file on G?") == "G:" + chr(92)
    assert resolve_drive_scope("what is the biggest single file on G?") == "G:" + chr(92)
    assert resolve_drive_scope("free space on d") == "D:" + chr(92)
    # A non-drive trailing word is not a drive scope.
    assert resolve_drive_scope("what about dinner on a plate") is None
